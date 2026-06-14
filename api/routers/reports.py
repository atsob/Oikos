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
           SUM(CASE WHEN cat_type = 'Expense' THEN ABS(amount) ELSE 0 END) AS expense
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
    cat_type: str = Query("Expense"),
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
def get_allocation():
    """Asset allocation breakdown for a donut chart."""
    with get_db() as conn:
        # Cash accounts
        cash_df = pd.read_sql("""
            SELECT 'Cash & Savings' AS label,
                   SUM(a.Accounts_Balance * COALESCE(
                       (SELECT FX_Rate FROM Historical_FX WHERE Currencies_Id_1 = a.Currencies_Id ORDER BY Date DESC LIMIT 1), 1
                   )) AS value_eur
            FROM Accounts a
            JOIN Currencies c ON a.Currencies_Id = c.Currencies_Id
            WHERE a.Accounts_Type NOT IN ('Brokerage','Pension','Other Investment','Margin','Real Estate','Vehicle','Asset','Liability')
              AND a.Is_Active = TRUE
        """, conn)

        # Investments
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

        # Real assets
        asset_df = pd.read_sql("""
            SELECT Accounts_Type AS label,
                   SUM(Accounts_Balance) AS value_eur
            FROM Accounts
            WHERE Accounts_Type IN ('Real Estate','Vehicle','Asset')
              AND Is_Active = TRUE
            GROUP BY Accounts_Type
        """, conn)

    result = pd.concat([cash_df, inv_df, asset_df], ignore_index=True)
    result = result[result["value_eur"].notna() & (result["value_eur"] > 0)]
    return _df_to_list(result)


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
    other_inv_like AS (
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
        WHERE a.Accounts_Type = 'Other Investment'
    ),
    investment_universe AS (
        SELECT DISTINCT Securities_Id, Accounts_Id
        FROM Investments
        WHERE Action IN ('Buy','Reinvest','ShrIn','Sell','ShrOut')
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
        WHERE a.Accounts_Type IN ('Brokerage','Margin')
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
        WHERE a.Accounts_Type = 'Pension'
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
        SUM(CASE WHEN Accounts_Type IN ('Cash','Checking','Savings','Other','Other Investment')
            THEN balance_eur ELSE 0 END) AS cash,
        SUM(CASE WHEN Accounts_Type IN ('Brokerage','Margin')
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
                    (CASE WHEN i.Action IN ('Buy', 'MiscExp') THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0), i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0))
                          WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'Reinvest', 'RtrnCap') THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0), i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0))
                          ELSE 0 END) ELSE 0 END) AS cf_dtd,
                SUM(CASE WHEN i.Date > (SELECT dtd_start FROM periods) THEN
                    (CASE WHEN i.Action IN ('Buy', 'MiscExp') THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close + COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                          WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'Reinvest', 'RtrnCap') THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close - COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                          ELSE 0 END) ELSE 0 END) AS cf_dtd_eur,
                SUM(CASE WHEN i.Date > (SELECT wtd_start FROM periods) THEN
                    (CASE WHEN i.Action IN ('Buy', 'MiscExp') THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0), i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0))
                          WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'Reinvest', 'RtrnCap') THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0), i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0))
                          ELSE 0 END) ELSE 0 END) AS cf_wtd,
                SUM(CASE WHEN i.Date > (SELECT wtd_start FROM periods) THEN
                    (CASE WHEN i.Action IN ('Buy', 'MiscExp') THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close + COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                          WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'Reinvest', 'RtrnCap') THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Price_Per_Share - COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close - COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                          ELSE 0 END) ELSE 0 END) AS cf_wtd_eur,
                SUM(CASE WHEN i.Date > (SELECT mtd_start FROM periods) THEN
                    (CASE WHEN i.Action IN ('Buy', 'MiscExp') THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0), i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0))
                          WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'Reinvest', 'RtrnCap') THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0), i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0))
                          ELSE 0 END) ELSE 0 END) AS cf_mtd,
                SUM(CASE WHEN i.Date > (SELECT mtd_start FROM periods) THEN
                    (CASE WHEN i.Action IN ('Buy', 'MiscExp') THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close + COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                          WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'Reinvest', 'RtrnCap') THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close - COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                          ELSE 0 END) ELSE 0 END) AS cf_mtd_eur,
                SUM(CASE WHEN i.Date > (SELECT qtd_start FROM periods) THEN
                    (CASE WHEN i.Action IN ('Buy', 'MiscExp') THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0), i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0))
                          WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'Reinvest', 'RtrnCap') THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0), i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0))
                          ELSE 0 END) ELSE 0 END) AS cf_qtd,
                SUM(CASE WHEN i.Date > (SELECT qtd_start FROM periods) THEN
                    (CASE WHEN i.Action IN ('Buy', 'MiscExp') THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close + COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                          WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'Reinvest', 'RtrnCap') THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close - COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                          ELSE 0 END) ELSE 0 END) AS cf_qtd_eur,
                SUM(CASE WHEN i.Date > (SELECT ytd_start FROM periods) THEN
                    (CASE WHEN i.Action IN ('Buy', 'MiscExp') THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0), i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0))
                          WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'Reinvest', 'RtrnCap') THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0), i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0))
                          ELSE 0 END) ELSE 0 END) AS cf_ytd,
                SUM(CASE WHEN i.Date > (SELECT ytd_start FROM periods) THEN
                    (CASE WHEN i.Action IN ('Buy', 'MiscExp') THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close + COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                          WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'Reinvest', 'RtrnCap') THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close - COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                          ELSE 0 END) ELSE 0 END) AS cf_ytd_eur,
                SUM(CASE WHEN i.Date > (SELECT ytd_start FROM periods) THEN
                    CASE WHEN i.Action IN ('Buy', 'CashOut', 'MiscExp')
                            THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close + COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                         WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'CashIn', 'RtrnCap')
                            THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close - COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                         ELSE 0 END
                ELSE 0 END) AS net_invested_ytd_eur,
                SUM(CASE WHEN i.Action IN ('Buy', 'MiscExp', 'Reinvest', 'Exercise', 'ShrIn') THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0), i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0))
                         WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'RtrnCap', 'ShrOut') THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0), i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0))
                         ELSE 0 END) AS cf_all_time,
                SUM(CASE WHEN i.Action IN ('Buy', 'MiscExp', 'Reinvest', 'Exercise', 'ShrIn') THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close + COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                         WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'RtrnCap', 'ShrOut') THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close - COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                         ELSE 0 END) AS cf_all_time_eur,
                SUM(CASE WHEN i.Action IN ('Buy', 'CashOut', 'MiscExp')
                            THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close + COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                         WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'CashIn', 'RtrnCap')
                            THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close - COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                         ELSE 0 END) AS net_invested_all_time_eur,
                SUM(CASE WHEN i.Action IN ('Buy', 'CashOut', 'MiscExp')
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
            pf.Accounts_Name, pf.Securities_Name,
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
            h.Quantity * pf.price_today * COALESCE(pf.fx_today, 1) - h.Quantity * COALESCE(h.Fifo_Avg_Cost_EUR, h.Fifo_Avg_Price * COALESCE(pf.fx_today, 1)) AS unrealized_pnl_eur,
            COALESCE((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)), 0) - COALESCE(cf.net_invested_all_time_eur, 0)
            - (h.Quantity * pf.price_today * COALESCE(pf.fx_today, 1) - h.Quantity * COALESCE(h.Fifo_Avg_Cost_EUR, h.Fifo_Avg_Price * COALESCE(pf.fx_today, 1))) AS realized_pnl_eur,
            ROUND(dy.annual_income / NULLIF(h.Quantity * h.Fifo_Avg_Price, 0) * 100, 8) AS dividend_yoc_pct
        FROM prices_fx pf
        LEFT JOIN cash_flows cf ON pf.Accounts_Id = cf.Accounts_Id AND pf.Securities_Id = cf.Securities_Id
        LEFT JOIN Holdings h ON h.Accounts_Id = pf.Accounts_Id AND h.Securities_Id = pf.Securities_Id
        LEFT JOIN dividend_yoc dy ON dy.Accounts_Id = pf.Accounts_Id AND dy.Securities_Id = pf.Securities_Id
        LEFT JOIN account_direct_flows adf ON adf.Accounts_Id = pf.Accounts_Id
        LEFT JOIN account_linked_flows alf ON alf.inv_acc_id = pf.Accounts_Id
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
    WHERE i.Action IN ('Dividend','DivX','ReinvDiv')
      AND i.Date BETWEEN %(start_date)s AND %(end_date)s
    ORDER BY i.Date DESC
    """
    with get_db() as conn:
        df = pd.read_sql(query, conn, params={"start_date": start_date, "end_date": end_date})
    return _df_to_list(df)


@router.get("/capital-gains")
def get_capital_gains(year: int = Query(2024)):
    """Realized capital gains for the year from investment sell transactions."""
    query = """
    SELECT
        i.Date::text AS date,
        s.Securities_Name AS security,
        s.Ticker AS ticker,
        a.Accounts_Name AS account,
        i.Action AS action,
        i.Quantity AS quantity,
        i.Price_Per_Share AS sell_price,
        h.simple_avg_price AS avg_cost,
        i.Quantity * (i.Price_Per_Share - h.simple_avg_price) AS gain_loss,
        c.Currencies_ShortName AS currency
    FROM Investments i
    JOIN Securities s ON i.Securities_Id = s.Securities_Id
    JOIN Accounts a ON i.Accounts_Id = a.Accounts_Id
    LEFT JOIN Holdings h ON h.Securities_Id = i.Securities_Id AND h.Accounts_Id = i.Accounts_Id
    JOIN Currencies c ON s.Currencies_Id = c.Currencies_Id
    WHERE i.Action IN ('Sell','SellX')
      AND EXTRACT(YEAR FROM i.Date) = %(year)s
    ORDER BY i.Date DESC
    """
    with get_db() as conn:
        df = pd.read_sql(query, conn, params={"year": year})
    return _df_to_list(df)


@router.get("/budget-vs-actual")
def get_budget_vs_actual(year: int = Query(2024), month: Optional[int] = Query(None)):
    """Budget vs actual spending by category."""
    month_clause = "AND EXTRACT(MONTH FROM t.Date) = %(month)s" if month else ""
    query = f"""
    WITH RECURSIVE CategoryHierarchy AS (
        SELECT Categories_Id, Categories_Name::TEXT AS Full_Path,
               Categories_Type::TEXT AS Categories_Type, Categories_Id_Parent
        FROM Categories WHERE Categories_Id_Parent IS NULL
        UNION ALL
        SELECT c.Categories_Id, ch.Full_Path || ' : ' || c.Categories_Name,
               c.Categories_Type::TEXT, c.Categories_Id_Parent
        FROM Categories c JOIN CategoryHierarchy ch ON c.Categories_Id_Parent = ch.Categories_Id
    ),
    actuals AS (
        SELECT s.Categories_Id,
               SUM(ABS(s.Amount)) AS actual
        FROM Splits s
        JOIN Transactions t ON t.Transactions_Id = s.Transactions_Id
        JOIN Accounts a ON t.Accounts_Id = a.Accounts_Id
        WHERE EXTRACT(YEAR FROM t.Date) = %(year)s
          {month_clause}
          AND a.Accounts_Type IN ('Cash','Checking','Savings','Credit Card','Loan','Other')
        GROUP BY s.Categories_Id
    )
    SELECT
        ch.Full_Path AS category,
        ch.Categories_Type AS type,
        COALESCE(b.Budget_Amount, 0) AS budget,
        COALESCE(act.actual, 0) AS actual,
        COALESCE(b.Budget_Amount, 0) - COALESCE(act.actual, 0) AS variance
    FROM CategoryHierarchy ch
    LEFT JOIN Annual_Budgets b ON b.Categories_Id = ch.Categories_Id AND b.Year = %(year)s
    LEFT JOIN actuals act ON act.Categories_Id = ch.Categories_Id
    WHERE ch.Categories_Type = 'Expense'
      AND (b.Budget_Amount IS NOT NULL OR act.actual IS NOT NULL)
    ORDER BY ABS(COALESCE(act.actual, 0)) DESC
    """
    params: dict = {"year": year}
    if month:
        params["month"] = month
    with get_db() as conn:
        df = pd.read_sql(query, conn, params=params)
    return _df_to_list(df)


@router.get("/cash-flow-forecast")
def get_cash_flow_forecast(months_ahead: int = Query(6)):
    """Cash flow forecast from recurring templates."""
    query = """
    SELECT
        rt.Templates_Id AS template_id,
        rt.Templates_Name AS name,
        a.Accounts_Name AS account,
        p.Payees_Name AS payee,
        rt.Total_Amount AS amount,
        rt.Next_Due_Date::text AS next_due_date,
        rt.Periodicity AS periodicity,
        rt.Is_Active AS is_active
    FROM Recurring_Templates rt
    LEFT JOIN Accounts a ON rt.Accounts_Id = a.Accounts_Id
    LEFT JOIN Payees p ON rt.Payees_Id = p.Payees_Id
    WHERE rt.Is_Active = TRUE
      AND rt.Next_Due_Date <= (CURRENT_DATE + (%(months_ahead)s || ' months')::interval)
    ORDER BY rt.Next_Due_Date ASC
    """
    with get_db() as conn:
        df = pd.read_sql(query, conn, params={"months_ahead": months_ahead})
    return _df_to_list(df)


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

    query = f"""
    WITH
    period_dates AS (
        SELECT (gs - INTERVAL '1 day')::date AS period_end
        FROM generate_series(
            date_trunc('{trunc_unit}', '{start_date}'::date) + '{pg_interval}'::interval,
            date_trunc('{trunc_unit}', CURRENT_DATE),
            '{pg_interval}'::interval
        ) gs
        UNION SELECT CURRENT_DATE::date ORDER BY 1
    ),
    daily_fx AS (
        SELECT p.period_end, cur.Currencies_Id,
            (SELECT FX_Rate FROM Historical_FX WHERE Currencies_Id_1=cur.Currencies_Id AND Date<=p.period_end ORDER BY Date DESC LIMIT 1) AS fx_rate
        FROM period_dates p CROSS JOIN Currencies cur WHERE cur.Currencies_ShortName != 'EUR'
    ),
    cash_bal AS (
        SELECT p.period_end, a.Accounts_Name, a.Accounts_Type,
            CASE WHEN a.Accounts_Type IN ('Real Estate','Vehicle','Asset')
                 THEN GREATEST(0, a.Accounts_Balance - COALESCE((SELECT SUM(Total_Amount) FROM Transactions WHERE Accounts_Id=a.Accounts_Id AND Date>p.period_end),0))
                 ELSE a.Accounts_Balance - COALESCE((SELECT SUM(Total_Amount) FROM Transactions WHERE Accounts_Id=a.Accounts_Id AND Date>p.period_end),0)
            END * COALESCE((SELECT fx_rate FROM daily_fx WHERE period_end=p.period_end AND Currencies_Id=a.Currencies_Id),1) AS balance_eur
        FROM period_dates p CROSS JOIN Accounts a
        WHERE a.Accounts_Type NOT IN ('Brokerage','Margin','Pension','Other Investment')
    ),
    inv_universe AS (SELECT DISTINCT Securities_Id, Accounts_Id FROM Investments WHERE Action IN ('Buy','Reinvest','ShrIn','Sell','ShrOut')),
    inv_bal AS (
        SELECT p.period_end, a.Accounts_Name, a.Accounts_Type,
            SUM(GREATEST(COALESCE((
                SELECT SUM(CASE WHEN Action IN ('Buy','Reinvest','ShrIn') THEN Quantity WHEN Action IN ('Sell','ShrOut') THEN -Quantity ELSE 0 END)
                FROM Investments i2 WHERE i2.Securities_Id=iu.Securities_Id AND i2.Accounts_Id=iu.Accounts_Id AND i2.Date<=p.period_end
            ),0),0) * COALESCE((SELECT Close FROM Historical_Prices WHERE Securities_Id=iu.Securities_Id AND Date<=p.period_end ORDER BY Date DESC LIMIT 1),0)
            * COALESCE((SELECT fx_rate FROM daily_fx WHERE period_end=p.period_end AND Currencies_Id=s.Currencies_Id),1)) AS balance_eur
        FROM period_dates p CROSS JOIN inv_universe iu
        JOIN Accounts a ON iu.Accounts_Id=a.Accounts_Id
        JOIN Securities s ON iu.Securities_Id=s.Securities_Id
        WHERE a.Accounts_Type IN ('Brokerage','Margin')
        GROUP BY p.period_end, a.Accounts_Name, a.Accounts_Type
    ),
    pension_bal AS (
        SELECT p.period_end, a.Accounts_Name, a.Accounts_Type,
            GREATEST(0, a.Accounts_Balance - COALESCE((
                SELECT SUM(CASE WHEN Action IN ('CashIn','IntInc') THEN Total_Amount_AccCur WHEN Action='CashOut' THEN -Total_Amount_AccCur ELSE 0 END)
                FROM Investments WHERE Accounts_Id=a.Accounts_Id AND Date>p.period_end
            ),0)) * COALESCE((SELECT fx_rate FROM daily_fx WHERE period_end=p.period_end AND Currencies_Id=a.Currencies_Id),1) AS balance_eur
        FROM period_dates p CROSS JOIN Accounts a WHERE a.Accounts_Type IN ('Pension','Other Investment')
    )
    SELECT period_end::text AS period, accounts_name, accounts_type,
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
           ROUND(income::numeric,2) AS income_eur,
           ROUND(expense::numeric,2) AS expenses_eur,
           ROUND((income - expense)::numeric,2) AS savings_eur,
           CASE WHEN income > 0 THEN ROUND(((income - expense) / income * 100)::numeric, 1) ELSE 0 END AS savings_rate_pct
    FROM monthly ORDER BY month ASC
    """
    with get_db() as conn:
        df = pd.read_sql(query, conn, params={"months": months})
    return _df_to_list(df)


# ── Monthly portfolio values helper ───────────────────────────────────────────
def _get_monthly_portfolio_values(start_date: str, end_date: str, conn) -> pd.DataFrame:
    query = """
    WITH RECURSIVE months AS (
        SELECT (date_trunc('month', %(start_date)s::date) + INTERVAL '1 month' - INTERVAL '1 day')::date AS d
        UNION ALL
        SELECT (date_trunc('month', d + INTERVAL '1 month') + INTERVAL '1 month' - INTERVAL '1 day')::date
        FROM months WHERE d < date_trunc('month', %(end_date)s::date)
    ),
    dates AS (SELECT d FROM months WHERE d <= %(end_date)s::date UNION SELECT %(end_date)s::date),
    inv_universe AS (SELECT DISTINCT Securities_Id, Accounts_Id FROM Investments WHERE Action IN ('Buy','Reinvest','ShrIn','Sell','ShrOut')),
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


# ── TWR ───────────────────────────────────────────────────────────────────────
@router.get("/twr")
def get_twr(
    start_date: str = Query(None),
    end_date: str = Query(None),
):
    from datetime import date as _date
    import numpy as np
    if not start_date:
        start_date = (_date.today().replace(year=_date.today().year - 3)).isoformat()
    if not end_date:
        end_date = _date.today().isoformat()

    with get_db() as conn:
        vals_df = _get_monthly_portfolio_values(start_date, end_date, conn)
        cf_df = pd.read_sql("""
            SELECT DATE_TRUNC('month', Date)::date::text AS month,
                SUM(CASE WHEN Action='CashIn' THEN Total_Amount_AccCur * COALESCE(
                        (SELECT FX_Rate FROM Historical_FX WHERE Currencies_Id_1=a.Currencies_Id AND Date<=i.Date ORDER BY Date DESC LIMIT 1),1)
                         WHEN Action='CashOut' THEN -Total_Amount_AccCur * COALESCE(
                        (SELECT FX_Rate FROM Historical_FX WHERE Currencies_Id_1=a.Currencies_Id AND Date<=i.Date ORDER BY Date DESC LIMIT 1),1)
                         ELSE 0 END) AS net_cashflow_eur
            FROM Investments i JOIN Accounts a ON a.Accounts_Id=i.Accounts_Id
            WHERE Action IN ('CashIn','CashOut') AND Date BETWEEN %(start_date)s AND %(end_date)s
            GROUP BY DATE_TRUNC('month', Date) ORDER BY 1
        """, conn, params={"start_date": start_date, "end_date": end_date})

    if vals_df.empty:
        return {"summary": {"twr_total_pct": 0, "twr_ann_pct": 0, "months": 0}, "chart": []}

    vals_df = vals_df.set_index("date")
    cf_map = dict(zip(cf_df["month"].tolist(), cf_df["net_cashflow_eur"].tolist())) if not cf_df.empty else {}
    dates = list(vals_df.index)
    running = 1.0
    chart = []
    for i, d in enumerate(dates):
        v_end = float(vals_df.loc[d, "portfolio_value_eur"] or 0)
        if i == 0:
            chart.append({"date": d, "twr_cumulative_pct": 0.0, "portfolio_value_eur": round(v_end, 2)})
            continue
        v_start = float(vals_df.loc[dates[i-1], "portfolio_value_eur"] or 0)
        cf = float(cf_map.get(d, 0) or 0)
        denom = v_start + cf
        r = (v_end - v_start - cf) / denom if denom > 0 else 0.0
        running *= (1 + r)
        chart.append({"date": d, "twr_cumulative_pct": round((running - 1) * 100, 4), "portfolio_value_eur": round(v_end, 2)})

    n = len(dates)
    twr_total = running - 1
    twr_ann = (running ** (12.0 / max(n, 1)) - 1) if n > 1 else twr_total
    return {
        "summary": {
            "twr_total_pct": round(twr_total * 100, 2),
            "twr_ann_pct": round(twr_ann * 100, 2),
            "months": n,
        },
        "chart": chart,
    }


# ── Risk Metrics ──────────────────────────────────────────────────────────────
@router.get("/risk-metrics")
def get_risk_metrics(
    start_date: str = Query(None),
    end_date: str = Query(None),
):
    from datetime import date as _date
    import numpy as np
    if not start_date:
        start_date = (_date.today().replace(year=_date.today().year - 3)).isoformat()
    if not end_date:
        end_date = _date.today().isoformat()

    with get_db() as conn:
        vals_df = _get_monthly_portfolio_values(start_date, end_date, conn)

    if vals_df.empty or len(vals_df) < 2:
        return {"ann_vol_pct": 0, "ann_return_pct": 0, "sharpe": 0, "sortino": 0,
                "max_drawdown_pct": 0, "var_95_pct": 0, "cvar_95_pct": 0, "months": 0, "drawdown_chart": []}

    vals = vals_df["portfolio_value_eur"].astype(float).values
    rets = np.diff(vals) / np.where(vals[:-1] > 0, vals[:-1], np.nan)
    rets = rets[~np.isnan(rets)]
    n = len(rets)
    if n < 2:
        return {"ann_vol_pct": 0, "ann_return_pct": 0, "sharpe": 0, "sortino": 0,
                "max_drawdown_pct": 0, "var_95_pct": 0, "cvar_95_pct": 0, "months": n, "drawdown_chart": []}

    ann_vol = float(np.std(rets, ddof=1) * np.sqrt(12))
    ann_ret = float((1 + np.mean(rets)) ** 12 - 1)
    rf = 0.03
    sharpe = (ann_ret - rf) / ann_vol if ann_vol > 0 else 0.0
    down = rets[rets < 0]
    down_dev = float(np.std(down, ddof=1) * np.sqrt(12)) if len(down) > 1 else 0.0
    sortino = (ann_ret - rf) / down_dev if down_dev > 0 else 0.0
    cum = np.cumprod(1 + rets)
    roll_max = np.maximum.accumulate(cum)
    dd = (cum - roll_max) / roll_max
    max_dd = float(dd.min())
    var_95 = float(np.percentile(rets, 5))
    tail = rets[rets <= var_95]
    cvar_95 = float(tail.mean()) if len(tail) > 0 else var_95

    dates = vals_df["date"].tolist()[1:]
    drawdown_chart = [{"date": dates[i], "drawdown_pct": round(float(dd[i]) * 100, 4)} for i in range(len(dates))]

    return {
        "ann_vol_pct": round(ann_vol * 100, 2),
        "ann_return_pct": round(ann_ret * 100, 2),
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "var_95_pct": round(var_95 * 100, 2),
        "cvar_95_pct": round(cvar_95 * 100, 2),
        "months": n,
        "drawdown_chart": drawdown_chart,
    }


# ── Tax-Loss Harvesting ───────────────────────────────────────────────────────
@router.get("/tax-loss-harvesting")
def get_tax_loss_harvesting():
    query = """
    WITH fx AS (SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC),
    latest_price AS (SELECT DISTINCT ON (Securities_Id) Securities_Id, Close FROM Historical_Prices ORDER BY Securities_Id, Date DESC)
    SELECT s.Securities_Name, s.Securities_Type::text, s.Ticker,
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
    from datetime import date as _date
    if year is None:
        year = _date.today().year
    query = """
    SELECT i.Date::text AS date, s.Securities_Name, a.Accounts_Name AS account_name,
           i.Action,
           COALESCE(s.Is_Tax_Exempt, FALSE) AS is_tax_exempt,
           CASE WHEN cur.Currencies_ShortName='EUR' THEN i.Total_Amount_AccCur
                ELSE i.Total_Amount_AccCur * COALESCE(
                    (SELECT FX_Rate FROM Historical_FX WHERE Currencies_Id_1=s.Currencies_Id AND Date<=i.Date ORDER BY Date DESC LIMIT 1),1)
           END AS amount_eur
    FROM Investments i
    JOIN Securities s ON s.Securities_Id=i.Securities_Id
    JOIN Accounts a ON a.Accounts_Id=i.Accounts_Id
    JOIN Currencies cur ON cur.Currencies_Id=a.Currencies_Id
    WHERE i.Action IN ('Dividend','IntInc','Reinvest','RtrnCap')
      AND EXTRACT(YEAR FROM i.Date)=%(year)s
      AND i.Total_Amount_AccCur <> 0
    ORDER BY i.Date DESC, s.Securities_Name
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
    SELECT s.Securities_Name, s.Ticker, s.Securities_Type::text,
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
