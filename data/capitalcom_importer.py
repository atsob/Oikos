"""
Capital.com CSV Importer
Imports leveraged trades history + funds history CSVs into the database.
"""

import io
import pandas as pd
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


# ── Pre-import preview helpers ─────────────────────────────────────────────────

def check_existing_records(
    inv_records: list, tx_records: list, account_id: int,
) -> tuple[set, set]:
    """Return (existing_inv_descs, existing_tx_descs) for the preview screen."""
    conn = get_connection()
    cur  = conn.cursor()
    try:
        existing_inv: set = set()
        existing_tx:  set = set()
        if inv_records:
            descs = [r['desc'] for r in inv_records]
            ph    = ",".join(["%s"] * len(descs))
            cur.execute(
                f"SELECT Description FROM Investments WHERE Accounts_Id = %s AND Description IN ({ph})",
                [account_id] + descs,
            )
            existing_inv = {row[0] for row in cur.fetchall()}
        if tx_records:
            descs = [r['description'] for r in tx_records]
            ph    = ",".join(["%s"] * len(descs))
            cur.execute(
                f"SELECT Description FROM Transactions WHERE Accounts_Id = %s AND Description IN ({ph})",
                [account_id] + descs,
            )
            existing_tx = {row[0] for row in cur.fetchall()}
        return existing_inv, existing_tx
    finally:
        cur.close()
        conn.close()


def preview_security_matches(inv_records: list) -> dict[str, tuple]:
    """Return {symbol → (securities_id | None, match_type)} for each unique instrument.

    Match priority mirrors _get_or_create_security: saved mapping → name → ticker → 'new'.
    """
    from database.queries import get_security_mappings as _get_map
    mappings = _get_map("Capital.com")

    unique: dict[str, tuple] = {
        r['symbol']: (r['symbol'], r['name'])
        for r in inv_records
        if r.get('symbol')
    }
    result: dict[str, tuple] = {}
    conn = get_connection()
    cur  = conn.cursor()
    try:
        for symbol, (sym, name) in unique.items():
            if sym in mappings:
                sec_id = mappings[sym]
                cur.execute("SELECT Securities_Name FROM Securities WHERE Securities_Id = %s", (sec_id,))
                row = cur.fetchone()
                result[symbol] = (sec_id, f"mapped:{row[0] if row else sym}")
                continue

            if name:
                cur.execute("SELECT Securities_Id FROM Securities WHERE Securities_Name = %s LIMIT 1", (name,))
                row = cur.fetchone()
                if row:
                    result[symbol] = (row[0], "name")
                    continue

            cur.execute("SELECT Securities_Id FROM Securities WHERE Ticker = %s LIMIT 1", (sym,))
            row = cur.fetchone()
            if row:
                result[symbol] = (row[0], "ticker")
                continue

            result[symbol] = (None, "new")
        return result
    finally:
        cur.close()
        conn.close()


def build_preview_records(
    trades_content: str, funds_content: str,
    include_swaps: bool, include_dividends: bool,
) -> tuple[list, list]:
    """Parse the two CSVs and build (inv_records, tx_records) without touching the DB."""
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
    return inv_records, tx_records


# ── Main import function ──────────────────────────────────────────────────────

def run_import(
    trades_content: str,
    funds_content: str,
    account_id: int,
    include_swaps: bool,
    include_dividends: bool,
    replace_mode: bool = False,
    progress_cb=None,
    selected_inv: set[str] | None = None,
    selected_tx: set[str] | None = None,
) -> dict:
    inv_records, tx_records = build_preview_records(
        trades_content, funds_content, include_swaps, include_dividends,
    )
    if selected_inv is not None:
        inv_records = [r for r in inv_records if r['desc'] in selected_inv]
    if selected_tx is not None:
        tx_records = [r for r in tx_records if r['description'] in selected_tx]

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
