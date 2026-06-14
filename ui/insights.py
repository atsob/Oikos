"""Actionable spending insight cards for the Dashboard.

Queries the database for financial patterns and renders styled cards
that surface things the user should actually act on:
  • Category overspending vs. last month  (transfers excluded, full path, no Uncategorized)
  • Unusual large transaction             (description shown, sample count included)
  • Negative cash balance warning         (only fires when balance < 0)
  • Credit card near limit               (remaining < 10% or < €500)
  • Savings rate trend                    (negative rate shown as spending deficit)
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from database.connection import get_connection
from ui.components import insight_card


@st.cache_data(ttl=300, show_spinner=False)
def _get_category_overspending() -> pd.DataFrame:
    """Find categories (full path, no Uncategorized) where last month's spending ≥ 20% more."""
    conn = get_connection()
    try:
        df = pd.read_sql("""
            WITH RECURSIVE cat_path AS (
                SELECT Categories_Id,
                       Categories_Name::TEXT AS full_path,
                       Categories_Id_Parent
                FROM   Categories
                WHERE  Categories_Id_Parent IS NULL
                UNION ALL
                SELECT c.Categories_Id,
                       cp.full_path || ' : ' || c.Categories_Name,
                       c.Categories_Id_Parent
                FROM   Categories c
                JOIN   cat_path cp ON c.Categories_Id_Parent = cp.Categories_Id
            ),
            last_month AS (
                SELECT
                    cp.full_path             AS category,
                    ABS(SUM(s.Amount))       AS spent
                FROM Transactions t
                JOIN Splits     s  ON s.Transactions_Id = t.Transactions_Id
                JOIN cat_path   cp ON cp.Categories_Id  = s.Categories_Id
                WHERE t.Date >= DATE_TRUNC('month', CURRENT_DATE - INTERVAL '1 month')
                  AND t.Date <  DATE_TRUNC('month', CURRENT_DATE)
                  AND t.Transfers_Id IS NULL
                  AND t.Is_Draft     = FALSE
                  AND s.Amount < 0
                GROUP BY 1
            ),
            prev_month AS (
                SELECT
                    cp.full_path             AS category,
                    ABS(SUM(s.Amount))       AS spent
                FROM Transactions t
                JOIN Splits     s  ON s.Transactions_Id = t.Transactions_Id
                JOIN cat_path   cp ON cp.Categories_Id  = s.Categories_Id
                WHERE t.Date >= DATE_TRUNC('month', CURRENT_DATE - INTERVAL '2 months')
                  AND t.Date <  DATE_TRUNC('month', CURRENT_DATE - INTERVAL '1 month')
                  AND t.Transfers_Id IS NULL
                  AND t.Is_Draft     = FALSE
                  AND s.Amount < 0
                GROUP BY 1
            )
            SELECT
                l.category,
                ROUND(l.spent::numeric, 2)                          AS this_month,
                ROUND(COALESCE(p.spent, 0)::numeric, 2)             AS last_month,
                ROUND((l.spent - COALESCE(p.spent, 0))::numeric, 2) AS increase,
                CASE WHEN COALESCE(p.spent, 0) > 0
                     THEN ROUND(((l.spent - p.spent) / p.spent * 100)::numeric, 1)
                     ELSE NULL END                                   AS pct_change
            FROM last_month l
            LEFT JOIN prev_month p ON p.category = l.category
            WHERE l.spent > 30
              AND l.spent > COALESCE(p.spent, 0) * 1.20
            ORDER BY increase DESC
            LIMIT 3
        """, conn)
    finally:
        conn.close()
    return df


@st.cache_data(ttl=300, show_spinner=False)
def _get_negative_balance_accounts() -> pd.DataFrame:
    """Return active Checking/Savings/Cash accounts with a negative balance."""
    conn = get_connection()
    try:
        df = pd.read_sql("""
            SELECT
                a.Accounts_Name                       AS name,
                ROUND(a.Accounts_Balance::numeric, 2) AS balance,
                c.Currencies_ShortName                AS currency
            FROM Accounts a
            JOIN Currencies c ON c.Currencies_Id = a.Currencies_Id
            WHERE a.Is_Active      = TRUE
              AND a.Accounts_Type IN ('Checking', 'Savings', 'Cash')
              AND a.Accounts_Balance < 0
            ORDER BY a.Accounts_Balance ASC
        """, conn)
    finally:
        conn.close()
    return df


@st.cache_data(ttl=300, show_spinner=False)
def _get_credit_card_near_limit() -> pd.DataFrame:
    """Return Credit Card accounts where remaining credit < 10% of limit or < €500."""
    conn = get_connection()
    try:
        df = pd.read_sql("""
            SELECT
                a.Accounts_Name                                          AS name,
                ROUND(ABS(a.Credit_Limit)::numeric, 2)                  AS credit_limit,
                ROUND((ABS(a.Credit_Limit) + a.Accounts_Balance)::numeric, 2) AS remaining,
                ROUND(
                    ((ABS(a.Credit_Limit) + a.Accounts_Balance)
                     / NULLIF(ABS(a.Credit_Limit), 0) * 100)::numeric, 1
                )                                                        AS pct_remaining,
                c.Currencies_ShortName                                   AS currency
            FROM Accounts a
            JOIN Currencies c ON c.Currencies_Id = a.Currencies_Id
            WHERE a.Is_Active      = TRUE
              AND a.Accounts_Type  = 'Credit Card'
              AND a.Credit_Limit  IS NOT NULL
              AND a.Credit_Limit  <> 0
              AND (
                  (ABS(a.Credit_Limit) + a.Accounts_Balance)
                      / NULLIF(ABS(a.Credit_Limit), 0) < 0.10
                  OR
                  (ABS(a.Credit_Limit) + a.Accounts_Balance) < 500
              )
            ORDER BY remaining ASC
        """, conn)
    finally:
        conn.close()
    return df


@st.cache_data(ttl=300, show_spinner=False)
def _get_savings_rate_trend() -> pd.DataFrame:
    """Return savings rate for the last 3 complete months.

    Uses split amounts (not transaction totals) and excludes transfers — matching
    the methodology of the Income & Expense report.  Covering all account types
    (checking, savings, credit card, etc.) via the splits table ensures credit
    card expenses are counted even though the cash movement is on the credit card
    account, not the bank account.
    """
    conn = get_connection()
    try:
        df = pd.read_sql("""
            SELECT
                DATE_TRUNC('month', t.Date)::date             AS month,
                SUM(CASE WHEN s.Amount > 0 THEN  s.Amount
                         ELSE 0 END)                          AS income,
                ABS(SUM(CASE WHEN s.Amount < 0 THEN s.Amount
                             ELSE 0 END))                     AS expenses
            FROM Transactions t
            JOIN Splits s ON s.Transactions_Id = t.Transactions_Id
            WHERE t.Transfers_Id IS NULL
              AND t.Is_Draft     = FALSE
              AND t.Date >= DATE_TRUNC('month', CURRENT_DATE - INTERVAL '3 months')
              AND t.Date <  DATE_TRUNC('month', CURRENT_DATE)
            GROUP BY 1
            ORDER BY 1
        """, conn)
    finally:
        conn.close()
    if not df.empty and df['income'].sum() > 0:
        df['savings_rate'] = (
            (df['income'] - df['expenses']) / df['income'] * 100
        ).round(1)
    else:
        df['savings_rate'] = 0.0
    return df


@st.cache_data(ttl=300, show_spinner=False)
def _get_recent_large_transactions() -> pd.DataFrame:
    """Return unusually large transactions in the last 14 days (z-score ≥ 2.5).

    Includes description and the number of prior transactions used to compute
    the baseline (so the user can judge reliability of the z-score).
    """
    conn = get_connection()
    try:
        df = pd.read_sql("""
            WITH stats AS (
                SELECT
                    COALESCE(p.Payees_Name, 'Unknown') AS payee,
                    AVG(ABS(t.Total_Amount))            AS mean_amt,
                    STDDEV(ABS(t.Total_Amount))         AS std_amt,
                    COUNT(*)                            AS sample_count
                FROM Transactions t
                LEFT JOIN Payees p ON p.Payees_Id = t.Payees_Id
                WHERE t.Date >= CURRENT_DATE - INTERVAL '90 days'
                  AND t.Transfers_Id IS NULL
                  AND t.Total_Amount <> 0
                GROUP BY 1
                HAVING COUNT(*) >= 3
            )
            SELECT
                t.Date                                        AS date,
                COALESCE(p.Payees_Name, t.Description, '—')  AS payee,
                t.Description                                 AS description,
                ROUND(t.Total_Amount::numeric, 2)             AS amount,
                ROUND(s.mean_amt::numeric, 2)                 AS typical,
                ROUND(
                    ABS(ABS(t.Total_Amount) - s.mean_amt) / NULLIF(s.std_amt, 0), 1
                )                                             AS z_score,
                s.sample_count
            FROM Transactions t
            LEFT JOIN Payees  p ON p.Payees_Id = t.Payees_Id
            JOIN      stats   s ON s.payee = COALESCE(p.Payees_Name, 'Unknown')
            WHERE t.Date >= CURRENT_DATE - INTERVAL '14 days'
              AND t.Is_Draft = FALSE
              AND ABS(ABS(t.Total_Amount) - s.mean_amt) / NULLIF(s.std_amt, 0) >= 2.5
            ORDER BY ABS(t.Total_Amount) DESC
            LIMIT 3
        """, conn)
    finally:
        conn.close()
    return df


@st.fragment
def render_spending_insights() -> None:
    """Render actionable spending insight cards. Uses st.fragment for isolated reruns."""

    cards: list[dict] = []

    # ── 1. Category overspending ───────────────────────────────────────────
    try:
        df_over = _get_category_overspending()
        for _, row in df_over.iterrows():
            pct = row['pct_change']
            inc = row['increase']
            cat = row['category']
            if pct is not None and not pd.isna(pct):
                change_desc = f"**{pct:+.0f}%** more than the prior month (↑ €{inc:,.0f})"
            else:
                change_desc = f"↑ **€{inc:,.0f}** vs the prior month (nothing spent there last month)"
            cards.append({
                "icon":    "💸",
                "title":   f"Overspending · {cat}",
                "message": (
                    f"Last month you spent **€{row['this_month']:,.0f}** on **{cat}** — "
                    f"{change_desc}. Consider reviewing this category."
                ),
                "level":   "warning" if (pct or 0) < 50 else "danger",
            })
    except Exception:
        pass

    # ── 2. Negative cash balance ───────────────────────────────────────────
    try:
        df_neg = _get_negative_balance_accounts()
        for _, row in df_neg.iterrows():
            sym = {"EUR": "€", "USD": "$", "GBP": "£"}.get(row['currency'], row['currency'])
            cards.append({
                "icon":    "🔴",
                "title":   f"Negative balance · {row['name']}",
                "message": (
                    f"**{row['name']}** is overdrawn: **{sym}{row['balance']:,.2f}**. "
                    "Transfer funds or check for unreconciled transactions."
                ),
                "level":   "danger",
            })
    except Exception:
        pass

    # ── 3. Credit card near limit ──────────────────────────────────────────
    try:
        df_cc = _get_credit_card_near_limit()
        for _, row in df_cc.iterrows():
            sym = {"EUR": "€", "USD": "$", "GBP": "£"}.get(row['currency'], row['currency'])
            cards.append({
                "icon":    "💳",
                "title":   f"Credit limit nearly reached · {row['name']}",
                "message": (
                    f"**{row['name']}** has only **{sym}{row['remaining']:,.2f}** "
                    f"({row['pct_remaining']:.1f}%) of its {sym}{row['credit_limit']:,.0f} "
                    "credit limit remaining."
                ),
                "level":   "danger" if row['pct_remaining'] < 5 else "warning",
            })
    except Exception:
        pass

    # ── 4. Recent unusually large transactions ─────────────────────────────
    try:
        df_large = _get_recent_large_transactions()
        for _, row in df_large.iterrows():
            desc = str(row.get('description') or '').strip()
            desc_part = f" — *{desc}*" if desc and desc not in ('—', row['payee']) else ""
            cards.append({
                "icon":    "🔍",
                "title":   f"Unusual transaction · {row['payee']}",
                "message": (
                    f"**€{abs(row['amount']):,.2f}** on {row['date']} with **{row['payee']}**"
                    f"{desc_part} — "
                    f"{row['z_score']}× above the usual **€{row['typical']:,.2f}** "
                    f"(based on {row['sample_count']} transactions in the last 90 days). "
                    "Was this expected?"
                ),
                "level":   "info",
            })
    except Exception:
        pass

    # ── 5. Savings rate trend ──────────────────────────────────────────────
    try:
        df_sr = _get_savings_rate_trend()
        if len(df_sr) >= 2:
            latest     = float(df_sr.iloc[-1]["savings_rate"])
            prev       = float(df_sr.iloc[-2]["savings_rate"])
            delta      = latest - prev
            income     = float(df_sr.iloc[-1]["income"])
            expenses   = float(df_sr.iloc[-1]["expenses"])
            month_lbl  = pd.to_datetime(df_sr.iloc[-1]["month"]).strftime("%B")

            if latest >= 25 and delta >= 5:
                cards.append({
                    "icon":    "🎯",
                    "title":   "Savings rate improving",
                    "message": (
                        f"Your savings rate in {month_lbl} was **{latest:.1f}%** — "
                        f"up {delta:+.1f} pp vs. the prior month. Great work!"
                    ),
                    "level":   "success",
                })
            elif latest < 0:
                deficit = expenses - income
                cards.append({
                    "icon":    "🚨",
                    "title":   f"Expenses exceed recorded income · {month_lbl}",
                    "message": (
                        f"In {month_lbl} recorded expenses (**€{expenses:,.0f}**) exceeded "
                        f"recorded income (**€{income:,.0f}**) by **€{deficit:,.0f}**. "
                        "This often means large payments (taxes, investments, credit card "
                        "settlements) are not yet linked as transfers — check the "
                        "Income & Expense report for details."
                    ),
                    "level":   "warning",
                })
            elif latest < 10:
                cards.append({
                    "icon":    "📉",
                    "title":   f"Low savings rate · {month_lbl}",
                    "message": (
                        f"Your savings rate in {month_lbl} was only **{latest:.1f}%** "
                        f"(€{income:,.0f} income, €{expenses:,.0f} expenses). "
                        "Review your expenses to find areas to cut back."
                    ),
                    "level":   "warning",
                })
    except Exception:
        pass

    if not cards:
        return   # nothing to show — don't render the section at all

    with st.expander(
        f"💡 **{len(cards)} financial insight{'s' if len(cards) != 1 else ''}** — click to review",
        expanded=False,
    ):
        for c in cards:
            insight_card(c["icon"], c["title"], c["message"], c["level"])
        st.caption("Insights refresh every 5 minutes · Based on your last 90 days of transactions")
