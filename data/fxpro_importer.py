"""
FxPro PDF Statement Importer
Supports MT4 and MT5 statement formats for the same account.
"""

import re
import io
import json
from pathlib import Path
from datetime import datetime
import pandas as pd
import pdfplumber
import streamlit as st
from database.connection import get_connection
from database.crud import update_investment_balances, update_holdings, update_accounts_balances

_FXP_PREFIX = 'FXP|'

# Legacy JSON file — kept only for one-time migration to the DB table.
_MAPPING_FILE = Path(__file__).parent / 'fxpro_security_map.json'


def _load_saved_mapping() -> dict[str, int]:
    """Return {pdf_symbol → securities_id} from the DB.

    On the very first call after upgrading from the JSON-file approach this
    function silently migrates the old label-based JSON into the DB and renames
    the JSON file so the migration only runs once.
    """
    from database.queries import get_security_mappings, save_security_mappings

    # One-time migration: JSON file exists but DB is empty for FxPro
    if _MAPPING_FILE.exists():
        try:
            existing_db = get_security_mappings("FxPro")
            if not existing_db:
                label_map = json.loads(_MAPPING_FILE.read_text(encoding='utf-8'))
                if label_map:
                    db_df = _load_db_securities()
                    label_to_id = {
                        f"{r['ticker']} | {r['name']} | {r['currency']}": int(r['sec_id'])
                        for _, r in db_df.iterrows()
                    }
                    to_save = {
                        sym: label_to_id[label]
                        for sym, label in label_map.items()
                        if label in label_to_id
                    }
                    if to_save:
                        save_security_mappings("FxPro", to_save)
            # Rename the JSON file so we don't attempt migration again
            _MAPPING_FILE.rename(_MAPPING_FILE.with_suffix('.json.migrated'))
        except Exception:
            pass  # migration is best-effort; never block the UI

    return get_security_mappings("FxPro")


def _save_mapping(sec_id_map: dict[str, int]) -> None:
    """Persist {pdf_symbol → securities_id} to the DB (non-None values only)."""
    from database.queries import save_security_mappings
    to_save = {sym: sid for sym, sid in sec_id_map.items() if sid is not None}
    if to_save:
        save_security_mappings("FxPro", to_save)

# ── Security classification ──────────────────────────────────────────────────

_FX_CURRENCIES = {
    'EUR', 'USD', 'GBP', 'JPY', 'CHF', 'AUD', 'CAD', 'NZD',
    'TRY', 'NOK', 'SEK', 'SGD', 'HKD', 'CNH', 'MXN', 'ZAR',
    'PLN', 'CZK', 'HUF', 'DKK', 'RUB',
}
_COMMODITY_PREFIXES = (
    'GOLD', 'BRENT', 'WTI', 'OIL', 'SILVER', 'NGAS',
    'COPPER', 'PALLADIUM', 'PLATINUM', 'WHEAT', 'CORN',
)
_CRYPTO_SUBSTRINGS = ('BTC', 'ETH', 'DOGE', 'XRP', 'LTC', 'BCH', 'ADA', 'DOT', 'BNB')


def _classify_security(symbol: str) -> str:
    s = symbol.strip()
    if s.startswith('#'):
        # Symbols with digits → indices (#UK100, #USSPX500, #Euro50, #GerTech30)
        # Symbols without digits → stocks (#Apple, #Intel)
        if any(c.isdigit() for c in s[1:]):
            return 'Market Index'
        return 'Stock'
    su = s.upper()
    for k in _CRYPTO_SUBSTRINGS:
        if k in su:
            return 'Crypto'
    for k in _COMMODITY_PREFIXES:
        if su.startswith(k):
            return 'Commodity'
    if len(su) == 6 and su[:3] in _FX_CURRENCIES and su[3:] in _FX_CURRENCIES:
        return 'FX Spot'
    return 'Stock'


# ── PDF format detection ─────────────────────────────────────────────────────

def _detect_format(pdf_bytes: bytes) -> str:
    """Return 'MT5', 'MT5_POS', 'MT4', or 'unknown'.

    MT5_POS = MT5 Positions statement (open/closed positions table, no Deals section).
    """
    full_text = ''
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ''
            full_text += text + '\n'

            # Strategy 1 – text keywords
            if ('Direction' in text and 'Deal' in text) or ('MT5' in text and 'Deals' in text):
                return 'MT5'
            if ('Ticket' in text and 'Open Time' in text) or ('MT4' in text and 'Close Time' in text):
                return 'MT4'

            # Strategy 2 – table column headers (handles HTML-rendered / text-only PDFs)
            for settings in (_TABLE_SETTINGS[0], _TABLE_SETTINGS[2]):   # default + text/text
                try:
                    for table in (page.extract_tables(settings) or []):
                        for row in (table or []):
                            if not row:
                                continue
                            cells = {str(c).strip() for c in row if c}
                            cells_l = {c.lower() for c in cells}
                            if 'Direction' in cells and ('Deal' in cells or 'Symbol' in cells):
                                return 'MT5'
                            if 'position' in cells_l and 'symbol' in cells_l and (
                                'type' in cells_l or 'volume' in cells_l
                            ):
                                return 'MT5_POS'
                            if any('Open Time' in c for c in cells) and any('Close Time' in c for c in cells):
                                return 'MT4'
                except Exception:
                    continue

    # Strategy 3 – loose text fallback
    if 'MT5' in full_text:
        # Distinguish Positions-only report by absence of the Deals marker
        if 'Position' in full_text and 'Deal' not in full_text:
            return 'MT5_POS'
        return 'MT5'
    if 'MT4' in full_text:
        return 'MT4'

    return 'unknown'


# ── MT5 parsing ──────────────────────────────────────────────────────────────

_MT5_DEAL_HEADER_KEYS = {'time', 'deal', 'symbol', 'type', 'direction', 'volume', 'price'}

# Map any variation pdfplumber might extract → canonical column name
_MT5_CANONICAL_COLS = {
    'time': 'Time', 'deal': 'Deal', 'symbol': 'Symbol', 'type': 'Type',
    'direction': 'Direction', 'dir': 'Direction', 'dir.': 'Direction',
    'volume': 'Volume', 'vol': 'Volume', 'vol.': 'Volume',
    'price': 'Price', 'order': 'Order',
    'commission': 'Commission', 'comm': 'Commission', 'comm.': 'Commission',
    'fee': 'Fee', 'swap': 'Swap', 'profit': 'Profit',
    'balance': 'Balance', 'comment': 'Comment',
}

# Table extraction settings to try in order; the large HTML-rendered MT5 PDF
# may not have visible borders, so we fall back to text-alignment strategies.
_TABLE_SETTINGS = [
    {},  # pdfplumber defaults (line-based)
    {'vertical_strategy': 'text', 'horizontal_strategy': 'lines_strict'},
    {'vertical_strategy': 'text', 'horizontal_strategy': 'text'},
    {'vertical_strategy': 'lines_strict', 'horizontal_strategy': 'text'},
]


def _is_mt5_deal_header(row: list) -> bool:
    row_lower = {str(c).strip().lower() for c in row if c}
    # Require at least 5 matching keys (relaxed from 6) so that minor variations
    # in column sets (e.g., no Fee column) still match.
    return len(_MT5_DEAL_HEADER_KEYS & row_lower) >= 5


def _find_best_mt5_strategy(pdf_bytes: bytes) -> dict:
    """Scan up to the first 10 pages with each extraction strategy and return
    the settings that first produces a Deals header row.  Falls back to the
    default settings if none of them find a header."""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        probe_pages = pdf.pages[:10]
        for settings in _TABLE_SETTINGS:
            for page in probe_pages:
                try:
                    for table in (page.extract_tables(settings) or []):
                        for row in (table or []):
                            if row and _is_mt5_deal_header(
                                [str(c).strip() if c else '' for c in row]
                            ):
                                return settings
                except Exception:
                    continue
    return _TABLE_SETTINGS[0]


def _parse_mt5_date(s: str):
    s = str(s).strip()[:19]  # drop milliseconds
    for fmt in ('%Y.%m.%d %H:%M:%S', '%Y.%m.%d'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def _parse_mt5_pdf(pdf_bytes: bytes) -> pd.DataFrame:
    """Extract the Deals table from an MT5 statement PDF.

    First probes the PDF to find which table extraction strategy successfully
    locates the Deals header, then uses that strategy for the full parse.
    This handles both line-bordered and HTML-rendered (text-aligned) PDFs.
    """
    strategy = _find_best_mt5_strategy(pdf_bytes)
    deal_rows = []
    headers = None

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            try:
                tables = page.extract_tables(strategy) or []
            except Exception:
                tables = page.extract_tables() or []
            for table in tables:
                if not table:
                    continue
                for row in table:
                    if not row or all(c is None or str(c).strip() == '' for c in row):
                        continue
                    cleaned = [str(c).strip() if c is not None else '' for c in row]

                    if headers is None:
                        if _is_mt5_deal_header(cleaned):
                            headers = cleaned
                    else:
                        # Stop collecting if we hit a new section header:
                        # non-empty first cell, rest of visible cells empty, no digits
                        first = cleaned[0]
                        rest_empty = all(c == '' for c in cleaned[1:5])
                        if first and rest_empty and not any(c.isdigit() for c in first[:6]):
                            headers = None
                            continue
                        deal_rows.append(cleaned)

    if not headers or not deal_rows:
        return pd.DataFrame()

    # Canonicalize + deduplicate column names.
    # 1. Strip internal whitespace/newlines and map to canonical names.
    # 2. If the same canonical name appears twice, suffix the second with _1.
    seen_cols: dict = {}
    canonical = []
    for h in headers:
        h_norm = re.sub(r'\s+', ' ', h).strip()
        h_canon = _MT5_CANONICAL_COLS.get(h_norm.lower(), h_norm)
        if h_canon in seen_cols:
            seen_cols[h_canon] += 1
            canonical.append(f'{h_canon}_{seen_cols[h_canon]}')
        else:
            seen_cols[h_canon] = 0
            canonical.append(h_canon)

    n = len(canonical)
    norm = [(row + [''] * n)[:n] for row in deal_rows]
    df = pd.DataFrame(norm, columns=canonical)

    # Keep only rows whose Time matches a date pattern
    df = df[df['Time'].astype(str).str.match(r'^\d{4}\.\d{2}\.\d{2}', na=False)].copy()
    if df.empty:
        return df

    for col in ['Volume', 'Price', 'Commission', 'Fee', 'Swap', 'Profit', 'Balance']:
        if col in df.columns:
            df[col] = (
                df[col]
                .astype(str)
                .str.replace(r'\s', '', regex=True)
                .pipe(pd.to_numeric, errors='coerce')
                .fillna(0.0)
            )

    df['Date'] = df['Time'].apply(_parse_mt5_date)
    # Ensure all text columns exist and are clean strings, even if the PDF
    # omitted a column (e.g., some statements have no 'Direction' column).
    for col in ('Symbol', 'Type', 'Direction', 'Deal', 'Comment'):
        if col not in df.columns:
            df[col] = ''
        df[col] = df[col].astype(str).str.strip()
    df['Type']      = df['Type'].str.lower()
    df['Direction'] = df['Direction'].str.lower()

    return df


# ── MT5 Positions parsing ─────────────────────────────────────────────────────

_MT5_POS_HEADER_KEYS = {'position', 'symbol', 'type', 'volume', 'price'}

_MT5_POS_CANONICAL = {
    'position': 'Position', 'pos': 'Position',
    'time': 'Time', 'open time': 'Time',
    'symbol': 'Symbol',
    'type': 'Type',
    'volume': 'Volume', 'vol': 'Volume',
    'price': 'Price', 'open price': 'Price',
    's/l': 'SL', 'stop loss': 'SL',
    't/p': 'TP', 'take profit': 'TP',
    'current price': 'CurrentPrice', 'close price': 'CurrentPrice',
    'profit': 'Profit',
    'swap': 'Swap',
    'commission': 'Commission',
    'comment': 'Comment',
}


def _is_mt5_pos_header(row: list) -> bool:
    row_lower = {str(c).strip().lower() for c in row if c}
    return len(_MT5_POS_HEADER_KEYS & row_lower) >= 4


def _parse_mt5_positions_pdf(pdf_bytes: bytes) -> pd.DataFrame:
    """Extract the Positions table from an MT5 positions statement."""
    pos_rows = []
    headers = None
    strategy = _find_best_mt5_strategy.__wrapped__(pdf_bytes) if hasattr(
        _find_best_mt5_strategy, '__wrapped__') else {}

    # Probe for the best strategy (reuse helper but look for pos header)
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for settings in _TABLE_SETTINGS:
            for page in pdf.pages[:6]:
                try:
                    for table in (page.extract_tables(settings) or []):
                        for row in (table or []):
                            if row and _is_mt5_pos_header(
                                [str(c).strip() if c else '' for c in row]
                            ):
                                strategy = settings
                                raise StopIteration
                except StopIteration:
                    break
            else:
                continue
            break

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            try:
                tables = page.extract_tables(strategy) or []
            except Exception:
                tables = page.extract_tables() or []
            for table in tables:
                if not table:
                    continue
                for row in table:
                    if not row or all(c is None or str(c).strip() == '' for c in row):
                        continue
                    cleaned = [str(c).strip() if c is not None else '' for c in row]

                    if headers is None:
                        if _is_mt5_pos_header(cleaned):
                            headers = cleaned
                    else:
                        first = cleaned[0]
                        rest_empty = all(c == '' for c in cleaned[1:5])
                        if first and rest_empty and not any(c.isdigit() for c in first[:6]):
                            headers = None
                            continue
                        pos_rows.append(cleaned)

    if not headers or not pos_rows:
        return pd.DataFrame()

    seen_cols: dict = {}
    canonical = []
    for h in headers:
        h_norm = re.sub(r'\s+', ' ', h).strip()
        h_canon = _MT5_POS_CANONICAL.get(h_norm.lower(), h_norm)
        if h_canon in seen_cols:
            seen_cols[h_canon] += 1
            canonical.append(f'{h_canon}_{seen_cols[h_canon]}')
        else:
            seen_cols[h_canon] = 0
            canonical.append(h_canon)

    n = len(canonical)
    norm = [(row + [''] * n)[:n] for row in pos_rows]
    df = pd.DataFrame(norm, columns=canonical)

    # Keep rows with a numeric Position id
    if 'Position' in df.columns:
        df = df[df['Position'].str.match(r'^\d+', na=False)].copy()
    if df.empty:
        return df

    for col in ['Volume', 'Price', 'CurrentPrice', 'Profit', 'Swap', 'Commission']:
        if col in df.columns:
            df[col] = (
                df[col].astype(str)
                .str.replace(r'\s', '', regex=True)
                .pipe(pd.to_numeric, errors='coerce')
                .fillna(0.0)
            )

    if 'Time' in df.columns:
        df['Date'] = df['Time'].apply(_parse_mt5_date)
    else:
        from datetime import date as _date
        df['Date'] = _date.today()

    for col in ('Symbol', 'Type', 'Position', 'Comment'):
        if col not in df.columns:
            df[col] = ''
        df[col] = df[col].astype(str).str.strip()
    df['Type'] = df['Type'].str.lower()

    return df


def _build_mt5_position_records(df: pd.DataFrame) -> list:
    """Each open position → one closing investment record.

    A 'buy' (long) position is closed by a Sell; a 'sell' (short) by a Buy.
    The close price used is CurrentPrice if available, else the open Price.
    This zeroes out the unbalanced holdings left by the Deals statements.
    """
    inv_records = []

    for _, row in df.iterrows():
        symbol = row.get('Symbol', '').strip()
        pos_id = row.get('Position', '').strip()
        pos_type = row.get('Type', '').lower()
        volume = abs(float(row.get('Volume', 0) or 0))
        open_price = float(row.get('Price', 0) or 0)
        close_price = float(row.get('CurrentPrice', 0) or 0) or open_price
        profit = float(row.get('Profit', 0) or 0)
        swap = float(row.get('Swap', 0) or 0)
        commission = float(row.get('Commission', 0) or 0)
        dt = row.get('Date')

        if not symbol or volume == 0 or pos_type not in ('buy', 'sell') or not dt:
            continue

        # Closing action is opposite of position direction
        close_action = 'Sell' if pos_type == 'buy' else 'Buy'
        total_eur = round(volume * close_price, 4)

        inv_records.append({
            'desc': f'{_FXP_PREFIX}MT5|POS|{pos_id}',
            'symbol': symbol,
            'date': dt,
            'action': close_action,
            'quantity': round(volume, 6),
            'price': round(close_price, 5),
            'total_eur': total_eur,
        })

        # Book any net P&L (profit + swap) as MiscExp
        net_pnl = profit + swap
        if abs(net_pnl) > 0.001:
            inv_records.append({
                'desc': f'{_FXP_PREFIX}MT5|POS|PNL|{pos_id}',
                'symbol': symbol,
                'date': dt,
                'action': 'MiscExp',
                'quantity': 1.0,
                'price': round(abs(net_pnl), 4),
                'total_eur': round(abs(net_pnl), 2),
            })

        if abs(commission) > 0.001:
            inv_records.append({
                'desc': f'{_FXP_PREFIX}MT5|POS|COMM|{pos_id}',
                'symbol': symbol,
                'date': dt,
                'action': 'MiscExp',
                'quantity': 1.0,
                'price': round(abs(commission), 4),
                'total_eur': round(abs(commission), 2),
            })

    return inv_records


def _build_mt5_records(df: pd.DataFrame) -> tuple:
    """One investment record per trade deal; one transaction per balance deal."""
    inv_records = []
    tx_records = []

    for _, row in df.iterrows():
        symbol = row.get('Symbol', '').strip()
        deal_id = row.get('Deal', '').strip()
        dt = row.get('Date')
        deal_type = row.get('Type', '').lower()
        direction = row.get('Direction', '').lower()
        volume = abs(float(row.get('Volume', 0) or 0))
        price = float(row.get('Price', 0) or 0)
        commission = float(row.get('Commission', 0) or 0)
        fee = float(row.get('Fee', 0) or 0)
        swap = float(row.get('Swap', 0) or 0)
        profit = float(row.get('Profit', 0) or 0)

        if not dt or not deal_id:
            continue

        # Balance/deposit/withdrawal rows have no symbol
        if not symbol:
            if abs(profit) >= 0.01:
                label = 'Deposit' if profit > 0 else 'Withdrawal'
                tx_records.append({
                    'date': dt,
                    'amount': profit,
                    'description': f'FxPro MT5 {label} [{deal_id}]',
                })
            continue

        if volume == 0:
            continue

        # Determine action from direction + type
        # buy-in  = opening a long  → Buy
        # sell-in = opening a short → Sell
        # sell-out = closing a long  → Sell
        # buy-out  = closing a short → Buy
        if direction == 'in':
            action = 'Buy' if deal_type == 'buy' else 'Sell'
        elif direction == 'out':
            action = 'Sell' if deal_type == 'sell' else 'Buy'
        else:
            continue  # unrecognised direction

        total_eur = round(volume * price, 4)

        inv_records.append({
            'desc': f'{_FXP_PREFIX}MT5|{deal_id}',
            'symbol': symbol,
            'date': dt,
            'action': action,
            'quantity': round(volume, 6),
            'price': round(price, 5),
            'total_eur': total_eur,
        })

        # Commission + fee as MiscExp
        comm_total = abs(commission) + abs(fee)
        if comm_total > 0.001:
            inv_records.append({
                'desc': f'{_FXP_PREFIX}MT5|COMM|{deal_id}',
                'symbol': symbol,
                'date': dt,
                'action': 'MiscExp',
                'quantity': 1.0,
                'price': round(comm_total, 4),
                'total_eur': round(comm_total, 2),
            })

        # Swap as MiscExp
        if abs(swap) > 0.001:
            inv_records.append({
                'desc': f'{_FXP_PREFIX}MT5|SWAP|{deal_id}',
                'symbol': symbol,
                'date': dt,
                'action': 'MiscExp',
                'quantity': 1.0,
                'price': round(abs(swap), 4),
                'total_eur': round(abs(swap), 2),
            })

    return inv_records, tx_records


# ── MT4 parsing ──────────────────────────────────────────────────────────────

_MT4_HEADER_KEYS = {'ticket', 'open time', 'close time', 'type', 'volume'}


def _is_mt4_trade_header(row: list) -> bool:
    row_lower = {str(c).strip().lower() for c in row if c}
    return len(_MT4_HEADER_KEYS & row_lower) >= 4


def _parse_mt4_date(s: str):
    s = str(s).strip()
    for fmt in ('%m/%d/%Y %I:%M:%S %p', '%m/%d/%Y %H:%M:%S',
                '%Y.%m.%d %H:%M:%S', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


_MT4_COL_MAP = {
    'ticket':      'ticket',
    'open time':   'open_time',
    'type':        'type',
    'volume':      'volume',
    'size':        'volume',
    'item':        'item',
    'symbol':      'item',
    'open price':  'open_price',
    's/l':         'sl',
    't/p':         'tp',
    'close time':  'close_time',
    'close price': 'close_price',
    'commission':  'commission',
    'taxes':       'taxes',
    'swap':        'swap',
    'profit':      'profit',
    'comment':     'comment',
    'account':     'account',
}


def _parse_mt4_pdf(pdf_bytes: bytes) -> pd.DataFrame:
    """Extract the closed-trades table from an MT4 statement PDF."""
    all_rows = []
    headers = None

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            for table in (page.extract_tables() or []):
                if not table:
                    continue
                for row in table:
                    if not row or all(c is None or str(c).strip() == '' for c in row):
                        continue
                    cleaned = [str(c).strip() if c is not None else '' for c in row]

                    if headers is None:
                        if _is_mt4_trade_header(cleaned):
                            headers = [c.lower() for c in cleaned]
                    else:
                        # Stop at summary/footer rows (first cell not numeric)
                        first = cleaned[0]
                        if first and not first.isdigit() and not re.match(r'^\d+$', first):
                            # Check if it looks like a section footer/summary
                            if any(kw in first.lower() for kw in ('closed', 'open', 'deposit', 'total', 'summary')):
                                continue
                        all_rows.append(cleaned)

    if not headers or not all_rows:
        return pd.DataFrame()

    n = len(headers)
    norm = [(row + [''] * n)[:n] for row in all_rows]
    df = pd.DataFrame(norm, columns=headers)

    # Rename columns to standard names
    rename = {}
    for col in df.columns:
        for key, std in _MT4_COL_MAP.items():
            if key in col:
                rename[col] = std
                break
    df = df.rename(columns=rename)

    # Keep rows with numeric ticket
    if 'ticket' in df.columns:
        df = df[df['ticket'].str.match(r'^\d+$', na=False)].copy()

    for col in ('volume', 'open_price', 'close_price', 'commission', 'taxes', 'swap', 'profit'):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0.0)

    if 'open_time' in df.columns:
        df['open_date'] = df['open_time'].apply(_parse_mt4_date)
    if 'close_time' in df.columns:
        df['close_date'] = df['close_time'].apply(_parse_mt4_date)

    if 'type' in df.columns:
        df['type'] = df['type'].str.strip().str.lower()

    return df


def _extract_symbol_from_comment(comment: str) -> str | None:
    """Return an FxPro instrument symbol found in an MT4 balance comment, or None."""
    if not comment:
        return None
    s = comment.strip()
    # #Name pattern (stocks / indices: #Apple, #USSPX500, #UK100 …)
    m = re.search(r'(#[A-Za-z][A-Za-z0-9]+)', s)
    if m:
        return m.group(1)
    su = s.upper()
    # Known commodity prefixes (GOLD, GOLDgr, BRENT, WTI …)
    for kw in _COMMODITY_PREFIXES:
        if kw in su:
            m2 = re.search(r'\b' + kw + r'[A-Za-z0-9]*\b', su)
            if m2:
                return m2.group(0)
    # FX pair: exactly 6 uppercase letters, both halves are currency codes
    for m3 in re.finditer(r'\b([A-Z]{6})\b', su):
        pair = m3.group(1)
        if pair[:3] in _FX_CURRENCIES and pair[3:] in _FX_CURRENCIES:
            return pair
    return None


def _build_mt4_records(df: pd.DataFrame) -> tuple:
    """Each trade row → Buy+Sell pair.

    Balance rows: if the Comment mentions a security symbol they become MiscExp
    investment records; otherwise they become Transactions (deposit/withdrawal).
    """
    inv_records = []
    tx_records = []

    for _, row in df.iterrows():
        ticket = str(row.get('ticket', '')).strip()
        trade_type = str(row.get('type', '')).lower().strip()
        profit = float(row.get('profit', 0) or 0)

        if trade_type in ('balance', 'credit', 'deposit', 'withdrawal'):
            if abs(profit) >= 0.01:
                open_date = row.get('open_date')
                close_date = row.get('close_date')
                dt = open_date or close_date
                comment = str(row.get('comment', '')).strip()
                sym = _extract_symbol_from_comment(comment) if trade_type == 'balance' else None
                if sym:
                    # Balance row references a security → MiscExp investment
                    inv_records.append({
                        'desc': f'{_FXP_PREFIX}MT4|BAL|{ticket}',
                        'symbol': sym,
                        'date': dt,
                        'action': 'MiscExp',
                        'quantity': 1.0,
                        'price': round(abs(profit), 4),
                        'total_eur': round(abs(profit), 2),
                    })
                else:
                    label = trade_type.capitalize()
                    tx_records.append({
                        'date': dt,
                        'amount': profit,
                        'description': f'FxPro MT4 {label} [{ticket}]',
                    })
            continue

        if trade_type not in ('buy', 'sell'):
            continue

        symbol = str(row.get('item', '')).strip()
        volume = abs(float(row.get('volume', 0) or 0))
        open_price = float(row.get('open_price', 0) or 0)
        close_price = float(row.get('close_price', 0) or 0)
        commission = float(row.get('commission', 0) or 0)
        taxes = float(row.get('taxes', 0) or 0)
        swap = float(row.get('swap', 0) or 0)
        open_date = row.get('open_date')
        close_date = row.get('close_date')

        if not symbol or volume == 0 or not open_date or not close_date:
            continue

        is_long = trade_type == 'buy'
        open_action = 'Buy' if is_long else 'Sell'
        close_action = 'Sell' if is_long else 'Buy'

        open_total = round(volume * open_price, 4)
        close_total = round(volume * close_price, 4)

        inv_records.append({
            'desc': f'{_FXP_PREFIX}MT4|OPEN|{ticket}',
            'symbol': symbol,
            'date': open_date,
            'action': open_action,
            'quantity': round(volume, 6),
            'price': round(open_price, 5),
            'total_eur': open_total,
        })

        inv_records.append({
            'desc': f'{_FXP_PREFIX}MT4|CLOSE|{ticket}',
            'symbol': symbol,
            'date': close_date,
            'action': close_action,
            'quantity': round(volume, 6),
            'price': round(close_price, 5),
            'total_eur': close_total,
        })

        comm_total = abs(commission) + abs(taxes)
        if comm_total > 0.001:
            inv_records.append({
                'desc': f'{_FXP_PREFIX}MT4|COMM|{ticket}',
                'symbol': symbol,
                'date': open_date,
                'action': 'MiscExp',
                'quantity': 1.0,
                'price': round(comm_total, 4),
                'total_eur': round(comm_total, 2),
            })

        if abs(swap) > 0.001:
            inv_records.append({
                'desc': f'{_FXP_PREFIX}MT4|SWAP|{ticket}',
                'symbol': symbol,
                'date': close_date,
                'action': 'MiscExp',
                'quantity': 1.0,
                'price': round(abs(swap), 4),
                'total_eur': round(abs(swap), 2),
            })

    return inv_records, tx_records


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
           VALUES (%s, 'Margin', 0,
                   (SELECT Currencies_Id FROM Currencies
                    WHERE Currencies_ShortName = 'EUR' LIMIT 1))
           RETURNING Accounts_Id""",
        (name,),
    )
    return cur.fetchone()[0]


def _get_or_create_security(cur, symbol: str,
                             _cached_mappings: dict | None = None) -> int:
    """Resolve or create a Security record for *symbol*.

    Match priority:
      0. Saved mapping in import_security_mappings (user-defined override)
      1. Ticker match in Securities
      2. Create new security
    """
    # 0. Saved mapping
    if _cached_mappings is None:
        from database.queries import get_security_mappings as _get_map
        _cached_mappings = _get_map("FxPro")
    if symbol in _cached_mappings:
        return _cached_mappings[symbol]

    # 1. Ticker match
    cur.execute(
        "SELECT Securities_Id FROM Securities WHERE Ticker = %s LIMIT 1", (symbol,)
    )
    row = cur.fetchone()
    if row:
        return row[0]

    # 2. Create new
    sec_type = _classify_security(symbol)
    name = symbol.lstrip('#')
    cur.execute(
        """INSERT INTO Securities (Ticker, Securities_Name, Securities_Type, Currencies_Id)
           VALUES (%s, %s, %s,
                   (SELECT Currencies_Id FROM Currencies
                    WHERE Currencies_ShortName = 'EUR' LIMIT 1))
           RETURNING Securities_Id""",
        (symbol, name, sec_type),
    )
    return cur.fetchone()[0]


def _investment_exists(cur, acc_id: int, description: str) -> bool:
    cur.execute(
        "SELECT 1 FROM Investments WHERE Accounts_Id = %s AND Description = %s LIMIT 1",
        (acc_id, description),
    )
    return cur.fetchone() is not None


def _transaction_exists(cur, acc_id: int, description: str) -> bool:
    cur.execute(
        "SELECT 1 FROM Transactions WHERE Accounts_Id = %s AND Description = %s LIMIT 1",
        (acc_id, description),
    )
    return cur.fetchone() is not None


# ── Security mapping helpers ──────────────────────────────────────────────────

def _load_db_securities() -> pd.DataFrame:
    conn = get_connection()
    df = pd.read_sql(
        """SELECT s.Securities_Id  AS sec_id,
                  s.Ticker         AS ticker,
                  s.Securities_Name AS name,
                  s.Securities_Type AS sec_type,
                  c.Currencies_ShortName AS currency
           FROM Securities s
           JOIN Currencies c ON s.Currencies_Id = c.Currencies_Id
           ORDER BY s.Securities_Name""",
        conn,
    )
    conn.close()
    return df


def _best_db_match(symbol: str, db_df: pd.DataFrame) -> dict | None:
    """Return the first DB row that matches symbol, or None."""
    for candidate in (symbol, symbol.lstrip('#')):
        m = db_df[db_df['ticker'].str.lower() == candidate.lower()]
        if not m.empty:
            return m.iloc[0].to_dict()
    return None


def _render_security_mapping(symbols: list, db_df: pd.DataFrame) -> dict:
    """Render the security-mapping table.

    Returns ``sec_id_map = {pdf_symbol: int | None}`` where *None* means
    'create new security on import'.

    Mappings are now stored in the ``import_security_mappings`` DB table
    (source = 'FxPro') instead of the legacy JSON file.
    """
    _NONE_LABEL = '— Create new —'

    db_options = [_NONE_LABEL] + [
        f"{r['ticker']} | {r['name']} | {r['currency']}"
        for _, r in db_df.iterrows()
    ]
    label_to_id = {
        f"{r['ticker']} | {r['name']} | {r['currency']}": int(r['sec_id'])
        for _, r in db_df.iterrows()
    }
    id_to_label = {v: k for k, v in label_to_id.items()}

    # Load saved {symbol → sec_id} from DB (includes one-time JSON migration)
    saved_ids = _load_saved_mapping()

    rows = []
    for sym in symbols:
        match = _best_db_match(sym, db_df)
        if match:
            auto_label = f"{match['ticker']} | {match['name']} | {match['currency']}"
            db_ticker, db_name, db_currency = match['ticker'], match['name'], match['currency']
        else:
            auto_label = _NONE_LABEL
            db_ticker = db_name = db_currency = ''

        # Priority: saved DB mapping > auto-match by ticker > Create new
        saved_sid = saved_ids.get(sym)
        if saved_sid is not None:
            default = id_to_label.get(int(saved_sid), auto_label)
        else:
            default = auto_label

        rows.append({
            'PDF Symbol':   sym,
            'PDF Type':     _classify_security(sym),
            'DB Ticker':    db_ticker,
            'DB Name':      db_name,
            'DB Currency':  db_currency,
            'Map to':       default,
        })

    mapping_df = pd.DataFrame(rows)

    edited = st.data_editor(
        mapping_df,
        column_config={
            'PDF Symbol':  st.column_config.TextColumn('PDF Symbol',  disabled=True),
            'PDF Type':    st.column_config.TextColumn('PDF Type',    disabled=True),
            'DB Ticker':   st.column_config.TextColumn('DB Ticker',   disabled=True),
            'DB Name':     st.column_config.TextColumn('DB Name',     disabled=True),
            'DB Currency': st.column_config.TextColumn('DB Currency', disabled=True),
            'Map to':      st.column_config.SelectboxColumn(
                'Map to DB Security',
                options=db_options,
                required=True,
                help='Select an existing DB security or leave as "— Create new —".',
            ),
        },
        hide_index=True,
        width="stretch",
        key='fxp_sec_mapping',
    )

    sec_id_map = {}
    for _, row in edited.iterrows():
        sel = row['Map to']
        sec_id_map[row['PDF Symbol']] = label_to_id.get(sel)   # None → create new
    return sec_id_map


# ── Per-file parse cache ──────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def _cached_parse_pdf(pdf_bytes: bytes) -> tuple:
    """Parse one FxPro PDF and return (fmt, inv_records, tx_records, error_str).

    Decorated with @st.cache_data so the expensive PDF parsing only runs once
    per unique file; Streamlit reruns caused by UI interactions hit the cache.
    """
    fmt = _detect_format(pdf_bytes)
    if fmt == 'MT5':
        df = _parse_mt5_pdf(pdf_bytes)
        if df.empty:
            return fmt, [], [], 'No deals table found in MT5 PDF.'
        inv, tx = _build_mt5_records(df)
        return fmt, inv, tx, None
    elif fmt == 'MT5_POS':
        df = _parse_mt5_positions_pdf(pdf_bytes)
        if df.empty:
            return fmt, [], [], 'No positions table found in MT5 Positions PDF.'
        inv = _build_mt5_position_records(df)
        return fmt, inv, [], None
    elif fmt == 'MT4':
        df = _parse_mt4_pdf(pdf_bytes)
        if df.empty:
            return fmt, [], [], 'No trades table found in MT4 PDF.'
        inv, tx = _build_mt4_records(df)
        return fmt, inv, tx, None
    else:
        return fmt, [], [], (
            'Could not detect MT4/MT5 format. '
            'Check that the PDF is an FxPro account statement.'
        )


# ── Main import function ──────────────────────────────────────────────────────

def run_import(
    pdf_bytes_list: list,
    account_id: int,
    replace_mode: bool = False,
    security_map: dict | None = None,
    progress_cb=None,
) -> dict:
    """Import one or more FxPro PDF statements into the database."""
    all_inv = []
    all_tx = []
    fmt_used = set()

    for pdf_bytes in pdf_bytes_list:
        fmt = _detect_format(pdf_bytes)
        fmt_used.add(fmt)
        if fmt == 'MT5':
            df = _parse_mt5_pdf(pdf_bytes)
            if not df.empty:
                inv, tx = _build_mt5_records(df)
                all_inv.extend(inv)
                all_tx.extend(tx)
        elif fmt == 'MT5_POS':
            df = _parse_mt5_positions_pdf(pdf_bytes)
            if not df.empty:
                all_inv.extend(_build_mt5_position_records(df))
        elif fmt == 'MT4':
            df = _parse_mt4_pdf(pdf_bytes)
            if not df.empty:
                inv, tx = _build_mt4_records(df)
                all_inv.extend(inv)
                all_tx.extend(tx)
        else:
            raise ValueError("Could not detect MT4/MT5 format in one of the uploaded PDFs.")

    # Deduplicate by description (in case same PDF uploaded twice)
    seen = {}
    for rec in all_inv:
        seen.setdefault(rec['desc'], rec)
    all_inv = list(seen.values())

    conn = get_connection()
    cur = conn.cursor()
    counts = {
        'investments': 0, 'investments_skip': 0,
        'transactions': 0, 'transactions_skip': 0,
        'deleted_investments': 0, 'deleted_transactions': 0,
        'formats': sorted(fmt_used),
    }

    try:
        if replace_mode:
            cur.execute("DELETE FROM Investments WHERE Accounts_Id = %s", (account_id,))
            counts['deleted_investments'] = cur.rowcount
            cur.execute("DELETE FROM Transactions WHERE Accounts_Id = %s", (account_id,))
            counts['deleted_transactions'] = cur.rowcount

        total = len(all_inv) + len(all_tx)
        done = 0

        # Merge UI-supplied overrides on top of DB mappings (UI wins).
        # Load DB mappings once to avoid a round-trip per record.
        from database.queries import get_security_mappings as _get_sec_map
        _fxp_mappings = _get_sec_map("FxPro")
        if security_map:
            _fxp_mappings = {**_fxp_mappings, **{k: v for k, v in security_map.items() if v is not None}}

        for rec in all_inv:
            sec_id = _get_or_create_security(cur, rec['symbol'], _cached_mappings=_fxp_mappings)
            if not replace_mode and _investment_exists(cur, account_id, rec['desc']):
                counts['investments_skip'] += 1
            else:
                # FxPro is EUR-based; all amounts already in EUR → FX_Rate = 1.0
                cur.execute(
                    """INSERT INTO Investments
                           (Accounts_Id, Securities_Id, Date, Action, Quantity,
                            Price_Per_Share,
                            Total_Amount_AccCur, Total_Amount_SecCur, FX_Rate,
                            Description)
                       VALUES (%s, %s, %s, %s::investments_action, %s, %s, %s, %s, %s, %s)""",
                    (account_id, sec_id, rec['date'], rec['action'],
                     rec['quantity'], rec['price'],
                     rec['total_eur'], rec['total_eur'], 1.0,
                     rec['desc']),
                )
                counts['investments'] += 1
            done += 1
            if progress_cb and done % 50 == 0:
                progress_cb(done / total)

        for rec in all_tx:
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
    _fx_linked = get_linked_account_id(account_id)
    if _fx_linked:
        create_linked_cash_transactions_for_unlinked(account_id, _fx_linked)
        update_accounts_balances(_fx_linked)
        update_investment_balances()

    return counts


# ── Streamlit UI ──────────────────────────────────────────────────────────────

def render_fxpro_importer():
    st.subheader("FxPro PDF Statement Importer")
    st.caption(
        "Import FxPro MT4 and MT5 trade statements from PDF. "
        "Upload multiple PDFs at once to import several statements for the same account."
    )

    # ── Restore last-used settings (first render only) ───────────────────────
    from database.queries import get_app_setting, save_app_setting
    if "fxp_acc_mode" not in st.session_state:
        st.session_state["fxp_acc_mode"] = get_app_setting("fxp_acc_mode") or "Existing"
    if "fxp_acc_select" not in st.session_state:
        _saved = get_app_setting("fxp_acc_select")
        if _saved:
            st.session_state["fxp_acc_select"] = _saved
    if "fxp_replace_mode" not in st.session_state:
        st.session_state["fxp_replace_mode"] = get_app_setting("fxp_replace_mode") == "true"

    pdf_files = st.file_uploader(
        "FxPro Statement PDFs",
        type="pdf",
        accept_multiple_files=True,
        key="fxp_pdf_files",
    )

    if not pdf_files:
        st.info("Upload one or more FxPro statement PDFs to continue.")
        return

    all_pdf_bytes = [f.read() for f in pdf_files]

    # Parse PDFs — results are cached per unique file content so that
    # Streamlit reruns (e.g., while editing the mapping table) are instant.
    fmt_list = []
    parse_errors = []
    all_inv = []
    all_tx = []

    with st.spinner("Parsing PDFs…"):
        for pdf_bytes, f in zip(all_pdf_bytes, pdf_files):
            fmt, inv, tx, err = _cached_parse_pdf(pdf_bytes)
            fmt_list.append(fmt)
            if err:
                parse_errors.append(f"{f.name}: {err}")
            else:
                all_inv.extend(inv)
                all_tx.extend(tx)

    for err in parse_errors:
        st.error(err)

    if not all_inv and not all_tx:
        st.warning("No records were parsed from the uploaded PDFs.")
        return

    # ── Summary ───────────────────────────────────────────────────────────────
    inv_df = pd.DataFrame(all_inv)
    symbols = sorted(inv_df['symbol'].unique()) if not inv_df.empty else []
    date_min = inv_df['date'].min() if not inv_df.empty else None
    date_max = inv_df['date'].max() if not inv_df.empty else None

    buy_sell = inv_df[inv_df['action'].isin(['Buy', 'Sell'])] if not inv_df.empty else pd.DataFrame()
    misc_exp = inv_df[inv_df['action'] == 'MiscExp'] if not inv_df.empty else pd.DataFrame()

    misc_total = misc_exp['total_eur'].sum() if not misc_exp.empty else 0.0
    tx_total = sum(r['amount'] for r in all_tx)

    _FMT_LABELS = {'MT5': 'MT5 Deals', 'MT5_POS': 'MT5 Positions', 'MT4': 'MT4', 'unknown': 'unknown'}
    fmt_str = ', '.join(sorted({_FMT_LABELS.get(f, f) for f in fmt_list}))
    files_str = ', '.join(f.name for f in pdf_files)

    st.markdown(f"""
| | |
|---|---|
| **Files** | {files_str} |
| **Formats detected** | {fmt_str} |
| **Date range** | {date_min} → {date_max} |
| **Trade records** | {len(buy_sell)} (Buy/Sell entries) |
| **Cost records** | {len(misc_exp)} (commission/swap · total {misc_total:,.2f}) |
| **Transaction records** | {len(all_tx)} (deposits/withdrawals · total {tx_total:,.2f}) |
| **Instruments** | {len(symbols)} |
""")

    # ── Open-position warning ─────────────────────────────────────────────────
    # Detect securities where Buy qty ≠ Sell qty — these will produce a non-zero
    # holding after import, which means the statements don't cover their full
    # open-to-close lifecycle (positions open at statement date, or missing PDFs).
    if not buy_sell.empty:
        net = (
            buy_sell.assign(
                signed=lambda d: d.apply(
                    lambda r: r['quantity'] if r['action'] == 'Buy' else -r['quantity'], axis=1
                )
            )
            .groupby('symbol')['signed'].sum()
        )
        unbalanced = net[net.abs() > 0.000001]
        if not unbalanced.empty:
            lines = ', '.join(
                f"**{sym}** ({qty:+.4g} lots)" for sym, qty in unbalanced.items()
            )
            st.warning(
                f"⚠ {len(unbalanced)} symbol(s) have unmatched open/close deals in the "
                f"uploaded PDFs: {lines}. "
                "These will show a non-zero holding after import. "
                "If all positions should be closed, upload the missing statement(s) "
                "and re-import with **Replace** mode."
            )

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
        mode = st.radio("Account", ["Existing", "New"], key="fxp_acc_mode")
    with col_acc:
        if mode == "Existing":
            acc_options = dict(zip(acc_df['accounts_name'], acc_df['accounts_id']))
            default_idx = next(
                (i for i, n in enumerate(acc_df['accounts_name']) if 'fxpro' in n.lower()),
                0,
            )
            selected_name = st.selectbox(
                "Select account",
                list(acc_options.keys()),
                index=default_idx,
                key="fxp_acc_select",
            )
            account_id = acc_options[selected_name]
        else:
            new_name = st.text_input("New account name", value="FxPro", key="fxp_acc_name")
            account_id = None

    st.divider()

    # ── Security mapping ──────────────────────────────────────────────────────
    st.markdown("#### Security Mapping")
    st.caption(
        "Review how each FxPro symbol maps to a security in your database. "
        "The **DB** columns show the best automatic match by ticker. "
        "Override **Map to DB Security** to link to a different existing security, "
        "or leave as '— Create new —' to insert a new one on import. "
        "Mappings are stored in the database and applied automatically on future imports."
    )
    db_df = _load_db_securities()
    security_map = _render_security_mapping(symbols, db_df)

    col_save_map, _ = st.columns([1, 5])
    with col_save_map:
        if st.button("💾 Save Mapping", key="fxp_save_map_btn"):
            _save_mapping(security_map)
            st.success("Mapping saved to database.")

    st.divider()

    # ── Options ───────────────────────────────────────────────────────────────
    replace_mode = st.checkbox(
        "Replace: delete ALL account data before import",
        value=False,
        key="fxp_replace_mode",
        help=(
            "Deletes ALL investments and transactions for the selected account before importing. "
            "Use this to cleanly re-import the full FxPro history."
        ),
    )

    # ── Import ────────────────────────────────────────────────────────────────
    if st.button("⬆ Import", type="primary", key="fxp_import_btn"):
        resolved_account_id = account_id
        if mode == "New":
            conn2 = get_connection()
            cur2 = conn2.cursor()
            try:
                resolved_account_id = _get_or_create_account(cur2, new_name)
                conn2.commit()
            finally:
                cur2.close()
                conn2.close()

        progress = st.progress(0.0, text="Importing…")

        try:
            counts = run_import(
                pdf_bytes_list=all_pdf_bytes,
                account_id=resolved_account_id,
                replace_mode=replace_mode,
                security_map=security_map,
                progress_cb=lambda p: progress.progress(p, text="Importing…"),
            )
            _save_mapping(security_map)   # persist mapping after every successful import
            try:
                save_app_setting("fxp_acc_mode",    mode)
                save_app_setting("fxp_acc_select",  selected_name if mode == "Existing" else "")
                save_app_setting("fxp_replace_mode", "true" if replace_mode else "false")
            except Exception:
                pass
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
            st.rerun()
        except Exception as e:
            progress.empty()
            st.error(f"Import failed: {e}")
            st.exception(e)
