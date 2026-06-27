"""Pure-Python bank statement parser — no Streamlit dependency.

Extracted from ui/bank_import.py so it can be used by the FastAPI layer
without needing the Streamlit UI package (which is excluded from the Docker image).
"""

from __future__ import annotations

import io
import logging
import re

import pandas as pd

_log = logging.getLogger(__name__)


def _desc_key(desc: str) -> str:
    s = str(desc).strip().lower()
    s = re.sub(r'^\[[a-z]{3}\]\s*', '', s)
    s = re.sub(r'\s+', ' ', s)
    return s


def _parse_number(val, decimal_sep='.', thousands_sep=',') -> float | None:
    if pd.isna(val):
        return None
    s = str(val).strip()
    if not s or s in ('-', '—', ''):
        return None
    s = re.sub(r'[€$£\s]', '', s)
    if thousands_sep and thousands_sep in s:
        s = s.replace(thousands_sep, '')
    if decimal_sep != '.':
        s = s.replace(decimal_sep, '.')
    try:
        return float(s)
    except ValueError:
        return None


def parse_statement(file_bytes: bytes, file_name: str, profile: dict) -> pd.DataFrame:
    """Parse an uploaded bank statement file into a normalised DataFrame.

    Returns columns: date, description, amount (negative=debit, positive=credit), balance
    """
    ext = file_name.rsplit('.', 1)[-1].lower()
    dec  = profile.get('decimal_separator', '.')
    thou = profile.get('thousands_separator', ',')
    skip = int(profile.get('skip_rows', 0))

    try:
        if ext in ('xlsx', 'xls'):
            raw = pd.read_excel(io.BytesIO(file_bytes), skiprows=skip, header=0)
        else:
            enc = profile.get('encoding', 'utf-8')
            _enc_order = list(dict.fromkeys(
                [enc, 'utf-8-sig', 'windows-1253', 'iso-8859-7', 'latin-1']
            ))
            _sep_order = [None, ';', ',', '\t']
            raw = None
            for _enc in _enc_order:
                if raw is not None:
                    break
                for _sep in _sep_order:
                    try:
                        _kw: dict = {'skiprows': skip, 'header': 0, 'encoding': _enc}
                        if _sep is None:
                            _kw.update({'sep': None, 'engine': 'python'})
                        else:
                            _kw['sep'] = _sep
                        _candidate = pd.read_csv(io.BytesIO(file_bytes), **_kw)
                        if len(_candidate.columns) >= 2:
                            raw = _candidate
                            break
                    except Exception:
                        continue
            if raw is None:
                raise ValueError("Unable to read CSV with any supported encoding.")
    except Exception as e:
        _log.error("Failed to read file: %s", e)
        return pd.DataFrame()

    raw.columns = [str(c).strip().lstrip('﻿').strip('"').strip()
                   for c in raw.columns]

    date_col     = profile.get('date_column', '').strip()
    sec_date_col = profile.get('secondary_date_column', '').strip()
    desc_col     = profile.get('description_column', '').strip()
    deb_col      = profile.get('debit_column', '').strip()
    cre_col      = profile.get('credit_column', '').strip()
    amt_col      = profile.get('amount_column', '').strip()
    bal_col      = profile.get('balance_column', '').strip()
    inst_col     = profile.get('installment_column', '').strip()
    sign_conv    = profile.get('sign_convention', 'debit_credit')
    date_fmt     = profile.get('date_format', '%d/%m/%Y')

    missing = [c for c in [date_col, desc_col] if c and c not in raw.columns]
    if missing:
        _log.error("Column(s) not found in file: %s. Available: %s", missing, list(raw.columns))
        return pd.DataFrame()

    from datetime import timedelta
    rows = []
    for _, r in raw.iterrows():
        raw_date = r.get(date_col, '')
        try:
            txn_date = pd.to_datetime(str(raw_date).strip(), format=date_fmt, dayfirst=True).date()
        except Exception:
            try:
                txn_date = pd.to_datetime(str(raw_date).strip(), dayfirst=True).date()
            except Exception:
                continue

        orig_date_suffix = ''
        if sec_date_col and sec_date_col in raw.columns:
            raw_sec = r.get(sec_date_col, '')
            try:
                sec_date = pd.to_datetime(str(raw_sec).strip(), format=date_fmt, dayfirst=True).date()
            except Exception:
                try:
                    sec_date = pd.to_datetime(str(raw_sec).strip(), dayfirst=True).date()
                except Exception:
                    sec_date = None
            if sec_date and abs((txn_date - sec_date).days) > 3:
                orig_date_suffix = f", orig: {sec_date.strftime('%d/%m/%Y')}"

        desc = str(r.get(desc_col, '')).strip()
        if not desc or desc.lower() in ('nan', 'none', ''):
            continue
        extras = []
        if inst_col and inst_col in raw.columns:
            inst_val = str(r.get(inst_col, '')).strip()
            if inst_val and inst_val.lower() not in ('nan', 'none', ''):
                extras.append(inst_val)
        if orig_date_suffix:
            extras.append(orig_date_suffix.lstrip(', '))
        if extras:
            desc = f"{desc} [{', '.join(extras)}]"

        amount = None
        if sign_conv == 'debit_credit':
            deb = _parse_number(r.get(deb_col), dec, thou) if deb_col and deb_col in raw.columns else None
            cre = _parse_number(r.get(cre_col), dec, thou) if cre_col and cre_col in raw.columns else None
            if deb is not None and deb != 0:
                amount = -abs(deb)
            elif cre is not None and cre != 0:
                amount = abs(cre)
        else:
            amount = _parse_number(r.get(amt_col), dec, thou) if amt_col and amt_col in raw.columns else None

        if amount is None:
            continue

        if profile.get('invert_amounts'):
            amount = -amount

        balance = _parse_number(r.get(bal_col), dec, thou) if bal_col and bal_col in raw.columns else None

        rows.append({'date': txn_date, 'description': desc, 'amount': amount, 'balance': balance})

    if not rows:
        _log.warning("No valid transactions found in the file after parsing.")
        return pd.DataFrame()

    df = pd.DataFrame(rows).sort_values('date').reset_index(drop=True)
    return df
