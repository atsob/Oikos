"""Bank Statement Import & Account Reconciliation.

Workflow
--------
1. Select account
2. Upload CSV / XLSX bank statement
3. Choose or create an import profile (column mapping)
4. Preview parsed rows
5. Auto-match against existing app transactions (date + amount)
6. Review: matched → mark reconciled; new → optionally import with payee learning
7. Save reconciliation session

Greek bank support
------------------
Pre-built profiles are seeded for Alpha Bank and Eurobank.
Both banks expose an Excel/CSV export from their e-banking
transaction-history search.  The official *monthly PDF statement*
is a separate document — we parse the CSV/XLSX export here.
"""

from __future__ import annotations

import io
import logging
import re
from datetime import date as _date, timedelta

import pandas as pd

from database.connection import get_db, get_connection

_log = logging.getLogger(__name__)

# Streamlit is optional — only needed when running the legacy UI.
try:
    import streamlit as st
    _ST = True
except Exception:
    st = None  # type: ignore[assignment]
    _ST = False


def _st_error(msg: str) -> None:
    if _ST:
        try:
            st.error(msg)  # type: ignore[union-attr]
        except Exception:
            _log.error(msg)
    else:
        _log.error(msg)


def _st_warning(msg: str) -> None:
    if _ST:
        try:
            st.warning(msg)  # type: ignore[union-attr]
        except Exception:
            _log.warning(msg)
    else:
        _log.warning(msg)
from database.queries import (
    _ensure_import_tables,
    get_import_profiles,
    save_import_profile,
    delete_import_profile,
    get_payee_rules,
    save_payee_rule,
    update_payee_rule,
    delete_payee_rule,
    apply_payee_rules,
    save_reconciliation_session,
    mark_transactions_reconciled,
    get_reconciliation_history,
    get_top_categories_for_payee,
    get_account_transactions_for_reconciliation,
    update_transaction_description,
    update_transaction_amount,
    update_transaction_category,
    get_statement_history_suggestions,
    save_statement_history,
)
from ui.components import copy_df_button

# ---------------------------------------------------------------------------
# Built-in bank profile templates (seeded on first use)
# ---------------------------------------------------------------------------

_DEFAULT_PROFILES = [
    {
        "profile_name":       "Alpha Bank (myAlpha Web)",
        "bank_name":          "Alpha Bank",
        "file_type":          "xlsx",
        "date_column":        "Ημερομηνία",
        "description_column": "Περιγραφή",
        "debit_column":       "Χρέωση",
        "credit_column":      "Πίστωση",
        "amount_column":      "",
        "balance_column":     "Υπόλοιπο",
        "date_format":        "%d/%m/%Y",
        "encoding":           "utf-8",
        "skip_rows":          0,
        "decimal_separator":  ",",
        "thousands_separator":".",
        "sign_convention":    "debit_credit",
    },
    {
        "profile_name":       "Eurobank (e-banking)",
        "bank_name":          "Eurobank",
        "file_type":          "xlsx",
        "date_column":        "Ημερομηνία",
        "description_column": "Περιγραφή Κίνησης",
        "debit_column":       "Χρέωση",
        "credit_column":      "Πίστωση",
        "amount_column":      "",
        "balance_column":     "Υπόλοιπο",
        "date_format":        "%d/%m/%Y",
        "encoding":           "utf-8",
        "skip_rows":          0,
        "decimal_separator":  ",",
        "thousands_separator":".",
        "sign_convention":    "debit_credit",
    },
    {
        "profile_name":       "Generic CSV (amount column)",
        "bank_name":          "",
        "file_type":          "csv",
        "date_column":        "Date",
        "description_column": "Description",
        "debit_column":       "",
        "credit_column":      "",
        "amount_column":      "Amount",
        "balance_column":     "Balance",
        "date_format":        "%Y-%m-%d",
        "encoding":           "utf-8",
        "skip_rows":          0,
        "decimal_separator":  ".",
        "thousands_separator": ",",
        "sign_convention":    "signed_amount",
    },
    {
        # Alpha Bank credit-card transaction CSV
        # (card history search → Export, NOT the monthly statement PDF).
        # The export has 6 metadata rows before the actual column header.
        # Date includes time (e.g. "22/05/2026 19:47") — the time is stripped.
        # Amount column "Ποσό (EUR)" is signed: negative = purchase, positive = payment.
        # No running balance is included in credit-card exports.
        # Pending rows ("Σε επεξεργασία") are included; mark them Skip if unwanted.
        "profile_name":       "Alpha Bank Credit Card (CSV)",
        "bank_name":          "Alpha Bank",
        "file_type":          "csv",
        "date_column":        "Ημ/νία συναλλαγής",
        "description_column": "Αιτιολογία",
        "debit_column":       "",
        "credit_column":      "",
        "amount_column":      "Ποσό (EUR)",
        "balance_column":     "",
        "date_format":        "%d/%m/%Y %H:%M",
        "encoding":           "utf-8",
        "skip_rows":          6,
        "decimal_separator":  ",",
        "thousands_separator": ".",
        "sign_convention":    "signed_amount",
        "invert_amounts":     False,
    },
    {
        # PayPal → Activity → Statements → Download → CSV (all activity)
        # "Gross" is signed: positive = received, negative = sent/paid.
        # PayPal exports use comma decimal + period thousands for EN locale.
        # If your PayPal account is set to a European locale the separators
        # may be reversed — adjust in Import Profiles if needed.
        "profile_name":       "PayPal (Activity CSV)",
        "bank_name":          "PayPal",
        "file_type":          "csv",
        "date_column":        "Date",
        "description_column": "Name",
        "debit_column":       "",
        "credit_column":      "",
        "amount_column":      "Gross",
        "balance_column":     "Balance",
        "date_format":        "%d/%m/%Y",
        "encoding":           "utf-8",
        "skip_rows":          0,
        "decimal_separator":  ",",
        "thousands_separator": ".",
        "sign_convention":    "signed_amount",
        "invert_amounts":     False,
    },
]


def _seed_default_profiles():
    """Insert built-in profiles if they don't exist yet."""
    existing = get_import_profiles()
    existing_names = set(existing['profile_name'].tolist()) if not existing.empty else set()
    for p in _DEFAULT_PROFILES:
        if p['profile_name'] not in existing_names:
            save_import_profile(p)
    get_import_profiles.clear()


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _desc_key(desc: str) -> str:
    """Normalise a transaction description to a stable lookup key.

    Strips surrounding whitespace, lowercases, collapses multiple spaces,
    and removes leading currency tags added by the PayPal connector (e.g. '[USD] ').
    """
    s = str(desc).strip().lower()
    s = re.sub(r'^\[[a-z]{3}\]\s*', '', s)   # strip [CCY] prefix
    s = re.sub(r'\s+', ' ', s)
    return s


def _parse_number(val, decimal_sep='.', thousands_sep=',') -> float | None:
    """Convert a potentially formatted number string to float."""
    if pd.isna(val):
        return None
    s = str(val).strip()
    if not s or s in ('-', '—', ''):
        return None
    # Remove currency symbols and whitespace
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
            # Encoding candidates (tried in order):
            #   utf-8-sig  — BOM-aware UTF-8 (strips the ﻿ byte-order mark)
            #   profile enc — whatever the profile specifies (e.g. windows-1253)
            #   windows-1253 / iso-8859-7 — Greek Windows / ISO encodings used by
            #                               many Greek bank e-banking CSV exports
            #   latin-1    — byte-safe last resort (never raises a decode error)
            _enc_order = list(dict.fromkeys(
                [enc, 'utf-8-sig', 'windows-1253', 'iso-8859-7', 'latin-1']
            ))
            # Separator candidates: auto-detect first, then the three most common
            # delimiters in European bank exports (semicolon, comma, tab).
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
                        # Reject single-column results — separator was not found.
                        if len(_candidate.columns) >= 2:
                            raw = _candidate
                            break
                    except Exception:
                        continue
            if raw is None:
                raise ValueError("Unable to read CSV with any supported encoding.")
    except Exception as e:
        _st_error(f"Failed to read file: {e}")
        return pd.DataFrame()

    # Normalise column names: strip whitespace, BOM, and surrounding quotes.
    # Some bank exports (e.g. Eurobank CSV) wrap the first column in double-quotes
    # and/or prepend the UTF-8 BOM character, producing names like
    # '﻿"ΗΜ/ΝΙΑ ΣΥΝΑΛΛΑΓΗΣ"' instead of 'ΗΜ/ΝΙΑ ΣΥΝΑΛΛΑΓΗΣ'.
    raw.columns = [str(c).strip().lstrip('﻿').strip('"').strip()
                   for c in raw.columns]

    date_col   = profile.get('date_column', '').strip()
    sec_date_col = profile.get('secondary_date_column', '').strip()
    desc_col   = profile.get('description_column', '').strip()
    deb_col    = profile.get('debit_column', '').strip()
    cre_col    = profile.get('credit_column', '').strip()
    amt_col    = profile.get('amount_column', '').strip()
    bal_col    = profile.get('balance_column', '').strip()
    inst_col   = profile.get('installment_column', '').strip()
    sign_conv  = profile.get('sign_convention', 'debit_credit')
    date_fmt   = profile.get('date_format', '%d/%m/%Y')

    # Validate required columns
    missing = [c for c in [date_col, desc_col] if c and c not in raw.columns]
    if missing:
        _st_error(f"Column(s) not found in file: {missing}. Available: {list(raw.columns)}")
        return pd.DataFrame()

    rows = []
    for _, r in raw.iterrows():
        # Date
        raw_date = r.get(date_col, '')
        try:
            txn_date = pd.to_datetime(str(raw_date).strip(), format=date_fmt, dayfirst=True).date()
        except Exception:
            try:
                txn_date = pd.to_datetime(str(raw_date).strip(), dayfirst=True).date()
            except Exception:
                continue   # skip unparseable rows

        # Secondary date (e.g. original purchase date for credit-card installments).
        # When it differs from the primary (billing) date by more than 3 days, it is
        # appended to the description so the original purchase date is preserved.
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

        # Description — append installment info and/or original date when applicable
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

        # Amount
        amount = None
        if sign_conv == 'debit_credit':
            deb = _parse_number(r.get(deb_col), dec, thou) if deb_col and deb_col in raw.columns else None
            cre = _parse_number(r.get(cre_col), dec, thou) if cre_col and cre_col in raw.columns else None
            if deb is not None and deb != 0:
                amount = -abs(deb)
            elif cre is not None and cre != 0:
                amount = abs(cre)
        else:  # signed_amount
            amount = _parse_number(r.get(amt_col), dec, thou) if amt_col and amt_col in raw.columns else None

        if amount is None:
            continue

        # Invert sign when the bank's export uses the opposite convention
        # (e.g. credit-card CSVs where purchases are positive but should be negative).
        if profile.get('invert_amounts'):
            amount = -amount

        # Balance
        balance = _parse_number(r.get(bal_col), dec, thou) if bal_col and bal_col in raw.columns else None

        rows.append({'date': txn_date, 'description': desc, 'amount': amount, 'balance': balance})

    if not rows:
        _st_warning("No valid transactions found in the file after parsing.")
        return pd.DataFrame()

    df = pd.DataFrame(rows).sort_values('date').reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Matching logic
# ---------------------------------------------------------------------------

def _load_app_transactions(account_id: int, date_from: _date, date_to: _date) -> pd.DataFrame:
    """Load existing Transactions for an account in the given date window."""
    conn = get_connection()
    df = pd.read_sql("""
        SELECT
            t.Transactions_Id  AS transactions_id,
            t.Date             AS date,
            COALESCE(p.Payees_Name, t.Description) AS payee,
            t.Payees_Id        AS payees_id,
            t.Total_Amount     AS amount,
            t.Reconciled       AS reconciled
        FROM Transactions t
        LEFT JOIN Payees p ON p.Payees_Id = t.Payees_Id
        WHERE t.Accounts_Id = %(aid)s
          AND t.Date BETWEEN %(d0)s AND %(d1)s
        ORDER BY t.Date
    """, conn, params={"aid": account_id, "d0": date_from, "d1": date_to})
    conn.close()
    if not df.empty:
        df['date']     = pd.to_datetime(df['date']).dt.date
        df['amount']   = df['amount'].astype(float)
        df['payees_id'] = pd.to_numeric(df['payees_id'], errors='coerce')
    return df


def match_statement_to_app(df_stmt: pd.DataFrame, df_app: pd.DataFrame,
                            amount_tol: float = 0.02,
                            payee_date_tol: int = 5) -> pd.DataFrame:
    """Match each statement row to an app transaction.

    Three strategies in priority order:
      1. Exact   — same date + amount within tolerance
      2. Fuzzy   — ±1 day + amount within tolerance  (weekend / holiday lag)
      3. Payee   — rule-assigned payee matches, same amount, date within ±payee_date_tol days
                   (catches credit-card billing-date vs. transaction-date gaps)

    df_stmt may contain '_rule_payees_id' (int or NaN) pre-populated by payee rules.

    Returns df_stmt extended with:
      match_status       : 'matched' | 'possible_dup' | 'payee_match' | 'new'
      match_tx_id        : matched Transactions_Id or None
      match_payee        : matched payee name or ''
      already_reconciled : bool
    """
    if df_app.empty:
        df_out = df_stmt.copy()
        df_out['match_status']       = 'new'
        df_out['match_tx_id']        = None
        df_out['match_payee']        = ''
        df_out['already_reconciled'] = False
        return df_out

    has_payee_col = 'payees_id' in df_app.columns
    has_rule_col  = '_rule_payees_id' in df_stmt.columns

    def _day_diff(app_date, s_date):
        try:
            return abs((app_date - s_date).days)
        except Exception:
            return 9999

    results = []
    for _, srow in df_stmt.iterrows():
        s_date   = srow['date']
        s_amount = float(srow['amount'])

        # 1. Exact match: same date + amount within tolerance
        mask_exact = (
            (df_app['date'] == s_date) &
            (abs(df_app['amount'] - s_amount) <= amount_tol)
        )
        if mask_exact.any():
            best = df_app[mask_exact].iloc[0]
            results.append({
                'match_status':       'matched',
                'match_tx_id':        int(best['transactions_id']),
                'match_payee':        str(best['payee']),
                'already_reconciled': bool(best['reconciled']),
            })
            continue

        # 2. Fuzzy match: ±1 day + amount within tolerance
        mask_fuzzy = (
            df_app['date'].apply(lambda d: _day_diff(d, s_date)) <= 1
        ) & (abs(df_app['amount'] - s_amount) <= amount_tol)
        if mask_fuzzy.any():
            best = df_app[mask_fuzzy].iloc[0]
            results.append({
                'match_status':       'possible_dup',
                'match_tx_id':        int(best['transactions_id']),
                'match_payee':        str(best['payee']),
                'already_reconciled': bool(best['reconciled']),
            })
            continue

        # 3. Payee rule match: rule-assigned payee + amount within tolerance + ±N days
        matched_by_payee = False
        if has_rule_col and has_payee_col:
            rule_pid = srow.get('_rule_payees_id')
            if rule_pid is not None and not (isinstance(rule_pid, float) and rule_pid != rule_pid):
                rule_pid = int(rule_pid)
                mask_payee = (
                    (df_app['payees_id'] == rule_pid) &
                    (abs(df_app['amount'] - s_amount) <= amount_tol) &
                    (df_app['date'].apply(lambda d: _day_diff(d, s_date)) <= payee_date_tol)
                )
                if mask_payee.any():
                    best = df_app[mask_payee].iloc[0]
                    results.append({
                        'match_status':       'payee_match',
                        'match_tx_id':        int(best['transactions_id']),
                        'match_payee':        str(best['payee']),
                        'already_reconciled': bool(best['reconciled']),
                    })
                    matched_by_payee = True

        if matched_by_payee:
            continue

        results.append({
            'match_status':       'new',
                'match_tx_id':        None,
                'match_payee':        '',
                'already_reconciled': False,
            })

    df_out = df_stmt.copy()
    for k in ['match_status', 'match_tx_id', 'match_payee', 'already_reconciled']:
        df_out[k] = [r[k] for r in results]
    return df_out


# ---------------------------------------------------------------------------
# Main render function
# ---------------------------------------------------------------------------

def render_bank_section() -> None:
    """Bank import section — called from the Importers page (no page title)."""
    st.markdown(
        "Import a CSV or Excel bank statement export, automatically match transactions "
        "already recorded in the app, import new ones with payee learning, and mark "
        "everything as reconciled — all in one workflow."
    )

    # Ensure DB tables exist
    conn = get_connection()
    _ensure_import_tables(conn)
    conn.close()
    _seed_default_profiles()

    tab_import, tab_se, tab_revolut, tab_revs, tab_profiles, tab_rules, tab_history = st.tabs([
        "📥 Import & Reconcile",
        "🏦 Salt Edge (Open Banking)",
        "💚 Revolut Personal",
        "🐣 Revolut Savings",
        "⚙️ Import Profiles",
        "🏷️ Payee Rules",
        "📋 Reconciliation History",
    ])

    # =========================================================
    # TAB 1 — Import & Reconcile
    # =========================================================
    with tab_import:
        _render_import_tab()

    # =========================================================
    # TAB 2 — Salt Edge (PSD2 open banking)
    # =========================================================
    with tab_se:
        from ui.saltedge_import import render_saltedge_import
        render_saltedge_import()

    # =========================================================
    # TAB 3 — Revolut Personal (bank account CSV)
    # =========================================================
    with tab_revolut:
        from ui.broker_import import render_revolut_import
        render_revolut_import()

    # =========================================================
    # TAB 4 — Revolut Savings (money-market fund CSV)
    # =========================================================
    with tab_revs:
        from ui.broker_import import render_revolut_savings_import
        render_revolut_savings_import()

    # =========================================================
    # TAB 5 — Import Profiles
    # =========================================================
    with tab_profiles:
        _render_profiles_tab()

    # =========================================================
    # TAB 6 — Payee Rules
    # =========================================================
    with tab_rules:
        _render_rules_tab()

    # =========================================================
    # TAB 7 — Reconciliation History
    # =========================================================
    with tab_history:
        _render_history_tab()


def render_bank_import() -> None:
    """Legacy entry point — shows page title then delegates to render_bank_section()."""
    st.title("🏦 Bank Import & Reconciliation")
    render_bank_section()


# ---------------------------------------------------------------------------
# Sub-renderers
# ---------------------------------------------------------------------------


def _render_statement_pipeline(df_stmt: pd.DataFrame, selected_acc_id: int,
                                selected_acc_bal: float, kp: str = 'bi'):
    """Shared Steps 3-5 pipeline used by both CSV and PayPal import tabs.

    Parameters
    ----------
    df_stmt         : Parsed statement DataFrame (date, description, amount, balance).
    selected_acc_id : Accounts_Id of the target account.
    selected_acc_bal: Current account balance stored in the app.
    kp              : Key prefix to avoid widget-key collisions between tabs.
    """
    # ── Step 3: Statement closing balance + date range ───────────────────
    st.markdown("### Step 3 — Statement Details")

    _has_balance_col = df_stmt['balance'].notna().any()
    balance_na = st.checkbox(
        "No balance provided (credit card transaction list or partial export)",
        value=not _has_balance_col,
        key=f"{kp}_balance_na",
        help="Tick when the statement does not include a closing balance. "
             "The balance comparison will be skipped.",
    )

    app_balance = selected_acc_bal

    if not balance_na:
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            stmt_date = st.date_input("Statement closing date", value=df_stmt['date'].max(),
                                      key=f"{kp}_stmt_date")
        with col_b:
            stmt_balance = st.number_input(
                "Statement closing balance (€)",
                value=float(df_stmt['balance'].dropna().iloc[-1]) if _has_balance_col else 0.0,
                step=0.01, format="%.2f", key=f"{kp}_stmt_balance",
            )
        with col_c:
            st.metric("App Account Balance (€)", f"€ {app_balance:,.2f}")
        diff = stmt_balance - app_balance
        if abs(diff) < 0.01:
            st.success(f"✅ Balances match — difference: **€ {diff:+.2f}**")
        else:
            st.warning(f"⚠️ Balance difference: **€ {diff:+.2f}** — review unmatched transactions below.")
    else:
        stmt_date    = st.date_input("Statement closing date", value=df_stmt['date'].max(),
                                     key=f"{kp}_stmt_date")
        stmt_balance = None
        st.info("ℹ️ Balance comparison skipped — no closing balance in this statement.")

    # ── Step 4: Preview & Matching ────────────────────────────────────────
    st.markdown("### Step 4 — Match & Review")

    date_from = df_stmt['date'].min()
    date_to   = df_stmt['date'].max()
    # Extend the lookup window by the maximum match tolerance (5 days) on both sides.
    # Credit-card statements use the *billing* date, which can be several days later
    # than the manually-entered transaction date in the app — so existing transactions
    # just before date_from would otherwise be invisible to the matcher.
    _MATCH_PAD = timedelta(days=5)
    df_app = _load_app_transactions(
        selected_acc_id,
        date_from - _MATCH_PAD,
        date_to   + _MATCH_PAD,
    )

    # Apply payee rules BEFORE matching so strategy 3 (payee match) can use them.
    rules_df = get_payee_rules()
    _rule_results = df_stmt['description'].apply(lambda d: apply_payee_rules(d, rules_df))
    df_stmt = df_stmt.copy()
    df_stmt['_rule_payees_id']     = _rule_results.apply(lambda r: r[0])
    df_stmt['_rule_categories_id'] = _rule_results.apply(lambda r: r[1])

    # Load statement-line history for this account so we can pre-fill actions and
    # payee/category for descriptions we've seen before.
    _hist_keys = df_stmt['description'].apply(_desc_key).tolist()
    _stmt_history = get_statement_history_suggestions(selected_acc_id, _hist_keys)

    df_matched = match_statement_to_app(df_stmt, df_app)

    n_matched      = (df_matched['match_status'] == 'matched').sum()
    n_possible     = (df_matched['match_status'] == 'possible_dup').sum()
    n_payee_match  = (df_matched['match_status'] == 'payee_match').sum()
    n_new          = (df_matched['match_status'] == 'new').sum()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("✅ Matched",            n_matched,     help="Same date + amount.")
    c2.metric("⚠️ Possible Duplicate", n_possible,    help="Date ±1 day, same amount.")
    c3.metric("🏷️ Payee Match",        n_payee_match, help="Matched via Payee Rule + amount within ±5 days.")
    c4.metric("➕ New",                n_new,         help="Not found in the app — offered for import.")

    # ── Existing app transactions viewer / editor ─────────────────────────
    with st.expander(
        f"📋 View & Edit Existing App Transactions for this period "
        f"({date_from} → {date_to})",
        expanded=False,
    ):
        df_app_full = get_account_transactions_for_reconciliation(
            selected_acc_id, date_from, date_to
        )
        if df_app_full.empty:
            st.info("No existing transactions found for this account and date range.")
        else:
            st.caption(
                f"{len(df_app_full)} transaction(s) already recorded in the app for this period. "
                "You can edit **Description** and **Category** inline and save before applying the import."
            )

            # Load full-path category list for the SelectboxColumn.
            _ec_conn = get_connection()
            _cat_opts_df = pd.read_sql("""
                WITH RECURSIVE ch AS (
                    SELECT Categories_Id,
                           Categories_Name::TEXT AS full_path
                    FROM   Categories
                    WHERE  Categories_Id_Parent IS NULL
                    UNION ALL
                    SELECT c.Categories_Id,
                           ch.full_path || ' : ' || c.Categories_Name
                    FROM   Categories c
                    JOIN   ch ON c.Categories_Id_Parent = ch.Categories_Id
                )
                SELECT Categories_Id AS categories_id, full_path FROM ch ORDER BY full_path
            """, _ec_conn)
            _ec_conn.close()
            _cat_name_to_id = dict(
                _cat_opts_df[["full_path", "categories_id"]].values.tolist()
            )
            _cat_options = [""] + _cat_opts_df["full_path"].tolist()

            # Build display DataFrame — keep categories_id internally, show only
            # the human-readable full_path in the editor's category column.
            df_app_disp = df_app_full.copy()
            df_app_disp["date"] = df_app_disp["date"].astype(str)
            _editor_cols = ["transactions_id", "date", "payee",
                            "description", "amount", "cleared", "reconciled", "category"]

            edited_app = st.data_editor(
                df_app_disp[_editor_cols],
                disabled=["transactions_id", "date", "payee", "cleared", "reconciled"],
                column_config={
                    "transactions_id": st.column_config.NumberColumn("ID",         format="%d",     width="small"),
                    "date":            st.column_config.TextColumn("Date",                          width="small"),
                    "payee":           st.column_config.TextColumn("Payee",        disabled=True),
                    "description":     st.column_config.TextColumn("Description",
                                           help="Editable — update the stored description."),
                    "amount":          st.column_config.NumberColumn("Amount (€)",
                                           format="€ %,.2f", step=0.01,
                                           help="Editable — corrects the transaction amount "
                                                "and keeps the split in sync."),
                    "cleared":         st.column_config.CheckboxColumn("Cleared",                   width="small"),
                    "reconciled":      st.column_config.CheckboxColumn("Reconciled",                width="small"),
                    "category":        st.column_config.SelectboxColumn(
                                           "Category",
                                           options=_cat_options,
                                           help="Editable — update or assign the transaction category.",
                                       ),
                },
                hide_index=True,
                width="stretch",
                key=f"{kp}_app_txn_editor",
            )

            _orig = df_app_disp[_editor_cols]
            _desc_changed   = not edited_app["description"].equals(_orig["description"])
            _cat_changed    = not edited_app["category"].equals(_orig["category"])
            _amount_changed = not edited_app["amount"].astype(float).round(4).equals(
                                  _orig["amount"].astype(float).round(4))
            _any_changed    = _desc_changed or _cat_changed or _amount_changed

            if st.button("💾 Save Changes", key=f"{kp}_save_app_desc",
                         disabled=not _any_changed):
                saved = 0
                for i, row in edited_app.iterrows():
                    orig_row  = _orig.iloc[i]
                    full_row  = df_app_full.iloc[i]
                    tx_id     = int(full_row["transactions_id"])
                    new_amt   = float(row["amount"])
                    old_amt   = float(orig_row["amount"])
                    amt_diff  = abs(new_amt - old_amt) > 0.001

                    changed = False

                    if row["description"] != orig_row["description"]:
                        update_transaction_description(tx_id, str(row["description"]))
                        changed = True

                    if amt_diff:
                        update_transaction_amount(tx_id, new_amt)
                        changed = True

                    if row["category"] != orig_row["category"]:
                        cat_name = str(row["category"]).strip()
                        new_cid  = _cat_name_to_id.get(cat_name) if cat_name else None
                        # Pass the (potentially edited) amount so the split stays in sync.
                        update_transaction_category(tx_id, new_cid, new_amt)
                        changed = True
                    elif amt_diff:
                        # Amount changed but category unchanged — split amount was
                        # already synced by update_transaction_amount above.
                        pass

                    if changed:
                        saved += 1

                st.cache_data.clear()
                st.success(f"Updated {saved} transaction(s).")

    # Build editable review table
    st.markdown("#### Review each row")
    st.caption(
        "**Action** column controls what happens when you click Apply:\n"
        "- **Reconcile** — mark the matched app transaction as reconciled (no new transaction created)\n"
        "- **Import** — add as a new transaction in the app\n"
        "- **Skip** — do nothing with this row"
    )

    # Build name-lookup dicts so rule suggestions are human-readable in the table.
    _conn = get_connection()
    _payee_id_to_name = dict(pd.read_sql(
        "SELECT Payees_Id, Payees_Name FROM Payees", _conn
    ).values.tolist())
    _cat_id_to_name = dict(pd.read_sql(
        "SELECT Categories_Id, Categories_Name FROM Categories", _conn
    ).values.tolist())
    _conn.close()

    _STATUS_LABEL = {
        'matched':      '✅ Matched',
        'possible_dup': '⚠️ Possible Dup',
        'payee_match':  '🏷️ Payee Match',
        'new':          '➕ New',
    }

    review_rows = []
    for idx, row in df_matched.iterrows():
        ms = row['match_status']

        # Re-use the rule results already attached to df_stmt (avoid re-running per row).
        pid = row.get('_rule_payees_id')
        cid = row.get('_rule_categories_id')
        if isinstance(pid, float) and pid != pid:   # NaN check
            pid = None
        if isinstance(cid, float) and cid != cid:
            cid = None
        if pid is not None:
            pid = int(pid)
        if cid is not None:
            cid = int(cid)

        # History lookup — for new (unmatched) rows, use last known action + payee/category.
        hist = _stmt_history.get(_desc_key(str(row['description'])))

        # Priority for default action:
        #   1. Match status (auto-Reconcile when already in app, auto-Skip for possible dups)
        #   2. Statement history (last time this description appeared — Skip or Import)
        #   3. Fall back to Import
        if ms in ('matched', 'payee_match'):
            default_action = 'Reconcile'
        elif ms == 'possible_dup':
            default_action = 'Skip'
        elif hist and hist['last_action'] in ('Import', 'Skip'):
            default_action = hist['last_action']
        else:
            default_action = 'Import'

        # Payee/category: payee rule wins, then history, then nothing.
        if pid is None and hist:
            pid = hist.get('payees_id')
        if cid is None and hist:
            cid = hist.get('categories_id')

        # Build a visible "Rule Match" hint (payee + category when available).
        rule_hint = ''
        if pid is not None:
            rule_hint = _payee_id_to_name.get(pid, f'#{pid}')
            if cid is not None:
                rule_hint += f' / {_cat_id_to_name.get(cid, f"#{cid}")}'

        review_rows.append({
            '#':            idx + 1,
            'Date':         row['date'].strftime('%Y-%m-%d') if hasattr(row['date'], 'strftime') else str(row['date']),
            'Description':  row['description'],
            'Amount (€)':   round(float(row['amount']), 2),
            'Status':       _STATUS_LABEL.get(ms, ms),
            'Matched To':   row['match_payee'] if ms != 'new' else '',
            'Rule Match':   rule_hint,
            'Action':       default_action,
            '_match_tx_id': row['match_tx_id'],
            '_already_rec': row['already_reconciled'],
            '_payees_id':   pid,
            '_categories_id': cid,
        })

    df_review = pd.DataFrame(review_rows)
    display_cols = ['#', 'Date', 'Description', 'Amount (€)', 'Status', 'Matched To', 'Rule Match', 'Action']

    edited = st.data_editor(
        df_review[display_cols],
        hide_index=True,
        width="stretch",
        num_rows="fixed",
        key=f"{kp}_review_table",
        column_config={
            '#':           st.column_config.NumberColumn("#",  width="small"),
            'Date':        st.column_config.TextColumn("Date", width="small"),
            'Description': st.column_config.TextColumn("Description"),
            'Amount (€)':  st.column_config.NumberColumn("Amount (€)", format="%.2f"),
            'Status':      st.column_config.TextColumn("Status",     disabled=True, width="small"),
            'Matched To':  st.column_config.TextColumn("Matched To", disabled=True),
            'Rule Match':  st.column_config.TextColumn(
                "Rule Match", disabled=True,
                help="Payee (and category) suggested by a Payee Rule. "
                     "Pre-filled automatically in the Assign section below.",
            ),
            'Action':      st.column_config.SelectboxColumn(
                "Action", options=["Reconcile", "Import", "Skip"], required=True, width="small"
            ),
        },
    )

    # ── Payee / Category assignment for "Import" rows ────────────────────
    import_mask = edited['Action'] == 'Import'
    # assign_results: row_no -> {'payee': str, 'category': str}
    assign_results: dict = {}

    if import_mask.any():
        st.markdown("#### Assign Payee & Category for New Transactions")
        st.caption(
            "Rows marked **Import** need a payee and category. "
            "Auto-filled where a Payee Rule matched. "
            "Changing the **Payee** re-sorts the **Category** list to surface "
            "the most-used categories for that payee. "
            "Add rules in the **Payee Rules** tab to automate future imports."
        )
        conn = get_connection()
        payee_opts = dict(pd.read_sql(
            "SELECT Payees_Id, Payees_Name FROM Payees ORDER BY Payees_Name", conn
        ).values.tolist())
        cat_opts   = dict(pd.read_sql("""
            WITH RECURSIVE ch AS (
                SELECT Categories_Id, Categories_Name::TEXT AS full_path
                FROM Categories WHERE Categories_Id_Parent IS NULL
                UNION ALL
                SELECT c.Categories_Id, ch.full_path || ' : ' || c.Categories_Name
                FROM Categories c JOIN ch ON c.Categories_Id_Parent = ch.Categories_Id
            )
            SELECT Categories_Id, full_path FROM ch ORDER BY full_path
        """, conn).values.tolist())
        conn.close()

        payee_rev  = {v: k for k, v in payee_opts.items()}
        cat_list   = list(cat_opts.values())   # full alphabetical fallback
        payee_list = ['(New…)'] + list(payee_opts.values())

        # Column headers
        h0, h1, h2, h3, h4, h5 = st.columns([0.4, 0.9, 2.8, 1.0, 2.0, 2.0])
        h0.markdown("**#**")
        h1.markdown("**Date**")
        h2.markdown("**Description**")
        h3.markdown("**Amount (€)**")
        h4.markdown("**Payee**")
        h5.markdown("**Category**")

        for ridx in df_review[import_mask].index:
            dr     = df_review.loc[ridx]
            row_no = int(dr['#'])
            pid    = int(dr['_payees_id'])     if pd.notna(dr['_payees_id'])     else None
            cid    = int(dr['_categories_id']) if pd.notna(dr['_categories_id']) else None

            init_payee = payee_opts.get(pid, '(New…)') if pid else '(New…)'

            c0, c1, c2, c3, c4, c5 = st.columns([0.4, 0.9, 2.8, 1.0, 2.0, 2.0])
            c0.write(f"**{row_no}**")
            c1.write(dr['Date'])
            desc_str = str(dr['Description'])
            c2.write(desc_str[:40] + ('…' if len(desc_str) > 40 else ''))
            c3.write(f"€ {float(dr['Amount (€)']):,.2f}")

            sel_payee = c4.selectbox(
                "Payee",
                payee_list,
                index=payee_list.index(init_payee) if init_payee in payee_list else 0,
                key=f"{kp}_assign_payee_{row_no}",
                label_visibility="collapsed",
            )

            # Build per-row category list: prioritise categories most used with
            # the selected payee, then append the rest alphabetically.
            sorted_cats = cat_list.copy()
            if sel_payee and sel_payee != '(New…)':
                p_id_sel = payee_rev.get(sel_payee)
                if p_id_sel:
                    top_cats  = get_top_categories_for_payee(int(p_id_sel))
                    top_valid = [c for c in top_cats if c in cat_opts.values()]
                    rest      = [c for c in cat_list  if c not in top_valid]
                    sorted_cats = top_valid + rest

            # Default category: from payee rule → first in sorted list
            init_cat = cat_opts.get(cid, '') if cid else ''
            if not init_cat and sorted_cats:
                init_cat = sorted_cats[0]
            cat_idx = sorted_cats.index(init_cat) if init_cat in sorted_cats else 0

            sel_cat = c5.selectbox(
                "Category",
                sorted_cats,
                index=cat_idx,
                key=f"{kp}_assign_cat_{row_no}",
                label_visibility="collapsed",
            )

            assign_results[row_no] = {'payee': sel_payee, 'category': sel_cat}

    # ── Step 5: Apply ─────────────────────────────────────────────────────
    st.markdown("### Step 5 — Apply")
    n_reconcile = (edited['Action'] == 'Reconcile').sum()
    n_import    = (edited['Action'] == 'Import').sum()

    st.write(
        f"Ready to **reconcile {n_reconcile}** existing transaction(s) and "
        f"**import {n_import}** new transaction(s)."
    )

    notes = st.text_input("Session notes (optional)", key=f"{kp}_notes",
                          placeholder="e.g. Alpha Bank February 2026 statement")

    if st.button("✅ Apply & Reconcile", type="primary", key=f"{kp}_apply"):
        _apply_import(
            selected_acc_id  = selected_acc_id,
            stmt_date        = stmt_date,
            stmt_balance     = stmt_balance,
            app_balance      = app_balance,
            edited_review    = edited,
            assign_results   = assign_results,
            df_review_meta   = df_review,
            rules_df         = rules_df,
            notes            = notes,
        )


def _render_import_tab():
    # ── Step 1: Account selection ─────────────────────────────────────────
    st.markdown("### Step 1 — Select Account")
    conn = get_connection()
    df_accs = pd.read_sql("""
        SELECT a.Accounts_Id AS id, a.Accounts_Name AS name, a.Accounts_Type AS type,
               a.Accounts_Balance AS balance
        FROM Accounts a
        WHERE a.Accounts_Type IN ('Checking','Savings','Cash','Credit Card')
          AND a.Is_Active = TRUE
        ORDER BY a.Accounts_Name
    """, conn)
    conn.close()

    if df_accs.empty:
        st.info("No bank/cash accounts found.")
        return

    acc_opts = {f"{r['name']} ({r['type']})": int(r['id']) for _, r in df_accs.iterrows()}
    selected_acc_label = st.selectbox("Account", list(acc_opts.keys()), key="bi_account")
    selected_acc_id    = acc_opts[selected_acc_label]
    selected_acc_bal   = float(df_accs.loc[df_accs['id'] == selected_acc_id, 'balance'].iloc[0])

    st.markdown("### Step 2 — Upload Statement File")
    st.caption(
        "Upload the **Excel / CSV export** from your bank's e-banking transaction history. "
        "This is different from the official monthly PDF statement — look for an "
        "**Export** or **Save as Excel** button in the account history search."
    )

    # ── Profile selection ─────────────────────────────────────────────────
    profiles_df = get_import_profiles()
    if profiles_df.empty:
        st.warning("No import profiles found. Go to the **Import Profiles** tab to create one.")
        return

    profile_opts = {r['profile_name']: r.to_dict() for _, r in profiles_df.iterrows()}
    sel_profile_name = st.selectbox("Import Profile", list(profile_opts.keys()), key="bi_profile",
                                    help="Choose the bank/format that matches your file.")
    profile = profile_opts[sel_profile_name]

    # Show PayPal-specific download instructions when that profile is selected.
    if "paypal" in sel_profile_name.lower():
        st.caption(
            "💡 **PayPal export:** log in to paypal.com → Activity → Statements & Tax → "
            "Activity download → select date range → CSV → Download."
        )

    uploaded = st.file_uploader(
        "Statement file (XLSX / XLS / CSV)",
        type=["xlsx", "xls", "csv"],
        key="bi_uploader",
    )

    if uploaded is None:
        st.info("Upload a file to continue.")
        return

    file_bytes = uploaded.read()
    df_stmt = parse_statement(file_bytes, uploaded.name, profile)

    if df_stmt.empty:
        return

    st.success(f"✅ Parsed **{len(df_stmt)} transactions** from `{uploaded.name}`.")

    _render_statement_pipeline(df_stmt, selected_acc_id, selected_acc_bal, kp='bi')


def _apply_import(selected_acc_id, stmt_date, stmt_balance, app_balance,
                  edited_review, assign_results: dict, df_review_meta, rules_df, notes):
    """Execute the import: reconcile matched + insert new transactions.

    assign_results: {row_no: {'payee': str, 'category': str}}
    """
    from database.connection import get_db

    reconcile_ids  = []
    imported_count = 0
    errors         = []
    # Rules to auto-create AFTER the main transaction commits (avoids DDL-lock deadlock).
    pending_rules: list[tuple] = []
    # History entries accumulated here; written after the commit.
    history_rows:  list[dict]  = []

    # assign_results is already keyed by row '#' in the right format.
    assign_map = assign_results

    conn = get_connection()
    payee_name_to_id = dict(pd.read_sql(
        "SELECT Payees_Name, Payees_Id FROM Payees ORDER BY Payees_Id", conn
    ).values.tolist())
    # Build category lookup from full paths (e.g. "Food : Super Market") because
    # the Assign section uses full-path names, not leaf names.
    cat_name_to_id = dict(pd.read_sql("""
        WITH RECURSIVE ch AS (
            SELECT Categories_Id, Categories_Name::TEXT AS full_path
            FROM Categories WHERE Categories_Id_Parent IS NULL
            UNION ALL
            SELECT c.Categories_Id, ch.full_path || ' : ' || c.Categories_Name
            FROM Categories c JOIN ch ON c.Categories_Id_Parent = ch.Categories_Id
        )
        SELECT full_path, Categories_Id FROM ch
    """, conn).values.tolist())
    conn.close()

    with get_db() as conn:
        cur = conn.cursor()

        for _, erow in edited_review.iterrows():
            action = erow['Action']
            row_no = int(erow['#'])
            meta   = df_review_meta.loc[df_review_meta['#'] == row_no].iloc[0]

            amount = float(erow['Amount (€)'])
            desc   = str(erow['Description'])
            sign   = 1 if amount >= 0 else -1

            if action == 'Skip':
                history_rows.append({
                    'description_key': _desc_key(desc),
                    'amount_sign':     sign,
                    'last_action':     'Skip',
                    'payees_id':       None,
                    'categories_id':   None,
                })
                continue

            if action == 'Reconcile':
                tx_id = meta['_match_tx_id']
                if tx_id and not pd.isna(tx_id) and not meta['_already_rec']:
                    reconcile_ids.append(int(tx_id))
                history_rows.append({
                    'description_key': _desc_key(desc),
                    'amount_sign':     sign,
                    'last_action':     'Reconcile',
                    'payees_id':       None,
                    'categories_id':   None,
                })

            elif action == 'Import':
                try:
                    asgn       = assign_map.get(row_no, {})
                    payee_name = str(asgn.get('payee', '')).strip()
                    cat_name   = str(asgn.get('category', '')).strip()

                    payee_id = None
                    if payee_name and payee_name != '(New…)':
                        if payee_name not in payee_name_to_id:
                            # Create new payee
                            cur.execute(
                                "INSERT INTO Payees (Payees_Name) VALUES (%s) RETURNING Payees_Id",
                                (payee_name,)
                            )
                            payee_id = cur.fetchone()[0]
                            payee_name_to_id[payee_name] = payee_id
                        else:
                            payee_id = int(payee_name_to_id[payee_name])

                    cat_id = int(cat_name_to_id[cat_name]) if cat_name in cat_name_to_id else None

                    txn_date = erow['Date']

                    # Insert transaction
                    cur.execute("""
                        INSERT INTO Transactions
                            (Accounts_Id, Date, Payees_Id, Description, Total_Amount, Cleared, Reconciled)
                        VALUES (%s, %s, %s, %s, %s, TRUE, TRUE)
                        RETURNING Transactions_Id
                    """, (selected_acc_id, txn_date, payee_id, desc, amount))
                    new_tx_id = cur.fetchone()[0]

                    # Insert split if category assigned
                    if cat_id:
                        cur.execute("""
                            INSERT INTO Splits (Transactions_Id, Categories_Id, Amount)
                            VALUES (%s, %s, %s)
                        """, (new_tx_id, cat_id, amount))

                    # Queue a payee rule to learn from this import.
                    # IMPORTANT: do NOT call save_payee_rule() here — it opens a
                    # second DB connection and runs ALTER TABLE (DDL) which needs an
                    # AccessExclusiveLock that blocks until this transaction commits,
                    # causing an indefinite hang.  Save rules AFTER the commit below.
                    if payee_id and desc:
                        pending_rules.append((desc[:50].strip(), 'contains', payee_id, cat_id, 0))

                    history_rows.append({
                        'description_key': _desc_key(desc),
                        'amount_sign':     sign,
                        'last_action':     'Import',
                        'payees_id':       payee_id,
                        'categories_id':   cat_id,
                    })
                    imported_count += 1

                except Exception as e:
                    errors.append(f"Row {row_no}: {e}")

    # Save auto-learned payee rules NOW — after the main transaction has committed.
    # Each call opens its own connection and runs DDL (ALTER TABLE), which would
    # deadlock if attempted while the outer transaction was still open.
    for rule_args in pending_rules:
        try:
            save_payee_rule(*rule_args)
        except Exception:
            pass   # rule creation is best-effort; don't fail the whole import

    # Persist action history so future imports of the same account pre-fill correctly.
    try:
        save_statement_history(selected_acc_id, history_rows)
    except Exception:
        pass   # best-effort; never block a successful import

    # Reconcile matched transactions + save session
    total_reconciled = len(reconcile_ids) + imported_count
    session_id = save_reconciliation_session(
        selected_acc_id, stmt_date, stmt_balance, app_balance, total_reconciled, notes
    )

    if reconcile_ids:
        mark_transactions_reconciled(reconcile_ids, session_id)

    st.cache_data.clear()
    # The Register page caches transactions in session_state and only reloads on
    # account change.  Clear its cache so newly imported rows appear immediately.
    for _sk in ['register_df', 'register_acc_id']:
        st.session_state.pop(_sk, None)

    if errors:
        st.error("Some rows failed:\n" + "\n".join(errors))
    else:
        st.success(
            f"✅ Done! Reconciled **{len(reconcile_ids)}** existing transaction(s) and "
            f"imported **{imported_count}** new transaction(s). "
            f"Session #{session_id} saved."
        )
        st.balloons()


def _render_profiles_tab():
    st.markdown("### Import Profiles")
    st.caption(
        "Each profile defines the column mapping for a specific bank's CSV/XLSX export. "
        "Pre-built profiles for Alpha Bank and Eurobank are included. "
        "Create custom profiles for other banks or statement formats."
    )

    profiles_df = get_import_profiles()

    if not profiles_df.empty:
        st.dataframe(
            profiles_df.drop(columns=['profile_id', 'skip_rows', 'encoding'], errors='ignore'),
            hide_index=True,
            width="stretch",
            column_config={
                "profile_name":   st.column_config.Column("profile_name", pinned=True),
                "invert_amounts": st.column_config.CheckboxColumn("Invert Amounts"),
            },
        )

        del_opts = {r['profile_name']: int(r['profile_id']) for _, r in profiles_df.iterrows()}
        del_name = st.selectbox("Delete profile", ['— select —'] + list(del_opts.keys()),
                                key="bi_del_profile")
        if del_name != '— select —':
            if st.button(f"🗑️ Delete '{del_name}'", key="bi_confirm_del_profile"):
                delete_import_profile(del_opts[del_name])
                get_import_profiles.clear()
                st.rerun()

    st.divider()

    # ── Load selector ─────────────────────────────────────────────────────
    _NEW = "— New profile —"
    if not profiles_df.empty:
        load_opts = [_NEW] + profiles_df['profile_name'].tolist()
        sel_edit  = st.selectbox(
            "Load profile to edit",
            load_opts,
            key="bi_edit_sel",
            help="Select an existing profile to load its settings into the form below.",
        )
    else:
        sel_edit = _NEW

    # Resolve the config dict for the chosen profile (or empty dict for new)
    if sel_edit != _NEW and not profiles_df.empty:
        _ep = profiles_df[profiles_df['profile_name'] == sel_edit].iloc[0].to_dict()
    else:
        _ep = {}

    def _pv(field, default=""):
        """Return the profile's stored value or a sensible default."""
        v = _ep.get(field, default)
        return v if v is not None else default

    def _idx(options: list, field: str, default):
        """Safe index lookup for selectbox."""
        val = _pv(field, default)
        return options.index(val) if val in options else 0

    # Use sel_edit in every widget key so switching profiles re-initialises all fields.
    _k = sel_edit  # shorthand key suffix

    st.markdown("#### Create / Update Profile")

    col1, col2 = st.columns(2)
    with col1:
        pn   = st.text_input("Profile name (unique)",
                             value=_pv("profile_name"),
                             key=f"bi_pname_{_k}")
        bn   = st.text_input("Bank name",
                             value=_pv("bank_name"),
                             key=f"bi_bname_{_k}")
        _ft_opts = ["xlsx", "csv", "xls"]
        ft   = st.selectbox("File type", _ft_opts,
                            index=_idx(_ft_opts, "file_type", "xlsx"),
                            key=f"bi_ftype_{_k}")
        _sc_opts = ["debit_credit", "signed_amount"]
        sc   = st.selectbox("Sign convention", _sc_opts,
                            index=_idx(_sc_opts, "sign_convention", "debit_credit"),
                            key=f"bi_signconv_{_k}",
                            help="debit_credit: separate Debit/Credit columns; "
                                 "signed_amount: single Amount column (negative=debit)")
        sr   = st.number_input("Skip rows (header offset)",
                               min_value=0,
                               value=int(_pv("skip_rows", 0)),
                               key=f"bi_skip_{_k}")
        inv  = st.checkbox("Invert amounts",
                           value=bool(_pv("invert_amounts", False)),
                           key=f"bi_invert_{_k}",
                           help="Tick for credit-card exports where purchases are "
                                "positive in the file but should be stored as "
                                "negative (money out) in the app.")
    with col2:
        dc   = st.text_input("Date column",
                             value=_pv("date_column"),
                             key=f"bi_dcol_{_k}")
        dsc  = st.text_input("Description column",
                             value=_pv("description_column"),
                             key=f"bi_descol_{_k}")
        dbc  = st.text_input("Debit column",
                             value=_pv("debit_column"),
                             key=f"bi_debcol_{_k}")
        crc  = st.text_input("Credit column",
                             value=_pv("credit_column"),
                             key=f"bi_crecol_{_k}")
        amc  = st.text_input("Amount column (for signed_amount)",
                             value=_pv("amount_column"),
                             key=f"bi_amcol_{_k}")
        blc  = st.text_input("Balance column",
                             value=_pv("balance_column"),
                             key=f"bi_balcol_{_k}")
        instc  = st.text_input("Installment column (optional)",
                               value=_pv("installment_column"),
                               key=f"bi_instcol_{_k}",
                               help="Column containing installment info (e.g. ΔΟΣΗ → '11/12'). "
                                    "When set, the value is appended to the description as [11/12].")
        sec_dc = st.text_input("Original transaction date column (optional)",
                               value=_pv("secondary_date_column"),
                               key=f"bi_secdcol_{_k}",
                               help="For credit-card statements with two date columns: map this to the "
                                    "original purchase date (e.g. ΗΜ/ΝΙΑ ΣΥΝΑΛΛΑΓΗΣ). "
                                    "The primary Date column should be the billing/registration date "
                                    "(ΗΜ/ΝΙΑ ΕΓΓΡΑΦΗΣ) used for matching. "
                                    "When the two dates differ by more than 3 days the original date is "
                                    "appended to the description automatically.")
        dfmt = st.text_input("Date format",
                             value=_pv("date_format", "%d/%m/%Y"),
                             key=f"bi_dfmt_{_k}",
                             help="Python strptime format, e.g. %d/%m/%Y or %Y-%m-%d")
        _dsep_opts = [",", "."]
        dsep = st.selectbox("Decimal separator", _dsep_opts,
                            index=_idx(_dsep_opts, "decimal_separator", ","),
                            key=f"bi_dsep_{_k}")
        _tsep_opts = [".", ",", ""]
        tsep = st.selectbox("Thousands separator", _tsep_opts,
                            index=_idx(_tsep_opts, "thousands_separator", "."),
                            key=f"bi_tsep_{_k}")

    if st.button("💾 Save Profile", key="bi_save_profile"):
        if not pn:
            st.error("Profile name is required.")
        else:
            save_import_profile({
                "profile_name": pn, "bank_name": bn, "file_type": ft,
                "date_column": dc, "description_column": dsc,
                "debit_column": dbc, "credit_column": crc,
                "amount_column": amc, "balance_column": blc,
                "date_format": dfmt, "encoding": "utf-8", "skip_rows": int(sr),
                "decimal_separator": dsep, "thousands_separator": tsep,
                "sign_convention": sc, "invert_amounts": inv,
                "installment_column": instc, "secondary_date_column": sec_dc,
            })
            get_import_profiles.clear()
            st.success(f"Profile '{pn}' saved.")
            st.rerun()


def _render_rules_tab():
    st.markdown("### Payee Rules")
    st.caption(
        "Rules automatically assign a **Payee** and/or **Category** to imported transactions "
        "based on their description text. Rules are evaluated by **Priority** (highest first). "
        "New rules are also created automatically when you import a transaction and assign a payee."
    )

    rules_df = get_payee_rules()

    _NEW = "— New rule —"

    if not rules_df.empty:
        st.dataframe(
            rules_df[['rule_id', 'pattern', 'match_type', 'payee_name', 'category_name', 'priority']],
            hide_index=True, width="stretch",
            column_config={"rule_id": st.column_config.NumberColumn("ID", format="%d")},
        )

        rule_opts = {
            f"#{r['rule_id']} — {r['pattern'][:50]}": r.to_dict()
            for _, r in rules_df.iterrows()
        }
        sel_label = st.selectbox(
            "Load rule to edit",
            [_NEW] + list(rule_opts.keys()),
            key="bi_sel_rule",
            help="Select a rule to load its settings into the form below.",
        )
        _er = rule_opts[sel_label] if sel_label != _NEW else {}

        if _er:
            if st.button("🗑️ Delete this rule", key="bi_confirm_del_rule"):
                delete_payee_rule(int(_er['rule_id']))
                get_payee_rules.clear()
                st.rerun()
    else:
        sel_label = _NEW
        _er = {}

    st.divider()

    is_edit = bool(_er)
    _k      = str(_er.get('rule_id', 'new'))   # key suffix forces re-init on rule change
    st.markdown(f"#### {'Edit Rule' if is_edit else 'Add Rule'}")

    # Load payees and full-path categories
    conn = get_connection()
    payee_df = pd.read_sql(
        "SELECT Payees_Id AS payees_id, Payees_Name AS payees_name FROM Payees ORDER BY Payees_Name",
        conn,
    )
    cat_df = pd.read_sql("""
        WITH RECURSIVE ch AS (
            SELECT Categories_Id, Categories_Name::TEXT AS full_path
            FROM Categories WHERE Categories_Id_Parent IS NULL
            UNION ALL
            SELECT c.Categories_Id, ch.full_path || ' : ' || c.Categories_Name
            FROM Categories c JOIN ch ON c.Categories_Id_Parent = ch.Categories_Id
        )
        SELECT Categories_Id AS categories_id, full_path AS categories_name
        FROM ch ORDER BY full_path
    """, conn)
    conn.close()

    payee_map = {r['payees_name']: int(r['payees_id']) for _, r in payee_df.iterrows()}
    cat_map   = {r['categories_name']: int(r['categories_id']) for _, r in cat_df.iterrows()}

    _NO_PAYEE = '— none —'
    _NO_CAT   = '— none —'
    payee_list = [_NO_PAYEE] + list(payee_map.keys())
    cat_list   = [_NO_CAT]   + list(cat_map.keys())

    # Resolve pre-selected payee and category from stored IDs
    init_payee = _NO_PAYEE
    if _er and pd.notna(_er.get('payees_id')):
        stored_pid = int(_er['payees_id'])
        init_payee = next((n for n, i in payee_map.items() if i == stored_pid), _NO_PAYEE)

    init_cat = _NO_CAT
    if _er and pd.notna(_er.get('categories_id')):
        stored_cid = int(_er['categories_id'])
        init_cat = next((n for n, i in cat_map.items() if i == stored_cid), _NO_CAT)

    _mt_opts = ["contains", "starts_with", "exact", "regex"]

    col1, col2 = st.columns(2)
    with col1:
        pattern    = st.text_input(
            "Pattern (text to match in description)",
            value=_er.get('pattern', ''),
            key=f"bi_rule_pat_{_k}",
        )
        match_type = st.selectbox(
            "Match type", _mt_opts,
            index=_mt_opts.index(_er['match_type']) if _er.get('match_type') in _mt_opts else 0,
            key=f"bi_rule_mtype_{_k}",
        )
        priority   = st.number_input(
            "Priority (higher = checked first)",
            value=int(_er.get('priority', 0)),
            step=1,
            key=f"bi_rule_prio_{_k}",
        )
    with col2:
        sel_payee  = st.selectbox(
            "Assign Payee (optional)", payee_list,
            index=payee_list.index(init_payee) if init_payee in payee_list else 0,
            key=f"bi_rule_payee_{_k}",
        )

        # Build sorted category list: prioritise categories most frequently used
        # for the selected payee (same UX as the Assign section and transaction register).
        top_valid = []
        sorted_cats = cat_list.copy()
        if sel_payee and sel_payee != _NO_PAYEE:
            p_id_sel = payee_map.get(sel_payee)
            if p_id_sel:
                top_cats  = get_top_categories_for_payee(int(p_id_sel))
                top_valid = [c for c in top_cats if c in cat_map]
                if top_valid:
                    sorted_cats = [_NO_CAT] + top_valid + [
                        c for c in cat_list if c not in top_valid and c != _NO_CAT
                    ]

        # When editing a rule: keep its stored category.
        # When creating a new rule with a payee selected: auto-select the most-used
        # category so prioritisation is immediately visible (user can still change it).
        effective_init = init_cat
        if effective_init == _NO_CAT and top_valid:
            effective_init = top_valid[0]

        # Include the selected payee ID in the key so Streamlit re-initialises
        # the selectbox (and picks up the new sort order) when the payee changes.
        _cat_key = f"bi_rule_cat_{_k}_{payee_map.get(sel_payee, 'none')}"
        _cat_help = (
            f"Top {len(top_valid)} most-used categor{'y' if len(top_valid) == 1 else 'ies'} "
            f"for this payee shown first."
            if top_valid else
            "No past transactions found for this payee — showing all categories alphabetically."
        )
        sel_cat    = st.selectbox(
            "Assign Category (optional)", sorted_cats,
            index=sorted_cats.index(effective_init) if effective_init in sorted_cats else 0,
            key=_cat_key,
            help=_cat_help,
        )

    btn_label = "💾 Update Rule" if is_edit else "➕ Add Rule"
    if st.button(btn_label, key="bi_save_rule"):
        if not pattern:
            st.error("Pattern is required.")
        else:
            pid = payee_map.get(sel_payee) if sel_payee != _NO_PAYEE else None
            cid = cat_map.get(sel_cat)     if sel_cat   != _NO_CAT   else None
            if is_edit:
                update_payee_rule(int(_er['rule_id']), pattern, match_type, pid, cid, int(priority))
                st.success(f"Rule #{_er['rule_id']} updated.")
            else:
                save_payee_rule(pattern, match_type, pid, cid, int(priority))
                st.success(f"Rule added: '{pattern}'")
            st.rerun()


def _render_history_tab():
    st.markdown("### Reconciliation History")

    conn = get_connection()
    df_accs = pd.read_sql("""
        SELECT a.Accounts_Id AS id, a.Accounts_Name AS name
        FROM Accounts a
        WHERE a.Accounts_Type IN ('Checking','Savings','Cash','Credit Card')
          AND a.Is_Active = TRUE
          AND a.Accounts_Id IN (SELECT DISTINCT Accounts_Id FROM Reconciliation_Sessions)  
        ORDER BY a.Accounts_Name
    """, conn)
    conn.close()

    if df_accs.empty:
        st.info("No accounts found.")
        return

    acc_opts = {r['name']: int(r['id']) for _, r in df_accs.iterrows()}
    sel_acc  = st.selectbox("Account", list(acc_opts.keys()), key="bi_hist_acc")
    acc_id   = acc_opts[sel_acc]

    hist_df = get_reconciliation_history(acc_id)
    if hist_df.empty:
        st.info("No reconciliation sessions found for this account.")
        return

    hist_disp = hist_df.copy()
    hist_disp['session_date']   = hist_disp['session_date'].dt.strftime("%Y-%m-%d %H:%M")
    hist_disp['statement_date'] = hist_disp['statement_date'].dt.strftime("%Y-%m-%d")

    def _fmt(fmt: str, suffix: str = " €"):
        """Return a formatter that shows 'N/A' for None / NaN values."""
        return lambda v: f"{format(v, fmt)}{suffix}" if pd.notna(v) else "N/A"

    st.dataframe(
        hist_disp.style.format({
            "statement_balance": _fmt(",.2f"),
            "app_balance":       _fmt(",.2f"),
            "difference":        _fmt("+,.2f"),
        }),
        hide_index=True, width="stretch",
        column_config={
            "session_id":        st.column_config.NumberColumn("ID",      format="%d"),
            "session_date":      "Session Date",
            "statement_date":    "Statement Date",
            "statement_balance": st.column_config.NumberColumn("Statement Bal (€)", format="%,.2f €"),
            "app_balance":       st.column_config.NumberColumn("App Bal (€)",       format="%,.2f €"),
            "difference":        st.column_config.NumberColumn("Difference (€)",    format="%+,.2f €"),
            "tx_count":          st.column_config.NumberColumn("# Txns", format="%d"),
            "status":            "Status",
            "notes":             "Notes",
        },
    )
    copy_df_button(hist_disp, key="dl_reconciliation_history")
