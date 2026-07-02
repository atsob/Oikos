"""
Monthly AI Financial Summary
===========================
Run this script (or schedule it via cron / Task Scheduler) once a month.
It queries key financial metrics, asks the LLM to produce a plain-English
summary, and saves the result to the database table `AI_Monthly_Summaries`.

Usage:
    python -m ai.monthly_summary

Schedule (Linux cron — every 1st of the month at 07:00):
    0 7 1 * * cd /path/to/app && python -m ai.monthly_summary >> logs/monthly_summary.log 2>&1

Schedule (Windows Task Scheduler):
    Program : python
    Arguments: -m ai.monthly_summary
    Start in : C:\\path\\to\\app
"""

import logging
import textwrap
import warnings
from datetime import date, timedelta

# Suppress Streamlit cache warnings that fire when @st.cache_data decorated
# functions are imported outside a running Streamlit server.
warnings.filterwarnings("ignore", message="No runtime found", module="streamlit")
# Suppress pandas warning about non-SQLAlchemy DBAPI2 connections.
warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy connectable")

import pandas as pd

from config.settings import ENV_CONFIG
from database.connection import get_connection
from ai.llm import init_llm

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _query(conn, sql: str, params=None) -> pd.DataFrame:
    """Execute *sql* via psycopg2 cursor and return a DataFrame — no SQLAlchemy needed."""
    with conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [desc[0].lower() for desc in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)


# ──────────────────────────────────────────────────────────────────────────────
# SCHEMA  (run once to create the summary table)
# ──────────────────────────────────────────────────────────────────────────────
_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS AI_Monthly_Summaries (
    Summary_Id   SERIAL PRIMARY KEY,
    Month_Start   DATE NOT NULL,
    Generated_At TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    Summary_Text TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_monthly_summaries_month
    ON AI_Monthly_Summaries(Month_Start);
"""


def ensure_summary_table(conn):
    with conn.cursor() as cur:
        cur.execute(_CREATE_TABLE_SQL)
    conn.commit()


# ──────────────────────────────────────────────────────────────────────────────
# DATA GATHERING
# ──────────────────────────────────────────────────────────────────────────────

def _net_worth_as_of(conn, as_of_date: str) -> dict | None:
    """Reconstruct cash / investments / pension / assets / total as of *as_of_date*."""
    df = _query(conn, """
        WITH
        -- FX rates on or before period end
        fx AS (
            SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
            FROM Historical_FX
            WHERE Date <= %s::date
            ORDER BY Currencies_Id_1, Date DESC
        ),
        -- Prices on or before period end
        prices AS (
            SELECT DISTINCT ON (Securities_Id) Securities_Id, Close
            FROM Historical_Prices
            WHERE Date <= %s::date
            ORDER BY Securities_Id, Date DESC
        ),
        -- Cash / liability account balances reconstructed backwards to period_end
        account_totals AS (
            SELECT
                SUM(CASE
                    WHEN a.Accounts_Type NOT IN ('Brokerage','Pension','Other Investment','Margin',
                                                 'Real Estate','Vehicle','Asset','Liability')
                    THEN (a.Accounts_Balance - COALESCE((
                            SELECT SUM(t.Total_Amount) FROM Transactions t
                            WHERE t.Accounts_Id = a.Accounts_Id AND t.Date > %s::date
                         ), 0)) * COALESCE(fx.FX_Rate, 1)
                    ELSE 0
                END) AS cash_eur,

                SUM(CASE
                    WHEN a.Accounts_Type IN ('Pension')
                    THEN a.Accounts_Balance * COALESCE(fx.FX_Rate, 1)
                    ELSE 0
                END) AS pension_eur,

                SUM(CASE
                    WHEN a.Accounts_Type IN ('Real Estate','Vehicle','Asset','Liability')
                    THEN (CASE
                            WHEN a.Accounts_Type IN ('Real Estate','Vehicle','Asset')
                            THEN GREATEST(0, a.Accounts_Balance - COALESCE((
                                    SELECT SUM(t.Total_Amount) FROM Transactions t
                                    WHERE t.Accounts_Id = a.Accounts_Id AND t.Date > %s::date
                                 ), 0))
                            ELSE a.Accounts_Balance - COALESCE((
                                    SELECT SUM(t.Total_Amount) FROM Transactions t
                                    WHERE t.Accounts_Id = a.Accounts_Id AND t.Date > %s::date
                                 ), 0)
                          END) * COALESCE(fx.FX_Rate, 1)
                    ELSE 0
                END) AS assets_eur
            FROM Accounts a
            LEFT JOIN fx ON fx.Currencies_Id_1 = a.Currencies_Id
            WHERE a.Is_Active = TRUE
        ),
        -- Investment positions at period_end (forward cumulative)
        investment_universe AS (
            SELECT DISTINCT Securities_Id, Accounts_Id FROM Investments
            WHERE Action IN ('Buy','Reinvest','ShrIn','Sell','ShrOut')
        ),
        investment_totals AS (
            SELECT
                SUM(
                    GREATEST(COALESCE((
                        SELECT SUM(CASE
                            WHEN Action IN ('Buy','Reinvest','ShrIn') THEN Quantity
                            WHEN Action IN ('Sell','ShrOut')          THEN -Quantity
                            ELSE 0 END)
                        FROM Investments i2
                        WHERE i2.Securities_Id = iu.Securities_Id
                          AND i2.Accounts_Id   = iu.Accounts_Id
                          AND i2.Date <= %s::date
                    ), 0), 0)
                    * COALESCE(p.Close, 0)
                    * COALESCE(fx.FX_Rate, 1)
                ) AS investments_eur
            FROM investment_universe iu
            JOIN Securities s ON s.Securities_Id = iu.Securities_Id
            LEFT JOIN prices p ON p.Securities_Id = iu.Securities_Id
            LEFT JOIN fx      ON fx.Currencies_Id_1 = s.Currencies_Id
        )
        SELECT
            at.cash_eur,
            at.pension_eur,
            at.assets_eur,
            it.investments_eur
        FROM account_totals at
        CROSS JOIN investment_totals it
    """, (as_of_date,) * 6)
    if df.empty:
        return None
    r = df.iloc[0]
    cash        = float(r.cash_eur or 0)
    investments = float(r.investments_eur or 0)
    pension     = float(r.pension_eur or 0)
    assets      = float(r.assets_eur or 0)
    return {
        "cash": cash, "investments": investments, "pension": pension, "assets": assets,
        "total": cash + investments + pension + assets,
    }


def _gather_context(conn, month_start: str, month_end: str) -> str:
    """Pull key metrics from the DB and format them as a compact text block."""

    blocks = []

    # 1. Net worth snapshot as of month_end, plus the prior month-end for comparison
    try:
        now_nw = _net_worth_as_of(conn, month_end)
        prev_date = (pd.to_datetime(month_start) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        prev_nw = _net_worth_as_of(conn, prev_date)
        if now_nw is not None:
            text = (
                f"NET WORTH SNAPSHOT (as of {month_end}):\n"
                f"  Cash: €{now_nw['cash']:,.0f}  "
                f"Investments: €{now_nw['investments']:,.0f}  "
                f"Pension: €{now_nw['pension']:,.0f}  "
                f"Assets: €{now_nw['assets']:,.0f}  "
                f"TOTAL: €{now_nw['total']:,.0f}"
            )
            if prev_nw is not None:
                diff = now_nw["total"] - prev_nw["total"]
                text += (
                    f"\nPRIOR NET WORTH (as of {prev_date}): €{prev_nw['total']:,.0f}\n"
                    f"CHANGE FROM PRIOR MONTH: {'+' if diff >= 0 else ''}€{diff:,.0f}"
                )
            blocks.append(text)
    except Exception as e:
        conn.rollback()
        logging.exception("NET WORTH query failed")
        blocks.append(f"NET WORTH: unavailable ({e})")

    # 2. Monthly income & expenses
    try:
        df = _query(conn, """
            SELECT
                SUM(CASE WHEN c.Categories_Type = 'Income' THEN s.Amount ELSE 0 END) AS income,
                SUM(CASE WHEN c.Categories_Type = 'Expense' THEN s.Amount ELSE 0 END) AS expense,
                SUM(CASE WHEN c.Categories_Type = 'Tax' THEN s.Amount ELSE 0 END) AS tax,
                SUM(CASE WHEN c.Categories_Type = 'Interest' THEN s.Amount ELSE 0 END) AS interest,
                SUM(CASE WHEN c.Categories_Type NOT IN ('Income', 'Expense', 'Tax', 'Interest') THEN t.Total_Amount ELSE 0 END) AS other
            FROM Splits s
            JOIN Categories c ON c.Categories_Id = s.Categories_Id
			JOIN Transactions t ON t.Transactions_Id = s.Transactions_Id
            WHERE t.Date >= %s
            AND t.Date <= %s
        """, (month_start, month_end))
        if not df.empty:
            r = df.iloc[0]
            income   = float(r.income   or 0)
            expense  = float(r.expense  or 0)
            tax      = float(r.tax      or 0)
            interest = float(r.interest or 0)
            other    = float(r.other    or 0)
            net = income + expense + tax + interest + other
            # Build a plain-prose explanation that still reconciles to the net figure —
            # a reader comparing income vs. expenses by hand won't match the net whenever
            # tax/interest/other move money, so any non-trivial component gets named.
            extra_flows = []
            if abs(tax) > 1:
                extra_flows.append(f"€{abs(tax):,.2f} in tax")
            if abs(interest) > 1:
                extra_flows.append(f"€{abs(interest):,.2f} in interest")
            if abs(other) > 1:
                extra_flows.append(f"€{abs(other):,.2f} in other cash movements (e.g. investment contributions or transfers)")
            lead = f"On €{income:,.2f} of income against €{abs(expense):,.2f} of expenses"
            if extra_flows:
                lead += ", plus " + ", ".join(extra_flows)
            if net >= 0:
                net_explanation = f"{lead}, you ended the month with a net surplus of €{net:,.2f}."
            else:
                net_explanation = f"{lead}, you ended the month with a net shortfall of €{abs(net):,.2f}."
            blocks.append(
                f"MONTHLY CASH FLOWS (last 30 days):\n"
                f"  Income:   €{income:,.2f}\n"
                f"  Expenses: €{expense:,.2f}  (negative = money out)\n"
                f"  Tax:      €{tax:,.2f}  (negative = money out, treated as an expense)\n"
                f"  Interest: €{interest:,.2f}\n"
                f"  Other:    €{other:,.2f}\n"
                f"  NET:      €{net:,.2f}  — {net_explanation}"
            )
    except Exception as e:
        conn.rollback()
        logging.exception("MONTHLY CASH FLOWS query failed")
        blocks.append(f"MONTHLY CASH FLOWS: unavailable ({e})")

    # 3. Investment P&L this month
    try:
        df = _query(conn, """
            WITH periods AS (
                SELECT 
                    %s::date AS mtd_start,
                    %s::date AS today
            ),
            historical_entities AS (
                SELECT Accounts_Id, Securities_Id FROM Holdings
                UNION
                SELECT Accounts_Id, Securities_Id FROM Investments
            ),
            historical_holdings AS (
                SELECT
                    p.*,
                    he.Accounts_Id, he.Securities_Id,
                    COALESCE(inv.qty_today, 0) AS qty_today,
                    COALESCE(inv.qty_mtd,   0) AS qty_mtd
                FROM periods p
                CROSS JOIN historical_entities he
                LEFT JOIN LATERAL (
                    SELECT
                        SUM(CASE WHEN Action IN ('Buy','Reinvest','ShrIn') THEN Quantity
                                WHEN Action IN ('Sell','ShrOut') THEN -Quantity ELSE 0 END)
                            FILTER (WHERE Date <= p.today)     AS qty_today,
                        SUM(CASE WHEN Action IN ('Buy','Reinvest','ShrIn') THEN Quantity
                                WHEN Action IN ('Sell','ShrOut') THEN -Quantity ELSE 0 END)
                            FILTER (WHERE Date <= p.mtd_start) AS qty_mtd
                    FROM Investments
                    WHERE Accounts_Id = he.Accounts_Id AND Securities_Id = he.Securities_Id
                ) inv ON true
            ),
            prices_fx AS (
                SELECT
                    hh.*,
                    COALESCE(hp_today.Close, 0)  AS price_today,
                    COALESCE(hp_mtd.Close, 0)    AS price_mtd,
                    COALESCE(fx_today.FX_Rate, 1) AS fx_today,
                    COALESCE(fx_mtd.FX_Rate, 1)   AS fx_mtd
                FROM historical_holdings hh
                JOIN Securities s ON hh.Securities_Id = s.Securities_Id
                LEFT JOIN LATERAL (
                    SELECT
                        MAX(Date) FILTER (WHERE Date <= hh.today)      AS d_today,
                        MAX(Date) FILTER (WHERE Date <= hh.mtd_start)  AS d_mtd
                    FROM Historical_Prices WHERE Securities_Id = hh.Securities_Id
                ) pd ON true
                LEFT JOIN Historical_Prices hp_today ON hp_today.Securities_Id = hh.Securities_Id AND hp_today.Date = pd.d_today
                LEFT JOIN Historical_Prices hp_mtd   ON hp_mtd.Securities_Id   = hh.Securities_Id AND hp_mtd.Date   = pd.d_mtd
                LEFT JOIN LATERAL (
                    SELECT
                        MAX(Date) FILTER (WHERE Date <= hh.today)      AS d_today,
                        MAX(Date) FILTER (WHERE Date <= hh.mtd_start)  AS d_mtd
                    FROM Historical_FX WHERE Currencies_Id_1 = s.Currencies_Id
                ) fxd ON true
                LEFT JOIN Historical_FX fx_today ON fx_today.Currencies_Id_1 = s.Currencies_Id AND fx_today.Date = fxd.d_today
                LEFT JOIN Historical_FX fx_mtd   ON fx_mtd.Currencies_Id_1   = s.Currencies_Id AND fx_mtd.Date   = fxd.d_mtd
            ),
            cash_flows AS (
                SELECT
                    i.Accounts_Id, i.Securities_Id,
                    -- Converts each cash flow immediately using its specific historical transaction date FX rate
                    SUM(CASE WHEN i.Date > p.mtd_start AND i.Date <= p.today THEN
                        (CASE WHEN i.Action IN ('Buy', 'MiscExp') THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0), i.Quantity * i.Price_Per_Share)
                            WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'Reinvest', 'RtrnCap') THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0), i.Quantity * i.Price_Per_Share)
                            ELSE 0 END) * COALESCE(hfx.FX_Rate, 1)
                        ELSE 0 END) AS cf_mtd_eur
                FROM periods p
                CROSS JOIN Investments i
                JOIN Accounts a ON i.Accounts_Id = a.Accounts_Id
                LEFT JOIN Historical_FX hfx
                    ON hfx.Currencies_Id_1 = a.Currencies_Id
                    AND hfx.Date = i.Date
                GROUP BY i.Accounts_Id, i.Securities_Id
            )
            SELECT
                SUM(
                    (pf.qty_today * pf.price_today * pf.fx_today) - 
                    (pf.qty_mtd * pf.price_mtd * pf.fx_mtd) - 
                    COALESCE(cf.cf_mtd_eur, 0)
                ) AS pnl_mtd_eur
            FROM prices_fx pf
            LEFT JOIN cash_flows cf
                ON pf.Accounts_Id = cf.Accounts_Id AND pf.Securities_Id = cf.Securities_Id
            WHERE pf.qty_today <> 0 OR pf.qty_mtd <> 0 OR cf.cf_mtd_eur IS NOT NULL;
        """, (month_start, month_end))        
        if not df.empty:
        #    pnl = float(df.iloc[0]['monthly_pnl_eur'] or 0)
            pnl = float(df.iloc[0]['pnl_mtd_eur'] or 0)
            blocks.append(f"INVESTMENT P&L (month): €{pnl:+,.2f}")
    except Exception as e:
        conn.rollback()
        logging.exception("INVESTMENT P&L query failed")
        blocks.append(f"INVESTMENT P&L: unavailable ({e})")

    # 4. Top 10 payees this month (largest absolute amounts, real expenses only)
    try:
        df = _query(conn, """
            SELECT p.Payees_Name, ABS(SUM(t.Total_Amount)) AS abs_amount, SUM(t.Total_Amount) AS total_amount
            FROM Transactions t
            LEFT JOIN Payees p ON t.Payees_Id = p.Payees_Id
            WHERE t.Date >= %s AND t.Date <= %s
            AND t.Transfers_Id IS NULL  -- exclude internal transfers
            AND t.Transactions_Id NOT IN (
                SELECT Transactions_Id FROM Investments WHERE Transactions_Id IS NOT NULL
            )  -- exclude investment funding transactions
            AND p.Payees_Name IS NOT NULL  -- exclude unnamed payees
            GROUP BY p.Payees_Name
            HAVING SUM(t.Total_Amount) < 0
            ORDER BY ABS(SUM(t.Total_Amount)) DESC
            LIMIT 10
        """, (month_start, month_end))
        if not df.empty:
            rows = "\n".join(
                f"  {r.payees_name:30s}  €{abs(float(r.total_amount)):,.2f}"
                for _, r in df.iterrows()
            )
            blocks.append(f"TOP 10 PAYEES THIS MONTH (expenses only, amounts are positive outflows):\n{rows}")
    except Exception as e:
        conn.rollback()
        logging.exception("TOP PAYEES query failed")
        blocks.append(f"TOP PAYEES: unavailable ({e})")

    return "\n\n".join(blocks)


# ──────────────────────────────────────────────────────────────────────────────
# LLM SUMMARY GENERATION
# ──────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = textwrap.dedent("""\
    You are a personal finance assistant. Write a short, direct monthly summary in plain prose.
    Use "you" / "your". No greeting, no sign-off, no markdown (no headers, no bold, no asterisks) —
    this is displayed as raw plain text, so any markdown syntax would show up literally to the user.

    Follow this structure exactly, in 4 short paragraphs:
    Paragraph 1 – Cash flows: quote the NET line's explanation from the context verbatim as the
        opening sentence — it already states income, expenses, and any tax/interest/other flows
        needed to reconcile to the net figure. Do not state your own net figure or your own
        "income minus expenses" framing; use only the quoted explanation for the net.
    Paragraph 2 – Top payees: present the TOP 10 PAYEES as a simple numbered list ("1. Name: €X"),
        one per line, largest first. A numbered list is fine here — it is not markdown. Skip this
        paragraph if there is no payee data.
    Paragraph 3 – Investments: state the investment P&L for the month plainly (gain or loss, and
        the amount).
    Paragraph 4 – Net worth: state the CHANGE FROM PRIOR MONTH from the context (if present) as
        the opening sentence, then the current TOTAL broken down into its Cash / Investments /
        Pension / Assets components, all as one flowing sentence, not a list.

    Rules:
    - Only use numbers that appear in the DATA block. Never invent, calculate, or estimate a number
      that isn't explicitly present in the DATA block — this includes prior-period comparisons.
    - Never state a net/savings figure that isn't exactly the NET value from the DATA block.
    - Never use placeholders like [X] or (insert value here).
    - Keep the total length under 250 words.
""")


def generate_summary(llm, context: str) -> str:
    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"=== FINANCIAL DATA FOR THIS MONTH ===\n"
        f"{context}\n"
        f"=== END OF DATA ===\n\n"
        f"Write the 4–5 paragraph summary now:"
    )
    try:
        response = llm.invoke(prompt)
        if hasattr(response, "content"):
            return response.content.strip()
        return str(response).strip()
    except Exception as e:
        return f"[LLM error: {e}]\n\nRaw context:\n{context}"


# ──────────────────────────────────────────────────────────────────────────────
# PERSISTENCE
# ──────────────────────────────────────────────────────────────────────────────

def save_summary(conn, month_start, summary_text):
    # Καθαρισμός τυχόν προηγούμενου κρυφού σφάλματος στη σύνδεση
    conn.rollback() 

    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO AI_Monthly_Summaries (Month_Start, Summary_Text)
                VALUES (%s, %s)
                ON CONFLICT (Month_Start) 
                DO UPDATE SET 
                    Summary_Text = EXCLUDED.Summary_Text,
                    Generated_At = CURRENT_TIMESTAMP;
            """, (month_start, summary_text))
        conn.commit()  # Οριστικοποίηση αν όλα πήγαν καλά

    except Exception as e:
        conn.rollback()  # Καθαρισμός της αποτυχημένης συναλλαγής
        print(f"Database error in save_summary: {e}")
        raise e

# ──────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ──────────────────────────────────────────────────────────────────────────────

def run(target_month=None):
    logging.info("Starting Monthly AI financial summary generation...")

    conn = get_connection()
    try:
        ensure_summary_table(conn)

        if target_month is None:
            # ---------------------------------------------------------
            # ΠΡΟΕΠΙΛΟΓΗ: Υπολογισμός για τον ΠΡΟΗΓΟΥΜΕΝΟ μήνα
            # ---------------------------------------------------------
            current_date = pd.Timestamp.now()
            # Πηγαίνουμε στον προηγούμενο μήνα και βρίσκουμε την 1η του μέρα
            first_day = (current_date - pd.offsets.MonthEnd(1)).replace(day=1).strftime('%Y-%m-%d')
            # Βρίσκουμε την τελευταία μέρα του προηγούμενου μήνα
            last_day = (current_date - pd.offsets.MonthEnd(1)).strftime('%Y-%m-%d')
        else:
            # ---------------------------------------------------------
            # ΕΠΙΛΟΓΗ ΧΡΗΣΤΗ: Υπολογισμός για τον μήνα που δόθηκε ως input
            # ---------------------------------------------------------
            try:
                parsed_date = pd.to_datetime(target_month)
                first_day = parsed_date.replace(day=1).strftime('%Y-%m-%d')
                last_day = (parsed_date + pd.offsets.MonthEnd(0)).strftime('%Y-%m-%d')
            except Exception as e:
                logging.error(f"Invalid date format specified: {target_month}. Use 'YYYY-MM-DD' or 'YYYY-MM'.")
                raise e

        month_start = first_day
        month_end = last_day
        logging.info(f"Month start: {month_start}")
        logging.info(f"Month end: {month_end}")

        context = _gather_context(conn, month_start, month_end)
        logging.info("Context gathered:\n%s", context)

        llm = init_llm()
        summary = generate_summary(llm, context)
        logging.info("Summary generated.")

        save_summary(conn, month_start, summary)
        logging.info("Summary saved to DB.")

        print(f"\n{'='*60}")
        print(f"MONTHLY SUMMARY  ({month_start})")
        print('='*60)
        print(summary)
        print('='*60)

    finally:
        conn.close()


if __name__ == "__main__":
    run()
