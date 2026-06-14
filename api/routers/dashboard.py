"""Dashboard API endpoints: net worth, accounts, summaries, drafts."""
from fastapi import APIRouter, Query, HTTPException
from typing import Optional
import math
import pandas as pd
from database.connection import get_db, get_connection

router = APIRouter()


def _df_to_list(df: pd.DataFrame) -> list:
    """Convert DataFrame to JSON-serialisable list of dicts."""
    df = df.copy()
    for col in df.select_dtypes(include=["datetime", "datetimetz"]).columns:
        df[col] = df[col].astype(str)
    records = df.where(pd.notnull(df), other=None).to_dict(orient="records")
    return [{k: None if isinstance(v, float) and math.isnan(v) else v for k, v in r.items()} for r in records]


@router.get("/net-worth")
def get_net_worth(start_date: str = Query("2020-01-01")):
    """Historical monthly net worth (cash, invested, pension, assets)."""
    query = f"""
    WITH RECURSIVE
    months AS (
        SELECT (date_trunc('month', %(sd)s::date) + INTERVAL '1 month' - INTERVAL '1 day')::date AS d
        UNION ALL
        SELECT (date_trunc('month', d + INTERVAL '1 month') + INTERVAL '1 month' - INTERVAL '1 day')::date
        FROM months WHERE d < date_trunc('month', CURRENT_DATE)
    ),
    dates AS (
        SELECT d FROM months WHERE d <= CURRENT_DATE
        UNION SELECT CURRENT_DATE::date
    ),
    historical_assets AS (
        SELECT dt.d AS date, a.Accounts_Id, a.Currencies_Id,
            a.Accounts_Balance - COALESCE((
                SELECT SUM(Total_Amount) FROM Transactions
                WHERE Accounts_Id = a.Accounts_Id AND Date > dt.d
            ), 0) AS balance_at_date
        FROM dates dt CROSS JOIN Accounts a
        WHERE a.Accounts_Type IN ('Real Estate', 'Vehicle', 'Asset', 'Liability')
    ),
    historical_cash AS (
        SELECT dt.d AS date, a.Accounts_Id, a.Currencies_Id,
            a.Accounts_Balance - COALESCE((
                SELECT SUM(Total_Amount) FROM Transactions
                WHERE Accounts_Id = a.Accounts_Id AND Date > dt.d
            ), 0) AS balance_at_date
        FROM dates dt CROSS JOIN Accounts a
        WHERE a.Accounts_Type NOT IN ('Brokerage','Pension','Other Investment','Margin','Real Estate','Vehicle','Asset','Liability')
        UNION ALL
        SELECT dt.d AS date, a.Accounts_Id, a.Currencies_Id,
            a.Accounts_Balance - COALESCE((
                SELECT SUM(Total_Amount) FROM Transactions
                WHERE Accounts_Id = a.Accounts_Id AND Date > dt.d
            ), 0) AS balance_at_date
        FROM dates dt CROSS JOIN Accounts a
        WHERE a.Accounts_Type IN ('Other Investment')
    ),
    historical_pension AS (
        SELECT dt.d AS date, a.Accounts_Id, a.Currencies_Id,
            a.Accounts_Balance - COALESCE((
                SELECT SUM(CASE WHEN Action IN ('CashIn','IntInc') THEN Total_Amount_AccCur
                               WHEN Action IN ('CashOut') THEN -Total_Amount_AccCur
                               ELSE 0 END)
                FROM Investments WHERE Accounts_Id = a.Accounts_Id AND Date > dt.d
            ), 0) AS balance_at_date
        FROM dates dt CROSS JOIN Accounts a
        WHERE a.Accounts_Type IN ('Pension')
    ),
    historical_inv AS (
        SELECT dt.d AS date, h.Securities_Id,
            h.Quantity - COALESCE((
                SELECT SUM(CASE WHEN Action='Buy' THEN Quantity WHEN Action='Sell' THEN -Quantity ELSE 0 END)
                FROM Investments WHERE Securities_Id = h.Securities_Id AND Date > dt.d
            ), 0) AS qty_at_date
        FROM dates dt CROSS JOIN Holdings h
    ),
    daily_fx AS (
        SELECT dt.d AS date, c.Currencies_Id,
            (SELECT FX_Rate FROM Historical_FX WHERE Date <= dt.d AND Currencies_Id_1 = c.Currencies_Id ORDER BY Date DESC LIMIT 1) AS fx_rate
        FROM dates dt CROSS JOIN Currencies c
    ),
    daily_prices AS (
        SELECT dt.d AS date, s.Securities_Id,
            (SELECT Close FROM Historical_Prices WHERE Date <= dt.d AND Securities_Id = s.Securities_Id ORDER BY Date DESC LIMIT 1) AS close
        FROM dates dt CROSS JOIN Securities s
    ),
    final_calculation AS (
        SELECT dt.d AS date,
            (SELECT SUM(CASE WHEN cur.Currencies_ShortName='EUR' THEN ha.balance_at_date
                             ELSE ha.balance_at_date * COALESCE(dfx.fx_rate,1) END)
             FROM historical_assets ha
             JOIN Currencies cur ON ha.Currencies_Id = cur.Currencies_Id
             LEFT JOIN daily_fx dfx ON ha.date=dfx.date AND ha.Currencies_Id=dfx.Currencies_Id
             WHERE ha.date=dt.d) AS total_assets,
            (SELECT SUM(CASE WHEN cur.Currencies_ShortName='EUR' THEN hc.balance_at_date
                             ELSE hc.balance_at_date * COALESCE(dfx.fx_rate,1) END)
             FROM historical_cash hc
             JOIN Currencies cur ON hc.Currencies_Id = cur.Currencies_Id
             LEFT JOIN daily_fx dfx ON hc.date=dfx.date AND hc.Currencies_Id=dfx.Currencies_Id
             WHERE hc.date=dt.d) AS total_cash,
            (SELECT SUM(CASE WHEN cur.Currencies_ShortName='EUR' THEN hp.balance_at_date
                             ELSE hp.balance_at_date * COALESCE(dfx.fx_rate,1) END)
             FROM historical_pension hp
             JOIN Currencies cur ON hp.Currencies_Id = cur.Currencies_Id
             LEFT JOIN daily_fx dfx ON hp.date=dfx.date AND hp.Currencies_Id=dfx.Currencies_Id
             WHERE hp.date=dt.d) AS total_pension,
            (SELECT SUM(hi.qty_at_date * COALESCE(dp.close,0) *
                CASE WHEN cs.Currencies_ShortName='EUR' THEN 1 ELSE COALESCE(dfx_inv.fx_rate,1) END)
             FROM historical_inv hi
             JOIN Securities s ON hi.Securities_Id=s.Securities_Id
             JOIN Currencies cs ON s.Currencies_Id=cs.Currencies_Id
             LEFT JOIN daily_prices dp ON hi.date=dp.date AND hi.Securities_Id=dp.Securities_Id
             LEFT JOIN daily_fx dfx_inv ON hi.date=dfx_inv.date AND s.Currencies_Id=dfx_inv.Currencies_Id
             WHERE hi.date=dt.d) AS total_invested
        FROM dates dt
    )
    SELECT date,
           COALESCE(total_assets,0) AS total_assets,
           COALESCE(total_cash,0) AS total_cash,
           COALESCE(total_pension,0) AS total_pension,
           COALESCE(total_invested,0) AS total_invested,
           (COALESCE(total_assets,0)+COALESCE(total_cash,0)+COALESCE(total_pension,0)+COALESCE(total_invested,0)) AS total_net_worth
    FROM final_calculation
    ORDER BY date ASC
    """
    with get_db() as conn:
        df = pd.read_sql(query, conn, params={"sd": start_date})
    return _df_to_list(df)


@router.get("/accounts")
def get_accounts(include_future: bool = Query(False)):
    """All accounts with balance, currency and EUR equivalent.

    include_future=False (default): cash balances exclude transactions dated
    after today (i.e. show today's actual balance, not what's already scheduled).
    include_future=True: Accounts_Balance is used as-is (includes all future-
    dated non-draft transactions that have already been entered).

    Investment accounts (Brokerage, Margin, Other Investment) always show
    the current market value of holdings.  Pension uses Accounts_Balance
    (maintained by update_pension_balances).
    """
    future_clause = "" if include_future else """
        -- subtract future-dated non-draft transactions to get today's balance
        - COALESCE((
            SELECT SUM(t.Total_Amount)
            FROM Transactions t
            WHERE t.Accounts_Id = a.Accounts_Id
              AND t.Date > CURRENT_DATE
              AND t.Is_Draft = FALSE
        ), 0)
    """
    with get_db() as conn:
        df = pd.read_sql(f"""
            WITH latest_fx AS (
                SELECT DISTINCT ON (Currencies_Id_1)
                    Currencies_Id_1, FX_Rate
                FROM Historical_FX
                ORDER BY Currencies_Id_1, Date DESC
            ),
            latest_price AS (
                SELECT DISTINCT ON (Securities_Id)
                    Securities_Id, Close
                FROM Historical_Prices
                ORDER BY Securities_Id, Date DESC
            ),
            holdings_value AS (
                SELECT
                    h.Accounts_Id,
                    SUM(
                        h.Quantity
                        * COALESCE(lp.Close, 0)
                        * CASE WHEN sc.Currencies_ShortName = 'EUR' THEN 1
                               ELSE COALESCE(lfx.FX_Rate, 1) END
                    ) AS value_eur
                FROM Holdings h
                JOIN Securities s  ON s.Securities_Id  = h.Securities_Id
                JOIN Currencies sc ON sc.Currencies_Id = s.Currencies_Id
                LEFT JOIN latest_price lp  ON lp.Securities_Id  = h.Securities_Id
                LEFT JOIN latest_fx   lfx ON lfx.Currencies_Id_1 = s.Currencies_Id
                GROUP BY h.Accounts_Id
            )
            SELECT
                a.Accounts_Id      AS id,
                a.Accounts_Name    AS name,
                a.Accounts_Type    AS type,
                a.Accounts_Balance {future_clause} AS balance,
                a.Is_Active        AS is_active,
                c.Currencies_ShortName AS currency,
                i.Institutions_Name    AS institution,
                CASE
                    WHEN a.Accounts_Type IN ('Brokerage','Margin','Other Investment')
                    THEN COALESCE(hv.value_eur, 0)
                    WHEN c.Currencies_ShortName = 'EUR'
                    THEN a.Accounts_Balance {future_clause}
                    ELSE (a.Accounts_Balance {future_clause}) * COALESCE(lfx.FX_Rate, 1)
                END AS balance_eur
            FROM Accounts a
            JOIN Currencies c ON a.Currencies_Id = c.Currencies_Id
            LEFT JOIN Institutions i    ON a.Institutions_Id = i.Institutions_Id
            LEFT JOIN latest_fx lfx     ON lfx.Currencies_Id_1 = c.Currencies_Id
            LEFT JOIN holdings_value hv ON hv.Accounts_Id = a.Accounts_Id
            ORDER BY a.Is_Active DESC, a.Accounts_Type, a.Accounts_Name
        """, conn)
    return _df_to_list(df)


@router.get("/alerts")
def get_alerts():
    """Triggered user-defined alerts (price, allocation drift, etc.)."""
    try:
        from database.queries import check_triggered_alerts
        return check_triggered_alerts()
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/upcoming-bills")
def get_upcoming_bills(days: int = Query(14)):
    """Upcoming bills: confirmed future transactions + pattern-detected recurring payees."""
    with get_db() as conn:
        # 1. Confirmed: future-dated register entries (same logic as old UI)
        df_confirmed = pd.read_sql("""
            WITH RECURSIVE cat_path AS (
                SELECT Categories_Id, Categories_Name::TEXT AS full_path, Categories_Id_Parent
                FROM Categories WHERE Categories_Id_Parent IS NULL
                UNION ALL
                SELECT c.Categories_Id, cp.full_path || ' : ' || c.Categories_Name, c.Categories_Id_Parent
                FROM Categories c JOIN cat_path cp ON c.Categories_Id_Parent = cp.Categories_Id
            )
            SELECT
                t.Date::text       AS date,
                p.Payees_Name      AS payee,
                t.Total_Amount     AS amount_eur,
                STRING_AGG(DISTINCT cp.full_path, ', ') AS category,
                'Confirmed'        AS type
            FROM Transactions t
            LEFT JOIN Payees p    ON p.Payees_Id       = t.Payees_Id
            LEFT JOIN Splits s    ON s.Transactions_Id = t.Transactions_Id
            LEFT JOIN cat_path cp ON cp.Categories_Id  = s.Categories_Id
            WHERE t.Date > CURRENT_DATE
              AND t.Date <= CURRENT_DATE + %(days)s * INTERVAL '1 day'
              AND t.Transfers_Id IS NULL
            GROUP BY t.Transactions_Id, t.Date, p.Payees_Name, t.Total_Amount
            ORDER BY t.Date
        """, conn, params={"days": days})

        # 2. Projected: payees present in ALL of the last 3 complete months
        df_projected = pd.read_sql("""
            WITH RECURSIVE cat_path AS (
                SELECT Categories_Id, Categories_Name::TEXT AS full_path, Categories_Id_Parent
                FROM Categories WHERE Categories_Id_Parent IS NULL
                UNION ALL
                SELECT c.Categories_Id, cp.full_path || ' : ' || c.Categories_Name, c.Categories_Id_Parent
                FROM Categories c JOIN cat_path cp ON c.Categories_Id_Parent = cp.Categories_Id
            ),
            recent AS (
                SELECT
                    p.Payees_Name,
                    DATE_TRUNC('month', t.Date)::date AS month_start,
                    t.Date,
                    SUM(s.Amount) AS amount,
                    cp.full_path  AS category
                FROM Transactions t
                LEFT JOIN Payees p    ON p.Payees_Id       = t.Payees_Id
                LEFT JOIN Splits s    ON s.Transactions_Id = t.Transactions_Id
                LEFT JOIN cat_path cp ON cp.Categories_Id  = s.Categories_Id
                WHERE t.Date >= DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '3 months'
                  AND t.Date <  DATE_TRUNC('month', CURRENT_DATE)
                  AND t.Payees_Id IS NOT NULL
                  AND t.Transfers_Id IS NULL
                GROUP BY p.Payees_Name, t.Date, DATE_TRUNC('month', t.Date)::date, cp.full_path
            ),
            qualified AS (
                SELECT Payees_Name FROM recent
                GROUP BY Payees_Name
                HAVING COUNT(DISTINCT month_start) = 3
            ),
            -- LAG must be computed before aggregating (PostgreSQL forbids window inside aggregate)
            with_lag AS (
                SELECT
                    r.Payees_Name,
                    r.category,
                    r.amount,
                    r.Date,
                    (r.Date - LAG(r.Date) OVER (
                        PARTITION BY r.Payees_Name, r.category ORDER BY r.Date
                    ))::float AS days_since_prev
                FROM recent r
                JOIN qualified q ON q.Payees_Name = r.Payees_Name
            ),
            stats AS (
                SELECT
                    Payees_Name,
                    category,
                    AVG(amount)            AS avg_amount,
                    MAX(Date)              AS last_date,
                    AVG(days_since_prev)   AS avg_interval_days
                FROM with_lag
                GROUP BY Payees_Name, category
            )
            SELECT
                (last_date + ROUND(COALESCE(avg_interval_days, 30))::int)::text AS date,
                Payees_Name     AS payee,
                avg_amount      AS amount_eur,
                category        AS category,
                'Projected'     AS type
            FROM stats
            WHERE (last_date + ROUND(COALESCE(avg_interval_days, 30))::int) > CURRENT_DATE
              AND (last_date + ROUND(COALESCE(avg_interval_days, 30))::int)
                    <= CURRENT_DATE + %(days)s * INTERVAL '1 day'
            ORDER BY date
        """, conn, params={"days": days})

    # Deduplicate: drop projected rows where there's already a confirmed entry
    # for the same payee within 7 days
    confirmed_list = _df_to_list(df_confirmed)
    projected_list = _df_to_list(df_projected)

    def _covered(proj):
        for c in confirmed_list:
            if c["payee"] == proj["payee"]:
                try:
                    delta = abs((pd.Timestamp(c["date"]) - pd.Timestamp(proj["date"])).days)
                    if delta <= 7:
                        return True
                except Exception:
                    pass
        return False

    projected_filtered = [p for p in projected_list if not _covered(p)]
    combined = sorted(confirmed_list + projected_filtered, key=lambda r: r.get("date") or "")
    return combined


@router.get("/anomalies")
def get_anomalies(days: int = Query(30), z: float = Query(2.5)):
    """Unusual transactions: amount >= z std-deviations from payee+category mean."""
    with get_db() as conn:
        df = pd.read_sql("""
            WITH RECURSIVE cat_path AS (
                SELECT Categories_Id, Categories_Name::TEXT AS full_path, Categories_Id_Parent
                FROM Categories WHERE Categories_Id_Parent IS NULL
                UNION ALL
                SELECT c.Categories_Id, cp.full_path || ' : ' || c.Categories_Name, c.Categories_Id_Parent
                FROM Categories c JOIN cat_path cp ON c.Categories_Id_Parent = cp.Categories_Id
            ),
            fx AS (
                SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
                FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC
            ),
            splits_eur AS (
                SELECT
                    t.Date::text                                                       AS date,
                    p.Payees_Name,
                    cp.full_path                                                       AS category,
                    a.Accounts_Name,
                    s.Amount,
                    ROUND((s.Amount * COALESCE(fx.FX_Rate, 1))::numeric, 2)           AS amount_eur
                FROM Transactions t
                JOIN Accounts a   ON t.Accounts_Id = a.Accounts_Id
                JOIN Currencies c ON a.Currencies_Id = c.Currencies_Id
                LEFT JOIN Payees p    ON t.Payees_Id       = p.Payees_Id
                LEFT JOIN Splits s    ON s.Transactions_Id = t.Transactions_Id
                LEFT JOIN cat_path cp ON cp.Categories_Id  = s.Categories_Id
                LEFT JOIN fx          ON fx.Currencies_Id_1 = c.Currencies_Id
                WHERE t.Date >= CURRENT_DATE - %(days)s * INTERVAL '1 day'
                  AND p.Payees_Name IS NOT NULL
                  AND s.Amount IS NOT NULL
                  AND t.Transfers_Id IS NULL
                  AND t.Is_Draft = FALSE
            ),
            stats AS (
                SELECT
                    Payees_Name, category,
                    AVG(amount_eur)    AS mean_eur,
                    STDDEV(amount_eur) AS std_eur,
                    COUNT(*)           AS sample_size
                FROM splits_eur
                GROUP BY Payees_Name, category
                HAVING COUNT(*) >= 3 AND STDDEV(amount_eur) > 0
            )
            SELECT
                se.date,
                se.Payees_Name AS payees_name,
                se.category,
                se.Accounts_Name AS accounts_name,
                se.amount_eur,
                ROUND(st.mean_eur::numeric, 2)  AS mean_eur,
                ROUND(st.std_eur::numeric, 2)   AS std_eur,
                ROUND(((se.amount_eur - st.mean_eur) / st.std_eur)::numeric, 2) AS z_score
            FROM splits_eur se
            JOIN stats st ON st.Payees_Name = se.Payees_Name
                          AND (st.category = se.category OR (st.category IS NULL AND se.category IS NULL))
            WHERE ABS((se.amount_eur - st.mean_eur) / st.std_eur) >= %(z)s
            ORDER BY ABS((se.amount_eur - st.mean_eur) / st.std_eur) DESC
            LIMIT 30
        """, conn, params={"days": days, "z": z})
    return _df_to_list(df)


@router.get("/weekly-summaries")
def get_weekly_summaries(limit: int = Query(12)):
    """Latest AI-generated weekly financial summaries."""
    with get_db() as conn:
        df = pd.read_sql("""
            SELECT week_start, summary_text
            FROM ai_weekly_summaries
            ORDER BY week_start DESC
            LIMIT %(limit)s
        """, conn, params={"limit": limit})
    return _df_to_list(df)


@router.post("/weekly-summaries/generate")
def generate_weekly_summary(data: dict):
    """Generate (or regenerate) an AI summary for a given week and persist it."""
    week_start = data.get("week_start")  # expects 'YYYY-MM-DD' (Monday)
    if not week_start:
        raise HTTPException(400, "week_start required")

    with get_db() as conn:
        stats_df = pd.read_sql("""
            SELECT
                SUM(CASE WHEN s.Amount > 0 THEN s.Amount ELSE 0 END) AS income,
                ABS(SUM(CASE WHEN s.Amount < 0 THEN s.Amount ELSE 0 END)) AS expenses
            FROM Transactions t
            JOIN Splits s ON s.Transactions_Id = t.Transactions_Id
            WHERE t.Transfers_Id IS NULL AND t.Is_Draft = FALSE
              AND t.Date >= %(ws)s::date AND t.Date < %(ws)s::date + INTERVAL '7 days'
        """, conn, params={"ws": week_start})

        top_df = pd.read_sql("""
            WITH RECURSIVE cp AS (
                SELECT Categories_Id, Categories_Name::TEXT AS full_path
                FROM Categories WHERE Categories_Id_Parent IS NULL
                UNION ALL
                SELECT c.Categories_Id, cp.full_path || ' : ' || c.Categories_Name
                FROM Categories c JOIN cp ON c.Categories_Id_Parent = cp.Categories_Id
            )
            SELECT cp.full_path AS category, ROUND(ABS(SUM(s.Amount))::numeric, 2) AS spent
            FROM Transactions t
            JOIN Splits s ON s.Transactions_Id = t.Transactions_Id
            JOIN cp ON cp.Categories_Id = s.Categories_Id
            WHERE t.Transfers_Id IS NULL AND t.Is_Draft = FALSE AND s.Amount < 0
              AND t.Date >= %(ws)s::date AND t.Date < %(ws)s::date + INTERVAL '7 days'
            GROUP BY 1 ORDER BY 2 DESC LIMIT 5
        """, conn, params={"ws": week_start})

    income = float(stats_df.iloc[0]["income"] or 0)
    expenses = float(stats_df.iloc[0]["expenses"] or 0)
    savings = income - expenses
    top_cats = "; ".join(f"{r['category']} €{float(r['spent']):,.0f}" for _, r in top_df.iterrows())

    prompt = (
        f"Weekly financial summary for the week starting {week_start}. "
        f"Income: €{income:,.0f}. Expenses: €{expenses:,.0f}. Net: €{savings:,.0f}. "
        f"Top expense categories: {top_cats or 'none'}. "
        "Write a concise 2-4 sentence summary of this week's spending with one practical tip. "
        "Be direct and conversational, no markdown."
    )

    try:
        from ai.agent import run_agent
        summary_text = run_agent(prompt)
    except Exception:
        summary_text = (
            f"Income €{income:,.0f} · Expenses €{expenses:,.0f} · Net €{savings:,.0f}. "
            f"Top: {top_cats or 'N/A'}."
        )

    conn2 = get_connection()
    try:
        cur = conn2.cursor()
        cur.execute("""
            INSERT INTO ai_weekly_summaries (week_start, summary_text)
            VALUES (%s, %s)
            ON CONFLICT (week_start) DO UPDATE SET summary_text = EXCLUDED.summary_text
        """, (week_start, summary_text))
        conn2.commit()
    except Exception as e:
        conn2.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn2.close()

    return {"week_start": week_start, "summary_text": summary_text}


@router.get("/monthly-summaries")
def get_monthly_summaries(limit: int = Query(24)):
    """Latest AI-generated monthly financial summaries."""
    with get_db() as conn:
        df = pd.read_sql("""
            SELECT month_start, summary_text
            FROM ai_monthly_summaries
            ORDER BY month_start DESC
            LIMIT %(limit)s
        """, conn, params={"limit": limit})
    return _df_to_list(df)


@router.get("/draft-transactions")
def get_draft_transactions():
    """All pending draft transactions."""
    with get_db() as conn:
        df = pd.read_sql("""
            SELECT t.Transactions_Id AS id,
                   t.Date::text AS date,
                   t.Description AS description,
                   t.Total_Amount AS amount,
                   p.Payees_Name AS payee,
                   a.Accounts_Name AS account,
                   a.Accounts_Id AS account_id
            FROM Transactions t
            JOIN Accounts a ON t.Accounts_Id = a.Accounts_Id
            LEFT JOIN Payees p ON t.Payees_Id = p.Payees_Id
            WHERE t.Is_Draft = TRUE
            ORDER BY t.Date DESC, t.Transactions_Id DESC
        """, conn)
    return _df_to_list(df)


@router.post("/confirm-draft/{transaction_id}")
def confirm_draft(transaction_id: int):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE Transactions SET Is_Draft = FALSE WHERE Transactions_Id = %s AND Is_Draft = TRUE",
            (transaction_id,)
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Draft not found")
        conn.commit()
        return {"message": "Confirmed"}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.delete("/delete-draft/{transaction_id}")
def delete_draft(transaction_id: int):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM Splits WHERE Transactions_Id = %s", (transaction_id,))
        cur.execute("DELETE FROM Transactions WHERE Transactions_Id = %s AND Is_Draft = TRUE", (transaction_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Draft not found")
        conn.commit()
        return {"deleted": transaction_id}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.post("/confirm-all-drafts")
def confirm_all_drafts():
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE Transactions SET Is_Draft = FALSE WHERE Is_Draft = TRUE")
        count = cur.rowcount
        conn.commit()
        return {"confirmed": count}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()

@router.post("/monthly-summaries/generate")
def generate_monthly_summary(data: dict):
    """Generate (or regenerate) an AI summary for a given month and persist it."""
    month_start = data.get("month_start")  # expects 'YYYY-MM-01'
    if not month_start:
        raise HTTPException(400, "month_start required")

    # Gather stats for the month
    with get_db() as conn:
        stats_df = pd.read_sql("""
            SELECT
                SUM(CASE WHEN s.Amount > 0 THEN s.Amount ELSE 0 END) AS income,
                ABS(SUM(CASE WHEN s.Amount < 0 THEN s.Amount ELSE 0 END)) AS expenses
            FROM Transactions t
            JOIN Splits s ON s.Transactions_Id = t.Transactions_Id
            WHERE t.Transfers_Id IS NULL AND t.Is_Draft = FALSE
              AND t.Date >= %(ms)s::date
              AND t.Date <  %(ms)s::date + INTERVAL '1 month'
        """, conn, params={"ms": month_start})

        top_df = pd.read_sql("""
            WITH RECURSIVE cp AS (
                SELECT Categories_Id, Categories_Name::TEXT AS full_path
                FROM Categories WHERE Categories_Id_Parent IS NULL
                UNION ALL
                SELECT c.Categories_Id, cp.full_path || ' : ' || c.Categories_Name
                FROM Categories c JOIN cp ON c.Categories_Id_Parent = cp.Categories_Id
            )
            SELECT cp.full_path AS category, ROUND(ABS(SUM(s.Amount))::numeric, 2) AS spent
            FROM Transactions t
            JOIN Splits s ON s.Transactions_Id = t.Transactions_Id
            JOIN cp ON cp.Categories_Id = s.Categories_Id
            WHERE t.Transfers_Id IS NULL AND t.Is_Draft = FALSE AND s.Amount < 0
              AND t.Date >= %(ms)s::date AND t.Date < %(ms)s::date + INTERVAL '1 month'
            GROUP BY 1 ORDER BY 2 DESC LIMIT 5
        """, conn, params={"ms": month_start})

    income = float(stats_df.iloc[0]["income"] or 0)
    expenses = float(stats_df.iloc[0]["expenses"] or 0)
    savings = income - expenses
    savings_rate = (savings / income * 100) if income > 0 else 0
    top_cats = "; ".join(f"{r['category']} €{float(r['spent']):,.0f}" for _, r in top_df.iterrows())

    prompt = (
        f"Monthly financial summary for {month_start[:7]}. "
        f"Income: €{income:,.0f}. Expenses: €{expenses:,.0f}. "
        f"Savings: €{savings:,.0f} ({savings_rate:.1f}% savings rate). "
        f"Top expense categories: {top_cats or 'none'}. "
        "Write a concise 3-5 sentence summary with key observations and one actionable tip. "
        "Be direct and friendly, no markdown headers."
    )

    try:
        from ai.agent import run_agent
        summary_text = run_agent(prompt)
    except Exception as e:
        summary_text = (
            f"Income €{income:,.0f} · Expenses €{expenses:,.0f} · "
            f"Savings €{savings:,.0f} ({savings_rate:.1f}%). "
            f"Top categories: {top_cats or 'N/A'}."
        )

    conn2 = get_connection()
    try:
        cur = conn2.cursor()
        cur.execute("""
            INSERT INTO ai_monthly_summaries (month_start, summary_text)
            VALUES (%s, %s)
            ON CONFLICT (month_start) DO UPDATE SET summary_text = EXCLUDED.summary_text
        """, (month_start, summary_text))
        conn2.commit()
    except Exception as e:
        conn2.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn2.close()

    return {"month_start": month_start, "summary_text": summary_text}


@router.get("/insights")
def get_insights():
    """Actionable spending insights."""
    insights = []
    with get_db() as conn:
        try:
            df = pd.read_sql("""
                WITH RECURSIVE cat_path AS (
                    SELECT Categories_Id, Categories_Name::TEXT AS full_path, Categories_Id_Parent
                    FROM Categories WHERE Categories_Id_Parent IS NULL
                    UNION ALL
                    SELECT c.Categories_Id, cp.full_path || ' : ' || c.Categories_Name, c.Categories_Id_Parent
                    FROM Categories c JOIN cat_path cp ON c.Categories_Id_Parent = cp.Categories_Id
                ),
                last_month AS (
                    SELECT cp.full_path AS category, ABS(SUM(s.Amount)) AS spent
                    FROM Transactions t
                    JOIN Splits s ON s.Transactions_Id = t.Transactions_Id
                    JOIN cat_path cp ON cp.Categories_Id = s.Categories_Id
                    WHERE t.Date >= DATE_TRUNC('month', CURRENT_DATE - INTERVAL '1 month')
                      AND t.Date <  DATE_TRUNC('month', CURRENT_DATE)
                      AND t.Transfers_Id IS NULL AND t.Is_Draft = FALSE AND s.Amount < 0
                    GROUP BY 1
                ),
                prev_month AS (
                    SELECT cp.full_path AS category, ABS(SUM(s.Amount)) AS spent
                    FROM Transactions t
                    JOIN Splits s ON s.Transactions_Id = t.Transactions_Id
                    JOIN cat_path cp ON cp.Categories_Id = s.Categories_Id
                    WHERE t.Date >= DATE_TRUNC('month', CURRENT_DATE - INTERVAL '2 months')
                      AND t.Date <  DATE_TRUNC('month', CURRENT_DATE - INTERVAL '1 month')
                      AND t.Transfers_Id IS NULL AND t.Is_Draft = FALSE AND s.Amount < 0
                    GROUP BY 1
                )
                SELECT l.category,
                       ROUND(l.spent::numeric,2) AS this_month,
                       ROUND(COALESCE(p.spent,0)::numeric,2) AS last_month,
                       ROUND((l.spent - COALESCE(p.spent,0))::numeric,2) AS increase,
                       CASE WHEN COALESCE(p.spent,0) > 0
                            THEN ROUND(((l.spent - p.spent) / p.spent * 100)::numeric,1)
                            ELSE NULL END AS pct_change
                FROM last_month l LEFT JOIN prev_month p ON p.category = l.category
                WHERE l.spent > 30 AND l.spent > COALESCE(p.spent,0) * 1.20
                ORDER BY increase DESC LIMIT 3
            """, conn)
            for _, row in df.iterrows():
                pct = row["pct_change"]
                inc = float(row["increase"])
                cat = row["category"]
                is_nan = isinstance(pct, float) and math.isnan(pct)
                change_desc = f"{pct:+.0f}% more than prior month (up EUR {inc:,.0f})" if pct is not None and not is_nan else f"up EUR {inc:,.0f} vs prior month"
                insights.append({"type": "warning" if (pct or 0) < 50 else "danger", "icon": "overspend",
                    "title": f"Overspending: {cat}",
                    "message": f"Last month you spent EUR {float(row['this_month']):,.0f} on {cat} - {change_desc}."})
        except Exception:
            pass
        try:
            df = pd.read_sql("""
                SELECT a.Accounts_Name AS name, ROUND(a.Accounts_Balance::numeric,2) AS balance,
                       c.Currencies_ShortName AS currency
                FROM Accounts a JOIN Currencies c ON c.Currencies_Id = a.Currencies_Id
                WHERE a.Is_Active = TRUE AND a.Accounts_Type IN ('Checking','Savings','Cash')
                  AND a.Accounts_Balance < 0 ORDER BY a.Accounts_Balance ASC
            """, conn)
            for _, row in df.iterrows():
                insights.append({"type": "danger", "icon": "negative_balance",
                    "title": f"Negative balance: {row['name']}",
                    "message": f"{row['name']} is overdrawn: {row['currency']} {float(row['balance']):,.2f}."})
        except Exception:
            pass
        try:
            df = pd.read_sql("""
                SELECT a.Accounts_Name AS name,
                       ROUND(ABS(a.Credit_Limit)::numeric,2) AS credit_limit,
                       ROUND((ABS(a.Credit_Limit) + a.Accounts_Balance)::numeric,2) AS remaining,
                       ROUND(((ABS(a.Credit_Limit) + a.Accounts_Balance) / NULLIF(ABS(a.Credit_Limit),0) * 100)::numeric,1) AS pct_remaining,
                       c.Currencies_ShortName AS currency
                FROM Accounts a JOIN Currencies c ON c.Currencies_Id = a.Currencies_Id
                WHERE a.Is_Active = TRUE AND a.Accounts_Type = 'Credit Card'
                  AND a.Credit_Limit IS NOT NULL AND a.Credit_Limit <> 0
                  AND ((ABS(a.Credit_Limit) + a.Accounts_Balance) / NULLIF(ABS(a.Credit_Limit),0) < 0.10
                       OR (ABS(a.Credit_Limit) + a.Accounts_Balance) < 500)
                ORDER BY remaining ASC
            """, conn)
            for _, row in df.iterrows():
                pct_rem = float(row["pct_remaining"]) if row["pct_remaining"] is not None else 0
                insights.append({"type": "danger" if pct_rem < 5 else "warning", "icon": "credit_limit",
                    "title": f"Credit limit nearly reached: {row['name']}",
                    "message": f"{row['name']} has only {row['currency']} {float(row['remaining']):,.0f} ({pct_rem:.1f}%) of its {row['currency']} {float(row['credit_limit']):,.0f} limit remaining."})
        except Exception:
            pass
        try:
            df = pd.read_sql("""
                WITH stats AS (
                    SELECT COALESCE(p.Payees_Name,'Unknown') AS payee,
                           AVG(ABS(t.Total_Amount)) AS mean_amt,
                           STDDEV(ABS(t.Total_Amount)) AS std_amt,
                           COUNT(*) AS sample_count
                    FROM Transactions t LEFT JOIN Payees p ON p.Payees_Id = t.Payees_Id
                    WHERE t.Date >= CURRENT_DATE - INTERVAL '90 days'
                      AND t.Transfers_Id IS NULL AND t.Total_Amount <> 0
                    GROUP BY 1 HAVING COUNT(*) >= 3
                )
                SELECT t.Date AS date, COALESCE(p.Payees_Name, t.Description, '?') AS payee,
                       t.Description AS description,
                       ROUND(t.Total_Amount::numeric,2) AS amount,
                       ROUND(s.mean_amt::numeric,2) AS typical,
                       ROUND(ABS(ABS(t.Total_Amount) - s.mean_amt) / NULLIF(s.std_amt,0),1) AS z_score,
                       s.sample_count
                FROM Transactions t
                LEFT JOIN Payees p ON p.Payees_Id = t.Payees_Id
                JOIN stats s ON s.payee = COALESCE(p.Payees_Name,'Unknown')
                WHERE t.Date >= CURRENT_DATE - INTERVAL '14 days' AND t.Is_Draft = FALSE
                  AND ABS(ABS(t.Total_Amount) - s.mean_amt) / NULLIF(s.std_amt,0) >= 2.5
                ORDER BY ABS(t.Total_Amount) DESC LIMIT 3
            """, conn)
            for _, row in df.iterrows():
                desc = str(row.get("description") or "").strip()
                desc_part = f" - {desc}" if desc and desc not in ("?", str(row["payee"])) else ""
                insights.append({"type": "info", "icon": "unusual_tx",
                    "title": f"Unusual transaction: {row['payee']}",
                    "message": f"EUR {abs(float(row['amount'])):,.2f} on {str(row['date'])[:10]} with {row['payee']}{desc_part} - {float(row['z_score'])}x above the usual EUR {float(row['typical']):,.2f} (based on {row['sample_count']} transactions)."})
        except Exception:
            pass
        try:
            df = pd.read_sql("""
                SELECT DATE_TRUNC('month', t.Date)::date AS month,
                       SUM(CASE WHEN s.Amount > 0 THEN s.Amount ELSE 0 END) AS income,
                       ABS(SUM(CASE WHEN s.Amount < 0 THEN s.Amount ELSE 0 END)) AS expenses
                FROM Transactions t
                JOIN Splits s ON s.Transactions_Id = t.Transactions_Id
                WHERE t.Transfers_Id IS NULL AND t.Is_Draft = FALSE
                  AND t.Date >= DATE_TRUNC('month', CURRENT_DATE - INTERVAL '3 months')
                  AND t.Date <  DATE_TRUNC('month', CURRENT_DATE)
                GROUP BY 1 ORDER BY 1
            """, conn)
            if len(df) >= 2 and float(df["income"].sum()) > 0:
                df["savings_rate"] = ((df["income"] - df["expenses"]) / df["income"] * 100).round(1)
                latest = float(df.iloc[-1]["savings_rate"])
                prev_sr = float(df.iloc[-2]["savings_rate"])
                delta = latest - prev_sr
                income = float(df.iloc[-1]["income"])
                expenses = float(df.iloc[-1]["expenses"])
                m = df.iloc[-1]["month"]
                month_lbl = m.strftime("%B") if hasattr(m, "strftime") else str(m)[:7]
                if latest >= 25 and delta >= 5:
                    insights.append({"type": "success", "icon": "savings_up", "title": "Savings rate improving",
                        "message": f"Your savings rate in {month_lbl} was {latest:.1f}% - up {delta:+.1f} pp vs. the prior month."})
                elif latest < 0:
                    insights.append({"type": "warning", "icon": "savings_deficit",
                        "title": f"Expenses exceed income - {month_lbl}",
                        "message": f"In {month_lbl} expenses (EUR {expenses:,.0f}) exceeded income (EUR {income:,.0f}) by EUR {expenses-income:,.0f}. Check for unlinked transfers."})
                elif latest < 10:
                    insights.append({"type": "warning", "icon": "savings_low",
                        "title": f"Low savings rate - {month_lbl}",
                        "message": f"Savings rate in {month_lbl} was only {latest:.1f}% (EUR {income:,.0f} income, EUR {expenses:,.0f} expenses)."})
        except Exception:
            pass
    return insights
