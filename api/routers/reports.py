"""Reports API endpoints: income/expense, P&L, savings rate."""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional
import math
import pandas as pd
from database.connection import get_db, get_connection

router = APIRouter()


def _df_to_list(df: pd.DataFrame) -> list:
    df = df.copy()
    for col in df.select_dtypes(include=["datetime", "datetimetz"]).columns:
        df[col] = df[col].astype(str)
    records = df.where(pd.notnull(df), other=None).to_dict(orient="records")
    return [{k: None if isinstance(v, float) and math.isnan(v) else v for k, v in r.items()} for r in records]


@router.get("/income-expense")
def get_income_expense(
    start_date: str = Query("2024-01-01"),
    end_date: str = Query("2099-12-31"),
):
    """Monthly income vs expense totals, EUR-converted."""
    query = """
    WITH RECURSIVE CategoryHierarchy AS (
        SELECT Categories_Id, Categories_Name::TEXT AS Full_Path,
               Categories_Type::TEXT AS Categories_Type, Categories_Id_Parent, 0 AS Level
        FROM Categories WHERE Categories_Id_Parent IS NULL
        UNION ALL
        SELECT c.Categories_Id, ch.Full_Path || ' : ' || c.Categories_Name,
               c.Categories_Type::TEXT, c.Categories_Id_Parent, ch.Level + 1
        FROM Categories c JOIN CategoryHierarchy ch ON c.Categories_Id_Parent = ch.Categories_Id
    ),
    tx_with_cat AS (
        SELECT
            date_trunc('month', t.Date)::date AS month,
            COALESCE(s.Amount, t.Total_Amount) AS amount,
            COALESCE(cat.Categories_Type, 'Uncategorized') AS cat_type,
            SPLIT_PART(COALESCE(cat.Full_Path, 'Uncategorized'), ' : ', 1) AS top_category,
            COALESCE(cat.Full_Path, 'Uncategorized') AS full_category
        FROM Transactions t
        LEFT JOIN Splits s ON s.Transactions_Id = t.Transactions_Id
        LEFT JOIN CategoryHierarchy cat ON s.Categories_Id = cat.Categories_Id
        JOIN Accounts a ON t.Accounts_Id = a.Accounts_Id
        WHERE t.Date BETWEEN %(start_date)s AND %(end_date)s
          AND a.Accounts_Type IN ('Cash','Checking','Savings','Credit Card','Loan','Other')
          AND t.accounts_id_target IS NULL
    )
    SELECT month,
           SUM(CASE WHEN cat_type = 'Income' THEN amount ELSE 0 END) AS income,
           SUM(CASE WHEN cat_type = 'Interest' THEN amount ELSE 0 END) AS interest,
           SUM(CASE WHEN cat_type = 'Expense' THEN ABS(amount) ELSE 0 END) AS expense,
           SUM(CASE WHEN cat_type = 'Tax' THEN ABS(amount) ELSE 0 END) AS tax
    FROM tx_with_cat
    GROUP BY month
    ORDER BY month ASC
    """
    with get_db() as conn:
        df = pd.read_sql(query, conn, params={"start_date": start_date, "end_date": end_date})
    return _df_to_list(df)


@router.get("/top-categories")
def get_top_categories(
    start_date: str = Query("2024-01-01"),
    end_date: str = Query("2099-12-31"),
    cat_type: str = Query(pattern="^(Tax|Expense|Income|Interest)$"),
    top_n: int = Query(10),
):
    """Top N income or expense categories for the period."""
    query = """
    WITH RECURSIVE CategoryHierarchy AS (
        SELECT Categories_Id, Categories_Name::TEXT AS Full_Path,
               Categories_Type::TEXT AS Categories_Type, Categories_Id_Parent
        FROM Categories WHERE Categories_Id_Parent IS NULL
        UNION ALL
        SELECT c.Categories_Id, ch.Full_Path || ' : ' || c.Categories_Name,
               c.Categories_Type::TEXT, c.Categories_Id_Parent
        FROM Categories c JOIN CategoryHierarchy ch ON c.Categories_Id_Parent = ch.Categories_Id
    )
    SELECT
        SPLIT_PART(COALESCE(cat.Full_Path,'Uncategorized'),' : ',1) AS category,
        SUM(ABS(COALESCE(s.Amount, t.Total_Amount))) AS total
    FROM Transactions t
    LEFT JOIN Splits s ON s.Transactions_Id = t.Transactions_Id
    LEFT JOIN CategoryHierarchy cat ON s.Categories_Id = cat.Categories_Id
    JOIN Accounts a ON t.Accounts_Id = a.Accounts_Id
    WHERE t.Date BETWEEN %(start_date)s AND %(end_date)s
      AND a.Accounts_Type IN ('Cash','Checking','Savings','Credit Card','Loan','Other')
      AND t.accounts_id_target IS NULL
      AND COALESCE(cat.Categories_Type,'Uncategorized') = %(cat_type)s
    GROUP BY 1
    ORDER BY total DESC
    LIMIT %(top_n)s
    """
    with get_db() as conn:
        df = pd.read_sql(query, conn, params={
            "start_date": start_date,
            "end_date": end_date,
            "cat_type": cat_type,
            "top_n": top_n,
        })
    return _df_to_list(df)


@router.get("/savings-rate")
def get_savings_rate(months: int = Query(12)):
    """Monthly savings rate for the last N months."""
    query = """
    WITH RECURSIVE CategoryHierarchy AS (
        SELECT Categories_Id, Categories_Name::TEXT AS Full_Path,
               Categories_Type::TEXT AS Categories_Type, Categories_Id_Parent
        FROM Categories WHERE Categories_Id_Parent IS NULL
        UNION ALL
        SELECT c.Categories_Id, ch.Full_Path || ' : ' || c.Categories_Name,
               c.Categories_Type::TEXT, c.Categories_Id_Parent
        FROM Categories c JOIN CategoryHierarchy ch ON c.Categories_Id_Parent = ch.Categories_Id
    ),
    monthly AS (
        SELECT
            date_trunc('month', t.Date)::date AS month,
            SUM(CASE WHEN cat.Categories_Type='Income' THEN COALESCE(s.Amount,t.Total_Amount) ELSE 0 END) AS income,
            SUM(CASE WHEN cat.Categories_Type='Expense' THEN ABS(COALESCE(s.Amount,t.Total_Amount)) ELSE 0 END) AS expense
        FROM Transactions t
        LEFT JOIN Splits s ON s.Transactions_Id = t.Transactions_Id
        LEFT JOIN CategoryHierarchy cat ON s.Categories_Id=cat.Categories_Id
        JOIN Accounts a ON t.Accounts_Id=a.Accounts_Id
        WHERE a.Accounts_Type IN ('Cash','Checking','Savings','Credit Card','Loan','Other')
          AND t.accounts_id_target IS NULL
          AND t.Date >= (CURRENT_DATE - (%(months)s || ' months')::interval)
        GROUP BY 1
    )
    SELECT month,
           income,
           expense,
           CASE WHEN income > 0 THEN ROUND(((income - expense) / income * 100)::numeric, 1) ELSE 0 END AS savings_rate
    FROM monthly
    ORDER BY month ASC
    """
    with get_db() as conn:
        df = pd.read_sql(query, conn, params={"months": months})
    return _df_to_list(df)


@router.get("/portfolio-summary")
def get_portfolio_summary():
    """Current holdings with value in EUR grouped by account."""
    query = """
    SELECT
        a.Accounts_Name AS account,
        a.Accounts_Type AS account_type,
        s.Securities_Id AS securities_id,
        s.Securities_Name AS security,
        s.Ticker AS ticker,
        h.Quantity AS quantity,
        COALESCE(
            (SELECT Close FROM Historical_Prices WHERE Securities_Id=h.Securities_Id ORDER BY Date DESC LIMIT 1),
            0
        ) AS last_price,
        COALESCE(
            (SELECT FX_Rate FROM Historical_FX
             WHERE Currencies_Id_1=s.Currencies_Id
             ORDER BY Date DESC LIMIT 1),
            1
        ) AS fx_rate,
        h.Quantity * COALESCE(
            (SELECT Close FROM Historical_Prices WHERE Securities_Id=h.Securities_Id ORDER BY Date DESC LIMIT 1),
            0
        ) * COALESCE(
            (SELECT FX_Rate FROM Historical_FX WHERE Currencies_Id_1=s.Currencies_Id ORDER BY Date DESC LIMIT 1),
            1
        ) AS value_eur,
        c.Currencies_ShortName AS currency
    FROM Holdings h
    JOIN Securities s ON h.Securities_Id = s.Securities_Id
    JOIN Accounts a ON h.Accounts_Id = a.Accounts_Id
    JOIN Currencies c ON s.Currencies_Id = c.Currencies_Id
    WHERE h.Quantity != 0
    ORDER BY value_eur DESC
    """
    with get_db() as conn:
        df = pd.read_sql(query, conn)
    return _df_to_list(df)


@router.get("/allocation")
def get_allocation(scope: str = Query("investments")):
    """Asset allocation breakdown for a donut chart.

    scope=investments (default): only securities held in Holdings, grouped by Securities_Type.
    scope=all: full net-worth allocation — securities + cash + real assets.
    """
    with get_db() as conn:
        inv_df = pd.read_sql("""
            SELECT s.Securities_Type AS label,
                   SUM(h.Quantity * COALESCE(
                       (SELECT Close FROM Historical_Prices WHERE Securities_Id = h.Securities_Id ORDER BY Date DESC LIMIT 1), 0
                   ) * COALESCE(
                       (SELECT FX_Rate FROM Historical_FX WHERE Currencies_Id_1 = s.Currencies_Id ORDER BY Date DESC LIMIT 1), 1
                   )) AS value_eur
            FROM Holdings h
            JOIN Securities s ON h.Securities_Id = s.Securities_Id
            WHERE h.Quantity != 0
            GROUP BY s.Securities_Type
        """, conn)

        if scope == "all":
            cash_df = pd.read_sql("""
                SELECT 'Cash & Savings' AS label,
                       SUM(a.Accounts_Balance * COALESCE(
                           (SELECT FX_Rate FROM Historical_FX WHERE Currencies_Id_1 = a.Currencies_Id ORDER BY Date DESC LIMIT 1), 1
                       )) AS value_eur
                FROM Accounts a
                WHERE a.Accounts_Type NOT IN ('Brokerage','Pension','Other Investment','Margin','Real Estate','Vehicle','Asset','Liability')
                  AND a.Is_Active = TRUE
            """, conn)
            asset_df = pd.read_sql("""
                SELECT Accounts_Type AS label,
                       SUM(Accounts_Balance) AS value_eur
                FROM Accounts
                WHERE Accounts_Type IN ('Real Estate','Vehicle','Asset')
                  AND Is_Active = TRUE
                GROUP BY Accounts_Type
            """, conn)
            result = pd.concat([cash_df, inv_df, asset_df], ignore_index=True)
        else:
            result = inv_df

    result = result[result["value_eur"].notna() & (result["value_eur"] > 0)]
    return _df_to_list(result)


@router.get("/allocation-targets")
def get_allocation_targets():
    """Return saved target percentages from Allocation_Targets."""
    with get_db() as conn:
        df = pd.read_sql(
            "SELECT Securities_Type AS securities_type, Target_Pct AS target_pct FROM Allocation_Targets ORDER BY Securities_Type",
            conn,
        )
    return _df_to_list(df)


@router.post("/allocation-targets")
def save_allocation_targets(payload: dict):
    """Upsert {securities_type: target_pct} map into Allocation_Targets."""
    with get_db() as conn:
        cur = conn.cursor()
        for sec_type, pct in payload.items():
            cur.execute(
                """INSERT INTO Allocation_Targets (Securities_Type, Target_Pct)
                   VALUES (%s, %s)
                   ON CONFLICT (Securities_Type)
                   DO UPDATE SET Target_Pct = EXCLUDED.Target_Pct""",
                (sec_type, float(pct)),
            )
    return {"ok": True}


@router.get("/allocation-delta")
def get_allocation_delta():
    """Current allocation vs targets + delta for the Rebalancing Delta table."""
    with get_db() as conn:
        df = pd.read_sql("""
            WITH fx AS (
                SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
                FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC
            ),
            prices AS (
                SELECT DISTINCT ON (Securities_Id) Securities_Id, Close
                FROM Historical_Prices ORDER BY Securities_Id, Date DESC
            ),
            holdings_value AS (
                SELECT
                    s.Securities_Type::text AS securities_type,
                    SUM(h.Quantity * COALESCE(p.Close, 0) * COALESCE(fx.FX_Rate, 1)) AS value_eur
                FROM Holdings h
                JOIN Securities s ON h.Securities_Id = s.Securities_Id
                LEFT JOIN prices p  ON p.Securities_Id = h.Securities_Id
                LEFT JOIN fx        ON fx.Currencies_Id_1 = s.Currencies_Id
                WHERE h.Quantity > 0
                GROUP BY s.Securities_Type
            ),
            total AS (SELECT SUM(value_eur) AS grand_total FROM holdings_value)
            SELECT
                hv.securities_type,
                ROUND(hv.value_eur::numeric, 2)                                                           AS value_eur,
                ROUND((hv.value_eur / NULLIF(t.grand_total, 0) * 100)::numeric, 2)                        AS actual_pct,
                COALESCE(at.Target_Pct, 0)                                                                 AS target_pct,
                ROUND((hv.value_eur / NULLIF(t.grand_total, 0) * 100 - COALESCE(at.Target_Pct, 0))::numeric, 2) AS delta_pct,
                ROUND(((COALESCE(at.Target_Pct, 0) - hv.value_eur / NULLIF(t.grand_total, 0) * 100) / 100 * t.grand_total)::numeric, 2) AS rebalance_eur
            FROM holdings_value hv
            CROSS JOIN total t
            LEFT JOIN Allocation_Targets at ON at.Securities_Type = hv.securities_type
            ORDER BY value_eur DESC
        """, conn)
    return _df_to_list(df)


@router.get("/rebalancing-plan")
def get_rebalancing_plan():
    """Per-security rebalancing action plan proportional to current holdings weight."""
    with get_db() as conn:
        df = pd.read_sql("""
            WITH fx AS (
                SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
                FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC
            ),
            prices AS (
                SELECT DISTINCT ON (Securities_Id) Securities_Id, Close
                FROM Historical_Prices ORDER BY Securities_Id, Date DESC
            ),
            holding_vals AS (
                SELECT
                    s.Securities_Id,
                    s.Securities_Name,
                    s.Securities_Type::text                                     AS securities_type,
                    s.Ticker,
                    c.Currencies_ShortName                                      AS currency,
                    COALESCE(p.Close, 0)                                        AS current_price,
                    COALESCE(fx.FX_Rate, 1)                                     AS fx_rate,
                    SUM(h.Quantity)                                             AS current_qty,
                    ROUND((SUM(h.Quantity) * COALESCE(p.Close,0) * COALESCE(fx.FX_Rate,1))::numeric, 2) AS value_eur
                FROM Holdings h
                JOIN Securities s  ON s.Securities_Id  = h.Securities_Id
                JOIN Currencies c  ON c.Currencies_Id  = s.Currencies_Id
                LEFT JOIN prices p ON p.Securities_Id  = h.Securities_Id
                LEFT JOIN fx       ON fx.Currencies_Id_1 = s.Currencies_Id
                WHERE h.Quantity > 0
                GROUP BY s.Securities_Id, s.Securities_Name, s.Securities_Type,
                         s.Ticker, c.Currencies_ShortName, p.Close, fx.FX_Rate
            ),
            grand AS (SELECT SUM(value_eur) AS total FROM holding_vals),
            type_vals AS (
                SELECT securities_type, SUM(value_eur) AS type_eur
                FROM holding_vals GROUP BY securities_type
            )
            SELECT
                hv.Securities_Id                                                AS securities_id,
                hv.Securities_Name                                              AS security,
                hv.securities_type                                              AS type,
                hv.Ticker                                                       AS ticker,
                hv.currency,
                hv.current_qty                                                  AS qty,
                hv.current_price                                                AS price,
                hv.value_eur,
                ROUND((hv.value_eur / NULLIF(g.total,0)*100)::numeric, 2)      AS weight_pct,
                ROUND((tv.type_eur  / NULLIF(g.total,0)*100)::numeric, 2)      AS type_actual_pct,
                COALESCE(at.Target_Pct, 0)                                     AS type_target_pct,
                ROUND((COALESCE(at.Target_Pct,0) - tv.type_eur/NULLIF(g.total,0)*100)::numeric, 2) AS type_delta_pct,
                ROUND((hv.value_eur / NULLIF(tv.type_eur,0)
                       * ((COALESCE(at.Target_Pct,0) - tv.type_eur/NULLIF(g.total,0)*100) / 100 * g.total))::numeric, 2) AS suggested_delta_eur,
                g.total                                                        AS portfolio_total_eur
            FROM holding_vals hv
            CROSS JOIN grand g
            JOIN type_vals tv         ON tv.securities_type = hv.securities_type
            LEFT JOIN Allocation_Targets at ON at.Securities_Type = hv.securities_type
            ORDER BY ABS(COALESCE(at.Target_Pct,0) - tv.type_eur/NULLIF(g.total,0)*100) DESC,
                     ABS(hv.value_eur / NULLIF(tv.type_eur,0)
                         * ((COALESCE(at.Target_Pct,0) - tv.type_eur/NULLIF(g.total,0)*100) / 100 * g.total)) DESC
        """, conn)
    return _df_to_list(df)


@router.get("/net-worth-report")
def get_net_worth_report(
    start_date: str = Query("2020-01-01"),
    end_date: str = Query("2099-12-31"),
    grouping: str = Query("month"),
):
    """Historical net worth — ported directly from database/queries.py get_net_worth_report_data."""
    trunc_map = {"month": "month", "quarter": "quarter", "year": "year"}
    intv_map  = {"month": "1 month", "quarter": "3 months", "year": "1 year"}
    trunc_unit  = trunc_map.get(grouping, "month")
    pg_interval = intv_map.get(grouping, "1 month")

    query = f"""
    WITH
    period_dates AS (
        SELECT (gs - INTERVAL '1 day')::date AS period_end
        FROM generate_series(
            date_trunc('{trunc_unit}', '{start_date}'::date) + '{pg_interval}'::interval,
            date_trunc('{trunc_unit}', CURRENT_DATE),
            '{pg_interval}'::interval
        ) gs
        UNION
        SELECT CURRENT_DATE::date
        ORDER BY 1
    ),
    daily_fx AS (
        SELECT p.period_end, cur.Currencies_Id,
            (SELECT FX_Rate FROM Historical_FX
             WHERE Currencies_Id_1 = cur.Currencies_Id AND Date <= p.period_end
             ORDER BY Date DESC LIMIT 1) AS fx_rate
        FROM period_dates p
        CROSS JOIN Currencies cur
        WHERE cur.Currencies_ShortName != 'EUR'
    ),
    cash_like AS (
        SELECT
            p.period_end,
            a.Accounts_Type,
            CASE
                WHEN a.Accounts_Type IN ('Real Estate', 'Vehicle', 'Asset')
                THEN GREATEST(0, a.Accounts_Balance - COALESCE((
                    SELECT SUM(Total_Amount) FROM Transactions
                    WHERE Accounts_Id = a.Accounts_Id AND Date > p.period_end
                ), 0))
                ELSE (a.Accounts_Balance - COALESCE((
                    SELECT SUM(Total_Amount) FROM Transactions
                    WHERE Accounts_Id = a.Accounts_Id AND Date > p.period_end
                ), 0))
            END * COALESCE(
                (SELECT fx_rate FROM daily_fx
                 WHERE period_end = p.period_end AND Currencies_Id = a.Currencies_Id), 1
            ) AS balance_eur
        FROM period_dates p
        CROSS JOIN Accounts a
        WHERE a.Accounts_Type NOT IN ('Brokerage','Margin','Pension','Other Investment')
    ),
    investment_universe AS (
        SELECT DISTINCT Securities_Id, Accounts_Id
        FROM Investments
        WHERE Action IN ('Buy','Reinvest','ShrIn','Sell','ShrOut')
    ),
    inv_accounts AS (
        SELECT DISTINCT Accounts_Id FROM investment_universe
    ),
    investment_holdings AS (
        SELECT
            p.period_end,
            a.Accounts_Type,
            SUM(
                GREATEST(COALESCE((
                    SELECT SUM(CASE
                        WHEN Action IN ('Buy','Reinvest','ShrIn') THEN  Quantity
                        WHEN Action IN ('Sell','ShrOut')          THEN -Quantity
                        ELSE 0 END)
                    FROM Investments i2
                    WHERE i2.Securities_Id = i.Securities_Id
                      AND i2.Accounts_Id   = i.Accounts_Id
                      AND i2.Date          <= p.period_end
                ), 0), 0) *
                COALESCE((
                    SELECT Close FROM Historical_Prices
                    WHERE Securities_Id = i.Securities_Id AND Date <= p.period_end
                    ORDER BY Date DESC LIMIT 1
                ), 0) *
                COALESCE(
                    (SELECT fx_rate FROM daily_fx
                     WHERE period_end = p.period_end AND Currencies_Id = s.Currencies_Id), 1
                )
            ) AS balance_eur
        FROM period_dates p
        CROSS JOIN investment_universe i
        JOIN Accounts   a ON i.Accounts_Id   = a.Accounts_Id
        JOIN Securities s ON i.Securities_Id = s.Securities_Id
        WHERE a.Accounts_Type IN ('Brokerage','Margin','Pension','Other Investment')
        GROUP BY p.period_end, a.Accounts_Type
    ),
    pension_like AS (
        SELECT
            p.period_end,
            a.Accounts_Type,
            GREATEST(0, a.Accounts_Balance - COALESCE((
                SELECT SUM(CASE
                    WHEN Action IN ('CashIn','IntInc') THEN  Total_Amount_AccCur
                    WHEN Action IN ('CashOut')         THEN -Total_Amount_AccCur
                    ELSE 0 END)
                FROM Investments
                WHERE Accounts_Id = a.Accounts_Id AND Date > p.period_end
            ), 0)) * COALESCE(
                (SELECT fx_rate FROM daily_fx
                 WHERE period_end = p.period_end AND Currencies_Id = a.Currencies_Id), 1
            ) AS balance_eur
        FROM period_dates p
        CROSS JOIN Accounts a
        WHERE a.Accounts_Type IN ('Pension','Other Investment')
          AND a.Accounts_Id NOT IN (SELECT Accounts_Id FROM inv_accounts)
    ),
    combined AS (
        SELECT period_end, Accounts_Type, balance_eur FROM cash_like
        UNION ALL
        SELECT period_end, Accounts_Type, balance_eur FROM investment_holdings
        UNION ALL
        SELECT period_end, Accounts_Type, balance_eur FROM pension_like
        UNION ALL
        SELECT period_end, Accounts_Type, balance_eur FROM other_inv_like
    )
    SELECT
        period_end AS period,
        SUM(CASE WHEN Accounts_Type IN ('Cash','Checking','Savings','Other')
            THEN balance_eur ELSE 0 END) AS cash,
        SUM(CASE WHEN Accounts_Type IN ('Brokerage','Margin','Other Investment')
            THEN balance_eur ELSE 0 END) AS investments,
        SUM(CASE WHEN Accounts_Type = 'Pension'
            THEN balance_eur ELSE 0 END) AS pension,
        SUM(CASE WHEN Accounts_Type IN ('Real Estate','Vehicle','Asset')
            THEN balance_eur ELSE 0 END) AS assets,
        SUM(CASE WHEN Accounts_Type IN ('Credit Card','Loan','Liability')
            THEN balance_eur ELSE 0 END) AS liabilities,
        SUM(CASE
            WHEN Accounts_Type IN ('Credit Card','Loan','Liability') THEN  balance_eur
            ELSE balance_eur END
        ) AS net_worth
    FROM combined
    GROUP BY period_end
    ORDER BY period_end
    """

    with get_db() as conn:
        df = pd.read_sql(query, conn)

    if not df.empty:
        df["net_worth"] = (
            df["cash"] + df["investments"] + df["pension"] +
            df["assets"] + df["liabilities"]
        )

    return _df_to_list(df)


@router.get("/pnl")
def get_pnl(
    start_date: str = Query("1900-01-01"),
    end_date: Optional[str] = Query(None),
):
    """
    Investment P&L per security: DTD/WTD/MTD/QTD/YTD/all-time windows.
    Ported verbatim from database/queries.py:get_pnl_report_data().
    """
    from datetime import date as _date
    if end_date is None:
        end_date = _date.today().isoformat()

    query = f"""
    WITH DateRange AS (
        SELECT '{start_date}'::date AS start_date, '{end_date}'::date AS end_date
    ),
        periods AS (
            SELECT
                (date_trunc('day', end_date) - INTERVAL '1 day')::date as dtd_start,
                (date_trunc('week', end_date) - INTERVAL '1 day')::date as wtd_start,
                (date_trunc('month', end_date) - INTERVAL '1 day')::date as mtd_start,
                (date_trunc('quarter', end_date) - INTERVAL '1 day')::date as qtd_start,
                (date_trunc('year', end_date) - INTERVAL '1 day')::date as ytd_start,
                start_date as all_time_start,
                end_date as today
            FROM DateRange
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
                COALESCE(inv.qty_today, 0) as qty_today,
                COALESCE(inv.qty_dtd,   0) as qty_dtd,
                COALESCE(inv.qty_wtd,   0) as qty_wtd,
                COALESCE(inv.qty_mtd,   0) as qty_mtd,
                COALESCE(inv.qty_qtd,   0) as qty_qtd,
                COALESCE(inv.qty_ytd,   0) as qty_ytd
            FROM periods p
            CROSS JOIN historical_entities he
            LEFT JOIN LATERAL (
                SELECT
                    SUM(CASE WHEN Action IN ('Buy','Reinvest','ShrIn') THEN Quantity
                             WHEN Action IN ('Sell','ShrOut') THEN -Quantity ELSE 0 END)
                        FILTER (WHERE Date <= p.today)     AS qty_today,
                    SUM(CASE WHEN Action IN ('Buy','Reinvest','ShrIn') THEN Quantity
                             WHEN Action IN ('Sell','ShrOut') THEN -Quantity ELSE 0 END)
                        FILTER (WHERE Date <= p.dtd_start) AS qty_dtd,
                    SUM(CASE WHEN Action IN ('Buy','Reinvest','ShrIn') THEN Quantity
                             WHEN Action IN ('Sell','ShrOut') THEN -Quantity ELSE 0 END)
                        FILTER (WHERE Date <= p.wtd_start) AS qty_wtd,
                    SUM(CASE WHEN Action IN ('Buy','Reinvest','ShrIn') THEN Quantity
                             WHEN Action IN ('Sell','ShrOut') THEN -Quantity ELSE 0 END)
                        FILTER (WHERE Date <= p.mtd_start) AS qty_mtd,
                    SUM(CASE WHEN Action IN ('Buy','Reinvest','ShrIn') THEN Quantity
                             WHEN Action IN ('Sell','ShrOut') THEN -Quantity ELSE 0 END)
                        FILTER (WHERE Date <= p.qtd_start) AS qty_qtd,
                    SUM(CASE WHEN Action IN ('Buy','Reinvest','ShrIn') THEN Quantity
                             WHEN Action IN ('Sell','ShrOut') THEN -Quantity ELSE 0 END)
                        FILTER (WHERE Date <= p.ytd_start) AS qty_ytd
                FROM Investments
                WHERE Accounts_Id = he.Accounts_Id AND Securities_Id = he.Securities_Id
            ) inv ON true
        ),
        prices_fx AS (
            SELECT
                hh.*,
                hp_today.Close  AS price_today,
                hp_dtd.Close    AS price_dtd,
                hp_wtd.Close    AS price_wtd,
                hp_mtd.Close    AS price_mtd,
                hp_qtd.Close    AS price_qtd,
                hp_ytd.Close    AS price_ytd,
                fx_today.FX_Rate AS fx_today,
                fx_dtd.FX_Rate   AS fx_dtd,
                fx_wtd.FX_Rate   AS fx_wtd,
                fx_mtd.FX_Rate   AS fx_mtd,
                fx_qtd.FX_Rate   AS fx_qtd,
                fx_ytd.FX_Rate   AS fx_ytd,
                s.Securities_Name, a.Accounts_Name, s.Currencies_Id AS sec_curr_id
            FROM historical_holdings hh
            JOIN Securities s ON hh.Securities_Id = s.Securities_Id
            JOIN Accounts   a ON hh.Accounts_Id   = a.Accounts_Id
            LEFT JOIN LATERAL (
                SELECT
                    MAX(Date) FILTER (WHERE Date <= hh.today)      AS d_today,
                    MAX(Date) FILTER (WHERE Date <= hh.dtd_start)  AS d_dtd,
                    MAX(Date) FILTER (WHERE Date <= hh.wtd_start)  AS d_wtd,
                    MAX(Date) FILTER (WHERE Date <= hh.mtd_start)  AS d_mtd,
                    MAX(Date) FILTER (WHERE Date <= hh.qtd_start)  AS d_qtd,
                    MAX(Date) FILTER (WHERE Date <= hh.ytd_start)  AS d_ytd
                FROM Historical_Prices WHERE Securities_Id = hh.Securities_Id
            ) pd ON true
            LEFT JOIN Historical_Prices hp_today ON hp_today.Securities_Id = hh.Securities_Id AND hp_today.Date = pd.d_today
            LEFT JOIN Historical_Prices hp_dtd   ON hp_dtd.Securities_Id   = hh.Securities_Id AND hp_dtd.Date   = pd.d_dtd
            LEFT JOIN Historical_Prices hp_wtd   ON hp_wtd.Securities_Id   = hh.Securities_Id AND hp_wtd.Date   = pd.d_wtd
            LEFT JOIN Historical_Prices hp_mtd   ON hp_mtd.Securities_Id   = hh.Securities_Id AND hp_mtd.Date   = pd.d_mtd
            LEFT JOIN Historical_Prices hp_qtd   ON hp_qtd.Securities_Id   = hh.Securities_Id AND hp_qtd.Date   = pd.d_qtd
            LEFT JOIN Historical_Prices hp_ytd   ON hp_ytd.Securities_Id   = hh.Securities_Id AND hp_ytd.Date   = pd.d_ytd
            LEFT JOIN LATERAL (
                SELECT
                    MAX(Date) FILTER (WHERE Date <= hh.today)      AS d_today,
                    MAX(Date) FILTER (WHERE Date <= hh.dtd_start)  AS d_dtd,
                    MAX(Date) FILTER (WHERE Date <= hh.wtd_start)  AS d_wtd,
                    MAX(Date) FILTER (WHERE Date <= hh.mtd_start)  AS d_mtd,
                    MAX(Date) FILTER (WHERE Date <= hh.qtd_start)  AS d_qtd,
                    MAX(Date) FILTER (WHERE Date <= hh.ytd_start)  AS d_ytd
                FROM Historical_FX WHERE Currencies_Id_1 = s.Currencies_Id
            ) fxd ON true
            LEFT JOIN Historical_FX fx_today ON fx_today.Currencies_Id_1 = s.Currencies_Id AND fx_today.Date = fxd.d_today
            LEFT JOIN Historical_FX fx_dtd   ON fx_dtd.Currencies_Id_1   = s.Currencies_Id AND fx_dtd.Date   = fxd.d_dtd
            LEFT JOIN Historical_FX fx_wtd   ON fx_wtd.Currencies_Id_1   = s.Currencies_Id AND fx_wtd.Date   = fxd.d_wtd
            LEFT JOIN Historical_FX fx_mtd   ON fx_mtd.Currencies_Id_1   = s.Currencies_Id AND fx_mtd.Date   = fxd.d_mtd
            LEFT JOIN Historical_FX fx_qtd   ON fx_qtd.Currencies_Id_1   = s.Currencies_Id AND fx_qtd.Date   = fxd.d_qtd
            LEFT JOIN Historical_FX fx_ytd   ON fx_ytd.Currencies_Id_1   = s.Currencies_Id AND fx_ytd.Date   = fxd.d_ytd
        ),
        cash_flows AS (
            SELECT
                i.Accounts_Id, i.Securities_Id,
                SUM(CASE WHEN i.Date > (SELECT dtd_start FROM periods) THEN
                    (CASE WHEN i.Action IN ('Buy', 'MiscExp', 'ShrIn') THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0), i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0))
                          WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'Reinvest', 'RtrnCap', 'ShrOut') THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0), i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0))
                          ELSE 0 END) ELSE 0 END) AS cf_dtd,
                SUM(CASE WHEN i.Date > (SELECT dtd_start FROM periods) THEN
                    (CASE WHEN i.Action IN ('Buy', 'MiscExp', 'ShrIn') THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close + COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                          WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'Reinvest', 'RtrnCap', 'ShrOut') THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close - COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                          ELSE 0 END) ELSE 0 END) AS cf_dtd_eur,
                SUM(CASE WHEN i.Date > (SELECT wtd_start FROM periods) THEN
                    (CASE WHEN i.Action IN ('Buy', 'MiscExp', 'ShrIn') THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0), i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0))
                          WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'Reinvest', 'RtrnCap', 'ShrOut') THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0), i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0))
                          ELSE 0 END) ELSE 0 END) AS cf_wtd,
                SUM(CASE WHEN i.Date > (SELECT wtd_start FROM periods) THEN
                    (CASE WHEN i.Action IN ('Buy', 'MiscExp', 'ShrIn') THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close + COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                          WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'Reinvest', 'RtrnCap', 'ShrOut') THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Price_Per_Share - COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close - COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                          ELSE 0 END) ELSE 0 END) AS cf_wtd_eur,
                SUM(CASE WHEN i.Date > (SELECT mtd_start FROM periods) THEN
                    (CASE WHEN i.Action IN ('Buy', 'MiscExp', 'ShrIn') THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0), i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0))
                          WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'Reinvest', 'RtrnCap', 'ShrOut') THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0), i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0))
                          ELSE 0 END) ELSE 0 END) AS cf_mtd,
                SUM(CASE WHEN i.Date > (SELECT mtd_start FROM periods) THEN
                    (CASE WHEN i.Action IN ('Buy', 'MiscExp', 'ShrIn') THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close + COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                          WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'Reinvest', 'RtrnCap', 'ShrOut') THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close - COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                          ELSE 0 END) ELSE 0 END) AS cf_mtd_eur,
                SUM(CASE WHEN i.Date > (SELECT qtd_start FROM periods) THEN
                    (CASE WHEN i.Action IN ('Buy', 'MiscExp', 'ShrIn') THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0), i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0))
                          WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'Reinvest', 'RtrnCap', 'ShrOut') THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0), i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0))
                          ELSE 0 END) ELSE 0 END) AS cf_qtd,
                SUM(CASE WHEN i.Date > (SELECT qtd_start FROM periods) THEN
                    (CASE WHEN i.Action IN ('Buy', 'MiscExp', 'ShrIn') THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close + COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                          WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'Reinvest', 'RtrnCap', 'ShrOut') THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close - COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                          ELSE 0 END) ELSE 0 END) AS cf_qtd_eur,
                SUM(CASE WHEN i.Date > (SELECT ytd_start FROM periods) THEN
                    (CASE WHEN i.Action IN ('Buy', 'MiscExp', 'ShrIn') THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0), i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0))
                          WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'Reinvest', 'RtrnCap', 'ShrOut') THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0), i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0))
                          ELSE 0 END) ELSE 0 END) AS cf_ytd,
                SUM(CASE WHEN i.Date > (SELECT ytd_start FROM periods) THEN
                    (CASE WHEN i.Action IN ('Buy', 'MiscExp', 'ShrIn') THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close + COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                          WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'Reinvest', 'RtrnCap', 'ShrOut') THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close - COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                          ELSE 0 END) ELSE 0 END) AS cf_ytd_eur,
                SUM(CASE WHEN i.Date > (SELECT ytd_start FROM periods) THEN
                    CASE WHEN i.Action IN ('Buy', 'CashOut', 'MiscExp', 'ShrIn')
                            THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close + COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                         WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'CashIn', 'RtrnCap', 'ShrOut')
                            THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close - COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                         ELSE 0 END
                ELSE 0 END) AS net_invested_ytd_eur,
                SUM(CASE WHEN i.Action IN ('Buy', 'MiscExp', 'Reinvest', 'Exercise', 'ShrIn') THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0), i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0))
                         WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'RtrnCap', 'ShrOut') THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0), i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0))
                         ELSE 0 END) AS cf_all_time,
                SUM(CASE WHEN i.Action IN ('Buy', 'MiscExp', 'Reinvest', 'Exercise', 'ShrIn') THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close + COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                         WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'RtrnCap', 'ShrOut') THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close - COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                         ELSE 0 END) AS cf_all_time_eur,
                SUM(CASE WHEN i.Action IN ('Buy', 'CashOut', 'MiscExp', 'ShrIn')
                            THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close + COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                         WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'CashIn', 'RtrnCap', 'ShrOut')
                            THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close - COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                         ELSE 0 END) AS net_invested_all_time_eur,
                SUM(CASE WHEN i.Action IN ('Buy', 'CashOut', 'MiscExp', 'ShrIn')
                            THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close + COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                         ELSE 0 END) AS gross_invested_all_time_eur
            FROM Investments i
            JOIN Accounts a ON i.Accounts_Id = a.Accounts_Id
            JOIN Securities s ON i.Securities_Id = s.Securities_Id
            LEFT JOIN Historical_FX hfx
                   ON hfx.Currencies_Id_1 = a.Currencies_Id
                  AND hfx.Date = i.Date
            LEFT JOIN Historical_FX hfx_sec
                   ON hfx_sec.Currencies_Id_1 = s.Currencies_Id
                  AND hfx_sec.Date = i.Date
            LEFT JOIN LATERAL (
                SELECT hp.Close
                FROM Historical_Prices hp
                WHERE hp.Securities_Id = i.Securities_Id
                  AND hp.Date <= i.Date
                ORDER BY hp.Date DESC
                LIMIT 1
            ) hist_price ON i.Action = 'Reinvest'
                        AND (i.Price_Per_Share = 0 OR i.Price_Per_Share IS NULL)
                        AND (i.Total_Amount_SecCur = 0 OR i.Total_Amount_SecCur IS NULL)
            GROUP BY i.Accounts_Id, i.Securities_Id
        ),
        dividend_yoc AS (
            SELECT
                i.Securities_Id, i.Accounts_Id,
                SUM(
                    CASE
                        WHEN i.Action = 'Dividend' THEN i.Total_Amount_AccCur
                        WHEN i.Action IN ('Reinvest', 'ShrIn') THEN
                            i.Quantity * COALESCE(
                                NULLIF(i.Price_Per_Share, 0),
                                (SELECT hp.Close FROM Historical_Prices hp
                                 WHERE hp.Securities_Id = i.Securities_Id
                                   AND hp.Date <= i.Date
                                 ORDER BY hp.Date DESC LIMIT 1)
                            )
                        ELSE 0
                    END
                ) AS annual_income
            FROM Investments i
            WHERE i.Action IN ('Dividend', 'Reinvest', 'ShrIn')
              AND i.Date >= CURRENT_DATE - INTERVAL '1 year'
            GROUP BY i.Securities_Id, i.Accounts_Id
        ),
        account_direct_flows AS (
            SELECT
                i.Accounts_Id,
                SUM(CASE WHEN i.Action = 'CashIn'
                         THEN i.Total_Amount_AccCur * COALESCE(hfx.FX_Rate, 1) ELSE 0 END) AS direct_cashin_eur
            FROM Investments i
            JOIN Accounts a ON i.Accounts_Id = a.Accounts_Id
            LEFT JOIN Historical_FX hfx
                   ON hfx.Currencies_Id_1 = a.Currencies_Id
                  AND hfx.Date = i.Date
            WHERE i.Securities_Id IS NULL
            GROUP BY i.Accounts_Id
        ),
        account_linked_flows AS (
            SELECT
                a.Accounts_Id AS inv_acc_id,
                SUM(-t.Total_Amount * COALESCE(fxl.FX_Rate, 1)) AS linked_cashin_eur
            FROM Accounts a
            INNER JOIN Accounts al ON al.Accounts_Id = a.Accounts_Id_Linked
            INNER JOIN Transactions t
                    ON t.Accounts_Id       = al.Accounts_Id
                   AND t.Accounts_Id_Target = a.Accounts_Id
                   AND t.Total_Amount < 0
            LEFT JOIN Historical_FX fxl
                   ON fxl.Currencies_Id_1 = al.Currencies_Id
                  AND fxl.Date = t.Date
            GROUP BY a.Accounts_Id
        )
        SELECT
            pf.Securities_Id AS securities_id,
            pf.Accounts_Name, pf.Securities_Name,
            pf.qty_today,
            pf.price_today,
            c.Currencies_ShortName AS currency,
            (pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)) as current_value_eur,
            ((pf.qty_today * pf.price_today) - (pf.qty_dtd * pf.price_dtd) - COALESCE(cf.cf_dtd, 0)) * COALESCE(pf.fx_today, 1) as pnl_dtd_market_eur,
            (pf.qty_dtd * pf.price_dtd) * (COALESCE(pf.fx_today, 1) - COALESCE(pf.fx_dtd, 1)) as pnl_dtd_fx_eur,
            ((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)) - (pf.qty_dtd * pf.price_dtd * COALESCE(pf.fx_dtd, 1)) - COALESCE(cf.cf_dtd_eur, 0)) as pnl_dtd_eur,
            CASE WHEN (pf.qty_dtd * pf.price_dtd * COALESCE(pf.fx_dtd, 1)) = 0 THEN 0
                 ELSE (((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)) - (pf.qty_dtd * pf.price_dtd * COALESCE(pf.fx_dtd, 1)) - COALESCE(cf.cf_dtd_eur, 0)) / (pf.qty_dtd * pf.price_dtd * COALESCE(pf.fx_dtd, 1))) * 100
            END as pnl_dtd_percent,
            ((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)) - (pf.qty_wtd * pf.price_wtd * COALESCE(pf.fx_wtd, 1)) - COALESCE(cf.cf_wtd_eur, 0)) as pnl_wtd_eur,
            ((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)) - (pf.qty_mtd * pf.price_mtd * COALESCE(pf.fx_mtd, 1)) - COALESCE(cf.cf_mtd_eur, 0)) as pnl_mtd_eur,
            ((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)) - (pf.qty_qtd * pf.price_qtd * COALESCE(pf.fx_qtd, 1)) - COALESCE(cf.cf_qtd_eur, 0)) as pnl_qtd_eur,
            (CASE WHEN pf.qty_today = 0 THEN COALESCE((pf.qty_today * pf.price_today), 0) - COALESCE((pf.qty_ytd * pf.price_ytd),0)
                  ELSE COALESCE((pf.qty_today * pf.price_today), 0) - COALESCE((pf.qty_ytd * pf.price_ytd),0)
             END - COALESCE(cf.cf_ytd_eur, 0)) * COALESCE(pf.fx_today, 1) as pnl_ytd_market_eur,
            CASE WHEN pf.qty_today = 0 THEN COALESCE((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)), 0) - COALESCE((pf.qty_ytd * pf.price_ytd * COALESCE(pf.fx_ytd, 1)),0)
                 ELSE COALESCE((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)), 0) - COALESCE((pf.qty_ytd * pf.price_ytd * COALESCE(pf.fx_ytd, 1)),0)
            END - COALESCE(cf.cf_ytd_eur, 0)
            -
            (CASE WHEN pf.qty_today = 0 THEN COALESCE((pf.qty_today * pf.price_today), 0) - COALESCE((pf.qty_ytd * pf.price_ytd),0)
                  ELSE COALESCE((pf.qty_today * pf.price_today), 0) - COALESCE((pf.qty_ytd * pf.price_ytd),0)
             END - COALESCE(cf.cf_ytd_eur, 0)) * COALESCE(pf.fx_today, 1)
            as pnl_ytd_fx_eur,
            CASE WHEN pf.qty_today = 0 THEN COALESCE((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)), 0) - COALESCE((pf.qty_ytd * pf.price_ytd * COALESCE(pf.fx_ytd, 1)),0)
                 ELSE COALESCE((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)), 0) - COALESCE((pf.qty_ytd * pf.price_ytd * COALESCE(pf.fx_ytd, 1)),0)
            END - COALESCE(cf.cf_ytd_eur, 0) as pnl_ytd_eur,
            CASE WHEN COALESCE((pf.qty_ytd * pf.price_ytd * COALESCE(pf.fx_ytd, 1)), 0) = 0 THEN 0
                 ELSE (((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)) - COALESCE((pf.qty_ytd * pf.price_ytd * COALESCE(pf.fx_ytd, 1)),0) - COALESCE(cf.cf_ytd_eur, 0)) / COALESCE((pf.qty_ytd * pf.price_ytd * COALESCE(pf.fx_ytd, 1)), 1)) * 100
            END as pnl_ytd_percent,
            CASE WHEN pf.qty_today <> 0 AND pf.qty_ytd = 0 THEN COALESCE((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)), 0) - COALESCE(cf.cf_ytd_eur, 0)
                 WHEN pf.qty_today <> 0 AND pf.qty_ytd <> 0 AND pf.qty_today >= pf.qty_ytd AND COALESCE(cf.net_invested_ytd_eur, 0) >= 0 THEN COALESCE((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)), 0) - COALESCE((pf.qty_ytd * pf.price_ytd * COALESCE(pf.fx_ytd, 1)), 0) - COALESCE(cf.net_invested_ytd_eur, 0)
                 WHEN pf.qty_today <> 0 AND pf.qty_ytd <> 0 AND pf.qty_today >= pf.qty_ytd AND COALESCE(cf.net_invested_ytd_eur, 0) < 0 THEN COALESCE((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)), 0) - COALESCE((pf.qty_ytd * pf.price_ytd * COALESCE(pf.fx_ytd, 1)), 0)
                 ELSE 0
            END AS unrealized_pnl_ytd_eur,
            CASE WHEN pf.qty_today = 0 THEN COALESCE((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)), 0) - COALESCE((pf.qty_ytd * pf.price_ytd * COALESCE(pf.fx_ytd, 1)),0)
                 ELSE COALESCE((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)), 0) - COALESCE((pf.qty_ytd * pf.price_ytd * COALESCE(pf.fx_ytd, 1)),0)
            END - COALESCE(cf.cf_ytd_eur, 0)
            -
            CASE WHEN pf.qty_today <> 0 AND pf.qty_ytd = 0 THEN COALESCE((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)), 0) - COALESCE(cf.cf_ytd_eur, 0)
                 WHEN pf.qty_today <> 0 AND pf.qty_ytd <> 0 AND pf.qty_today >= pf.qty_ytd AND COALESCE(cf.net_invested_ytd_eur, 0) >= 0 THEN COALESCE((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)), 0) - COALESCE((pf.qty_ytd * pf.price_ytd * COALESCE(pf.fx_ytd, 1)), 0) - COALESCE(cf.net_invested_ytd_eur, 0)
                 WHEN pf.qty_today <> 0 AND pf.qty_ytd <> 0 AND pf.qty_today >= pf.qty_ytd AND COALESCE(cf.net_invested_ytd_eur, 0) < 0 THEN COALESCE((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)), 0) - COALESCE((pf.qty_ytd * pf.price_ytd * COALESCE(pf.fx_ytd, 1)), 0)
                 ELSE 0
            END AS realized_pnl_ytd_eur,
            ((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)) - COALESCE(cf.cf_all_time_eur, 0)) as pnl_all_time_eur,
            COALESCE((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)),0) - COALESCE(cf.net_invested_all_time_eur, 0) as pnl_net_all_time_eur,
            CASE WHEN COALESCE(cf.gross_invested_all_time_eur, 0) = 0 THEN 0
                 ELSE (COALESCE((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)),0) - COALESCE(cf.net_invested_all_time_eur, 0))
                      / cf.gross_invested_all_time_eur * 100
            END as pnl_net_all_time_percent,
            COALESCE(cf.gross_invested_all_time_eur, 0) as gross_invested_all_time_eur,
            COALESCE(adf.direct_cashin_eur, 0) AS direct_cashin_eur,
            COALESCE(alf.linked_cashin_eur, 0) AS linked_cashin_eur,
            COALESCE(h.Quantity, 0) * pf.price_today * COALESCE(pf.fx_today, 1) - COALESCE(h.Quantity, 0) * COALESCE(h.Fifo_Avg_Cost_EUR, h.Fifo_Avg_Price * COALESCE(pf.fx_today, 1), 0) AS unrealized_pnl_eur,
            COALESCE((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)), 0) - COALESCE(cf.net_invested_all_time_eur, 0)
            - COALESCE(COALESCE(h.Quantity, 0) * pf.price_today * COALESCE(pf.fx_today, 1) - COALESCE(h.Quantity, 0) * COALESCE(h.Fifo_Avg_Cost_EUR, h.Fifo_Avg_Price * COALESCE(pf.fx_today, 1), 0), 0) AS realized_pnl_eur,
            ROUND(dy.annual_income / NULLIF(h.Quantity * h.Fifo_Avg_Price, 0) * 100, 8) AS dividend_yoc_pct
        FROM prices_fx pf
        LEFT JOIN cash_flows cf ON pf.Accounts_Id = cf.Accounts_Id AND pf.Securities_Id = cf.Securities_Id
        LEFT JOIN Holdings h ON h.Accounts_Id = pf.Accounts_Id AND h.Securities_Id = pf.Securities_Id
        LEFT JOIN dividend_yoc dy ON dy.Accounts_Id = pf.Accounts_Id AND dy.Securities_Id = pf.Securities_Id
        LEFT JOIN account_direct_flows adf ON adf.Accounts_Id = pf.Accounts_Id
        LEFT JOIN account_linked_flows alf ON alf.inv_acc_id = pf.Accounts_Id
        LEFT JOIN Currencies c ON c.Currencies_Id = pf.sec_curr_id
        WHERE (pf.qty_today != 0 OR cf.cf_all_time IS NOT NULL)
        ORDER BY pf.Accounts_Name, pf.Securities_Name
    """
    with get_db() as conn:
        df = pd.read_sql(query, conn)
    return _df_to_list(df)


@router.get("/pnl-summary")
def get_pnl_summary(
    start_date: str = Query("2024-01-01"),
    end_date: str = Query("2099-12-31"),
):
    """Monthly P&L summary: realized gains + dividends per month."""
    query = """
    WITH realized AS (
        SELECT date_trunc('month', i.Date)::date AS month,
               i.Quantity * (i.Price_Per_Share - COALESCE(h.simple_avg_price, 0))
               * COALESCE(
                   (SELECT FX_Rate FROM Historical_FX
                    WHERE Currencies_Id_1 = c2.Currencies_Id AND Date <= i.Date
                    ORDER BY Date DESC LIMIT 1), 1
               ) AS value
        FROM Investments i
        JOIN Accounts a ON a.Accounts_Id = i.Accounts_Id
        JOIN Currencies c2 ON c2.Currencies_Id = a.Currencies_Id
        LEFT JOIN Holdings h ON h.Securities_Id = i.Securities_Id AND h.Accounts_Id = i.Accounts_Id
        WHERE i.Action IN ('Sell','ShrOut')
          AND i.Date BETWEEN %(start_date)s AND %(end_date)s
    ),
    divs AS (
        SELECT date_trunc('month', i.Date)::date AS month,
               ABS(i.Total_Amount_AccCur)
               * COALESCE(
                   (SELECT FX_Rate FROM Historical_FX
                    WHERE Currencies_Id_1 = c2.Currencies_Id AND Date <= i.Date
                    ORDER BY Date DESC LIMIT 1), 1
               ) AS value
        FROM Investments i
        JOIN Accounts a ON a.Accounts_Id = i.Accounts_Id
        JOIN Currencies c2 ON c2.Currencies_Id = a.Currencies_Id
        WHERE i.Action IN ('Dividend','Reinvest','IntInc','MiscInc','RtrnCap')
          AND i.Date BETWEEN %(start_date)s AND %(end_date)s
    )
    SELECT
        COALESCE(r.month, d.month) AS month,
        COALESCE(SUM(r.value), 0) AS realized_gain,
        COALESCE(SUM(d.value), 0) AS dividend_income
    FROM (SELECT month, SUM(value) AS value FROM realized GROUP BY month) r
    FULL OUTER JOIN (SELECT month, SUM(value) AS value FROM divs GROUP BY month) d
        ON r.month = d.month
    GROUP BY 1
    ORDER BY 1
    """
    with get_db() as conn:
        df = pd.read_sql(query, conn, params={"start_date": start_date, "end_date": end_date})
    return _df_to_list(df)


@router.get("/income-expense-detail")
def get_income_expense_detail(
    start_date: str = Query("2024-01-01"),
    end_date: str = Query("2099-12-31"),
    grouping: str = Query("month"),
):
    """Income/expense by period and top-level category with hierarchy."""
    trunc = {"month": "month", "quarter": "quarter", "year": "year"}.get(grouping, "month")
    query = f"""
    WITH RECURSIVE CategoryHierarchy AS (
        SELECT Categories_Id, Categories_Name::TEXT AS Full_Path, Categories_Name::TEXT AS top_name,
               Categories_Type::TEXT AS Categories_Type, Categories_Id_Parent
        FROM Categories WHERE Categories_Id_Parent IS NULL
        UNION ALL
        SELECT c.Categories_Id, ch.Full_Path || ' : ' || c.Categories_Name, ch.top_name,
               c.Categories_Type::TEXT, c.Categories_Id_Parent
        FROM Categories c JOIN CategoryHierarchy ch ON c.Categories_Id_Parent = ch.Categories_Id
    )
    SELECT
        date_trunc('{trunc}', t.Date)::date AS period,
        COALESCE(cat.top_name, 'Uncategorized') AS top_category,
        COALESCE(cat.Full_Path, 'Uncategorized') AS category,
        COALESCE(cat.Categories_Type, 'Uncategorized') AS cat_type,
        SUM(ABS(COALESCE(s.Amount, t.Total_Amount))) AS total
    FROM Transactions t
    LEFT JOIN Splits s ON s.Transactions_Id = t.Transactions_Id
    LEFT JOIN CategoryHierarchy cat ON s.Categories_Id = cat.Categories_Id
    JOIN Accounts a ON t.Accounts_Id = a.Accounts_Id
    WHERE t.Date BETWEEN %(start_date)s AND %(end_date)s
      AND a.Accounts_Type IN ('Cash','Checking','Savings','Credit Card','Loan','Other')
      AND t.accounts_id_target IS NULL
    GROUP BY 1, 2, 3, 4
    ORDER BY period ASC, total DESC
    """
    with get_db() as conn:
        df = pd.read_sql(query, conn, params={"start_date": start_date, "end_date": end_date})
    return _df_to_list(df)


@router.get("/dividends")
def get_dividends(
    start_date: str = Query("2024-01-01"),
    end_date: str = Query("2099-12-31"),
):
    """Dividend income received (from Investments with action=Dividend/DivX)."""
    query = """
    SELECT
        i.Date::text AS date,
        s.Securities_Name AS security,
        s.Ticker AS ticker,
        a.Accounts_Name AS account,
        i.Total_Amount_AccCur AS amount,
        c2.Currencies_ShortName AS currency,
        COALESCE(
            (SELECT FX_Rate FROM Historical_FX WHERE Currencies_Id_1=c2.Currencies_Id ORDER BY Date DESC LIMIT 1), 1
        ) AS fx_rate,
        i.Total_Amount_AccCur * COALESCE(
            (SELECT FX_Rate FROM Historical_FX WHERE Currencies_Id_1=c2.Currencies_Id ORDER BY Date DESC LIMIT 1), 1
        ) AS amount_eur
    FROM Investments i
    JOIN Securities s ON i.Securities_Id = s.Securities_Id
    JOIN Accounts a ON i.Accounts_Id = a.Accounts_Id
    JOIN Currencies c2 ON a.Currencies_Id = c2.Currencies_Id
    WHERE i.Action IN ('Dividend','IntInc','Reinvest','MiscInc','RtrnCap')
      AND i.Total_Amount_AccCur > 0
      AND i.Date BETWEEN %(start_date)s AND %(end_date)s
    ORDER BY i.Date DESC
    """
    with get_db() as conn:
        df = pd.read_sql(query, conn, params={"start_date": start_date, "end_date": end_date})
    return _df_to_list(df)


# ── Dividend & Interest Income Tracker ─────────────────────────────────────────
@router.get("/dividends-tracker")
def get_dividends_tracker(
    period: str = Query("YTD"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
):
    """Monthly dividend/interest income with FIFO cost basis & annualised YOC per security."""
    from datetime import date as _date, timedelta as _td

    today = _date.today()
    period_days = {"1 Year": 365, "2 Years": 730, "3 Years": 1095, "5 Years": 1825}

    if period == "Custom":
        sd = _date.fromisoformat(start_date) if start_date else today - _td(days=365)
        ed = _date.fromisoformat(end_date) if end_date else today
    elif period == "All Time":
        sd, ed = _date(1900, 1, 1), today
    elif period == "YTD":
        sd, ed = _date(today.year, 1, 1), today
    elif period == "Previous Year":
        sd, ed = _date(today.year - 1, 1, 1), _date(today.year - 1, 12, 31)
    elif period in period_days:
        sd, ed = today - _td(days=period_days[period]), today
    else:
        sd, ed = _date(today.year, 1, 1), today

    period_label = {
        "All Time": "All Time", "Custom": "Custom",
        "YTD": f"YTD {today.year}",
        "Previous Year": str(today.year - 1),
    }.get(period, f"Last {period}")

    query = """
        WITH fx AS (
            SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
            FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC
        ),
        income AS (
            SELECT
                i.Date,
                DATE_TRUNC('month', i.Date)::date AS month,
                i.Securities_Id,
                s.Securities_Name,
                s.Securities_Type,
                a.Accounts_Name,
                a.Currencies_Id,
                SUM(
                    CASE WHEN i.Action = 'MiscExp'
                    THEN -i.Total_Amount_AccCur * COALESCE(fx.FX_Rate, 1)
                    ELSE  i.Total_Amount_AccCur * COALESCE(fx.FX_Rate, 1)
                    END
                ) AS income_eur,
                i.Action
            FROM Investments i
            JOIN Securities s ON i.Securities_Id = s.Securities_Id
            JOIN Accounts   a ON i.Accounts_Id   = a.Accounts_Id
            LEFT JOIN fx      ON fx.Currencies_Id_1 = a.Currencies_Id
            WHERE i.Action IN ('Dividend','IntInc','Reinvest','RtrnCap')
              AND i.Date BETWEEN %(start_date)s AND %(end_date)s
            GROUP BY i.Date, i.Securities_Id, s.Securities_Name, s.Securities_Type,
                     a.Accounts_Name, a.Currencies_Id, i.Action
        )
        SELECT
            i.Date AS date,
            i.month,
            i.Securities_Id AS securities_id,
            i.Securities_Name AS securities_name,
            i.Securities_Type AS securities_type,
            i.Accounts_Name AS accounts_name,
            i.Action AS action,
            ROUND(i.income_eur::numeric, 2) AS income_eur,
            ROUND(COALESCE(fc.cost_eur, 0)::numeric, 2) AS cost_basis_eur,
            fc.position_start_date
        FROM income i
        CROSS JOIN LATERAL (
            WITH buys AS (
                SELECT
                    b.Date AS buy_date,
                    b.Quantity AS buy_qty,
                    ABS(b.Total_Amount_AccCur) * COALESCE(fx2.FX_Rate, 1) / NULLIF(b.Quantity, 0) AS cost_per_unit_eur,
                    SUM(b.Quantity) OVER (ORDER BY b.Date, b.Investments_Id) AS running_buy_qty
                FROM Investments b
                JOIN  Accounts a2 ON b.Accounts_Id      = a2.Accounts_Id
                LEFT JOIN fx fx2  ON fx2.Currencies_Id_1 = a2.Currencies_Id
                WHERE b.Securities_Id = i.Securities_Id
                  AND (
                      b.Action IN ('Buy','ShrIn','Vest')
                      OR (b.Action = 'Reinvest' AND i.Securities_Type NOT IN ('CD','Bond'))
                  )
                  AND b.Date    <= i.Date
                  AND b.Quantity > 0
            ),
            sells AS (
                SELECT COALESCE(SUM(s.Quantity), 0) AS total_sell_qty
                FROM Investments s
                WHERE s.Securities_Id = i.Securities_Id
                  AND s.Action IN ('Sell','ShrOut','Expire')
                  AND s.Date     < i.Date
            ),
            fifo AS (
                SELECT
                    b.buy_date,
                    GREATEST(0.0, LEAST(b.buy_qty, b.running_buy_qty - s.total_sell_qty)) AS remaining_qty,
                    GREATEST(0.0, LEAST(b.buy_qty, b.running_buy_qty - s.total_sell_qty)) * b.cost_per_unit_eur AS lot_cost
                FROM buys b CROSS JOIN sells s
            )
            SELECT
                COALESCE(SUM(lot_cost), 0) AS cost_eur,
                MIN(CASE WHEN remaining_qty > 0 THEN buy_date END) AS position_start_date
            FROM fifo
        ) AS fc
        ORDER BY i.Date DESC, i.income_eur DESC
    """
    with get_db() as conn:
        df = pd.read_sql(query, conn, params={"start_date": str(sd), "end_date": str(ed)})

    if df.empty:
        return {"period_label": period_label, "monthly": [], "by_security": [],
                "by_type": [], "detail": [], "summary": {}}

    df["month"] = pd.to_datetime(df["month"])
    df["date"] = pd.to_datetime(df["date"])
    df["position_start_date"] = pd.to_datetime(df["position_start_date"])

    df = df[df["cost_basis_eur"] > 0].copy()
    if df.empty:
        return {"period_label": period_label, "monthly": [], "by_security": [],
                "by_type": [], "detail": [], "summary": {}}

    period_months = {"1 Year": 12, "2 Years": 24, "3 Years": 36, "5 Years": 60, "Previous Year": 12}
    if period in period_months:
        max_span_days = (period_months[period] - 1) * 365.25 / 12
    elif period in ("Custom", "YTD"):
        custom_months = (ed - sd).days / (365.25 / 12)
        max_span_days = max(custom_months - 1, 0) * 365.25 / 12
    else:
        max_span_days = None

    if max_span_days is not None:
        last_per_sec = df.groupby("securities_name")["date"].transform("max")
        df = df[(last_per_sec - df["date"]).dt.days <= max_span_days].copy()
        if df.empty:
            return {"period_label": period_label, "monthly": [], "by_security": [],
                    "by_type": [], "detail": [], "summary": {}}

    monthly = df.groupby("month")["income_eur"].sum().reset_index().sort_values("month")

    df_sorted = df.sort_values(["securities_name", "date"])

    def _wtd_cost(g):
        abs_inc = g["income_eur"].abs()
        total_w = abs_inc.sum()
        if total_w == 0:
            return g["cost_basis_eur"].iloc[-1]
        return (g["cost_basis_eur"] * abs_inc).sum() / total_w

    rows = []
    for (sec_name, sec_type), g in df_sorted.groupby(["securities_name", "securities_type"]):
        rows.append({
            "securities_id": g["securities_id"].iloc[0],
            "securities_name": sec_name,
            "securities_type": sec_type,
            "period_income_eur": g["income_eur"].sum(),
            "cost_basis_eur": _wtd_cost(g),
            "position_start_date": g["position_start_date"].min(),
            "last_income_date": g["date"].max(),
        })
    df_t12 = pd.DataFrame(rows).sort_values("period_income_eur", ascending=False)

    ann_days_map = {"1 Year": 365, "2 Years": 730, "3 Years": 1095, "5 Years": 1825, "Previous Year": 365}
    if period == "All Time":
        base_ann_days = None  # computed per-security below
    elif period in ann_days_map:
        base_ann_days = ann_days_map[period]
    else:
        base_ann_days = max((ed - sd).days, 1)

    with get_db() as conn2:
        df_sec_meta = pd.read_sql("""
            SELECT Securities_Name, Dividend_Yield AS fwd_yield_pct,
                   Ex_Dividend_Date AS ex_div_date, Dividend_Frequency AS div_frequency
            FROM Securities
            WHERE Dividend_Yield IS NOT NULL OR Ex_Dividend_Date IS NOT NULL OR Dividend_Frequency IS NOT NULL
        """, conn2)

    if not df_sec_meta.empty:
        df_sec_meta.columns = df_sec_meta.columns.str.lower()
        df_t12 = df_t12.merge(df_sec_meta, on="securities_name", how="left")
    else:
        df_t12["fwd_yield_pct"] = None
        df_t12["ex_div_date"] = None
        df_t12["div_frequency"] = None

    # Map frequency to minimum annualization denominator (days per full cycle)
    _freq_min_days = {"Annual": 365, "Semi-Annual": 182, "Quarterly": 91, "Monthly": 30}

    def _ann_days_for_row(row):
        if base_ann_days is None:
            # All Time: span between first and last income date
            return max((row["last_income_date"] - row["position_start_date"]).days, 1)
        freq_floor = _freq_min_days.get(row.get("div_frequency"), 0)
        return max(base_ann_days, freq_floor)

    ann_days_series = df_t12.apply(_ann_days_for_row, axis=1)

    df_t12["yoc_pct"] = (
        df_t12["period_income_eur"] / df_t12["cost_basis_eur"].replace(0, float("nan"))
        * 100 * 365 / ann_days_series
    ).fillna(0)

    total_income = float(df_t12["period_income_eur"].sum())
    yoc_positive = df_t12[df_t12["yoc_pct"] > 0]["yoc_pct"]
    avg_yoc = float(yoc_positive.mean()) if not yoc_positive.empty else None

    by_type = (
        df_t12.groupby("securities_type")["period_income_eur"].sum()
        .reset_index().sort_values("period_income_eur", ascending=False)
    )

    summary = {
        "total_income_eur": round(total_income, 2),
        "securities_paying": len(df_t12),
        "avg_yoc_pct": round(avg_yoc, 4) if avg_yoc is not None else None,
    }

    disp_cols = ["securities_id", "securities_name", "securities_type", "period_income_eur", "cost_basis_eur",
                 "yoc_pct", "fwd_yield_pct", "ex_div_date", "div_frequency"]
    detail_cols = ["month", "securities_name", "accounts_name", "action", "income_eur"]

    return {
        "period_label": period_label,
        "monthly": _df_to_list(monthly),
        "by_security": _df_to_list(df_t12[disp_cols]),
        "by_type": _df_to_list(by_type),
        "detail": _df_to_list(df_sorted[detail_cols]),
        "summary": summary,
    }


# ── Dividend Forecast ───────────────────────────────────────────────────────────
@router.get("/dividends-forecast")
def get_dividends_forecast():
    """Projected 12-month dividend income for currently-held dividend-paying securities."""
    import calendar as _cal
    from datetime import date as _date, timedelta as _timedelta

    today = _date.today()

    def _add_months(d: _date, n: int) -> _date:
        m = d.month + n
        y = d.year + (m - 1) // 12
        m = (m - 1) % 12 + 1
        return d.replace(year=y, month=m, day=min(d.day, _cal.monthrange(y, m)[1]))

    _FREQ_MAP = {"monthly": 12, "quarterly": 4, "semi-annual": 2, "bi-annual": 2, "annual": 1, "yearly": 1}

    def _ppy(freq_str) -> int:
        if not freq_str or (isinstance(freq_str, float) and pd.isna(freq_str)):
            return 4
        return _FREQ_MAP.get(str(freq_str).strip().lower(), 4)

    def _next_dates(anchor, freq_str, horizon_months: int = 12) -> list:
        ppy = _ppy(freq_str)
        interval = max(round(12 / ppy), 1)
        try:
            d = pd.Timestamp(anchor).date() if anchor and not (isinstance(anchor, float) and pd.isna(anchor)) else today
        except Exception:
            d = today
        while d <= today:
            d = _add_months(d, interval)
        cutoff = _add_months(today, horizon_months)
        dates = []
        while d <= cutoff:
            dates.append(d)
            d = _add_months(d, interval)
        return dates

    query = """
        WITH fx_latest AS (
            SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
            FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC
        ),
        price_latest AS (
            SELECT DISTINCT ON (Securities_Id) Securities_Id, Close
            FROM Historical_Prices ORDER BY Securities_Id, Date DESC
        ),
        holdings_agg AS (
            SELECT h.Securities_Id,
                SUM(h.Quantity) AS total_qty,
                SUM(h.Quantity * COALESCE(h.Fifo_Avg_Cost_EUR, h.Fifo_Avg_Price * COALESCE(fx.FX_Rate, 1), 0)) AS cost_basis_eur
            FROM Holdings h
            JOIN Accounts a ON h.Accounts_Id = a.Accounts_Id
            LEFT JOIN fx_latest fx ON fx.Currencies_Id_1 = a.Currencies_Id
            WHERE h.Quantity > 0
            GROUP BY h.Securities_Id
        ),
        last_div AS (
            SELECT DISTINCT ON (Securities_Id)
                Securities_Id, Ex_Date AS last_ex_date, Amount AS last_amount
            FROM Securities_Dividends ORDER BY Securities_Id, Ex_Date DESC
        ),
        trailing_income AS (
            SELECT i.Securities_Id,
                SUM(i.Total_Amount_AccCur * COALESCE(fx.FX_Rate, 1)) AS trailing_12m_income_eur
            FROM Investments i
            JOIN Accounts a ON i.Accounts_Id = a.Accounts_Id
            LEFT JOIN fx_latest fx ON fx.Currencies_Id_1 = a.Currencies_Id
            WHERE i.Action IN ('Dividend','IntInc','Reinvest')
              AND i.Date >= CURRENT_DATE - INTERVAL '12 months'
            GROUP BY i.Securities_Id
        )
        SELECT s.Securities_Id AS securities_id, s.Securities_Name AS securities_name,
            s.Securities_Type AS securities_type,
            ha.total_qty, ha.cost_basis_eur,
            ROUND((ha.total_qty * COALESCE(pl.Close, 0) * COALESCE(fx2.FX_Rate, 1))::numeric, 2) AS market_value_eur,
            COALESCE(fx2.FX_Rate, 1) AS fx_rate,
            s.Dividend_Yield AS dividend_yield, s.Dividend_Rate AS dividend_rate,
            s.Ex_Dividend_Date AS ex_dividend_date, s.Dividend_Pay_Date AS dividend_pay_date,
            s.Dividend_Frequency AS dividend_frequency,
            ld.last_ex_date, ld.last_amount,
            COALESCE(ti.trailing_12m_income_eur, 0) AS trailing_12m_income_eur
        FROM Securities s
        JOIN holdings_agg ha ON ha.Securities_Id = s.Securities_Id
        LEFT JOIN price_latest pl  ON pl.Securities_Id  = s.Securities_Id
        LEFT JOIN fx_latest    fx2 ON fx2.Currencies_Id_1 = s.Currencies_Id
        LEFT JOIN last_div     ld  ON ld.Securities_Id  = s.Securities_Id
        LEFT JOIN trailing_income ti ON ti.Securities_Id = s.Securities_Id
        WHERE (s.Dividend_Yield IS NOT NULL OR s.Dividend_Rate IS NOT NULL OR ti.trailing_12m_income_eur > 0)
        ORDER BY s.Securities_Name
    """
    with get_db() as conn:
        df = pd.read_sql(query, conn)

    if df.empty:
        return {"summary": {}, "monthly_forecast": [], "by_security": [], "upcoming": []}

    rows = []
    for _, r in df.iterrows():
        mv   = float(r.get("market_value_eur") or 0)
        cost = float(r.get("cost_basis_eur")   or 0)
        qty  = float(r.get("total_qty")         or 0)
        fx   = float(r.get("fx_rate")           or 1)
        dr   = r.get("dividend_rate")
        dy   = r.get("dividend_yield")
        t12  = float(r.get("trailing_12m_income_eur") or 0)

        if pd.notna(dr) and float(dr) > 0 and qty > 0:
            # dr is in security currency; multiply by qty to get total in sec currency, then convert to EUR
            annual = float(dr) * qty * fx
            method = "Dividend Rate"
        elif pd.notna(dy) and float(dy) > 0 and mv > 0:
            annual = mv * float(dy) / 100
            method = "Fwd Yield"
        elif t12 > 0:
            annual = t12
            method = "Trailing 12m"
        else:
            continue

        if annual <= 0:
            continue

        freq    = r.get("dividend_frequency")
        ppy     = _ppy(freq)
        per_pmt = annual / ppy

        raw_ex  = r.get("ex_dividend_date")
        raw_pay = r.get("dividend_pay_date")

        ex_anchor = raw_ex if pd.notna(raw_ex) else r.get("last_ex_date")

        # Dividend_Pay_Date is only trustworthy as its own forecast anchor when it's a
        # plausible lag *after* the ex-date (a few weeks, typically) — some data sources
        # return stale/garbage pay dates (seen: decades in the past) that would otherwise
        # get projected forward independently of the ex-date and land on a completely
        # unrelated month, out of sync with the (correct) ex-date projection.
        _lag = None
        if pd.notna(raw_ex) and pd.notna(raw_pay):
            candidate_lag = (pd.Timestamp(raw_pay).date() - pd.Timestamp(raw_ex).date()).days
            if 0 <= candidate_lag <= 90:
                _lag = candidate_lag

        pay_anchor = (
            (pd.Timestamp(ex_anchor).date() + _timedelta(days=_lag))
            if (_lag is not None and pd.notna(ex_anchor)) else ex_anchor
        )
        ex_dates   = _next_dates(ex_anchor,  freq, horizon_months=13)
        pay_dates  = _next_dates(pay_anchor, freq, horizon_months=13)

        rows.append({
            "securities_id":            int(r["securities_id"]),
            "securities_name":          str(r["securities_name"]),
            "securities_type":          str(r["securities_type"]),
            "total_qty":                round(qty, 4),
            "market_value_eur":         round(mv, 2),
            "cost_basis_eur":           round(cost, 2),
            "annual_forecast_eur":      round(annual, 2),
            "per_payment_eur":          round(per_pmt, 2),
            "payments_per_year":        ppy,
            "frequency":                str(freq) if freq and not (isinstance(freq, float) and pd.isna(freq)) else "Quarterly (assumed)",
            "method":                   method,
            "dividend_yield":           float(dy) if pd.notna(dy) else None,
            "next_expected_ex_date":    str(ex_dates[0])  if ex_dates  else None,
            "next_expected_pay_date":   str(pay_dates[0]) if pay_dates else None,
            "last_known_ex_date":       str(pd.Timestamp(raw_ex).date())  if pd.notna(raw_ex)  else None,
            "last_known_pay_date":      str(pd.Timestamp(raw_pay).date()) if pd.notna(raw_pay) else None,
            "pay_lag_days":             _lag if _lag else None,
            "_ex_dates":                [str(d) for d in ex_dates],
            "_pay_dates":               [str(d) for d in pay_dates],
        })

    if not rows:
        return {"summary": {}, "monthly_forecast": [], "by_security": [], "upcoming": []}

    total_annual  = sum(r["annual_forecast_eur"] for r in rows)
    total_monthly = total_annual / 12
    total_cost    = sum(r["cost_basis_eur"] for r in rows)
    portfolio_yoc = (total_annual / total_cost * 100) if total_cost > 0 else 0

    monthly_map: dict = {}
    for r in rows:
        for d in r["_pay_dates"]:
            key = str(_date.fromisoformat(d).replace(day=1))
            monthly_map[key] = round(monthly_map.get(key, 0) + r["per_payment_eur"], 2)
    monthly_forecast = sorted(
        [{"month": k, "income_eur": v} for k, v in monthly_map.items()],
        key=lambda x: x["month"]
    )[:12]

    cutoff_3m = str(_add_months(today, 3))
    upcoming: list = []
    for r in rows:
        for ex_d, pay_d in zip(r["_ex_dates"], r["_pay_dates"]):
            if ex_d <= cutoff_3m:
                upcoming.append({
                    "ex_date":         ex_d,
                    "pay_date":        pay_d,
                    "securities_name": r["securities_name"],
                    "per_payment_eur": r["per_payment_eur"],
                    "frequency":       r["frequency"],
                    "method":          r["method"],
                })
    upcoming.sort(key=lambda x: x["ex_date"])

    by_security = sorted(
        [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows],
        key=lambda x: x["annual_forecast_eur"], reverse=True
    )

    return {
        "summary": {
            "total_annual_eur":  round(total_annual, 2),
            "total_monthly_eur": round(total_monthly, 2),
            "securities_count":  len(rows),
            "portfolio_yoc_pct": round(portfolio_yoc, 2),
        },
        "monthly_forecast": monthly_forecast,
        "by_security":      by_security,
        "upcoming":         upcoming,
    }


# ── Dividend Income Recommendations ────────────────────────────────────────────
@router.get("/dividend-recommendations")
def get_dividend_recommendations():
    """Score and rank securities in the database for passive income potential.

    5-factor composite score (0–100):
      Yield (35%) · Sharpe (25%) · Consistency (25%) · Analyst (10%) · 5yr Growth (5%)
    """

    query = """
        WITH fx_latest AS (
            SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
            FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC
        ),
        price_latest AS (
            SELECT DISTINCT ON (Securities_Id) Securities_Id, Close
            FROM Historical_Prices ORDER BY Securities_Id, Date DESC
        ),
        holdings_agg AS (
            SELECT h.Securities_Id,
                SUM(h.Quantity) AS total_qty,
                SUM(h.Quantity * COALESCE(h.Fifo_Avg_Cost_EUR,
                    h.Fifo_Avg_Price * COALESCE(fx.FX_Rate, 1), 0)) AS cost_basis_eur
            FROM Holdings h
            JOIN Accounts a ON h.Accounts_Id = a.Accounts_Id
            LEFT JOIN fx_latest fx ON fx.Currencies_Id_1 = a.Currencies_Id
            WHERE h.Quantity > 0
            GROUP BY h.Securities_Id
        ),
        trailing_income AS (
            SELECT i.Securities_Id,
                SUM(i.Total_Amount_AccCur * COALESCE(fx.FX_Rate, 1)) AS trailing_12m_income_eur
            FROM Investments i
            JOIN Accounts a ON i.Accounts_Id = a.Accounts_Id
            LEFT JOIN fx_latest fx ON fx.Currencies_Id_1 = a.Currencies_Id
            WHERE i.Action IN ('Dividend', 'IntInc', 'Reinvest')
              AND i.Date >= CURRENT_DATE - INTERVAL '12 months'
            GROUP BY i.Securities_Id
        ),
        sharpe_data AS (
            SELECT Securities_Id,
                COUNT(*) AS price_days,
                ROUND(((AVG(daily_r) * 252 - 0.03) / NULLIF(STDDEV(daily_r) * SQRT(252), 0))::numeric, 3)
                    AS sharpe_ratio
            FROM (
                SELECT Securities_Id,
                    Close / NULLIF(LAG(Close) OVER (PARTITION BY Securities_Id ORDER BY Date), 0) - 1
                        AS daily_r
                FROM Historical_Prices
                WHERE Date >= CURRENT_DATE - INTERVAL '365 days'
            ) t
            WHERE daily_r IS NOT NULL
            GROUP BY Securities_Id
            HAVING COUNT(*) >= 30
        ),
        div_consistency AS (
            SELECT Securities_Id, COUNT(*) AS div_payments
            FROM Securities_Dividends
            WHERE Ex_Date >= CURRENT_DATE - INTERVAL '3 years'
            GROUP BY Securities_Id
        )
        SELECT
            s.Securities_Id            AS securities_id,
            s.Securities_Name          AS securities_name,
            s.Securities_Type          AS securities_type,
            COALESCE(NULLIF(TRIM(s.Sector), ''), NULL) AS sector,
            s.Dividend_Yield           AS dividend_yield,
            s.Five_Year_Avg_Yield      AS five_year_avg_yield,
            s.Dividend_Frequency       AS dividend_frequency,
            LOWER(s.Analyst_Rating)    AS analyst_rating,
            s.Analyst_Target_Price     AS analyst_target_price,
            COALESCE(pl.Close, 0) * COALESCE(fx2.FX_Rate, 1) AS current_price_eur,
            ha.total_qty,
            ha.cost_basis_eur,
            ROUND((COALESCE(ha.total_qty, 0) * COALESCE(pl.Close, 0) * COALESCE(fx2.FX_Rate, 1))::numeric, 2)
                AS market_value_eur,
            sd.sharpe_ratio,
            sd.price_days,
            COALESCE(dc.div_payments, 0) AS div_payments,
            COALESCE(ti.trailing_12m_income_eur, 0) AS trailing_12m_income_eur
        FROM Securities s
        LEFT JOIN holdings_agg ha   ON ha.Securities_Id = s.Securities_Id
        LEFT JOIN price_latest pl   ON pl.Securities_Id = s.Securities_Id
        LEFT JOIN fx_latest    fx2  ON fx2.Currencies_Id_1 = s.Currencies_Id
        LEFT JOIN sharpe_data  sd   ON sd.Securities_Id = s.Securities_Id
        LEFT JOIN div_consistency dc ON dc.Securities_Id = s.Securities_Id
        LEFT JOIN trailing_income ti ON ti.Securities_Id = s.Securities_Id
        WHERE (s.Dividend_Yield IS NOT NULL OR s.Dividend_Rate IS NOT NULL
               OR ti.trailing_12m_income_eur > 0 OR dc.div_payments > 0)
        ORDER BY s.Securities_Name
    """

    with get_db() as conn:
        df = pd.read_sql(query, conn)

    if df.empty:
        return []

    def _sf(v):
        try:
            f = float(v)
            return None if pd.isna(f) else f
        except Exception:
            return None

    def _ff(v, d=0.0):
        return _sf(v) or d

    _ANALYST_SCORE = {
        "strong_buy": 100, "buy": 75, "outperform": 75,
        "hold": 50, "neutral": 50, "market_perform": 50,
        "underperform": 25, "sell": 0, "strong_sell": 0,
    }

    rows = []
    for _, r in df.iterrows():
        dy      = _ff(r.get("dividend_yield"))
        t12     = _ff(r.get("trailing_12m_income_eur"))
        mv      = _ff(r.get("market_value_eur"))
        cost    = _ff(r.get("cost_basis_eur"))
        qty     = _ff(r.get("total_qty"))
        five_yr = _ff(r.get("five_year_avg_yield"))
        div_pay = int(_ff(r.get("div_payments")))
        sharpe  = _sf(r.get("sharpe_ratio"))
        analyst = str(r.get("analyst_rating") or "").strip().lower() or None

        # Effective yield: forward → trailing-based fallback
        eff_yield = dy if dy > 0 else (t12 / mv * 100 if mv > 0 and t12 > 0 else 0)

        if eff_yield <= 0 and div_pay == 0:
            continue  # no dividend signal at all

        # ── Factor scores (0–100) ─────────────────────────────────────────────
        # Yield: 0%→0, 8%+→100 (cap at 8% to avoid trap yields skewing ranking)
        yield_score = min(eff_yield / 8.0 * 100, 100)

        # Sharpe: maps [-0.5, 2.0] → [0, 100]
        sharpe_score = max(min((sharpe + 0.5) / 2.5 * 100, 100), 0) if sharpe is not None else None

        # Consistency: quarterly payer over 3yr = 12 payments = 100
        consistency_score = min(div_pay / 12.0 * 100, 100)

        # 5yr growth: current yield above 5yr avg signals rising dividends
        if five_yr > 0 and eff_yield > 0:
            growth_score = min(max(eff_yield / five_yr * 50, 0), 100)
        else:
            growth_score = None

        # Analyst signal
        analyst_score = _ANALYST_SCORE.get(analyst) if analyst else None

        # Weighted composite (re-normalise across available components)
        _components = {
            "yield":       (yield_score,      0.35),
            "consistency": (consistency_score, 0.25),
        }
        if sharpe_score  is not None: _components["sharpe"]  = (sharpe_score,  0.25)
        if growth_score  is not None: _components["growth"]  = (growth_score,  0.05)
        if analyst_score is not None: _components["analyst"] = (analyst_score, 0.10)

        total_w   = sum(w for _, w in _components.values())
        composite = sum(s * w for s, w in _components.values()) / total_w if total_w else 0

        # ── Tags ──────────────────────────────────────────────────────────────
        tags: list[str] = []
        if eff_yield >= 4:           tags.append("High Yield")
        if consistency_score >= 70:  tags.append("Consistent")
        if sharpe is not None and sharpe >= 0.8: tags.append("Good Sharpe")
        if five_yr > 0 and eff_yield > five_yr:  tags.append("Yield Growing")
        if analyst_score is not None and analyst_score >= 75: tags.append("Analyst: Buy")

        freq = r.get("dividend_frequency")
        freq_str = str(freq) if freq is not None and not (isinstance(freq, float) and pd.isna(freq)) else None

        rows.append({
            "securities_id":       int(r["securities_id"]),
            "securities_name":     str(r["securities_name"]),
            "securities_type":     str(r["securities_type"]),
            "sector":              str(r["sector"]) if r.get("sector") and not (isinstance(r["sector"], float) and pd.isna(r["sector"])) else None,
            "effective_yield_pct": round(eff_yield, 2),
            "five_year_avg_yield": round(five_yr, 2) if five_yr > 0 else None,
            "dividend_frequency":  freq_str,
            "analyst_rating":      analyst or None,
            "analyst_target_eur":  round(float(r["analyst_target_price"]) * _ff(r.get("current_price_eur"), 1) / _ff(r.get("current_price_eur"), 1), 2) if _sf(r.get("analyst_target_price")) else None,
            "sharpe_ratio":        round(sharpe, 2) if sharpe is not None else None,
            "price_days":          int(_ff(r.get("price_days"))) if r.get("price_days") is not None else None,
            "div_payments_3yr":    div_pay,
            "trailing_12m_eur":    round(t12, 2),
            "market_value_eur":    round(mv, 2) if mv > 0 else None,
            "cost_basis_eur":      round(cost, 2) if cost > 0 else None,
            "is_held":             qty > 0,
            "yield_score":         round(yield_score, 1),
            "sharpe_score":        round(sharpe_score, 1) if sharpe_score is not None else None,
            "consistency_score":   round(consistency_score, 1),
            "growth_score":        round(growth_score, 1) if growth_score is not None else None,
            "analyst_score":       analyst_score,
            "composite_score":     round(composite, 1),
            "tags":                tags,
        })

    rows.sort(key=lambda x: x["composite_score"], reverse=True)
    return rows


def _get_all_inv_txns_for_gains(conn) -> pd.DataFrame:
    """All buy/sell investment transactions with EUR amounts for FIFO/LIFO lot matching."""
    query = """
        WITH txn_with_eur AS (
            SELECT
                i.Investments_Id,
                i.Securities_Id,
                i.Accounts_Id,
                i.Date,
                i.Action,
                i.Instrument_Type,
                ABS(COALESCE(i.Quantity, 0)) AS quantity,
                i.Price_Per_Share,
                CASE
                    WHEN i.Transactions_Id IS NOT NULL AND t_cash.Total_Amount IS NOT NULL
                        THEN ABS(t_cash.Total_Amount)
                    WHEN i.Total_Amount_AccCur IS NOT NULL AND i.Total_Amount_AccCur != 0
                        THEN CASE WHEN c.Currencies_ShortName != 'EUR'
                            THEN ABS(i.Total_Amount_AccCur) * COALESCE(
                                (SELECT fx.FX_Rate FROM Historical_FX fx
                                 WHERE fx.Currencies_Id_1 = c.Currencies_Id
                                   AND fx.Date <= i.Date
                                 ORDER BY fx.Date DESC LIMIT 1), 1.0)
                            ELSE ABS(i.Total_Amount_AccCur)
                        END
                    -- Total_Amount_AccCur missing/zero (e.g. a same-day sell+rebuy cost-basis
                    -- reset with no recorded cash amount) — fall back to price*qty*FX_Rate
                    -- rather than dropping the row, which would silently distort later lot
                    -- matching (a skipped buy/sell shifts which lots later sells consume).
                    ELSE ABS(i.Price_Per_Share) * ABS(COALESCE(i.Quantity, 0)) * COALESCE(i.FX_Rate, 1.0)
                END AS amount_eur
            FROM Investments i
            JOIN Securities s   ON s.Securities_Id = i.Securities_Id
            JOIN Currencies c   ON c.Currencies_Id = s.Currencies_Id
            LEFT JOIN Transactions t_cash ON t_cash.Transactions_Id = i.Transactions_Id
            WHERE i.Action IN ('Buy','Sell','Reinvest','ShrIn','ShrOut','Expire','CashIn','CashOut')
        )
        SELECT
            te.*,
            s.Securities_Name                  AS securities_name,
            a.Accounts_Name                    AS account_name,
            -- Instrument-type override takes precedence over security-level Is_Tax_Exempt
            CASE WHEN ito.Tax_Category_Override IS NOT NULL THEN FALSE
                 ELSE COALESCE(s.Is_Tax_Exempt, FALSE) END          AS is_tax_exempt,
            s.Securities_Type                                        AS securities_type,
            COALESCE(ito.Tax_Category_Override, s.Tax_Category)     AS tax_category,
            tcr.Gains_Taxable                                        AS gains_taxable,
            tcr.Gains_Rate                                           AS gains_rate,
            tcr.Gains_Tax_Code                                       AS gains_tax_code
        FROM txn_with_eur te
        JOIN Securities s ON s.Securities_Id = te.Securities_Id
        JOIN Accounts   a ON a.Accounts_Id   = te.Accounts_Id
        LEFT JOIN Instrument_Type_Tax_Override ito ON ito.Instrument_Type = te.Instrument_Type::text
        LEFT JOIN Tax_Category_Rules           tcr ON tcr.Tax_Category = COALESCE(ito.Tax_Category_Override, s.Tax_Category)
        WHERE COALESCE(tcr.Show_In_Capital_Gains, TRUE) = TRUE
        ORDER BY te.Securities_Id, te.Accounts_Id, te.Date, te.Investments_Id
    """
    df = pd.read_sql(query, conn)
    if not df.empty:
        df['date'] = pd.to_datetime(df['date'])
    return df


def _compute_lot_gains(df_all: pd.DataFrame, tax_year: int, method: str = 'FIFO') -> pd.DataFrame:
    """FIFO or LIFO lot matching.

    Tracks both long lots and short lots per (security, account). A sell first
    closes long lots (recognised now); any quantity left over opens/extends a
    short position instead of being treated as free profit — the previous
    version simply stopped once the lot queue ran dry, but still used the
    *full* sale amount as proceeds against only the partially-matched cost
    basis, silently inflating gains for any account that ever sold short (e.g.
    margin/CFD accounts). A buy mirrors this: it first covers outstanding
    short lots (recognised now, gain = the original short-sale proceeds minus
    this cover's cost) before any leftover opens a new long lot. Ordinary
    long-only accounts are numerically unaffected.
    """
    from collections import deque
    BUY_ACTIONS  = {'Buy', 'Reinvest', 'ShrIn', 'CashIn'}
    SELL_ACTIONS = {'Sell', 'Expire', 'ShrOut', 'CashOut'}
    is_lifo = (method == 'LIFO')
    results = []

    for (sec_id, acc_id), grp in df_all.groupby(['securities_id', 'accounts_id'], sort=False):
        long_lots: deque = deque()
        short_lots: deque = deque()

        def make_row(row, date, action, qty, proceeds, cost, first_date):
            days_held = (date - first_date).days if first_date else 0
            results.append({
                'securities_id':   sec_id,
                'security':        row['securities_name'],
                'ticker':          None,
                'account':         row['account_name'],
                'date':            date.date().isoformat(),
                'action':          action,
                'quantity':        qty,
                'sell_price':      row.get('price_per_share'),
                'avg_cost':        cost / qty if qty > 1e-9 else None,
                'proceeds_eur':    proceeds,
                'cost_eur':        cost,
                'gain_loss_eur':   proceeds - cost,
                'holding_type':    'Long-term' if days_held >= 365 else 'Short-term',
                'is_tax_exempt':   bool(row.get('is_tax_exempt', False)),
                'instrument_type': row.get('instrument_type'),
                'securities_type': row.get('securities_type'),
                'tax_category':    row.get('tax_category'),
                'gains_taxable':   bool(row['gains_taxable']) if pd.notna(row.get('gains_taxable')) else None,
                'gains_rate':      float(row['gains_rate']) if pd.notna(row.get('gains_rate')) else None,
                'gains_tax_code':  row.get('gains_tax_code') if pd.notna(row.get('gains_tax_code')) else None,
            })

        for _, row in grp.sort_values(['date', 'investments_id']).iterrows():
            action = row['action']
            qty    = float(row['quantity'])   if pd.notna(row['quantity'])   else 0.0
            amount = float(row['amount_eur']) if pd.notna(row['amount_eur']) else 0.0
            date = row['date']
            if qty <= 1e-9:
                continue
            cost_ps = amount / qty

            if action in BUY_ACTIONS:
                remaining = qty
                cover_cost, cover_proceeds, cover_qty, first_short_date = 0.0, 0.0, 0.0, None
                while remaining > 1e-9 and short_lots:
                    lot = short_lots[-1] if is_lifo else short_lots[0]
                    if first_short_date is None:
                        first_short_date = lot['date']
                    consumed = min(lot['qty'], remaining)
                    cover_proceeds += consumed * lot['cost_ps']  # what the short sale originally received
                    cover_cost     += consumed * cost_ps         # what covering it now costs
                    cover_qty      += consumed
                    lot['qty']     -= consumed
                    remaining      -= consumed
                    if lot['qty'] < 1e-9:
                        (short_lots.pop() if is_lifo else short_lots.popleft())
                if cover_qty > 1e-9 and date.year == tax_year:
                    make_row(row, date, action, cover_qty, cover_proceeds, cover_cost, first_short_date)
                if remaining > 1e-9:
                    long_lots.append({'date': date, 'qty': remaining, 'cost_ps': cost_ps})

            elif action in SELL_ACTIONS:
                remaining = qty
                close_cost, close_proceeds, close_qty, first_buy_date = 0.0, 0.0, 0.0, None
                while remaining > 1e-9 and long_lots:
                    lot = long_lots[-1] if is_lifo else long_lots[0]
                    if first_buy_date is None:
                        first_buy_date = lot['date']
                    consumed = min(lot['qty'], remaining)
                    close_cost     += consumed * lot['cost_ps']
                    close_proceeds += consumed * cost_ps
                    close_qty      += consumed
                    lot['qty']     -= consumed
                    remaining      -= consumed
                    if lot['qty'] < 1e-9:
                        (long_lots.pop() if is_lifo else long_lots.popleft())
                if close_qty > 1e-9 and date.year == tax_year:
                    make_row(row, date, action, close_qty, close_proceeds, close_cost, first_buy_date)
                if remaining > 1e-9:
                    short_lots.append({'date': date, 'qty': remaining, 'cost_ps': cost_ps})

    cols = ['securities_id','security','ticker','account','date','action','quantity',
            'sell_price','avg_cost','proceeds_eur','cost_eur','gain_loss_eur',
            'holding_type','is_tax_exempt','instrument_type','securities_type',
            'tax_category','gains_taxable','gains_rate','gains_tax_code']
    return pd.DataFrame(results, columns=cols) if results else pd.DataFrame(columns=cols)


def _compute_wac_gains(df_all: pd.DataFrame, tax_year: int) -> pd.DataFrame:
    """Weighted-Average-Cost gains, using a single running average per position.

    Maintains one (qty, avg_cost) pair per (security, account) for the long
    side and one for the short side. A buy blends into the long average (or
    first covers any outstanding short at that short's own average cost); a
    sell is costed at the current long average — which does NOT change as a
    result of the sell, only the quantity shrinks — (or opens/extends a short
    position at the sale's own average price if oversold). The average resets
    only when the corresponding quantity hits exactly zero.

    The previous implementation recomputed an average from every buy since
    the last time the position was fully flat, which double-counted buy
    quantity/value already consumed by an earlier partial sell within the
    same still-open episode, overstating cost basis on later sells in that
    episode. This version tracks the position continuously instead.
    """
    BUY_ACTIONS  = {'Buy', 'Reinvest', 'ShrIn', 'CashIn'}
    SELL_ACTIONS = {'Sell', 'Expire', 'ShrOut', 'CashOut'}
    results = []

    for (sec_id, acc_id), grp in df_all.groupby(['securities_id', 'accounts_id'], sort=False):
        long_qty, long_avg_cost, long_open_date = 0.0, 0.0, None
        short_qty, short_avg_cost, short_open_date = 0.0, 0.0, None

        def make_row(row, date, action, qty, proceeds, cost, open_date):
            days_held = (date - open_date).days if open_date else 0
            results.append({
                'securities_id':   sec_id,
                'security':        row['securities_name'],
                'ticker':          None,
                'account':         row['account_name'],
                'date':            date.date().isoformat(),
                'action':          action,
                'quantity':        qty,
                'sell_price':      row.get('price_per_share'),
                'avg_cost':        cost / qty if qty > 1e-9 else None,
                'proceeds_eur':    proceeds,
                'cost_eur':        cost,
                'gain_loss_eur':   proceeds - cost,
                'holding_type':    'Long-term' if days_held >= 365 else 'Short-term',
                'is_tax_exempt':   bool(row.get('is_tax_exempt', False)),
                'instrument_type': row.get('instrument_type'),
                'securities_type': row.get('securities_type'),
                'tax_category':    row.get('tax_category'),
                'gains_taxable':   bool(row['gains_taxable']) if pd.notna(row.get('gains_taxable')) else None,
                'gains_rate':      float(row['gains_rate']) if pd.notna(row.get('gains_rate')) else None,
                'gains_tax_code':  row.get('gains_tax_code') if pd.notna(row.get('gains_tax_code')) else None,
            })

        for _, row in grp.sort_values(['date', 'investments_id']).iterrows():
            action = row['action']
            qty    = float(row['quantity'])   if pd.notna(row['quantity'])   else 0.0
            amount = float(row['amount_eur']) if pd.notna(row['amount_eur']) else 0.0
            date = row['date']
            if qty <= 1e-9:
                continue
            cost_ps = amount / qty

            if action in BUY_ACTIONS:
                remaining = qty
                if short_qty > 1e-9:
                    cover_qty = min(short_qty, remaining)
                    cover_proceeds = cover_qty * short_avg_cost  # the short sale's original proceeds/share
                    cover_cost     = cover_qty * cost_ps         # cost to cover now
                    if date.year == tax_year:
                        make_row(row, date, action, cover_qty, cover_proceeds, cover_cost, short_open_date)
                    short_qty -= cover_qty
                    remaining -= cover_qty
                    if short_qty < 1e-9:
                        short_qty, short_avg_cost, short_open_date = 0.0, 0.0, None
                if remaining > 1e-9:
                    if long_qty < 1e-9:
                        long_open_date = date
                    new_qty = long_qty + remaining
                    long_avg_cost = (long_qty * long_avg_cost + remaining * cost_ps) / new_qty
                    long_qty = new_qty

            elif action in SELL_ACTIONS:
                remaining = qty
                if long_qty > 1e-9:
                    close_qty = min(long_qty, remaining)
                    close_cost     = close_qty * long_avg_cost   # fixed average — unaffected by this sell
                    close_proceeds = close_qty * cost_ps
                    if date.year == tax_year:
                        make_row(row, date, action, close_qty, close_proceeds, close_cost, long_open_date)
                    long_qty -= close_qty
                    remaining -= close_qty
                    if long_qty < 1e-9:
                        long_qty, long_avg_cost, long_open_date = 0.0, 0.0, None
                if remaining > 1e-9:
                    if short_qty < 1e-9:
                        short_open_date = date
                    new_qty = short_qty + remaining
                    short_avg_cost = (short_qty * short_avg_cost + remaining * cost_ps) / new_qty
                    short_qty = new_qty

    cols = ['securities_id','security','ticker','account','date','action','quantity',
            'sell_price','avg_cost','proceeds_eur','cost_eur','gain_loss_eur',
            'holding_type','is_tax_exempt','instrument_type','securities_type',
            'tax_category','gains_taxable','gains_rate','gains_tax_code']
    return pd.DataFrame(results, columns=cols) if results else pd.DataFrame(columns=cols)


@router.get("/capital-gains")
def get_capital_gains(year: int = Query(None), method: str = Query('WAC')):
    """Realized capital gains for a tax year.

    method: WAC (Weighted Average Cost), FIFO, or LIFO.
    - WAC: per-sell WAC using only buys in the current open position (resets after full close).
    - FIFO/LIFO: lot-based matching from first or last purchased lots.
    - Excludes ShrOut (non-taxable corporate action shares-out).
    """
    from datetime import date as _date
    if year is None:
        with get_db() as conn:
            yr_df = pd.read_sql(
                "SELECT EXTRACT(YEAR FROM Date)::int AS yr FROM Investments WHERE Action IN ('Sell','Expire') ORDER BY Date DESC LIMIT 1",
                conn
            )
        year = int(yr_df.iloc[0]["yr"]) if not yr_df.empty else _date.today().year

    with get_db() as conn:
        df_all = _get_all_inv_txns_for_gains(conn)

    if method.upper() in ('FIFO', 'LIFO'):
        df = _compute_lot_gains(df_all, year, method=method.upper())
        return _df_to_list(df)

    df = _compute_wac_gains(df_all, year)
    return _df_to_list(df)


@router.get("/budget-vs-actual")
def get_budget_vs_actual(year: int = Query(2024), ref_years: int = Query(2)):
    """Budget vs actual — matches Streamlit get_budget_vs_actual with FX conversion."""
    query = """
    WITH RECURSIVE cat_path AS (
        SELECT Categories_Id,
               Categories_Name::TEXT AS full_path,
               Categories_Type::TEXT AS Categories_Type,
               Categories_Id_Parent
        FROM Categories WHERE Categories_Id_Parent IS NULL
        UNION ALL
        SELECT c.Categories_Id,
               cp.full_path || ' : ' || c.Categories_Name,
               c.Categories_Type::TEXT,
               c.Categories_Id_Parent
        FROM Categories c
        JOIN cat_path cp ON c.Categories_Id_Parent = cp.Categories_Id
    ),
    fx AS (
        SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
        FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC
    ),
    hist_annual AS (
        SELECT s.Categories_Id,
               EXTRACT(year FROM t.Date)::int AS yr,
               ABS(SUM(s.Amount *
                   CASE WHEN cur.Currencies_ShortName = 'EUR' THEN 1
                        ELSE COALESCE(fx.FX_Rate, 1) END)) AS annual_spend
        FROM Splits s
        JOIN Transactions t ON t.Transactions_Id = s.Transactions_Id
        JOIN Categories c   ON c.Categories_Id   = s.Categories_Id
        JOIN Accounts a     ON a.Accounts_Id      = t.Accounts_Id
        JOIN Currencies cur ON cur.Currencies_Id  = a.Currencies_Id
        LEFT JOIN fx        ON fx.Currencies_Id_1 = a.Currencies_Id
        WHERE t.Transfers_Id IS NULL
          AND c.Categories_Type NOT IN ('Income','Transfer','Trading','Investment','Interest','Dividend')
          AND EXTRACT(year FROM t.Date) >= EXTRACT(year FROM CURRENT_DATE) - %(ref_years)s
          AND EXTRACT(year FROM t.Date) <  EXTRACT(year FROM CURRENT_DATE)
        GROUP BY s.Categories_Id, EXTRACT(year FROM t.Date)
    ),
    hist AS (
        SELECT Categories_Id, ROUND(AVG(annual_spend)::numeric, 2) AS avg_annual
        FROM hist_annual GROUP BY Categories_Id
    ),
    actual_year AS (
        SELECT s.Categories_Id,
               ROUND(ABS(SUM(s.Amount *
                   CASE WHEN cur.Currencies_ShortName = 'EUR' THEN 1
                        ELSE COALESCE(fx.FX_Rate, 1) END))::numeric, 2) AS actual_amount
        FROM Splits s
        JOIN Transactions t ON t.Transactions_Id = s.Transactions_Id
        JOIN Categories c   ON c.Categories_Id   = s.Categories_Id
        JOIN Accounts a     ON a.Accounts_Id      = t.Accounts_Id
        JOIN Currencies cur ON cur.Currencies_Id  = a.Currencies_Id
        LEFT JOIN fx        ON fx.Currencies_Id_1 = a.Currencies_Id
        WHERE t.Transfers_Id IS NULL
          AND c.Categories_Type NOT IN ('Income','Transfer','Trading','Investment','Interest','Dividend')
          AND EXTRACT(year FROM t.Date) = %(year)s
        GROUP BY s.Categories_Id
    ),
    prior_year AS (
        SELECT s.Categories_Id,
               ROUND(ABS(SUM(s.Amount *
                   CASE WHEN cur.Currencies_ShortName = 'EUR' THEN 1
                        ELSE COALESCE(fx.FX_Rate, 1) END))::numeric, 2) AS prior_amount
        FROM Splits s
        JOIN Transactions t ON t.Transactions_Id = s.Transactions_Id
        JOIN Categories c   ON c.Categories_Id   = s.Categories_Id
        JOIN Accounts a     ON a.Accounts_Id      = t.Accounts_Id
        JOIN Currencies cur ON cur.Currencies_Id  = a.Currencies_Id
        LEFT JOIN fx        ON fx.Currencies_Id_1 = a.Currencies_Id
        WHERE t.Transfers_Id IS NULL
          AND c.Categories_Type NOT IN ('Income','Transfer','Trading','Investment','Interest','Dividend')
          AND EXTRACT(year FROM t.Date) = %(year)s - 1
        GROUP BY s.Categories_Id
    ),
    budgets AS (
        SELECT Categories_Id, Budget_Amount
        FROM Annual_Budgets WHERE Year = %(year)s
    )
    SELECT
        c.Categories_Id                                          AS categories_id,
        c.full_path                                             AS categories_name,
        COALESCE(h.avg_annual, 0)                              AS avg_annual_hist,
        COALESCE(py.prior_amount, 0)                           AS prior_year_amount,
        COALESCE(b.Budget_Amount, 0)                           AS budget_amount,
        COALESCE(ay.actual_amount, 0)                          AS actual_amount,
        COALESCE(b.Budget_Amount, 0) - COALESCE(ay.actual_amount, 0) AS variance_eur,
        CASE WHEN COALESCE(b.Budget_Amount, 0) > 0
             THEN ROUND((COALESCE(ay.actual_amount, 0) / b.Budget_Amount * 100)::numeric, 1)
             ELSE NULL END                                      AS variance_pct,
        (COALESCE(ay.actual_amount, 0) > COALESCE(b.Budget_Amount, 0)) AS over_budget
    FROM cat_path c
    LEFT JOIN hist         h  ON h.Categories_Id  = c.Categories_Id
    LEFT JOIN prior_year   py ON py.Categories_Id = c.Categories_Id
    LEFT JOIN actual_year  ay ON ay.Categories_Id = c.Categories_Id
    LEFT JOIN budgets       b  ON b.Categories_Id  = c.Categories_Id
    WHERE (h.Categories_Id IS NOT NULL OR ay.Categories_Id IS NOT NULL
           OR py.Categories_Id IS NOT NULL OR b.Categories_Id IS NOT NULL)
      AND c.Categories_Type NOT IN ('Income','Transfer','Trading','Investment','Interest','Dividend')
    ORDER BY c.full_path
    """
    # Ensure the table exists before querying
    from database.queries import ensure_budgets_table
    ensure_budgets_table()
    with get_db() as conn:
        df = pd.read_sql(query, conn, params={"year": year, "ref_years": ref_years})
    return _df_to_list(df)


@router.get("/annual-income")
def get_annual_income(year: int = Query(2024)):
    """Total income (Income + Dividend + Interest) for the year, FX-converted to EUR."""
    query = """
    WITH fx AS (
        SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
        FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC
    )
    SELECT COALESCE(SUM(
        s.Amount *
        CASE WHEN cur.Currencies_ShortName = 'EUR' THEN 1
             ELSE COALESCE(fx.FX_Rate, 1) END
    ), 0) AS total_income_eur
    FROM Splits s
    JOIN Transactions t  ON t.Transactions_Id = s.Transactions_Id
    JOIN Categories   c  ON c.Categories_Id   = s.Categories_Id
    JOIN Accounts     a  ON a.Accounts_Id      = t.Accounts_Id
    JOIN Currencies   cur ON cur.Currencies_Id = a.Currencies_Id
    LEFT JOIN fx          ON fx.Currencies_Id_1 = a.Currencies_Id
    WHERE t.Transfers_Id IS NULL
      AND c.Categories_Type IN ('Income','Dividend','Interest')
      AND EXTRACT(year FROM t.Date) = %(year)s
    """
    with get_db() as conn:
        df = pd.read_sql(query, conn, params={"year": year})
    return {"total_income_eur": float(df["total_income_eur"].iloc[0])}


@router.get("/ytd-expense-transactions")
def get_ytd_expense_transactions(year: int = Query(2024)):
    """All expense transactions for the year with full category path, for drill-down."""
    query = """
    WITH RECURSIVE cat_path AS (
        SELECT Categories_Id, Categories_Name::TEXT AS full_path, Categories_Type::TEXT, Categories_Id_Parent
        FROM Categories WHERE Categories_Id_Parent IS NULL
        UNION ALL
        SELECT c.Categories_Id, cp.full_path || ' : ' || c.Categories_Name, c.Categories_Type::TEXT, c.Categories_Id_Parent
        FROM Categories c JOIN cat_path cp ON c.Categories_Id_Parent = cp.Categories_Id
    ),
    fx AS (
        SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
        FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC
    )
    SELECT
        t.Date::date::text          AS date,
        COALESCE(p.Payees_Name, '') AS payee,
        cp.full_path                AS category,
        ROUND((ABS(s.Amount) *
               CASE WHEN cur.Currencies_ShortName = 'EUR' THEN 1
                    ELSE COALESCE(fx.FX_Rate, 1) END)::numeric, 2) AS amount_eur,
        COALESCE(t.Description, '') AS notes
    FROM Splits s
    JOIN Transactions t  ON t.Transactions_Id  = s.Transactions_Id
    JOIN cat_path cp     ON cp.Categories_Id   = s.Categories_Id
    JOIN Accounts a      ON a.Accounts_Id       = t.Accounts_Id
    JOIN Currencies cur  ON cur.Currencies_Id   = a.Currencies_Id
    LEFT JOIN fx         ON fx.Currencies_Id_1  = a.Currencies_Id
    LEFT JOIN Payees p   ON p.Payees_Id         = t.Payees_Id
    WHERE t.Transfers_Id IS NULL
      AND cp.Categories_Type NOT IN ('Income','Transfer','Trading','Investment','Interest','Dividend')
      AND EXTRACT(year FROM t.Date) = %(year)s
    ORDER BY t.Date DESC, ABS(s.Amount) DESC
    """
    with get_db() as conn:
        df = pd.read_sql(query, conn, params={"year": year})
    return _df_to_list(df)


@router.get("/cash-flow-forecast")
def get_cash_flow_forecast(months_ahead: int = Query(6)):
    """Cash flow forecast from recurring templates — all active, projecting next occurrence."""
    import datetime as _dt
    query = """
    SELECT
        rt.templates_id AS template_id,
        rt.name,
        a.accounts_name AS account,
        py.payees_name AS payee,
        rt.total_amount AS amount,
        rt.next_due_date AS next_due_date_raw,
        rt.periodicity
    FROM Recurring_Templates rt
    LEFT JOIN Accounts a  ON a.accounts_id  = rt.accounts_id
    LEFT JOIN Payees   py ON py.payees_id   = rt.payees_id
    WHERE rt.active = TRUE
      AND (rt.end_date IS NULL OR rt.end_date >= CURRENT_DATE)
    ORDER BY rt.next_due_date ASC NULLS LAST
    """
    with get_db() as conn:
        df = pd.read_sql(query, conn)

    if df.empty:
        return []

    # Project next_due_date forward to the next upcoming occurrence
    cutoff = _dt.date.today() + _dt.timedelta(days=months_ahead * 30)
    period_days = {
        'Daily': 1, 'Weekly': 7, 'Bi-Weekly': 14, 'Monthly': 30,
        'Bi-Monthly': 61, 'Quarterly': 91, 'Semi-Annual': 182, 'Annual': 365,
    }
    rows = []
    today = _dt.date.today()
    for _, r in df.iterrows():
        raw = r['next_due_date_raw']
        if raw is None or (hasattr(raw, '__class__') and str(raw) == 'NaT'):
            proj = today
        else:
            proj = raw.date() if hasattr(raw, 'date') else _dt.date.fromisoformat(str(raw)[:10])

        # Advance past-due date forward by one period at a time (max 500 steps)
        step = period_days.get(r.get('periodicity') or 'Monthly', 30)
        steps = 0
        while proj < today and steps < 500:
            proj += _dt.timedelta(days=step)
            steps += 1

        if proj <= cutoff:
            rows.append({
                'template_id': r['template_id'],
                'name': r['name'],
                'account': r['account'],
                'payee': r['payee'],
                'amount': r['amount'],
                'next_due_date': proj.isoformat(),
                'periodicity': r['periodicity'],
            })

    rows.sort(key=lambda x: x['next_due_date'])
    return rows


@router.get("/cash-flow-forecast-full")
def get_cash_flow_forecast_full(
    days: int = Query(60),
    months_back: int = Query(2),
):
    """
    Full cash-flow forecast replicating the Streamlit view, plus recurring templates:
    - Explicitly scheduled future transactions (Date > today, within horizon)
    - Active Recurring Templates, projected forward from their own next_due_date/periodicity
    - Recurring patterns detected from last N complete months, projected forward
      (payees already covered by a scheduled transaction OR an active template are excluded,
      to avoid the same bill being counted twice)
    Returns: { scheduled, templates, recurring, metrics }
    """
    import datetime as _dt
    from dateutil.relativedelta import relativedelta

    today = _dt.date.today()
    cutoff = today + _dt.timedelta(days=days)
    _PERIOD_STEP = {
        'Daily': relativedelta(days=1), 'Weekly': relativedelta(weeks=1),
        'Bi-Weekly': relativedelta(weeks=2), 'Monthly': relativedelta(months=1),
        'Bi-Monthly': relativedelta(months=2), 'Quarterly': relativedelta(months=3),
        'Semi-Annual': relativedelta(months=6), 'Annual': relativedelta(years=1),
    }
    mb = max(2, min(6, int(months_back)))

    with get_db() as conn:
        df_future = pd.read_sql("""
            WITH RECURSIVE CategoryHierarchy AS (
                SELECT Categories_Id, Categories_Name::TEXT AS Full_Path, Categories_Id_Parent
                FROM Categories WHERE Categories_Id_Parent IS NULL
                UNION ALL
                SELECT c.Categories_Id, ch.Full_Path || ' : ' || c.Categories_Name, c.Categories_Id_Parent
                FROM Categories c JOIN CategoryHierarchy ch ON c.Categories_Id_Parent = ch.Categories_Id
            ),
            LatestFX AS (
                SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
                FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC
            )
            SELECT
                t.Date,
                p.Payees_Name,
                a.Accounts_Name,
                c.Currencies_ShortName AS currency,
                CASE WHEN c.Currencies_ShortName = 'EUR' THEN s.Amount
                     ELSE s.Amount * COALESCE(fx.FX_Rate, 1) END AS amount_eur,
                cat.Full_Path AS category
            FROM Transactions t
            JOIN Accounts a ON t.Accounts_Id = a.Accounts_Id
            JOIN Currencies c ON a.Currencies_Id = c.Currencies_Id
            LEFT JOIN Payees p ON t.Payees_Id = p.Payees_Id
            LEFT JOIN Splits s ON t.Transactions_Id = s.Transactions_Id
            LEFT JOIN CategoryHierarchy cat ON s.Categories_Id = cat.Categories_Id
            LEFT JOIN LatestFX fx ON fx.Currencies_Id_1 = c.Currencies_Id
            WHERE t.Date > CURRENT_DATE
              AND t.Transfers_Id IS NULL
            ORDER BY t.Date ASC
        """, conn)

        df_templates = pd.read_sql("""
            WITH RECURSIVE CategoryHierarchy AS (
                SELECT Categories_Id, Categories_Name::TEXT AS Full_Path, Categories_Id_Parent
                FROM Categories WHERE Categories_Id_Parent IS NULL
                UNION ALL
                SELECT c.Categories_Id, ch.Full_Path || ' : ' || c.Categories_Name, c.Categories_Id_Parent
                FROM Categories c JOIN CategoryHierarchy ch ON c.Categories_Id_Parent = ch.Categories_Id
            ),
            LatestFX AS (
                SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
                FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC
            ),
            tmpl_categories AS (
                SELECT rts.templates_id, STRING_AGG(DISTINCT c.Full_Path, ', ') AS category
                FROM Recurring_Template_Splits rts
                LEFT JOIN CategoryHierarchy c ON c.Categories_Id = rts.Categories_Id
                GROUP BY rts.templates_id
            )
            SELECT
                rt.templates_id AS template_id,
                COALESCE(py.Payees_Name, rt.name) AS payees_name,
                a.Accounts_Name AS accounts_name,
                tc.category,
                rt.total_amount AS amount,
                CASE WHEN cur.Currencies_ShortName = 'EUR' THEN rt.total_amount
                     ELSE rt.total_amount * COALESCE(fx.FX_Rate, 1) END AS amount_eur,
                cur.Currencies_ShortName AS currency,
                COALESCE(rt.next_due_date, CURRENT_DATE) AS next_due_date,
                rt.periodicity
            FROM Recurring_Templates rt
            JOIN Accounts a ON a.Accounts_Id = rt.accounts_id
            JOIN Currencies cur ON cur.Currencies_Id = a.Currencies_Id
            LEFT JOIN Payees py ON py.Payees_Id = rt.payees_id
            LEFT JOIN LatestFX fx ON fx.Currencies_Id_1 = a.Currencies_Id
            LEFT JOIN tmpl_categories tc ON tc.templates_id = rt.templates_id
            WHERE rt.active = TRUE
              AND rt.accounts_id_target IS NULL
              AND (rt.end_date IS NULL OR rt.end_date >= CURRENT_DATE)
        """, conn)

        df_recurring = pd.read_sql(f"""
            WITH RECURSIVE CategoryHierarchy AS (
                SELECT Categories_Id, Categories_Name::TEXT AS Full_Path, Categories_Id_Parent
                FROM Categories WHERE Categories_Id_Parent IS NULL
                UNION ALL
                SELECT c.Categories_Id, ch.Full_Path || ' : ' || c.Categories_Name, c.Categories_Id_Parent
                FROM Categories c JOIN CategoryHierarchy ch ON c.Categories_Id_Parent = ch.Categories_Id
            ),
            recent AS (
                SELECT
                    t.Payees_Id, p.Payees_Name,
                    s.Categories_Id, cat.Categories_Name,
                    DATE_TRUNC('month', t.Date)::date AS month_start,
                    t.Date,
                    SUM(s.Amount) AS amount,
                    a.Currencies_Id
                FROM Transactions t
                JOIN  Accounts a  ON a.Accounts_Id  = t.Accounts_Id
                LEFT JOIN Payees p ON p.Payees_Id   = t.Payees_Id
                LEFT JOIN Splits s ON s.Transactions_Id = t.Transactions_Id
                LEFT JOIN Categories cat ON cat.Categories_Id = s.Categories_Id
                WHERE t.Date >= DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '{mb} months'
                  AND t.Date <  DATE_TRUNC('month', CURRENT_DATE)
                  AND t.Payees_Id IS NOT NULL
                  AND t.Transfers_Id IS NULL
                GROUP BY t.Payees_Id, p.Payees_Name, s.Categories_Id, cat.Categories_Name,
                         t.Date, DATE_TRUNC('month', t.Date)::date, a.Currencies_Id
            ),
            qualified AS (
                SELECT Payees_Id, Categories_Id, Currencies_Id
                FROM   recent
                GROUP  BY Payees_Id, Categories_Id, Currencies_Id
                HAVING COUNT(DISTINCT month_start) = {mb}
            ),
            tx_freq AS (
                SELECT r.Payees_Id, r.Categories_Id, r.Currencies_Id,
                       COUNT(*)::float / {mb} AS avg_tx_per_month
                FROM recent r
                JOIN qualified q ON q.Payees_Id=r.Payees_Id AND q.Categories_Id=r.Categories_Id AND q.Currencies_Id=r.Currencies_Id
                GROUP BY r.Payees_Id, r.Categories_Id, r.Currencies_Id
            ),
            tx_lag AS (
                SELECT r.Payees_Id, r.Categories_Id, r.Currencies_Id,
                       (r.Date - LAG(r.Date) OVER (
                           PARTITION BY r.Payees_Id, r.Categories_Id, r.Currencies_Id ORDER BY r.Date
                       ))::float AS days_since_prev
                FROM recent r
                JOIN qualified q ON q.Payees_Id=r.Payees_Id AND q.Categories_Id=r.Categories_Id AND q.Currencies_Id=r.Currencies_Id
            ),
            interval_tx AS (
                SELECT Payees_Id, Categories_Id, Currencies_Id,
                       COALESCE(AVG(days_since_prev), 30) AS avg_interval
                FROM   tx_lag
                GROUP  BY Payees_Id, Categories_Id, Currencies_Id
            ),
            monthly_repr AS (
                SELECT r.Payees_Id, r.Payees_Name, r.Categories_Id, r.Categories_Name,
                       r.month_start, r.Currencies_Id, MIN(r.Date) AS repr_date
                FROM recent r
                JOIN qualified q ON q.Payees_Id=r.Payees_Id AND q.Categories_Id=r.Categories_Id AND q.Currencies_Id=r.Currencies_Id
                GROUP BY r.Payees_Id, r.Payees_Name, r.Categories_Id, r.Categories_Name, r.month_start, r.Currencies_Id
            ),
            monthly_lag AS (
                SELECT *,
                       (repr_date - LAG(repr_date) OVER (
                           PARTITION BY Payees_Id, Categories_Id, Currencies_Id ORDER BY month_start
                       ))::float AS days_since_prev
                FROM monthly_repr
            ),
            interval_monthly AS (
                SELECT Payees_Id, Categories_Id, Currencies_Id,
                       COALESCE(AVG(days_since_prev), 30) AS avg_interval
                FROM   monthly_lag
                GROUP  BY Payees_Id, Categories_Id, Currencies_Id
            ),
            amount_stats AS (
                SELECT r.Payees_Id, r.Categories_Id, r.Currencies_Id,
                       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY r.amount) AS median_amount,
                       MAX(r.Date) AS last_date
                FROM recent r
                JOIN qualified q ON q.Payees_Id=r.Payees_Id AND q.Categories_Id=r.Categories_Id AND q.Currencies_Id=r.Currencies_Id
                GROUP BY r.Payees_Id, r.Categories_Id, r.Currencies_Id
            ),
            names AS (
                SELECT DISTINCT ON (Payees_Id, Categories_Id, Currencies_Id)
                       Payees_Id, Payees_Name, Categories_Id, Categories_Name, Currencies_Id
                FROM   monthly_repr
            ),
            stats AS (
                SELECT n.Payees_Id, n.Payees_Name, n.Categories_Id, n.Categories_Name,
                       am.median_amount AS avg_amount,
                       CASE WHEN tf.avg_tx_per_month > 1.5 THEN it.avg_interval
                            ELSE im.avg_interval END AS avg_days_between,
                       am.last_date, n.Currencies_Id
                FROM names n
                JOIN amount_stats   am ON am.Payees_Id=n.Payees_Id AND am.Categories_Id=n.Categories_Id AND am.Currencies_Id=n.Currencies_Id
                JOIN tx_freq        tf ON tf.Payees_Id=n.Payees_Id AND tf.Categories_Id=n.Categories_Id AND tf.Currencies_Id=n.Currencies_Id
                JOIN interval_tx    it ON it.Payees_Id=n.Payees_Id AND it.Categories_Id=n.Categories_Id AND it.Currencies_Id=n.Currencies_Id
                JOIN interval_monthly im ON im.Payees_Id=n.Payees_Id AND im.Categories_Id=n.Categories_Id AND im.Currencies_Id=n.Currencies_Id
            ),
            fx AS (
                SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
                FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC
            )
            SELECT
                s.Payees_Name,
                COALESCE(cat.Full_Path, s.Categories_Name) AS category,
                ROUND(s.avg_days_between::numeric, 0)    AS avg_days_between,
                s.last_date,
                (s.last_date + ROUND(s.avg_days_between)::int)::date AS next_expected_date,
                c.Currencies_ShortName                   AS currency,
                ROUND((s.avg_amount * COALESCE(fx.FX_Rate, 1))::numeric, 2) AS avg_amount_eur
            FROM   stats s
            JOIN   Currencies c ON c.Currencies_Id    = s.Currencies_Id
            LEFT   JOIN fx      ON fx.Currencies_Id_1 = s.Currencies_Id
            LEFT   JOIN CategoryHierarchy cat ON cat.Categories_Id = s.Categories_Id
            ORDER  BY next_expected_date ASC
        """, conn)

    # Filter scheduled to horizon
    if not df_future.empty:
        df_future['date'] = pd.to_datetime(df_future['date'])
        df_f = df_future[df_future['date'].dt.date <= cutoff].copy()
    else:
        df_f = pd.DataFrame()

    # Project each active template forward from its own next_due_date, one step at a
    # time, for every occurrence that falls strictly after today and within the
    # horizon. next_due_date already reflects whatever generate_drafts() last
    # advanced it to, so this never re-projects an occurrence that's already been
    # materialized into an actual (draft or confirmed) Transactions row.
    template_rows = []
    if not df_templates.empty:
        df_templates['next_due_date'] = pd.to_datetime(df_templates['next_due_date'])
        today_ts = pd.Timestamp(today)
        cutoff_ts = pd.Timestamp(cutoff)
        for _, row in df_templates.iterrows():
            step = _PERIOD_STEP.get(str(row['periodicity']), relativedelta(months=1))
            occ = row['next_due_date']
            while occ <= today_ts:
                occ += step
            while occ <= cutoff_ts:
                template_rows.append({
                    'date': occ.date().isoformat(),
                    'payees_name': str(row['payees_name'] or ''),
                    'accounts_name': str(row['accounts_name'] or ''),
                    'category': str(row.get('category') or ''),
                    'amount_eur': float(row['amount_eur'] if pd.notna(row['amount_eur']) else 0),
                    'currency': str(row['currency'] or 'EUR'),
                    'periodicity': str(row['periodicity'] or ''),
                })
                occ += step
    template_rows.sort(key=lambda x: x['date'])

    # Deduplicate: drop statistically-detected recurring patterns whose payee already
    # has a scheduled transaction OR an active recurring template — those are already
    # represented accurately elsewhere, so counting them again here would double them up.
    if not df_recurring.empty:
        covered_payees = set()
        if not df_f.empty:
            covered_payees |= set(df_f['payees_name'].dropna().str.strip().str.lower().unique())
        if not df_templates.empty:
            covered_payees |= set(df_templates['payees_name'].dropna().str.strip().str.lower().unique())
        if covered_payees:
            df_recurring = df_recurring[
                ~df_recurring['payees_name'].str.strip().str.lower().isin(covered_payees)
            ].copy()

    # Project recurring patterns as concrete future occurrences
    recur_rows = []
    if not df_recurring.empty:
        df_recurring['next_expected_date'] = pd.to_datetime(df_recurring['next_expected_date'])
        today_ts = pd.Timestamp(today)
        cutoff_ts = pd.Timestamp(cutoff)
        for _, row in df_recurring.iterrows():
            avg_d = float(row['avg_days_between']) if pd.notna(row['avg_days_between']) else None
            if not avg_d or avg_d < 1:
                continue
            next_dt = row['next_expected_date']
            while next_dt <= today_ts:
                next_dt += pd.Timedelta(days=avg_d)
            while next_dt <= cutoff_ts:
                recur_rows.append({
                    'date': next_dt.date().isoformat(),
                    'payees_name': str(row['payees_name'] or ''),
                    'category': str(row.get('category') or ''),
                    'amount_eur': float(row['avg_amount_eur']),
                    'avg_days_between': int(round(avg_d)),
                    'currency': str(row['currency'] or 'EUR'),
                })
                next_dt += pd.Timedelta(days=avg_d)

    recur_rows.sort(key=lambda x: x['date'])

    # Build scheduled list
    scheduled = []
    if not df_f.empty:
        for _, row in df_f.iterrows():
            scheduled.append({
                'date': str(row['date'])[:10],
                'payees_name': str(row['payees_name'] or ''),
                'accounts_name': str(row['accounts_name'] or ''),
                'category': str(row.get('category') or ''),
                'amount_eur': float(row['amount_eur'] if pd.notna(row['amount_eur']) else 0),
                'currency': str(row['currency'] or 'EUR'),
            })

    sched_in  = sum(r['amount_eur'] for r in scheduled if r['amount_eur'] > 0)
    sched_out = sum(r['amount_eur'] for r in scheduled if r['amount_eur'] < 0)
    tmpl_in   = sum(r['amount_eur'] for r in template_rows if r['amount_eur'] > 0)
    tmpl_out  = sum(r['amount_eur'] for r in template_rows if r['amount_eur'] < 0)
    recur_in  = sum(r['amount_eur'] for r in recur_rows if r['amount_eur'] > 0)
    recur_out = sum(r['amount_eur'] for r in recur_rows if r['amount_eur'] < 0)

    return {
        'scheduled': scheduled,
        'templates': template_rows,
        'recurring': recur_rows,
        'metrics': {
            'sched_in':  round(sched_in,  2),
            'sched_out': round(sched_out, 2),
            'tmpl_in':   round(tmpl_in,   2),
            'tmpl_out':  round(tmpl_out,  2),
            'recur_in':  round(recur_in,  2),
            'recur_out': round(recur_out, 2),
            'net_total': round(sched_in + sched_out + tmpl_in + tmpl_out + recur_in + recur_out, 2),
        },
    }


@router.get("/budgets")
def get_budgets(year: int = Query(2024), month: Optional[int] = Query(None)):
    """Budget entries by category and year."""
    with get_db() as conn:
        df = pd.read_sql("""
            SELECT b.Budget_Id AS id, b.Year AS year, b.Categories_Id AS categories_id,
                   c.Categories_Name AS category_name, b.Budget_Amount AS budget_amount
            FROM Annual_Budgets b
            JOIN Categories c ON c.Categories_Id = b.Categories_Id
            WHERE b.Year = %(year)s
            ORDER BY c.Categories_Name
        """, conn, params={"year": year})
    return _df_to_list(df)


@router.post("/budgets")
def upsert_budget(data: dict):
    from database.connection import get_connection
    from fastapi import HTTPException
    conn = get_connection()
    try:
        cur = conn.cursor()
        bid = data.get('id')
        if bid:
            cur.execute(
                "UPDATE Annual_Budgets SET Budget_Amount=%s WHERE Budget_Id=%s",
                (data.get('budget_amount'), bid)
            )
        else:
            cur.execute("""
                INSERT INTO Annual_Budgets (Year, Categories_Id, Budget_Amount)
                VALUES (%s, %s, %s)
                ON CONFLICT (Year, Categories_Id) DO UPDATE SET Budget_Amount = EXCLUDED.Budget_Amount
                RETURNING Budget_Id
            """, (data.get('year'), data.get('categories_id'), data.get('budget_amount')))
            row = cur.fetchone()
            if row:
                bid = row[0]
        conn.commit()
        return {"id": bid}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.delete("/budgets/{bid}")
def delete_budget(bid: int):
    from database.connection import get_connection
    from fastapi import HTTPException
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM Annual_Budgets WHERE Budget_Id=%s", (bid,))
        conn.commit()
        return {"deleted": bid}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.get("/category-breakdown")
def get_category_breakdown(
    start_date: str = Query("2024-01-01"),
    end_date: str = Query("2099-12-31"),
):
    """Hierarchical category spending breakdown."""
    with get_db() as conn:
        df = pd.read_sql("""
        WITH RECURSIVE CategoryHierarchy AS (
            SELECT Categories_Id, Categories_Name::TEXT AS Full_Path,
                   Categories_Type::TEXT AS Categories_Type, Categories_Id_Parent, 0 AS Level
            FROM Categories WHERE Categories_Id_Parent IS NULL
            UNION ALL
            SELECT c.Categories_Id, ch.Full_Path || ' : ' || c.Categories_Name,
                   c.Categories_Type::TEXT, c.Categories_Id_Parent, ch.Level + 1
            FROM Categories c JOIN CategoryHierarchy ch ON c.Categories_Id_Parent = ch.Categories_Id
        )
        SELECT
            COALESCE(cat.Full_Path, 'Uncategorized') AS category,
            cat.Categories_Type AS type,
            SUM(ABS(COALESCE(s.Amount, t.Total_Amount))) AS total
        FROM Transactions t
        LEFT JOIN Splits s ON s.Transactions_Id = t.Transactions_Id
        LEFT JOIN CategoryHierarchy cat ON s.Categories_Id = cat.Categories_Id
        JOIN Accounts a ON t.Accounts_Id = a.Accounts_Id
        WHERE t.Date BETWEEN %(start_date)s AND %(end_date)s
          AND a.Accounts_Type IN ('Cash','Checking','Savings','Credit Card','Loan','Other')
          AND t.accounts_id_target IS NULL
        GROUP BY 1, 2
        ORDER BY total DESC
        """, conn, params={"start_date": start_date, "end_date": end_date})
    return _df_to_list(df)


# ── Net Worth by Account (pivot data) ────────────────────────────────────────
@router.get("/net-worth-by-account")
def get_net_worth_by_account(
    start_date: str = Query("2020-01-01"),
    end_date: str = Query("2099-12-31"),
    grouping: str = Query("month"),
):
    trunc_map = {"month": "month", "quarter": "quarter", "year": "year"}
    intv_map  = {"month": "1 month", "quarter": "3 months", "year": "1 year"}
    trunc_unit  = trunc_map.get(grouping, "month")
    pg_interval = intv_map.get(grouping, "1 month")

    eff_end = f"LEAST('{end_date}'::date, CURRENT_DATE)"
    query = f"""
    WITH
    period_dates AS (
        SELECT (gs - INTERVAL '1 day')::date AS period_end
        FROM generate_series(
            date_trunc('{trunc_unit}', '{start_date}'::date) + '{pg_interval}'::interval,
            date_trunc('{trunc_unit}', {eff_end}),
            '{pg_interval}'::interval
        ) gs
        UNION SELECT {eff_end} ORDER BY 1
    ),
    daily_fx AS (
        SELECT p.period_end, cur.Currencies_Id,
            (SELECT FX_Rate FROM Historical_FX WHERE Currencies_Id_1=cur.Currencies_Id AND Date<=p.period_end ORDER BY Date DESC LIMIT 1) AS fx_rate
        FROM period_dates p CROSS JOIN Currencies cur WHERE cur.Currencies_ShortName != 'EUR'
    ),
    cash_bal AS (
        SELECT p.period_end, a.Accounts_Name, a.Accounts_Type, a.Is_Active,
            CASE WHEN a.Accounts_Type IN ('Real Estate','Vehicle','Asset')
                 THEN GREATEST(0, a.Accounts_Balance - COALESCE((SELECT SUM(Total_Amount) FROM Transactions WHERE Accounts_Id=a.Accounts_Id AND Date>p.period_end),0))
                 ELSE a.Accounts_Balance - COALESCE((SELECT SUM(Total_Amount) FROM Transactions WHERE Accounts_Id=a.Accounts_Id AND Date>p.period_end),0)
            END * COALESCE((SELECT fx_rate FROM daily_fx WHERE period_end=p.period_end AND Currencies_Id=a.Currencies_Id),1) AS balance_eur
        FROM period_dates p CROSS JOIN Accounts a
        WHERE a.Accounts_Type NOT IN ('Brokerage','Margin','Pension','Other Investment')
    ),
    inv_universe AS (SELECT DISTINCT Securities_Id, Accounts_Id FROM Investments WHERE Action IN ('Buy','Reinvest','ShrIn','Sell','ShrOut')),
    inv_accounts AS (SELECT DISTINCT Accounts_Id FROM inv_universe),
    inv_bal AS (
        SELECT p.period_end, a.Accounts_Name, a.Accounts_Type, a.Is_Active,
            SUM(GREATEST(COALESCE((
                SELECT SUM(CASE WHEN Action IN ('Buy','Reinvest','ShrIn') THEN Quantity WHEN Action IN ('Sell','ShrOut') THEN -Quantity ELSE 0 END)
                FROM Investments i2 WHERE i2.Securities_Id=iu.Securities_Id AND i2.Accounts_Id=iu.Accounts_Id AND i2.Date<=p.period_end
            ),0),0) * COALESCE((SELECT Close FROM Historical_Prices WHERE Securities_Id=iu.Securities_Id AND Date<=p.period_end ORDER BY Date DESC LIMIT 1),0)
            * COALESCE((SELECT fx_rate FROM daily_fx WHERE period_end=p.period_end AND Currencies_Id=s.Currencies_Id),1)) AS balance_eur
        FROM period_dates p CROSS JOIN inv_universe iu
        JOIN Accounts a ON iu.Accounts_Id=a.Accounts_Id
        JOIN Securities s ON iu.Securities_Id=s.Securities_Id
        WHERE a.Accounts_Type IN ('Brokerage','Margin','Pension','Other Investment')
        GROUP BY p.period_end, a.Accounts_Name, a.Accounts_Type, a.Is_Active
    ),
    pension_bal AS (
        SELECT p.period_end, a.Accounts_Name, a.Accounts_Type, a.Is_Active,
            GREATEST(0, a.Accounts_Balance - COALESCE((
                SELECT SUM(CASE WHEN Action IN ('CashIn','IntInc') THEN Total_Amount_AccCur WHEN Action='CashOut' THEN -Total_Amount_AccCur ELSE 0 END)
                FROM Investments WHERE Accounts_Id=a.Accounts_Id AND Date>p.period_end
            ),0)) * COALESCE((SELECT fx_rate FROM daily_fx WHERE period_end=p.period_end AND Currencies_Id=a.Currencies_Id),1) AS balance_eur
        FROM period_dates p CROSS JOIN Accounts a
        WHERE a.Accounts_Type IN ('Pension','Other Investment')
          AND a.Accounts_Id NOT IN (SELECT Accounts_Id FROM inv_accounts)
    )
    SELECT period_end::text AS period, accounts_name, accounts_type, is_active,
           ROUND(COALESCE(balance_eur,0)::numeric,2) AS balance_eur
    FROM (SELECT * FROM cash_bal UNION ALL SELECT * FROM inv_bal UNION ALL SELECT * FROM pension_bal) combined
    WHERE balance_eur IS NOT NULL
    ORDER BY period_end, accounts_type, accounts_name
    """
    with get_db() as conn:
        df = pd.read_sql(query, conn)
    return _df_to_list(df)


# ── Investment Positions History ──────────────────────────────────────────────
@router.get("/investment-positions-history")
def get_investment_positions_history(start_date: str = Query("2020-01-01")):
    query = """
    WITH RECURSIVE months AS (
        SELECT (date_trunc('month', %(start_date)s::date) + INTERVAL '1 month' - INTERVAL '1 day')::date AS d
        UNION ALL
        SELECT (date_trunc('month', d + INTERVAL '1 month') + INTERVAL '1 month' - INTERVAL '1 day')::date
        FROM months WHERE d < date_trunc('month', CURRENT_DATE)
    ),
    dates AS (SELECT d FROM months WHERE d <= CURRENT_DATE UNION SELECT CURRENT_DATE::date),
    inv_universe AS (SELECT DISTINCT Securities_Id, Accounts_Id FROM Investments WHERE Action IN ('Buy','Reinvest','ShrIn','Sell','ShrOut')),
    qty_at AS (
        SELECT dt.d AS date_pt, iu.Securities_Id, iu.Accounts_Id,
            GREATEST(COALESCE((
                SELECT SUM(CASE WHEN Action IN ('Buy','Reinvest','ShrIn') THEN Quantity WHEN Action IN ('Sell','ShrOut') THEN -Quantity ELSE 0 END)
                FROM Investments WHERE Securities_Id=iu.Securities_Id AND Accounts_Id=iu.Accounts_Id AND Date<=dt.d
            ),0),0) AS qty_at_date
        FROM dates dt CROSS JOIN inv_universe iu
    )
    SELECT qa.date_pt::text AS date, a.Accounts_Name AS accounts_name,
        SUM(qa.qty_at_date
            * COALESCE((SELECT Close FROM Historical_Prices WHERE Securities_Id=qa.Securities_Id AND Date<=qa.date_pt ORDER BY Date DESC LIMIT 1),0)
            * COALESCE((SELECT FX_Rate FROM Historical_FX WHERE Currencies_Id_1=s.Currencies_Id AND Date<=qa.date_pt ORDER BY Date DESC LIMIT 1),1)
        ) AS value_eur
    FROM qty_at qa
    JOIN Accounts a ON qa.Accounts_Id=a.Accounts_Id
    JOIN Securities s ON qa.Securities_Id=s.Securities_Id
    WHERE qa.qty_at_date > 0
    GROUP BY qa.date_pt, a.Accounts_Name
    ORDER BY qa.date_pt ASC, a.Accounts_Name ASC
    """
    with get_db() as conn:
        df = pd.read_sql(query, conn, params={"start_date": start_date})
    return _df_to_list(df)


@router.get("/holdings-snapshot")
def get_holdings_snapshot(as_of: str = Query(None)):
    """Per-security holdings snapshot at a given date (default: today)."""
    as_of_date = as_of or "CURRENT_DATE"
    # Use parameter binding for user-supplied dates; fall back to CURRENT_DATE literal
    params: dict = {}
    if as_of:
        date_expr = "%(as_of)s::date"
        params["as_of"] = as_of
    else:
        date_expr = "CURRENT_DATE"
    query = f"""
    WITH inv_universe AS (
        SELECT DISTINCT Securities_Id, Accounts_Id
        FROM Investments WHERE Action IN ('Buy','Reinvest','ShrIn','Sell','ShrOut')
    ),
    qty_at AS (
        SELECT iu.Securities_Id, iu.Accounts_Id,
            GREATEST(COALESCE((
                SELECT SUM(CASE WHEN Action IN ('Buy','Reinvest','ShrIn') THEN Quantity
                               WHEN Action IN ('Sell','ShrOut') THEN -Quantity ELSE 0 END)
                FROM Investments
                WHERE Securities_Id = iu.Securities_Id
                  AND Accounts_Id   = iu.Accounts_Id
                  AND Date <= {date_expr}
            ), 0), 0) AS qty
        FROM inv_universe iu
    ),
    prices_at AS (
        SELECT DISTINCT ON (Securities_Id) Securities_Id, Close, Date AS price_date
        FROM Historical_Prices
        WHERE Date <= {date_expr}
        ORDER BY Securities_Id, Date DESC
    ),
    fx_at AS (
        SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
        FROM Historical_FX
        WHERE Date <= {date_expr}
        ORDER BY Currencies_Id_1, Date DESC
    )
    SELECT
        a.Accounts_Name                                            AS account,
        s.Securities_Name                                          AS security,
        s.Ticker                                                   AS ticker,
        s.Securities_Type                                          AS type,
        c.Currencies_ShortName                                     AS currency,
        q.qty                                                      AS quantity,
        COALESCE(p.Close, 0)                                       AS price,
        p.price_date::text                                         AS price_date,
        ROUND((q.qty * COALESCE(p.Close,0) * COALESCE(fx.FX_Rate,1))::numeric, 2) AS value_eur
    FROM qty_at q
    JOIN Accounts   a  ON a.Accounts_Id   = q.Accounts_Id
    JOIN Securities s  ON s.Securities_Id = q.Securities_Id
    JOIN Currencies c  ON c.Currencies_Id = s.Currencies_Id
    LEFT JOIN prices_at p  ON p.Securities_Id  = q.Securities_Id
    LEFT JOIN fx_at     fx ON fx.Currencies_Id_1 = s.Currencies_Id
    WHERE q.qty > 0
    ORDER BY a.Accounts_Name, value_eur DESC
    """
    with get_db() as conn:
        df = pd.read_sql(query, conn, params=params if params else None)
    return _df_to_list(df)


# ── Sector Allocation ─────────────────────────────────────────────────────────
@router.get("/sector-allocation")
def get_sector_allocation():
    query = """
    WITH fx AS (SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC),
    prices AS (SELECT DISTINCT ON (Securities_Id) Securities_Id, Close FROM Historical_Prices ORDER BY Securities_Id, Date DESC),
    holdings_value AS (
        SELECT COALESCE(NULLIF(TRIM(s.Sector),''),'Other / Unknown') AS sector,
               COALESCE(NULLIF(TRIM(s.Industry),''),'Other / Unknown') AS industry,
               s.Securities_Type::text AS securities_type,
               SUM(h.Quantity * COALESCE(p.Close,0) * COALESCE(fx.FX_Rate,1)) AS value_eur
        FROM Holdings h JOIN Securities s ON h.Securities_Id=s.Securities_Id
        LEFT JOIN prices p ON p.Securities_Id=h.Securities_Id
        LEFT JOIN fx ON fx.Currencies_Id_1=s.Currencies_Id
        WHERE h.Quantity > 0 GROUP BY sector, industry, securities_type
    ),
    total AS (SELECT SUM(value_eur) AS grand_total FROM holdings_value)
    SELECT hv.sector, hv.industry, hv.securities_type,
           ROUND(hv.value_eur::numeric,2) AS value_eur,
           ROUND((hv.value_eur/NULLIF(t.grand_total,0)*100)::numeric,2) AS actual_pct
    FROM holdings_value hv CROSS JOIN total t
    WHERE hv.value_eur > 0 ORDER BY hv.value_eur DESC
    """
    with get_db() as conn:
        df = pd.read_sql(query, conn)
    return _df_to_list(df)


# ── FX Exposure ───────────────────────────────────────────────────────────────
@router.get("/fx-exposure")
def get_fx_exposure():
    query = """
    WITH fx AS (SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC),
    prices AS (SELECT DISTINCT ON (Securities_Id) Securities_Id, Close FROM Historical_Prices ORDER BY Securities_Id, Date DESC),
    cash_exp AS (
        SELECT a.Currencies_Id, SUM(a.Accounts_Balance) AS native_exposure
        FROM Accounts a WHERE a.Is_Active=TRUE AND a.Accounts_Type NOT IN ('Brokerage','Margin') GROUP BY a.Currencies_Id
    ),
    inv_exp AS (
        SELECT s.Currencies_Id, SUM(h.Quantity * COALESCE(p.Close,0)) AS native_exposure
        FROM Holdings h JOIN Securities s ON h.Securities_Id=s.Securities_Id
        LEFT JOIN prices p ON p.Securities_Id=h.Securities_Id
        WHERE h.Quantity > 0 GROUP BY s.Currencies_Id
    ),
    combined AS (
        SELECT Currencies_Id, native_exposure FROM cash_exp
        UNION ALL SELECT Currencies_Id, native_exposure FROM inv_exp
    ),
    aggregated AS (SELECT Currencies_Id, SUM(native_exposure) AS native_exposure FROM combined GROUP BY Currencies_Id)
    SELECT c.Currencies_ShortName AS currency,
           ROUND(a.native_exposure::numeric,2) AS native_exposure,
           ROUND((a.native_exposure * COALESCE(fx.FX_Rate,1))::numeric,2) AS eur_exposure,
           ROUND((a.native_exposure * COALESCE(fx.FX_Rate,1) * 0.05)::numeric,2) AS sensitivity_5pct_eur
    FROM aggregated a JOIN Currencies c ON c.Currencies_Id=a.Currencies_Id
    LEFT JOIN fx ON fx.Currencies_Id_1=a.Currencies_Id
    ORDER BY ABS(a.native_exposure * COALESCE(fx.FX_Rate,1)) DESC
    """
    with get_db() as conn:
        df = pd.read_sql(query, conn)
    return _df_to_list(df)


# ── Spending by Payee ─────────────────────────────────────────────────────────
@router.get("/spending-by-payee")
def get_spending_by_payee(
    start_date: str = Query("2024-01-01"),
    end_date: str = Query("2099-12-31"),
    top_n: int = Query(20),
):
    query = """
    WITH fx AS (SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC)
    SELECT COALESCE(py.Payees_Name,'(Unknown)') AS payee,
           COUNT(DISTINCT t.Transactions_Id) AS tx_count,
           ABS(SUM(s.Amount * CASE WHEN cur.Currencies_ShortName='EUR' THEN 1 ELSE COALESCE(fx.FX_Rate,1) END)) AS amount_eur,
           MIN(t.Date)::text AS first_seen, MAX(t.Date)::text AS last_seen
    FROM Splits s
    JOIN Transactions t ON t.Transactions_Id=s.Transactions_Id
    JOIN Categories c ON c.Categories_Id=s.Categories_Id
    JOIN Accounts a ON a.Accounts_Id=t.Accounts_Id
    JOIN Currencies cur ON cur.Currencies_Id=a.Currencies_Id
    LEFT JOIN fx ON fx.Currencies_Id_1=a.Currencies_Id
    LEFT JOIN Payees py ON py.Payees_Id=t.Payees_Id
    WHERE c.Categories_Type='Expense'
      AND t.accounts_id_target IS NULL
      AND t.Date BETWEEN %(start_date)s AND %(end_date)s
      AND a.Accounts_Type IN ('Cash','Checking','Savings','Credit Card','Loan','Other')
    GROUP BY COALESCE(py.Payees_Name,'(Unknown)')
    ORDER BY amount_eur DESC
    LIMIT %(top_n)s
    """
    with get_db() as conn:
        df = pd.read_sql(query, conn, params={"start_date": start_date, "end_date": end_date, "top_n": top_n})
    return _df_to_list(df)


# ── Spending Trends ───────────────────────────────────────────────────────────
@router.get("/spending-trends")
def get_spending_trends(months: int = Query(12)):
    query = """
    WITH RECURSIVE cat_root AS (
        SELECT Categories_Id, Categories_Name::text AS top_category, Categories_Type::text AS cat_type
        FROM Categories WHERE Categories_Id_Parent IS NULL
        UNION ALL
        SELECT c.Categories_Id, cr.top_category, cr.cat_type
        FROM Categories c JOIN cat_root cr ON c.Categories_Id_Parent=cr.Categories_Id
    ),
    fx AS (SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC)
    SELECT DATE_TRUNC('month', t.Date)::date::text AS month,
           cr.top_category AS category,
           ABS(SUM(s.Amount * CASE WHEN cur.Currencies_ShortName='EUR' THEN 1 ELSE COALESCE(fx.FX_Rate,1) END)) AS amount_eur
    FROM Splits s
    JOIN Transactions t ON t.Transactions_Id=s.Transactions_Id
    JOIN cat_root cr ON cr.Categories_Id=s.Categories_Id
    JOIN Accounts a ON a.Accounts_Id=t.Accounts_Id
    JOIN Currencies cur ON cur.Currencies_Id=a.Currencies_Id
    LEFT JOIN fx ON fx.Currencies_Id_1=a.Currencies_Id
    WHERE cr.cat_type='Expense'
      AND t.accounts_id_target IS NULL
      AND a.Accounts_Type IN ('Cash','Checking','Savings','Credit Card','Loan','Other')
      AND t.Date >= (CURRENT_DATE - (%(months)s || ' months')::interval)
      AND t.Date < DATE_TRUNC('month', CURRENT_DATE)
    GROUP BY DATE_TRUNC('month', t.Date), cr.top_category
    ORDER BY month, category
    """
    with get_db() as conn:
        df = pd.read_sql(query, conn, params={"months": months})
    return _df_to_list(df)


# ── Savings Rate Detail (dual-axis chart) ─────────────────────────────────────
@router.get("/savings-rate-detail")
def get_savings_rate_detail(months: int = Query(24)):
    query = """
    WITH fx AS (
        SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
        FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC
    ),
    splits_cat AS (
        SELECT
            DATE_TRUNC('month', t.Date)::date AS month,
            c.Categories_Type,
            s.Amount *
                CASE WHEN cur.Currencies_ShortName = 'EUR' THEN 1
                     ELSE COALESCE(fx.FX_Rate, 1) END AS amount_eur
        FROM Splits s
        JOIN Transactions t ON t.Transactions_Id = s.Transactions_Id
        JOIN Categories c   ON c.Categories_Id   = s.Categories_Id
        JOIN Accounts a     ON a.Accounts_Id      = t.Accounts_Id
        JOIN Currencies cur ON cur.Currencies_Id  = a.Currencies_Id
        LEFT JOIN fx        ON fx.Currencies_Id_1 = a.Currencies_Id
        WHERE t.Transfers_Id IS NULL
          AND c.Categories_Type NOT IN ('Transfer','Trading','Investment')
          AND t.Date < DATE_TRUNC('month', CURRENT_DATE)
          AND t.Date >= DATE_TRUNC('month', CURRENT_DATE) - (%(months)s || ' months')::INTERVAL
    )
    SELECT
        month,
        ROUND(SUM(CASE WHEN Categories_Type IN ('Income','Dividend','Interest') THEN amount_eur ELSE 0 END)::numeric, 2) AS income_eur,
        ROUND(ABS(SUM(CASE WHEN Categories_Type NOT IN ('Income','Dividend','Interest') THEN amount_eur ELSE 0 END))::numeric, 2) AS expenses_eur,
        ROUND((SUM(CASE WHEN Categories_Type IN ('Income','Dividend','Interest') THEN amount_eur ELSE 0 END)
             - ABS(SUM(CASE WHEN Categories_Type NOT IN ('Income','Dividend','Interest') THEN amount_eur ELSE 0 END)))::numeric, 2) AS savings_eur,
        CASE WHEN SUM(CASE WHEN Categories_Type IN ('Income','Dividend','Interest') THEN amount_eur ELSE 0 END) > 0
             THEN ROUND(((SUM(CASE WHEN Categories_Type IN ('Income','Dividend','Interest') THEN amount_eur ELSE 0 END)
                        - ABS(SUM(CASE WHEN Categories_Type NOT IN ('Income','Dividend','Interest') THEN amount_eur ELSE 0 END)))
                       / SUM(CASE WHEN Categories_Type IN ('Income','Dividend','Interest') THEN amount_eur ELSE 0 END) * 100)::numeric, 1)
             ELSE 0 END AS savings_rate_pct
    FROM splits_cat
    GROUP BY month
    ORDER BY month ASC
    """
    with get_db() as conn:
        df = pd.read_sql(query, conn, params={"months": months})
    return _df_to_list(df)


# ── Monthly portfolio values helper ───────────────────────────────────────────
def _get_monthly_portfolio_values(start_date: str, end_date: str, conn, account_ids: Optional[list] = None) -> pd.DataFrame:
    acct_clause = _acct_clause(account_ids, "Accounts_Id") if account_ids else ""
    query = f"""
    WITH RECURSIVE months AS (
        SELECT (date_trunc('month', %(start_date)s::date) + INTERVAL '1 month' - INTERVAL '1 day')::date AS d
        UNION ALL
        SELECT (date_trunc('month', d + INTERVAL '1 month') + INTERVAL '1 month' - INTERVAL '1 day')::date
        FROM months WHERE d < date_trunc('month', %(end_date)s::date)
    ),
    dates AS (SELECT d FROM months WHERE d <= %(end_date)s::date UNION SELECT %(end_date)s::date),
    inv_universe AS (SELECT DISTINCT Securities_Id, Accounts_Id FROM Investments WHERE Action IN ('Buy','Reinvest','ShrIn','Sell','ShrOut'){acct_clause}),
    qty_at AS (
        SELECT dt.d AS date_pt, iu.Securities_Id, iu.Accounts_Id,
            GREATEST(COALESCE((
                SELECT SUM(CASE WHEN Action IN ('Buy','Reinvest','ShrIn') THEN Quantity WHEN Action IN ('Sell','ShrOut') THEN -Quantity ELSE 0 END)
                FROM Investments WHERE Securities_Id=iu.Securities_Id AND Accounts_Id=iu.Accounts_Id AND Date<=dt.d
            ),0),0) AS qty_at_date
        FROM dates dt CROSS JOIN inv_universe iu
    )
    SELECT qa.date_pt::text AS date,
        SUM(qa.qty_at_date
            * COALESCE((SELECT Close FROM Historical_Prices WHERE Securities_Id=qa.Securities_Id AND Date<=qa.date_pt ORDER BY Date DESC LIMIT 1),0)
            * COALESCE((SELECT FX_Rate FROM Historical_FX WHERE Currencies_Id_1=s.Currencies_Id AND Date<=qa.date_pt ORDER BY Date DESC LIMIT 1),1)
        ) AS portfolio_value_eur
    FROM qty_at qa
    JOIN Securities s ON qa.Securities_Id=s.Securities_Id
    WHERE qa.qty_at_date > 0
    GROUP BY qa.date_pt ORDER BY qa.date_pt
    """
    return pd.read_sql(query, conn, params={"start_date": start_date, "end_date": end_date})


# ── TWR / MWR ─────────────────────────────────────────────────────────────────
def _xirr(cashflows: list, dates: list) -> float:
    """Solve for annualised IRR given irregular cash flows using Brent's method."""
    from datetime import date as _date
    if len(cashflows) < 2:
        return 0.0
    d0 = dates[0]
    years = [(d - d0).days / 365.25 for d in dates]

    def npv(r):
        if r <= -1:
            return float('inf')
        return sum(cf / (1 + r) ** t for cf, t in zip(cashflows, years))

    # Try a wide bracket first, fall back to sign-search
    try:
        import scipy.optimize as _opt
        return float(_opt.brentq(npv, -0.999, 50.0, maxiter=500, xtol=1e-6))
    except Exception:
        pass
    # Newton fallback
    r = 0.1
    for _ in range(200):
        f = npv(r)
        df = sum(-t * cf / (1 + r) ** (t + 1) for cf, t in zip(cashflows, years))
        if df == 0:
            break
        r -= f / df
        if abs(f) < 1e-8:
            break
    return round(r, 6) if -1 < r < 50 else 0.0


@router.get("/twr")
def get_twr(
    lookback_days: int = Query(756),
    account_ids: Optional[str] = Query(None),
):
    import numpy as np
    from datetime import date as _date, timedelta
    from database.queries import get_price_returns, get_portfolio_weights, get_investable_portfolio_value

    acct_ids = tuple(_parse_account_ids(account_ids)) if _parse_account_ids(account_ids) else None
    cf_acct_clause = _acct_clause(list(acct_ids) if acct_ids else [], "i.Accounts_Id")

    empty = {
        "twr_window_pct": 0, "twr_ann_pct": 0, "mwr_pct": None,
        "trading_days": 0, "date_from": None, "date_to": None,
        "chart": [], "cashflows": [], "insufficient": True,
    }

    # ── Daily TWR via price returns (same engine as Risk Metrics) ──────────────
    df_prices = get_price_returns(lookback_days, acct_ids)
    if df_prices is None or df_prices.empty:
        return empty

    daily_returns = df_prices.ffill(limit=5).pct_change(fill_method=None).dropna(how='all')
    if daily_returns.empty or len(daily_returns) < 5:
        return empty

    df_weights = get_portfolio_weights(acct_ids)
    if not df_weights.empty:
        wmap  = dict(zip(df_weights["ticker"], df_weights["weight"]))
        avail = [c for c in daily_returns.columns if c in wmap]
        if avail:
            w = pd.Series([wmap[t] for t in avail], index=avail)
            w = w / w.sum()
            port_returns = daily_returns[avail].fillna(0).dot(w)
        else:
            port_returns = daily_returns.mean(axis=1)
    else:
        port_returns = daily_returns.mean(axis=1)

    cum = (1 + port_returns).cumprod()
    twr_total = float(cum.iloc[-1]) - 1.0
    n_days = len(port_returns)
    twr_ann = float((1 + twr_total) ** (252 / n_days) - 1) if n_days >= 2 else twr_total

    chart = [
        {"date": str(d)[:10], "twr_cumulative_pct": round((v - 1) * 100, 4)}
        for d, v in cum.items()
    ]

    # ── MWR / XIRR (all-time, regardless of lookback) ─────────────────────────
    # Use Buy/Sell/Dividend/IntInc as the investor cash-flow series.
    # Buy/ShrIn   → investor cash out  → negative CF
    # Sell/ShrOut → investor cash in   → positive CF
    # Dividend/IntInc/RtrnCap   → positive CF
    # Terminal value (current portfolio) → positive CF
    # ShrIn/ShrOut must be included: this query can be scoped to a subset of
    # accounts via cf_acct_clause, and a same-security transfer crossing that
    # scope boundary is a real contribution/withdrawal for the scoped account,
    # not investment performance (same bug as the period-P&L cash_flows CTE).
    with get_db() as conn:
        cf_df = pd.read_sql(f"""
            SELECT i.Date::date AS cf_date,
                   i.Action,
                   acc.Accounts_Name AS account_name,
                   COALESCE(s.Securities_Name, '') AS security_name,
                   CASE
                     WHEN i.Action IN ('Buy','MiscExp','ShrIn') THEN
                       COALESCE(NULLIF(i.Total_Amount_AccCur,0),
                                i.Quantity * i.Price_Per_Share + COALESCE(i.Commission,0))
                          * COALESCE(
                              (SELECT FX_Rate FROM Historical_FX
                               WHERE Currencies_Id_1=acc.Currencies_Id AND Date<=i.Date
                               ORDER BY Date DESC LIMIT 1), 1)
                     WHEN i.Action IN ('Sell','Dividend','IntInc','Reinvest','RtrnCap','CashIn','CashOut','ShrOut') THEN
                       COALESCE(NULLIF(i.Total_Amount_AccCur,0),
                                i.Quantity * i.Price_Per_Share - COALESCE(i.Commission,0))
                          * COALESCE(
                              (SELECT FX_Rate FROM Historical_FX
                               WHERE Currencies_Id_1=acc.Currencies_Id AND Date<=i.Date
                               ORDER BY Date DESC LIMIT 1), 1)
                     ELSE 0
                   END AS amount_eur
            FROM Investments i
            JOIN Accounts acc ON acc.Accounts_Id=i.Accounts_Id
            LEFT JOIN Securities s ON s.Securities_Id=i.Securities_Id
            WHERE i.Action IN ('Buy','Sell','Dividend','IntInc','Reinvest','RtrnCap','MiscExp','CashIn','CashOut','ShrIn','ShrOut')
            {cf_acct_clause}
            ORDER BY i.Date
        """, conn)

    mwr_pct = None
    if not cf_df.empty:
        xirr_cfs: list = []
        xirr_dates: list = []
        for _, row in cf_df.iterrows():
            d = row["cf_date"]
            if hasattr(d, 'date'):
                d = d.date()
            amt = float(row["amount_eur"] or 0)
            action = str(row["action"])
            # Buy/MiscExp/ShrIn = cash out (negative); everything else = cash in (positive)
            if action in ('Buy', 'MiscExp', 'ShrIn'):
                xirr_cfs.append(-abs(amt))
            else:
                xirr_cfs.append(abs(amt))
            xirr_dates.append(d)
        # Terminal cash flow = current portfolio value
        port_val = float(get_investable_portfolio_value(acct_ids))
        xirr_cfs.append(port_val)
        xirr_dates.append(_date.today())
        if len(xirr_cfs) >= 2 and any(c < 0 for c in xirr_cfs) and any(c > 0 for c in xirr_cfs):
            r = _xirr(xirr_cfs, xirr_dates)
            mwr_pct = round(r * 100, 2)

    cashflows = []
    if not cf_df.empty:
        for _, row in cf_df.iterrows():
            cashflows.append({
                "date": str(row["cf_date"])[:10],
                "action": str(row["action"]),
                "account": str(row["account_name"]),
                "security": str(row["security_name"]),
                "amount_eur": float(row["amount_eur"] or 0),
            })

    return {
        "twr_window_pct": round(twr_total * 100, 2),
        "twr_ann_pct": round(twr_ann * 100, 2),
        "mwr_pct": mwr_pct,
        "trading_days": n_days,
        "date_from": str(port_returns.index[0])[:10],
        "date_to": str(port_returns.index[-1])[:10],
        "chart": chart,
        "cashflows": cashflows,
        "insufficient": n_days < 10,
    }


# ── Risk Metrics ──────────────────────────────────────────────────────────────
@router.get("/risk-metrics")
def get_risk_metrics(
    lookback_days: int = Query(756),
    benchmark_sec_id: Optional[int] = Query(None),
    account_ids: Optional[str] = Query(None),
):
    import numpy as np
    from database.queries import (
        get_price_returns, get_portfolio_weights,
        get_investable_portfolio_value, get_benchmark_returns,
    )
    acct_ids = tuple(_parse_account_ids(account_ids)) if _parse_account_ids(account_ids) else None

    df_prices = get_price_returns(lookback_days, acct_ids)
    empty = {"ann_vol_pct": None, "sharpe": None, "sortino": None, "max_drawdown_pct": None,
             "var_95_pct": None, "cvar_95_pct": None, "var_95_eur": None, "cvar_95_eur": None,
             "beta": None, "alpha": None, "trading_days": 0, "date_from": None, "date_to": None,
             "portfolio_value": 0, "rolling_sharpe": [], "insufficient": True}

    if df_prices is None or df_prices.empty or df_prices.shape[1] < 1:
        return empty

    daily_returns = df_prices.ffill(limit=5).pct_change(fill_method=None).dropna(how='all')
    if daily_returns.empty or len(daily_returns) < 10:
        return empty

    df_weights = get_portfolio_weights(acct_ids)
    if not df_weights.empty:
        wmap  = dict(zip(df_weights["ticker"], df_weights["weight"]))
        avail = [c for c in daily_returns.columns if c in wmap]
        if avail:
            w = pd.Series([wmap[t] for t in avail], index=avail)
            w = w / w.sum()
            port_returns = daily_returns[avail].fillna(0).dot(w)
        else:
            port_returns = daily_returns.mean(axis=1)
    else:
        port_returns = daily_returns.mean(axis=1)

    portfolio_value = float(get_investable_portfolio_value(acct_ids))

    rf_rate    = 0.03
    ann_vol    = float(port_returns.std() * np.sqrt(252))
    ann_return = float((1 + port_returns.mean()) ** 252 - 1)
    excess     = ann_return - rf_rate
    sharpe     = excess / ann_vol if ann_vol > 0 else 0.0

    down_ret = port_returns[port_returns < 0]
    down_dev = float(down_ret.std() * np.sqrt(252)) if len(down_ret) > 0 else 0.0
    sortino  = excess / down_dev if down_dev > 0 else 0.0

    cum_ret  = (1 + port_returns).cumprod()
    roll_max = cum_ret.cummax()
    drawdown = (cum_ret - roll_max) / roll_max
    max_dd   = float(drawdown.min())

    var_95      = float(np.percentile(port_returns, 5))
    tail        = port_returns[port_returns <= var_95]
    cvar_95     = float(tail.mean()) if len(tail) > 0 else var_95
    var_95_eur  = round(abs(var_95)  * portfolio_value, 0)
    cvar_95_eur = round(abs(cvar_95) * portfolio_value, 0)

    beta  = None
    alpha = None
    if benchmark_sec_id is not None:
        bench_prices = get_benchmark_returns(benchmark_sec_id, lookback_days)
        if not bench_prices.empty:
            all_dates     = port_returns.index.union(bench_prices.index).sort_values()
            bench_aligned = bench_prices.reindex(all_dates).ffill().reindex(port_returns.index)
            bench_ret     = bench_aligned.pct_change(fill_method=None).dropna()
            common_idx    = port_returns.index.intersection(bench_ret.index)
            if len(common_idx) >= 30:
                p = port_returns.loc[common_idx].values
                b = bench_ret.loc[common_idx].values
                bench_var = float(np.var(b))
                if bench_var > 0:
                    beta  = round(float(np.cov(p, b)[0, 1] / bench_var), 3)
                    bench_ann_ret = float((1 + bench_ret.mean()) ** 252 - 1)
                    alpha = round(float(ann_return - (rf_rate + beta * (bench_ann_ret - rf_rate))) * 100, 2)

    n_days    = len(port_returns)
    date_from = port_returns.index.min().strftime("%Y-%m-%d")
    date_to   = port_returns.index.max().strftime("%Y-%m-%d")
    insufficient = n_days < lookback_days * 0.5

    rolling_sharpe = port_returns.rolling(30).apply(
        lambda x: (x.mean() * 252 - rf_rate) / (x.std() * np.sqrt(252)) if x.std() > 0 else 0,
        raw=True,
    )
    rs_df = pd.DataFrame({"date": port_returns.index.strftime("%Y-%m-%d"), "sharpe": rolling_sharpe.round(4)}).dropna()

    return {
        "ann_vol_pct":       round(ann_vol * 100, 2),
        "sharpe":            round(sharpe, 3),
        "sortino":           round(sortino, 3),
        "max_drawdown_pct":  round(max_dd * 100, 2),
        "var_95_pct":        round(var_95 * 100, 2),
        "cvar_95_pct":       round(cvar_95 * 100, 2),
        "var_95_eur":        var_95_eur,
        "cvar_95_eur":       cvar_95_eur,
        "beta":              beta,
        "alpha":             alpha,
        "trading_days":      n_days,
        "date_from":         date_from,
        "date_to":           date_to,
        "portfolio_value":   round(portfolio_value, 0),
        "rolling_sharpe":    rs_df.to_dict(orient="records"),
        "insufficient":      insufficient,
    }


# ── Benchmark candidates (for Risk Metrics benchmark selector) ────────────────
@router.get("/benchmark-candidates")
def get_benchmark_candidates_endpoint(min_days: int = Query(30)):
    from database.queries import get_benchmark_candidates
    df = get_benchmark_candidates(min_days=min_days)
    return _df_to_list(df)


# ── Tax-Loss Harvesting ───────────────────────────────────────────────────────
@router.get("/tax-loss-harvesting")
def get_tax_loss_harvesting():
    query = """
    WITH fx AS (SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC),
    latest_price AS (SELECT DISTINCT ON (Securities_Id) Securities_Id, Close FROM Historical_Prices ORDER BY Securities_Id, Date DESC)
    SELECT s.Securities_Id AS securities_id, s.Securities_Name, s.Securities_Type::text, s.Ticker,
           SUM(h.Quantity) AS quantity,
           lp.Close AS current_price,
           AVG(h.Fifo_Avg_Price) AS cost_basis,
           ROUND((SUM(h.Quantity * lp.Close) * COALESCE(fx.FX_Rate,1))::numeric,2) AS current_value_eur,
           ROUND((SUM(h.Quantity * h.Fifo_Avg_Price) * COALESCE(fx.FX_Rate,1))::numeric,2) AS cost_basis_eur,
           ROUND(((SUM(h.Quantity * lp.Close) - SUM(h.Quantity * h.Fifo_Avg_Price)) * COALESCE(fx.FX_Rate,1))::numeric,2) AS unrealized_loss_eur,
           ROUND(((SUM(h.Quantity * lp.Close) - SUM(h.Quantity * h.Fifo_Avg_Price)) / NULLIF(SUM(h.Quantity * h.Fifo_Avg_Price),0) * 100)::numeric,2) AS loss_pct
    FROM Holdings h
    JOIN Securities s ON s.Securities_Id=h.Securities_Id
    LEFT JOIN latest_price lp ON lp.Securities_Id=h.Securities_Id
    LEFT JOIN fx ON fx.Currencies_Id_1=s.Currencies_Id
    WHERE h.Quantity > 0
    GROUP BY s.Securities_Id, s.Securities_Name, s.Securities_Type, s.Ticker, lp.Close, fx.FX_Rate
    HAVING (SUM(h.Quantity * lp.Close) - SUM(h.Quantity * h.Fifo_Avg_Price)) * COALESCE(fx.FX_Rate,1) < 0
    ORDER BY unrealized_loss_eur ASC
    """
    with get_db() as conn:
        df = pd.read_sql(query, conn)
    return _df_to_list(df)


# ── Dividend Income for Tax ───────────────────────────────────────────────────
@router.get("/dividend-income-tax")
def get_dividend_income_tax(year: int = Query(None)):
    """Investment income (dividends, reinvested, interest, RtrnCap) for a tax year.
    Uses effective tax category (instrument-type override > security tax_category) to:
    - Route CD/Bond IntInc to 'interest' section, others to 'dividend'
    - Exclude Reinvest for tax-exempt categories (UCITS, Local Listed, Foreign Listed)
    - Compute local_tax_liability = max(0, gross * local_rate% - abs(withholding))
    """
    from datetime import date as _date
    if year is None:
        year = _date.today().year
    query = """
    WITH eff AS (
        -- Effective tax category: instrument-type override wins over security's category
        SELECT
            i.Investments_Id,
            COALESCE(ito.Tax_Category_Override, s.Tax_Category) AS eff_tax_cat
        FROM Investments i
        JOIN Securities s ON s.Securities_Id = i.Securities_Id
        LEFT JOIN Instrument_Type_Tax_Override ito
               ON ito.Instrument_Type = i.Instrument_Type::text
        WHERE i.Action IN ('Dividend','IntInc','Reinvest','RtrnCap')
          AND EXTRACT(YEAR FROM i.Date) = %(year)s
          AND i.Total_Amount_AccCur <> 0
    )
    SELECT
        i.Date::text                        AS date,
        s.Securities_Id                     AS securities_id,
        s.Securities_Name                   AS securities_name,
        a.Accounts_Name                     AS account_name,
        i.Action                            AS action,
        eff.eff_tax_cat                     AS tax_category,
        COALESCE(s.Is_Tax_Exempt, FALSE)    AS is_tax_exempt,
        -- Section: CD/Bond IntInc → 'interest', everything else → 'dividend'
        CASE
            WHEN i.Action = 'IntInc'
             AND eff.eff_tax_cat IN ('CD','Bond') THEN 'interest'
            ELSE 'dividend'
        END AS section,
        -- Tax rates from the effective tax category rules
        tcr.Dividend_Local_Tax_Rate         AS dividend_local_tax_rate,
        tcr.Income_Tax_Rate                 AS income_tax_rate,
        -- amount_eur: convert Total_Amount_AccCur using the *account* currency
        CASE
            WHEN ca.Currencies_ShortName = 'EUR'
                THEN i.Total_Amount_AccCur
            ELSE i.Total_Amount_AccCur * COALESCE(
                    (SELECT fx.FX_Rate FROM Historical_FX fx
                     WHERE fx.Currencies_Id_1 = ca.Currencies_Id AND fx.Date <= i.Date
                     ORDER BY fx.Date DESC LIMIT 1),
                    (SELECT fx.FX_Rate FROM Historical_FX fx
                     WHERE fx.Currencies_Id_1 = ca.Currencies_Id
                     ORDER BY fx.Date ASC LIMIT 1),
                    1.0)
        END AS amount_eur,
        i.Tax_Amount AS tax_amount,
        -- tax_amount_eur: same FX logic on Tax_Amount (stored in account currency)
        CASE
            WHEN i.Tax_Amount IS NULL THEN NULL
            WHEN ca.Currencies_ShortName = 'EUR'
                THEN i.Tax_Amount
            ELSE i.Tax_Amount * COALESCE(
                    (SELECT fx.FX_Rate FROM Historical_FX fx
                     WHERE fx.Currencies_Id_1 = ca.Currencies_Id AND fx.Date <= i.Date
                     ORDER BY fx.Date DESC LIMIT 1),
                    (SELECT fx.FX_Rate FROM Historical_FX fx
                     WHERE fx.Currencies_Id_1 = ca.Currencies_Id
                     ORDER BY fx.Date ASC LIMIT 1),
                    1.0)
        END AS tax_amount_eur
    FROM Investments i
    JOIN Securities   s   ON s.Securities_Id  = i.Securities_Id
    JOIN Accounts     a   ON a.Accounts_Id    = i.Accounts_Id
    JOIN Currencies   ca  ON ca.Currencies_Id = a.Currencies_Id
    JOIN eff              ON eff.Investments_Id = i.Investments_Id
    LEFT JOIN Tax_Category_Rules tcr ON tcr.Tax_Category = eff.eff_tax_cat
    WHERE i.Action IN ('Dividend','IntInc','Reinvest','RtrnCap')
      AND EXTRACT(YEAR FROM i.Date) = %(year)s
      AND i.Total_Amount_AccCur <> 0
      -- Exclude Reinvest for categories where it is not taxable
      AND NOT (
          i.Action = 'Reinvest'
          AND COALESCE(tcr.Reinvest_Taxable, FALSE) = FALSE
      )
    ORDER BY i.Date DESC, s.Securities_Name
    """
    # Bond/T-bill maturity interest: Sell of Show_In_Capital_Gains=FALSE bonds
    # The "gain" (proceeds - cost) is the interest at maturity, not a capital gain.
    maturity_query = """
    WITH buy_cost AS (
        SELECT i.Securities_Id, i.Accounts_Id,
               SUM(ABS(i.Total_Amount_AccCur)) AS total_cost
        FROM Investments i
        JOIN Securities s ON s.Securities_Id = i.Securities_Id
        JOIN Tax_Category_Rules tcr ON tcr.Tax_Category = s.Tax_Category
        WHERE i.Action = 'Buy'
          AND COALESCE(tcr.Show_In_Capital_Gains, TRUE) = FALSE
          AND s.Tax_Category != 'CD'  -- CDs already have IntInc, skip
        GROUP BY i.Securities_Id, i.Accounts_Id
    )
    SELECT
        i.Date::text                        AS date,
        s.Securities_Id                     AS securities_id,
        s.Securities_Name                   AS securities_name,
        a.Accounts_Name                     AS account_name,
        'MaturityInc'                       AS action,
        s.Tax_Category                      AS tax_category,
        COALESCE(s.Is_Tax_Exempt, FALSE)    AS is_tax_exempt,
        'interest'                          AS section,
        NULL::numeric                       AS dividend_local_tax_rate,
        tcr.Income_Tax_Rate                 AS income_tax_rate,
        -- Interest = proceeds - cost basis
        ABS(i.Total_Amount_AccCur) - COALESCE(bc.total_cost, 0) AS amount_eur,
        NULL::numeric                       AS tax_amount,
        NULL::numeric                       AS tax_amount_eur
    FROM Investments i
    JOIN Securities s ON s.Securities_Id = i.Securities_Id
    JOIN Accounts   a ON a.Accounts_Id   = i.Accounts_Id
    JOIN Tax_Category_Rules tcr ON tcr.Tax_Category = s.Tax_Category
    LEFT JOIN buy_cost bc ON bc.Securities_Id = i.Securities_Id
                         AND bc.Accounts_Id   = i.Accounts_Id
    WHERE i.Action IN ('Sell','Expire')
      AND EXTRACT(YEAR FROM i.Date) = %(year)s
      AND COALESCE(tcr.Show_In_Capital_Gains, TRUE) = FALSE
      AND s.Tax_Category != 'CD'
      AND ABS(i.Total_Amount_AccCur) - COALESCE(bc.total_cost, 0) > 0.005
    ORDER BY i.Date DESC, s.Securities_Name
    """
    with get_db() as conn:
        df = pd.read_sql(query, conn, params={"year": year})
        df_mat = pd.read_sql(maturity_query, conn, params={"year": year})
    if not df_mat.empty:
        df = pd.concat([df, df_mat], ignore_index=True)
        df = df.sort_values('date', ascending=False)
    # Compute local_tax_liability in Python: max(0, gross_eur * rate/100 - abs(withholding_eur))
    def _local_liability(row):
        if row.get('action') == 'RtrnCap':
            return None  # not income; reduces cost basis — no tax liability
        rate = row.get('dividend_local_tax_rate')
        gross = row.get('amount_eur') or 0
        wht = abs(row.get('tax_amount_eur') or 0)
        if rate is None or gross <= 0:
            return None
        return max(0.0, round(gross * float(rate) / 100 - wht, 4))
    def _income_tax_liability(row):
        """For CD/Bond interest rows: max(0, gross * income_tax_rate% - abs(wht))."""
        if row.get('section') != 'interest' or row.get('is_tax_exempt'):
            return None
        rate = row.get('income_tax_rate')
        gross = row.get('amount_eur') or 0
        wht = abs(row.get('tax_amount_eur') or 0)
        if rate is None or gross <= 0:
            return None
        return max(0.0, round(gross * float(rate) / 100 - wht, 4))

    records = _df_to_list(df)
    for r in records:
        r['local_tax_liability'] = _local_liability(r)
        r['income_tax_liability'] = _income_tax_liability(r)
    return records


@router.get("/bank-interest-tax")
def get_bank_interest_tax(year: int = Query(None)):
    """Bank & Savings interest from non-investment accounts for a tax year.
    Mirrors Streamlit get_bank_interest_report.
    """
    from datetime import date as _date
    if year is None:
        year = _date.today().year
    query = """
    WITH interest_splits AS (
        -- Interest income splits (positive amounts in Interest/income-interest categories)
        SELECT
            s.Splits_Id,
            s.Transactions_Id,
            s.Amount,
            c.Categories_Name  AS category,
            SUM(s.Amount) OVER (PARTITION BY s.Transactions_Id) AS total_interest_in_txn
        FROM Splits s
        JOIN Categories c ON c.Categories_Id = s.Categories_Id
        WHERE s.Amount > 0
          AND (
              c.Categories_Type = 'Interest'
              OR (c.Categories_Type IN ('Income','Dividend')
                  AND LOWER(c.Categories_Name) LIKE '%%interest%%')
          )
    ),
    tax_splits AS (
        -- Total tax withheld per transaction (negative splits in Tax categories)
        SELECT
            s.Transactions_Id,
            SUM(s.Amount) AS tax_amount
        FROM Splits s
        JOIN Categories c ON c.Categories_Id = s.Categories_Id
        WHERE s.Amount < 0
          AND (
              c.Categories_Type = 'Tax'
              OR LOWER(c.Categories_Name) LIKE '%%tax%%'
          )
        GROUP BY s.Transactions_Id
    )
    SELECT
        t.Date::text                    AS date,
        a.Accounts_Name                 AS account_name,
        a.Accounts_Type                 AS account_type,
        COALESCE(p.Payees_Name, '—')    AS payee,
        i.category                      AS category,
        cur.Currencies_ShortName        AS currency,
        i.Amount * CASE
            WHEN cur.Currencies_ShortName = 'EUR' THEN 1.0
            ELSE COALESCE(
                (SELECT hfx.FX_Rate FROM Historical_FX hfx
                 WHERE hfx.Currencies_Id_1 = cur.Currencies_Id AND hfx.Date <= t.Date
                 ORDER BY hfx.Date DESC LIMIT 1),
                1.0)
        END                             AS amount_eur,
        -- Distribute tax proportionally: each split gets tax * (split / total_interest)
        (ts.tax_amount * (i.Amount / NULLIF(i.total_interest_in_txn, 0))) * CASE
            WHEN cur.Currencies_ShortName = 'EUR' THEN 1.0
            ELSE COALESCE(
                (SELECT hfx.FX_Rate FROM Historical_FX hfx
                 WHERE hfx.Currencies_Id_1 = cur.Currencies_Id AND hfx.Date <= t.Date
                 ORDER BY hfx.Date DESC LIMIT 1),
                1.0)
        END                             AS tax_amount_eur
    FROM interest_splits i
    JOIN Transactions t   ON t.Transactions_Id = i.Transactions_Id
    JOIN Accounts     a   ON a.Accounts_Id     = t.Accounts_Id
    JOIN Currencies   cur ON cur.Currencies_Id = a.Currencies_Id
    LEFT JOIN Payees  p   ON p.Payees_Id       = t.Payees_Id
    LEFT JOIN tax_splits ts ON ts.Transactions_Id = t.Transactions_Id
    WHERE t.Transfers_Id IS NULL
      AND a.Accounts_Type NOT IN ('Brokerage','Pension','Other Investment','Margin')
      AND EXTRACT(YEAR FROM t.Date) = %(year)s
    ORDER BY t.Date DESC, a.Accounts_Name
    """
    with get_db() as conn:
        df = pd.read_sql(query, conn, params={"year": year})
    return _df_to_list(df)


# ── Price Changes ─────────────────────────────────────────────────────────────
@router.get("/price-changes")
def get_price_changes():
    query = """
    WITH latest AS (SELECT DISTINCT ON (Securities_Id) Securities_Id, Close AS price_today FROM Historical_Prices ORDER BY Securities_Id, Date DESC),
    periods AS (
        SELECT
           (CURRENT_DATE - INTERVAL '1 day')::date AS dtd,
           (date_trunc('week', CURRENT_DATE) - INTERVAL '1 day')::date AS wtd,
           (date_trunc('month', CURRENT_DATE) - INTERVAL '1 day')::date AS mtd,
           (date_trunc('quarter', CURRENT_DATE) - INTERVAL '1 day')::date AS qtd,
           (date_trunc('year', CURRENT_DATE) - INTERVAL '1 day')::date AS ytd
    )
    SELECT s.Securities_Id AS securities_id, s.Securities_Name, s.Ticker, s.Securities_Type::text,
           l.price_today,
           ROUND(((l.price_today - p_dtd.Close)/NULLIF(p_dtd.Close,0)*100)::numeric,2) AS dtd_pct,
           ROUND(((l.price_today - p_wtd.Close)/NULLIF(p_wtd.Close,0)*100)::numeric,2) AS wtd_pct,
           ROUND(((l.price_today - p_mtd.Close)/NULLIF(p_mtd.Close,0)*100)::numeric,2) AS mtd_pct,
           ROUND(((l.price_today - p_qtd.Close)/NULLIF(p_qtd.Close,0)*100)::numeric,2) AS qtd_pct,
           ROUND(((l.price_today - p_ytd.Close)/NULLIF(p_ytd.Close,0)*100)::numeric,2) AS ytd_pct,
           ROUND((h.Quantity * l.price_today * COALESCE(fx.FX_Rate,1))::numeric,2) AS value_eur
    FROM Holdings h
    JOIN Securities s ON s.Securities_Id=h.Securities_Id
    JOIN latest l ON l.Securities_Id=h.Securities_Id
    CROSS JOIN periods per
    LEFT JOIN LATERAL (SELECT Close FROM Historical_Prices WHERE Securities_Id=h.Securities_Id AND Date<=per.dtd ORDER BY Date DESC LIMIT 1) p_dtd ON true
    LEFT JOIN LATERAL (SELECT Close FROM Historical_Prices WHERE Securities_Id=h.Securities_Id AND Date<=per.wtd ORDER BY Date DESC LIMIT 1) p_wtd ON true
    LEFT JOIN LATERAL (SELECT Close FROM Historical_Prices WHERE Securities_Id=h.Securities_Id AND Date<=per.mtd ORDER BY Date DESC LIMIT 1) p_mtd ON true
    LEFT JOIN LATERAL (SELECT Close FROM Historical_Prices WHERE Securities_Id=h.Securities_Id AND Date<=per.qtd ORDER BY Date DESC LIMIT 1) p_qtd ON true
    LEFT JOIN LATERAL (SELECT Close FROM Historical_Prices WHERE Securities_Id=h.Securities_Id AND Date<=per.ytd ORDER BY Date DESC LIMIT 1) p_ytd ON true
    LEFT JOIN LATERAL (SELECT FX_Rate FROM Historical_FX WHERE Currencies_Id_1=s.Currencies_Id ORDER BY Date DESC LIMIT 1) fx ON true
    WHERE h.Quantity > 0
    ORDER BY value_eur DESC NULLS LAST
    """
    with get_db() as conn:
        df = pd.read_sql(query, conn)
    return _df_to_list(df)


# ── Goals ─────────────────────────────────────────────────────────────────────
def _ensure_goals_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS Goals (
            Goal_Id SERIAL PRIMARY KEY,
            Goal_Name VARCHAR(200) NOT NULL,
            Target_Amount NUMERIC(15,2) NOT NULL,
            Target_Date DATE,
            Current_Amount NUMERIC(15,2) DEFAULT 0,
            Notes TEXT,
            Is_Active BOOLEAN DEFAULT TRUE,
            Created_At TIMESTAMP DEFAULT NOW()
        )
    """)


@router.get("/goals")
def get_goals():
    conn = get_connection()
    try:
        cur = conn.cursor()
        _ensure_goals_table(cur)
        conn.commit()
    finally:
        conn.close()
    with get_db() as conn:
        df = pd.read_sql("""
            SELECT Goal_Id AS goal_id, Goal_Name AS goal_name,
                   Target_Amount AS target_amount, Current_Amount AS current_amount,
                   Target_Date::text AS target_date, Notes AS notes,
                   CASE WHEN Target_Amount > 0 THEN ROUND((Current_Amount / Target_Amount * 100)::numeric,1) ELSE 0 END AS progress_pct
            FROM Goals WHERE Is_Active=TRUE ORDER BY Target_Date ASC NULLS LAST, Goal_Id
        """, conn)
    return _df_to_list(df)


@router.post("/goals")
def upsert_goal(data: dict):
    conn = get_connection()
    try:
        cur = conn.cursor()
        _ensure_goals_table(cur)
        gid = data.get("goal_id")
        if gid:
            cur.execute("""
                UPDATE Goals SET Goal_Name=%s, Target_Amount=%s, Target_Date=%s,
                    Current_Amount=%s, Notes=%s WHERE Goal_Id=%s
            """, (data.get("goal_name"), data.get("target_amount"),
                  data.get("target_date") or None, data.get("current_amount", 0),
                  data.get("notes") or None, gid))
        else:
            cur.execute("""
                INSERT INTO Goals (Goal_Name, Target_Amount, Target_Date, Current_Amount, Notes)
                VALUES (%s, %s, %s, %s, %s) RETURNING Goal_Id
            """, (data.get("goal_name"), data.get("target_amount"),
                  data.get("target_date") or None, data.get("current_amount", 0),
                  data.get("notes") or None))
            gid = cur.fetchone()[0]
        conn.commit()
        return {"goal_id": gid}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.delete("/goals/{goal_id}")
def delete_goal(goal_id: int):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE Goals SET Is_Active=FALSE WHERE Goal_Id=%s", (goal_id,))
        conn.commit()
        return {"deleted": goal_id}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


# ── Savings Accounts — Yield over Cost & APY ───────────────────────────────────
@router.get("/savings-accounts")
def get_savings_accounts():
    """Per-savings-account principal, interest, YOC%, and APY% — lifetime and last interest period."""
    query = """
    WITH CategorizedSplits AS (
        SELECT
            t.Accounts_Id, t.Transactions_Id, t.Date, t.Transfers_Id,
            CASE WHEN t.Transfers_Id IS NOT NULL THEN t.Total_Amount ELSE s.Amount END AS Amount,
            cat.Categories_Type,
            CASE WHEN t.Transfers_Id IS NOT NULL THEN 'Principal'
                 WHEN cat.Categories_Type = 'Interest' THEN 'Interest'
                 ELSE 'Principal' END AS Kind
        FROM Transactions t
        LEFT JOIN Splits s   ON s.Transactions_Id = t.Transactions_Id
        LEFT JOIN Categories cat ON cat.Categories_Id = s.Categories_Id
        LEFT JOIN Accounts a ON a.Accounts_Id = t.Accounts_Id
        WHERE a.Accounts_Type = 'Savings'
    ),
    NonEURAccounts AS (
        SELECT DISTINCT a.Accounts_Id, a.Currencies_Id
        FROM Accounts a
        WHERE a.Currencies_Id NOT IN (SELECT Currencies_Id FROM Currencies WHERE Currencies_ShortName = 'EUR')
    ),
    Last_FXRates AS (
        SELECT nea.Accounts_Id, hfx.FX_Rate
        FROM Historical_FX hfx
        JOIN NonEURAccounts nea ON nea.Currencies_Id = hfx.Currencies_Id_1
        WHERE hfx.Currencies_Id_2 = (SELECT Currencies_Id FROM Currencies WHERE Currencies_ShortName = 'EUR')
          AND hfx.Date = (
                SELECT MAX(h2.Date) FROM Historical_FX h2
                WHERE h2.Currencies_Id_1 = hfx.Currencies_Id_1 AND h2.Currencies_Id_2 = hfx.Currencies_Id_2
                  AND h2.Date <= CURRENT_DATE
              )
    ),
    AccountStats AS (
        SELECT
            cs.Accounts_Id,
            MIN(cs.Date) AS first_tx_date,
            MAX(cs.Date) AS last_tx_date,
            SUM(CASE WHEN cs.Kind = 'Principal' THEN COALESCE(cs.Amount,0) ELSE 0 END) AS principal,
            SUM(CASE WHEN cs.Kind = 'Principal' THEN COALESCE(cs.Amount,0) * COALESCE(fx.FX_Rate,1) ELSE 0 END) AS principal_eur,
            SUM(CASE WHEN cs.Kind = 'Interest' THEN COALESCE(cs.Amount,0) ELSE 0 END) AS total_interest,
            SUM(CASE WHEN cs.Kind = 'Interest' THEN COALESCE(cs.Amount,0) * COALESCE(fx.FX_Rate,1) ELSE 0 END) AS total_interest_eur
        FROM CategorizedSplits cs
        LEFT JOIN Last_FXRates fx ON fx.Accounts_Id = cs.Accounts_Id
        GROUP BY cs.Accounts_Id
    ),
    InterestDates AS (
        SELECT cs.Accounts_Id, cs.Date AS interest_date,
               ROW_NUMBER() OVER (PARTITION BY cs.Accounts_Id ORDER BY cs.Date DESC) AS rn
        FROM (SELECT DISTINCT Accounts_Id, Date FROM CategorizedSplits WHERE Kind = 'Interest') cs
    ),
    LastInterestDate  AS (SELECT Accounts_Id, interest_date AS last_interest_date  FROM InterestDates WHERE rn = 1),
    PriorInterestDate AS (SELECT Accounts_Id, interest_date AS prior_interest_date FROM InterestDates WHERE rn = 2),
    LastPeriodInterest AS (
        SELECT cs.Accounts_Id,
               SUM(cs.Amount) AS last_interest_sum,
               SUM(cs.Amount * COALESCE(fx.FX_Rate,1)) AS last_interest_sum_eur
        FROM CategorizedSplits cs
        JOIN LastInterestDate li ON li.Accounts_Id = cs.Accounts_Id
        LEFT JOIN Last_FXRates fx ON fx.Accounts_Id = cs.Accounts_Id
        WHERE cs.Kind = 'Interest' AND cs.Date = li.last_interest_date
        GROUP BY cs.Accounts_Id
    ),
    PeriodDates AS (
        SELECT pid.Accounts_Id,
               pid.prior_interest_date + generate_series(0, (lid.last_interest_date - pid.prior_interest_date) - 1)::int AS calendar_day
        FROM PriorInterestDate pid
        JOIN LastInterestDate lid ON pid.Accounts_Id = lid.Accounts_Id
    ),
    DailyBalances AS (
        SELECT pd.Accounts_Id, pd.calendar_day,
               (SELECT SUM(cs.Amount) FROM CategorizedSplits cs
                WHERE cs.Accounts_Id = pd.Accounts_Id AND cs.Date <= pd.calendar_day) AS daily_balance
        FROM PeriodDates pd
    ),
    PeriodAverageBalance AS (
        SELECT dbal.Accounts_Id, AVG(dbal.daily_balance) AS avg_period_balance
        FROM DailyBalances dbal
        GROUP BY dbal.Accounts_Id
    )
    SELECT
        a.Accounts_Id AS accounts_id,
        a.Accounts_Name AS accounts_name,
        a.Accounts_Type AS accounts_type,
        c.Currencies_ShortName AS currency,
        a.Accounts_Balance AS current_balance,
        ast.first_tx_date::text AS first_tx_date,
        ast.last_tx_date::text AS last_tx_date,
        lid.last_interest_date::text AS last_interest_date,
        ast.principal,
        ast.principal_eur,
        ast.total_interest,
        ast.total_interest_eur,
        pid.prior_interest_date::text AS prior_interest_date,
        lpi.last_interest_sum,
        pab.avg_period_balance
    FROM Accounts a
    JOIN Currencies c ON c.Currencies_Id = a.Currencies_Id
    LEFT JOIN AccountStats ast ON ast.Accounts_Id = a.Accounts_Id
    LEFT JOIN PriorInterestDate pid ON pid.Accounts_Id = a.Accounts_Id
    LEFT JOIN LastInterestDate lid ON lid.Accounts_Id = a.Accounts_Id
    LEFT JOIN LastPeriodInterest lpi ON lpi.Accounts_Id = a.Accounts_Id
    LEFT JOIN PeriodAverageBalance pab ON pab.Accounts_Id = a.Accounts_Id
    WHERE a.Accounts_Type = 'Savings'
    ORDER BY a.Accounts_Name
    """
    with get_db() as conn:
        df = pd.read_sql(query, conn)

    if df.empty:
        return {"summary": {}, "detail": [], "detail_last": []}

    for dc in ["first_tx_date", "last_tx_date", "last_interest_date", "prior_interest_date"]:
        df[dc] = pd.to_datetime(df[dc], errors="coerce")

    df["holding_days_total"] = (df["last_tx_date"] - df["first_tx_date"]).dt.days.clip(lower=1)
    principal_safe = df["principal"].replace(0, float("nan"))

    df["annual_interest_cash"] = df["total_interest"] / df["holding_days_total"] * 365
    df["annual_yoc_pct"] = (df["annual_interest_cash"] / principal_safe * 100).fillna(0)

    r_total = df["total_interest"] / principal_safe
    df["apy_pct"] = (((1 + r_total) ** (365 / df["holding_days_total"]) - 1) * 100).fillna(0)

    period_start = df["prior_interest_date"].fillna(df["first_tx_date"])
    df["period_start_date"] = period_start
    df["holding_days_last"] = (df["last_interest_date"] - period_start).dt.days.clip(lower=1)

    avg_p_safe = df["avg_period_balance"].replace(0, float("nan"))
    df["avg_principal_last"] = avg_p_safe
    df["annual_interest_cash_last"] = df["last_interest_sum"] / df["holding_days_last"] * 365
    df["annual_yoc_pct_last"] = (df["annual_interest_cash_last"] / avg_p_safe * 100).fillna(0)

    r_last = df["last_interest_sum"] / avg_p_safe
    df["apy_pct_last"] = (((1 + r_last) ** (365 / df["holding_days_last"]) - 1) * 100).fillna(0)

    savings_accounts_count = len(df)
    total_principal_eur = float(df["principal_eur"].sum())
    total_interest_eur = float(df["total_interest_eur"].sum())
    yoc_nonzero = df["annual_yoc_pct"].replace(0, float("nan"))
    apy_nonzero = df["apy_pct"].replace(0, float("nan"))
    avg_yoc = float(yoc_nonzero.mean()) if not yoc_nonzero.dropna().empty else None
    avg_apy = float(apy_nonzero.mean()) if not apy_nonzero.dropna().empty else None

    detail_cols = [
        "accounts_name", "accounts_type", "currency",
        "principal", "total_interest", "annual_interest_cash",
        "current_balance", "annual_yoc_pct", "apy_pct",
        "holding_days_total", "first_tx_date", "last_tx_date",
    ]
    detail_cols_last = [
        "accounts_name", "accounts_type", "currency",
        "avg_principal_last", "last_interest_sum", "annual_interest_cash_last",
        "annual_yoc_pct_last", "apy_pct_last",
        "holding_days_last", "period_start_date", "last_interest_date",
    ]

    detail_df = df[detail_cols].copy()
    detail_last_df = df[detail_cols_last].copy()

    summary = {
        "savings_accounts_count": savings_accounts_count,
        "total_principal_eur": round(total_principal_eur, 2),
        "total_interest_eur": round(total_interest_eur, 2),
        "avg_yoc_pct": round(avg_yoc, 4) if avg_yoc is not None else None,
        "avg_apy_pct": round(avg_apy, 4) if avg_apy is not None else None,
        "chart": [
            {"accounts_name": r["accounts_name"], "annual_yoc_pct": round(float(r["annual_yoc_pct"]), 4)}
            for _, r in df[df["annual_yoc_pct"] != 0].sort_values("annual_yoc_pct").iterrows()
        ],
    }

    return {
        "summary": summary,
        "detail": _df_to_list(detail_df),
        "detail_last": _df_to_list(detail_last_df),
    }


# ── Bond Schedule ─────────────────────────────────────────────────────────────
@router.get("/bond-schedule")
def get_bond_schedule():
    query = """
    WITH fx AS (
        SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
        FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC
    ),
    bond_holdings AS (
        SELECT h.Securities_Id AS securities_id, s.Securities_Name, h.Quantity,
               s.Maturity_Date, s.Coupon_Rate, s.Face_Value, s.Coupon_Frequency,
               s.Currencies_Id, c.Currencies_ShortName AS currency
        FROM Holdings h
        JOIN Securities s ON h.Securities_Id = s.Securities_Id
        JOIN Currencies c ON s.Currencies_Id = c.Currencies_Id
        WHERE h.Quantity > 0 AND s.Securities_Type = 'Bond'
    )
    SELECT
        bh.Securities_Name,
        bh.Quantity,
        bh.Face_Value,
        ROUND((bh.Quantity * COALESCE(bh.Face_Value,0))::numeric, 2) AS total_face_native,
        ROUND((bh.Quantity * COALESCE(bh.Face_Value,0) * COALESCE(fx.FX_Rate,1))::numeric, 2) AS total_face_eur,
        bh.Coupon_Rate,
        bh.Coupon_Frequency,
        ROUND((bh.Quantity * COALESCE(bh.Face_Value,0) * COALESCE(bh.Coupon_Rate,0) / 100 *
            CASE bh.Coupon_Frequency
                WHEN 'At Maturity' THEN 0 WHEN 'Semi-Annual' THEN 0.5
                WHEN 'Quarterly'   THEN 0.25 WHEN 'Monthly'  THEN 1.0/12
                ELSE 1.0 END * COALESCE(fx.FX_Rate,1))::numeric, 2) AS next_coupon_eur,
        ROUND((bh.Quantity * COALESCE(bh.Face_Value,0) * COALESCE(bh.Coupon_Rate,0) / 100 *
            CASE bh.Coupon_Frequency WHEN 'At Maturity' THEN 0 ELSE 1.0 END
            * COALESCE(fx.FX_Rate,1))::numeric, 2) AS annual_coupon_eur,
        bh.Maturity_Date::text AS maturity_date,
        (bh.Maturity_Date - CURRENT_DATE) AS days_to_maturity,
        bh.currency
    FROM bond_holdings bh
    LEFT JOIN fx ON fx.Currencies_Id_1 = bh.Currencies_Id
    ORDER BY bh.Maturity_Date ASC NULLS LAST
    """
    with get_db() as conn:
        df = pd.read_sql(query, conn)
    return _df_to_list(df)


def _parse_account_ids(account_ids: Optional[str]) -> Optional[list]:
    if not account_ids:
        return None
    ids = [int(x) for x in account_ids.split(",") if x.strip()]
    return ids or None


def _acct_clause(account_ids: Optional[list], col: str = "h.Accounts_Id") -> str:
    if not account_ids:
        return ""
    ids_sql = ",".join(str(i) for i in account_ids)
    return f" AND {col} IN ({ids_sql})"


# ── Portfolio Presets ───────────────────────────────────────────────────────────
def _ensure_presets_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS Portfolio_Presets (
            Preset_Id   SERIAL PRIMARY KEY,
            Preset_Name VARCHAR(100) UNIQUE NOT NULL,
            Account_Ids INTEGER[] NOT NULL DEFAULT '{}',
            Created_At  TIMESTAMP DEFAULT NOW(),
            Updated_At  TIMESTAMP DEFAULT NOW()
        )
    """)


@router.get("/portfolio-presets")
def get_portfolio_presets():
    conn = get_connection()
    try:
        cur = conn.cursor()
        _ensure_presets_table(cur)
        conn.commit()
        df = pd.read_sql("""
            SELECT Preset_Id AS preset_id, Preset_Name AS preset_name, Account_Ids AS account_ids
            FROM Portfolio_Presets ORDER BY Preset_Name
        """, conn)
        return _df_to_list(df)
    finally:
        conn.close()


@router.post("/portfolio-presets")
def upsert_portfolio_preset(data: dict):
    name = (data.get("name") or "").strip()
    account_ids = data.get("account_ids") or []
    if not name:
        raise HTTPException(400, "Preset name is required")
    conn = get_connection()
    try:
        cur = conn.cursor()
        _ensure_presets_table(cur)
        cur.execute("""
            INSERT INTO Portfolio_Presets (Preset_Name, Account_Ids, Updated_At)
            VALUES (%s, %s, NOW())
            ON CONFLICT (Preset_Name) DO UPDATE
                SET Account_Ids = EXCLUDED.Account_Ids, Updated_At = NOW()
        """, (name, account_ids))
        conn.commit()
        return {"saved": name}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.delete("/portfolio-presets/{preset_id}")
def delete_portfolio_preset(preset_id: int):
    conn = get_connection()
    try:
        cur = conn.cursor()
        _ensure_presets_table(cur)
        cur.execute("DELETE FROM Portfolio_Presets WHERE Preset_Id = %s", (preset_id,))
        conn.commit()
        return {"deleted": preset_id}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


# ── Benchmark candidates ───────────────────────────────────────────────────────
@router.get("/benchmark-candidates")
def get_benchmark_candidates(min_days: int = Query(30)):
    query = """
    SELECT s.Securities_Id AS id, s.Securities_Name AS name, s.Ticker AS ticker,
           COUNT(hp.Date) AS price_days
    FROM Securities s
    JOIN Historical_Prices hp ON hp.Securities_Id = s.Securities_Id
    WHERE s.Securities_Type = 'Market Index'
    GROUP BY s.Securities_Id, s.Securities_Name, s.Ticker
    HAVING COUNT(hp.Date) >= %(min_days)s
    ORDER BY s.Securities_Name
    """
    with get_db() as conn:
        df = pd.read_sql(query, conn, params={"min_days": min_days})
    return _df_to_list(df)


# ── Benchmark comparison ───────────────────────────────────────────────────────
@router.get("/benchmark")
def get_benchmark(
    benchmark_id: int = Query(...),
    lookback_days: int = Query(252),
    account_ids: Optional[str] = Query(None),
    resample: str = Query("Daily"),
):
    """Portfolio (weighted avg of holdings) vs benchmark, both indexed to 100."""
    acct_ids = _parse_account_ids(account_ids)
    held_clause = _acct_clause(acct_ids, "h.Accounts_Id")
    weights_clause = _acct_clause(acct_ids, "h.Accounts_Id")

    with get_db() as conn:
        prices_df = pd.read_sql(f"""
            WITH held AS (
                SELECT DISTINCT h.Securities_Id FROM Holdings h WHERE h.Quantity > 0{held_clause}
            ),
            price_counts AS (
                SELECT hp.Securities_Id FROM Historical_Prices hp
                JOIN held ON held.Securities_Id = hp.Securities_Id
                GROUP BY hp.Securities_Id HAVING COUNT(*) >= 30
            )
            SELECT hp.Date AS date, s.Securities_Name AS ticker, hp.Close AS close
            FROM Historical_Prices hp
            JOIN price_counts pc ON pc.Securities_Id = hp.Securities_Id
            JOIN Securities s ON s.Securities_Id = hp.Securities_Id
            WHERE hp.Date >= CURRENT_DATE - (%(lb)s || ' days')::INTERVAL
            ORDER BY hp.Date
        """, conn, params={"lb": lookback_days})

        weights_df = pd.read_sql(f"""
            WITH fx AS (SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC),
                 lp  AS (SELECT DISTINCT ON (Securities_Id) Securities_Id, Close FROM Historical_Prices ORDER BY Securities_Id, Date DESC)
            SELECT s.Securities_Name AS ticker,
                   SUM(h.Quantity * COALESCE(lp.Close,0) * CASE WHEN c.Currencies_ShortName='EUR' THEN 1 ELSE COALESCE(fx.FX_Rate,1) END) AS value_eur
            FROM Holdings h
            JOIN Securities s ON s.Securities_Id=h.Securities_Id
            JOIN Currencies c ON c.Currencies_Id=s.Currencies_Id
            JOIN lp ON lp.Securities_Id=h.Securities_Id
            LEFT JOIN fx ON fx.Currencies_Id_1=s.Currencies_Id
            WHERE h.Quantity > 0{weights_clause}
            GROUP BY s.Securities_Name
            HAVING SUM(h.Quantity * COALESCE(lp.Close,0) * CASE WHEN c.Currencies_ShortName='EUR' THEN 1 ELSE COALESCE(fx.FX_Rate,1) END) > 0
        """, conn)

        bench_df = pd.read_sql("""
            SELECT Date AS date, Close AS close FROM Historical_Prices
            WHERE Securities_Id = %(bid)s AND Date >= CURRENT_DATE - (%(lb)s || ' days')::INTERVAL
            ORDER BY Date
        """, conn, params={"bid": benchmark_id, "lb": lookback_days})

    if prices_df.empty or weights_df.empty or bench_df.empty:
        return []

    prices_df["date"] = pd.to_datetime(prices_df["date"])
    bench_df["date"]  = pd.to_datetime(bench_df["date"])

    wide = prices_df.pivot_table(index="date", columns="ticker", values="close", aggfunc="mean")
    total = weights_df["value_eur"].sum()
    weights_df["weight"] = weights_df["value_eur"] / total
    w = weights_df.set_index("ticker")["weight"]
    common = wide.columns.intersection(w.index)
    wide = wide[common]
    w = w[common]
    w = w / w.sum()

    wide_ffill = wide.ffill()
    ret = wide_ffill.pct_change().fillna(0)
    port_ret = ret.dot(w)
    port_idx = (1 + port_ret).cumprod() * 100
    port_idx.iloc[0] = 100

    bench_s = bench_df.set_index("date")["close"].reindex(port_idx.index).ffill().bfill()
    first_bench = bench_s.iloc[0] if not pd.isna(bench_s.iloc[0]) else bench_s.dropna().iloc[0] if not bench_s.dropna().empty else None
    bench_idx = bench_s / first_bench * 100 if first_bench else pd.Series(index=port_idx.index, dtype=float)

    combined = pd.DataFrame({"portfolio": port_idx, "benchmark": bench_idx})

    resample_map = {"Daily": None, "Weekly": "W", "Monthly": "ME"}
    freq = resample_map.get(resample)
    if freq:
        combined = combined.resample(freq).last().dropna(how="all")

    result = []
    for d, row in combined.iterrows():
        result.append({
            "date": d.strftime("%Y-%m-%d"),
            "portfolio": round(float(row["portfolio"]), 4) if not pd.isna(row["portfolio"]) else None,
            "benchmark": round(float(row["benchmark"]), 4) if not pd.isna(row["benchmark"]) else None,
        })
    return result


# ── Correlation matrix ─────────────────────────────────────────────────────────
@router.get("/correlation")
def get_correlation(
    lookback_days: int = Query(252),
    max_holdings: int = Query(20),
    account_ids: Optional[str] = Query(None),
):
    acct_ids = _parse_account_ids(account_ids)
    weights_clause = _acct_clause(acct_ids, "h.Accounts_Id")
    with get_db() as conn:
        weights_df = pd.read_sql(f"""
            WITH fx AS (SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC),
                 lp  AS (SELECT DISTINCT ON (Securities_Id) Securities_Id, Close FROM Historical_Prices ORDER BY Securities_Id, Date DESC)
            SELECT s.Securities_Name AS ticker,
                   SUM(h.Quantity * COALESCE(lp.Close,0) * CASE WHEN c.Currencies_ShortName='EUR' THEN 1 ELSE COALESCE(fx.FX_Rate,1) END) AS value_eur
            FROM Holdings h
            JOIN Securities s ON s.Securities_Id=h.Securities_Id
            JOIN Currencies c ON c.Currencies_Id=s.Currencies_Id
            JOIN lp ON lp.Securities_Id=h.Securities_Id
            LEFT JOIN fx ON fx.Currencies_Id_1=s.Currencies_Id
            WHERE h.Quantity > 0{weights_clause}
            GROUP BY s.Securities_Name
            HAVING SUM(h.Quantity * COALESCE(lp.Close,0) * CASE WHEN c.Currencies_ShortName='EUR' THEN 1 ELSE COALESCE(fx.FX_Rate,1) END) > 0
            ORDER BY value_eur DESC
            LIMIT %(max_h)s
        """, conn, params={"max_h": max_holdings})

        if weights_df.empty:
            return {"tickers": [], "matrix": []}

        tickers = tuple(weights_df["ticker"].tolist())
        prices_df = pd.read_sql("""
            SELECT hp.Date AS date, s.Securities_Name AS ticker, hp.Close AS close
            FROM Historical_Prices hp
            JOIN Securities s ON s.Securities_Id = hp.Securities_Id
            WHERE s.Securities_Name IN %(tickers)s
              AND hp.Date >= CURRENT_DATE - (%(lb)s || ' days')::INTERVAL
            ORDER BY hp.Date
        """, conn, params={"tickers": tickers, "lb": lookback_days})

    if prices_df.empty:
        return {"tickers": [], "matrix": []}

    prices_df["date"] = pd.to_datetime(prices_df["date"])
    wide = prices_df.pivot_table(index="date", columns="ticker", values="close", aggfunc="mean").ffill()
    ret = wide.pct_change().dropna(how="all")
    corr = ret.corr()
    tickers = corr.columns.tolist()
    matrix = [[round(float(v), 4) if not pd.isna(v) else None for v in row] for row in corr.values]
    return {"tickers": tickers, "matrix": matrix}


# ── Monte Carlo projection ──────────────────────────────────────────────────────
@router.get("/monte-carlo")
def get_monte_carlo(
    years_ahead: int = Query(10),
    num_sims: int = Query(500),
    monthly_contrib: float = Query(500.0),
    lookback_days: int = Query(756),
    account_ids: Optional[str] = Query(None),
    initial_value: Optional[float] = Query(None),
    override_return_pct: Optional[float] = Query(None),
    override_vol_pct: Optional[float] = Query(None),
):
    import numpy as np
    acct_ids = _parse_account_ids(account_ids)
    held_clause = _acct_clause(acct_ids, "h.Accounts_Id")

    with get_db() as conn:
        prices_df = pd.read_sql(f"""
            WITH held AS (
                SELECT DISTINCT h.Securities_Id FROM Holdings h WHERE h.Quantity > 0{held_clause}
            ),
            price_counts AS (
                SELECT hp.Securities_Id FROM Historical_Prices hp
                JOIN held ON held.Securities_Id = hp.Securities_Id
                GROUP BY hp.Securities_Id HAVING COUNT(*) >= 30
            )
            SELECT hp.Date AS date, s.Securities_Name AS ticker, hp.Close AS close
            FROM Historical_Prices hp
            JOIN price_counts pc ON pc.Securities_Id = hp.Securities_Id
            JOIN Securities s ON s.Securities_Id = hp.Securities_Id
            WHERE hp.Date >= CURRENT_DATE - (%(lb)s || ' days')::INTERVAL
            ORDER BY hp.Date
        """, conn, params={"lb": lookback_days})

        weights_df = pd.read_sql(f"""
            WITH fx AS (SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC),
                 lp  AS (SELECT DISTINCT ON (Securities_Id) Securities_Id, Close FROM Historical_Prices ORDER BY Securities_Id, Date DESC)
            SELECT s.Securities_Name AS ticker,
                   SUM(h.Quantity * COALESCE(lp.Close,0) * CASE WHEN c.Currencies_ShortName='EUR' THEN 1 ELSE COALESCE(fx.FX_Rate,1) END) AS value_eur
            FROM Holdings h
            JOIN Securities s ON s.Securities_Id=h.Securities_Id
            JOIN Currencies c ON c.Currencies_Id=s.Currencies_Id
            JOIN lp ON lp.Securities_Id=h.Securities_Id
            LEFT JOIN fx ON fx.Currencies_Id_1=s.Currencies_Id
            WHERE h.Quantity > 0{held_clause}
            GROUP BY s.Securities_Name
            HAVING SUM(h.Quantity * COALESCE(lp.Close,0) * CASE WHEN c.Currencies_ShortName='EUR' THEN 1 ELSE COALESCE(fx.FX_Rate,1) END) > 0
        """, conn)

    current_value = float(weights_df["value_eur"].sum()) if not weights_df.empty else 0.0
    init_val = initial_value if initial_value is not None else current_value

    ann_return = None
    ann_vol = None
    if not prices_df.empty and not weights_df.empty:
        prices_df["date"] = pd.to_datetime(prices_df["date"])
        wide = prices_df.pivot_table(index="date", columns="ticker", values="close", aggfunc="mean")
        total = weights_df["value_eur"].sum()
        weights_df["weight"] = weights_df["value_eur"] / total
        w = weights_df.set_index("ticker")["weight"]
        common = wide.columns.intersection(w.index)
        wide = wide[common]
        w = w[common]
        if w.sum() > 0:
            w = w / w.sum()
            ret = wide.ffill().pct_change().dropna(how="all").fillna(0)
            port_ret = ret.dot(w)
            if len(port_ret) > 5:
                ann_return = float(port_ret.mean() * 252)
                ann_vol = float(port_ret.std() * np.sqrt(252))

    if ann_return is None:
        ann_return, ann_vol = 0.06, 0.12

    used_return = (override_return_pct / 100) if override_return_pct is not None else ann_return
    used_vol = (override_vol_pct / 100) if override_vol_pct is not None else ann_vol

    n_steps = max(1, years_ahead * 12)
    monthly_mean = (1 + used_return) ** (1 / 12) - 1
    monthly_vol = used_vol / np.sqrt(12)

    rng = np.random.default_rng(42)
    sim_returns = rng.normal(monthly_mean, monthly_vol, size=(num_sims, n_steps))
    paths = np.zeros((num_sims, n_steps + 1))
    paths[:, 0] = init_val
    for t in range(1, n_steps + 1):
        paths[:, t] = paths[:, t - 1] * (1 + sim_returns[:, t - 1]) + monthly_contrib

    p10 = np.percentile(paths, 10, axis=0)
    p50 = np.percentile(paths, 50, axis=0)
    p90 = np.percentile(paths, 90, axis=0)

    chart = [{"month": m, "p10": round(float(p10[m]), 2), "p50": round(float(p50[m]), 2), "p90": round(float(p90[m]), 2)}
             for m in range(n_steps + 1)]

    targets = [50000, 100000, 250000, 500000, 1000000]
    final_vals = paths[:, -1]
    probabilities = [{"target": t, "probability_pct": round(float((final_vals >= t).mean() * 100), 1)} for t in targets]

    return {
        "calibration": {
            "ann_return_pct": round(ann_return * 100, 2),
            "ann_vol_pct": round(ann_vol * 100, 2),
        },
        "used": {
            "ann_return_pct": round(used_return * 100, 2),
            "ann_vol_pct": round(used_vol * 100, 2),
            "initial_value": round(init_val, 2),
        },
        "chart": chart,
        "probabilities": probabilities,
    }


# ── Portfolio Signals (Securities & Portfolio Analysis) ────────────────────────
@router.get("/portfolio-signals")
def get_portfolio_signals_endpoint():
    from database.queries import get_portfolio_signals
    df = get_portfolio_signals(None)
    if df is None or df.empty:
        return []
    return _df_to_list(df)


# ── Income & Expense Full (Streamlit-equivalent) ───────────────────────────────
@router.get("/income-expense-full")
def get_income_expense_full(
    start_date: str = Query("2024-01-01"),
    end_date: str = Query("2099-12-31"),
    cash_account_types: str = Query("Cash,Checking,Savings,Credit Card,Loan,Real Estate,Vehicle,Asset,Liability,Other"),
    inv_account_types: str = Query("Brokerage,Other Investment,Margin"),
    category_id: Optional[int] = Query(None),
):
    from database.queries import get_income_expense_data
    cash_list = [t.strip() for t in cash_account_types.split(",") if t.strip()]
    inv_list = [t.strip() for t in inv_account_types.split(",") if t.strip()]
    df = get_income_expense_data(start_date, end_date, category_id, cash_list, inv_list)
    if df is None or df.empty:
        return []
    # Normalise column name casing coming from DB
    df.columns = [c.lower() for c in df.columns]
    if "date" in df.columns:
        df["date"] = df["date"].astype(str).str[:10]
    if "month_date" in df.columns:
        df["month_date"] = df["month_date"].astype(str).str[:10]
    return _df_to_list(df)


# ── Custom Reports ─────────────────────────────────────────────────────────────

def _fetch_presets_fresh() -> list:
    """Query Custom_Report_Presets directly — bypasses @st.cache_data on get_custom_report_presets."""
    import json
    from database.queries import _ensure_custom_reports_table
    conn = get_connection()
    try:
        _ensure_custom_reports_table(conn)
        df = pd.read_sql(
            "SELECT Preset_Id AS preset_id, Preset_Name AS preset_name, Config AS config "
            "FROM Custom_Report_Presets ORDER BY Preset_Name",
            conn,
        )
    finally:
        conn.close()
    rows = []
    for _, r in df.iterrows():
        cfg = r["config"]
        if isinstance(cfg, str):
            cfg = json.loads(cfg)
        rows.append({"preset_id": int(r["preset_id"]), "preset_name": r["preset_name"], "config": cfg or {}})
    return rows


@router.get("/custom-report-presets")
def list_custom_report_presets():
    return _fetch_presets_fresh()


@router.post("/custom-report-presets")
def save_custom_report_preset(body: dict):
    import json
    from database.queries import _ensure_custom_reports_table
    name = body.get("preset_name", "").strip()
    if not name:
        raise HTTPException(400, "preset_name is required")
    config = body.get("config", {})
    conn = get_connection()
    try:
        _ensure_custom_reports_table(conn)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO Custom_Report_Presets (Preset_Name, Config, Updated_At)
                VALUES (%s, %s::jsonb, NOW())
                ON CONFLICT (Preset_Name) DO UPDATE
                    SET Config = EXCLUDED.Config, Updated_At = NOW()
            """, (name, json.dumps(config)))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@router.delete("/custom-report-presets/{preset_id}")
def remove_custom_report_preset(preset_id: int):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM Custom_Report_Presets WHERE Preset_Id = %s", (preset_id,))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@router.get("/custom-report-filter-data")
def get_custom_report_filter_data():
    """Return all accounts, expense categories, payees and securities for the Custom Report filters."""
    from database.queries import get_all_payees, get_all_securities_for_filter, get_expense_categories
    from database.connection import get_connection as _gc
    conn = _gc()
    try:
        import pandas as _pd
        accounts_df = _pd.read_sql("""
            SELECT a.Accounts_Id AS accounts_id, a.Accounts_Name AS accounts_name
            FROM Accounts a
            WHERE a.Is_Active = TRUE
            ORDER BY a.Accounts_Name
        """, conn)
    finally:
        conn.close()
    payees_df = get_all_payees()
    cats_df   = get_expense_categories()
    secs_df   = get_all_securities_for_filter()
    return {
        "accounts":   _df_to_list(accounts_df),
        "categories": _df_to_list(cats_df),
        "payees":     _df_to_list(payees_df),
        "securities": _df_to_list(secs_df),
    }


@router.post("/custom-report/run")
def run_custom_report(body: dict):
    from database.queries import get_custom_report_data, get_custom_report_investment_data
    date_from        = body["date_from"]
    date_to          = body["date_to"]
    grouping         = body.get("grouping", "month")
    account_ids      = body.get("account_ids") or None
    category_ids     = body.get("category_ids") or None
    payee_names      = body.get("payee_names") or None
    security_ids     = body.get("security_ids") or None
    include_transfers = body.get("include_transfers", False)
    use_account_currency = body.get("use_account_currency", False)
    investment_mode  = body.get("investment_mode", False)

    if investment_mode:
        df = get_custom_report_investment_data(
            date_from, date_to, grouping,
            security_ids=security_ids or [],
            account_ids=account_ids,
            use_account_currency=use_account_currency,
        )
    else:
        df = get_custom_report_data(
            date_from, date_to, grouping,
            account_ids=account_ids,
            category_ids=category_ids,
            payee_names=payee_names,
            security_ids=security_ids,
            include_transfers=include_transfers,
            use_account_currency=use_account_currency,
        )
    return _df_to_list(df)


@router.post("/custom-report/drill-down")
def custom_report_drill_down(body: dict):
    from database.queries import get_custom_report_drill_down
    df = get_custom_report_drill_down(
        body["date_from"], body["date_to"],
        category_path=body.get("category_path"),
        account_ids=body.get("account_ids") or None,
        category_ids=body.get("category_ids") or None,
        payee_names=body.get("payee_names") or None,
        security_ids=body.get("security_ids") or None,
        include_transfers=body.get("include_transfers", False),
        use_account_currency=body.get("use_account_currency", False),
    )
    return _df_to_list(df)


@router.post("/custom-report/investment-drill-down")
def custom_report_investment_drill_down(body: dict):
    from database.queries import get_custom_report_investment_drill_down
    df = get_custom_report_investment_drill_down(
        body["date_from"], body["date_to"],
        security_name=body.get("security_name"),
        account_ids=body.get("account_ids") or None,
        use_account_currency=body.get("use_account_currency", False),
    )
    return _df_to_list(df)
