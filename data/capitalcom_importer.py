"""
Capital.com CSV Importer
Imports leveraged trades history + funds history CSVs into the database.
"""

import io
import csv
import pandas as pd
import streamlit as st
from database.connection import get_connection
from database.crud import (
    update_investment_balances, update_holdings, update_accounts_balances,
    resolve_investment_fx,
)

# ── Instrument → Securities_Type classification ───────────────────────────────

_CRYPTO_KEYWORDS = {'ETH', 'BTC', 'DOGE', 'CRO', 'LTC', 'XRP', 'TRX', 'DGB',
                    'MATIC', 'XTZ', 'Cosmos', 'Crypto'}
_INDEX_SYMBOLS   = {'US500', 'UK100', 'DE40', 'NL25', 'SG25', 'VIX', 'VXZ21'}
_COMMODITY_NAMES = {'Gold', 'Oil', 'Crude', 'Copper', 'Palladium', 'Orange Juice'}
_FX_KEYWORDS     = {'EUR/', '/EUR', 'USD/', '/USD', 'GBP/', '/GBP', 'JPY', 'CAD',
                    'TRY', 'CNH'}


def _classify_security(symbol: str, name: str, currency: str) -> str:
    if symbol in _INDEX_SYMBOLS:
        return 'Market Index'
    if any(k in symbol for k in _CRYPTO_KEYWORDS) or any(k in name for k in _CRYPTO_KEYWORDS):
        return 'Crypto'
    if any(k in name for k in _COMMODITY_NAMES):
        return 'Commodity'
    if any(k in symbol for k in _FX_KEYWORDS) and len(symbol) <= 10:
        return 'FX Spot'
    if symbol in ('EZU',):
        return 'ETF'
    return 'Stock'


# ── CSV parsing ───────────────────────────────────────────────────────────────

def _parse_trades(file_content: str) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(file_content), sep=';', dtype=str)
    df.columns = [c.strip() for c in df.columns]
    for col in ['Quantity', 'Price', 'rpl', 'Rpl Converted', 'Swap', 'Swap Converted', 'Fee']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0.0)
    df['Timestamp (UTC)'] = pd.to_datetime(df['Timestamp (UTC)'], utc=True, errors='coerce')
    df['Date'] = df['Timestamp (UTC)'].dt.date
    return df


def _parse_funds(file_content: str) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(file_content), sep=';', dtype=str)
    df.columns = [c.strip() for c in df.columns]
    df['Amount'] = pd.to_numeric(df['Amount'], errors='coerce')
    df['Balance'] = pd.to_numeric(df['Balance'], errors='coerce')
    df['Modified (UTC)'] = pd.to_datetime(df['Modified (UTC)'], utc=True, errors='coerce')
    df['Date'] = df['Modified (UTC)'].dt.date
    return df[df['Status'] == 'PROCESSED'].copy()


# ── FX rate helpers ───────────────────────────────────────────────────────────

_FX_FALLBACK = {'USD': 1.20, 'GBP': 0.87, 'SGD': 1.45, 'EUR': 1.0}


def _fx_rate(rpl, rpl_conv, swap, swap_conv, currency: str) -> float:
    """Return units of trade currency per 1 EUR.

    Priority: rpl ratio → swap ratio → hardcoded fallback.
    Both Rpl Converted and Swap Converted are already in EUR.
    """
    if currency == 'EUR':
        return 1.0
    if abs(rpl) > 0.0001 and abs(rpl_conv) > 0.0001:
        return abs(rpl) / abs(rpl_conv)
    if abs(swap) > 0.0001 and abs(swap_conv) > 0.0001:
        return abs(swap) / abs(swap_conv)
    return _FX_FALLBACK.get(currency, 1.20)


# ── Record builders ───────────────────────────────────────────────────────────

_CAP_PREFIX = 'CAP|'   # prefix for all Capital.com descriptions in Investments


def _build_investment_records(trades_df: pd.DataFrame, funds_df: pd.DataFrame) -> list:
    """
    For each Trade ID: one Buy/Sell (open) + one or more Sell/Buy (closes).
    Total_Amount for the close = notional at close price in EUR.
    Total_Amount for the open  = sum(close_notional) - sum(P&L_eur)  ← exactly qty*open_price/fx.

    Rpl Converted and Swap Converted are already in EUR for all currencies.
    We use Rpl Converted as EUR P&L directly; funds TRADE Amount is used as a
    cross-check fallback when available.
    """
    records = []

    # exec_id → EUR P&L from funds TRADE rows (cross-check / override for partial closes)
    fund_trade_df = funds_df[funds_df['Type'] == 'TRADE'][['Trade Id', 'Amount']].copy()
    exec_pnl = dict(zip(fund_trade_df['Trade Id'], fund_trade_df['Amount']))

    opened = trades_df[trades_df['Status'] == 'OPENED'].copy()
    closed = trades_df[trades_df['Status'] == 'CLOSED'].copy()

    for _, open_row in opened.iterrows():
        trade_id = open_row['Trade Id']
        closes   = closed[closed['Trade Id'] == trade_id]
        if closes.empty:
            continue  # still-open position – skip

        symbol   = open_row['Instrument Symbol']
        name     = open_row['Instrument Name']
        currency = open_row['Currency']
        is_long  = open_row['Quantity'] > 0

        buy_total = 0.0
        open_qty  = 0.0

        open_fx_sum = 0.0

        for _, cr in closes.iterrows():
            exec_id   = cr['Exec Id']
            close_qty = abs(cr['Quantity'])
            # Rpl Converted is in EUR; use funds Amount when available (more precise)
            pnl_eur   = exec_pnl.get(exec_id, cr['Rpl Converted'])
            # fx = sec_currency per EUR (e.g. USD per EUR)
            fx        = _fx_rate(cr['rpl'], cr['Rpl Converted'],
                                 cr.get('Swap', 0.0), cr.get('Swap Converted', 0.0),
                                 currency)
            close_notional = close_qty * cr['Price'] / fx
            # Long: open is a Buy, close is a Sell.  open_cost = close_proceeds - pnl
            # Short: open is a Sell, close is a Buy.  open_proceeds = cover_cost + pnl
            if is_long:
                buy_total += close_notional - pnl_eur   # cost basis contribution
            else:
                buy_total += close_notional + pnl_eur   # short-open proceeds
            open_qty  += close_qty
            open_fx_sum += fx * close_qty

            # FX_Rate DB convention: acc_cur per sec_cur (EUR per USD) = 1/fx
            db_fx = round(1.0 / fx, 6) if fx and currency != 'EUR' else 1.0
            records.append({
                'record_type': 'investment',
                'desc':     f'{_CAP_PREFIX}CLOSE|{exec_id}',
                'symbol':   symbol,
                'name':     name,
                'currency': currency,
                'date':     cr['Date'],
                'action':   'Sell' if is_long else 'Buy',
                'quantity': round(close_qty, 6),
                'price':    round(cr['Price'], 4),
                'total_eur': round(close_notional, 2),
                'fx_rate':  db_fx,
            })

        # Weighted-average FX rate for the open record
        open_db_fx = round(1.0 / (open_fx_sum / open_qty), 6) if open_qty and currency != 'EUR' else 1.0
        records.append({
            'record_type': 'investment',
            'desc':     f'{_CAP_PREFIX}OPEN|{trade_id}',
            'symbol':   symbol,
            'name':     name,
            'currency': currency,
            'date':     open_row['Date'],
            'action':   'Buy' if is_long else 'Sell',
            'quantity': round(open_qty, 6),
            'price':    round(open_row['Price'], 4),
            'total_eur': round(max(buy_total, 0.0), 2),
            'fx_rate':  open_db_fx,
        })

    return records


def _build_dividend_records(trades_df: pd.DataFrame) -> list:
    records = []
    for _, row in trades_df[trades_df['Status'] == 'DIVIDEND'].iterrows():
        records.append({
            'record_type': 'investment',
            'desc':     f'{_CAP_PREFIX}DIV|{row["Exec Id"]}',
            'symbol':   row['Instrument Symbol'],
            'name':     row['Instrument Name'],
            'date':     row['Date'],
            'action':   'Dividend',
            'quantity': abs(row['Quantity']),
            'price':    row['Price'],
            'total_eur': abs(row['Rpl Converted']),
        })
    return records


_TX_TYPE_LABELS = {
    'DEPOSIT':    'Deposit',
    'WITHDRAWAL': 'Withdrawal',
    'ADJUSTMENT': 'Adjustment',
}

# Fund types that should land in Investments, keyed to (cap_prefix_tag, investment_action)
_INV_FUND_TYPES = {
    'CORPORATE_ACTION':          ('CORP', 'Dividend'),
    'TRADE_SLIPPAGE_PROTECTION': ('SLIP', 'RtrnCap'),
    'TRADE_CORRECTION':          ('CORR', 'RtrnCap'),
}


def _resolve_instrument(row, trades_df: pd.DataFrame) -> tuple:
    """Return (symbol, name) for a funds row.

    Priority: direct Instrument columns → Trade Id lookup in trades_df → ('', '', '').
    Returns (symbol, name, currency).
    """
    def _safe(val) -> str:
        import pandas as pd
        return '' if pd.isna(val) else str(val).strip()

    symbol   = _safe(row.get('Instrument Symbol', ''))
    name     = _safe(row.get('Instrument Name',   ''))
    currency = _safe(row.get('Currency', ''))
    if symbol and name:
        return symbol, name, currency
    trade_id = _safe(row.get('Trade Id', ''))
    if trade_id:
        match = trades_df[trades_df['Trade Id'] == trade_id]
        if not match.empty:
            r = match.iloc[0]
            return _safe(r['Instrument Symbol']), _safe(r['Instrument Name']), _safe(r['Currency'])
    return '', '', ''


def _build_trade_adjustment_records(funds_df: pd.DataFrame, trades_df: pd.DataFrame) -> tuple:
    """Convert Corporate Actions, Slippage Protection and Trade Corrections into
    Investment records (Dividend / RtrnCap) linked to their security via the
    Instrument columns or via Trade Id lookup.  Falls back to Transaction only
    when no security can be resolved."""
    inv_records = []
    tx_records  = []
    for fund_type, (tag, action) in _INV_FUND_TYPES.items():
        for _, row in funds_df[funds_df['Type'] == fund_type].iterrows():
            amount = row['Amount']
            symbol, name, currency = _resolve_instrument(row, trades_df)
            if symbol and name and abs(amount) > 0:
                inv_records.append({
                    'record_type': 'investment',
                    'desc':     f'{_CAP_PREFIX}{tag}|{row["Id"]}',
                    'currency': currency,
                    'symbol':   symbol,
                    'name':     name,
                    'date':     row['Date'],
                    'action':   action,
                    'quantity': 1.0,
                    'price':    round(abs(amount), 4),
                    'total_eur': round(abs(amount), 2),
                })
            else:
                label = fund_type.replace('_', ' ').title()
                tx_records.append({
                    'record_type': 'transaction',
                    'ext_id':      row['Id'],
                    'date':        row['Date'],
                    'amount':      amount,
                    'description': f"Capital.com {label} [{row['Id']}]",
                })
    return inv_records, tx_records


def _build_transaction_records(funds_df: pd.DataFrame) -> list:
    records = []
    for fund_type, label in _TX_TYPE_LABELS.items():
        for _, row in funds_df[funds_df['Type'] == fund_type].iterrows():
            records.append({
                'record_type': 'transaction',
                'ext_id':      row['Id'],
                'date':        row['Date'],
                'amount':      row['Amount'],
                'description': f"Capital.com {label} [{row['Id']}]",
            })
    return records


def _build_swap_records(trades_df: pd.DataFrame) -> list:
    """One MiscExp investment per security for total lifetime swap/financing costs.

    Uses the SWAP status rows in the trades CSV (which carry Instrument Name/Symbol
    and Swap Converted in EUR), grouped by instrument.
    """
    swaps = trades_df[trades_df['Status'] == 'SWAP'].copy()
    if swaps.empty:
        return []
    grouped = (
        swaps
        .groupby(['Instrument Symbol', 'Instrument Name', 'Currency'])
        .agg(total_swap=('Swap Converted', 'sum'), last_date=('Date', 'max'))
        .reset_index()
    )
    records = []
    for _, row in grouped.iterrows():
        total_swap = row['total_swap']
        if abs(total_swap) < 0.01:
            continue
        records.append({
            'record_type': 'investment',
            'desc':     f'{_CAP_PREFIX}SWAP|{row["Instrument Symbol"]}',
            'symbol':   row['Instrument Symbol'],
            'name':     row['Instrument Name'],
            'date':     row['last_date'],
            'action':   'MiscExp',
            'quantity': 1.0,
            'price':    round(abs(total_swap), 4),
            'total_eur': round(abs(total_swap), 2),
        })
    return records


# ── Database helpers ──────────────────────────────────────────────────────────

def _get_or_create_account(cur, name: str) -> int:
    cur.execute(
        "SELECT Accounts_Id FROM Accounts WHERE Accounts_Name = %s LIMIT 1", (name,)
    )
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute(
        """INSERT INTO Accounts (Accounts_Name, Accounts_Type, Accounts_Balance, Currencies_Id)
           VALUES (%s, 'Margin', 0, (SELECT Currencies_Id FROM Currencies WHERE Currencies_ShortName = 'EUR' LIMIT 1))
           RETURNING Accounts_Id""",
        (name,),
    )
    return cur.fetchone()[0]


def _get_or_create_security(cur, symbol: str, name: str, currency: str,
                             _cached_mappings: dict | None = None) -> int:
    """Resolve or create a Security record.

    Match priority:
      0. Saved mapping in import_security_mappings (user-defined override, keyed by symbol)
      1. Exact name match in Securities
      2. Ticker match in Securities (instrument symbol)
      3. Create new security
    """
    # 0. Saved mapping
    if _cached_mappings is None:
        from database.queries import get_security_mappings as _get_map
        _cached_mappings = _get_map("Capital.com")
    if symbol and symbol in _cached_mappings:
        return _cached_mappings[symbol]

    # 1. Exact name match
    if name:
        cur.execute(
            "SELECT Securities_Id FROM Securities WHERE Securities_Name = %s LIMIT 1", (name,)
        )
        row = cur.fetchone()
        if row:
            return row[0]

    # 2. Ticker / symbol match
    if symbol:
        cur.execute(
            "SELECT Securities_Id FROM Securities WHERE Ticker = %s LIMIT 1", (symbol,)
        )
        row = cur.fetchone()
        if row:
            return row[0]

    # 3. Create new
    sec_type = _classify_security(symbol, name, currency)
    ticker   = symbol or name[:20]
    cur.execute(
        """INSERT INTO Securities (Ticker, Securities_Name, Securities_Type, Currencies_Id)
           VALUES (%s, %s, %s,
                  (SELECT Currencies_Id FROM Currencies
                   WHERE Currencies_ShortName = %s LIMIT 1))
           RETURNING Securities_Id""",
        (ticker, name, sec_type, currency or 'EUR'),
    )
    return cur.fetchone()[0]


def _investment_exists(cur, acc_id: int, description: str) -> bool:
    cur.execute(
        "SELECT 1 FROM Investments WHERE Accounts_Id = %s AND Description = %s LIMIT 1",
        (acc_id, description),
    )
    return cur.fetchone() is not None


def _delete_existing_cap_investments(cur, acc_id: int) -> int:
    cur.execute("DELETE FROM Investments WHERE Accounts_Id = %s", (acc_id,))
    return cur.rowcount


def _delete_existing_cap_transactions(cur, acc_id: int) -> int:
    cur.execute("DELETE FROM Transactions WHERE Accounts_Id = %s", (acc_id,))
    return cur.rowcount


def _transaction_exists(cur, acc_id: int, description: str) -> bool:
    cur.execute(
        "SELECT 1 FROM Transactions WHERE Accounts_Id = %s AND Description = %s LIMIT 1",
        (acc_id, description),
    )
    return cur.fetchone() is not None


# ── Main import function ──────────────────────────────────────────────────────

def run_import(
    trades_content: str,
    funds_content: str,
    account_id: int,
    include_swaps: bool,
    include_dividends: bool,
    replace_mode: bool = False,
    progress_cb=None,
) -> dict:
    trades_df = _parse_trades(trades_content)
    funds_df  = _parse_funds(funds_content)

    inv_records = _build_investment_records(trades_df, funds_df)
    if include_dividends:
        inv_records += _build_dividend_records(trades_df)
    if include_swaps:
        inv_records += _build_swap_records(trades_df)
    adj_inv, adj_tx = _build_trade_adjustment_records(funds_df, trades_df)
    inv_records += adj_inv
    tx_records   = _build_transaction_records(funds_df) + adj_tx

    conn = get_connection()
    cur  = conn.cursor()
    counts = {'investments': 0, 'investments_skip': 0, 'transactions': 0, 'transactions_skip': 0,
              'deleted_investments': 0, 'deleted_transactions': 0}

    try:
        if replace_mode:
            counts['deleted_investments'] = _delete_existing_cap_investments(cur, account_id)
            counts['deleted_transactions'] = _delete_existing_cap_transactions(cur, account_id)

        total = len(inv_records) + len(tx_records)
        done  = 0

        # Look up the account's currency once
        cur.execute("SELECT Currencies_Id FROM Accounts WHERE Accounts_Id = %s", (account_id,))
        _acc_cur_row = cur.fetchone()
        _acc_cur_id  = int(_acc_cur_row[0]) if _acc_cur_row else None

        # Load user-defined security mappings once to avoid a DB call per record
        from database.queries import get_security_mappings as _get_sec_map
        _cap_mappings = _get_sec_map("Capital.com")

        # ── Investments ───────────────────────────────────────────────────────
        for rec in inv_records:
            sec_id = _get_or_create_security(
                cur, rec['symbol'], rec['name'], rec.get('currency', ''),
                _cached_mappings=_cap_mappings,
            )
            if not replace_mode and _investment_exists(cur, account_id, rec['desc']):
                counts['investments_skip'] += 1
            else:
                # Resolve security currency and FX (no explicit FX in Capital.com records)
                cur.execute(
                    "SELECT Currencies_Id FROM Securities WHERE Securities_Id = %s",
                    (sec_id,),
                )
                _scr = cur.fetchone()
                _sec_cur_id = int(_scr[0]) if _scr and _scr[0] else None
                _cap_total  = rec['total_eur']
                _explicit_fx = rec.get('fx_rate')
                if _cap_total is not None and _cap_total != 0:
                    _cap_sec, _cap_fx = resolve_investment_fx(
                        cur, _cap_total, _acc_cur_id, _sec_cur_id, rec['date'],
                        explicit_fx_rate=_explicit_fx,
                    )
                else:
                    _cap_sec, _cap_fx = _cap_total, 1.0
                cur.execute(
                    """INSERT INTO Investments
                           (Accounts_Id, Securities_Id, Date, Action, Quantity,
                            Price_Per_Share,
                            Total_Amount_AccCur, Total_Amount_SecCur, FX_Rate,
                            Description)
                       VALUES (%s, %s, %s, %s::investments_action, %s, %s, %s, %s, %s, %s)""",
                    (account_id, sec_id, rec['date'], rec['action'],
                     rec['quantity'], rec['price'],
                     _cap_total, _cap_sec, _cap_fx,
                     rec['desc']),
                )
                counts['investments'] += 1
            done += 1
            if progress_cb and done % 50 == 0:
                progress_cb(done / total)

        # ── Transactions ──────────────────────────────────────────────────────
        for rec in tx_records:
            if _transaction_exists(cur, account_id, rec['description']):
                counts['transactions_skip'] += 1
            else:
                cur.execute(
                    """INSERT INTO Transactions
                           (Accounts_Id, Date, Total_Amount, Description, Cleared)
                       VALUES (%s, %s, %s, %s, TRUE)""",
                    (account_id, rec['date'], rec['amount'], rec['description']),
                )
                counts['transactions'] += 1
            done += 1
            if progress_cb and done % 50 == 0:
                progress_cb(done / total)

        conn.commit()

        # Refresh balances
        update_holdings()
        update_investment_balances()
        update_accounts_balances(account_id)

    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

    # Auto-create linked cash transactions when the investment account has a
    # configured linked cash account.
    from database.crud import create_linked_cash_transactions_for_unlinked, get_linked_account_id
    _cap_linked = get_linked_account_id(account_id)
    if _cap_linked:
        create_linked_cash_transactions_for_unlinked(account_id, _cap_linked)
        update_accounts_balances(_cap_linked)
        update_investment_balances()

    return counts


# ── Security Mapping UI ───────────────────────────────────────────────────────

def _render_cap_security_mapping(trades_df: pd.DataFrame) -> None:
    """Show the security mapping panel for Capital.com instrument symbols.

    Identifies instruments that have no saved mapping and no automatic match
    (by name or ticker) and lets the user link them to existing DB securities.
    Mappings are persisted to import_security_mappings under source='Capital.com'.
    """
    from database.queries import get_security_mappings, save_security_mappings

    # Unique instruments that appear in trade records
    instr_df = (
        trades_df[trades_df['Status'].isin(['OPENED', 'CLOSED', 'DIVIDEND', 'SWAP'])]
        [['Instrument Symbol', 'Instrument Name', 'Currency']]
        .drop_duplicates()
        .sort_values('Instrument Name')
    )
    if instr_df.empty:
        return

    saved_ids = get_security_mappings("Capital.com")   # {symbol → sec_id}

    # Load all DB securities
    conn = get_connection()
    all_secs = pd.read_sql(
        """SELECT s.Securities_Id   AS securities_id,
                  s.Ticker          AS ticker,
                  s.Securities_Name AS name
           FROM   Securities s
           ORDER  BY s.Securities_Name""",
        conn,
    )
    conn.close()

    name_to_id   = dict(zip(all_secs["name"],   all_secs["securities_id"].astype(int)))
    ticker_to_id = dict(zip(all_secs["ticker"], all_secs["securities_id"].astype(int)))
    id_to_name   = dict(zip(all_secs["securities_id"].astype(int), all_secs["name"]))

    # Find instruments with no resolved match
    unmapped: list[dict] = []
    for _, row in instr_df.iterrows():
        sym  = str(row["Instrument Symbol"]).strip()
        nm   = str(row["Instrument Name"]).strip()
        ccy  = str(row.get("Currency", "")).strip()
        if sym in saved_ids:
            continue                                    # already mapped
        if nm in name_to_id or sym in ticker_to_id:
            continue                                    # auto-matched
        unmapped.append({"symbol": sym, "name": nm, "currency": ccy})

    # Always show the expander so users can review / update saved mappings
    saved_count   = len(saved_ids)
    unmapped_count = len(unmapped)

    with st.expander(
        f"🗺️ Security Mappings — {unmapped_count} unmapped · {saved_count} saved",
        expanded=bool(unmapped_count),
    ):
        if unmapped_count == 0 and saved_count == 0:
            st.success("All instruments matched automatically — no manual mapping needed.")
            return

        if unmapped_count:
            st.caption(
                "These instruments could not be matched in your database by name or ticker. "
                "Select the corresponding DB security for each one, then click **💾 Save Mappings**. "
                "Mappings are permanent and used for all future Capital.com imports."
            )
            sec_options = ["(create new — will be added on import)"] + all_secs["name"].tolist()
            pending: dict[str, int] = {}

            for item in unmapped:
                sym = item["symbol"]
                c1, c2, c3 = st.columns([1, 2, 3])
                with c1:
                    st.markdown(f"**{sym}**")
                with c2:
                    st.caption(item["name"])
                with c3:
                    chosen = st.selectbox(
                        f"Map {sym}",
                        sec_options,
                        key=f"cap_map_{sym.replace(' ', '_').replace('/', '_').replace('#', 'h')}",
                        label_visibility="collapsed",
                    )
                    if not chosen.startswith("(create new"):
                        sid = name_to_id.get(chosen)
                        if sid:
                            pending[sym] = int(sid)

            if pending:
                if st.button("💾 Save Mappings", key="cap_save_mappings", type="primary"):
                    try:
                        save_security_mappings("Capital.com", pending)
                        st.success(f"✅ Saved {len(pending)} mapping(s).")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Failed to save mappings: {exc}")

        # Show / manage already-saved mappings
        if saved_count:
            st.divider()
            st.markdown(f"**Saved mappings** ({saved_count}):")
            rows = [
                {"Symbol": sym, "Mapped to": id_to_name.get(int(sid), f"id={sid}")}
                for sym, sid in saved_ids.items()
            ]
            st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')

            to_delete = st.multiselect(
                "Remove mappings:", list(saved_ids.keys()), key="cap_del_mappings"
            )
            if to_delete and st.button("🗑️ Remove selected", key="cap_del_mappings_btn"):
                from database.queries import delete_security_mapping
                for sym in to_delete:
                    delete_security_mapping("Capital.com", sym)
                st.success(f"Removed {len(to_delete)} mapping(s).")
                st.rerun()


# ── Streamlit UI ──────────────────────────────────────────────────────────────

def render_capitalcom_importer():
    st.subheader("Capital.com CSV Importer")
    st.caption(
        "Import Capital.com all-time trade and funds history. "
        "Upload both CSV exports from the Capital.com platform."
    )

    # ── Restore last-used settings (first render only) ───────────────────────
    from database.queries import get_app_setting, save_app_setting
    if "cap_acc_mode" not in st.session_state:
        st.session_state["cap_acc_mode"] = get_app_setting("cap_acc_mode") or "Existing"
    if "cap_acc_select" not in st.session_state:
        _saved = get_app_setting("cap_acc_select")
        if _saved:
            st.session_state["cap_acc_select"] = _saved
    if "cap_include_swaps" not in st.session_state:
        st.session_state["cap_include_swaps"] = get_app_setting("cap_include_swaps") != "false"
    if "cap_include_divs" not in st.session_state:
        st.session_state["cap_include_divs"] = get_app_setting("cap_include_divs") != "false"
    if "cap_replace_mode" not in st.session_state:
        st.session_state["cap_replace_mode"] = get_app_setting("cap_replace_mode") == "true"

    col_t, col_f = st.columns(2)
    with col_t:
        trades_file = st.file_uploader(
            "Leveraged Trades History CSV",
            type="csv",
            key="cap_trades_file",
            help="capitalcom-leveraged_trades_history.csv",
        )
    with col_f:
        funds_file = st.file_uploader(
            "Funds History CSV",
            type="csv",
            key="cap_funds_file",
            help="capitalcom-funds_history-*.csv",
        )

    if not trades_file or not funds_file:
        st.info("Upload both CSV files to continue.")
        return

    # ── Parse & summarise ─────────────────────────────────────────────────────
    trades_content = trades_file.read().decode('utf-8')
    funds_content  = funds_file.read().decode('utf-8')

    trades_df = _parse_trades(trades_content)
    funds_df  = _parse_funds(funds_content)

    opened = trades_df[trades_df['Status'] == 'OPENED']
    closed = trades_df[trades_df['Status'] == 'CLOSED']
    divs   = trades_df[trades_df['Status'] == 'DIVIDEND']
    dep    = funds_df[funds_df['Type'] == 'DEPOSIT']['Amount'].sum()
    wdl    = funds_df[funds_df['Type'] == 'WITHDRAWAL']['Amount'].sum()
    pnl    = funds_df[funds_df['Type'] == 'TRADE']['Amount'].sum()
    swap   = funds_df[funds_df['Type'] == 'SWAP']['Amount'].sum()

    date_min = trades_df['Date'].min()
    date_max = trades_df['Date'].max()

    st.markdown(f"""
| | |
|---|---|
| **Date range** | {date_min} → {date_max} |
| **Trades** | {len(opened)} opened · {len(closed)} close executions |
| **Dividends** | {len(divs)} |
| **Instruments** | {trades_df['Instrument Symbol'].nunique()} |
| **Deposits** | €{dep:,.2f} |
| **Withdrawals** | €{wdl:,.2f} |
| **Trade P&L** | €{pnl:,.2f} |
| **Swap fees** | €{swap:,.2f} |
| **Net** | €{dep+wdl+pnl+swap:,.2f} |
""")

    st.divider()

    # ── Security Mapping ──────────────────────────────────────────────────────
    _render_cap_security_mapping(trades_df)

    st.divider()

    # ── Account selection ─────────────────────────────────────────────────────
    _INVESTMENT_TYPES = ('Brokerage', 'Margin', 'Pension', 'Other Investment')
    conn = get_connection()
    acc_df = pd.read_sql(
        f"SELECT Accounts_Id AS accounts_id, Accounts_Name AS accounts_name FROM Accounts "
        f"WHERE Accounts_Type IN {_INVESTMENT_TYPES} ORDER BY Accounts_Name",
        conn,
    )
    conn.close()

    col_mode, col_acc = st.columns([1, 3])
    with col_mode:
        mode = st.radio("Account", ["Existing", "New"], key="cap_acc_mode")
    with col_acc:
        if mode == "Existing":
            acc_options = dict(zip(acc_df['accounts_name'], acc_df['accounts_id']))
            default_idx = next(
                (i for i, n in enumerate(acc_df['accounts_name']) if 'capital' in n.lower()),
                0,
            )
            selected_name = st.selectbox(
                "Select account",
                list(acc_options.keys()),
                index=default_idx,
                key="cap_acc_select",
            )
            account_id = acc_options[selected_name]
        else:
            new_name = st.text_input(
                "New account name", value="Capital.com", key="cap_acc_name"
            )
            account_id = None  # resolved at import time

    st.divider()

    # ── Options ───────────────────────────────────────────────────────────────
    col_o1, col_o2, col_o3 = st.columns(3)
    with col_o1:
        include_swaps = st.checkbox(
            "Import swap fees as MiscExp (per security)",
            value=True,
            key="cap_include_swaps",
            help=f"Total swap/financing cost: {swap:,.2f} EUR — imported as MiscExp in Investments, one row per security, so they count toward Total Net P&L.",
        )
    with col_o2:
        include_dividends = st.checkbox(
            "Import dividends",
            value=True,
            key="cap_include_divs",
            help=f"{len(divs)} dividend records",
        )
    with col_o3:
        replace_mode = st.checkbox(
            "Replace: delete ALL account data before import",
            value=False,
            key="cap_replace_mode",
            help="Deletes ALL investments and transactions for the selected account before importing. Use this to cleanly re-import the full Capital.com history.",
        )

    # ── Preview ───────────────────────────────────────────────────────────────
    with st.expander("Preview instruments to be imported", expanded=False):
        instr = (
            trades_df[trades_df['Status'].isin(['OPENED', 'CLOSED', 'DIVIDEND'])]
            [['Instrument Symbol', 'Instrument Name', 'Currency']]
            .drop_duplicates()
            .sort_values('Instrument Name')
        )
        instr['Type'] = instr.apply(
            lambda r: _classify_security(r['Instrument Symbol'], r['Instrument Name'], r['Currency']),
            axis=1,
        )
        st.dataframe(instr, hide_index=True, width="stretch")

    # ── Import ────────────────────────────────────────────────────────────────
    if st.button("⬆ Import", type="primary", key="cap_import_btn"):
        resolved_account_id = account_id
        if mode == "New":
            conn2 = get_connection()
            cur2  = conn2.cursor()
            try:
                resolved_account_id = _get_or_create_account(cur2, new_name)
                conn2.commit()
            finally:
                cur2.close()
                conn2.close()

        progress = st.progress(0.0, text="Importing…")

        try:
            counts = run_import(
                trades_content=trades_content,
                funds_content=funds_content,
                account_id=resolved_account_id,
                include_swaps=include_swaps,
                include_dividends=include_dividends,
                replace_mode=replace_mode,
                progress_cb=lambda p: progress.progress(p, text="Importing…"),
            )
            progress.progress(1.0, text="Done.")
            deleted_msg = (
                f" Deleted first: {counts['deleted_investments']} investments · "
                f"{counts['deleted_transactions']} transactions."
            ) if replace_mode else ""
            st.success(
                f"Imported: **{counts['investments']}** investment records · "
                f"**{counts['transactions']}** transactions.  "
                f"Skipped (already exist): {counts['investments_skip']} investments · "
                f"{counts['transactions_skip']} transactions.{deleted_msg}"
            )
            try:
                save_app_setting("cap_acc_mode",      mode)
                save_app_setting("cap_acc_select",    selected_name if mode == "Existing" else "")
                save_app_setting("cap_include_swaps", "true" if include_swaps    else "false")
                save_app_setting("cap_include_divs",  "true" if include_dividends else "false")
                save_app_setting("cap_replace_mode",  "true" if replace_mode     else "false")
            except Exception:
                pass
            st.rerun()
        except Exception as e:
            progress.empty()
            st.error(f"Import failed: {e}")
            st.exception(e)
