"""Revolut Personal CSV Importer.

How to export from the Revolut app
-----------------------------------
1. Open Revolut → tap your account → tap the ↓ (statement) icon.
2. Choose date range → format **CSV** → Download.
3. Upload the downloaded file here.

CSV columns (Revolut Personal, current format):
    Type, Product, Started Date, Completed Date,
    Description, Amount, Fee, Currency, State, Balance

Notes
-----
- Only COMPLETED / PENDING rows are imported; FAILED / REVERTED are skipped.
- FX exchanges appear as *two* rows (one per currency leg) — they are
  automatically paired into a single "FX: CCY1 → CCY2" record.
- Revolut uses the Unicode minus sign U+2212 (−) for negative amounts,
  not the regular ASCII hyphen.  This is handled automatically.
"""

from __future__ import annotations

import io
import re
from datetime import date, datetime
from typing import Optional

import pandas as pd

_REV_PREFIX = "REV|"

_TYPE_LABELS: dict[str, str] = {
    "TOPUP":           "Revolut Deposit",
    "TRANSFER":        "Revolut Transfer",
    "CARD_PAYMENT":    "Revolut Card Payment",
    "ATM":             "Revolut ATM Withdrawal",
    "REFUND":          "Revolut Refund",
    "CASHBACK":        "Revolut Cashback",
    "REWARD":          "Revolut Reward",
    "FEE":             "Revolut Fee",
    "EXCHANGE":        "Revolut FX Exchange",
    "CRYPTO_PURCHASE": "Revolut Crypto Buy",
    "CRYPTO_SALE":     "Revolut Crypto Sell",
    "SAVINGS":         "Revolut Savings Transfer",
    "INTEREST":        "Revolut Interest",
}

_SKIP_STATES  = {"FAILED", "REVERTED", "DECLINED"}
_UNICODE_MINUS = "−"   # − (U+2212) used by Revolut for negative amounts


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _clean_amount(val) -> Optional[float]:
    if pd.isna(val):
        return None
    s = str(val).strip().replace(_UNICODE_MINUS, "-")
    # Remove currency symbols (€ £ $) and thousands separators
    s = re.sub(r"[€£$,]", "", s)
    # Remove 2-4 letter currency codes (e.g. "EUR 100" → "100", "EUR -100" → "-100")
    s = re.sub(r"[A-Z]{2,4}", "", s)
    s = s.strip()
    try:
        return float(s)
    except ValueError:
        return None


def _parse_rev_date(val) -> Optional[date]:
    raw = str(val).strip()[:19]
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%d/%m/%Y %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%d/%m/%Y",
    ):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------------------
# CSV parser
# ---------------------------------------------------------------------------

def parse_revolut_csv(file_bytes: bytes) -> pd.DataFrame:
    """Parse a Revolut Personal CSV export into a normalised DataFrame.

    Returns columns:
        date, type, description, amount, fee, currency, state, balance, is_exchange
    """
    text = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = file_bytes.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            pass
    if text is None:
        raise ValueError("Could not decode the Revolut CSV file.")

    df = pd.read_csv(io.StringIO(text), dtype=str)
    df.columns = [c.strip() for c in df.columns]

    # Normalise column names — Revolut has tweaked them across app versions
    rename: dict[str, str] = {}
    for col in df.columns:
        cl = col.lower().replace(" ", "_")
        if cl == "type":                         rename[col] = "type"
        elif cl == "product":                    rename[col] = "product"
        elif "started" in cl:                    rename[col] = "started_date"
        elif "completed" in cl:                  rename[col] = "completed_date"
        elif cl == "description":                rename[col] = "description"
        elif cl == "amount":                     rename[col] = "amount"
        elif cl == "fee":                        rename[col] = "fee"
        elif cl == "currency":                   rename[col] = "currency"
        elif cl == "state":                      rename[col] = "state"
        elif cl == "balance":                    rename[col] = "balance"
    df = df.rename(columns=rename)

    required = {"type", "started_date", "description", "amount", "currency", "state"}
    missing  = required - set(df.columns)
    if missing:
        # Detect a Revolut Savings CSV by its unique column names (checked on the
        # raw/un-renamed column set, since this parser won't have renamed them).
        _raw_lower = {c.strip().lower() for c in df.columns}
        if any("price per share" in c for c in _raw_lower) or \
                any("quantity" in c for c in _raw_lower):
            raise ValueError(
                "This looks like a Revolut Savings (Flexible Cash Funds) statement, "
                "not a Revolut Personal account statement. "
                "Please switch to the '🐣 Revolut Savings' tab to import it."
            )
        raise ValueError(
            f"Revolut CSV is missing columns: {', '.join(sorted(missing))}. "
            "Please export a fresh statement from the Revolut app."
        )

    # Drop failed / reversed rows
    df = df[~df["state"].str.strip().str.upper().isin(_SKIP_STATES)].copy()

    # Parse numeric columns
    df["amount"]  = df["amount"].apply(_clean_amount)
    df["fee"]     = df.get("fee",     pd.Series(dtype=str)).apply(
                        lambda x: _clean_amount(x) or 0.0
                    )
    df["balance"] = df.get("balance", pd.Series(dtype=str)).apply(_clean_amount)

    # Parse date — prefer completed_date
    date_col = "completed_date" if "completed_date" in df.columns else "started_date"
    df["date"] = df[date_col].apply(_parse_rev_date)
    df = df.dropna(subset=["date", "amount"]).copy()

    df["is_exchange"] = df["type"].str.upper() == "EXCHANGE"
    df["type"]        = df["type"].str.upper()

    return df[[
        "date", "type", "description", "amount", "fee",
        "currency", "state", "balance", "is_exchange",
    ]].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Record builder
# ---------------------------------------------------------------------------

def build_records(df: pd.DataFrame) -> tuple[list, list]:
    """Convert a parsed Revolut DataFrame into (inv_records, tx_records).

    FX exchange pairs are matched by date + description and emitted as two
    cash transaction records (one per currency leg).  Crypto trades go into
    inv_records.  Interest goes into inv_records as IntInc.  Everything else
    goes into tx_records.
    """
    inv_records: list[dict] = []
    tx_records:  list[dict] = []
    paired_indices: set     = set()

    # ── Pair FX exchange rows ────────────────────────────────────────────────
    # Each FX exchange appears twice (one per currency), same description & date.
    ex = df[df["is_exchange"]].copy()
    for (dt, desc), grp in ex.groupby(["date", "description"]):
        neg = grp[grp["amount"] < 0]
        pos = grp[grp["amount"] > 0]
        if neg.empty or pos.empty:
            continue
        nr = neg.iloc[0]
        pr = pos.iloc[0]
        paired_indices.update([nr.name, pr.name])
        base_key = f"{_REV_PREFIX}FX|{dt}|{desc}"
        for suffix, row, sign_desc in (
            ("|OUT", nr, f"{nr['currency']} → {pr['currency']}"),
            ("|IN",  pr, f"{nr['currency']} → {pr['currency']}"),
        ):
            tx_records.append({
                "record_type": "transaction",
                "source":      "Revolut",
                "desc":        base_key + suffix,
                "date":        dt,
                "amount":      round(row["amount"], 2),
                "description": f"Revolut FX: {sign_desc} ({desc})",
                "currency":    row["currency"],
            })

    # ── All other rows ───────────────────────────────────────────────────────
    crypto_buy_types  = {"CRYPTO_PURCHASE"}
    crypto_sell_types = {"CRYPTO_SALE"}

    for idx, row in df.iterrows():
        if idx in paired_indices:
            continue

        rev_type = str(row["type"]).upper()
        currency = str(row.get("currency", "EUR"))
        amount   = row["amount"]
        dt       = row["date"]
        desc     = str(row.get("description", ""))
        fee      = float(row.get("fee") or 0.0)
        key      = f"{_REV_PREFIX}{rev_type}|{dt}|{desc}|{amount}"

        if rev_type in crypto_buy_types | crypto_sell_types:
            action = "Buy" if rev_type in crypto_buy_types else "Sell"
            m      = re.search(r"\b([A-Z]{2,8})\b", desc)
            symbol = m.group(1) if m else "CRYPTO"
            inv_records.append({
                "record_type":    "investment",
                "source":         "Revolut",
                "desc":           key,
                "symbol":         symbol,
                "name":           f"Revolut Crypto {symbol}",
                "isin":           "",
                "currency":       currency,
                "asset_category": "CRYPTO",
                "date":           dt,
                "action":         action,
                "quantity":       1.0,
                "price":          round(abs(amount), 4),
                "commission":     round(abs(fee), 4),
                "total_eur":      round(abs(amount), 2),
            })

        elif rev_type == "INTEREST":
            inv_records.append({
                "record_type":    "investment",
                "source":         "Revolut",
                "desc":           key,
                "symbol":         "REV-INT",
                "name":           "Revolut Interest",
                "isin":           "",
                "currency":       currency,
                "asset_category": "BOND",
                "date":           dt,
                "action":         "IntInc",
                "quantity":       1.0,
                "price":          round(abs(amount), 4),
                "commission":     0.0,
                "total_eur":      round(abs(amount), 2),
            })

        else:
            label = _TYPE_LABELS.get(rev_type,
                                     f"Revolut {rev_type.replace('_', ' ').title()}")
            tx_records.append({
                "record_type": "transaction",
                "source":      "Revolut",
                "desc":        key,
                "date":        dt,
                "amount":      round(amount, 2),
                "description": f"{label}: {desc}",
                "currency":    currency,
            })
            if abs(fee) > 0.001:
                tx_records.append({
                    "record_type": "transaction",
                    "source":      "Revolut",
                    "desc":        key + "|FEE",
                    "date":        dt,
                    "amount":      round(-abs(fee), 2),
                    "description": f"Revolut Fee: {desc}",
                    "currency":    currency,
                })

    return inv_records, tx_records


# ===========================================================================
# Revolut Trading (brokerage) CSV importer
# ===========================================================================
#
# CSV columns (Revolut Trading, current format):
#   Date, Ticker, Type, Quantity, Price per share, Total amount, Currency, FX Rate
#
# Types include: BUY, SELL, DIVIDEND, CUSTODY FEE, CASH TOP-UP, CASH WITHDRAWAL
# ===========================================================================

_REV_TRADING_PREFIX = "REVT|"

_TRADING_INV_TYPES = {"BUY", "SELL", "DIVIDEND", "STOCK SPLIT", "STOCK MERGER"}
_TRADING_TX_TYPES  = {"CASH TOP-UP", "CASH WITHDRAWAL", "CUSTODY FEE",
                       "CASH TOP UP", "CASH TOPUP"}


def parse_revolut_trading_csv(file_bytes: bytes) -> "pd.DataFrame":
    """Parse a Revolut Trading CSV export into a normalised DataFrame.

    Returns columns:
        date, ticker, type, quantity, price_per_share, total_amount, currency,
        fx_rate, raw_type
    """
    import io as _io
    text = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = file_bytes.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            pass
    if text is None:
        raise ValueError("Could not decode the Revolut Trading CSV file.")

    df = pd.read_csv(_io.StringIO(text), dtype=str)
    df.columns = [c.strip() for c in df.columns]

    # Normalise column names
    rename: dict[str, str] = {}
    for col in df.columns:
        cl = col.lower().replace(" ", "_")
        if cl in ("date", "datetime"):                          rename[col] = "date"
        elif cl == "ticker":                                    rename[col] = "ticker"
        elif cl == "type":                                      rename[col] = "type"
        elif "quantity" in cl:                                  rename[col] = "quantity"
        elif cl in ("price_per_share", "price"):                rename[col] = "price_per_share"
        elif cl in ("total_amount", "total", "amount"):         rename[col] = "total_amount"
        elif cl == "currency":                                  rename[col] = "currency"
        elif cl in ("fx_rate", "fx rate", "fxrate"):           rename[col] = "fx_rate"
    df = df.rename(columns=rename)

    required = {"date", "type", "total_amount", "currency"}
    missing  = required - set(df.columns)
    if missing:
        raise ValueError(
            f"Revolut Trading CSV is missing columns: {', '.join(sorted(missing))}. "
            "Please export a fresh statement from the Revolut app → Stocks → History."
        )

    # Preserve raw timestamp string for dedup key (same-day same-amount records
    # would collide if we only kept the date; microsecond precision prevents that).
    df["raw_date_str"]   = df["date"].str.strip().str[:23]   # e.g. "2025-01-17T18:07:36.966"

    df["raw_type"]       = df["type"].str.strip().str.upper()
    df["quantity"]       = df.get("quantity",       pd.Series(dtype=str)).apply(_clean_amount).fillna(0.0)
    df["price_per_share"]= df.get("price_per_share",pd.Series(dtype=str)).apply(_clean_amount).fillna(0.0)
    df["total_amount"]   = df["total_amount"].apply(_clean_amount)
    df["fx_rate"]        = df.get("fx_rate",        pd.Series(dtype=str)).apply(_clean_amount).fillna(1.0)
    df["date"]           = df["date"].apply(_parse_rev_date)
    df["ticker"]         = df.get("ticker",         pd.Series(dtype=str)).fillna("").str.strip()
    df                   = df.dropna(subset=["date", "total_amount"]).copy()

    return df[[
        "date", "raw_date_str", "ticker", "type", "raw_type",
        "quantity", "price_per_share", "total_amount",
        "currency", "fx_rate",
    ]].reset_index(drop=True)


def build_trading_records(df: pd.DataFrame) -> "tuple[list, list]":
    """Convert a parsed Revolut Trading DataFrame into (inv_records, tx_records)."""
    inv_records: list[dict] = []
    tx_records:  list[dict] = []

    for _, row in df.iterrows():
        raw_type      = str(row["raw_type"]).upper()
        ticker        = str(row["ticker"]).strip()
        currency      = str(row.get("currency", "EUR"))
        total         = float(row["total_amount"])
        qty           = float(row.get("quantity", 0) or 0)
        price         = float(row.get("price_per_share", 0) or 0)
        fx_rate       = float(row.get("fx_rate", 1) or 1)
        dt            = row["date"]
        # Use microsecond-precision timestamp so same-day same-amount records
        # (e.g. multiple CASH TOP-UPs of €100) get unique dedup keys.
        raw_ts        = str(row.get("raw_date_str", dt))
        key           = f"{_REV_TRADING_PREFIX}{raw_type}|{raw_ts}|{ticker}|{round(total, 4)}"

        # Normalize type variants produced by the Revolut app:
        #   "BUY - MARKET", "BUY - LIMIT"  → "BUY"
        #   "SELL - MARKET", "SELL - LIMIT" → "SELL"
        if raw_type.startswith("BUY"):
            base_type = "BUY"
        elif raw_type.startswith("SELL"):
            base_type = "SELL"
        else:
            base_type = raw_type

        # FX convention: Revolut's fx_rate = sec_currency / acc_currency (e.g. USD/EUR).
        # Our DB convention: FX_Rate = acc_currency / sec_currency (e.g. EUR/USD).
        # total_sec_cur = abs(total) is the raw amount in the security's currency.
        _rev_fx_db    = round(1.0 / fx_rate, 8) if fx_rate else 1.0
        _total_sec    = round(abs(total), 8)
        _total_acc    = round(abs(total) / fx_rate, 2) if fx_rate else round(abs(total), 2)

        if base_type == "BUY":
            inv_records.append({
                "record_type":    "investment",
                "source":         "Revolut Trading",
                "desc":           key,
                "symbol":         ticker or "UNKNOWN",
                "name":           ticker or "Revolut Trading",
                "isin":           "",
                "currency":       currency,
                "asset_category": "ETF",
                "date":           dt,
                "action":         "Buy",
                "quantity":       round(abs(qty), 6),
                "price":          round(abs(price), 6),
                "commission":     0.0,
                "total_eur":      _total_acc,
                "total_sec_cur":  _total_sec,
                "fx_rate_db":     _rev_fx_db,
            })

        elif base_type == "SELL":
            inv_records.append({
                "record_type":    "investment",
                "source":         "Revolut Trading",
                "desc":           key,
                "symbol":         ticker or "UNKNOWN",
                "name":           ticker or "Revolut Trading",
                "isin":           "",
                "currency":       currency,
                "asset_category": "ETF",
                "date":           dt,
                "action":         "Sell",
                "quantity":       round(abs(qty), 6),
                "price":          round(abs(price), 6),
                "commission":     0.0,
                "total_eur":      _total_acc,
                "total_sec_cur":  _total_sec,
                "fx_rate_db":     _rev_fx_db,
            })

        elif base_type == "DIVIDEND":
            inv_records.append({
                "record_type":    "investment",
                "source":         "Revolut Trading",
                "desc":           key,
                "symbol":         ticker or "REV-DIV",
                "name":           f"{ticker} Dividend" if ticker else "Revolut Dividend",
                "isin":           "",
                "currency":       currency,
                "asset_category": "ETF",
                "date":           dt,
                "action":         "Dividend",
                "quantity":       1.0,
                "price":          round(_total_acc, 4),
                "commission":     0.0,
                "total_eur":      _total_acc,
                "total_sec_cur":  _total_sec,
                "fx_rate_db":     _rev_fx_db,
            })

        elif base_type in ("CUSTODY FEE", "CUSTODYFEE"):
            tx_records.append({
                "record_type": "transaction",
                "source":      "Revolut Trading",
                "desc":        key,
                "date":        dt,
                "amount":      round(-abs(total) / fx_rate, 2),
                "description": "Revolut Trading Custody Fee",
                "currency":    currency,
            })

        elif base_type in ("CASH TOP-UP", "CASH TOP UP", "CASH TOPUP"):
            tx_records.append({
                "record_type": "transaction",
                "source":      "Revolut Trading",
                "desc":        key,
                "date":        dt,
                "amount":      round(abs(total) / fx_rate, 2),
                "description": "Revolut Trading Cash Top-Up",
                "currency":    currency,
            })

        elif base_type == "CASH WITHDRAWAL":
            tx_records.append({
                "record_type": "transaction",
                "source":      "Revolut Trading",
                "desc":        key,
                "date":        dt,
                "amount":      round(-abs(total) / fx_rate, 2),
                "description": "Revolut Trading Cash Withdrawal",
                "currency":    currency,
            })

        else:
            # Catch-all: emit as a plain transaction
            tx_records.append({
                "record_type": "transaction",
                "source":      "Revolut Trading",
                "desc":        key,
                "date":        dt,
                "amount":      round(total / fx_rate, 2),
                "description": (f"Revolut Trading {raw_type.title()}: {ticker}").strip(": "),
                "currency":    currency,
            })

    return inv_records, tx_records


def trading_investments_preview_df(records: list) -> "pd.DataFrame":
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    cols = ["date", "action", "symbol", "name", "quantity",
            "price", "total_eur", "currency", "asset_category", "desc"]
    return df[[c for c in cols if c in df.columns]].copy()


def trading_transactions_preview_df(records: list) -> "pd.DataFrame":
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    return df[["date", "description", "amount", "currency"]].copy()


def run_trading_import(
    inv_records: list,
    tx_records:  list,
    account_id:  int,
    replace_mode: bool = False,
    progress_cb=None,
) -> dict:
    """Insert parsed Revolut Trading records into the database."""
    from database.connection import get_connection as _get_conn
    from database.crud import update_holdings, update_accounts_balances, update_investment_balances

    conn = _get_conn()
    cur  = conn.cursor()
    counts = {
        "investments": 0, "investments_skip": 0,
        "transactions": 0, "transactions_skip": 0,
    }

    try:
        if replace_mode:
            cur.execute(
                "DELETE FROM Investments WHERE Accounts_Id = %s AND Description LIKE %s",
                (account_id, f"{_REV_TRADING_PREFIX}%"),
            )
            cur.execute(
                "DELETE FROM Transactions WHERE Accounts_Id = %s AND Description LIKE %s",
                (account_id, f"{_REV_TRADING_PREFIX}%"),
            )

        total = len(inv_records) + len(tx_records)
        done  = 0

        for rec in inv_records:
            sec_id = _get_or_create_security(
                cur, rec["symbol"], rec["name"],
                rec.get("currency", "EUR"), rec.get("asset_category", "STK"),
                source="Revolut Trading",
            )
            desc = rec["desc"]
            if not replace_mode and _inv_exists(cur, account_id, desc):
                counts["investments_skip"] += 1
            else:
                # total_sec_cur and fx_rate_db are set by build_trading_records
                _rt_sec = rec.get("total_sec_cur", rec["total_eur"])
                _rt_fx  = rec.get("fx_rate_db", 1.0)
                cur.execute(
                    """INSERT INTO Investments
                           (Accounts_Id, Securities_Id, Date, Action, Quantity,
                            Price_Per_Share,
                            Total_Amount_AccCur, Total_Amount_SecCur, FX_Rate,
                            Description)
                       VALUES (%s, %s, %s, %s::investments_action, %s, %s, %s, %s, %s, %s)""",
                    (account_id, sec_id, rec["date"], rec["action"],
                     rec["quantity"], rec["price"],
                     rec["total_eur"], _rt_sec, _rt_fx,
                     desc),
                )
                counts["investments"] += 1
            done += 1
            if progress_cb and done % 25 == 0:
                progress_cb(done / total)

        for rec in tx_records:
            desc = rec["desc"]
            if not replace_mode and _tx_exists(cur, account_id, desc):
                counts["transactions_skip"] += 1
            else:
                cur.execute(
                    """INSERT INTO Transactions
                           (Accounts_Id, Date, Total_Amount, Description, Cleared)
                       VALUES (%s, %s, %s, %s, TRUE)""",
                    (account_id, rec["date"], rec["amount"], desc),
                )
                counts["transactions"] += 1
            done += 1
            if progress_cb and done % 25 == 0:
                progress_cb(done / total)

        conn.commit()
        update_holdings()
        update_accounts_balances()
        update_investment_balances()

    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()

    # Auto-create linked cash transactions when the investment account has a
    # configured linked cash account.
    from database.crud import create_linked_cash_transactions_for_unlinked, get_linked_account_id
    _rt_linked = get_linked_account_id(account_id)
    if _rt_linked:
        create_linked_cash_transactions_for_unlinked(account_id, _rt_linked)
        update_accounts_balances(_rt_linked)
        update_investment_balances()

    return counts


# ---------------------------------------------------------------------------
# Preview helpers
# ---------------------------------------------------------------------------

def investments_preview_df(records: list) -> pd.DataFrame:
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    cols = ["date", "action", "symbol", "name", "quantity",
            "price", "total_eur", "currency", "asset_category", "desc"]
    return df[[c for c in cols if c in df.columns]].copy()


def transactions_preview_df(records: list) -> pd.DataFrame:
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    return df[["date", "description", "amount", "currency"]].copy()


# ---------------------------------------------------------------------------
# Database insertion
# ---------------------------------------------------------------------------

_ASSET_TO_SECTYPE: dict[str, str] = {
    "STK":    "Stock",
    "BOND":   "Bond",
    "CRYPTO": "Crypto",
    "ETF":    "ETF",
}


def _get_or_create_security(cur, symbol: str, name: str,
                             currency: str, asset_category: str,
                             source: str = "", isin: str = "") -> int:
    """Resolve or create a Security record.

    Match priority:
      0. Saved mapping in import_security_mappings (user-defined override)
      1. ISIN match in Securities (when isin is provided)
      2. Ticker match in Securities
      3. Name match in Securities
      4. Create new security
    """
    # 0. Check saved mapping (user-defined override) — keyed by ISIN then symbol
    if source:
        from database.queries import get_security_mappings as _get_map
        _mappings = _get_map(source)
        for _key in (isin, symbol):
            if _key and _key in _mappings:
                return _mappings[_key]

    # 1. Match by ISIN
    if isin:
        cur.execute(
            "SELECT Securities_Id FROM Securities WHERE ISIN = %s LIMIT 1",
            (isin,),
        )
        row = cur.fetchone()
        if row:
            return row[0]

    # 2. Match by Ticker
    if symbol:
        cur.execute(
            "SELECT Securities_Id FROM Securities WHERE Ticker = %s LIMIT 1",
            (symbol,),
        )
        row = cur.fetchone()
        if row:
            return row[0]

    # 3. Match by name
    if name:
        cur.execute(
            "SELECT Securities_Id FROM Securities WHERE Securities_Name = %s LIMIT 1",
            (name,),
        )
        row = cur.fetchone()
        if row:
            return row[0]

    # 4. Create new
    sec_type = _ASSET_TO_SECTYPE.get(asset_category.upper(), "Stock")
    cur.execute(
        """INSERT INTO Securities
               (Ticker, Securities_Name, ISIN, Securities_Type, Currencies_Id)
           VALUES (%s, %s, %s, %s,
                  (SELECT Currencies_Id FROM Currencies
                   WHERE Currencies_ShortName = %s LIMIT 1))
           RETURNING Securities_Id""",
        (symbol or name[:30], name, isin or None, sec_type, currency or "EUR"),
    )
    return cur.fetchone()[0]


def _inv_exists(cur, acc_id: int, desc: str) -> bool:
    cur.execute(
        "SELECT 1 FROM Investments WHERE Accounts_Id = %s AND Description = %s LIMIT 1",
        (acc_id, desc),
    )
    return cur.fetchone() is not None


def _tx_exists(cur, acc_id: int, desc: str) -> bool:
    cur.execute(
        "SELECT 1 FROM Transactions WHERE Accounts_Id = %s AND Description = %s LIMIT 1",
        (acc_id, desc),
    )
    return cur.fetchone() is not None


# ---------------------------------------------------------------------------
# Pre-import reconciliation helpers (read-only — used by the UI preview)
# ---------------------------------------------------------------------------

def check_existing_records(
    inv_records: list, tx_records: list, account_id: int
) -> tuple[set, set]:
    """Return (existing_inv_descs, existing_tx_descs) via IN-list queries."""
    from database.connection import get_connection as _get_conn
    conn = _get_conn()
    cur  = conn.cursor()
    try:
        existing_inv: set[str] = set()
        existing_tx:  set[str] = set()

        inv_descs = [r["desc"] for r in inv_records]
        if inv_descs:
            placeholders = ",".join(["%s"] * len(inv_descs))
            cur.execute(
                f"SELECT Description FROM Investments "
                f"WHERE Accounts_Id = %s AND Description IN ({placeholders})",
                [account_id] + inv_descs,
            )
            existing_inv = {row[0] for row in cur.fetchall()}

        tx_descs = [r["desc"] for r in tx_records]
        if tx_descs:
            placeholders = ",".join(["%s"] * len(tx_descs))
            cur.execute(
                f"SELECT Description FROM Transactions "
                f"WHERE Accounts_Id = %s AND Description IN ({placeholders})",
                [account_id] + tx_descs,
            )
            existing_tx = {row[0] for row in cur.fetchall()}

        return existing_inv, existing_tx
    finally:
        cur.close()
        conn.close()


def check_fuzzy_duplicates(
    inv_records: list, tx_records: list, account_id: int
) -> tuple[set, set]:
    """Secondary duplicate detection using date + action + quantity/amount.

    Catches records already in the DB under a different description key
    (e.g. manually entered or imported from a bank statement).
    Returns (fuzzy_inv_descs, fuzzy_tx_descs).
    """
    from database.connection import get_connection as _get_conn
    conn = _get_conn()
    cur  = conn.cursor()
    fuzzy_inv: set[str] = set()
    fuzzy_tx:  set[str] = set()
    try:
        for rec in inv_records:
            cur.execute(
                """SELECT 1 FROM Investments
                   WHERE Accounts_Id     = %s
                     AND Date            = %s
                     AND Action::text    ILIKE %s
                     AND ABS(Quantity - %s) < 0.001
                   LIMIT 1""",
                (account_id, rec["date"], rec["action"], rec["quantity"]),
            )
            if cur.fetchone():
                fuzzy_inv.add(rec["desc"])

        for rec in tx_records:
            cur.execute(
                """SELECT 1 FROM Transactions
                   WHERE Accounts_Id          = %s
                     AND Date                 = %s
                     AND ABS(Total_Amount - %s) < 0.01
                   LIMIT 1""",
                (account_id, rec["date"], rec["amount"]),
            )
            if cur.fetchone():
                fuzzy_tx.add(rec["desc"])

        return fuzzy_inv, fuzzy_tx
    finally:
        cur.close()
        conn.close()


def preview_security_matches(inv_records: list) -> dict[str, tuple]:
    """Return {ticker → (securities_id | None, match_type)} for each unique ticker.

    Match priority:
      0. Saved mapping (import_security_mappings table) — explicit user override
      1. Ticker column in Securities
      2. Securities_Name
      3. 'new' — will be created on import

    match_type: 'mapped:<name>' | 'ticker' | 'name' | 'new'
    """
    from database.connection import get_connection as _get_conn
    from database.queries import get_security_mappings as _get_mappings
    mappings = _get_mappings("Revolut Trading")   # {symbol → sec_id}

    conn = _get_conn()
    cur  = conn.cursor()
    result: dict[str, tuple] = {}
    try:
        seen: set[str] = set()
        for rec in inv_records:
            ticker = (rec.get("symbol") or "").strip()
            name   = (rec.get("name")   or "").strip()
            key    = ticker if ticker else name
            if not key or key in seen:
                continue
            seen.add(key)

            # 0. Saved mapping (user-defined override)
            if ticker and ticker in mappings:
                sec_id = mappings[ticker]
                cur.execute(
                    "SELECT Securities_Name FROM Securities WHERE Securities_Id = %s",
                    (sec_id,),
                )
                row = cur.fetchone()
                result[key] = (sec_id, f"mapped:{row[0] if row else ticker}")
                continue

            # 1. Match by Ticker column
            if ticker:
                cur.execute(
                    "SELECT Securities_Id FROM Securities WHERE Ticker = %s LIMIT 1",
                    (ticker,),
                )
                row = cur.fetchone()
                if row:
                    result[key] = (row[0], "ticker")
                    continue

            # 2. Match by Securities_Name
            if name:
                cur.execute(
                    "SELECT Securities_Id FROM Securities "
                    "WHERE Securities_Name = %s LIMIT 1",
                    (name,),
                )
                row = cur.fetchone()
                if row:
                    result[key] = (row[0], "name")
                    continue

            result[key] = (None, "new")
    finally:
        cur.close()
        conn.close()
    return result


def run_import(
    inv_records: list,
    tx_records:  list,
    account_id:  int,
    replace_mode: bool = False,
    progress_cb=None,
) -> dict:
    """Insert parsed Revolut records into the database."""
    from database.connection import get_connection
    from database.crud import update_holdings, update_accounts_balances, update_investment_balances

    conn = get_connection()
    cur  = conn.cursor()
    counts = {
        "investments": 0, "investments_skip": 0,
        "transactions": 0, "transactions_skip": 0,
    }

    try:
        if replace_mode:
            cur.execute(
                "DELETE FROM Investments WHERE Accounts_Id = %s AND Description LIKE %s",
                (account_id, f"{_REV_PREFIX}%"),
            )
            cur.execute(
                "DELETE FROM Transactions WHERE Accounts_Id = %s AND Description LIKE %s",
                (account_id, f"{_REV_PREFIX}%"),
            )

        total = len(inv_records) + len(tx_records)
        done  = 0

        for rec in inv_records:
            sec_id = _get_or_create_security(
                cur, rec["symbol"], rec["name"],
                rec.get("currency", "EUR"), rec.get("asset_category", "STK"),
            )
            if not replace_mode and _inv_exists(cur, account_id, rec["desc"]):
                counts["investments_skip"] += 1
            else:
                # Revolut supplies the native-currency amount and FX rate at
                # parse time; they are embedded in the record as total_sec_cur
                # and fx_rate_db (our acc/sec convention).
                _rev_sec  = rec.get("total_sec_cur", rec["total_eur"])
                _rev_fx   = rec.get("fx_rate_db", 1.0)
                cur.execute(
                    """INSERT INTO Investments
                           (Accounts_Id, Securities_Id, Date, Action, Quantity,
                            Price_Per_Share,
                            Total_Amount_AccCur, Total_Amount_SecCur, FX_Rate,
                            Description)
                       VALUES (%s, %s, %s, %s::investments_action, %s, %s, %s, %s, %s, %s)""",
                    (account_id, sec_id, rec["date"], rec["action"],
                     rec["quantity"], rec["price"],
                     rec["total_eur"], _rev_sec, _rev_fx,
                     rec["desc"]),
                )
                counts["investments"] += 1
            done += 1
            if progress_cb and done % 25 == 0:
                progress_cb(done / total)

        for rec in tx_records:
            if not replace_mode and _tx_exists(cur, account_id, rec["desc"]):
                counts["transactions_skip"] += 1
            else:
                cur.execute(
                    """INSERT INTO Transactions
                           (Accounts_Id, Date, Total_Amount, Description, Cleared)
                       VALUES (%s, %s, %s, %s, TRUE)""",
                    (account_id, rec["date"], rec["amount"], rec["desc"]),
                )
                counts["transactions"] += 1
            done += 1
            if progress_cb and done % 25 == 0:
                progress_cb(done / total)

        conn.commit()
        update_holdings()
        update_accounts_balances()
        update_investment_balances()

    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()

    # Auto-create linked cash transactions when the investment account has a
    # configured linked cash account.
    from database.crud import create_linked_cash_transactions_for_unlinked, get_linked_account_id
    _rev_linked = get_linked_account_id(account_id)
    if _rev_linked:
        create_linked_cash_transactions_for_unlinked(account_id, _rev_linked)
        update_accounts_balances(_rev_linked)
        update_investment_balances()

    return counts


# ===========================================================================
# Revolut Savings CSV importer
# ===========================================================================
#
# CSV columns (Revolut Savings, current format):
#   Date, Description, "Value, EUR", Price per share, Quantity of shares
#
# Date format example: "May 25, 2026, 4:29:55 AM"
#
# Transaction types (from Description field):
#   BUY EUR Class R <ISIN>              → Investment (Buy)
#   Interest PAID EUR Class R <ISIN>   → Investment (Dividend)
#   Service Fee Charged EUR Class <ISIN> → Investment (MiscExp)
#   Interest Reinvested Class R EUR <ISIN> → skipped (paired BUY covers it)
# ===========================================================================

_REV_SAVINGS_PREFIX = "REVS|"
_SAVINGS_ISIN       = "IE000AZVL3K0"   # Revolut EUR money market fund default


def _parse_savings_date(val) -> "Optional[date]":
    """Parse Revolut Savings date: 'May 25, 2026, 4:29:55 AM'."""
    raw = str(val).strip()
    for fmt in (
        "%b %d, %Y, %I:%M:%S %p",   # "May 25, 2026, 4:29:55 AM"
        "%B %d, %Y, %I:%M:%S %p",   # full month name fallback
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    return None


def parse_revolut_savings_csv(file_bytes: bytes) -> "pd.DataFrame":
    """Parse a Revolut Savings statement CSV export into a normalised DataFrame.

    Returns columns:
        date, raw_date_str, description, value_eur, price_per_share, quantity
    """
    text = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = file_bytes.decode(enc)
            break
        except (UnicodeDecodeError, LookupError):
            pass
    if text is None:
        raise ValueError("Could not decode the Revolut Savings CSV file.")

    df = pd.read_csv(io.StringIO(text), dtype=str)
    df.columns = [c.strip() for c in df.columns]

    # Normalise column names
    rename: dict[str, str] = {}
    for col in df.columns:
        cl = col.lower().replace(" ", "_")
        if cl in ("date", "datetime"):  rename[col] = "date"
        elif cl == "description":       rename[col] = "description"
        elif "value" in cl:             rename[col] = "value_eur"
        elif "price" in cl:             rename[col] = "price_per_share"
        elif "quantity" in cl:          rename[col] = "quantity"
    df = df.rename(columns=rename)

    required = {"date", "description", "value_eur"}
    missing  = required - set(df.columns)
    if missing:
        # Detect a Revolut Personal CSV by its distinctive column names.
        _raw_lower = {c.strip().lower() for c in df.columns}
        if "started date" in _raw_lower or "started_date" in _raw_lower or \
                "state" in _raw_lower:
            raise ValueError(
                "This looks like a Revolut Personal (current account) statement, "
                "not a Revolut Savings statement. "
                "Please switch to the '💚 Revolut Personal' tab to import it."
            )
        raise ValueError(
            f"Revolut Savings CSV is missing columns: {', '.join(sorted(missing))}. "
            "Please export a savings statement: Revolut app → Savings → ⋮ → Statement."
        )

    # Preserve raw date string for dedup keys (keeps same-day records distinct)
    df["raw_date_str"]    = df["date"].str.strip()
    df["value_eur"]       = df["value_eur"].apply(_clean_amount)
    df["price_per_share"] = df.get("price_per_share",
                                   pd.Series(dtype=str)).apply(_clean_amount)
    df["quantity"]        = df.get("quantity",
                                   pd.Series(dtype=str)).apply(_clean_amount)
    df["date"]            = df["raw_date_str"].apply(_parse_savings_date)
    df = df.dropna(subset=["date", "value_eur"]).copy()

    return df[["date", "raw_date_str", "description",
               "value_eur", "price_per_share", "quantity"]].reset_index(drop=True)


def build_savings_records(df: "pd.DataFrame") -> "tuple[list, list]":
    """Convert a parsed Revolut Savings DataFrame into (inv_records, tx_records).

    Mapping:
      BUY ...              → Investment (Buy)
    #  Interest PAID ...    → Investment (Dividend)
      Interest PAID ...    → Investment (Reinvest with Quantity = Value)
    #  Service Fee ...      → Investment (MiscExp)
      Service Fee ...      → Investment (Reinvest with Quantity = Value)
    #  Interest Reinvested  → skipped (the paired BUY the next day records it)
      Interest Reinvested  → Investment (Sell with Price = 1 and Quantuity = Value, i.e. a reinvestment record that doesn't affect holdings or cash)
      anything else        → plain cash Transaction
    """
    inv_records: list[dict] = []
    tx_records:  list[dict] = []

    for _, row in df.iterrows():
        desc   = str(row["description"]).strip()
        value  = float(row["value_eur"])
        dt     = row["date"]
        raw_ts = str(row.get("raw_date_str", dt))

        _pps  = row.get("price_per_share")
        price = float(_pps) if (_pps is not None and not pd.isna(_pps)) else 1.0

        _qty = row.get("quantity")
        qty  = float(_qty)  if (_qty  is not None and not pd.isna(_qty))  else 0.0

        # Extract ISIN from description (e.g. "IE000AZVL3K0")
        isin_m = re.search(r'\b([A-Z]{2}[A-Z0-9]{10})\b', desc)
        isin   = isin_m.group(1) if isin_m else _SAVINGS_ISIN

        # Stable dedup key — raw timestamp keeps same-day events distinct
        key = f"{_REV_SAVINGS_PREFIX}{raw_ts}|{desc[:50]}|{round(value, 4)}"

        # Common investment-record base
        base = {
            "record_type":    "investment",
            "source":         "Revolut Savings",
            "desc":           key,
            "symbol":         isin,
            "name":           "Revolut EUR Money Market Fund",
            "isin":           isin,
            "currency":       "EUR",
            "asset_category": "BOND",
            "date":           dt,
            "commission":     0.0,
        }

        desc_upper = desc.upper()

        if desc_upper.startswith("BUY"):
            qty_used = abs(qty) if qty else (abs(value) / max(price, 0.0001))
            inv_records.append({**base,
                "action":    "Buy",
                "quantity":  round(qty_used, 6),
                "price":     round(price if price else 1.0, 6),
                "total_eur": round(abs(value), 2),
            })

        elif desc_upper.startswith("SELL"):
            # Withdrawal: sell fund units and transfer proceeds to linked cash account.
            qty_used = abs(qty) if qty else (abs(value) / max(price, 0.0001))
            inv_records.append({**base,
                "action":    "Sell",
                "quantity":  round(qty_used, 6),
                "price":     round(price if price else 1.0, 6),
                "total_eur": round(abs(value), 2),
            })

        elif desc_upper.startswith("INTEREST PAID"):
            inv_records.append({**base,
            #    "action":    "Dividend",
                "action":    "Reinvest",
            #    "quantity":  1.0,
                "quantity":  round(abs(value), 6),  # reinvestment record with Quantity = interest amount
            #    "price":     round(abs(value), 6),
                "price":     round(price if price else 1.0, 6),
                "total_eur": round(abs(value), 2),
            })

        elif desc_upper.startswith("SERVICE FEE"):
            inv_records.append({**base,
            #    "action":    "MiscExp",
                "action":    "Reinvest",
            #    "quantity":  1.0, 
                "quantity":  round(value, 6),
            #    "price":     round(abs(value), 6),
                "price":     round(price if price else 1.0, 6),
                "total_eur": round(value, 2),
            })

        elif desc_upper.startswith("INTEREST REINVESTED"):
            # Skip — the paired BUY on the following day records the reinvestment
        #    pass
            inv_records.append({**base,
            #    "action":    "MiscExp",
                "action":    "Sell",
            #    "quantity":  1.0, 
                "quantity":  round(abs(value), 6),
            #    "price":     round(abs(value), 6),
                "price":     round(price if price else 1.0, 6),
                "total_eur": round(abs(value), 2),
            })

        else:
            # Catch-all: emit as a plain cash transaction
            tx_records.append({
                "record_type": "transaction",
                "source":      "Revolut Savings",
                "desc":        key,
                "date":        dt,
                "amount":      round(value, 2),
                "description": f"Revolut Savings: {desc}",
                "currency":    "EUR",
            })

    return inv_records, tx_records


def build_savings_records_as_tx(df: "pd.DataFrame") -> "tuple[list, list]":
    """Transaction-mode alternative to build_savings_records.

    Maps all Revolut Savings events to plain cash Transactions so they are
    visible in a standard Savings / Checking account register — no Investments
    table records are created.

    Mapping:
      BUY ...              → Transaction (+ amount, "Revolut Savings: Fund Purchase")
      Interest PAID ...    → Transaction (+ amount, "Revolut Savings: Interest Earned")
      Service Fee ...      → Transaction (- amount, "Revolut Savings: Service Fee")
      Interest Reinvested  → skipped (covered by the paired BUY)
      anything else        → Transaction (raw description)
    """
    tx_records: list[dict] = []

    for _, row in df.iterrows():
        desc   = str(row["description"]).strip()
        value  = float(row["value_eur"])
        dt     = row["date"]
        raw_ts = str(row.get("raw_date_str", dt))

        # Stable dedup key (same prefix so replace-mode DELETE still catches them)
        key = f"{_REV_SAVINGS_PREFIX}{raw_ts}|{desc[:50]}|{round(value, 4)}"

        desc_upper = desc.upper()

        if desc_upper.startswith("INTEREST REINVESTED"):
            # Skip — the paired BUY the next day records the actual fund purchase
            continue

        if desc_upper.startswith("BUY"):
            label = "Revolut Savings: Fund Purchase"
        elif desc_upper.startswith("SELL"):
            label = "Revolut Savings: Fund Withdrawal"
        elif desc_upper.startswith("INTEREST PAID"):
            label = "Revolut Savings: Interest Earned"
        elif desc_upper.startswith("SERVICE FEE"):
            label = "Revolut Savings: Service Fee"
        else:
            label = f"Revolut Savings: {desc}"

        tx_records.append({
            "record_type": "transaction",
            "source":      "Revolut Savings",
            "desc":        key,
            "date":        dt,
            "amount":      value,          # keep full precision (e.g. 0.0696 interest)
            "description": label,
            "currency":    "EUR",
        })

    return [], tx_records


def preview_savings_security(isin: str = _SAVINGS_ISIN) -> "dict | None":
    """Return DB security info that will be used for the Revolut Savings import.

    Resolution order mirrors _get_or_create_security:
      0. Saved mapping  (import_security_mappings for source='Revolut Savings')
      1. ISIN match     (Securities.ISIN = isin)
      2. Ticker match   (Securities.Ticker = isin)
      3. None           (will create a new security on import)

    Returns a dict with keys: sec_id, ticker, name, match_type
    or None if no existing security matches.
    """
    from database.connection import get_connection as _get_conn
    from database.queries import get_security_mappings as _get_map

    mappings = _get_map("Revolut Savings")   # {key → sec_id}

    conn = _get_conn()
    cur  = conn.cursor()
    try:
        # 0. Saved mapping (keyed by ISIN)
        if isin and isin in mappings:
            sec_id = mappings[isin]
            cur.execute(
                "SELECT Ticker, Securities_Name FROM Securities WHERE Securities_Id = %s",
                (sec_id,),
            )
            row = cur.fetchone()
            if row:
                return {"sec_id": sec_id, "ticker": row[0],
                        "name": row[1], "match_type": "mapped"}

        # 1. ISIN match
        if isin:
            cur.execute(
                "SELECT Securities_Id, Ticker, Securities_Name "
                "FROM Securities WHERE ISIN = %s LIMIT 1",
                (isin,),
            )
            row = cur.fetchone()
            if row:
                return {"sec_id": row[0], "ticker": row[1],
                        "name": row[2], "match_type": "isin"}

        # 2. Ticker match (ISIN used as ticker fallback)
        if isin:
            cur.execute(
                "SELECT Securities_Id, Ticker, Securities_Name "
                "FROM Securities WHERE Ticker = %s LIMIT 1",
                (isin,),
            )
            row = cur.fetchone()
            if row:
                return {"sec_id": row[0], "ticker": row[1],
                        "name": row[2], "match_type": "ticker"}

        return None   # will create on import
    finally:
        cur.close()
        conn.close()


def savings_investments_preview_df(records: list) -> "pd.DataFrame":
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    cols = ["date", "action", "symbol", "name", "quantity",
            "price", "total_eur", "currency", "asset_category", "desc"]
    return df[[c for c in cols if c in df.columns]].copy()


def savings_transactions_preview_df(records: list) -> "pd.DataFrame":
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    return df[["date", "description", "amount", "currency"]].copy()


def run_savings_import(
    inv_records:  list,
    tx_records:   list,
    account_id:   int,
    replace_mode: bool = False,
    progress_cb=None,
) -> dict:
    """Insert parsed Revolut Savings records into the database."""
    from database.connection import get_connection as _get_conn
    from database.crud import update_holdings, update_accounts_balances, update_investment_balances

    conn = _get_conn()
    cur  = conn.cursor()
    counts = {
        "investments": 0, "investments_skip": 0,
        "transactions": 0, "transactions_skip": 0,
    }

    try:
        if replace_mode:
            cur.execute(
                "DELETE FROM Investments  WHERE Accounts_Id = %s AND Description LIKE %s",
                (account_id, f"{_REV_SAVINGS_PREFIX}%"),
            )
            cur.execute(
                "DELETE FROM Transactions WHERE Accounts_Id = %s AND Description LIKE %s",
                (account_id, f"{_REV_SAVINGS_PREFIX}%"),
            )

        total = len(inv_records) + len(tx_records)
        done  = 0

        for rec in inv_records:
            sec_id = _get_or_create_security(
                cur,
                rec["symbol"], rec["name"],
                rec.get("currency", "EUR"), rec.get("asset_category", "BOND"),
                source="Revolut Savings", isin=rec.get("isin", ""),
            )
            desc = rec["desc"]
            if not replace_mode and _inv_exists(cur, account_id, desc):
                counts["investments_skip"] += 1
            else:
                # Revolut Savings is a EUR money-market fund; FX_Rate = 1.0
                cur.execute(
                    """INSERT INTO Investments
                           (Accounts_Id, Securities_Id, Date, Action, Quantity,
                            Price_Per_Share,
                            Total_Amount_AccCur, Total_Amount_SecCur, FX_Rate,
                            Description)
                       VALUES (%s, %s, %s, %s::investments_action, %s, %s, %s, %s, %s, %s)""",
                    (account_id, sec_id, rec["date"], rec["action"],
                     rec["quantity"], rec["price"],
                     rec["total_eur"], rec["total_eur"], 1.0,
                     desc),
                )
                counts["investments"] += 1
            done += 1
            if progress_cb and done % 25 == 0:
                progress_cb(done / total)

        for rec in tx_records:
            desc = rec["desc"]
            if not replace_mode and _tx_exists(cur, account_id, desc):
                counts["transactions_skip"] += 1
            else:
                cur.execute(
                    """INSERT INTO Transactions
                           (Accounts_Id, Date, Total_Amount, Description, Cleared)
                       VALUES (%s, %s, %s, %s, TRUE)""",
                    (account_id, rec["date"], rec["amount"], desc),
                )
                counts["transactions"] += 1
            done += 1
            if progress_cb and done % 25 == 0:
                progress_cb(done / total)

        conn.commit()
        update_holdings()
        update_accounts_balances()
        update_investment_balances()

    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()

    # Auto-create linked cash transactions when the investment account has a
    # configured linked cash account.
    from database.crud import create_linked_cash_transactions_for_unlinked, get_linked_account_id
    _rsav_linked = get_linked_account_id(account_id)
    if _rsav_linked:
        create_linked_cash_transactions_for_unlinked(account_id, _rsav_linked)
        update_accounts_balances(_rsav_linked)
        update_investment_balances()

    return counts
