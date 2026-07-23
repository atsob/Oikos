import pandas as pd
from database.connection import get_connection

from collections import deque
from datetime import datetime, timedelta

def get_category_hierarchy():
    """Get category hierarchy with full paths"""
    conn = get_connection()

    query = """
    WITH RECURSIVE CategoryHierarchy AS (
        SELECT 
            Categories_Id, 
            Categories_Name::TEXT as Full_Path,
            Categories_Name::TEXT as Name,
            Categories_Type,
            Categories_Id_Parent,
            0 as Level
        FROM Categories 
        WHERE Categories_Id_Parent IS NULL
        
        UNION ALL
        
        SELECT 
            c.Categories_Id, 
            ch.Full_Path || ' : ' || c.Categories_Name as Full_Path,
            c.Categories_Name as Name,
            c.Categories_Type,
            c.Categories_Id_Parent,
            ch.Level + 1 as Level
        FROM Categories c
        JOIN CategoryHierarchy ch ON c.Categories_Id_Parent = ch.Categories_Id
    )
    SELECT * FROM CategoryHierarchy ORDER BY Full_Path
    """

    df = pd.read_sql(query, conn)
    conn.close()
    return df


def get_hist_net_worth_data(start_date):
    """Get historical net worth data."""
    conn = get_connection()
    
    query = f"""
    WITH RECURSIVE 
    months AS (
        SELECT (date_trunc('month', '{start_date}'::date) + INTERVAL '1 month' - INTERVAL '1 day')::date as d
        UNION ALL
        SELECT (date_trunc('month', d + INTERVAL '1 month') + INTERVAL '1 month' - INTERVAL '1 day')::date 
        FROM months 
        WHERE d < date_trunc('month', CURRENT_DATE)
    ),
    dates AS (
        SELECT d FROM months WHERE d <= CURRENT_DATE
        UNION
        SELECT CURRENT_DATE::date
    ),
    historical_assets AS (
        SELECT 
            dt.d as date,
            a.Accounts_Id,
            a.Currencies_Id,
            a.Accounts_Balance - COALESCE((
                SELECT SUM(Total_Amount) 
                FROM Transactions 
                WHERE Accounts_Id = a.Accounts_Id 
                AND Date > dt.d
            ), 0) as balance_at_date
        FROM dates dt
        CROSS JOIN Accounts a
        WHERE a.Accounts_Type IN ('Real Estate', 'Vehicle', 'Asset', 'Liability')
    ),
    historical_cash AS (
        SELECT 
            dt.d as date,
            a.Accounts_Id,
            a.Currencies_Id,
            a.Accounts_Balance - COALESCE((
                SELECT SUM(Total_Amount) 
                FROM Transactions 
                WHERE Accounts_Id = a.Accounts_Id 
                AND Date > dt.d
            ), 0) as balance_at_date
        FROM dates dt
        CROSS JOIN Accounts a
        WHERE a.Accounts_Type NOT IN ('Brokerage', 'Pension', 'Other Investment', 'Margin', 'Real Estate', 'Vehicle', 'Asset', 'Liability')
        UNION ALL
        SELECT 
            dt.d as date,
            a.Accounts_Id,
            a.Currencies_Id,
            a.Accounts_Balance - COALESCE((
                SELECT SUM(Total_Amount) 
                FROM Transactions 
                WHERE Accounts_Id = a.Accounts_Id 
                AND Date > dt.d
            ), 0) as balance_at_date
        FROM dates dt
        CROSS JOIN Accounts a
        WHERE a.Accounts_Type IN ('Other Investment')        
    ),
    historical_pension AS (
        SELECT 
            dt.d as date,
            a.Accounts_Id,
            a.Currencies_Id,
            a.Accounts_Balance - COALESCE((
                SELECT  
                    SUM(CASE WHEN Action IN ('CashIn', 'IntInc') THEN Total_Amount_AccCur
                             WHEN Action IN ('CashOut') THEN -Total_Amount_AccCur
                             ELSE 0 END)
                FROM Investments
                WHERE Accounts_Id = a.Accounts_Id
                AND Date > dt.d
            ), 0) as balance_at_date
        FROM dates dt
        CROSS JOIN Accounts a
        WHERE a.Accounts_Type IN ('Pension')
    ),
    historical_inv AS (
        SELECT 
            dt.d as date,
            h.Securities_Id,
            h.Quantity - COALESCE((
                SELECT SUM(CASE WHEN Action = 'Buy' THEN Quantity WHEN Action = 'Sell' THEN -Quantity ELSE 0 END)
                FROM Investments 
                WHERE Securities_Id = h.Securities_Id 
                AND Date > dt.d
            ), 0) as qty_at_date
        FROM dates dt
        CROSS JOIN Holdings h
    ),
    daily_fx AS (
        SELECT dt.d as date, c.Currencies_Id,
            (SELECT FX_Rate FROM Historical_FX WHERE Date <= dt.d AND Currencies_Id_1 = c.Currencies_Id ORDER BY Date DESC LIMIT 1) as fx_rate
        FROM dates dt CROSS JOIN Currencies c
    ),
    daily_prices AS (
        SELECT dt.d as date, s.Securities_Id,
            (SELECT Close FROM Historical_Prices WHERE Date <= dt.d AND Securities_Id = s.Securities_Id ORDER BY Date DESC LIMIT 1) as close
        FROM dates dt CROSS JOIN Securities s
    ),
    final_calculation AS (
        SELECT 
            dt.d as date,
            (SELECT SUM(CASE 
                WHEN cur.Currencies_ShortName = 'EUR' THEN ha.balance_at_date 
                ELSE ha.balance_at_date * COALESCE(dfx.fx_rate, 1) 
             END)
             FROM historical_assets ha
             JOIN Currencies cur ON ha.Currencies_Id = cur.Currencies_Id
             LEFT JOIN daily_fx dfx ON ha.date = dfx.date AND ha.Currencies_Id = dfx.Currencies_Id
             WHERE ha.date = dt.d) as total_assets,
            (SELECT SUM(CASE 
                WHEN cur.Currencies_ShortName = 'EUR' THEN hc.balance_at_date 
                ELSE hc.balance_at_date * COALESCE(dfx.fx_rate, 1) 
             END)
             FROM historical_cash hc
             JOIN Currencies cur ON hc.Currencies_Id = cur.Currencies_Id
             LEFT JOIN daily_fx dfx ON hc.date = dfx.date AND hc.Currencies_Id = dfx.Currencies_Id
             WHERE hc.date = dt.d) as total_cash,
            (SELECT SUM(CASE 
                WHEN cur.Currencies_ShortName = 'EUR' THEN hp.balance_at_date 
                ELSE hp.balance_at_date * COALESCE(dfx.fx_rate, 1) 
             END)
             FROM historical_pension hp
             JOIN Currencies cur ON hp.Currencies_Id = cur.Currencies_Id
             LEFT JOIN daily_fx dfx ON hp.date = dfx.date AND hp.Currencies_Id = dfx.Currencies_Id
             WHERE hp.date = dt.d) as total_pension,
            (SELECT SUM(hi.qty_at_date * COALESCE(dp.close, 0) * 
                CASE WHEN cs.Currencies_ShortName = 'EUR' THEN 1 ELSE COALESCE(dfx_inv.fx_rate, 1) END
             )
             FROM historical_inv hi
             JOIN Securities s ON hi.Securities_Id = s.Securities_Id
             JOIN Currencies cs ON s.Currencies_Id = cs.Currencies_Id
             LEFT JOIN daily_prices dp ON hi.date = dp.date AND hi.Securities_Id = dp.Securities_Id
             LEFT JOIN daily_fx dfx_inv ON hi.date = dfx_inv.date AND s.Currencies_Id = dfx_inv.Currencies_Id
             WHERE hi.date = dt.d) as total_invested
        FROM dates dt
    )
    SELECT 
        date,
        COALESCE(total_assets, 0) as total_assets,
        COALESCE(total_cash, 0) as total_cash,
        COALESCE(total_pension, 0) as total_pension,
        COALESCE(total_invested, 0) as total_invested,
        (COALESCE(total_assets, 0) + COALESCE(total_cash, 0) + COALESCE(total_pension, 0) + COALESCE(total_invested, 0)) as total_net_worth
    FROM final_calculation
    ORDER BY date ASC
    """
    
    df = pd.read_sql(query, conn)
    conn.close()
    return df

def get_hist_inv_positions_data(start_date):
    conn = get_connection()
    query = f"""
    WITH RECURSIVE 
    months AS (
        SELECT (date_trunc('month', '{start_date}'::date) + INTERVAL '1 month' - INTERVAL '1 day')::date as d
        UNION ALL
        SELECT (date_trunc('month', d + INTERVAL '1 month') + INTERVAL '1 month' - INTERVAL '1 day')::date 
        FROM months 
        WHERE d < date_trunc('month', CURRENT_DATE)
    ),
    dates AS (
        SELECT d FROM months WHERE d <= CURRENT_DATE
        UNION
        SELECT CURRENT_DATE::date
    ),
    historical_qty AS (
        SELECT 
            dt.d as date,
            h.Securities_Id,
            h.Accounts_Id,
            h.Quantity - COALESCE((
                SELECT SUM(CASE 
                    WHEN Action IN ('Buy', 'Reinvest', 'ShrIn') THEN Quantity 
                    WHEN Action IN ('Sell', 'ShrOut') THEN -Quantity 
                    ELSE 0 END)
                FROM Investments 
                WHERE Securities_Id = h.Securities_Id AND Accounts_Id = h.Accounts_Id
                AND Date > dt.d
            ), 0) as qty_at_date
        FROM dates dt
        CROSS JOIN Holdings h
    ),
    daily_fx AS (
        SELECT dt.d as date, c.Currencies_Id,
            (SELECT FX_Rate FROM Historical_FX WHERE Date <= dt.d AND Currencies_Id_1 = c.Currencies_Id ORDER BY Date DESC LIMIT 1) as fx_rate
        FROM dates dt CROSS JOIN Currencies c
    ),
    daily_prices AS (
        SELECT dt.d as date, s.Securities_Id,
            (SELECT Close FROM Historical_Prices WHERE Date <= dt.d AND Securities_Id = s.Securities_Id ORDER BY Date DESC LIMIT 1) as close
        FROM dates dt CROSS JOIN Securities s
    )
    SELECT 
        hq.date,
        a.Accounts_Name,
        s.Securities_Name,
        hq.qty_at_date,
        COALESCE(dp.close, 0) as price_at_date,
        (hq.qty_at_date * COALESCE(dp.close, 0) * 
            CASE WHEN cur_s.Currencies_ShortName = 'EUR' THEN 1 ELSE COALESCE(dfx.fx_rate, 1) END
        ) as value_in_eur
    FROM historical_qty hq
    JOIN Accounts a ON hq.Accounts_Id = a.Accounts_Id
    JOIN Securities s ON hq.Securities_Id = s.Securities_Id
    JOIN Currencies cur_s ON s.Currencies_Id = cur_s.Currencies_Id
    LEFT JOIN daily_prices dp ON hq.date = dp.date AND hq.Securities_Id = dp.Securities_Id
    LEFT JOIN daily_fx dfx ON hq.date = dfx.date AND s.Currencies_Id = dfx.Currencies_Id
    WHERE hq.qty_at_date != 0
    ORDER BY hq.date ASC, a.Accounts_Name ASC, value_in_eur DESC
    """
    df = pd.read_sql(query, conn)
    conn.close()
    return df


def get_net_worth_report_data(start_date: str, interval: str = 'Year', account_ids: tuple = None):
    """Per-account historical balances at each period-end for the Quicken-style Net Worth report."""
    conn = get_connection()

    trunc_map = {'Year': 'year', 'Quarter': 'quarter', 'Month': 'month'}
    intv_map  = {'Year': '1 year', 'Quarter': '3 months', 'Month': '1 month'}
    trunc_unit  = trunc_map.get(interval, 'year')
    pg_interval = intv_map.get(interval, '1 year')

    # Build account filter clause (account_ids are internal DB integers — safe to embed)
    if account_ids:
        ids_sql = ', '.join(str(int(i)) for i in account_ids)
        acc_filter = f'AND a.Accounts_Id IN ({ids_sql})'
        inv_acc_filter = f'AND Accounts_Id IN ({ids_sql})'
    else:
        acc_filter = ''
        inv_acc_filter = ''

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
    -- Cash, savings, credit cards, loans, assets, liabilities
    -- (Other Investment excluded: handled by other_inv_like below)
    cash_like AS (
        SELECT
            p.period_end,
            a.Accounts_Id,
            a.Accounts_Name,
            a.Accounts_Type,
            CASE
                WHEN a.Accounts_Type IN ('Real Estate', 'Vehicle', 'Asset')
                -- Physical assets cannot go negative (backwards reconstruction artefact)
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
                 WHERE period_end = p.period_end AND Currencies_Id = a.Currencies_Id),
                1
            ) AS balance_eur
        FROM period_dates p
        CROSS JOIN Accounts a
        WHERE a.Accounts_Type NOT IN ('Brokerage', 'Margin', 'Pension', 'Other Investment')
          {acc_filter}
    ),
    -- Other Investment (e.g. Revolut Savings / Flexible Cash Funds):
    -- Accounts_Balance is set by update_investment_balances() which sums both
    -- Investments (CashIn/CashOut unlinked) and Transactions.
    -- Historical reconstruction mirrors the Dashboard: subtract future Transactions
    -- from current balance.  The net of unlinked Investments is baked into
    -- Accounts_Balance, so only future Transactions need to be unwound.
/*
    other_inv_like AS (
        SELECT
            p.period_end,
            a.Accounts_Id,
            a.Accounts_Name,
            a.Accounts_Type,
            (
                a.Accounts_Balance
                - COALESCE((
                    SELECT SUM(Total_Amount) FROM Transactions
                    WHERE Accounts_Id = a.Accounts_Id AND Date > p.period_end
                ), 0)
            ) * COALESCE(
                (SELECT fx_rate FROM daily_fx
                 WHERE period_end = p.period_end AND Currencies_Id = a.Currencies_Id),
                1
            ) AS balance_eur
        FROM period_dates p
        CROSS JOIN Accounts a
        WHERE a.Accounts_Type = 'Other Investment'
          {acc_filter}
    ),
*/
    other_inv_like AS (
        SELECT
            p.period_end,
            a.Accounts_Id,
            a.Accounts_Name,
            a.Accounts_Type,
            GREATEST(0, a.Accounts_Balance - COALESCE((
                SELECT SUM(CASE
                    WHEN Action IN ('CashIn', 'IntInc') THEN  Total_Amount_AccCur
                    WHEN Action IN ('CashOut')          THEN -Total_Amount_AccCur
                    ELSE 0 END)
                FROM Investments
                WHERE Accounts_Id = a.Accounts_Id AND Date > p.period_end
            ), 0)) * COALESCE(
                (SELECT fx_rate FROM daily_fx
                 WHERE period_end = p.period_end AND Currencies_Id = a.Currencies_Id),
                1
            ) AS balance_eur
        FROM period_dates p
        CROSS JOIN Accounts a
        WHERE a.Accounts_Type = 'Other Investment'
          {acc_filter}
    ),    
    -- Brokerage / Margin: forward cumulative qty per security × price × fx
    -- Uses Investments table directly so fully-sold securities appear in history
    investment_universe AS (
        SELECT DISTINCT Securities_Id, Accounts_Id
        FROM Investments
        WHERE Action IN ('Buy', 'Reinvest', 'ShrIn', 'Sell', 'ShrOut')
          {inv_acc_filter}
    ),
    investment_holdings AS (
        SELECT
            p.period_end,
            a.Accounts_Id,
            a.Accounts_Name,
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
                     WHERE period_end = p.period_end AND Currencies_Id = s.Currencies_Id),
                    1
                )
            ) AS balance_eur
        FROM period_dates p
        CROSS JOIN investment_universe i
        JOIN Accounts   a ON i.Accounts_Id   = a.Accounts_Id
        JOIN Securities s ON i.Securities_Id = s.Securities_Id
        WHERE a.Accounts_Type IN ('Brokerage', 'Margin', 'Other Investment')
          {acc_filter}
        GROUP BY p.period_end, a.Accounts_Id, a.Accounts_Name, a.Accounts_Type
    ),
    -- Pension: backwards from current balance via CashIn/CashOut
    pension_like AS (
        SELECT
            p.period_end,
            a.Accounts_Id,
            a.Accounts_Name,
            a.Accounts_Type,
            GREATEST(0, a.Accounts_Balance - COALESCE((
                SELECT SUM(CASE
                    WHEN Action IN ('CashIn', 'IntInc') THEN  Total_Amount_AccCur
                    WHEN Action IN ('CashOut')          THEN -Total_Amount_AccCur
                    ELSE 0 END)
                FROM Investments
                WHERE Accounts_Id = a.Accounts_Id AND Date > p.period_end
            ), 0)) * COALESCE(
                (SELECT fx_rate FROM daily_fx
                 WHERE period_end = p.period_end AND Currencies_Id = a.Currencies_Id),
                1
            ) AS balance_eur
        FROM period_dates p
        CROSS JOIN Accounts a
        WHERE a.Accounts_Type = 'Pension'
          {acc_filter}
    )
    SELECT
        period_end,
        Accounts_Id   AS accounts_id,
        Accounts_Name AS accounts_name,
        Accounts_Type AS accounts_type,
        CASE Accounts_Type
            WHEN 'Credit Card' THEN 'Liabilities'
            WHEN 'Loan'        THEN 'Liabilities'
            WHEN 'Liability'   THEN 'Liabilities'
            ELSE 'Assets'
        END AS section,
        CASE Accounts_Type
            WHEN 'Brokerage'        THEN 'Investments'
            WHEN 'Margin'           THEN 'Investments'
            WHEN 'Pension'          THEN 'Pension'
            WHEN 'Cash'             THEN 'Cash & Bank'
            WHEN 'Checking'         THEN 'Cash & Bank'
            WHEN 'Savings'          THEN 'Cash & Bank'
            WHEN 'Other Investment' THEN 'Cash & Bank'
            WHEN 'Other'            THEN 'Cash & Bank'
            WHEN 'Real Estate'      THEN 'Other Assets'
            WHEN 'Vehicle'          THEN 'Other Assets'
            WHEN 'Asset'            THEN 'Other Assets'
            WHEN 'Credit Card'      THEN 'Credit Cards'
            WHEN 'Loan'             THEN 'Loans'
            WHEN 'Liability'        THEN 'Other Liabilities'
            ELSE 'Other'
        END AS group_name,
        COALESCE(balance_eur, 0) AS balance_eur
    FROM (
        SELECT period_end, Accounts_Id, Accounts_Name, Accounts_Type, balance_eur FROM cash_like
        UNION ALL
        SELECT period_end, Accounts_Id, Accounts_Name, Accounts_Type, balance_eur FROM investment_holdings
        UNION ALL
        SELECT period_end, Accounts_Id, Accounts_Name, Accounts_Type, balance_eur FROM pension_like
        UNION ALL
        SELECT period_end, Accounts_Id, Accounts_Name, Accounts_Type, balance_eur FROM other_inv_like
    ) all_accounts
    ORDER BY period_end, section DESC, group_name, Accounts_Name
    """

    df = pd.read_sql(query, conn)
    conn.close()
    return df


def get_all_accounts_for_nwr():
    """All accounts available for Net Worth Report selection, ordered by type then name."""
    conn = get_connection()
    df = pd.read_sql("""
        SELECT Accounts_Id AS accounts_id, Accounts_Name AS accounts_name,
               Accounts_Type AS accounts_type
        FROM Accounts
        ORDER BY Accounts_Type, Accounts_Name
    """, conn)
    conn.close()
    return df


def get_investment_accounts():
    """Active brokerage / pension / investment accounts only (used for risk analytics)."""
    conn = get_connection()
    df = pd.read_sql("""
        SELECT Accounts_Id AS accounts_id, Accounts_Name AS accounts_name,
               Accounts_Type AS accounts_type
        FROM Accounts
        WHERE Is_Active = TRUE
          AND Accounts_Type IN ('Brokerage', 'Margin', 'Pension', 'Other Investment')
        ORDER BY Accounts_Type, Accounts_Name
    """, conn)
    conn.close()
    return df


def get_price_anomalies(threshold_pct: float = 100.0, securities_ids: tuple = None):
    """
    Detect Historical_Prices rows that are outliers via two independent signals:
      1. Day-to-day spike: price ratio vs previous OR next trading day >= threshold.
      2. Transaction divergence: price deviates from the nearest buy/sell within 7 days
         by >= threshold — catches prices that clearly mismatch a same-week transaction.
         The window is intentionally short (7 days) to avoid false positives from
         cost-basis dummy entries created for corporate restructuring events.
    A LATERAL join fetches the nearest transaction once per price row efficiently.
    """
    conn = get_connection()

    sec_filter = (
        f"AND hp.Securities_Id IN ({', '.join(str(int(i)) for i in securities_ids)})"
        if securities_ids else ""
    )
    ratio = 1.0 + threshold_pct / 100.0

    query = f"""
    WITH price_neighbors AS (
        -- Step 1: compute LAG/LEAD for all price rows (sequential scan, unavoidable).
        SELECT
            hp.Securities_Id,
            s.Securities_Name AS security_name,
            hp.Date,
            hp.Close,
            hp.Source,
            hp.Downloaded_At,
            LAG(hp.Close)  OVER (PARTITION BY hp.Securities_Id ORDER BY hp.Date) AS prev_close,
            LEAD(hp.Close) OVER (PARTITION BY hp.Securities_Id ORDER BY hp.Date) AS next_close
        FROM Historical_Prices hp
        JOIN Securities s ON s.Securities_Id = hp.Securities_Id
        WHERE hp.Close > 0
          {sec_filter}
    ),
    tx_nearby AS (
        -- Step 2: cheap set — which (Securities_Id, Date) pairs are within 7 days
        -- of ANY qualifying transaction.  Uses the small Investments table only.
        SELECT DISTINCT hp.Securities_Id, hp.Date
        FROM Investments i
        JOIN Historical_Prices hp
          ON  hp.Securities_Id = i.Securities_Id
          AND hp.Date BETWEEN i.Date - 7 AND i.Date + 7
        WHERE i.Action IN ('Buy','Sell','Reinvest','ShrIn','ShrOut')
          AND i.Quantity > 0
          {sec_filter.replace('hp.', 'i.')}
    ),
    candidates AS (
        -- Step 3: union of Signal-1 hits and near-transaction rows.
        -- The LATERAL join will run ONLY on these rows, not on all price rows.
        SELECT Securities_Id, Date FROM price_neighbors
        WHERE (prev_close > 0 AND (Close/prev_close >= {ratio} OR prev_close/Close >= {ratio}))
           OR (next_close > 0 AND (Close/next_close >= {ratio} OR next_close/Close >= {ratio}))
        UNION
        SELECT Securities_Id, Date FROM tx_nearby
    ),
    enriched AS (
        -- Step 4: LATERAL join scoped to candidates only.
        SELECT
            pn.*,
            CASE WHEN pn.prev_close > 0
                 THEN ROUND(((pn.Close / pn.prev_close) - 1) * 100, 1) END AS pct_vs_prev,
            CASE WHEN pn.next_close > 0
                 THEN ROUND(((pn.Close / pn.next_close) - 1) * 100, 1) END AS pct_vs_next,
            tx.tx_date,
            tx.tx_action,
            tx.tx_price,
            tx.days_diff
        FROM price_neighbors pn
        JOIN candidates c ON c.Securities_Id = pn.Securities_Id AND c.Date = pn.Date
        LEFT JOIN LATERAL (
            SELECT
                i.Date   AS tx_date,
                i.Action AS tx_action,
                ROUND((i.Total_Amount_AccCur / NULLIF(i.Quantity, 0))::numeric, 4) AS tx_price,
                ABS(pn.Date - i.Date) AS days_diff
            FROM Investments i
            WHERE i.Securities_Id = pn.Securities_Id
              AND i.Action IN ('Buy','Sell','Reinvest','ShrIn','ShrOut')
              AND i.Quantity > 0
              AND ABS(pn.Date - i.Date) <= 7
            ORDER BY ABS(pn.Date - i.Date)
            LIMIT 1
        ) tx ON TRUE
    )
    SELECT
        Securities_Id AS securities_id,
        security_name,
        Date          AS date,
        Close         AS price,
        prev_close,
        next_close,
        pct_vs_prev,
        pct_vs_next,
        tx_date,
        tx_action,
        tx_price,
        days_diff,
        CASE WHEN tx_price > 0
             THEN ROUND(((Close / tx_price) - 1) * 100, 1)
        END           AS pct_vs_tx,
        Source        AS source,
        Downloaded_At AS downloaded_at
    FROM enriched
    WHERE
        (prev_close > 0 AND (Close / prev_close >= {ratio} OR prev_close / Close >= {ratio}))
        OR
        (next_close > 0 AND (Close / next_close >= {ratio} OR next_close / Close >= {ratio}))
        OR
        (tx_price > 0 AND (Close / tx_price >= {ratio} OR tx_price / Close >= {ratio}))
    ORDER BY security_name, date
    """

    df = pd.read_sql(query, conn)
    conn.close()
    return df


def get_missing_tx_prices() -> pd.DataFrame:
    """Return investment transactions whose Price_Per_Share is not yet in Historical_Prices.

    When multiple transactions for the same security exist on the same date the
    average price is used.  Only actions that carry a meaningful per-share price
    are included (Buy, Sell, Reinvest, ShrIn, ShrOut).
    """
    conn = get_connection()
    df = pd.read_sql("""
        SELECT
            i.Securities_Id                      AS securities_id,
            s.Securities_Name                    AS security_name,
            i.Date                               AS date,
            ROUND(AVG(i.Price_Per_Share)::numeric, 4) AS price,
            string_agg(DISTINCT i.Action::text, ', ' ORDER BY i.Action::text) AS actions,
            COUNT(*)                             AS tx_count
        FROM Investments i
        JOIN Securities s ON s.Securities_Id = i.Securities_Id
        WHERE i.Price_Per_Share > 0
          AND i.Action IN ('Buy','Sell','Reinvest','ShrIn','ShrOut')
          AND NOT EXISTS (
              SELECT 1 FROM Historical_Prices hp
              WHERE hp.Securities_Id = i.Securities_Id
                AND hp.Date          = i.Date
          )
        GROUP BY i.Securities_Id, s.Securities_Name, i.Date
        ORDER BY s.Securities_Name, i.Date
    """, conn)
    conn.close()
    return df


def get_investments_with_dummy_prices(price_tolerance_pct: float = 1.0) -> pd.DataFrame:
    """Return Investments rows where Quantity or Price_Per_Share looks like a placeholder.

    Detection: Price_Per_Share is a whole number (no decimal component) AND either
    a Historical_Price exists (buy side) or there are corresponding buys to match against
    (sell side).

    The second branch (whole-number quantity + price differs from close) uses a
    percentage-based tolerance so that normal intraday execution-price variation is
    not flagged as a dummy.  price_tolerance_pct defaults to 1.0 (1 %).

    Preview columns:
      • Buys  — new_qty = Total_Amount_SecCur / hist_price  (phase-1 formula)
      • Sells — new_qty = total_buy_qty × (sell_total / all_sell_total)  (phase-2 formula,
                so positions close correctly)

    Total_Amount_AccCur is never touched.
    """
    conn = get_connection()
    tol = float(price_tolerance_pct) / 100.0   # e.g. 1.0 % → 0.01
    df = pd.read_sql("""
        WITH
        -- Identify candidate rows (whole-number price → dummy placeholder)
        candidates AS (
            SELECT i.Investments_Id, i.Accounts_Id, i.Securities_Id,
                   i.Date, i.Action::text AS action,
                   i.Total_Amount_AccCur, i.Quantity, i.Price_Per_Share
            FROM Investments i
            WHERE i.Action IN ('Buy','Sell','Reinvest','ShrIn','ShrOut')
              AND i.Price_Per_Share > 0
              AND i.Total_Amount_AccCur    > 0
              AND (
                  -- Whole-number price that also differs from hist close beyond tolerance
                  (i.Price_Per_Share = FLOOR(i.Price_Per_Share)
                   AND EXISTS (
                       SELECT 1 FROM Historical_Prices hp2
                       WHERE hp2.Securities_Id = i.Securities_Id
                         AND hp2.Date          = i.Date
                         AND ABS(i.Price_Per_Share - hp2.Close)
                             / NULLIF(hp2.Close, 0) > %(tol)s
                   ))
                  OR
                  -- Whole-number quantity whose price also differs beyond tolerance
                  (i.Quantity = FLOOR(i.Quantity)
                   AND EXISTS (
                       SELECT 1 FROM Historical_Prices hp2
                       WHERE hp2.Securities_Id = i.Securities_Id
                         AND hp2.Date          = i.Date
                         AND ABS(i.Price_Per_Share - hp2.Close)
                             / NULLIF(hp2.Close, 0) > %(tol)s
                   ))
              )
        ),
        -- Normalised buy totals per (account, security) — used to pin sell quantities
        buy_totals AS (
            SELECT c.Accounts_Id, c.Securities_Id,
                   SUM(ROUND((c.Total_Amount_AccCur / NULLIF(hp.Close, 0))::numeric, 6)) AS total_buy_qty
            FROM candidates c
            JOIN Historical_Prices hp
                 ON hp.Securities_Id = c.Securities_Id AND hp.Date = c.Date
            WHERE c.action IN ('Buy','Reinvest','ShrIn')
            GROUP BY c.Accounts_Id, c.Securities_Id
        ),
        -- Use ABS so losing trades (negative Total_Amount) don't invert weights
        sell_totals AS (
            SELECT Accounts_Id, Securities_Id,
                   SUM(ABS(Total_Amount_AccCur)) AS total_sell_amt_abs
            FROM candidates
            WHERE action IN ('Sell','ShrOut')
            GROUP BY Accounts_Id, Securities_Id
        )
        SELECT
            c.Investments_Id                        AS investments_id,
            c.Accounts_Id                           AS accounts_id,
            a.Accounts_Name                         AS account_name,
            c.Securities_Id                         AS securities_id,
            s.Securities_Name                       AS security_name,
            c.Date                                  AS date,
            c.action,
            c.Total_Amount_AccCur                          AS total_amount,
            c.Quantity                              AS current_qty,
            c.Price_Per_Share                       AS current_price,
            hp.Close                                AS hist_price,
            CASE
                WHEN c.action IN ('Buy','Reinvest','ShrIn') THEN
                    ROUND((c.Total_Amount_AccCur / NULLIF(hp.Close, 0))::numeric, 6)
                ELSE
                    -- sell: proportional share of total normalised buy qty;
                    -- ABS(Total_Amount) so losing trades (negative) stay positive
                    ROUND((bt.total_buy_qty
                           * (ABS(c.Total_Amount_AccCur) / NULLIF(st.total_sell_amt_abs, 0)))::numeric, 6)
            END                                     AS new_qty,
            CASE
                WHEN c.action IN ('Buy','Reinvest','ShrIn') THEN hp.Close
                ELSE
                    ROUND((ABS(c.Total_Amount_AccCur)
                           / NULLIF(bt.total_buy_qty
                                    * (ABS(c.Total_Amount_AccCur) / NULLIF(st.total_sell_amt_abs, 0)), 0))::numeric, 4)
            END                                     AS new_price
        FROM candidates c
        JOIN Securities s ON s.Securities_Id = c.Securities_Id
        JOIN Accounts   a ON a.Accounts_Id   = c.Accounts_Id
        LEFT JOIN Historical_Prices hp
             ON hp.Securities_Id = c.Securities_Id AND hp.Date = c.Date
        LEFT JOIN buy_totals  bt ON bt.Accounts_Id  = c.Accounts_Id
                                AND bt.Securities_Id = c.Securities_Id
        LEFT JOIN sell_totals st ON st.Accounts_Id  = c.Accounts_Id
                                AND st.Securities_Id = c.Securities_Id
        -- Buys require a hist price; sells require a buy total to distribute against
        WHERE (c.action IN ('Buy','Reinvest','ShrIn') AND hp.Close IS NOT NULL)
           OR (c.action IN ('Sell','ShrOut')           AND bt.total_buy_qty IS NOT NULL)
        ORDER BY s.Securities_Name, c.Date
    """, conn, params={"tol": tol})
    conn.close()
    return df


def get_all_securities_for_filter():
    """All securities for use in filter dropdowns."""
    conn = get_connection()
    df = pd.read_sql(
        "SELECT Securities_Id AS securities_id, Securities_Name AS securities_name "
        "FROM Securities ORDER BY Securities_Name",
        conn,
    )
    conn.close()
    return df


def get_app_setting(key: str) -> str | None:
    """Read a single text value from app_settings. Returns None if not found."""
    conn = get_connection()
    cur  = conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT)
        """)
        conn.commit()
        cur.execute("SELECT value FROM app_settings WHERE key = %s", (key,))
        row = cur.fetchone()
        return row[0] if row else None
    except Exception:
        conn.rollback()
        return None
    finally:
        cur.close()
        conn.close()


def save_app_setting(key: str, value: str) -> None:
    """Upsert a single text value into app_settings."""
    conn = get_connection()
    cur  = conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT)
        """)
        cur.execute("""
            INSERT INTO app_settings (key, value) VALUES (%s, %s)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """, (key, value))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


# ---------------------------------------------------------------------------
# Import: ignored records  (source, desc_key) → skip forever
# ---------------------------------------------------------------------------

_SQL_CREATE_IGNORED = """
    CREATE TABLE IF NOT EXISTS import_ignored_records (
        source     TEXT        NOT NULL,
        desc_key   TEXT        NOT NULL,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        PRIMARY KEY (source, desc_key)
    )
"""


def get_ignored_records(source: str) -> set:
    """Return all desc_keys currently marked as 'ignore' for *source*."""
    conn = get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(_SQL_CREATE_IGNORED)
        conn.commit()
        cur.execute(
            "SELECT desc_key FROM import_ignored_records WHERE source = %s",
            (source,),
        )
        return {row[0] for row in cur.fetchall()}
    finally:
        cur.close()
        conn.close()


def save_ignored_records(source: str, desc_keys: list) -> None:
    """Add desc_keys to the ignore list (idempotent)."""
    if not desc_keys:
        return
    conn = get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(_SQL_CREATE_IGNORED)
        for key in desc_keys:
            cur.execute(
                "INSERT INTO import_ignored_records (source, desc_key) "
                "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (source, key),
            )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def remove_ignored_records(source: str, desc_keys: list) -> None:
    """Remove desc_keys from the ignore list."""
    if not desc_keys:
        return
    conn = get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(_SQL_CREATE_IGNORED)
        for key in desc_keys:
            cur.execute(
                "DELETE FROM import_ignored_records "
                "WHERE source = %s AND desc_key = %s",
                (source, key),
            )
        conn.commit()
    finally:
        cur.close()
        conn.close()


# ---------------------------------------------------------------------------
# Import: security mappings  (source, source_symbol) → securities_id
# ---------------------------------------------------------------------------

_SQL_CREATE_MAPPINGS = """
    CREATE TABLE IF NOT EXISTS import_security_mappings (
        source          TEXT    NOT NULL,
        source_symbol   TEXT    NOT NULL,
        securities_id   INTEGER NOT NULL,
        PRIMARY KEY (source, source_symbol)
    )
"""


def get_security_mappings(source: str) -> dict:
    """Return {source_symbol → securities_id} for *source*."""
    conn = get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(_SQL_CREATE_MAPPINGS)
        conn.commit()
        cur.execute(
            "SELECT source_symbol, securities_id "
            "FROM import_security_mappings WHERE source = %s",
            (source,),
        )
        return {row[0]: row[1] for row in cur.fetchall()}
    finally:
        cur.close()
        conn.close()


def save_security_mappings(source: str, mappings: dict) -> None:
    """Upsert {source_symbol → securities_id} for *source*."""
    if not mappings:
        return
    conn = get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(_SQL_CREATE_MAPPINGS)
        for symbol, sec_id in mappings.items():
            cur.execute(
                """INSERT INTO import_security_mappings
                       (source, source_symbol, securities_id)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (source, source_symbol)
                   DO UPDATE SET securities_id = EXCLUDED.securities_id""",
                (source, symbol, sec_id),
            )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def delete_security_mapping(source: str, source_symbol: str) -> None:
    """Remove one mapping."""
    conn = get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(_SQL_CREATE_MAPPINGS)
        cur.execute(
            "DELETE FROM import_security_mappings "
            "WHERE source = %s AND source_symbol = %s",
            (source, source_symbol),
        )
        conn.commit()
    finally:
        cur.close()
        conn.close()


def get_nwr_account_selection(settings_key: str = 'nwr_account_ids'):
    """Load saved account selection from app_settings. Returns list of ints or None."""
    conn = get_connection()
    cur = conn.cursor()
    import json
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT)
        """)
        conn.commit()
        cur.execute("SELECT value FROM app_settings WHERE key = %s", (settings_key,))
        row = cur.fetchone()
        return json.loads(row[0]) if row else None
    except Exception:
        conn.rollback()
        return None
    finally:
        cur.close()
        conn.close()


def get_nwr_security_detail(start_date: str, interval: str, account_id: int):
    """Security-level historical values for a single investment account (for NWR drilldown)."""
    conn = get_connection()

    trunc_map = {'Year': 'year', 'Quarter': 'quarter', 'Month': 'month'}
    intv_map  = {'Year': '1 year', 'Quarter': '3 months', 'Month': '1 month'}
    trunc_unit  = trunc_map.get(interval, 'year')
    pg_interval = intv_map.get(interval, '1 year')
    acc_id = int(account_id)

    query = f"""
    WITH
    period_dates AS (
        SELECT (gs - INTERVAL '1 day')::date AS period_end
        FROM generate_series(
            date_trunc('{trunc_unit}', '{start_date}'::date) + '{pg_interval}'::interval,
            date_trunc('{trunc_unit}', CURRENT_DATE),
            '{pg_interval}'::interval
        ) gs
        UNION SELECT CURRENT_DATE::date
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
    sec_universe AS (
        SELECT DISTINCT Securities_Id
        FROM Investments
        WHERE Accounts_Id = {acc_id}
          AND Action IN ('Buy','Reinvest','ShrIn','Sell','ShrOut')
    )
    SELECT
        p.period_end,
        s.Securities_Name  AS security_name,
        c.Currencies_ShortName AS currency,
        GREATEST(COALESCE((
            SELECT SUM(CASE
                WHEN Action IN ('Buy','Reinvest','ShrIn') THEN  Quantity
                WHEN Action IN ('Sell','ShrOut')          THEN -Quantity
                ELSE 0 END)
            FROM Investments i2
            WHERE i2.Securities_Id = u.Securities_Id
              AND i2.Accounts_Id   = {acc_id}
              AND i2.Date          <= p.period_end
        ), 0), 0) AS qty_at_date,
        COALESCE((
            SELECT Close FROM Historical_Prices
            WHERE Securities_Id = u.Securities_Id AND Date <= p.period_end
            ORDER BY Date DESC LIMIT 1
        ), 0) AS price_at_date,
        GREATEST(COALESCE((
            SELECT SUM(CASE
                WHEN Action IN ('Buy','Reinvest','ShrIn') THEN  Quantity
                WHEN Action IN ('Sell','ShrOut')          THEN -Quantity
                ELSE 0 END)
            FROM Investments i2
            WHERE i2.Securities_Id = u.Securities_Id
              AND i2.Accounts_Id   = {acc_id}
              AND i2.Date          <= p.period_end
        ), 0), 0) *
        COALESCE((
            SELECT Close FROM Historical_Prices
            WHERE Securities_Id = u.Securities_Id AND Date <= p.period_end
            ORDER BY Date DESC LIMIT 1
        ), 0) *
        COALESCE(
            (SELECT fx_rate FROM daily_fx
             WHERE period_end = p.period_end AND Currencies_Id = s.Currencies_Id),
            1
        ) AS value_eur
    FROM period_dates p
    CROSS JOIN sec_universe u
    JOIN Securities s ON u.Securities_Id = s.Securities_Id
    JOIN Currencies c ON s.Currencies_Id = c.Currencies_Id
    ORDER BY p.period_end, value_eur DESC, s.Securities_Name
    """

    df = pd.read_sql(query, conn)
    conn.close()
    return df


def get_portfolio_signals(selected_acc_id=None): # Προσθήκη '=' εδώ
    """Get signals for my investment portfolio."""
    conn = get_connection()
    
    # Χρησιμοποιούμε απλό τριπλό string (όχι f-string) για ασφάλεια με το pd.read_sql
    query = """
        WITH base_data AS (
            SELECT Securities_Id, Date, Close,
                   (Close / LAG(Close) OVER (PARTITION BY Securities_Id ORDER BY Date) - 1) as daily_ret
            FROM Historical_Prices
            WHERE Date >= (CURRENT_DATE - INTERVAL '62 months')
        ),
        ranked_prices AS (
            SELECT Securities_Id, Date, Close as price_today,
                   LAG(Close, 1) OVER (PARTITION BY Securities_Id ORDER BY Date) as price_1d,
                   LAG(Close, 5) OVER (PARTITION BY Securities_Id ORDER BY Date) as price_1w,
                   LAG(Close, 21) OVER (PARTITION BY Securities_Id ORDER BY Date) as price_1m,
                   LAG(Close, 63) OVER (PARTITION BY Securities_Id ORDER BY Date) as price_3m,
                   LAG(Close, 126) OVER (PARTITION BY Securities_Id ORDER BY Date) as price_6m,
                   LAG(Close, 252) OVER (PARTITION BY Securities_Id ORDER BY Date) as price_1y,
                   LAG(Close, 756) OVER (PARTITION BY Securities_Id ORDER BY Date) as price_3y,
                   ROW_NUMBER() OVER (PARTITION BY Securities_Id ORDER BY Date DESC) as rev_rank
            FROM base_data
        ),
        ytd_prices AS (
            SELECT DISTINCT ON (Securities_Id) Securities_Id, Close as price_ytd_start
            FROM Historical_Prices
            WHERE Date < date_trunc('year', CURRENT_DATE)
            ORDER BY Securities_Id, Date DESC
        ),
        -- 3-year High/Low using only prices dated AFTER the most recent
        -- Split/Reverse Split for that security (if one exists in the window).
        -- This avoids mixing pre- and post-split prices when the data provider
        -- has not retroactively adjusted historical quotes.
        -- When no split exists in the window, all 3 years of prices are used.
        last_split_in_window AS (
            SELECT Securities_Id,
                   MAX(Effective_Date) AS split_date
              FROM Corporate_Actions
             WHERE Action_Type IN ('Split', 'Reverse Split')
               AND Effective_Date >= (CURRENT_DATE - INTERVAL '3 years')
               AND Effective_Date <= CURRENT_DATE
             GROUP BY Securities_Id
        ),
        range_3y AS (
            SELECT hp.Securities_Id,
                   ROUND(MAX(hp.Close)::numeric, 4) AS high_3y,
                   ROUND(MIN(hp.Close)::numeric, 4) AS low_3y
              FROM Historical_Prices hp
              LEFT JOIN last_split_in_window ls ON ls.Securities_Id = hp.Securities_Id
             WHERE hp.Date >= (CURRENT_DATE - INTERVAL '3 years')
               AND hp.Date >= COALESCE(ls.split_date, (CURRENT_DATE - INTERVAL '3 years'))
             GROUP BY hp.Securities_Id
        ),
        latest_only AS (
            SELECT rp.*, yp.price_ytd_start
            FROM ranked_prices rp
            LEFT JOIN ytd_prices yp ON rp.Securities_Id = yp.Securities_Id
            WHERE rp.rev_rank = 1
        ),
        -- All 4 volatility windows in one GROUP BY scan instead of 4 correlated subqueries
        volatility AS (
            SELECT Securities_Id,
                   ROUND((STDDEV(daily_ret) FILTER (WHERE Date > CURRENT_DATE - INTERVAL '1 month')   * SQRT(252) * 100)::numeric, 2) AS vol_1m_ann,
                   ROUND((STDDEV(daily_ret) FILTER (WHERE Date > CURRENT_DATE - INTERVAL '3 months')  * SQRT(252) * 100)::numeric, 2) AS vol_3m_ann,
                   ROUND((STDDEV(daily_ret) FILTER (WHERE Date > CURRENT_DATE - INTERVAL '12 months') * SQRT(252) * 100)::numeric, 2) AS vol_1y_ann,
                   ROUND((STDDEV(daily_ret) FILTER (WHERE Date >= date_trunc('year', CURRENT_DATE))   * SQRT(252) * 100)::numeric, 2) AS vol_ytd_ann
            FROM base_data
            GROUP BY Securities_Id
        ),
        -- Latest FX rate per currency — replaces 2 correlated subqueries in recommendations
        latest_fx AS (
            SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
            FROM Historical_FX
            WHERE Date <= CURRENT_DATE
            ORDER BY Currencies_Id_1, Date DESC
        ),
        performance_data AS (
            SELECT
                lo.Securities_Id, Sec.Securities_Name,
                lo.Date AS price_today_date,
                lo.price_today,
                ROUND(((lo.price_today / NULLIF(lo.price_1d, 0)) - 1) * 100, 2) as daily_chg_pct,
                ROUND(((lo.price_today / NULLIF(lo.price_1w, 0)) - 1) * 100, 2) as weekly_chg_pct,
                ROUND(((lo.price_today / NULLIF(lo.price_1m, 0)) - 1) * 100, 2) as monthly_chg_pct,
                ROUND(((lo.price_today / NULLIF(lo.price_3m, 0)) - 1) * 100, 2) as quarterly_chg_pct,
                ROUND(((lo.price_today / NULLIF(lo.price_6m, 0)) - 1) * 100, 2) as semiannual_chg_pct,
                ROUND(((lo.price_today / NULLIF(lo.price_1y, 0)) - 1) * 100, 2) as annual_chg_pct,
                ROUND(((lo.price_today / NULLIF(lo.price_3y, 0)) - 1) * 100, 2) as triannual_chg_pct,
                ROUND(((lo.price_today / NULLIF(lo.price_ytd_start, 0)) - 1) * 100, 2) as ytd_chg_pct,
                v.vol_1m_ann, v.vol_3m_ann, v.vol_1y_ann, v.vol_ytd_ann
            FROM latest_only lo
            JOIN Securities Sec ON lo.Securities_Id = Sec.Securities_Id
                AND Sec.Is_Active
                AND lo.Date > (CURRENT_DATE - INTERVAL '15 days')
            LEFT JOIN volatility v ON v.Securities_Id = lo.Securities_Id
        ),
        rfr AS (
            SELECT COALESCE(annual_chg_pct, 2.36) as value 
            FROM performance_data 
            WHERE Securities_Name LIKE 'Hellenic T-Bill 52W%%'
            LIMIT 1
        ),
        investment_signals AS (
            SELECT *,
                ROUND(((monthly_chg_pct * 0.5) + (quarterly_chg_pct * 0.3) + (annual_chg_pct * 0.2))::numeric, 2) as quality_score,
                ROUND(((annual_chg_pct - (SELECT value FROM rfr)) / NULLIF(vol_1y_ann, 0))::numeric, 2) as sharpe_ratio
            FROM performance_data
            WHERE vol_1y_ann > 0 
        ),
        portfolio_status AS (
            SELECT sig.*,
                SUM(COALESCE(h.Quantity, 0)) as current_qty,
                (SUM(COALESCE(h.Quantity, 0)) * sig.price_today) as market_value_base_curr,
                SUM(COALESCE(h.Fifo_Avg_Cost_EUR, 0) * COALESCE(h.Quantity, 0)) as total_cost_eur
            FROM investment_signals sig
            LEFT JOIN Holdings h ON sig.Securities_Id = h.Securities_Id
                AND (%s IS NULL OR h.Accounts_Id = %s)
	        GROUP BY sig.Securities_Id, sig.Securities_Name, sig.price_today_date, sig.price_today,
                     sig.daily_chg_pct, sig.weekly_chg_pct,
                     sig.monthly_chg_pct, sig.quarterly_chg_pct, sig.semiannual_chg_pct, sig.annual_chg_pct,
                     sig.triannual_chg_pct, sig.ytd_chg_pct, sig.vol_1m_ann, sig.vol_3m_ann, sig.vol_1y_ann,
                     sig.vol_ytd_ann, sig.quality_score, sig.sharpe_ratio
        ),
        recommendations AS (
            SELECT
                sig.*,
                sig.market_value_base_curr * COALESCE(fx.FX_Rate, 1) as current_value_eur,
                CASE WHEN sig.total_cost_eur > 0 THEN
                    sig.market_value_base_curr * COALESCE(fx.FX_Rate, 1) - sig.total_cost_eur
                ELSE NULL END as unrealized_pnl_eur,
                sec.Analyst_Rating as wall_street_view,
                sec.Analyst_Target_Price as target_price,
                ROUND((((sec.Analyst_Target_Price / NULLIF(sig.price_today, 0)) - 1) * 100)::numeric, 2) as upside_pct,
                r3.high_3y,
                r3.low_3y,
                ROUND((((sig.price_today / NULLIF(r3.high_3y, 0)) - 1) * 100)::numeric, 2) as pct_from_high_3y,
                ROUND((((sig.price_today / NULLIF(r3.low_3y,  0)) - 1) * 100)::numeric, 2) as pct_from_low_3y,
                ROUND(sec.Dividend_Yield::numeric, 2) as fwd_yield_pct,
                CASE
                    WHEN current_qty > 0 AND (sharpe_ratio < 0 OR quality_score < -5) THEN '🔴 SELL / REDUCE'
                    WHEN sharpe_ratio > 1.2 AND quality_score > 10 THEN '🟢 STRONG BUY'
                    WHEN sharpe_ratio > 0.7 OR quality_score > 8 THEN '🟢 BUY'
                    WHEN sharpe_ratio > 0.3 OR quality_score > 0 THEN '🟡 HOLD'
                    WHEN current_qty = 0 AND sharpe_ratio > 0.5 THEN '👀 WATCHLIST'
                    ELSE '🟡 HOLD'
                END as recommendation_signal
            FROM portfolio_status sig
            JOIN Securities sec ON sig.Securities_Id = sec.Securities_Id
            LEFT JOIN range_3y r3 ON r3.Securities_Id = sig.Securities_Id
            LEFT JOIN latest_fx fx ON fx.Currencies_Id_1 = sec.Currencies_Id
        )
        SELECT *,
            CASE
                -- ── Math BUY + Analyst alignment ───────────────────────────────
                WHEN recommendation_signal LIKE '🟢%%' AND wall_street_view IN ('buy', 'strong_buy') AND upside_pct > 20 THEN '🔥 HIGH CONVICTION BUY'
                WHEN recommendation_signal LIKE '🟢%%' AND wall_street_view = 'strong_buy'            THEN '💎 STRONG CONVICTION'
                WHEN recommendation_signal LIKE '🟢%%' AND wall_street_view = 'buy'                   THEN '💎 CONVICTION BUY'
                WHEN recommendation_signal LIKE '🟢%%' AND wall_street_view = 'hold'                  THEN '🚀 MOMENTUM BUY'
                WHEN recommendation_signal LIKE '🟢%%' AND wall_street_view IN ('sell', 'underperform') THEN '🔍 CONTRARIAN BUY'
                WHEN recommendation_signal LIKE '🟢%%' AND wall_street_view IS NULL                   THEN '⚙️ ALGO BUY'
                -- ── Math SELL + Analyst alignment ──────────────────────────────
                WHEN recommendation_signal LIKE '🔴%%' AND wall_street_view IN ('sell', 'underperform') THEN '⚠️ CONVICTION SELL'
                WHEN recommendation_signal LIKE '🔴%%' AND wall_street_view = 'hold'                  THEN '📉 MOMENTUM SELL'
                WHEN recommendation_signal LIKE '🔴%%' AND wall_street_view IN ('buy', 'strong_buy')  THEN '🔍 CONTRARIAN SELL'
                WHEN recommendation_signal LIKE '🔴%%' AND wall_street_view IS NULL                   THEN '⚙️ ALGO SELL'
                -- ── Math HOLD + Analyst view ────────────────────────────────────
                WHEN recommendation_signal LIKE '🟡%%' AND wall_street_view IN ('sell', 'underperform') THEN '⚠️ ANALYST CAUTION'
                WHEN recommendation_signal LIKE '🟡%%' AND wall_street_view IN ('buy', 'strong_buy')  THEN '📈 ANALYST UPGRADE'
                -- ── Watchlist + Analyst view ────────────────────────────────────
                WHEN recommendation_signal LIKE '👀%%' AND wall_street_view IN ('buy', 'strong_buy')  THEN '🔬 WATCH: ANALYST BUY'
                WHEN recommendation_signal LIKE '👀%%' AND wall_street_view IN ('sell', 'underperform') THEN '🔬 WATCH: ANALYST SELL'
                ELSE recommendation_signal
            END as final_signal
        FROM recommendations
        ORDER BY sharpe_ratio DESC;
    """
    
    # Το pandas.read_sql χειρίζεται σωστά τις παραμέτρους για την αποφυγή SQL Injection
    df = pd.read_sql(query, conn, params=(selected_acc_id, selected_acc_id))
    cur = conn.cursor()
    cur.close()
    conn.close()
    return df



def get_pnl_report_data(start_date: str = '1900-01-01', end_date: str = None):
    """Get P&L report data.

    Args:
        start_date: Beginning of the 'all-time' window (ISO date string, default '1900-01-01').
        end_date:   Reference date treated as 'today' (ISO date string, default: today).
    """
    from datetime import date as _date
    if end_date is None:
        end_date = _date.today().isoformat()

    conn = get_connection()

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
            --    '1900-01-01'::date as all_time_start,
                start_date as all_time_start,
            --    CURRENT_DATE::date as today
                end_date as today
            FROM DateRange
        ),
        -- Βρίσκουμε κάθε συνδυασμό Λογαριασμού/Τίτλου που υπήρξε ποτέ
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
            -- Single scan per (Account, Security) instead of 6 separate correlated subqueries
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
            -- One scan per security to find latest price date for each period cutoff
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
            -- One scan per currency to find latest FX date for each period cutoff
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
                -- DTD CF
                SUM(CASE WHEN i.Date > (SELECT dtd_start FROM periods) THEN
                    (CASE WHEN i.Action IN ('Buy', 'MiscExp') THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0), i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0))
                          WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'Reinvest', 'RtrnCap') THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0), i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0))
                          ELSE 0 END) ELSE 0 END) AS cf_dtd,
                -- DTD CF EUR
                SUM(CASE WHEN i.Date > (SELECT dtd_start FROM periods) THEN
                    (CASE WHEN i.Action IN ('Buy', 'MiscExp') THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close + COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                          WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'Reinvest', 'RtrnCap') THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close - COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                          ELSE 0 END) ELSE 0 END) AS cf_dtd_eur,
                -- WTD CF
                SUM(CASE WHEN i.Date > (SELECT wtd_start FROM periods) THEN
                    (CASE WHEN i.Action IN ('Buy', 'MiscExp') THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0), i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0))
                          WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'Reinvest', 'RtrnCap') THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0), i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0))
                          ELSE 0 END) ELSE 0 END) AS cf_wtd,
                -- WTD CF EUR
                SUM(CASE WHEN i.Date > (SELECT wtd_start FROM periods) THEN
                    (CASE WHEN i.Action IN ('Buy', 'MiscExp') THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close + COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                          WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'Reinvest', 'RtrnCap') THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close - COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                          ELSE 0 END) ELSE 0 END) AS cf_wtd_eur,
                -- MTD CF
                SUM(CASE WHEN i.Date > (SELECT mtd_start FROM periods) THEN
                    (CASE WHEN i.Action IN ('Buy', 'MiscExp') THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0), i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0))
                          WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'Reinvest', 'RtrnCap') THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0), i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0))
                          ELSE 0 END) ELSE 0 END) AS cf_mtd,
                -- MTD CF EUR
                SUM(CASE WHEN i.Date > (SELECT mtd_start FROM periods) THEN
                    (CASE WHEN i.Action IN ('Buy', 'MiscExp') THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close + COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                          WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'Reinvest', 'RtrnCap') THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close - COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                          ELSE 0 END) ELSE 0 END) AS cf_mtd_eur,
                -- QTD CF
                SUM(CASE WHEN i.Date > (SELECT qtd_start FROM periods) THEN
                    (CASE WHEN i.Action IN ('Buy', 'MiscExp') THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0), i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0))
                          WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'Reinvest', 'RtrnCap') THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0), i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0))
                          ELSE 0 END) ELSE 0 END) AS cf_qtd,
                -- QTD CF EUR
                SUM(CASE WHEN i.Date > (SELECT qtd_start FROM periods) THEN
                    (CASE WHEN i.Action IN ('Buy', 'MiscExp') THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close + COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                          WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'Reinvest', 'RtrnCap') THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close - COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                          ELSE 0 END) ELSE 0 END) AS cf_qtd_eur,
                -- YTD CF
                SUM(CASE WHEN i.Date > (SELECT ytd_start FROM periods) THEN
                    (CASE WHEN i.Action IN ('Buy', 'MiscExp') THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0), i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0))
                          WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'Reinvest', 'RtrnCap') THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0), i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0))
                          ELSE 0 END) ELSE 0 END) AS cf_ytd,
                    -- YTD CF EUR
                SUM(CASE WHEN i.Date > (SELECT ytd_start FROM periods) THEN
                    (CASE WHEN i.Action IN ('Buy', 'MiscExp') THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close + COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                          WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'Reinvest', 'RtrnCap') THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close - COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                          ELSE 0 END) ELSE 0 END) AS cf_ytd_eur,
                -- net_invested_ytd_eur: JOIN replaces per-row correlated subqueries
                SUM(CASE WHEN i.Date > (SELECT ytd_start FROM periods) THEN
                    CASE WHEN i.Action IN ('Buy', 'CashOut', 'MiscExp')
                            THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close + COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                         WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'CashIn', 'RtrnCap')
                            THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close - COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                         ELSE 0 END
                ELSE 0 END) AS net_invested_ytd_eur,
                -- Συνολικό CF (για Realized P&L)
                SUM(CASE WHEN i.Action IN ('Buy', 'MiscExp', 'Reinvest', 'Exercise', 'ShrIn') THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0), i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0))
                         WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'RtrnCap', 'ShrOut') THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0), i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0))
                         ELSE 0 END) AS cf_all_time,
                -- Συνολικό CF (για Realized P&L)
                SUM(CASE WHEN i.Action IN ('Buy', 'MiscExp', 'Reinvest', 'Exercise', 'ShrIn') THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close + COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                         WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'RtrnCap', 'ShrOut') THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close - COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                         ELSE 0 END) AS cf_all_time_eur,
                -- net_invested_all_time_eur: JOIN replaces per-row correlated subqueries
                SUM(CASE WHEN i.Action IN ('Buy', 'CashOut', 'MiscExp')
                            THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close + COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                         WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'CashIn', 'RtrnCap')
                            THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close - COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                         ELSE 0 END) AS net_invested_all_time_eur,
                -- gross_invested_all_time_eur: total cost of all buys (correct denominator for % return)
                -- Using net_invested as denominator is wrong for closed/profitable positions because
                -- proceeds from sells make it negative, flipping the sign of the percentage.
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
            -- Fallback price from Historical_Prices for Reinvest rows with no price/amount data
            -- Deliberately restricted to Reinvest so that intentional zero-value transactions
            -- (e.g. manual Sell entries with no amount) are never auto-valued.
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
        -- Account-level capital inflows — used as the ROI denominator.
        -- Priority 1: explicit CashIn (Action='CashIn', Securities_Id IS NULL) recorded
        --             directly in the Investments table (e.g. pension contributions, or
        --             deposits recorded before a Buy).
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
            WHERE i.Securities_Id IS NULL   -- CashIn/CashOut never have a security
            GROUP BY i.Accounts_Id
        ),
        -- Priority 2: explicit cash transfers recorded in the LINKED cash account
        --             (Transactions.Accounts_Id_Target = investment_account).
        --             Buy/Sell-linked transactions use transactions_id and have
        --             Accounts_Id_Target = NULL, so they are NOT included here.
        account_linked_flows AS (
            SELECT
                a.Accounts_Id AS inv_acc_id,
                SUM(-t.Total_Amount * COALESCE(fxl.FX_Rate, 1)) AS linked_cashin_eur
            FROM Accounts a
            INNER JOIN Accounts al ON al.Accounts_Id = a.Accounts_Id_Linked
            INNER JOIN Transactions t
                    ON t.Accounts_Id       = al.Accounts_Id
                   AND t.Accounts_Id_Target = a.Accounts_Id
                   AND t.Total_Amount < 0   -- negative = cash leaving the linked account (deposit into investment)
            LEFT JOIN Historical_FX fxl
                   ON fxl.Currencies_Id_1 = al.Currencies_Id
                  AND fxl.Date = t.Date
            GROUP BY a.Accounts_Id
        )
        SELECT
            pf.Accounts_Name, pf.Securities_Name,

            (pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)) as current_value_eur,
            
            -- DTD Analysis
            -- 1. P&L λόγω μεταβολής Τιμής (Market Effect YTD)
            ((pf.qty_today * pf.price_today) - (pf.qty_dtd * pf.price_dtd) - COALESCE(cf.cf_dtd, 0)) * COALESCE(pf.fx_today, 1) as pnl_dtd_market_eur,
            -- 2. P&L λόγω μεταβολής Ισοτιμίας (FX Effect YTD)
            (pf.qty_dtd * pf.price_dtd) * (COALESCE(pf.fx_today, 1) - COALESCE(pf.fx_dtd, 1)) as pnl_dtd_fx_eur,
            -- 3. Total P&L DTD		
            ((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)) - (pf.qty_dtd * pf.price_dtd * COALESCE(pf.fx_dtd, 1)) - COALESCE(cf.cf_dtd_eur, 0)) as pnl_dtd_eur,
            -- DTD P&L %
            CASE WHEN (pf.qty_dtd * pf.price_dtd * COALESCE(pf.fx_dtd, 1)) = 0 THEN 0
                 ELSE (((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)) - (pf.qty_dtd * pf.price_dtd * COALESCE(pf.fx_dtd, 1)) - COALESCE(cf.cf_dtd_eur, 0)) / (pf.qty_dtd * pf.price_dtd * COALESCE(pf.fx_dtd, 1))) * 100
            END as pnl_dtd_percent,

            -- WTD/MTD/QTD Analysis
            ((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)) - (pf.qty_wtd * pf.price_wtd * COALESCE(pf.fx_wtd, 1)) - COALESCE(cf.cf_wtd_eur, 0)) as pnl_wtd_eur,
            ((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)) - (pf.qty_mtd * pf.price_mtd * COALESCE(pf.fx_mtd, 1)) - COALESCE(cf.cf_mtd_eur, 0)) as pnl_mtd_eur,
            ((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)) - (pf.qty_qtd * pf.price_qtd * COALESCE(pf.fx_qtd, 1)) - COALESCE(cf.cf_qtd_eur, 0)) as pnl_qtd_eur,
            
            -- YTD Analysis
            -- 1. P&L λόγω μεταβολής Τιμής (Market Effect YTD)
			(CASE WHEN pf.qty_today = 0 THEN COALESCE((pf.qty_today * pf.price_today), 0) - COALESCE((pf.qty_ytd * pf.price_ytd),0) -- this is required for positions closed within the year and by today
				 ELSE COALESCE((pf.qty_today * pf.price_today), 0) - COALESCE((pf.qty_ytd * pf.price_ytd),0) -- this is required for outstanding positions
			END   - COALESCE(cf.cf_ytd_eur, 0) --/ COALESCE(pf.fx_today, 1) -- this is required for positions closed within the year and by today
			) * COALESCE(pf.fx_today, 1) as pnl_ytd_market_eur,
            -- 2. P&L λόγω μεταβολής Ισοτιμίας (FX Effect YTD)
			CASE WHEN pf.qty_today = 0 THEN COALESCE((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)), 0) - COALESCE((pf.qty_ytd * pf.price_ytd * COALESCE(pf.fx_ytd, 1)),0) -- this is required for positions closed within the year and by today
				 ELSE COALESCE((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)), 0) - COALESCE((pf.qty_ytd * pf.price_ytd * COALESCE(pf.fx_ytd, 1)),0) -- this is required for outstanding positions
			END   - COALESCE(cf.cf_ytd_eur, 0) --* COALESCE(pf.fx_today, 1) -- this is required for positions closed within the year and by today
			-
			(CASE WHEN pf.qty_today = 0 THEN COALESCE((pf.qty_today * pf.price_today), 0) - COALESCE((pf.qty_ytd * pf.price_ytd),0) -- this is required for positions closed within the year and by today
				 ELSE COALESCE((pf.qty_today * pf.price_today), 0) - COALESCE((pf.qty_ytd * pf.price_ytd),0) -- this is required for outstanding positions
			END   - COALESCE(cf.cf_ytd_eur, 0) --/ COALESCE(pf.fx_today, 1) -- this is required for positions closed within the year and by today
			) * COALESCE(pf.fx_today, 1)			
			as pnl_ytd_fx_eur,
			
            -- Total YTD P&L
			CASE WHEN pf.qty_today = 0 THEN COALESCE((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)), 0) - COALESCE((pf.qty_ytd * pf.price_ytd * COALESCE(pf.fx_ytd, 1)),0) -- this is required for positions closed within the year and by today
				 ELSE COALESCE((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)), 0) - COALESCE((pf.qty_ytd * pf.price_ytd * COALESCE(pf.fx_ytd, 1)),0) -- this is required for outstanding positions
			END   - COALESCE(cf.cf_ytd_eur, 0) -- this is required for positions closed within the year and by today
			as pnl_ytd_eur,	 
            -- Total YTD P&L %
            CASE WHEN COALESCE((pf.qty_ytd * pf.price_ytd * COALESCE(pf.fx_ytd, 1)), 0) = 0 THEN 0
                 ELSE (((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)) - COALESCE((pf.qty_ytd * pf.price_ytd * COALESCE(pf.fx_ytd, 1)),0) - COALESCE(cf.cf_ytd_eur, 0)) / COALESCE((pf.qty_ytd * pf.price_ytd * COALESCE(pf.fx_ytd, 1)), 1)) * 100
            END as pnl_ytd_percent,

            -- YTD UNREALIZED P&L
			CASE WHEN pf.qty_today <> 0 AND pf.qty_ytd = 0 THEN COALESCE((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)), 0) - COALESCE(cf.cf_ytd_eur, 0)  
				 WHEN pf.qty_today <> 0 AND pf.qty_ytd <> 0 AND pf.qty_today >= pf.qty_ytd AND COALESCE(cf.net_invested_ytd_eur, 0) >= 0 THEN COALESCE((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)), 0) - COALESCE((pf.qty_ytd * pf.price_ytd * COALESCE(pf.fx_ytd, 1)), 0) - COALESCE(cf.net_invested_ytd_eur, 0)
				 WHEN pf.qty_today <> 0 AND pf.qty_ytd <> 0 AND pf.qty_today >= pf.qty_ytd AND COALESCE(cf.net_invested_ytd_eur, 0) < 0 THEN COALESCE((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)), 0) - COALESCE((pf.qty_ytd * pf.price_ytd * COALESCE(pf.fx_ytd, 1)), 0) 
			 ELSE 0
			END	AS unrealized_pnl_ytd_eur,

            -- YTD REALIZED P&L
			CASE WHEN pf.qty_today = 0 THEN COALESCE((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)), 0) - COALESCE((pf.qty_ytd * pf.price_ytd * COALESCE(pf.fx_ytd, 1)),0) -- this is required for positions closed within the year and by today
				 ELSE COALESCE((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)), 0) - COALESCE((pf.qty_ytd * pf.price_ytd * COALESCE(pf.fx_ytd, 1)),0) -- this is required for outstanding positions
			END   - COALESCE(cf.cf_ytd_eur, 0)  -- this is required for positions closed within the year and by today
			-
			CASE WHEN pf.qty_today <> 0 AND pf.qty_ytd = 0 THEN COALESCE((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)), 0) - COALESCE(cf.cf_ytd_eur, 0)
				 WHEN pf.qty_today <> 0 AND pf.qty_ytd <> 0 AND pf.qty_today >= pf.qty_ytd AND COALESCE(cf.net_invested_ytd_eur, 0) >= 0 THEN COALESCE((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)), 0) - COALESCE((pf.qty_ytd * pf.price_ytd * COALESCE(pf.fx_ytd, 1)), 0) - COALESCE(cf.net_invested_ytd_eur, 0)
				 WHEN pf.qty_today <> 0 AND pf.qty_ytd <> 0 AND pf.qty_today >= pf.qty_ytd AND COALESCE(cf.net_invested_ytd_eur, 0) < 0 THEN COALESCE((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)), 0) - COALESCE((pf.qty_ytd * pf.price_ytd * COALESCE(pf.fx_ytd, 1)), 0)
			 ELSE 0
			END realized_pnl_ytd_eur,

            -- All Time P&L - ERROR - Use the NET instead
            ((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)) - COALESCE(cf.cf_all_time_eur, 0)) as pnl_all_time_eur,

            -- Συνολικό P&L από την αρχή (Total Net Economic Gain)
            COALESCE((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)),0) - COALESCE(cf.net_invested_all_time_eur, 0) as pnl_net_all_time_eur,
            -- Total Net Economic Gain %
            -- Denominator is gross_invested (total cost of buys), NOT net_invested.
            -- net_invested goes negative for profitable closed positions (proceeds > cost),
            -- which would flip the sign.  gross_invested is always >= 0.
            CASE WHEN COALESCE(cf.gross_invested_all_time_eur, 0) = 0 THEN 0
                 ELSE (COALESCE((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)),0) - COALESCE(cf.net_invested_all_time_eur, 0))
                      / cf.gross_invested_all_time_eur * 100
            END as pnl_net_all_time_percent,
            COALESCE(cf.gross_invested_all_time_eur, 0) as gross_invested_all_time_eur,
            -- Capital-inflow columns for account-level ROI denominator (same value on every
            -- row for an account; Python picks it up with groupby 'first').
            COALESCE(adf.direct_cashin_eur, 0) AS direct_cashin_eur,
            COALESCE(alf.linked_cashin_eur, 0) AS linked_cashin_eur,

            -- Unrealized P&L: current value minus FIFO cost at historical EUR FX rates
            h.Quantity * pf.price_today * COALESCE(pf.fx_today, 1) - h.Quantity * COALESCE(h.Fifo_Avg_Cost_EUR, h.Fifo_Avg_Price * COALESCE(pf.fx_today, 1)) AS unrealized_pnl_eur,

            -- Realized P&L (Total - Unrealized)
            COALESCE((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)), 0) - COALESCE(cf.net_invested_all_time_eur, 0)
            - (h.Quantity * pf.price_today * COALESCE(pf.fx_today, 1) - h.Quantity * COALESCE(h.Fifo_Avg_Cost_EUR, h.Fifo_Avg_Price * COALESCE(pf.fx_today, 1))) AS realized_pnl_eur,

            -- Annual YOC %
            ROUND(
                dy.annual_income / NULLIF(h.Quantity * h.Fifo_Avg_Price, 0) * 100,
                8
            ) AS dividend_yoc_pct

        FROM prices_fx pf
        LEFT JOIN cash_flows cf
               ON pf.Accounts_Id = cf.Accounts_Id AND pf.Securities_Id = cf.Securities_Id
        LEFT JOIN Holdings h
               ON h.Accounts_Id = pf.Accounts_Id AND h.Securities_Id = pf.Securities_Id
        LEFT JOIN dividend_yoc dy
               ON dy.Accounts_Id = pf.Accounts_Id AND dy.Securities_Id = pf.Securities_Id
        LEFT JOIN account_direct_flows adf ON adf.Accounts_Id    = pf.Accounts_Id
        LEFT JOIN account_linked_flows alf ON alf.inv_acc_id = pf.Accounts_Id
        WHERE (pf.qty_today != 0 OR cf.cf_all_time IS NOT NULL) -- Εξασφαλίζει ότι βλέπουμε και κλειστές θέσεις με ιστορικό
    --  AND pf.Accounts_Id = 89
        ORDER BY pf.Accounts_Name, pf.Securities_Name;
    """

    df = pd.read_sql(query, conn)
    conn.close()
    return df


_REALIZED_PNL_COLUMNS = [
    'date', 'month_date', 'year', 'month', 'split_amount', 'split_amount_original',
    'original_currency', 'categories_id', 'description', 'accounts_id', 'accounts_name',
    'accounts_type', 'payees_name', 'source_type', 'category_full_path', 'category_name',
    'categories_type', 'category_level', 'transaction_id', 'split_id', 'payees_id',
    'total_amount', 'is_draft', 'cleared', 'reconciled',
]

# Categories_Id for the single top-level "Trading" category all realized P&L is
# booked under (gains and losses together — one net trading P&L line, not split
# by sign). Top-level (no parent), so its hierarchy info is fixed/known rather
# than needing a join: Full_Path == Name == the category name, Level 0.
_RLZD_PNL_CATEGORY = {'id': 724, 'name': '_RlzdPnL'}


def _compute_realized_pnl_fifo(conn, start_date: str, end_date: str) -> "pd.DataFrame":
    """Realized investment P&L via FIFO lot matching — including short positions.

    The previous implementation (pure SQL) only matched sells against *earlier*
    buy lots. That's correct for ordinary long-only investing, but margin/CFD
    accounts routinely sell before any matching buy exists (opening a short
    position). When a sale had no prior buy lot to match, the old query simply
    left that portion out of the cost-basis sum while still counting its full
    sale proceeds as "gain" — silently inflating realized gains (e.g. one
    margin account alone added ~€441k of phantom "gain" in 2020).

    This version tracks two FIFO queues per (account, security): open long lots
    and open short lots. A sell first closes long lots (recognising P&L now)
    and only *opens/extends a short* for any quantity left over — which is not
    recognised as realized P&L until a later buy actually covers it. A buy
    mirrors this: it first covers outstanding short lots (recognising P&L now)
    before any leftover opens a new long lot. This matches how short positions
    actually realize P&L in real trading, and leaves ordinary long-only
    accounts (no shorting) numerically unaffected.

    Per-share value is taken from Total_Amount_AccCur / Quantity (already in
    the account's own currency and commission-inclusive — see the identical
    convention in update_holdings/crud.py), falling back to Price_Per_Share *
    FX_Rate only when Total_Amount_AccCur is zero/null (e.g. transfers). The
    old SQL used Price_Per_Share (the security's *native* currency) for the
    buy side but Total_Amount_AccCur (account currency) for the sell side —
    mixing currencies whenever a security's currency differs from its
    account's, which this fixes as a side effect. Because Total_Amount_AccCur
    is already commission-inclusive, commission is not subtracted separately
    (the old code's separate subtraction double-counted it).
    """
    # Explicit lowercase aliases throughout: unquoted Postgres identifiers fold
    # to lowercase regardless of how they're written in the SQL, so alias
    # everything explicitly rather than relying on that implicitly.
    tx = pd.read_sql("""
        SELECT i.Investments_Id AS investments_id, i.Accounts_Id AS accounts_id,
               i.Securities_Id AS securities_id, i.Date AS tx_date,
               i.Action AS action, i.Quantity AS quantity,
               i.Price_Per_Share AS price_per_share,
               i.Total_Amount_AccCur AS total_amount_acccur, i.FX_Rate AS fx_rate,
               a.Currencies_Id AS account_currency_id,
               curr_acc.Currencies_ShortName AS account_currency,
               a.Accounts_Name AS accounts_name, a.Accounts_Type AS accounts_type,
               s.Securities_Name AS securities_name
        FROM Investments i
        JOIN Securities s  ON i.Securities_Id = s.Securities_Id
        JOIN Accounts a    ON i.Accounts_Id   = a.Accounts_Id
        JOIN Currencies curr_acc ON a.Currencies_Id = curr_acc.Currencies_Id
        WHERE i.Action IN ('Buy', 'Reinvest', 'ShrIn', 'Sell', 'ShrOut')
          AND i.Date <= %(end_date)s
        ORDER BY i.Accounts_Id, i.Securities_Id, i.Date, i.Investments_Id
    """, conn, params={"end_date": end_date})

    if tx.empty:
        return pd.DataFrame(columns=_REALIZED_PNL_COLUMNS)

    # Per-currency sorted (date, rate-to-EUR) history, for "latest rate on or
    # before X" lookups — mirrors the old FX_Rates / DefaultFX_Rates CTEs. This
    # is only for the *account currency -> EUR* leg; the per-lot P&L itself is
    # already in account currency via total_amount_acccur, so no separate FX
    # lookup is needed for that part.
    fx_hist = pd.read_sql("""
        SELECT Currencies_Id_1 AS ccy_id, Date AS fx_date, FX_Rate AS fx_rate FROM Historical_FX
        WHERE Currencies_Id_2 = (SELECT Currencies_Id FROM Currencies WHERE Currencies_ShortName = 'EUR')
        ORDER BY Currencies_Id_1, Date
    """, conn)
    fx_hist['fx_date'] = pd.to_datetime(fx_hist['fx_date'])
    fx_by_ccy = {ccy: grp[['fx_date', 'fx_rate']].reset_index(drop=True) for ccy, grp in fx_hist.groupby('ccy_id')}
    end_date_ts = pd.Timestamp(end_date)

    def fx_rate_on(ccy_id, on_date):
        g = fx_by_ccy.get(ccy_id)
        if g is None or g.empty:
            return 1.0
        sub = g[g['fx_date'] <= on_date]
        if not sub.empty:
            return float(sub['fx_rate'].iloc[-1])
        sub2 = g[g['fx_date'] <= end_date_ts]
        return float(sub2['fx_rate'].iloc[-1]) if not sub2.empty else 1.0

    tx['tx_date'] = pd.to_datetime(tx['tx_date'])
    start_ts, end_ts = pd.Timestamp(start_date), pd.Timestamp(end_date)
    BUY_ACTIONS, SELL_ACTIONS = {'Buy', 'Reinvest', 'ShrIn'}, {'Sell', 'ShrOut'}

    results = []
    for (acc_id, _sec_id), grp in tx.groupby(['accounts_id', 'securities_id'], sort=False):
        long_lots, short_lots = deque(), deque()  # each: {'qty': float, 'value': float} (value = per-share, account currency)
        acc_name = grp['accounts_name'].iloc[0]
        acc_type = grp['accounts_type'].iloc[0]
        acc_ccy = grp['account_currency'].iloc[0]
        acc_ccy_id = grp['account_currency_id'].iloc[0]
        sec_name = grp['securities_name'].iloc[0]

        def emit(date, pnl_original):
            if pd.isna(pnl_original) or not (start_ts <= date <= end_ts) or abs(pnl_original) < 1e-9:
                return
            rate = 1.0 if acc_ccy == 'EUR' else fx_rate_on(acc_ccy_id, date)
            pnl_eur = pnl_original * rate
            cat = _RLZD_PNL_CATEGORY
            results.append({
                'date': date, 'month_date': date.replace(day=1), 'year': date.year, 'month': date.month,
                'split_amount': pnl_eur, 'split_amount_original': pnl_original, 'original_currency': acc_ccy,
                'categories_id': cat['id'], 'description': sec_name, 'accounts_id': acc_id,
                'accounts_name': acc_name, 'accounts_type': acc_type, 'payees_name': None,
                'source_type': 'Investment', 'category_full_path': cat['name'], 'category_name': cat['name'],
                'categories_type': 'Trading', 'category_level': 0, 'transaction_id': None, 'split_id': None,
                'payees_id': None, 'total_amount': None, 'is_draft': None, 'cleared': None, 'reconciled': None,
            })

        for row in grp.itertuples():
            qty = float(row.quantity) if pd.notna(row.quantity) else 0.0
            date = row.tx_date
            if qty <= 0:
                continue
            # `x or default` is wrong here: pandas represents SQL NULL as NaN, and
            # `float('nan') or 0` evaluates to NaN (NaN is truthy in Python), not 0 —
            # silently NaN-poisoning every lot downstream of one null/zero row. Use
            # pd.notna() to actually detect missing values.
            raw_total = row.total_amount_acccur
            total_acccur = float(raw_total) if pd.notna(raw_total) else 0.0
            if total_acccur != 0:
                value = total_acccur / qty
            else:
                fx = float(row.fx_rate) if pd.notna(row.fx_rate) else 1.0
                value = float(row.price_per_share) * fx

            recognised_qty = 0.0
            pnl_original = 0.0

            if row.action in BUY_ACTIONS:
                remaining = qty
                while remaining > 1e-9 and short_lots:
                    lot = short_lots[0]
                    covered = min(remaining, lot['qty'])
                    pnl_original += (lot['value'] - value) * covered
                    recognised_qty += covered
                    lot['qty'] -= covered
                    remaining -= covered
                    if lot['qty'] <= 1e-9:
                        short_lots.popleft()
                if remaining > 1e-9:
                    long_lots.append({'qty': remaining, 'value': value})
            elif row.action in SELL_ACTIONS:
                remaining = qty
                while remaining > 1e-9 and long_lots:
                    lot = long_lots[0]
                    closed = min(remaining, lot['qty'])
                    pnl_original += (value - lot['value']) * closed
                    recognised_qty += closed
                    lot['qty'] -= closed
                    remaining -= closed
                    if lot['qty'] <= 1e-9:
                        long_lots.popleft()
                if remaining > 1e-9:
                    short_lots.append({'qty': remaining, 'value': value})

            if recognised_qty > 1e-9:
                emit(date, pnl_original)

    return pd.DataFrame(results, columns=_REALIZED_PNL_COLUMNS) if results else pd.DataFrame(columns=_REALIZED_PNL_COLUMNS)


def get_income_expense_data(start_date, end_date, category_id=None, cash_account_types=None, inv_account_types=None):
    """Get income and expense data for a period, optionally filtered by category.
    Includes both bank transactions, investment transactions (dividends, interest, etc.),
    and realized P&L from securities sales calculated using FIFO method with FX conversion.
    All amounts are converted to EUR using historical FX rates.
    """
    conn = get_connection()

    # Μετατρέπουμε τη λίστα σε tuple για την SQL (π.χ. ('Brokerage', 'Pension'))
    if not cash_account_types:
        cash_account_types = ['Cash', 'Checking', 'Savings', 'Credit Card', 'Loan', 'Real Estate', 'Vehicle', 'Asset', 'Liability', 'Other']
    
    cash_account_types_tuple = tuple(cash_account_types)

    if not inv_account_types:
        inv_account_types = ['Brokerage', 'Other Investment', 'Margin']
    
    inv_account_types_tuple = tuple(inv_account_types)


    # First, create the CategoryHierarchy CTE
    category_hierarchy_cte = """
    WITH RECURSIVE CategoryHierarchy AS (
        SELECT 
            Categories_Id, 
            Categories_Name::TEXT as Full_Path,
            Categories_Name::TEXT as Name,
            Categories_Type::TEXT as Categories_Type,
            Categories_Id_Parent,
            0 as Level
        FROM Categories 
        WHERE Categories_Id_Parent IS NULL
        
        UNION ALL
        
        SELECT 
            c.Categories_Id, 
            ch.Full_Path || ' : ' || c.Categories_Name as Full_Path,
            c.Categories_Name as Name,
            c.Categories_Type::TEXT as Categories_Type,
            c.Categories_Id_Parent,
            ch.Level + 1 as Level
        FROM Categories c
        JOIN CategoryHierarchy ch ON c.Categories_Id_Parent = ch.Categories_Id
    )
    """
    
    # Build the complete query with UNION of Bank Transactions, Investment Transactions, and Realized P&L
    base_query = f"""
    {category_hierarchy_cte},
    DateRange AS (
        SELECT %s::date as start_date, %s::date as end_date
    ),
    -- Get FX rates for currency conversion
    FX_Rates AS (
        SELECT DISTINCT ON (currencies_id_1)
            hf.currencies_id_1,
            hf.currencies_id_2,
            hf.fx_rate,
            hf.date,
            c.currencies_shortname as base_currency
        FROM Historical_FX hf
        JOIN Currencies c ON hf.currencies_id_1 = c.currencies_id
        WHERE hf.currencies_id_2 = (SELECT currencies_id FROM Currencies WHERE currencies_shortname = 'EUR')
          AND hf.date <= (SELECT end_date FROM DateRange)
        ORDER BY currencies_id_1, hf.date DESC
    ),
    -- Default FX rates for each currency
    DefaultFX_Rates AS (
        SELECT DISTINCT ON (c.currencies_id)
            c.currencies_id as currencies_id_1,
            c.currencies_shortname as base_currency,
            COALESCE(hf.fx_rate, 1) as fx_rate
        FROM Currencies c
        LEFT JOIN Historical_FX hf ON c.currencies_id = hf.currencies_id_1 
            AND hf.currencies_id_2 = (SELECT currencies_id FROM Currencies WHERE currencies_shortname = 'EUR')
            AND hf.date <= (SELECT end_date FROM DateRange)
        WHERE c.currencies_shortname != 'EUR'
        ORDER BY c.currencies_id, hf.date DESC
    ),
    BankTransactionData AS (
        SELECT 
            t.date::timestamp without time zone as date,
            DATE_TRUNC('month', t.date)::timestamp without time zone as month_date,
            EXTRACT(YEAR FROM t.date) as year,
            EXTRACT(MONTH FROM t.date) as month,
            -- Convert amount to EUR using the FX rate as of the transaction date
            CASE 
                WHEN curr.currencies_shortname = 'EUR' THEN s.amount
                ELSE s.amount * COALESCE(
                    (SELECT fx_rate FROM FX_Rates 
                     WHERE currencies_id_1 = curr.currencies_id 
                       AND date <= t.date 
                     ORDER BY date DESC LIMIT 1),
                    (SELECT fx_rate FROM DefaultFX_Rates WHERE currencies_id_1 = curr.currencies_id),
                    1
                )
            END as split_amount_eur,
            s.amount as split_amount_original,
            curr.currencies_shortname as original_currency,
            s.categories_id,
            t.description,
            t.accounts_id,
            a.accounts_name,
            a.accounts_type,
            p.payees_name,
            'Bank' as source_type,
            t.transactions_id as transaction_id,
            s.splits_id as split_id,
            t.payees_id,
            t.total_amount,
            t.is_draft,
            t.cleared,
            t.reconciled
        FROM Transactions t
        JOIN Splits s ON t.transactions_id = s.transactions_id
        JOIN CategoryHierarchy ch ON s.categories_id = ch.Categories_Id
        JOIN Accounts a ON t.accounts_id = a.accounts_id
        JOIN Currencies curr ON a.currencies_id = curr.currencies_id
        LEFT JOIN Payees p ON t.payees_id = p.payees_id
        WHERE t.date BETWEEN (SELECT start_date FROM DateRange) AND (SELECT end_date FROM DateRange)
          AND a.accounts_type IN %s
          AND s.amount != 0
          AND t.Transfers_Id IS NULL   -- exclude transfers even when they have categorised splits
    ),
    InvestmentTransactionData AS (
        SELECT 
            t.date::timestamp without time zone as date,
            DATE_TRUNC('month', t.date)::timestamp without time zone as month_date,
            EXTRACT(YEAR FROM t.date) as year,
            EXTRACT(MONTH FROM t.date) as month,
            -- Convert amount to EUR using the FX rate as of the transaction date
            CASE 
                WHEN curr.currencies_shortname = 'EUR' THEN 
                    CASE 
                        WHEN t.action IN ('Dividend', 'IntInc') THEN t.Total_Amount_AccCur
                        WHEN t.action IN ('MiscExp') THEN -ABS(t.Total_Amount_AccCur)
                        ELSE 0
                    END
                ELSE
                    CASE
                        WHEN t.action IN ('Dividend', 'IntInc') THEN t.Total_Amount_AccCur * COALESCE(
                            (SELECT fx_rate FROM FX_Rates
                             WHERE currencies_id_1 = curr.currencies_id
                               AND date <= t.date
                             ORDER BY date DESC LIMIT 1),
                            (SELECT fx_rate FROM DefaultFX_Rates WHERE currencies_id_1 = curr.currencies_id),
                            1
                        )
                        WHEN t.action IN ('MiscExp') THEN -ABS(t.Total_Amount_AccCur) * COALESCE(
                            (SELECT fx_rate FROM FX_Rates
                             WHERE currencies_id_1 = curr.currencies_id
                               AND date <= t.date
                             ORDER BY date DESC LIMIT 1),
                            (SELECT fx_rate FROM DefaultFX_Rates WHERE currencies_id_1 = curr.currencies_id),
                            1
                        )
                        ELSE 0
                    END
            END as split_amount_eur,
            CASE
                WHEN t.action IN ('Dividend', 'IntInc') THEN t.Total_Amount_AccCur
                WHEN t.action IN ('MiscExp') THEN -ABS(t.Total_Amount_AccCur)
                ELSE 0
            END as split_amount_original,
            curr.currencies_shortname as original_currency,
            -- Map investment actions to appropriate categories
            CASE
                WHEN t.action = 'Dividend' THEN (SELECT Categories_Id FROM Categories WHERE Categories_Name = '_DivInc' LIMIT 1)
                WHEN t.action = 'IntInc' THEN (SELECT Categories_Id FROM Categories WHERE Categories_Name = '_IntInc' LIMIT 1)
                WHEN t.action = 'MiscExp' THEN (SELECT Categories_Id FROM Categories WHERE Categories_Name = '_MiscExp' LIMIT 1)
                ELSE NULL
            END as categories_id,
            COALESCE((SELECT s.Securities_Name FROM Securities s WHERE s.Securities_Id = t.Securities_id), t.description) as description,
            t.accounts_id,
            a.accounts_name,
            a.accounts_type,
            NULL as payees_name,
            'Investment' as source_type,
            NULL::integer as transaction_id,
            NULL::integer as split_id,
            NULL::integer as payees_id,
            NULL::numeric as total_amount,
            NULL::boolean as is_draft,
            NULL::boolean as cleared,
            NULL::boolean as reconciled
        FROM Investments t
        JOIN Accounts a ON t.accounts_id = a.accounts_id
        JOIN Currencies curr ON a.currencies_id = curr.currencies_id
        WHERE t.date BETWEEN (SELECT start_date FROM DateRange) AND (SELECT end_date FROM DateRange)
    --      AND a.accounts_type IN ('Brokerage', 'Pension', 'Other Investment', 'Margin')
          AND a.accounts_type IN %s
          AND t.action IN ('Dividend', 'IntInc', 'MiscExp')
          AND t.Total_Amount_AccCur != 0
    ),
    TransactionData AS (
        SELECT * FROM BankTransactionData
        UNION ALL
        SELECT * FROM InvestmentTransactionData
        -- Realized P&L (FIFO, incl. short positions) is computed in Python —
        -- see _compute_realized_pnl_fifo — and concatenated onto this query's
        -- result below, since it needs sequential lot-matching that isn't a
        -- good fit for a single SQL pass.
    )
    SELECT
        td.date,
        td.month_date,
        td.year,
        td.month,
        td.split_amount_eur as split_amount,
        td.split_amount_original,
        td.original_currency,
        td.categories_id,
        td.description,
        td.accounts_id,
        td.accounts_name,
        td.accounts_type,
        td.payees_name,
        td.source_type,
        COALESCE(c.Full_Path, 'Uncategorized') as category_full_path,
        COALESCE(c.Name, 'Uncategorized') as category_name,
        COALESCE(c.Categories_Type,
            CASE
                WHEN td.split_amount_eur != 0 THEN 'Income'::text
                ELSE 'Expense'::text
            END
        ) as Categories_Type,
        COALESCE(c.Level, 0) as category_level,
        td.transaction_id,
        td.split_id,
        td.payees_id,
        td.total_amount,
        td.is_draft,
        td.cleared,
        td.reconciled
    FROM TransactionData td
    LEFT JOIN CategoryHierarchy c ON td.categories_id = c.Categories_Id
    """
    
    params = [start_date, end_date, cash_account_types_tuple, inv_account_types_tuple]

    # Add category filter if specified
    category_path = None
    if category_id:
        cursor = conn.cursor()
        cursor.execute("""
            WITH RECURSIVE CategoryHierarchy AS (
                SELECT Categories_Id, Categories_Name::TEXT as Full_Path
                FROM Categories WHERE Categories_Id = %s
                UNION ALL
                SELECT c.Categories_Id, ch.Full_Path || ' : ' || c.Categories_Name
                FROM Categories c
                JOIN CategoryHierarchy ch ON c.Categories_Id_Parent = ch.Categories_Id
            )
            SELECT Full_Path FROM CategoryHierarchy WHERE Categories_Id = %s
        """, (category_id, category_id))
        result = cursor.fetchone()
        cursor.close()

        if result:
            category_path = result[0]
            base_query += " AND c.Full_Path LIKE %s || '%%'"
            params.append(category_path)

    base_query += " ORDER BY td.date DESC, c.Full_Path"

    df = pd.read_sql(base_query, conn, params=params)

    # Realized P&L (FIFO incl. short positions) — computed in Python, then
    # concatenated on. Reuses the same connection, so do this before closing it.
    pnl_df = _compute_realized_pnl_fifo(conn, start_date, end_date)
    conn.close()
    if category_path is not None:
        pnl_df = pnl_df[pnl_df['category_full_path'].astype(str).str.startswith(category_path)]
    if not pnl_df.empty:
        # Bank/register-only columns (transaction_id, payees_id, is_draft, cleared,
        # reconciled, total_amount, payees_name) are always None on P&L rows — an
        # "all-NA column" on one side of the concat. Cast them to df's dtype first
        # so pandas doesn't have to guess how to combine an all-NA column with a
        # typed one; this is exactly the workaround pandas' own FutureWarning
        # suggests ("exclude the relevant entries before the concat operation"),
        # and keeps today's dtypes stable once the deprecation takes effect.
        if not df.empty:
            for col in pnl_df.columns:
                if col in df.columns and pnl_df[col].isna().all():
                    try:
                        pnl_df[col] = pnl_df[col].astype(df[col].dtype)
                    except (TypeError, ValueError):
                        pass
        df = pd.concat([df, pnl_df], ignore_index=True)

    # Convert datetime columns to naive datetime
    if 'date' in df.columns:
        df['date'] = pd.to_datetime(df['date'], errors='coerce')
        if hasattr(df['date'].dt, 'tz') and df['date'].dt.tz is not None:
            df['date'] = df['date'].dt.tz_localize(None)

    if 'month_date' in df.columns:
        df['month_date'] = pd.to_datetime(df['month_date'], errors='coerce')
        if hasattr(df['month_date'].dt, 'tz') and df['month_date'].dt.tz is not None:
            df['month_date'] = df['month_date'].dt.tz_localize(None)

    if 'date' in df.columns:
        df = df.sort_values('date', ascending=False).reset_index(drop=True)

    return df


# ======================================================
# DRAFT CONFIRMATION (shared by Dashboard and Recurring)
# ======================================================
# There were previously three separate places that could confirm a draft
# transaction (Dashboard's confirm-draft/confirm-all-drafts, Recurring's
# drafts/confirm, and recurring-template auto-confirm) and only one of them
# actually created the mirror leg for transfer-type drafts — the other two
# silently left the target account's inflow missing. Centralising the logic
# here means there's only one place to get this right.

def _confirm_draft_row(cur, tx_id: int) -> None:
    """Confirm one draft transaction, creating its transfer mirror leg if needed."""
    cur.execute("""
        SELECT Accounts_Id, Date, Description, Total_Amount, Payees_Id, Accounts_Id_Target, Transfers_Id
        FROM Transactions WHERE Transactions_Id = %s AND Is_Draft = TRUE
    """, (tx_id,))
    row = cur.fetchone()
    if row is None:
        return
    src_account, date, description, amount, payees_id, target_account, existing_transfers_id = row

    cur.execute("UPDATE Transactions SET Is_Draft = FALSE WHERE Transactions_Id = %s", (tx_id,))

    def _refresh(acc_id):
        if acc_id:
            cur.execute("""
                UPDATE Accounts
                   SET Accounts_Balance = COALESCE((
                       SELECT SUM(Total_Amount) FROM Transactions
                       WHERE Accounts_Id = %s AND Is_Draft = FALSE
                   ), 0)
                 WHERE Accounts_Id = %s
            """, (acc_id, acc_id))

    _refresh(src_account)

    if target_account:
        if not existing_transfers_id:
            cur.execute("SELECT nextval('transfers_id_seq')")
            shared_tid = cur.fetchone()[0]
            mirror_amount = -float(amount or 0)
            cur.execute("""
                INSERT INTO Transactions
                    (Accounts_Id, Date, Description, Total_Amount, Payees_Id, Cleared, Reconciled, Is_Draft, Accounts_Id_Target, Transfers_Id)
                VALUES (%s, %s, %s, %s, %s, FALSE, FALSE, FALSE, %s, %s)
            """, (target_account, date, description, mirror_amount, payees_id, src_account, shared_tid))
            cur.execute("UPDATE Transactions SET Transfers_Id = %s WHERE Transactions_Id = %s", (shared_tid, tx_id))
        # Whether the mirror leg was just created above or already existed as its own
        # row, this row's Is_Draft flip just made the balance-maintaining trigger
        # (written for an older single-row transfer model) also apply its
        # Accounts_Id_Target branch to target_account — recompute it from scratch
        # either way. If the mirror leg is itself still a draft, this correctly
        # excludes it until it's confirmed too.
        _refresh(target_account)


def confirm_draft_transaction(tx_id: int) -> bool:
    """Confirm a single draft by id. Returns False if it wasn't a pending draft."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM Transactions WHERE Transactions_Id = %s AND Is_Draft = TRUE", (tx_id,))
        if cur.fetchone() is None:
            return False
        _confirm_draft_row(cur, tx_id)
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def confirm_all_draft_transactions() -> int:
    """Confirm every pending draft, creating transfer mirror legs as needed."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT Transactions_Id FROM Transactions WHERE Is_Draft = TRUE ORDER BY Transactions_Id")
        tx_ids = [r[0] for r in cur.fetchall()]
        for tx_id in tx_ids:
            _confirm_draft_row(cur, tx_id)
        conn.commit()
        return len(tx_ids)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ======================================================
# CASH FLOW FORECAST
# ======================================================

def get_cash_flow_forecast(months_back: int = 3):
    """
    Returns two DataFrames:
      - df_future   : explicitly scheduled future transactions (Date > today)
      - df_recurring: payees present in EVERY one of the last `months_back`
                      complete calendar months, with average amount and interval.
    All amounts in EUR.
    `months_back` is treated as a trusted integer (sidebar slider, range 2-6).
    """
    conn = get_connection()
    mb   = int(months_back)   # guard against accidental float

    # 1. Explicitly entered future transactions
    df_future = pd.read_sql("""
        WITH LatestFX AS (
            SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
            FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC
        )
        SELECT
            t.Date,
            p.Payees_Name,
            a.Accounts_Name,
            c.Currencies_ShortName AS currency,
            s.Amount,
            CASE WHEN c.Currencies_ShortName = 'EUR' THEN s.Amount
                 ELSE s.Amount * COALESCE(fx.FX_Rate, 1) END AS amount_eur,
            cat.Categories_Name AS category
        FROM Transactions t
        JOIN Accounts a ON t.Accounts_Id = a.Accounts_Id
        JOIN Currencies c ON a.Currencies_Id = c.Currencies_Id
        LEFT JOIN Payees p ON t.Payees_Id = p.Payees_Id
        LEFT JOIN Splits s ON t.Transactions_Id = s.Transactions_Id
        LEFT JOIN Categories cat ON s.Categories_Id = cat.Categories_Id
        LEFT JOIN LatestFX fx ON fx.Currencies_Id_1 = c.Currencies_Id
        WHERE t.Date > CURRENT_DATE
          AND t.Transfers_Id IS NULL       -- exclude internal transfers
        ORDER BY t.Date ASC
    """, conn)

    # 2. Recurring detection — payees present in ALL last mb complete months.
    #    One row per (payee, transaction_date) aggregating all splits so that
    #    the LAG-based interval reflects actual payment cadence.
    df_recurring = pd.read_sql(f"""
        WITH
        -- One row per (payee, category, date): amount = sum of splits for that
        -- category on that single transaction date.
        recent AS (
            SELECT
                t.Payees_Id,
                p.Payees_Name,
                s.Categories_Id,
                cat.Categories_Name,
                DATE_TRUNC('month', t.Date)::date   AS month_start,
                t.Date,
                SUM(s.Amount)                        AS amount,
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
        -- Keep only (payee, category) pairs present in EVERY one of the mb months
        qualified AS (
            SELECT Payees_Id, Categories_Id, Currencies_Id
            FROM   recent
            GROUP  BY Payees_Id, Categories_Id, Currencies_Id
            HAVING COUNT(DISTINCT month_start) = {mb}
        ),
        -- Average transactions per month — drives interval strategy selection
        tx_freq AS (
            SELECT r.Payees_Id, r.Categories_Id, r.Currencies_Id,
                   COUNT(*)::float / {mb} AS avg_tx_per_month
            FROM recent r
            JOIN qualified q
                 ON  q.Payees_Id     = r.Payees_Id
                 AND q.Categories_Id = r.Categories_Id
                 AND q.Currencies_Id = r.Currencies_Id
            GROUP BY r.Payees_Id, r.Categories_Id, r.Currencies_Id
        ),
        -- Strategy A — transaction-level LAG.
        -- Correct for high-frequency payees (e.g. 3-4 supermarket visits/month).
        tx_lag AS (
            SELECT r.Payees_Id, r.Categories_Id, r.Currencies_Id,
                   (r.Date - LAG(r.Date) OVER (
                       PARTITION BY r.Payees_Id, r.Categories_Id, r.Currencies_Id
                       ORDER BY r.Date
                   ))::float AS days_since_prev
            FROM recent r
            JOIN qualified q
                 ON  q.Payees_Id     = r.Payees_Id
                 AND q.Categories_Id = r.Categories_Id
                 AND q.Currencies_Id = r.Currencies_Id
        ),
        interval_tx AS (
            SELECT Payees_Id, Categories_Id, Currencies_Id,
                   COALESCE(AVG(days_since_prev), 30) AS avg_interval
            FROM   tx_lag
            GROUP  BY Payees_Id, Categories_Id, Currencies_Id
        ),
        -- Strategy B — monthly-repr LAG.
        -- Collapse to the earliest date per month so sporadic bonus entries
        -- (e.g. Christmas bonus on top of regular salary) don't create false
        -- short intra-month intervals.
        monthly_repr AS (
            SELECT r.Payees_Id, r.Payees_Name, r.Categories_Id, r.Categories_Name,
                   r.month_start, r.Currencies_Id,
                   MIN(r.Date) AS repr_date
            FROM recent r
            JOIN qualified q
                 ON  q.Payees_Id     = r.Payees_Id
                 AND q.Categories_Id = r.Categories_Id
                 AND q.Currencies_Id = r.Currencies_Id
            GROUP BY r.Payees_Id, r.Payees_Name, r.Categories_Id, r.Categories_Name,
                     r.month_start, r.Currencies_Id
        ),
        monthly_lag AS (
            SELECT *,
                   (repr_date - LAG(repr_date) OVER (
                       PARTITION BY Payees_Id, Categories_Id, Currencies_Id
                       ORDER BY month_start
                   ))::float AS days_since_prev
            FROM monthly_repr
        ),
        interval_monthly AS (
            SELECT Payees_Id, Categories_Id, Currencies_Id,
                   COALESCE(AVG(days_since_prev), 30) AS avg_interval
            FROM   monthly_lag
            GROUP  BY Payees_Id, Categories_Id, Currencies_Id
        ),
        -- Median single-transaction amount — robust against bonus outliers
        amount_stats AS (
            SELECT r.Payees_Id, r.Categories_Id, r.Currencies_Id,
                   PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY r.amount) AS median_amount,
                   STDDEV(r.amount)                                        AS std_amount,
                   MAX(r.Date)                                             AS last_date
            FROM recent r
            JOIN qualified q
                 ON  q.Payees_Id     = r.Payees_Id
                 AND q.Categories_Id = r.Categories_Id
                 AND q.Currencies_Id = r.Currencies_Id
            GROUP BY r.Payees_Id, r.Categories_Id, r.Currencies_Id
        ),
        -- Distinct names (one row per key from monthly_repr)
        names AS (
            SELECT DISTINCT ON (Payees_Id, Categories_Id, Currencies_Id)
                   Payees_Id, Payees_Name, Categories_Id, Categories_Name, Currencies_Id
            FROM   monthly_repr
        ),
        stats AS (
            SELECT
                n.Payees_Id,
                n.Payees_Name,
                n.Categories_Id,
                n.Categories_Name,
                am.median_amount                            AS avg_amount,
                am.std_amount,
                -- Use transaction-level intervals when avg > 1.5 tx/month
                -- (genuine high-frequency payees like supermarkets); use
                -- month-level intervals for low-frequency payees to suppress
                -- bonus-entry noise.
                CASE WHEN tf.avg_tx_per_month > 1.5
                     THEN it.avg_interval
                     ELSE im.avg_interval
                END                                         AS avg_days_between,
                am.last_date,
                n.Currencies_Id
            FROM names n
            JOIN amount_stats  am ON am.Payees_Id = n.Payees_Id AND am.Categories_Id = n.Categories_Id AND am.Currencies_Id = n.Currencies_Id
            JOIN tx_freq       tf ON tf.Payees_Id = n.Payees_Id AND tf.Categories_Id = n.Categories_Id AND tf.Currencies_Id = n.Currencies_Id
            JOIN interval_tx   it ON it.Payees_Id = n.Payees_Id AND it.Categories_Id = n.Categories_Id AND it.Currencies_Id = n.Currencies_Id
            JOIN interval_monthly im ON im.Payees_Id = n.Payees_Id AND im.Categories_Id = n.Categories_Id AND im.Currencies_Id = n.Currencies_Id
        ),
        fx AS (
            SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
            FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC
        )
        SELECT
            s.Payees_Name,
            s.Categories_Name                       AS category,
            ROUND(s.avg_amount::numeric,       2)   AS avg_amount,
            ROUND(s.std_amount::numeric,       2)   AS std_amount,
            ROUND(s.avg_days_between::numeric, 0)   AS avg_days_between,
            s.last_date,
            (s.last_date + ROUND(s.avg_days_between)::int)::date AS next_expected_date,
            c.Currencies_ShortName                  AS currency,
            ROUND((s.avg_amount * COALESCE(fx.FX_Rate, 1))::numeric, 2) AS avg_amount_eur
        FROM   stats s
        JOIN   Currencies c ON c.Currencies_Id    = s.Currencies_Id
        LEFT   JOIN fx      ON fx.Currencies_Id_1 = s.Currencies_Id
        ORDER  BY next_expected_date ASC
    """, conn)

    conn.close()
    if not df_future.empty:
        df_future['date'] = pd.to_datetime(df_future['date'])
    if not df_recurring.empty:
        df_recurring['last_date']          = pd.to_datetime(df_recurring['last_date'])
        df_recurring['next_expected_date'] = pd.to_datetime(df_recurring['next_expected_date'])
    return df_future, df_recurring


# ======================================================
# DIVIDEND INCOME TRACKER
# ======================================================

def get_dividend_tracker_data(start_date: str, end_date: str):
    """Monthly dividend and interest income and FIFO YOC per security for the given date range."""
    conn = get_connection()

    df = pd.read_sql("""
        WITH fx AS (
            SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
            FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC
        ),
        -- One row per actual income date (not truncated to month) so the LATERAL
        -- cutoffs use the exact date, avoiding the Expire-same-day bug.
        income AS (
            SELECT
                i.Date,
                DATE_TRUNC('month', i.Date)::date                        AS month,
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
                )                                                          AS income_eur,
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
            i.Date                                                        AS date,
            i.month,
            i.Securities_Name,
            i.Securities_Type,
            i.Accounts_Name,
            i.Action,
            ROUND(i.income_eur::numeric, 2)                               AS income_eur,
            ROUND(COALESCE(fc.cost_eur, 0)::numeric, 2)                   AS cost_basis_eur,
            -- date of the oldest FIFO lot still held at this income payment's date
            -- (used only for "All Time" annualisation)
            fc.position_start_date
        FROM income i
        -- FIFO cost of the position held on the exact day of each income payment.
        -- Buys: on or before income date. Sells/Expire: strictly before (so same-day
        -- expiry on a bond maturity date does not zero out the cost basis).
        -- Also returns position_start_date = oldest remaining lot date, used for YoC.
        CROSS JOIN LATERAL (
            WITH buys AS (
                SELECT
                    b.Date                                                                       AS buy_date,
                    b.Quantity                                                                   AS buy_qty,
                    ABS(b.Total_Amount_AccCur) * COALESCE(fx2.FX_Rate, 1) / NULLIF(b.Quantity, 0)     AS cost_per_unit_eur,
                    SUM(b.Quantity) OVER (ORDER BY b.Date, b.Investments_Id)                    AS running_buy_qty
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
                    GREATEST(0.0, LEAST(b.buy_qty, b.running_buy_qty - s.total_sell_qty))                         AS remaining_qty,
                    GREATEST(0.0, LEAST(b.buy_qty, b.running_buy_qty - s.total_sell_qty)) * b.cost_per_unit_eur   AS lot_cost
                FROM buys b CROSS JOIN sells s
            )
            SELECT
                COALESCE(SUM(lot_cost), 0)                              AS cost_eur,
                MIN(CASE WHEN remaining_qty > 0 THEN buy_date END)      AS position_start_date
            FROM fifo
        ) AS fc
        ORDER BY i.Date DESC, i.income_eur DESC
    """, conn, params={'start_date': start_date, 'end_date': end_date})

    conn.close()
    if not df.empty:
        df['month']               = pd.to_datetime(df['month'])
        df['date']                = pd.to_datetime(df['date'])
        df['position_start_date'] = pd.to_datetime(df['position_start_date'])
    return df


def get_dividend_forecast_data() -> "pd.DataFrame":
    """Return one row per currently-held, dividend-paying security with enough
    metadata to build a 12-month forward income forecast.

    Columns returned
    ----------------
    securities_id, securities_name, securities_type,
    total_qty, market_value_eur, cost_basis_eur,
    dividend_yield, dividend_rate, five_year_avg_yield,
    ex_dividend_date, dividend_pay_date, dividend_frequency,
    last_ex_date, last_amount,          <- most recent Securities_Dividends row
    trailing_12m_income_eur             <- actual income paid over the last 12 months
    """
    conn = get_connection()
    df = pd.read_sql("""
        WITH fx_latest AS (
            SELECT DISTINCT ON (Currencies_Id_1)
                   Currencies_Id_1, FX_Rate
            FROM   Historical_FX
            ORDER  BY Currencies_Id_1, Date DESC
        ),
        price_latest AS (
            SELECT DISTINCT ON (Securities_Id)
                   Securities_Id, Close
            FROM   Historical_Prices
            ORDER  BY Securities_Id, Date DESC
        ),
        holdings_agg AS (
            SELECT
                h.Securities_Id,
                SUM(h.Quantity)                                          AS total_qty,
                SUM(h.Quantity * COALESCE(h.Fifo_Avg_Cost_EUR,
                    h.Fifo_Avg_Price * COALESCE(fx.FX_Rate, 1), 0))     AS cost_basis_eur
            FROM   Holdings h
            JOIN   Accounts a ON h.Accounts_Id = a.Accounts_Id
            LEFT JOIN fx_latest fx ON fx.Currencies_Id_1 = a.Currencies_Id
            WHERE  h.Quantity > 0
            GROUP  BY h.Securities_Id
        ),
        last_div AS (
            SELECT DISTINCT ON (Securities_Id)
                   Securities_Id,
                   Ex_Date  AS last_ex_date,
                   Amount   AS last_amount
            FROM   Securities_Dividends
            ORDER  BY Securities_Id, Ex_Date DESC
        ),
        trailing_income AS (
            SELECT
                i.Securities_Id,
                SUM(i.Total_Amount_AccCur * COALESCE(fx.FX_Rate, 1)) AS trailing_12m_income_eur
            FROM   Investments i
            JOIN   Accounts a ON i.Accounts_Id = a.Accounts_Id
            LEFT JOIN fx_latest fx ON fx.Currencies_Id_1 = a.Currencies_Id
            WHERE  i.Action IN ('Dividend','IntInc','Reinvest')
              AND  i.Date >= CURRENT_DATE - INTERVAL '12 months'
            GROUP  BY i.Securities_Id
        )
        SELECT
            s.Securities_Id            AS securities_id,
            s.Securities_Name          AS securities_name,
            s.Securities_Type          AS securities_type,
            ha.total_qty,
            ha.cost_basis_eur,
            ROUND((ha.total_qty * COALESCE(pl.Close, 0) * COALESCE(fx2.FX_Rate, 1))::numeric, 2)
                                       AS market_value_eur,
            s.Dividend_Yield           AS dividend_yield,
            s.Dividend_Rate            AS dividend_rate,
            s.Five_Year_Avg_Yield      AS five_year_avg_yield,
            s.Ex_Dividend_Date         AS ex_dividend_date,
            s.Dividend_Pay_Date        AS dividend_pay_date,
            s.Dividend_Frequency       AS dividend_frequency,
            ld.last_ex_date,
            ld.last_amount,
            COALESCE(ti.trailing_12m_income_eur, 0) AS trailing_12m_income_eur
        FROM   Securities s
        JOIN   holdings_agg ha ON ha.Securities_Id = s.Securities_Id
        LEFT JOIN price_latest pl  ON pl.Securities_Id  = s.Securities_Id
        LEFT JOIN fx_latest    fx2 ON fx2.Currencies_Id_1 = s.Currencies_Id
        LEFT JOIN last_div     ld  ON ld.Securities_Id  = s.Securities_Id
        LEFT JOIN trailing_income ti ON ti.Securities_Id = s.Securities_Id
        WHERE (
            s.Dividend_Yield  IS NOT NULL
            OR s.Dividend_Rate IS NOT NULL
            OR ti.trailing_12m_income_eur > 0
        )
        ORDER BY s.Securities_Name
    """, conn)
    conn.close()
    return df


# ======================================================
# ASSET ALLOCATION VS TARGET
# ======================================================

def get_allocation_targets():
    """Return all rows from Allocation_Targets as a DataFrame."""
    conn = get_connection()
    df = pd.read_sql(
        "SELECT Securities_Type, Target_Pct FROM Allocation_Targets ORDER BY Securities_Type",
        conn,
    )
    conn.close()
    return df


def save_allocation_targets(targets: dict):
    """Upsert {securities_type: target_pct} into Allocation_Targets."""
    conn = get_connection()
    cur = conn.cursor()
    for sec_type, pct in targets.items():
        cur.execute(
            """
            INSERT INTO Allocation_Targets (Securities_Type, Target_Pct)
            VALUES (%s, %s)
            ON CONFLICT (Securities_Type)
            DO UPDATE SET Target_Pct = EXCLUDED.Target_Pct
            """,
            (sec_type, float(pct)),
        )
    conn.commit()
    cur.close()
    conn.close()


def get_sector_allocation_data():
    """Current allocation by Sector and Industry in EUR."""
    conn = get_connection()
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
                COALESCE(NULLIF(TRIM(s.Sector),   ''), 'Other / Unknown') AS sector,
                COALESCE(NULLIF(TRIM(s.Industry), ''), 'Other / Unknown') AS industry,
                s.Securities_Type::text                                    AS securities_type,
                SUM(h.Quantity * COALESCE(p.Close, 0)
                               * COALESCE(fx.FX_Rate, 1))                 AS value_eur
            FROM Holdings h
            JOIN  Securities s ON h.Securities_Id = s.Securities_Id
            JOIN  Accounts   a ON h.Accounts_Id   = a.Accounts_Id
            LEFT JOIN prices p ON p.Securities_Id  = h.Securities_Id
            LEFT JOIN fx       ON fx.Currencies_Id_1 = s.Currencies_Id
            WHERE h.Quantity > 0
            GROUP BY
                COALESCE(NULLIF(TRIM(s.Sector),   ''), 'Other / Unknown'),
                COALESCE(NULLIF(TRIM(s.Industry), ''), 'Other / Unknown'),
                s.Securities_Type::text
        ),
        total AS (SELECT SUM(value_eur) AS grand_total FROM holdings_value)
        SELECT
            hv.sector,
            hv.industry,
            hv.securities_type,
            ROUND(hv.value_eur::numeric, 2)                                        AS value_eur,
            ROUND((hv.value_eur / NULLIF(t.grand_total, 0) * 100)::numeric, 2)    AS actual_pct
        FROM holdings_value hv
        CROSS JOIN total t
        ORDER BY hv.value_eur DESC
    """, conn)
    conn.close()
    return df


def get_asset_allocation_data():
    """Current allocation by Securities_Type in EUR vs. targets from Allocation_Targets."""
    conn = get_connection()

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
            JOIN Accounts   a ON h.Accounts_Id   = a.Accounts_Id
            LEFT JOIN prices p  ON p.Securities_Id = h.Securities_Id
            LEFT JOIN fx        ON fx.Currencies_Id_1 = s.Currencies_Id
            WHERE h.Quantity > 0
            GROUP BY s.Securities_Type
        ),
        total AS (SELECT SUM(value_eur) AS grand_total FROM holdings_value)
        SELECT
            hv.securities_type,
            ROUND(hv.value_eur::numeric, 2)                              AS value_eur,
            ROUND((hv.value_eur / NULLIF(t.grand_total, 0) * 100)::numeric, 2) AS actual_pct,
            COALESCE(at.Target_Pct, 0)                                   AS target_pct,
            ROUND((hv.value_eur / NULLIF(t.grand_total, 0) * 100 - COALESCE(at.Target_Pct, 0))::numeric, 2) AS delta_pct
        FROM holdings_value hv
        CROSS JOIN total t
        LEFT JOIN Allocation_Targets at ON at.Securities_Type = hv.securities_type
        ORDER BY value_eur DESC
    """, conn)

    conn.close()
    return df


# ======================================================
# FX EXPOSURE
# ======================================================

def get_fx_exposure_data():
    """Net exposure per currency in EUR and sensitivity to a ±5 % FX move."""
    conn = get_connection()

    df = pd.read_sql("""
        WITH fx AS (
            SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
            FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC
        ),
        prices AS (
            SELECT DISTINCT ON (Securities_Id) Securities_Id, Close
            FROM Historical_Prices ORDER BY Securities_Id, Date DESC
        ),
        cash_exp AS (
            -- Mirrors dashboard logic: all account balances except Brokerage/Margin
            -- (whose value is captured entirely through Holdings)
            SELECT
                a.Currencies_Id,
                SUM(a.Accounts_Balance) AS balance
            FROM Accounts a
            WHERE a.Is_Active = TRUE
              AND a.Accounts_Type NOT IN ('Brokerage', 'Margin')
            GROUP BY a.Currencies_Id
        ),
        inv_exp AS (
            SELECT
                s.Currencies_Id,
                SUM(h.Quantity * COALESCE(p.Close, 0)) AS value_native
            FROM Holdings h
            JOIN Securities s ON h.Securities_Id = s.Securities_Id
            LEFT JOIN prices p ON p.Securities_Id = h.Securities_Id
            WHERE h.Quantity > 0
            GROUP BY s.Currencies_Id
        ),
        combined AS (
            SELECT Currencies_Id, SUM(balance) AS native_exposure FROM cash_exp GROUP BY Currencies_Id
            UNION ALL
            SELECT Currencies_Id, SUM(value_native) FROM inv_exp GROUP BY Currencies_Id
        ),
        aggregated AS (
            SELECT Currencies_Id, SUM(native_exposure) AS native_exposure FROM combined GROUP BY Currencies_Id
        )
        SELECT
            c.Currencies_ShortName                                          AS currency,
            ROUND(a.native_exposure::numeric, 2)                            AS native_exposure,
            ROUND((a.native_exposure * COALESCE(fx.FX_Rate, 1))::numeric, 2) AS eur_exposure,
            ROUND((a.native_exposure * COALESCE(fx.FX_Rate, 1) * 0.05)::numeric, 2) AS sensitivity_5pct_eur
        FROM aggregated a
        JOIN Currencies c ON c.Currencies_Id = a.Currencies_Id
        LEFT JOIN fx      ON fx.Currencies_Id_1 = a.Currencies_Id
        ORDER BY ABS(a.native_exposure * COALESCE(fx.FX_Rate, 1)) DESC
    """, conn)

    conn.close()
    return df


# ======================================================
# BOND SCHEDULE
# ======================================================

def get_bond_schedule_data():
    """Upcoming maturities and coupon cash flows for bond holdings."""
    conn = get_connection()

    df = pd.read_sql("""
        WITH fx AS (
            SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
            FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC
        ),
        bond_holdings AS (
            SELECT
                h.Securities_Id,
                s.Securities_Name,
                s.Securities_Type,
                h.Quantity,
                s.Maturity_Date,
                s.Coupon_Rate,
                s.Face_Value,
                s.Coupon_Frequency,
                s.Currencies_Id,
                c.Currencies_ShortName AS currency
            FROM Holdings h
            JOIN Securities s ON h.Securities_Id = s.Securities_Id
            JOIN Currencies c ON s.Currencies_Id = c.Currencies_Id
            WHERE h.Quantity > 0
              AND s.Securities_Type IN ('Bond')
        )
        SELECT
            bh.Securities_Name,
            bh.Quantity,
            bh.Face_Value,
            ROUND((bh.Quantity * COALESCE(bh.Face_Value, 0))::numeric, 2)   AS total_face_eur_native,
            ROUND((bh.Quantity * COALESCE(bh.Face_Value, 0) * COALESCE(fx.FX_Rate, 1))::numeric, 2) AS total_face_eur,
            bh.Coupon_Rate,
            bh.Coupon_Frequency,
            ROUND((bh.Quantity * COALESCE(bh.Face_Value, 0) * COALESCE(bh.Coupon_Rate, 0) / 100 *
                CASE bh.Coupon_Frequency
                    WHEN 'At Maturity'  THEN 0
                    WHEN 'Semi-Annual'  THEN 0.5
                    WHEN 'Quarterly'    THEN 0.25
                    WHEN 'Monthly'      THEN 1.0/12
                    ELSE 1.0
                END * COALESCE(fx.FX_Rate, 1))::numeric, 2) AS next_coupon_eur,
            ROUND((bh.Quantity * COALESCE(bh.Face_Value, 0) * COALESCE(bh.Coupon_Rate, 0) / 100 *
                CASE bh.Coupon_Frequency
                    WHEN 'At Maturity'  THEN 0
                    ELSE 1.0
                END * COALESCE(fx.FX_Rate, 1))::numeric, 2) AS annual_coupon_eur,
            bh.Maturity_Date,
            (bh.Maturity_Date - CURRENT_DATE)            AS days_to_maturity,
            bh.currency
        FROM bond_holdings bh
        LEFT JOIN fx ON fx.Currencies_Id_1 = bh.Currencies_Id
        ORDER BY bh.Maturity_Date ASC NULLS LAST
    """, conn)

    conn.close()
    if not df.empty and 'maturity_date' in df.columns:
        df['maturity_date'] = pd.to_datetime(df['maturity_date'])
    return df


# ======================================================
# ANOMALY DETECTION
# ======================================================

def get_transaction_anomalies(z_threshold: float = 2.5, lookback_days: int = 365):
    """
    Returns recent transactions whose split amount is >= z_threshold standard
    deviations from the mean for that payee+category combination.
    Amounts in EUR.
    """
    conn = get_connection()

    df = pd.read_sql("""
        WITH RECURSIVE cat_path AS (
            SELECT Categories_Id,
                   Categories_Name::TEXT AS Full_Path
            FROM   Categories
            WHERE  Categories_Id_Parent IS NULL
            UNION ALL
            SELECT c.Categories_Id,
                   cp.Full_Path || ' : ' || c.Categories_Name
            FROM   Categories c
            JOIN   cat_path cp ON c.Categories_Id_Parent = cp.Categories_Id
        ),
        fx AS (
            SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
            FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC
        ),
        splits_eur AS (
            SELECT
                t.Transactions_Id,
                t.Date,
                p.Payees_Name,
                COALESCE(cp.Full_Path, cat.Categories_Name) AS category,
                a.Accounts_Name,
                s.Amount,
                ROUND((s.Amount * COALESCE(fx.FX_Rate, 1))::numeric, 2) AS amount_eur
            FROM Transactions t
            JOIN Accounts a  ON t.Accounts_Id = a.Accounts_Id
            JOIN Currencies c ON a.Currencies_Id = c.Currencies_Id
            LEFT JOIN Payees p        ON t.Payees_Id = p.Payees_Id
            LEFT JOIN Splits s        ON s.Transactions_Id = t.Transactions_Id
            LEFT JOIN Categories cat  ON s.Categories_Id = cat.Categories_Id
            LEFT JOIN cat_path cp     ON s.Categories_Id = cp.Categories_Id
            LEFT JOIN fx ON fx.Currencies_Id_1 = a.Currencies_Id
            WHERE t.Date >= CURRENT_DATE - %(lookback)s * INTERVAL '1 day'
              AND p.Payees_Name IS NOT NULL
              AND s.Amount IS NOT NULL
              AND t.Transfers_Id IS NULL -- Exclude internal transfers
        ),
        stats AS (
            SELECT
                Payees_Name,
                category,
                AVG(amount_eur)    AS mean_eur,
                STDDEV(amount_eur) AS std_eur,
                COUNT(*)           AS sample_size
            FROM splits_eur
            GROUP BY Payees_Name, category
            HAVING COUNT(*) >= 3
        )
        SELECT
            se.Date,
            se.Payees_Name,
            se.category,
            se.Accounts_Name,
            se.amount_eur,
            st.mean_eur,
            st.std_eur,
            ROUND(((se.amount_eur - st.mean_eur) / NULLIF(st.std_eur, 0))::numeric, 2) AS z_score
        FROM splits_eur se
        JOIN stats st
          ON st.Payees_Name = se.Payees_Name
         AND (st.category = se.category OR (st.category IS NULL AND se.category IS NULL))
        WHERE ABS((se.amount_eur - st.mean_eur) / NULLIF(st.std_eur, 0)) >= %(z)s
          AND se.Date >= CURRENT_DATE - 30
        ORDER BY ABS((se.amount_eur - st.mean_eur) / NULLIF(st.std_eur, 0)) DESC
        LIMIT 20
    """, conn, params={"lookback": lookback_days, "z": z_threshold})

    conn.close()
    if not df.empty:
        df['date'] = pd.to_datetime(df['date'])
    return df


# ======================================================
# AI WEEKLY SUMMARIES
# ======================================================

def get_weekly_summaries(limit: int = 12) -> "pd.DataFrame":
    """Return the most recent AI weekly summaries, newest first."""
    conn = get_connection()
    df = pd.read_sql("""
        SELECT Week_Start, Generated_At, Summary_Text
        FROM AI_Weekly_Summaries
        ORDER BY Week_Start DESC
        LIMIT %s
    """, conn, params=(limit,))
    conn.close()
    if not df.empty:
        df['week_start']    = pd.to_datetime(df['week_start'])
        df['generated_at']  = pd.to_datetime(df['generated_at'])
    return df

def get_monthly_summaries(limit: int = 12) -> "pd.DataFrame":
    """Return the most recent AI monthly summaries, newest first."""
    conn = get_connection()
    df = pd.read_sql("""
        SELECT Month_Start, Generated_At, Summary_Text
        FROM AI_Monthly_Summaries
        ORDER BY Month_Start DESC
        LIMIT %s
    """, conn, params=(limit,))
    conn.close()
    if not df.empty:
        df['month_start']    = pd.to_datetime(df['month_start'])
        df['generated_at']  = pd.to_datetime(df['generated_at'])


# ======================================================
# A1. SAVINGS RATE
# ======================================================

def get_savings_rate_data(months: int = 12):
    """Monthly savings rate: income, expenses, savings, savings_rate_pct."""
    conn = get_connection()
    df = pd.read_sql("""
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
              AND c.Categories_Type NOT IN ('Transfer', 'Trading', 'Investment')
              AND t.Date < DATE_TRUNC('month', CURRENT_DATE)
              AND t.Date >= DATE_TRUNC('month', CURRENT_DATE) - (%(months)s || ' months')::INTERVAL
        )
        SELECT
            month,
            SUM(CASE WHEN Categories_Type IN ('Income','Dividend','Interest') THEN amount_eur ELSE 0 END)        AS income_eur,
            ABS(SUM(CASE WHEN Categories_Type NOT IN ('Income','Dividend','Interest') THEN amount_eur ELSE 0 END)) AS expenses_eur
        FROM splits_cat
        GROUP BY month
        ORDER BY month
    """, conn, params={"months": months})
    conn.close()
    if not df.empty:
        df['month'] = pd.to_datetime(df['month'])
        df['savings_eur'] = df['income_eur'] - df['expenses_eur']
        df['savings_rate_pct'] = df.apply(
            lambda r: (r['savings_eur'] / r['income_eur'] * 100) if r['income_eur'] > 0 else 0, axis=1
        )
    return df


# ======================================================
# A2. ANNUAL BUDGETS TABLE + CRUD
# ======================================================

def ensure_budgets_table():
    """Create Annual_Budgets table if it does not exist."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS Annual_Budgets (
            Budget_Id       SERIAL PRIMARY KEY,
            Year            INT NOT NULL,
            Categories_Id   INT NOT NULL REFERENCES Categories(Categories_Id),
            Budget_Amount   NUMERIC(15,2) NOT NULL,
            UNIQUE(Year, Categories_Id)
        )
    """)
    conn.commit()
    cur.close()
    conn.close()


def upsert_budget(year: int, categories_id: int, amount: float):
    """Insert or update an annual budget entry."""
    ensure_budgets_table()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO Annual_Budgets (Year, Categories_Id, Budget_Amount)
        VALUES (%s, %s, %s)
        ON CONFLICT (Year, Categories_Id)
        DO UPDATE SET Budget_Amount = EXCLUDED.Budget_Amount
    """, (year, categories_id, amount))
    conn.commit()
    cur.close()
    conn.close()


def delete_budget(year: int, categories_id: int):
    """Delete an annual budget entry."""
    ensure_budgets_table()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        DELETE FROM Annual_Budgets WHERE Year = %s AND Categories_Id = %s
    """, (year, categories_id))
    conn.commit()
    cur.close()
    conn.close()


def get_budget_vs_actual(year: int, ref_years: int = 2):
    """Annual budget vs actual for the selected year.

    Returns per expense category:
      avg_annual_hist  – average annual spend over the last N full calendar years
      budget_amount    – annual budget set by the user
      actual_amount    – total spend in the selected year (YTD if current year)
      variance_eur     – budget − actual
      variance_pct     – actual as % of budget
      over_budget      – bool
    """
    ensure_budgets_table()
    conn = get_connection()
    df = pd.read_sql("""
        WITH RECURSIVE cat_path AS (
            SELECT Categories_Id,
                   Categories_Name::TEXT AS full_path,
                   Categories_Type,
                   Categories_Id_Parent
            FROM Categories
            WHERE Categories_Id_Parent IS NULL
            UNION ALL
            SELECT c.Categories_Id,
                   cp.full_path || ' : ' || c.Categories_Name,
                   c.Categories_Type,
                   c.Categories_Id_Parent
            FROM Categories c
            JOIN cat_path cp ON c.Categories_Id_Parent = cp.Categories_Id
        ),
        fx AS (
            SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
            FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC
        ),
        -- Historical annual spend per category over last N full calendar years
        hist_annual AS (
            SELECT
                s.Categories_Id,
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
              AND c.Categories_Type NOT IN ('Income', 'Transfer', 'Trading', 'Investment', 'Interest', 'Dividend')
              AND EXTRACT(year FROM t.Date) >= EXTRACT(year FROM CURRENT_DATE) - %(ref_years)s
              AND EXTRACT(year FROM t.Date) <  EXTRACT(year FROM CURRENT_DATE)
            GROUP BY s.Categories_Id, EXTRACT(year FROM t.Date)
        ),
        hist AS (
            SELECT Categories_Id,
                   AVG(annual_spend) AS avg_annual
            FROM hist_annual
            GROUP BY Categories_Id
        ),
        -- Actual spend for the selected year (full year or YTD)
        actual_year AS (
            SELECT s.Categories_Id,
                   ABS(SUM(s.Amount *
                       CASE WHEN cur.Currencies_ShortName = 'EUR' THEN 1
                            ELSE COALESCE(fx.FX_Rate, 1) END)) AS actual_amount
            FROM Splits s
            JOIN Transactions t ON t.Transactions_Id = s.Transactions_Id
            JOIN Categories c   ON c.Categories_Id   = s.Categories_Id
            JOIN Accounts a     ON a.Accounts_Id      = t.Accounts_Id
            JOIN Currencies cur ON cur.Currencies_Id  = a.Currencies_Id
            LEFT JOIN fx        ON fx.Currencies_Id_1 = a.Currencies_Id
            WHERE t.Transfers_Id IS NULL
              AND c.Categories_Type NOT IN ('Income', 'Transfer', 'Trading', 'Investment', 'Interest', 'Dividend')
              AND EXTRACT(year FROM t.Date) = %(year)s
            GROUP BY s.Categories_Id
        ),
        -- Prior year full spend (year - 1)
        prior_year AS (
            SELECT s.Categories_Id,
                   ABS(SUM(s.Amount *
                       CASE WHEN cur.Currencies_ShortName = 'EUR' THEN 1
                            ELSE COALESCE(fx.FX_Rate, 1) END)) AS prior_amount
            FROM Splits s
            JOIN Transactions t ON t.Transactions_Id = s.Transactions_Id
            JOIN Categories c   ON c.Categories_Id   = s.Categories_Id
            JOIN Accounts a     ON a.Accounts_Id      = t.Accounts_Id
            JOIN Currencies cur ON cur.Currencies_Id  = a.Currencies_Id
            LEFT JOIN fx        ON fx.Currencies_Id_1 = a.Currencies_Id
            WHERE t.Transfers_Id IS NULL
              AND c.Categories_Type NOT IN ('Income', 'Transfer', 'Trading', 'Investment', 'Interest', 'Dividend')
              AND EXTRACT(year FROM t.Date) = %(year)s - 1
            GROUP BY s.Categories_Id
        ),
        budgets AS (
            SELECT Categories_Id, Budget_Amount
            FROM Annual_Budgets
            WHERE Year = %(year)s
        )
        SELECT
            c.Categories_Id                                                  AS categories_id,
            c.full_path                                                      AS categories_name,
            ROUND(COALESCE(h.avg_annual,     0)::numeric, 2)                AS avg_annual_hist,
            ROUND(COALESCE(py.prior_amount,  0)::numeric, 2)                AS prior_year_amount,
            COALESCE(b.Budget_Amount, 0)                                     AS budget_amount,
            ROUND(COALESCE(ay.actual_amount, 0)::numeric, 2)                AS actual_amount,
            COALESCE(b.Budget_Amount, 0)
                - ROUND(COALESCE(ay.actual_amount, 0)::numeric, 2)          AS variance_eur,
            CASE WHEN COALESCE(b.Budget_Amount, 0) > 0
                 THEN ROUND((COALESCE(ay.actual_amount, 0)
                             / b.Budget_Amount * 100)::numeric, 1)
                 ELSE NULL END                                               AS variance_pct,
            COALESCE(ay.actual_amount, 0)
                > COALESCE(b.Budget_Amount, 0)                              AS over_budget
        FROM cat_path c
        LEFT JOIN hist         h  ON h.Categories_Id  = c.Categories_Id
        LEFT JOIN prior_year   py ON py.Categories_Id = c.Categories_Id
        LEFT JOIN actual_year  ay ON ay.Categories_Id = c.Categories_Id
        LEFT JOIN budgets       b  ON b.Categories_Id  = c.Categories_Id
        WHERE (   h.Categories_Id  IS NOT NULL
               OR ay.Categories_Id IS NOT NULL
               OR py.Categories_Id IS NOT NULL
               OR b.Categories_Id  IS NOT NULL)
          AND c.Categories_Type NOT IN ('Income', 'Transfer', 'Trading', 'Investment', 'Interest', 'Dividend')
        ORDER BY c.full_path
    """, conn, params={"year": year, "ref_years": ref_years})
    conn.close()
    return df


def get_annual_income(year: int) -> float:
    """Total income (Income + Dividend + Interest categories) for the given year, in EUR.
    Uses the latest available FX rate for non-EUR accounts."""
    conn = get_connection()
    df = pd.read_sql("""
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
        JOIN Accounts     a  ON a.Accounts_Id     = t.Accounts_Id
        JOIN Currencies   cur ON cur.Currencies_Id = a.Currencies_Id
        LEFT JOIN fx          ON fx.Currencies_Id_1 = a.Currencies_Id
        WHERE t.Transfers_Id IS NULL
          AND c.Categories_Type IN ('Income', 'Dividend', 'Interest')
          AND EXTRACT(year FROM t.Date) = %(year)s
    """, conn, params={"year": year})
    conn.close()
    return float(df["total_income_eur"].iloc[0])


def get_ytd_expense_transactions(year: int):
    """All expense-category transactions for the selected year, with full category path."""
    conn = get_connection()
    df = pd.read_sql("""
        WITH RECURSIVE cat_path AS (
            SELECT Categories_Id,
                   Categories_Name::TEXT AS full_path,
                   Categories_Type,
                   Categories_Id_Parent
            FROM Categories
            WHERE Categories_Id_Parent IS NULL
            UNION ALL
            SELECT c.Categories_Id,
                   cp.full_path || ' : ' || c.Categories_Name,
                   c.Categories_Type,
                   c.Categories_Id_Parent
            FROM Categories c
            JOIN cat_path cp ON c.Categories_Id_Parent = cp.Categories_Id
        ),
        fx AS (
            SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
            FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC
        )
        SELECT
            s.Splits_Id                                                       AS splits_id,
            t.Transactions_Id                                                 AS transaction_id,
            t.Date                                                            AS date,
            p.Payees_Name                                                     AS payee,
            cp.full_path                                                      AS category,
            s.Categories_Id                                                   AS categories_id,
            ABS(s.Amount *
                CASE WHEN cur.Currencies_ShortName = 'EUR' THEN 1
                     ELSE COALESCE(fx.FX_Rate, 1) END)                       AS amount_eur,
            COALESCE(s.Memo, t.Description)                                   AS notes
        FROM Splits s
        JOIN Transactions t  ON t.Transactions_Id = s.Transactions_Id
        JOIN cat_path cp     ON cp.Categories_Id  = s.Categories_Id
        LEFT JOIN Payees p   ON p.Payees_Id       = t.Payees_Id
        JOIN Accounts a      ON a.Accounts_Id     = t.Accounts_Id
        JOIN Currencies cur  ON cur.Currencies_Id = a.Currencies_Id
        LEFT JOIN fx         ON fx.Currencies_Id_1 = a.Currencies_Id
        WHERE t.Transfers_Id IS NULL
          AND cp.Categories_Type NOT IN ('Income', 'Transfer', 'Trading', 'Investment', 'Interest', 'Dividend')
          AND EXTRACT(year FROM t.Date) = %(year)s
        ORDER BY cp.full_path, t.Date DESC
    """, conn, params={"year": year})
    conn.close()
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


def get_portfolio_weights(account_ids: tuple = None):
    """Position value and weight for each security in current holdings (ordered by value desc)."""
    conn = get_connection()
    acct_filter = (
        f"AND h.Accounts_Id IN ({', '.join(str(int(i)) for i in account_ids)})"
        if account_ids else ""
    )
    df = pd.read_sql(f"""
        WITH Latest_FX AS (
            SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
            FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC
        ),
        Latest_Prices AS (
            SELECT DISTINCT ON (Securities_Id) Securities_Id, Close
            FROM Historical_Prices ORDER BY Securities_Id, Date DESC
        ),
        positions AS (
            SELECT
                s.Securities_Name AS ticker,
                SUM(h.Quantity * COALESCE(lp.Close, 0) *
                    CASE WHEN c.Currencies_ShortName = 'EUR' THEN 1
                         ELSE COALESCE(fx.FX_Rate, 1) END) AS value_eur
            FROM Holdings h
            JOIN Securities s     ON s.Securities_Id  = h.Securities_Id
            JOIN Currencies c     ON c.Currencies_Id  = s.Currencies_Id
            JOIN Latest_Prices lp ON lp.Securities_Id = h.Securities_Id
            LEFT JOIN Latest_FX fx ON fx.Currencies_Id_1 = s.Currencies_Id
            WHERE h.Quantity > 0
            {acct_filter}
            GROUP BY s.Securities_Name
        )
        SELECT
            ticker,
            value_eur,
            value_eur / NULLIF(SUM(value_eur) OVER (), 0) AS weight
        FROM positions
        WHERE value_eur > 0
        ORDER BY value_eur DESC
    """, conn)
    conn.close()
    return df


def get_investable_portfolio_value(account_ids: tuple = None) -> float:
    """Returns the total value in EUR of investable assets: holdings + pension + other investment cash.
    When account_ids is provided only those accounts are included."""
    conn = get_connection()
    acct_filter_h = (
        f"AND h.Accounts_Id IN ({', '.join(str(int(i)) for i in account_ids)})"
        if account_ids else ""
    )
    acct_filter_a = (
        f"AND a.Accounts_Id IN ({', '.join(str(int(i)) for i in account_ids)})"
        if account_ids else ""
    )
    row = pd.read_sql(f"""
        WITH Latest_FX AS (
            SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
            FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC
        ),
        Latest_Prices AS (
            SELECT DISTINCT ON (Securities_Id) Securities_Id, Close
            FROM Historical_Prices ORDER BY Securities_Id, Date DESC
        ),
        holdings_val AS (
            SELECT SUM(
                h.Quantity * COALESCE(lp.Close, 0) *
                CASE WHEN c.Currencies_ShortName = 'EUR' THEN 1
                     ELSE COALESCE(fx.FX_Rate, 1) END
            ) AS val
            FROM Holdings h
            JOIN Securities s    ON s.Securities_Id  = h.Securities_Id
            JOIN Currencies c    ON c.Currencies_Id  = s.Currencies_Id
            JOIN Latest_Prices lp ON lp.Securities_Id = h.Securities_Id
            LEFT JOIN Latest_FX fx ON fx.Currencies_Id_1 = s.Currencies_Id
            WHERE h.Quantity <> 0
            {acct_filter_h}
        ),
        acct_val AS (
            SELECT SUM(
                a.Accounts_Balance *
                CASE WHEN c.Currencies_ShortName = 'EUR' THEN 1
                     ELSE COALESCE(fx.FX_Rate, 1) END
            ) AS val
            FROM Accounts a
            JOIN Currencies c    ON c.Currencies_Id  = a.Currencies_Id
            LEFT JOIN Latest_FX fx ON fx.Currencies_Id_1 = a.Currencies_Id
            WHERE a.Is_Active = TRUE
              AND a.Accounts_Type IN ('Pension', 'Other Investment')
            {acct_filter_a}
        )
        SELECT COALESCE(hv.val, 0) + COALESCE(av.val, 0) AS total
        FROM holdings_val hv, acct_val av
    """, conn)
    conn.close()
    if row.empty:
        return 0.0
    return float(row.iloc[0]["total"] or 0.0)


# ======================================================
# A3. SPENDING TRENDS
# ======================================================

def get_spending_trends(months: int = 24):
    """Monthly spending per top-level expense category."""
    conn = get_connection()
    df = pd.read_sql("""
        WITH fx AS (
            SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
            FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC
        ),
        root_cat AS (
            SELECT c.Categories_Id,
                   COALESCE(p.Categories_Name, c.Categories_Name) AS top_category
            FROM Categories c
            LEFT JOIN Categories p ON p.Categories_Id = c.Categories_Id_Parent
            WHERE c.Categories_Type NOT IN ('Income', 'Transfer', 'Trading', 'Investment', 'Interest', 'Dividend')
        )
        SELECT
            DATE_TRUNC('month', t.Date)::date AS month,
            rc.top_category AS category,
            ABS(SUM(s.Amount *
                CASE WHEN cur.Currencies_ShortName = 'EUR' THEN 1
                     ELSE COALESCE(fx.FX_Rate, 1) END)) AS amount_eur
        FROM Splits s
        JOIN Transactions t ON t.Transactions_Id = s.Transactions_Id
        JOIN Categories c   ON c.Categories_Id   = s.Categories_Id
        JOIN root_cat rc    ON rc.Categories_Id   = s.Categories_Id
        JOIN Accounts a     ON a.Accounts_Id      = t.Accounts_Id
        JOIN Currencies cur ON cur.Currencies_Id  = a.Currencies_Id
        LEFT JOIN fx        ON fx.Currencies_Id_1 = a.Currencies_Id
        WHERE c.Categories_Type NOT IN ('Income', 'Transfer', 'Trading', 'Investment', 'Interest', 'Dividend')
          AND t.Transfers_Id IS NULL
          AND t.Date >= DATE_TRUNC('month', CURRENT_DATE) - (%(months)s || ' months')::INTERVAL
          AND t.Date < DATE_TRUNC('month', CURRENT_DATE)
        GROUP BY DATE_TRUNC('month', t.Date), rc.top_category
        ORDER BY month, category
    """, conn, params={"months": months})
    conn.close()
    if not df.empty:
        df['month'] = pd.to_datetime(df['month'])
    return df


# ======================================================
# A3b. INVESTMENT INCOME REPORT (Dividend / Interest)
# ======================================================

def get_investment_income_report(tax_year: int):
    """Per-transaction dividend and interest income for a tax year, converted to EUR.
    Uses the linked EUR cash transaction when available, else historical FX."""
    conn = get_connection()
    df = pd.read_sql("""
        SELECT
            s.Securities_Name                                               AS securities_name,
            a.Accounts_Name                                                 AS account_name,
            i.Date                                                          AS date,
            i.Action                                                        AS action,
            COALESCE(s.Is_Tax_Exempt, FALSE)                               AS is_tax_exempt,
            CASE
                WHEN i.Transactions_Id IS NOT NULL AND t_cash.Total_Amount IS NOT NULL
                --    THEN ABS(t_cash.Total_Amount)
                    THEN t_cash.Total_Amount 
                WHEN c.Currencies_ShortName != 'EUR'
                --    THEN ABS(i.Total_Amount_AccCur) * COALESCE(
                    THEN i.Total_Amount_AccCur * COALESCE(
                        (SELECT fx.FX_Rate FROM Historical_FX fx
                         WHERE fx.Currencies_Id_1 = c.Currencies_Id
                           AND fx.Date <= i.Date
                         ORDER BY fx.Date DESC LIMIT 1),
                        (SELECT fx.FX_Rate FROM Historical_FX fx
                         WHERE fx.Currencies_Id_1 = c.Currencies_Id
                         ORDER BY fx.Date ASC LIMIT 1),
                        1.0)
            --    ELSE ABS(i.Total_Amount_AccCur)
                ELSE i.Total_Amount_AccCur
            END                                                             AS amount_eur
        FROM Investments i
        JOIN Securities   s      ON s.Securities_Id      = i.Securities_Id
        JOIN Accounts     a      ON a.Accounts_Id        = i.Accounts_Id
        JOIN Currencies   c      ON c.Currencies_Id      = s.Currencies_Id
        LEFT JOIN Transactions t_cash ON t_cash.Transactions_Id = i.Transactions_Id
        WHERE i.Action IN ('Dividend', 'IntInc', 'Reinvest', 'RtrnCap')
          AND EXTRACT(year FROM i.Date) = %(tax_year)s
    --      AND i.Total_Amount_AccCur > 0
          AND i.Total_Amount_AccCur <> 0
        ORDER BY i.Date DESC, s.Securities_Name
    """, conn, params={"tax_year": tax_year})
    conn.close()
    if not df.empty:
        df['date'] = pd.to_datetime(df['date'])
    return df


# ======================================================
# A3c. BANK INTEREST REPORT
# ======================================================

def get_bank_interest_report(tax_year: int):
    """Per-transaction interest income from non-investment accounts (Checking, Savings, Cash, etc.)
    for a given tax year, converted to EUR using the closest historical FX rate on or before
    the transaction date."""
    conn = get_connection()
    df = pd.read_sql("""
        SELECT
            t.Date                                                          AS date,
            a.Accounts_Name                                                 AS account_name,
            a.Accounts_Type                                                 AS account_type,
            COALESCE(p.Payees_Name, '—')                                   AS payee,
            c.Categories_Name                                               AS category,
            cur.Currencies_ShortName                                        AS currency,
            s.Amount *
                CASE WHEN cur.Currencies_ShortName = 'EUR' THEN 1.0
                     ELSE COALESCE(
                         (SELECT hfx.FX_Rate FROM Historical_FX hfx
                          WHERE hfx.Currencies_Id_1 = cur.Currencies_Id
                            AND hfx.Date <= t.Date
                          ORDER BY hfx.Date DESC LIMIT 1),
                         1.0)
                END                                                         AS amount_eur
        FROM Splits s
        JOIN Transactions t  ON t.Transactions_Id = s.Transactions_Id
        JOIN Categories   c  ON c.Categories_Id   = s.Categories_Id
        JOIN Accounts     a  ON a.Accounts_Id     = t.Accounts_Id
        JOIN Currencies   cur ON cur.Currencies_Id = a.Currencies_Id
        LEFT JOIN Payees  p  ON p.Payees_Id        = t.Payees_Id
        WHERE t.Transfers_Id IS NULL
          AND (
              c.Categories_Type = 'Interest'
              OR (c.Categories_Type IN ('Income', 'Dividend')
                  AND LOWER(c.Categories_Name) LIKE '%%interest%%')
          )
          AND a.Accounts_Type NOT IN ('Brokerage', 'Pension', 'Other Investment', 'Margin')
          AND EXTRACT(year FROM t.Date) = %(tax_year)s
          AND s.Amount > 0
        ORDER BY t.Date DESC, a.Accounts_Name
    """, conn, params={"tax_year": tax_year})
    conn.close()
    if not df.empty:
        df['date'] = pd.to_datetime(df['date'])
    return df


# ======================================================
# A4. CAPITAL GAINS REPORT
# ======================================================

def get_capital_gains_report(tax_year: int):
    """Capital gains report for a given tax year.

    WAC cost basis is computed per-sell from buys that occurred on or before the sell
    date, so partial-close scenarios are handled correctly. CashIn is included to cover
    CD / money-market instruments that use that action for purchase.
    """
    conn = get_connection()
    df = pd.read_sql("""
        WITH
        -- Per-transaction EUR amount: prefer the linked cash-side Transactions row
        -- (recorded in the EUR account at the broker's actual FX rate, inclusive of
        -- all fees), then fall back to native amount × closest historical FX rate.
        txn_with_eur AS (
            SELECT
                i.Investments_Id,
                i.Securities_Id,
                i.Accounts_Id,
                i.Date,
                i.Action,
                i.Quantity,
                i.Total_Amount_AccCur,
                i.Price_Per_Share,
                i.Transactions_Id,
                CASE
                    -- Linked EUR cash transaction: use its absolute amount directly.
                    WHEN i.Transactions_Id IS NOT NULL
                     AND t_cash.Total_Amount IS NOT NULL
                    THEN ABS(t_cash.Total_Amount)
                    -- No link: convert native amount at the closest historical FX rate.
                    WHEN c.Currencies_ShortName != 'EUR'
                    THEN ABS(i.Total_Amount_AccCur) * COALESCE(
                        (SELECT fx.FX_Rate
                         FROM Historical_FX fx
                         WHERE fx.Currencies_Id_1 = c.Currencies_Id
                           AND fx.Date <= i.Date
                         ORDER BY fx.Date DESC LIMIT 1),
                        (SELECT fx.FX_Rate
                         FROM Historical_FX fx
                         WHERE fx.Currencies_Id_1 = c.Currencies_Id
                         ORDER BY fx.Date ASC LIMIT 1),
                        1.0
                    )
                    -- Already EUR.
                    ELSE ABS(i.Total_Amount_AccCur)
                END AS amount_eur,
                -- Store whether the linked cash tx was used (for transparency).
                (i.Transactions_Id IS NOT NULL AND t_cash.Total_Amount IS NOT NULL) AS has_linked_tx
            FROM Investments i
            JOIN Securities   s      ON s.Securities_Id      = i.Securities_Id
            JOIN Currencies   c      ON c.Currencies_Id      = s.Currencies_Id
            LEFT JOIN Transactions t_cash ON t_cash.Transactions_Id = i.Transactions_Id
        ),
        -- Running net quantity after every transaction (for position tracking).
        txn_running_pos AS (
            SELECT
                tf.*,
                SUM(
                    CASE
                        WHEN tf.Action IN ('Buy','ShrIn','Reinvest','Vest','Grant','Exercise','CashIn')
                             THEN  ABS(tf.Quantity)
                        WHEN tf.Action IN ('Sell','ShrOut','Expire')
                             THEN -ABS(tf.Quantity)
                        ELSE 0
                    END
                ) OVER (
                    PARTITION BY tf.Securities_Id, tf.Accounts_Id
                    ORDER BY tf.Date, tf.Investments_Id
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                ) AS running_qty
            FROM txn_with_eur tf
        ),
        -- For each sell in the tax year: the most recent date (strictly before the sell)
        -- when the position was fully closed (running_qty = 0).  NULL = no prior close.
        last_full_close AS (
            SELECT
                s.Investments_Id    AS sell_id,
                MAX(b.Date)         AS last_close_date
            FROM txn_running_pos s
            JOIN txn_running_pos b
                ON  b.Securities_Id = s.Securities_Id
                AND b.Accounts_Id   = s.Accounts_Id
                AND b.Date          < s.Date
                AND b.running_qty   = 0
            WHERE s.Action IN ('Sell', 'Expire')
              AND EXTRACT(year FROM s.Date) = %(tax_year)s
            GROUP BY s.Investments_Id
        ),
        -- WAC cost basis in EUR per share, using only buys in the current open position.
        -- Each buy uses its linked EUR cash amount when available, else historical FX.
        buy_basis_per_sell AS (
            SELECT
                sell.Investments_Id                                              AS sell_id,
                SUM(buy.amount_eur) / NULLIF(SUM(ABS(buy.Quantity)), 0)         AS wac_per_share_eur
            FROM txn_running_pos sell
            LEFT JOIN last_full_close lfc ON lfc.sell_id = sell.Investments_Id
            JOIN txn_running_pos buy
                ON  buy.Securities_Id = sell.Securities_Id
                AND buy.Accounts_Id   = sell.Accounts_Id
                AND buy.Date         <= sell.Date
                AND buy.Date          > COALESCE(lfc.last_close_date, '1900-01-01'::date)
                AND buy.Action        IN ('Buy', 'Reinvest', 'ShrIn', 'CashIn')
                AND buy.Quantity       > 0
            WHERE sell.Action IN ('Sell', 'Expire')
              AND EXTRACT(year FROM sell.Date) = %(tax_year)s
            GROUP BY sell.Investments_Id
        ),
        -- Most recent buy in the current open position (for short/long-term classification)
        last_buy_per_sell AS (
            SELECT
                sell.Investments_Id AS sell_id,
                MAX(buy.Date)       AS last_buy_date
            FROM txn_running_pos sell
            LEFT JOIN last_full_close lfc ON lfc.sell_id = sell.Investments_Id
            JOIN txn_running_pos buy
                ON  buy.Securities_Id = sell.Securities_Id
                AND buy.Accounts_Id   = sell.Accounts_Id
                AND buy.Date         <= sell.Date
                AND buy.Date          > COALESCE(lfc.last_close_date, '1900-01-01'::date)
                AND buy.Action        IN ('Buy', 'Reinvest', 'ShrIn', 'CashIn')
            WHERE sell.Action IN ('Sell', 'Expire')
              AND EXTRACT(year FROM sell.Date) = %(tax_year)s
            GROUP BY sell.Investments_Id
        )
        SELECT
            s.Securities_Name                                               AS securities_name,
            a.Accounts_Name                                                 AS account_name,
            i.Date                                                          AS sell_date,
            ABS(i.Quantity)                                                 AS quantity,
            i.Price_Per_Share                                               AS sell_price,
            -- Proceeds in EUR: linked cash tx if available, else historical FX conversion
            itf.amount_eur                                                  AS sell_amount_eur,
            -- Cost basis in EUR: WAC from buys (each already EUR via linked tx or FX)
            ABS(i.Quantity) * COALESCE(
                bb.wac_per_share_eur,
                i.Price_Per_Share * (itf.amount_eur / NULLIF(ABS(i.Total_Amount_AccCur), 0))
            )                                                               AS cost_basis_eur,
            -- Gain/Loss = EUR proceeds − EUR cost basis
            itf.amount_eur
                - ABS(i.Quantity) * COALESCE(
                    bb.wac_per_share_eur,
                    i.Price_Per_Share * (itf.amount_eur / NULLIF(ABS(i.Total_Amount_AccCur), 0))
                )                                                           AS gain_loss_eur,
            CASE
                WHEN lb.last_buy_date IS NULL
                     OR (i.Date - lb.last_buy_date) < 365
                THEN 'Short-term'
                ELSE 'Long-term'
            END                                                             AS holding_type,
            COALESCE(s.Is_Tax_Exempt, FALSE)                               AS is_tax_exempt,
            i.Instrument_Type                                               AS instrument_type,
            s.Securities_Type                                               AS securities_type
        FROM Investments i
        JOIN txn_with_eur        itf ON itf.Investments_Id = i.Investments_Id
        JOIN Securities          s   ON s.Securities_Id    = i.Securities_Id
        JOIN Accounts            a   ON a.Accounts_Id      = i.Accounts_Id
        LEFT JOIN buy_basis_per_sell bb ON bb.sell_id      = i.Investments_Id
        LEFT JOIN last_buy_per_sell  lb ON lb.sell_id      = i.Investments_Id
        WHERE i.Action IN ('Sell', 'Expire')
          AND EXTRACT(year FROM i.Date) = %(tax_year)s
        ORDER BY i.Date
    """, conn, params={"tax_year": tax_year})
    conn.close()
    if not df.empty:
        df['sell_date'] = pd.to_datetime(df['sell_date'])
    return df


# ======================================================
# A5. TAX-LOSS HARVESTING
# ======================================================

def get_tax_loss_opportunities():
    """Current positions with unrealized losses (tax-loss harvesting candidates)."""
    conn = get_connection()
    df = pd.read_sql("""
        WITH fx AS (
            SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
            FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC
        ),
        latest_price AS (
            SELECT DISTINCT ON (Securities_Id) Securities_Id, Close
            FROM Historical_Prices ORDER BY Securities_Id, Date DESC
        )
        SELECT
            s.Securities_Name       AS securities_name,
            s.Securities_Type       AS securities_type,
            SUM(h.Quantity)         AS quantity,
            lp.Close                AS current_price,
            AVG(h.Fifo_Avg_Price)   AS cost_basis,
            SUM(h.Quantity * lp.Close) *
                CASE WHEN cur.Currencies_ShortName = 'EUR' THEN 1
                     ELSE COALESCE(fx.FX_Rate, 1) END AS current_value_eur,
            SUM(h.Quantity * h.Fifo_Avg_Price) *
                CASE WHEN cur.Currencies_ShortName = 'EUR' THEN 1
                     ELSE COALESCE(fx.FX_Rate, 1) END AS cost_basis_eur,
            (SUM(h.Quantity * lp.Close) - SUM(h.Quantity * h.Fifo_Avg_Price)) *
                CASE WHEN cur.Currencies_ShortName = 'EUR' THEN 1
                     ELSE COALESCE(fx.FX_Rate, 1) END AS unrealized_loss_eur,
            CASE WHEN SUM(h.Quantity * h.Fifo_Avg_Price) > 0
                 THEN ROUND(((SUM(h.Quantity * lp.Close) - SUM(h.Quantity * h.Fifo_Avg_Price))
                              / SUM(h.Quantity * h.Fifo_Avg_Price) * 100)::numeric, 2)
                 ELSE 0 END AS loss_pct
        FROM Holdings h
        JOIN Securities s    ON s.Securities_Id   = h.Securities_Id
        JOIN Currencies cur  ON cur.Currencies_Id = s.Currencies_Id
        JOIN latest_price lp ON lp.Securities_Id  = h.Securities_Id
        LEFT JOIN fx         ON fx.Currencies_Id_1 = s.Currencies_Id
        WHERE h.Quantity > 0
        GROUP BY s.Securities_Id, s.Securities_Name, s.Securities_Type,
                 lp.Close, cur.Currencies_ShortName, fx.FX_Rate
        HAVING (SUM(h.Quantity * lp.Close) - SUM(h.Quantity * h.Fifo_Avg_Price)) *
               CASE WHEN cur.Currencies_ShortName = 'EUR' THEN 1
                    ELSE COALESCE(fx.FX_Rate, 1) END < 0
        ORDER BY unrealized_loss_eur ASC
    """, conn)
    conn.close()
    return df


# ======================================================
# A6. GOALS TABLE + CRUD
# ======================================================

def ensure_goals_table():
    """Create Goals table if it does not exist."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS Goals (
            Goal_Id         SERIAL PRIMARY KEY,
            Goal_Name       VARCHAR(200) NOT NULL,
            Target_Amount   NUMERIC(15,2) NOT NULL,
            Target_Date     DATE,
            Current_Amount  NUMERIC(15,2) DEFAULT 0,
            Notes           TEXT,
            Is_Active       BOOLEAN DEFAULT TRUE,
            Created_At      TIMESTAMP DEFAULT NOW()
        )
    """)
    conn.commit()
    cur.close()
    conn.close()


def get_goals():
    """Return all active goals with progress_pct."""
    ensure_goals_table()
    conn = get_connection()
    df = pd.read_sql("""
        SELECT Goal_Id AS goal_id,
               Goal_Name AS goal_name,
               Target_Amount AS target_amount,
               Target_Date AS target_date,
               Current_Amount AS current_amount,
               Notes AS notes,
               Is_Active AS is_active,
               Created_At AS created_at,
               CASE WHEN Target_Amount > 0
                    THEN ROUND((Current_Amount / Target_Amount * 100)::numeric, 1)
                    ELSE 0 END AS progress_pct
        FROM Goals
        WHERE Is_Active = TRUE
        ORDER BY Target_Date NULLS LAST, Goal_Name
    """, conn)
    conn.close()
    if not df.empty:
        df['target_date'] = pd.to_datetime(df['target_date'])
        df['created_at'] = pd.to_datetime(df['created_at'])
    return df


def upsert_goal(goal_id, name, target_amount, target_date, current_amount, notes):
    """Insert or update a goal."""
    ensure_goals_table()
    conn = get_connection()
    cur = conn.cursor()
    if goal_id is None:
        cur.execute("""
            INSERT INTO Goals (Goal_Name, Target_Amount, Target_Date, Current_Amount, Notes)
            VALUES (%s, %s, %s, %s, %s)
        """, (name, target_amount, target_date if target_date else None, current_amount, notes))
    else:
        cur.execute("""
            UPDATE Goals SET Goal_Name=%s, Target_Amount=%s, Target_Date=%s,
                             Current_Amount=%s, Notes=%s
            WHERE Goal_Id=%s
        """, (name, target_amount, target_date if target_date else None, current_amount, notes, goal_id))
    conn.commit()
    cur.close()
    conn.close()


def delete_goal(goal_id: int):
    """Soft-delete a goal."""
    ensure_goals_table()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE Goals SET Is_Active = FALSE WHERE Goal_Id = %s", (goal_id,))
    conn.commit()
    cur.close()
    conn.close()


# ======================================================
# A7. PRICE RETURNS (CORRELATION MATRIX)
# ======================================================

def get_price_returns(lookback_days: int = 252, account_ids: tuple = None):
    """Wide DataFrame of daily close prices for current holdings."""
    conn = get_connection()
    acct_filter = (
        f"AND h.Accounts_Id IN ({', '.join(str(int(i)) for i in account_ids)})"
        if account_ids else ""
    )
    df = pd.read_sql(f"""
        WITH held AS (
            SELECT DISTINCT h.Securities_Id
            FROM Holdings h
            WHERE h.Quantity > 0
            {acct_filter}
        ),
        price_counts AS (
            SELECT hp.Securities_Id, COUNT(*) AS cnt
            FROM Historical_Prices hp
            JOIN held ON held.Securities_Id = hp.Securities_Id
            GROUP BY hp.Securities_Id
            HAVING COUNT(*) >= 30
        )
        SELECT
            hp.Date                 AS date,
            s.Securities_Name       AS ticker,
            hp.Close                AS close
        FROM Historical_Prices hp
        JOIN price_counts pc ON pc.Securities_Id = hp.Securities_Id
        JOIN Securities s   ON s.Securities_Id   = hp.Securities_Id
        WHERE hp.Date >= CURRENT_DATE - (%(lookback)s || ' days')::INTERVAL
        ORDER BY hp.Date
    """, conn, params={"lookback": lookback_days})
    conn.close()
    if df.empty:
        return df
    df['date'] = pd.to_datetime(df['date'])
    wide = df.pivot_table(index='date', columns='ticker', values='close', aggfunc='mean')
    return wide


# ======================================================
# A8. BENCHMARK RETURNS
# ======================================================

def get_benchmark_candidates(min_days: int = 30):
    """Market Index securities with enough price history to serve as a benchmark."""
    conn = get_connection()
    df = pd.read_sql("""
        SELECT s.Securities_Id AS id, s.Securities_Name AS name, s.Ticker AS ticker,
               COUNT(hp.Date) AS price_days
        FROM Securities s
        JOIN Historical_Prices hp ON hp.Securities_Id = s.Securities_Id
        WHERE s.Securities_Type = 'Market Index'
        GROUP BY s.Securities_Id, s.Securities_Name, s.Ticker
        HAVING COUNT(hp.Date) >= %(min_days)s
        ORDER BY s.Securities_Name
    """, conn, params={"min_days": min_days})
    conn.close()
    return df


def get_benchmark_returns(securities_id: int, lookback_days: int = 252):
    """Daily close prices for a benchmark security as a date-indexed Series."""
    conn = get_connection()
    df = pd.read_sql("""
        SELECT hp.Date AS date, hp.Close AS close
        FROM Historical_Prices hp
        WHERE hp.Securities_Id = %(sec_id)s
          AND hp.Date >= CURRENT_DATE - (%(lookback)s || ' days')::INTERVAL
        ORDER BY hp.Date
    """, conn, params={"sec_id": securities_id, "lookback": lookback_days})
    conn.close()
    if df.empty:
        return pd.Series(dtype=float)
    df['date'] = pd.to_datetime(df['date'])
    return df.set_index('date')['close']


# ── Benchmark presets ─────────────────────────────────────────────────────────

def _ensure_benchmark_presets_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS Benchmark_Presets (
                Preset_Id   SERIAL PRIMARY KEY,
                Preset_Name VARCHAR(100) UNIQUE NOT NULL,
                Account_Ids INTEGER[] NOT NULL DEFAULT '{}',
                Created_At  TIMESTAMP DEFAULT NOW(),
                Updated_At  TIMESTAMP DEFAULT NOW()
            )
        """)
    conn.commit()


def get_benchmark_presets():
    """Fetch all saved benchmark presets ordered by name."""
    conn = get_connection()
    _ensure_benchmark_presets_table(conn)
    df = pd.read_sql("""
        SELECT Preset_Id AS preset_id, Preset_Name AS preset_name, Account_Ids AS account_ids
        FROM Benchmark_Presets
        ORDER BY Preset_Name
    """, conn)
    conn.close()
    return df


def upsert_benchmark_preset(name: str, account_ids):
    """Insert or update a named benchmark preset (upsert on name)."""
    conn = get_connection()
    _ensure_benchmark_presets_table(conn)
    ids = list(account_ids) if account_ids else []
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO Benchmark_Presets (Preset_Name, Account_Ids, Updated_At)
            VALUES (%s, %s, NOW())
            ON CONFLICT (Preset_Name) DO UPDATE
                SET Account_Ids = EXCLUDED.Account_Ids,
                    Updated_At  = NOW()
        """, (name, ids))
    conn.commit()
    conn.close()


def delete_benchmark_preset(preset_id: int):
    """Delete a benchmark preset by ID."""
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM Benchmark_Presets WHERE Preset_Id = %s", (preset_id,))
    conn.commit()
    conn.close()


# ======================================================
# A9. SPENDING PAYEE DRILL-DOWN
# ======================================================

def get_spending_by_payee(category: str, months: int = 24):
    """Top payees within a given expense category for the last N complete months."""
    conn = get_connection()
    df = pd.read_sql("""
        WITH fx AS (
            SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
            FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC
        ),
        root_cat AS (
            SELECT c.Categories_Id,
                   COALESCE(p.Categories_Name, c.Categories_Name) AS top_category
            FROM Categories c
            LEFT JOIN Categories p ON p.Categories_Id = c.Categories_Id_Parent
        )
        SELECT
            COALESCE(py.Payees_Name, '(Unknown)') AS payee,
            COUNT(DISTINCT t.Transactions_Id)      AS tx_count,
            ABS(SUM(s.Amount *
                CASE WHEN cur.Currencies_ShortName = 'EUR' THEN 1
                     ELSE COALESCE(fx.FX_Rate, 1) END)) AS amount_eur,
            MIN(t.Date) AS first_seen,
            MAX(t.Date) AS last_seen
        FROM Splits s
        JOIN Transactions t  ON t.Transactions_Id = s.Transactions_Id
        JOIN Categories   c  ON c.Categories_Id   = s.Categories_Id
        JOIN root_cat     rc ON rc.Categories_Id   = s.Categories_Id
        JOIN Accounts     a  ON a.Accounts_Id      = t.Accounts_Id
        JOIN Currencies   cur ON cur.Currencies_Id = a.Currencies_Id
        LEFT JOIN fx          ON fx.Currencies_Id_1 = a.Currencies_Id
        LEFT JOIN Payees  py  ON py.Payees_Id       = t.Payees_Id
        WHERE rc.top_category = %(category)s
          AND c.Categories_Type NOT IN ('Income', 'Transfer', 'Trading', 'Investment')
          AND t.Transfers_Id IS NULL
          AND t.Date >= DATE_TRUNC('month', CURRENT_DATE) - (%(months)s || ' months')::INTERVAL
          AND t.Date < DATE_TRUNC('month', CURRENT_DATE)
        GROUP BY COALESCE(py.Payees_Name, '(Unknown)')
        ORDER BY amount_eur DESC
    """, conn, params={"category": category, "months": months})
    conn.close()
    if not df.empty:
        df['first_seen'] = pd.to_datetime(df['first_seen'])
        df['last_seen']  = pd.to_datetime(df['last_seen'])
    return df


# ======================================================
# A10. PAYEE TRANSACTION DETAIL + MUTATIONS
# ======================================================

def get_payee_transactions(payee: str, category: str, months: int):
    """Individual transactions for a payee within a top-level category over the lookback window."""
    conn = get_connection()
    df = pd.read_sql("""
        WITH fx AS (
            SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
            FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC
        ),
        cat_path AS (
            SELECT c.Categories_Id,
                   COALESCE(p.Categories_Name || ' : ', '') || c.Categories_Name AS full_path,
                   COALESCE(p.Categories_Name, c.Categories_Name) AS top_category,
                   c.Categories_Type
            FROM Categories c
            LEFT JOIN Categories p ON p.Categories_Id = c.Categories_Id_Parent
        )
        SELECT
            s.Splits_Id                                                AS splits_id,
            t.Transactions_Id                                          AS transaction_id,
            t.Date                                                     AS date,
            COALESCE(py.Payees_Name, '(Unknown)')                     AS payee,
            cp.full_path                                               AS category,
            s.Categories_Id                                            AS categories_id,
            ABS(s.Amount * CASE WHEN cur.Currencies_ShortName = 'EUR' THEN 1
                                 ELSE COALESCE(fx.FX_Rate, 1) END)   AS amount_eur,
            COALESCE(s.Memo, t.Description)                           AS notes
        FROM Splits s
        JOIN Transactions t  ON t.Transactions_Id = s.Transactions_Id
        JOIN cat_path cp     ON cp.Categories_Id  = s.Categories_Id
        JOIN Accounts a      ON a.Accounts_Id     = t.Accounts_Id
        JOIN Currencies cur  ON cur.Currencies_Id = a.Currencies_Id
        LEFT JOIN fx         ON fx.Currencies_Id_1 = a.Currencies_Id
        LEFT JOIN Payees py  ON py.Payees_Id       = t.Payees_Id
        WHERE cp.top_category = %(category)s
          AND COALESCE(py.Payees_Name, '(Unknown)') = %(payee)s
          AND t.Transfers_Id IS NULL
          AND cp.Categories_Type NOT IN ('Income', 'Transfer', 'Trading', 'Investment')
          AND t.Date >= DATE_TRUNC('month', CURRENT_DATE) - (%(months)s || ' months')::INTERVAL
          AND t.Date <= CURRENT_DATE
        ORDER BY t.Date DESC
    """, conn, params={"payee": payee, "category": category, "months": months})
    conn.close()
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


def update_split(split_id: int, category_id: int, memo: str = None):
    """Update the category and memo of a split."""
    conn = get_connection()
    with conn.cursor() as cur:
        memo_val = memo.strip() if memo and memo.strip() else None
        cur.execute(
            "UPDATE Splits SET Categories_Id = %s, Memo = %s WHERE Splits_Id = %s",
            (category_id, memo_val, split_id),
        )
    conn.commit()
    conn.close()


def get_expense_categories():
    """All expense/non-income categories with IDs and full paths for the category dropdown."""
    conn = get_connection()
    df = pd.read_sql("""
        WITH RECURSIVE cat_path AS (
            SELECT Categories_Id,
                   Categories_Name::TEXT AS full_path,
                   Categories_Type,
                   Categories_Id_Parent
            FROM Categories WHERE Categories_Id_Parent IS NULL
            UNION ALL
            SELECT c.Categories_Id,
                   cp.full_path || ' : ' || c.Categories_Name,
                   c.Categories_Type,
                   c.Categories_Id_Parent
            FROM Categories c JOIN cat_path cp ON c.Categories_Id_Parent = cp.Categories_Id
        )
        SELECT Categories_Id AS categories_id, full_path, Categories_Type AS categories_type
        FROM cat_path
        WHERE Categories_Type NOT IN ('Income', 'Transfer', 'Trading', 'Investment', 'Interest', 'Dividend')
        ORDER BY full_path
    """, conn)
    conn.close()
    return df


# ======================================================
# A11. CUSTOM REPORT PRESETS
# ======================================================

def get_all_securities_for_filter() -> pd.DataFrame:
    """Return all securities (Id + Name) for the Custom Reports securities filter."""
    conn = get_connection()
    df = pd.read_sql(
        "SELECT Securities_Id AS securities_id, Securities_Name AS securities_name "
        "FROM Securities ORDER BY Securities_Name",
        conn,
    )
    conn.close()
    return df


def _ensure_user_preferences_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS User_Preferences (
                Pref_Key   VARCHAR(100) PRIMARY KEY,
                Pref_Value JSONB NOT NULL,
                Updated_At TIMESTAMP DEFAULT NOW()
            )
        """)
    conn.commit()


def get_user_preferences() -> dict:
    """Fetch all persisted UI preferences as a single {key: value} dict."""
    conn = get_connection()
    try:
        _ensure_user_preferences_table(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT Pref_Key, Pref_Value FROM User_Preferences")
            rows = cur.fetchall()
        return {r[0]: r[1] for r in rows}
    finally:
        conn.close()


def set_user_preference(key: str, value) -> None:
    """Insert or update a single UI preference (upsert on key)."""
    import json
    conn = get_connection()
    try:
        _ensure_user_preferences_table(conn)
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO User_Preferences (Pref_Key, Pref_Value, Updated_At)
                VALUES (%s, %s::jsonb, NOW())
                ON CONFLICT (Pref_Key) DO UPDATE
                    SET Pref_Value = EXCLUDED.Pref_Value, Updated_At = NOW()
            """, (key, json.dumps(value)))
        conn.commit()
    finally:
        conn.close()


def _ensure_custom_reports_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS Custom_Report_Presets (
                Preset_Id   SERIAL PRIMARY KEY,
                Preset_Name VARCHAR(100) UNIQUE NOT NULL,
                Config      JSONB NOT NULL DEFAULT '{}',
                Created_At  TIMESTAMP DEFAULT NOW(),
                Updated_At  TIMESTAMP DEFAULT NOW()
            )
        """)
    conn.commit()


def get_custom_report_presets():
    """Fetch all saved custom report presets ordered by name."""
    conn = get_connection()
    _ensure_custom_reports_table(conn)
    df = pd.read_sql("""
        SELECT Preset_Id AS preset_id, Preset_Name AS preset_name, Config AS config
        FROM Custom_Report_Presets ORDER BY Preset_Name
    """, conn)
    conn.close()
    return df


def upsert_custom_report_preset(name: str, config: dict):
    """Insert or update a custom report preset (upsert on name)."""
    import json
    conn = get_connection()
    _ensure_custom_reports_table(conn)
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO Custom_Report_Presets (Preset_Name, Config, Updated_At)
            VALUES (%s, %s::jsonb, NOW())
            ON CONFLICT (Preset_Name) DO UPDATE
                SET Config = EXCLUDED.Config, Updated_At = NOW()
        """, (name, json.dumps(config)))
    conn.commit()
    conn.close()


def delete_custom_report_preset(preset_id: int):
    """Delete a custom report preset by ID."""
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM Custom_Report_Presets WHERE Preset_Id = %s", (preset_id,))
    conn.commit()
    conn.close()


def get_all_payees():
    """All payees for use in the custom report filter."""
    conn = get_connection()
    df = pd.read_sql("SELECT Payees_Id AS payees_id, Payees_Name AS payees_name FROM Payees ORDER BY Payees_Name", conn)
    conn.close()
    return df


def get_investment_consistency_data(account_ids=None):
    """Return all Investments rows with account/security currency for consistency checks."""
    conn = get_connection()
    acct_filter = (
        f"AND i.Accounts_Id IN ({','.join(str(int(x)) for x in account_ids)})"
        if account_ids else ""
    )
    df = pd.read_sql(f"""
        SELECT
            i.Investments_Id                        AS investments_id,
            i.Accounts_Id                           AS accounts_id,
            i.Date                                  AS date,
            a.Accounts_Name                         AS account,
            s.Securities_Name                       AS security,
            i.Action::text                          AS action,
            i.Quantity                              AS quantity,
            i.Price_Per_Share                       AS price,
            i.Commission                            AS commission,
            i.Total_Amount_AccCur                   AS total_acc,
            i.Total_Amount_SecCur                   AS total_sec,
            i.FX_Rate                               AS fx_rate,
            i.Instrument_Type::text                 AS instrument_type,
            acc_cur.Currencies_ShortName            AS acc_currency,
            sec_cur.Currencies_ShortName            AS sec_currency
        FROM Investments i
        JOIN Accounts a      ON a.Accounts_Id    = i.Accounts_Id
        JOIN Securities s    ON s.Securities_Id  = i.Securities_Id
        JOIN Currencies acc_cur ON acc_cur.Currencies_Id = a.Currencies_Id
        JOIN Currencies sec_cur ON sec_cur.Currencies_Id = s.Currencies_Id
        WHERE i.Action IN ('Buy','Sell','Dividend','IntInc','RtrnCap',
                           'CashIn','CashOut','MiscExp','Reinvest','ShrIn','ShrOut')
          {acct_filter}
        ORDER BY i.Date DESC, i.Investments_Id DESC
    """, conn)
    conn.close()
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


def update_investment_row(investments_id: int, fields: dict):
    """Update editable numeric fields on an Investments row.

    ``fields`` may contain any subset of:
        quantity, price, commission, total_acc, total_sec, fx_rate
    """
    _col_map = {
        "quantity":   "Quantity",
        "price":      "Price_Per_Share",
        "commission": "Commission",
        "total_acc":  "Total_Amount_AccCur",
        "total_sec":  "Total_Amount_SecCur",
        "fx_rate":    "FX_Rate",
    }
    sets, vals = [], []
    for k, v in fields.items():
        if k in _col_map:
            sets.append(f"{_col_map[k]} = %s")
            vals.append(v)
    if not sets:
        return
    vals.append(investments_id)
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE Investments SET {', '.join(sets)} WHERE Investments_Id = %s",
            vals,
        )
    conn.commit()
    conn.close()


def get_corporate_actions(securities_id: int):
    """Return all corporate actions recorded for a security, newest first."""
    conn = get_connection()
    df = pd.read_sql("""
        SELECT ca.Corporate_Actions_Id  AS id,
               ca.Action_Type           AS action_type,
               ca.Effective_Date        AS effective_date,
               ca.Ratio_New             AS ratio_new,
               ca.Ratio_Old             AS ratio_old,
               ca.Description           AS description,
               ca.Created_At            AS created_at
          FROM Corporate_Actions ca
         WHERE ca.Securities_Id = %s
         ORDER BY ca.Effective_Date DESC, ca.Corporate_Actions_Id DESC
    """, conn, params=(securities_id,))
    conn.close()
    return df


def get_all_securities():
    """Return all active securities with id, ticker, name and currency shortname."""
    conn = get_connection()
    df = pd.read_sql("""
        SELECT s.Securities_Id   AS securities_id,
               s.Ticker          AS ticker,
               s.Securities_Name AS securities_name,
               c.Currencies_ShortName AS currency
          FROM Securities s
          LEFT JOIN Currencies c ON c.Currencies_Id = s.Currencies_Id
         WHERE s.Is_Active = TRUE
         ORDER BY s.Securities_Name
    """, conn)
    conn.close()
    return df


def get_split_preview(securities_id: int, split_date, account_ids=None):
    """
    Return current holdings (quantity as of split_date) per account for a security.
    This is used to preview what ShrIn / ShrOut delta will be inserted.
    Columns: accounts_id, account, current_qty
    """
    conn = get_connection()
    acct_filter = (
        f"AND i.Accounts_Id IN ({','.join(str(int(x)) for x in account_ids)})"
        if account_ids else ""
    )
    df = pd.read_sql(f"""
        SELECT i.Accounts_Id                                          AS accounts_id,
               a.Accounts_Name                                        AS account,
               SUM(CASE
                     WHEN i.Action IN ('Buy','Reinvest','ShrIn') THEN  i.Quantity
                     WHEN i.Action IN ('Sell','ShrOut')          THEN -i.Quantity
                     ELSE 0
                   END)                                               AS current_qty
          FROM Investments i
          JOIN Accounts a ON a.Accounts_Id = i.Accounts_Id
         WHERE i.Securities_Id = %s
           AND i.Date < %s
           AND i.Action IN ('Buy','Sell','Reinvest','ShrIn','ShrOut')
           {acct_filter}
         GROUP BY i.Accounts_Id, a.Accounts_Name
         HAVING SUM(CASE
                     WHEN i.Action IN ('Buy','Reinvest','ShrIn') THEN  i.Quantity
                     WHEN i.Action IN ('Sell','ShrOut')          THEN -i.Quantity
                     ELSE 0
                   END) <> 0
         ORDER BY a.Accounts_Name
    """, conn, params=(securities_id, split_date))
    conn.close()
    return df


def get_custom_report_data(date_from, date_to, grouping: str,
                           account_ids=None, category_ids=None, payee_names=None,
                           include_transfers=False, security_ids=None,
                           use_account_currency=False):
    """Execute a custom spending report query and return a flat (period, category, amount) frame.

    Parameters
    ----------
    include_transfers   : include transactions that are internal transfers (Transfers_Id IS NOT NULL).
    security_ids        : restrict to transactions linked (via Investments) to these securities.
    use_account_currency: report amounts in each account's own currency instead of converting to EUR.
                          When True the result column is named 'amount' and may mix currencies if
                          multiple account currencies are involved.
    """
    conn = get_connection()

    if grouping == 'year':
        period_sql   = "TO_CHAR(t.Date, 'YYYY')"
        period_order = "DATE_TRUNC('year',    t.Date)"
    elif grouping == 'quarter':
        period_sql   = "TO_CHAR(t.Date, 'YYYY') || ' Q' || EXTRACT(QUARTER FROM t.Date)::int::text"
        period_order = "DATE_TRUNC('quarter', t.Date)"
    else:
        period_sql   = "TO_CHAR(t.Date, 'YYYY-MM')"
        period_order = "DATE_TRUNC('month',   t.Date)"

    # Account filter (IDs are ints — safe for f-string)
    acct_filter = (
        f"AND a.Accounts_Id IN ({','.join(str(int(i)) for i in account_ids)})"
        if account_ids else ""
    )

    # Category filter — recursive expansion of selected IDs + their descendants
    if category_ids:
        id_list = ','.join(str(int(i)) for i in category_ids)
        expanded_cats_cte = f"""
        expanded_cats AS (
            SELECT Categories_Id FROM Categories WHERE Categories_Id IN ({id_list})
            UNION ALL
            SELECT c.Categories_Id FROM Categories c
            JOIN expanded_cats ec ON c.Categories_Id_Parent = ec.Categories_Id
        ),"""
        cat_filter = "AND s.Categories_Id IN (SELECT Categories_Id FROM expanded_cats)"
    else:
        expanded_cats_cte = ""
        cat_filter = (
            "AND cp.Categories_Type NOT IN "
            "('Income','Transfer','Trading','Investment','Interest','Dividend')"
        )

    # Transfer filter
    transfer_filter = "" if include_transfers else "AND t.Transfers_Id IS NULL"

    # Securities filter — join to Investments table (which links cash txn → investment → security)
    if security_ids:
        sec_id_list    = ','.join(str(int(i)) for i in security_ids)
        sec_join       = "LEFT JOIN Investments inv ON inv.Transactions_Id = t.Transactions_Id"
        sec_filter     = f"AND inv.Securities_Id IN ({sec_id_list})"
    else:
        sec_join   = ""
        sec_filter = ""

    # Amount expression: EUR using period-end FX rate, or account native currency.
    # For each grouping period we cap the FX lookup at the last day of that period
    # (capped at CURRENT_DATE so future periods use the latest available rate).
    # e.g. yearly grouping → Dec 31 rate; monthly → last day of the month.
    if grouping == 'year':
        period_end_expr = "LEAST((DATE_TRUNC('year', t.Date) + INTERVAL '1 year') - INTERVAL '1 day', CURRENT_DATE)"
    elif grouping == 'quarter':
        period_end_expr = "LEAST((DATE_TRUNC('quarter', t.Date) + INTERVAL '3 months') - INTERVAL '1 day', CURRENT_DATE)"
    else:
        period_end_expr = "LEAST((DATE_TRUNC('month', t.Date) + INTERVAL '1 month') - INTERVAL '1 day', CURRENT_DATE)"

    if use_account_currency:
        amount_expr  = "SUM(s.Amount)"
        amount_alias = "amount_eur"   # keep same column name for result processing
        fx_cte       = ""
        fx_join      = ""
    else:
        amount_expr  = (
            "SUM(s.Amount * "
            "    CASE WHEN cur.Currencies_ShortName = 'EUR' THEN 1 "
            "         ELSE COALESCE("
            f"             (SELECT hfx.FX_Rate FROM Historical_FX hfx"
            "              WHERE hfx.Currencies_Id_1 = a.Currencies_Id"
            f"               AND hfx.Date <= {period_end_expr}"
            "              ORDER BY hfx.Date DESC LIMIT 1),"
            "             1) "
            "    END)"
        )
        amount_alias = "amount_eur"
        fx_cte       = ""
        fx_join      = ""

    params: dict = {"date_from": date_from, "date_to": date_to}
    payee_filter = ""
    if payee_names:
        payee_filter = (
            "AND COALESCE(py.Payees_Name, '(No Payee)') = ANY(%(payee_names)s::text[])"
        )
        params["payee_names"] = list(payee_names)

    query = f"""
        WITH RECURSIVE
        {expanded_cats_cte}
        {fx_cte}
        cat_path AS (
            SELECT Categories_Id, Categories_Name::TEXT AS full_path, Categories_Type
            FROM Categories WHERE Categories_Id_Parent IS NULL
            UNION ALL
            SELECT c.Categories_Id,
                   cp.full_path || ' : ' || c.Categories_Name,
                   c.Categories_Type
            FROM Categories c JOIN cat_path cp ON c.Categories_Id_Parent = cp.Categories_Id
        )
        SELECT
            {period_sql}       AS period,
            {period_order}     AS period_order,
            cp.full_path       AS category,
            {amount_expr}      AS {amount_alias}
        FROM Splits s
        JOIN Transactions t ON t.Transactions_Id = s.Transactions_Id
        JOIN cat_path cp    ON cp.Categories_Id  = s.Categories_Id
        JOIN Accounts a     ON a.Accounts_Id     = t.Accounts_Id
        JOIN Currencies cur ON cur.Currencies_Id = a.Currencies_Id
        {fx_join}
        LEFT JOIN Payees py ON py.Payees_Id = t.Payees_Id
        {sec_join}
        WHERE t.Date >= %(date_from)s
          AND t.Date <= %(date_to)s
          {transfer_filter}
          {acct_filter}
          {cat_filter}
          {payee_filter}
          {sec_filter}
        GROUP BY period, period_order, cp.full_path
        ORDER BY cp.full_path, period_order
    """

    df = pd.read_sql(query, conn, params=params)
    conn.close()
    if not df.empty:
        df["period_order"] = pd.to_datetime(df["period_order"], utc=True)
    return df


def get_custom_report_investment_data(date_from, date_to, grouping: str,
                                       security_ids, account_ids=None,
                                       use_account_currency=False):
    """Investment-based custom report: group by period × security.

    Used when the user selects securities but no expense categories.
    Returns the same column shape as get_custom_report_data so the display
    code works unchanged: period, period_order, category (= security name),
    amount_eur.

    Amounts are the net cash amount per investment action, converted to EUR
    using the period-end FX rate (same convention as get_custom_report_data).
    Sign: positive = cash IN to you (Sell, Dividend, IntInc, RtrnCap, CashOut);
          negative = cash OUT from you (Buy, CashIn, MiscExp).
    """
    conn = get_connection()

    if grouping == 'year':
        period_sql      = "TO_CHAR(i.Date, 'YYYY')"
        period_order    = "DATE_TRUNC('year',    i.Date)"
        period_end_expr = "LEAST((DATE_TRUNC('year',    i.Date) + INTERVAL '1 year')    - INTERVAL '1 day', CURRENT_DATE)"
    elif grouping == 'quarter':
        period_sql      = "TO_CHAR(i.Date, 'YYYY') || ' Q' || EXTRACT(QUARTER FROM i.Date)::int::text"
        period_order    = "DATE_TRUNC('quarter', i.Date)"
        period_end_expr = "LEAST((DATE_TRUNC('quarter', i.Date) + INTERVAL '3 months') - INTERVAL '1 day', CURRENT_DATE)"
    else:
        period_sql      = "TO_CHAR(i.Date, 'YYYY-MM')"
        period_order    = "DATE_TRUNC('month',   i.Date)"
        period_end_expr = "LEAST((DATE_TRUNC('month',   i.Date) + INTERVAL '1 month')  - INTERVAL '1 day', CURRENT_DATE)"

    sec_filter = (
        f"AND i.Securities_Id IN ({','.join(str(int(x)) for x in security_ids)})"
        if security_ids else ""
    )
    acct_filter = (
        f"AND i.Accounts_Id IN ({','.join(str(int(x)) for x in account_ids)})"
        if account_ids else ""
    )

    if use_account_currency:
        amount_expr = (
            "SUM(CASE WHEN i.Action IN ('Buy','CashIn','MiscExp') THEN -1 ELSE 1 END"
            "    * ABS(i.Total_Amount_AccCur))"
        )
    else:
        amount_expr = (
            "SUM(CASE WHEN i.Action IN ('Buy','CashIn','MiscExp') THEN -1 ELSE 1 END"
            "    * ABS(i.Total_Amount_AccCur)"
            "    * CASE WHEN cur.Currencies_ShortName = 'EUR' THEN 1"
            "           ELSE COALESCE("
            f"               (SELECT hfx.FX_Rate FROM Historical_FX hfx"
            "                WHERE hfx.Currencies_Id_1 = cur.Currencies_Id"
            f"                  AND hfx.Date <= {period_end_expr}"
            "                ORDER BY hfx.Date DESC LIMIT 1), 1)"
            "    END)"
        )

    query = f"""
        SELECT
            {period_sql}    AS period,
            {period_order}  AS period_order,
            s.Securities_Name AS category,
            {amount_expr}   AS amount_eur
        FROM Investments i
        JOIN Securities s  ON s.Securities_Id = i.Securities_Id
        JOIN Currencies cur ON cur.Currencies_Id = s.Currencies_Id
        WHERE i.Date >= %(date_from)s
          AND i.Date <= %(date_to)s
          AND i.Action IN ('Buy','Sell','Dividend','IntInc','RtrnCap','CashIn','CashOut','MiscExp','Reinvest')
          AND i.Total_Amount_AccCur IS NOT NULL
          {sec_filter}
          {acct_filter}
        GROUP BY period, period_order, s.Securities_Name
        ORDER BY s.Securities_Name, period_order
    """

    df = pd.read_sql(query, conn, params={"date_from": date_from, "date_to": date_to})
    conn.close()
    if not df.empty:
        df["period_order"] = pd.to_datetime(df["period_order"], utc=True)
    return df


def get_custom_report_investment_drill_down(date_from, date_to,
                                             security_name=None, account_ids=None,
                                             use_account_currency=False):
    """Individual investment rows for the investment-mode drill-down.

    Returns one row per Investments entry with columns:
      investments_id, date, security, action, quantity, price, amount, amount_eur, account
    """
    conn = get_connection()

    sec_filter  = "AND s.Securities_Name = %(security_name)s" if security_name else ""
    acct_filter = (
        f"AND i.Accounts_Id IN ({','.join(str(int(x)) for x in account_ids)})"
        if account_ids else ""
    )

    if use_account_currency:
        amount_eur_expr = "ABS(i.Total_Amount_AccCur)"
    else:
        amount_eur_expr = (
            "ABS(i.Total_Amount_AccCur)"
            " * CASE WHEN cur.Currencies_ShortName = 'EUR' THEN 1"
            "        ELSE COALESCE("
            "            (SELECT hfx.FX_Rate FROM Historical_FX hfx"
            "             WHERE hfx.Currencies_Id_1 = cur.Currencies_Id"
            "               AND hfx.Date <= i.Date"
            "             ORDER BY hfx.Date DESC LIMIT 1), 1)"
            " END"
        )

    query = f"""
        SELECT
            i.Investments_Id                        AS investments_id,
            i.Date                                  AS date,
            s.Securities_Name                       AS security,
            i.Action::text                          AS action,
            i.Quantity                              AS quantity,
            i.Price_Per_Share                       AS price,
            i.Total_Amount_AccCur                   AS amount,
            CASE WHEN i.Action IN ('Buy','CashIn','MiscExp') THEN -1 ELSE 1 END
                * {amount_eur_expr}                 AS amount_eur,
            a.Accounts_Name                         AS account,
            cur.Currencies_ShortName                AS currency
        FROM Investments i
        JOIN Securities s   ON s.Securities_Id  = i.Securities_Id
        JOIN Accounts a     ON a.Accounts_Id    = i.Accounts_Id
        JOIN Currencies cur ON cur.Currencies_Id = s.Currencies_Id
        WHERE i.Date >= %(date_from)s
          AND i.Date <= %(date_to)s
          AND i.Action IN ('Buy','Sell','Dividend','IntInc','RtrnCap','CashIn','CashOut','MiscExp','Reinvest')
          AND i.Total_Amount_AccCur IS NOT NULL
          {sec_filter}
          {acct_filter}
        ORDER BY i.Date DESC
    """

    params: dict = {"date_from": date_from, "date_to": date_to}
    if security_name:
        params["security_name"] = security_name

    df = pd.read_sql(query, conn, params=params)
    conn.close()
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


def get_custom_report_drill_down(date_from, date_to, category_path=None,
                                  account_ids=None, category_ids=None, payee_names=None,
                                  include_transfers=False, security_ids=None,
                                  use_account_currency=False):
    """Individual transactions for a category (and subcategories) over a date range.

    category_ids restricts results to the report's own category selection (and their
    descendants). category_path further narrows to a specific category chosen in the
    drill-down UI. Both filters are applied together (AND).
    """
    conn = get_connection()

    acct_filter = (
        f"AND a.Accounts_Id IN ({','.join(str(int(i)) for i in account_ids)})"
        if account_ids else ""
    )

    params: dict = {"date_from": date_from, "date_to": date_to}

    # Expand report-level category_ids to include all descendants via recursive CTE.
    if category_ids:
        id_list = ','.join(str(int(i)) for i in category_ids)
        expanded_cats_cte = f"""
        expanded_cats AS (
            SELECT Categories_Id FROM Categories WHERE Categories_Id IN ({id_list})
            UNION ALL
            SELECT c.Categories_Id FROM Categories c
            JOIN expanded_cats ec ON c.Categories_Id_Parent = ec.Categories_Id
        ),"""
        cat_id_filter = "AND s.Categories_Id IN (SELECT Categories_Id FROM expanded_cats)"
    else:
        expanded_cats_cte = ""
        cat_id_filter = (
            "AND cp.Categories_Type NOT IN "
            "('Income','Transfer','Trading','Investment','Interest','Dividend')"
        )

    # Additionally restrict to the drill-down's selected category path (and children).
    if category_path:
        cat_path_filter = (
            "AND (cp.full_path = %(cat_path)s "
            "OR cp.full_path LIKE %(cat_prefix)s)"
        )
        params["cat_path"]   = category_path
        params["cat_prefix"] = category_path + " : %"
    else:
        cat_path_filter = ""

    payee_filter = ""
    if payee_names:
        payee_filter = (
            "AND COALESCE(py.Payees_Name, '(No Payee)') = ANY(%(payee_names)s::text[])"
        )
        params["payee_names"] = list(payee_names)

    transfer_filter = "" if include_transfers else "AND t.Transfers_Id IS NULL"

    if security_ids:
        sec_id_list = ','.join(str(int(i)) for i in security_ids)
        sec_join    = "LEFT JOIN Investments inv ON inv.Transactions_Id = t.Transactions_Id"
        sec_filter  = f"AND inv.Securities_Id IN ({sec_id_list})"
    else:
        sec_join   = ""
        sec_filter = ""

    if use_account_currency:
        amount_expr = "s.Amount"
        fx_cte      = ""
        fx_join     = ""
    else:
        amount_expr = ("s.Amount * CASE WHEN cur.Currencies_ShortName = 'EUR' THEN 1 "
                       "ELSE COALESCE(fx.FX_Rate, 1) END")
        fx_cte  = ("fx AS (\n"
                   "    SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate\n"
                   "    FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC\n"
                   "),\n")
        fx_join = "LEFT JOIN fx ON fx.Currencies_Id_1 = a.Currencies_Id"

    query = f"""
        WITH RECURSIVE
        {expanded_cats_cte}
        {fx_cte}
        cat_path AS (
            SELECT Categories_Id, Categories_Name::TEXT AS full_path, Categories_Type
            FROM Categories WHERE Categories_Id_Parent IS NULL
            UNION ALL
            SELECT c.Categories_Id,
                   cp.full_path || ' : ' || c.Categories_Name,
                   c.Categories_Type
            FROM Categories c JOIN cat_path cp ON c.Categories_Id_Parent = cp.Categories_Id
        )
        SELECT
            s.Splits_Id                                               AS splits_id,
            t.Transactions_Id                                         AS transaction_id,
            t.Date                                                    AS date,
            COALESCE(py.Payees_Name, '(No Payee)')                   AS payee,
            cp.full_path                                              AS category,
            s.Categories_Id                                           AS categories_id,
            {amount_expr}                                             AS amount_eur,
            cur.Currencies_ShortName                                  AS currency,
            COALESCE(s.Memo, t.Description)                          AS notes
        FROM Splits s
        JOIN Transactions t ON t.Transactions_Id = s.Transactions_Id
        JOIN cat_path cp    ON cp.Categories_Id  = s.Categories_Id
        JOIN Accounts a     ON a.Accounts_Id     = t.Accounts_Id
        JOIN Currencies cur ON cur.Currencies_Id = a.Currencies_Id
        {fx_join}
        LEFT JOIN Payees py ON py.Payees_Id = t.Payees_Id
        {sec_join}
        WHERE t.Date >= %(date_from)s
          AND t.Date <= %(date_to)s
          {transfer_filter}
          {acct_filter}
          {cat_id_filter}
          {cat_path_filter}
          {payee_filter}
          {sec_filter}
        ORDER BY t.Date DESC, t.Transactions_Id
    """

    df = pd.read_sql(query, conn, params=params)
    conn.close()
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


# ======================================================
# A12. CAPITAL GAINS — ALL TRANSACTIONS (for FIFO)
# ======================================================

def get_all_inv_txns_for_gains():
    """All buy/sell investment transactions with EUR amounts for client-side FIFO computation."""
    conn = get_connection()
    df = pd.read_sql("""
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
                    WHEN c.Currencies_ShortName != 'EUR'
                        THEN ABS(i.Total_Amount_AccCur) * COALESCE(
                            (SELECT fx.FX_Rate FROM Historical_FX fx
                             WHERE fx.Currencies_Id_1 = c.Currencies_Id
                               AND fx.Date <= i.Date
                             ORDER BY fx.Date DESC LIMIT 1), 1.0)
                    ELSE ABS(i.Total_Amount_AccCur)
                END AS amount_eur
            FROM Investments i
            JOIN Securities s   ON s.Securities_Id = i.Securities_Id
            JOIN Currencies c   ON c.Currencies_Id = s.Currencies_Id
            LEFT JOIN Transactions t_cash ON t_cash.Transactions_Id = i.Transactions_Id
            WHERE i.Action IN ('Buy','Sell','Reinvest','ShrIn','ShrOut','Expire','CashIn','CashOut')
              AND i.Total_Amount_AccCur IS NOT NULL
        )
        SELECT
            te.*,
            s.Securities_Name                  AS securities_name,
            a.Accounts_Name                    AS account_name,
            COALESCE(s.Is_Tax_Exempt, FALSE)   AS is_tax_exempt,
            s.Securities_Type                  AS securities_type
        FROM txn_with_eur te
        JOIN Securities s ON s.Securities_Id = te.Securities_Id
        JOIN Accounts   a ON a.Accounts_Id   = te.Accounts_Id
        ORDER BY te.Securities_Id, te.Accounts_Id, te.Date, te.Investments_Id
    """, conn)
    conn.close()
    if not df.empty:
        df['date'] = pd.to_datetime(df['date'])
    return df


# ======================================================
# A11. PORTFOLIO CASH FLOWS (for MWR / XIRR)
# ======================================================

def get_investment_cashflows(account_ids: tuple = None):
    """All investment cash flows for MWR/XIRR computation.

    Sign convention (investor perspective):
      Negative = money leaves your pocket into the portfolio  (Buy, CashIn, MiscExp)
      Positive = money returned to you from the portfolio      (Sell, Dividend, IntInc, RtrnCap, CashOut)
    Uses the linked EUR cash transaction when available, else historical FX.
    """
    conn = get_connection()
    acct_filter = (
        f"AND i.Accounts_Id IN ({', '.join(str(int(x)) for x in account_ids)})"
        if account_ids else ""
    )
    df = pd.read_sql(f"""
        SELECT
            i.Date   AS date,
            i.Action AS action,
            CASE WHEN i.Action IN ('Buy','CashIn','MiscExp') THEN -1 ELSE 1 END
            * CASE
                WHEN i.Transactions_Id IS NOT NULL AND t_cash.Total_Amount IS NOT NULL
                    THEN ABS(t_cash.Total_Amount)
                WHEN c.Currencies_ShortName != 'EUR'
                    THEN ABS(i.Total_Amount_AccCur) * COALESCE(
                        (SELECT fx.FX_Rate FROM Historical_FX fx
                         WHERE fx.Currencies_Id_1 = c.Currencies_Id
                           AND fx.Date <= i.Date
                         ORDER BY fx.Date DESC LIMIT 1), 1.0)
                ELSE ABS(i.Total_Amount_AccCur)
            END AS cashflow_eur
        FROM Investments i
        JOIN Securities s ON s.Securities_Id = i.Securities_Id
        JOIN Currencies c ON c.Currencies_Id = s.Currencies_Id
        LEFT JOIN Transactions t_cash ON t_cash.Transactions_Id = i.Transactions_Id
        WHERE i.Action IN ('Buy','Sell','Dividend','IntInc','RtrnCap','CashIn','CashOut','MiscExp')
          AND i.Total_Amount_AccCur IS NOT NULL
          {acct_filter}
        ORDER BY i.Date
    """, conn)
    conn.close()
    if not df.empty:
        df['date'] = pd.to_datetime(df['date'])
    return df


# ======================================================
# A12. IMPORT PROFILES, PAYEE RULES, RECONCILIATION
# ======================================================

def _ensure_import_tables(conn):
    """Create import / reconciliation tables and add Reconciled column if missing."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS Import_Profiles (
                Profile_Id          SERIAL PRIMARY KEY,
                Profile_Name        VARCHAR(100) UNIQUE NOT NULL,
                Bank_Name           VARCHAR(100),
                File_Type           VARCHAR(10)  DEFAULT 'xlsx',
                Date_Column         VARCHAR(100),
                Description_Column  VARCHAR(100),
                Debit_Column        VARCHAR(100),
                Credit_Column       VARCHAR(100),
                Amount_Column       VARCHAR(100),
                Balance_Column      VARCHAR(100),
                Date_Format         VARCHAR(30)  DEFAULT '%%d/%%m/%%Y',
                Encoding            VARCHAR(20)  DEFAULT 'utf-8',
                Skip_Rows           INTEGER      DEFAULT 0,
                Decimal_Separator   VARCHAR(1)   DEFAULT '.',
                Thousands_Separator VARCHAR(1)   DEFAULT ',',
                Sign_Convention     VARCHAR(20)  DEFAULT 'debit_credit',
                Invert_Amounts      BOOLEAN      DEFAULT FALSE,
                Created_At          TIMESTAMP    DEFAULT NOW()
            )
        """)
        # Migrate existing tables that pre-date added columns
        cur.execute("""
            ALTER TABLE Import_Profiles
            ADD COLUMN IF NOT EXISTS Invert_Amounts BOOLEAN DEFAULT FALSE
        """)
        cur.execute("""
            ALTER TABLE Import_Profiles
            ADD COLUMN IF NOT EXISTS Installment_Column VARCHAR(100) DEFAULT ''
        """)
        cur.execute("""
            ALTER TABLE Import_Profiles
            ADD COLUMN IF NOT EXISTS Secondary_Date_Column VARCHAR(100) DEFAULT ''
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS Payee_Rules (
                Rule_Id       SERIAL PRIMARY KEY,
                Pattern       VARCHAR(500) NOT NULL,
                Match_Type    VARCHAR(20)  DEFAULT 'contains',
                Payees_Id     INTEGER REFERENCES Payees(Payees_Id)     ON DELETE SET NULL,
                Categories_Id INTEGER REFERENCES Categories(Categories_Id) ON DELETE SET NULL,
                Priority      INTEGER      DEFAULT 0,
                Created_At    TIMESTAMP    DEFAULT NOW()
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS Reconciliation_Sessions (
                Session_Id         SERIAL PRIMARY KEY,
                Accounts_Id        INTEGER REFERENCES Accounts(Accounts_Id) ON DELETE CASCADE,
                Session_Date       TIMESTAMP    DEFAULT NOW(),
                Statement_Date     DATE,
                Statement_Balance  NUMERIC(18,2),
                App_Balance        NUMERIC(18,2),
                Difference         NUMERIC(18,2),
                Transactions_Count INTEGER,
                Status             VARCHAR(20)  DEFAULT 'completed',
                Notes              TEXT
            )
        """)
        cur.execute("ALTER TABLE Transactions ADD COLUMN IF NOT EXISTS Reconciled BOOLEAN DEFAULT FALSE")
        cur.execute("""
            ALTER TABLE Transactions
            ADD COLUMN IF NOT EXISTS Reconciliation_Session_Id INTEGER
                REFERENCES Reconciliation_Sessions(Session_Id) ON DELETE SET NULL
        """)
        # Statement-line action history: remembers Reconcile / Import / Skip decisions
        # so future imports of the same account can pre-fill actions automatically.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS Import_Statement_History (
                History_Id      SERIAL PRIMARY KEY,
                Accounts_Id     INTEGER NOT NULL REFERENCES Accounts(Accounts_Id) ON DELETE CASCADE,
                Description_Key TEXT    NOT NULL,
                Amount_Sign     SMALLINT NOT NULL DEFAULT 0,
                Last_Action     VARCHAR(20) NOT NULL,
                Payees_Id       INTEGER REFERENCES Payees(Payees_Id)         ON DELETE SET NULL,
                Categories_Id   INTEGER REFERENCES Categories(Categories_Id) ON DELETE SET NULL,
                Last_Seen       TIMESTAMP DEFAULT NOW(),
                Seen_Count      INTEGER DEFAULT 1,
                UNIQUE (Accounts_Id, Description_Key, Amount_Sign)
            )
        """)
    conn.commit()


def get_import_profiles():
    conn = get_connection()
    _ensure_import_tables(conn)
    df = pd.read_sql("""
        SELECT Profile_Id AS profile_id, Profile_Name AS profile_name,
               Bank_Name AS bank_name, File_Type AS file_type,
               Date_Column AS date_column, Description_Column AS description_column,
               Debit_Column AS debit_column, Credit_Column AS credit_column,
               Amount_Column AS amount_column, Balance_Column AS balance_column,
               Date_Format AS date_format, Encoding AS encoding,
               Skip_Rows AS skip_rows, Decimal_Separator AS decimal_separator,
               Thousands_Separator AS thousands_separator,
               Sign_Convention AS sign_convention,
               COALESCE(Invert_Amounts, FALSE) AS invert_amounts,
               COALESCE(Installment_Column, '')      AS installment_column,
               COALESCE(Secondary_Date_Column, '')   AS secondary_date_column
        FROM Import_Profiles ORDER BY Profile_Name
    """, conn)
    conn.close()
    return df


def get_top_categories_for_payee(payee_id: int, limit: int = 5) -> list:
    """Return full-path category names ordered by frequency of use with the given payee.

    Returns paths like 'Food : Super Market' so they match the full-path category
    lists used throughout the UI (Import, Rules, Custom Reports).
    """
    conn = get_connection()
    df = pd.read_sql("""
        WITH RECURSIVE ch AS (
            SELECT Categories_Id, Categories_Name::TEXT AS full_path
            FROM Categories WHERE Categories_Id_Parent IS NULL
            UNION ALL
            SELECT c.Categories_Id, ch.full_path || ' : ' || c.Categories_Name
            FROM Categories c JOIN ch ON c.Categories_Id_Parent = ch.Categories_Id
        )
        SELECT ch.full_path AS category_name, COUNT(*) AS usage_count
        FROM Transactions t
        JOIN Splits s ON s.Transactions_Id = t.Transactions_Id
        JOIN ch          ON ch.Categories_Id = s.Categories_Id
        WHERE t.Payees_Id = %(payee_id)s
        GROUP BY ch.full_path
        ORDER BY usage_count DESC
        LIMIT %(limit)s
    """, conn, params={"payee_id": int(payee_id), "limit": limit})
    conn.close()
    return df["category_name"].tolist() if not df.empty else []


def save_import_profile(p: dict):
    """Upsert an import profile."""
    conn = get_connection()
    _ensure_import_tables(conn)
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO Import_Profiles
                (Profile_Name, Bank_Name, File_Type, Date_Column, Description_Column,
                 Debit_Column, Credit_Column, Amount_Column, Balance_Column,
                 Date_Format, Encoding, Skip_Rows, Decimal_Separator,
                 Thousands_Separator, Sign_Convention, Invert_Amounts,
                 Installment_Column, Secondary_Date_Column)
            VALUES (%(profile_name)s,%(bank_name)s,%(file_type)s,%(date_column)s,
                    %(description_column)s,%(debit_column)s,%(credit_column)s,
                    %(amount_column)s,%(balance_column)s,%(date_format)s,
                    %(encoding)s,%(skip_rows)s,%(decimal_separator)s,
                    %(thousands_separator)s,%(sign_convention)s,%(invert_amounts)s,
                    %(installment_column)s,%(secondary_date_column)s)
            ON CONFLICT (Profile_Name) DO UPDATE SET
                Bank_Name              = EXCLUDED.Bank_Name,
                File_Type              = EXCLUDED.File_Type,
                Date_Column            = EXCLUDED.Date_Column,
                Description_Column     = EXCLUDED.Description_Column,
                Debit_Column           = EXCLUDED.Debit_Column,
                Credit_Column          = EXCLUDED.Credit_Column,
                Amount_Column          = EXCLUDED.Amount_Column,
                Balance_Column         = EXCLUDED.Balance_Column,
                Date_Format            = EXCLUDED.Date_Format,
                Encoding               = EXCLUDED.Encoding,
                Skip_Rows              = EXCLUDED.Skip_Rows,
                Decimal_Separator      = EXCLUDED.Decimal_Separator,
                Thousands_Separator    = EXCLUDED.Thousands_Separator,
                Sign_Convention        = EXCLUDED.Sign_Convention,
                Invert_Amounts         = EXCLUDED.Invert_Amounts,
                Installment_Column     = EXCLUDED.Installment_Column,
                Secondary_Date_Column  = EXCLUDED.Secondary_Date_Column
        """, {
            "profile_name": p.get("profile_name",""),
            "bank_name":    p.get("bank_name",""),
            "file_type":    p.get("file_type","xlsx"),
            "date_column":  p.get("date_column",""),
            "description_column": p.get("description_column",""),
            "debit_column": p.get("debit_column",""),
            "credit_column":p.get("credit_column",""),
            "amount_column":p.get("amount_column",""),
            "balance_column":p.get("balance_column",""),
            "date_format":  p.get("date_format","%d/%m/%Y"),
            "encoding":     p.get("encoding","utf-8"),
            "skip_rows":    p.get("skip_rows",0),
            "decimal_separator":  p.get("decimal_separator","."),
            "thousands_separator":p.get("thousands_separator",","),
            "sign_convention":       p.get("sign_convention","debit_credit"),
            "invert_amounts":        bool(p.get("invert_amounts", False)),
            "installment_column":    p.get("installment_column", ""),
            "secondary_date_column": p.get("secondary_date_column", ""),
        })
    conn.commit()
    conn.close()


def delete_import_profile(profile_id: int):
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM Import_Profiles WHERE Profile_Id = %s", (profile_id,))
    conn.commit()
    conn.close()


def get_payee_rules():
    conn = get_connection()
    _ensure_import_tables(conn)
    df = pd.read_sql("""
        WITH RECURSIVE ch AS (
            SELECT Categories_Id, Categories_Name::TEXT AS full_path
            FROM Categories WHERE Categories_Id_Parent IS NULL
            UNION ALL
            SELECT c.Categories_Id, ch.full_path || ' : ' || c.Categories_Name
            FROM Categories c JOIN ch ON c.Categories_Id_Parent = ch.Categories_Id
        )
        SELECT pr.Rule_Id    AS rule_id,
               pr.Pattern    AS pattern,
               pr.Match_Type AS match_type,
               pr.Priority   AS priority,
               py.Payees_Name AS payee_name,  pr.Payees_Id    AS payees_id,
               ch.full_path   AS category_name, pr.Categories_Id AS categories_id
        FROM Payee_Rules pr
        LEFT JOIN Payees py ON py.Payees_Id     = pr.Payees_Id
        LEFT JOIN ch        ON ch.Categories_Id = pr.Categories_Id
        ORDER BY pr.Priority DESC, pr.Rule_Id
    """, conn)
    conn.close()
    return df


def save_payee_rule(pattern: str, match_type: str = 'contains',
                    payees_id: int = None, categories_id: int = None, priority: int = 0):
    conn = get_connection()
    _ensure_import_tables(conn)
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO Payee_Rules (Pattern, Match_Type, Payees_Id, Categories_Id, Priority)
            VALUES (%s, %s, %s, %s, %s)
        """, (pattern, match_type, payees_id, categories_id, priority))
    conn.commit()
    conn.close()


def update_payee_rule(rule_id: int, pattern: str, match_type: str = 'contains',
                      payees_id: int = None, categories_id: int = None, priority: int = 0):
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE Payee_Rules
            SET Pattern = %s, Match_Type = %s, Payees_Id = %s,
                Categories_Id = %s, Priority = %s
            WHERE Rule_Id = %s
        """, (pattern, match_type, payees_id, categories_id, priority, int(rule_id)))
    conn.commit()
    conn.close()


def delete_payee_rule(rule_id: int):
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM Payee_Rules WHERE Rule_Id = %s", (rule_id,))
    conn.commit()
    conn.close()


def apply_payee_rules(description: str, rules_df: pd.DataFrame):
    """Match description against rules (ordered by priority desc). Returns (payees_id, categories_id)."""
    if rules_df.empty or not description:
        return None, None
    import re
    desc_up = description.upper()
    for _, rule in rules_df.iterrows():
        pat  = str(rule['pattern']).upper()
        mtyp = str(rule.get('match_type', 'contains')).lower()
        hit  = False
        if   mtyp == 'contains':    hit = pat in desc_up
        elif mtyp == 'starts_with': hit = desc_up.startswith(pat)
        elif mtyp == 'exact':       hit = desc_up == pat
        elif mtyp == 'regex':       hit = bool(re.search(rule['pattern'], description, re.IGNORECASE))
        if hit:
            pid = int(rule['payees_id'])    if pd.notna(rule.get('payees_id'))    else None
            cid = int(rule['categories_id'])if pd.notna(rule.get('categories_id'))else None
            return pid, cid
    return None, None


def save_reconciliation_session(accounts_id: int, statement_date, statement_balance,
                                 app_balance: float, tx_count: int, notes: str = None) -> int:
    """statement_balance may be None when the statement does not include a balance."""
    conn = get_connection()
    _ensure_import_tables(conn)
    difference = (
        round(float(statement_balance) - float(app_balance), 2)
        if statement_balance is not None else None
    )
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO Reconciliation_Sessions
                (Accounts_Id, Statement_Date, Statement_Balance, App_Balance,
                 Difference, Transactions_Count, Notes)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            RETURNING Session_Id
        """, (accounts_id, statement_date, statement_balance, app_balance,
              difference, tx_count, notes))
        session_id = cur.fetchone()[0]
    conn.commit()
    conn.close()
    return session_id


def get_account_transactions_for_reconciliation(account_id: int, date_from, date_to) -> pd.DataFrame:
    """Load existing app transactions for a date range, including category for editing."""
    conn = get_connection()
    df = pd.read_sql("""
        WITH cat_paths AS (
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
            SELECT Categories_Id, full_path FROM ch
        )
        SELECT
            t.Transactions_Id                       AS transactions_id,
            t.Date                                  AS date,
            COALESCE(py.Payees_Name, '')             AS payee,
            COALESCE(t.Description, '')              AS description,
            t.Total_Amount                           AS amount,
            t.Cleared                                AS cleared,
            t.Reconciled                             AS reconciled,
            sp.Categories_Id                         AS categories_id,
            COALESCE(cp.full_path, '')               AS category
        FROM Transactions t
        LEFT JOIN Payees py ON py.Payees_Id = t.Payees_Id
        LEFT JOIN LATERAL (
            SELECT Categories_Id
            FROM   Splits
            WHERE  Transactions_Id = t.Transactions_Id
            ORDER  BY Splits_Id
            LIMIT  1
        ) sp ON TRUE
        LEFT JOIN cat_paths cp ON cp.Categories_Id = sp.Categories_Id
        WHERE t.Accounts_Id = %(aid)s
          AND t.Date BETWEEN %(d0)s AND %(d1)s
          AND t.Transfers_Id IS NULL
        ORDER BY t.Date, t.Transactions_Id
    """, conn, params={"aid": int(account_id), "d0": date_from, "d1": date_to})
    conn.close()
    if not df.empty:
        df["date"]         = pd.to_datetime(df["date"]).dt.date
        df["amount"]       = df["amount"].astype(float)
        df["categories_id"] = pd.to_numeric(df["categories_id"], errors="coerce")
    return df


def update_transaction_description(transaction_id: int, description: str):
    """Update the Description field of a transaction."""
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE Transactions SET Description = %s WHERE Transactions_Id = %s",
            (description.strip(), int(transaction_id)),
        )
    conn.commit()
    conn.close()


def update_transaction_amount(transaction_id: int, amount: float):
    """Update Total_Amount on the transaction and sync the primary split amount."""
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE Transactions SET Total_Amount = %s WHERE Transactions_Id = %s",
            (float(amount), int(transaction_id)),
        )
        # Keep the primary split in sync so split totals stay consistent.
        cur.execute("""
            UPDATE Splits SET Amount = %s
            WHERE  Splits_Id = (
                SELECT Splits_Id FROM Splits
                WHERE  Transactions_Id = %s
                ORDER  BY Splits_Id LIMIT 1
            )
        """, (float(amount), int(transaction_id)))
    conn.commit()
    conn.close()


def update_transaction_category(transaction_id: int,
                                 categories_id: int | None,
                                 amount: float):
    """Upsert the primary split category for a transaction.

    Updates the first existing split (if any), or inserts a new one.
    Passing ``categories_id=None`` removes the primary split entirely.
    """
    conn = get_connection()
    with conn.cursor() as cur:
        if categories_id is None:
            cur.execute(
                "DELETE FROM Splits WHERE Splits_Id = ("
                "  SELECT Splits_Id FROM Splits"
                "  WHERE  Transactions_Id = %s ORDER BY Splits_Id LIMIT 1"
                ")",
                (int(transaction_id),),
            )
        else:
            cur.execute("""
                UPDATE Splits
                SET    Categories_Id = %s, Amount = %s
                WHERE  Splits_Id = (
                    SELECT Splits_Id FROM Splits
                    WHERE  Transactions_Id = %s
                    ORDER  BY Splits_Id LIMIT 1
                )
            """, (int(categories_id), float(amount), int(transaction_id)))
            if cur.rowcount == 0:
                cur.execute(
                    "INSERT INTO Splits (Transactions_Id, Categories_Id, Amount)"
                    " VALUES (%s, %s, %s)",
                    (int(transaction_id), int(categories_id), float(amount)),
                )
    conn.commit()
    conn.close()


def mark_transactions_reconciled(tx_ids: list, session_id: int):
    """Bulk-mark transactions as reconciled and link them to the session."""
    if not tx_ids:
        return
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE Transactions
            SET Reconciled = TRUE, Reconciliation_Session_Id = %s
            WHERE Transactions_Id = ANY(%s)
        """, (session_id, list(tx_ids)))
    conn.commit()
    conn.close()


def get_reconciliation_history(accounts_id: int):
    conn = get_connection()
    _ensure_import_tables(conn)
    df = pd.read_sql("""
        SELECT Session_Id AS session_id, Session_Date AS session_date,
               Statement_Date AS statement_date, Statement_Balance AS statement_balance,
               App_Balance AS app_balance, Difference AS difference,
               Transactions_Count AS tx_count, Status AS status, Notes AS notes
        FROM Reconciliation_Sessions
        WHERE Accounts_Id = %(aid)s
        ORDER BY Session_Date DESC
    """, conn, params={"aid": accounts_id})
    conn.close()
    if not df.empty:
        df['session_date']   = pd.to_datetime(df['session_date'])
        df['statement_date'] = pd.to_datetime(df['statement_date'])
    return df


def get_statement_history_suggestions(accounts_id: int,
                                       desc_keys: list[str]) -> dict[str, dict]:
    """Return a dict of {description_key: {last_action, payees_id, categories_id, seen_count}}
    for any keys that have been seen before for this account.

    Only keys present in *desc_keys* are returned (avoids a full-table scan).
    """
    if not desc_keys:
        return {}
    conn = get_connection()
    df = pd.read_sql("""
        SELECT Description_Key  AS description_key,
               Amount_Sign      AS amount_sign,
               Last_Action      AS last_action,
               Payees_Id        AS payees_id,
               Categories_Id    AS categories_id,
               Seen_Count       AS seen_count
        FROM Import_Statement_History
        WHERE Accounts_Id = %(aid)s
          AND Description_Key = ANY(%(keys)s)
    """, conn, params={"aid": int(accounts_id), "keys": list(set(desc_keys))})
    conn.close()
    result: dict[str, dict] = {}
    for _, row in df.iterrows():
        key = str(row['description_key'])
        pid = int(row['payees_id'])    if pd.notna(row['payees_id'])    else None
        cid = int(row['categories_id'])if pd.notna(row['categories_id'])else None
        # If a description appears with both signs, prefer the entry with more history.
        if key not in result or int(row['seen_count']) > result[key]['seen_count']:
            result[key] = {
                'last_action':   str(row['last_action']),
                'payees_id':     pid,
                'categories_id': cid,
                'seen_count':    int(row['seen_count']),
            }
    return result


def save_statement_history(accounts_id: int, rows: list[dict]):
    """Upsert statement-line history entries.

    Each entry in *rows* must have:
        description_key : str   (normalised, lowercase)
        amount_sign     : int   (-1 or 1)
        last_action     : str   ('Reconcile' | 'Import' | 'Skip')
        payees_id       : int | None
        categories_id   : int | None
    """
    if not rows:
        return
    conn = get_connection()
    with conn.cursor() as cur:
        for entry in rows:
            cur.execute("""
                INSERT INTO Import_Statement_History
                    (Accounts_Id, Description_Key, Amount_Sign,
                     Last_Action, Payees_Id, Categories_Id)
                VALUES (%(aid)s, %(dk)s, %(sign)s, %(action)s, %(pid)s, %(cid)s)
                ON CONFLICT (Accounts_Id, Description_Key, Amount_Sign)
                DO UPDATE SET
                    Last_Action   = EXCLUDED.Last_Action,
                    Payees_Id     = EXCLUDED.Payees_Id,
                    Categories_Id = EXCLUDED.Categories_Id,
                    Last_Seen     = NOW(),
                    Seen_Count    = Import_Statement_History.Seen_Count + 1
            """, {
                'aid':    int(accounts_id),
                'dk':     str(entry['description_key']),
                'sign':   int(entry['amount_sign']),
                'action': str(entry['last_action']),
                'pid':    entry.get('payees_id'),
                'cid':    entry.get('categories_id'),
            })
    conn.commit()
    conn.close()


# ======================================================
# WATCHLIST
# ======================================================

def _ensure_watchlist_table():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS Watchlist (
                    Watchlist_Id    SERIAL PRIMARY KEY,
                    Securities_Id   INTEGER NOT NULL
                                    REFERENCES Securities(Securities_Id) ON DELETE CASCADE,
                    Target_Price    NUMERIC(20,8),
                    Stop_Loss       NUMERIC(20,8),
                    Note            TEXT,
                    Added_Date      DATE DEFAULT CURRENT_DATE,
                    UNIQUE(Securities_Id)
                )
            """)
        conn.commit()
    finally:
        conn.close()


def get_watchlist() -> pd.DataFrame:
    _ensure_watchlist_table()
    conn = get_connection()
    df = pd.read_sql("""
        WITH latest_price AS (
            SELECT DISTINCT ON (Securities_Id)
                   Securities_Id, Close AS current_price, Date AS price_date
            FROM   Historical_Prices
            ORDER  BY Securities_Id, Date DESC
        ),
        fx AS (
            SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
            FROM   Historical_FX ORDER BY Currencies_Id_1, Date DESC
        ),
        held AS (
            SELECT Securities_Id, SUM(Quantity) AS qty
            FROM   Holdings WHERE Quantity > 0
            GROUP  BY Securities_Id
        )
        SELECT
            w.Watchlist_Id                                              AS watchlist_id,
            s.Securities_Id                                             AS securities_id,
            s.Securities_Name                                           AS securities_name,
            s.Securities_Type::text                                     AS securities_type,
            s.Ticker                                                    AS ticker,
            c.Currencies_ShortName                                      AS currency,
            lp.current_price,
            lp.price_date,
            ROUND((lp.current_price * COALESCE(fx.FX_Rate,1))::numeric,2)
                                                                        AS current_price_eur,
            w.Target_Price                                              AS target_price,
            w.Stop_Loss                                                 AS stop_loss,
            s.Analyst_Target_Price                                      AS analyst_target,
            s.Dividend_Yield                                            AS dividend_yield,
            CASE
                WHEN w.Target_Price IS NOT NULL AND lp.current_price IS NOT NULL
                THEN ROUND(((lp.current_price - w.Target_Price)
                            / NULLIF(w.Target_Price,0) * 100)::numeric, 2)
            END                                                         AS pct_from_target,
            CASE
                WHEN w.Stop_Loss IS NOT NULL AND lp.current_price IS NOT NULL
                THEN ROUND(((lp.current_price - w.Stop_Loss)
                            / NULLIF(w.Stop_Loss,0) * 100)::numeric, 2)
            END                                                         AS pct_from_stop,
            CASE
                WHEN s.Analyst_Target_Price IS NOT NULL AND lp.current_price IS NOT NULL
                THEN ROUND(((s.Analyst_Target_Price - lp.current_price)
                            / NULLIF(lp.current_price,0) * 100)::numeric, 2)
            END                                                         AS upside_to_analyst,
            COALESCE(h.qty, 0) > 0                                      AS already_held,
            w.Note                                                      AS note,
            w.Added_Date                                                AS added_date
        FROM   Watchlist w
        JOIN   Securities s  ON s.Securities_Id  = w.Securities_Id
        JOIN   Currencies c  ON c.Currencies_Id  = s.Currencies_Id
        LEFT JOIN latest_price lp ON lp.Securities_Id = w.Securities_Id
        LEFT JOIN fx              ON fx.Currencies_Id_1 = s.Currencies_Id
        LEFT JOIN held h          ON h.Securities_Id   = w.Securities_Id
        ORDER  BY s.Securities_Name
    """, conn)
    conn.close()
    return df


def add_watchlist_item(securities_id: int, target_price=None,
                       stop_loss=None, note: str = None):
    _ensure_watchlist_table()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO Watchlist (Securities_Id, Target_Price, Stop_Loss, Note)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (Securities_Id) DO UPDATE SET
                    Target_Price = EXCLUDED.Target_Price,
                    Stop_Loss    = EXCLUDED.Stop_Loss,
                    Note         = EXCLUDED.Note
            """, (securities_id,
                  float(target_price) if target_price else None,
                  float(stop_loss)    if stop_loss    else None,
                  note or None))
        conn.commit()
    finally:
        conn.close()


def remove_watchlist_item(watchlist_id: int):
    _ensure_watchlist_table()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM Watchlist WHERE Watchlist_Id = %s",
                        (watchlist_id,))
        conn.commit()
    finally:
        conn.close()


# ======================================================
# ALERTS
# ======================================================

def _ensure_alerts_table():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS Alerts (
                    Alert_Id      SERIAL PRIMARY KEY,
                    Alert_Type    TEXT NOT NULL,
                    Securities_Id INTEGER REFERENCES Securities(Securities_Id)
                                  ON DELETE CASCADE,
                    Asset_Type    TEXT,
                    Threshold     NUMERIC(20,8),
                    Direction     TEXT,
                    Is_Active     BOOLEAN DEFAULT TRUE,
                    Note          TEXT,
                    Created_At    TIMESTAMP DEFAULT NOW()
                )
            """)
        conn.commit()
    finally:
        conn.close()


def get_alerts() -> pd.DataFrame:
    _ensure_alerts_table()
    conn = get_connection()
    df = pd.read_sql("""
        WITH latest_price AS (
            SELECT DISTINCT ON (Securities_Id)
                   Securities_Id, Close AS current_price, Date AS price_date
            FROM   Historical_Prices
            ORDER  BY Securities_Id, Date DESC
        )
        SELECT a.Alert_Id        AS alert_id,
               a.Alert_Type      AS alert_type,
               a.Securities_Id   AS securities_id,
               s.Securities_Name AS securities_name,
               a.Asset_Type      AS asset_type,
               a.Threshold       AS threshold,
               a.Direction       AS direction,
               a.Is_Active       AS is_active,
               a.Note            AS note,
               a.Created_At      AS created_at,
               lp.current_price  AS current_price,
               lp.price_date     AS price_date
        FROM   Alerts a
        LEFT JOIN Securities s    ON s.Securities_Id  = a.Securities_Id
        LEFT JOIN latest_price lp ON lp.Securities_Id = a.Securities_Id
        ORDER  BY a.Created_At DESC
    """, conn)
    conn.close()
    return df


def save_alert(alert_type: str, securities_id=None, asset_type: str = None,
               threshold=None, direction: str = None, note: str = None,
               alert_id: int = None):
    _ensure_alerts_table()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if alert_id:
                cur.execute("""
                    UPDATE Alerts
                    SET Alert_Type=%s, Securities_Id=%s, Asset_Type=%s,
                        Threshold=%s, Direction=%s, Note=%s
                    WHERE Alert_Id=%s
                """, (alert_type, securities_id, asset_type,
                      threshold, direction, note, alert_id))
            else:
                cur.execute("""
                    INSERT INTO Alerts
                        (Alert_Type, Securities_Id, Asset_Type, Threshold, Direction, Note)
                    VALUES (%s,%s,%s,%s,%s,%s)
                """, (alert_type, securities_id, asset_type,
                      threshold, direction, note))
        conn.commit()
    finally:
        conn.close()


def toggle_alert(alert_id: int, is_active: bool):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE Alerts SET Is_Active=%s WHERE Alert_Id=%s",
                        (is_active, alert_id))
        conn.commit()
    finally:
        conn.close()


def delete_alert(alert_id: int):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM Alerts WHERE Alert_Id=%s", (alert_id,))
        conn.commit()
    finally:
        conn.close()


def _ensure_signal_notifications_table():
    """Create Signal_Notifications table if it does not exist."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS Signal_Notifications (
                    Securities_Id     INTEGER PRIMARY KEY
                                      REFERENCES Securities(Securities_Id) ON DELETE CASCADE,
                    Last_Known_Signal TEXT,
                    Previous_Signal   TEXT,
                    Changed_At        TIMESTAMP DEFAULT NOW(),
                    Acknowledged      BOOLEAN DEFAULT TRUE
                )
            """)
            # Add Previous_Signal column if upgrading from earlier schema
            cur.execute("""
                ALTER TABLE Signal_Notifications
                ADD COLUMN IF NOT EXISTS Previous_Signal TEXT
            """)
        conn.commit()
    finally:
        conn.close()


def refresh_signal_notifications():
    """Compare current final_signal values against stored ones; record any changes.

    Called by the scheduler after market data refresh — NOT on every dashboard load.
    New securities are seeded silently (Acknowledged=TRUE) so they don't flood the
    dashboard on first run.
    """
    _ensure_signal_notifications_table()
    current = _compute_current_signals()
    if current.empty:
        return

    conn = get_connection()
    try:
        stored = pd.read_sql(
            "SELECT Securities_Id AS securities_id, Last_Known_Signal AS last_known_signal "
            "FROM Signal_Notifications",
            conn,
        )
        stored_map = (
            stored.set_index('securities_id')['last_known_signal'].to_dict()
            if not stored.empty else {}
        )

        with conn.cursor() as cur:
            for _, row in current.iterrows():
                sid    = int(row['securities_id'])
                signal = row['final_signal'] or row['recommendation_signal']
                if not signal:
                    continue
                prev = stored_map.get(sid)
                if prev is None:
                    # First time — seed silently, no notification
                    cur.execute(
                        "INSERT INTO Signal_Notifications "
                        "(Securities_Id, Last_Known_Signal, Previous_Signal, Acknowledged) "
                        "VALUES (%s, %s, NULL, TRUE) ON CONFLICT (Securities_Id) DO NOTHING",
                        (sid, signal),
                    )
                elif prev != signal:
                    # Signal changed — record and mark unacknowledged
                    cur.execute(
                        "UPDATE Signal_Notifications "
                        "SET Previous_Signal=%s, Last_Known_Signal=%s, "
                        "    Changed_At=NOW(), Acknowledged=FALSE "
                        "WHERE Securities_Id=%s",
                        (prev, signal, sid),
                    )
        conn.commit()
    finally:
        conn.close()


def acknowledge_signal_notification(securities_id: int):
    """Mark a signal change as acknowledged (user has seen it)."""
    _ensure_signal_notifications_table()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE Signal_Notifications SET Acknowledged=TRUE WHERE Securities_Id=%s",
                (securities_id,)
            )
        conn.commit()
    finally:
        conn.close()


def _compute_current_signals() -> pd.DataFrame:
    """Compute final_signal for ALL securities using the same logic as get_portfolio_signals.

    Returns DataFrame with (securities_id, securities_name, final_signal).
    Holdings value/cost columns are omitted — only the signal matters here.
    The SELL/REDUCE condition treats every security as if it has an open position
    (consistent with notifications covering the full universe, not just held names).
    """
    conn = get_connection()
    try:
        df = pd.read_sql("""
            WITH base_data AS (
                SELECT Securities_Id, Date, Close,
                       (Close / LAG(Close) OVER (PARTITION BY Securities_Id ORDER BY Date) - 1) AS daily_ret
                FROM Historical_Prices
                WHERE Date >= (CURRENT_DATE - INTERVAL '62 months')
            ),
            ranked_prices AS (
                SELECT Securities_Id, Date, Close AS price_today,
                       LAG(Close,   1) OVER (PARTITION BY Securities_Id ORDER BY Date) AS price_1d,
                       LAG(Close,   5) OVER (PARTITION BY Securities_Id ORDER BY Date) AS price_1w,
                       LAG(Close,  21) OVER (PARTITION BY Securities_Id ORDER BY Date) AS price_1m,
                       LAG(Close,  63) OVER (PARTITION BY Securities_Id ORDER BY Date) AS price_3m,
                       LAG(Close, 126) OVER (PARTITION BY Securities_Id ORDER BY Date) AS price_6m,
                       LAG(Close, 252) OVER (PARTITION BY Securities_Id ORDER BY Date) AS price_1y,
                       LAG(Close, 756) OVER (PARTITION BY Securities_Id ORDER BY Date) AS price_3y,
                       ROW_NUMBER() OVER (PARTITION BY Securities_Id ORDER BY Date DESC) AS rev_rank
                FROM base_data
            ),
            ytd_prices AS (
                SELECT DISTINCT ON (Securities_Id) Securities_Id, Close AS price_ytd_start
                FROM Historical_Prices
                WHERE Date < date_trunc('year', CURRENT_DATE)
                ORDER BY Securities_Id, Date DESC
            ),
            latest_only AS (
                SELECT rp.*, yp.price_ytd_start
                FROM ranked_prices rp
                LEFT JOIN ytd_prices yp ON rp.Securities_Id = yp.Securities_Id
                WHERE rp.rev_rank = 1
            ),
            performance_data AS (
                SELECT
                    lo.Securities_Id, sec.Securities_Name, lo.price_today,
                    ROUND(((lo.price_today / NULLIF(lo.price_1d,  0)) - 1) * 100, 2) AS daily_chg_pct,
                    ROUND(((lo.price_today / NULLIF(lo.price_1w,  0)) - 1) * 100, 2) AS weekly_chg_pct,
                    ROUND(((lo.price_today / NULLIF(lo.price_1m,  0)) - 1) * 100, 2) AS monthly_chg_pct,
                    ROUND(((lo.price_today / NULLIF(lo.price_3m,  0)) - 1) * 100, 2) AS quarterly_chg_pct,
                    ROUND(((lo.price_today / NULLIF(lo.price_6m,  0)) - 1) * 100, 2) AS semiannual_chg_pct,
                    ROUND(((lo.price_today / NULLIF(lo.price_1y,  0)) - 1) * 100, 2) AS annual_chg_pct,
                    ROUND(((lo.price_today / NULLIF(lo.price_3y,  0)) - 1) * 100, 2) AS triannual_chg_pct,
                    ROUND(((lo.price_today / NULLIF(lo.price_ytd_start, 0)) - 1) * 100, 2) AS ytd_chg_pct,
                    (SELECT ROUND((STDDEV(daily_ret) * SQRT(252) * 100)::numeric, 2)
                       FROM base_data bd WHERE bd.Securities_Id = lo.Securities_Id
                         AND bd.Date > (lo.Date - INTERVAL '1 month'))  AS vol_1m_ann,
                    (SELECT ROUND((STDDEV(daily_ret) * SQRT(252) * 100)::numeric, 2)
                       FROM base_data bd WHERE bd.Securities_Id = lo.Securities_Id
                         AND bd.Date > (lo.Date - INTERVAL '3 months')) AS vol_3m_ann,
                    (SELECT ROUND((STDDEV(daily_ret) * SQRT(252) * 100)::numeric, 2)
                       FROM base_data bd WHERE bd.Securities_Id = lo.Securities_Id
                         AND bd.Date > (lo.Date - INTERVAL '12 months')) AS vol_1y_ann,
                    (SELECT ROUND((STDDEV(daily_ret) * SQRT(252) * 100)::numeric, 2)
                       FROM base_data bd WHERE bd.Securities_Id = lo.Securities_Id
                         AND bd.Date >= date_trunc('year', CURRENT_DATE)) AS vol_ytd_ann
                FROM latest_only lo
                JOIN Securities sec ON lo.Securities_Id = sec.Securities_Id
                WHERE sec.Is_Active
                  AND lo.Date > (CURRENT_DATE - INTERVAL '15 days')
            ),
            rfr AS (
                SELECT COALESCE(annual_chg_pct, 2.36) AS value
                FROM performance_data
                WHERE Securities_Name LIKE 'Hellenic T-Bill 52W%%'
                LIMIT 1
            ),
            investment_signals AS (
                SELECT *,
                    ROUND(((monthly_chg_pct * 0.5) + (quarterly_chg_pct * 0.3) + (annual_chg_pct * 0.2))::numeric, 2) AS quality_score,
                    ROUND(((annual_chg_pct - (SELECT value FROM rfr)) / NULLIF(vol_1y_ann, 0))::numeric, 2) AS sharpe_ratio
                FROM performance_data
                WHERE vol_1y_ann > 0
            ),
            holdings_agg AS (
                SELECT Securities_Id, SUM(COALESCE(Quantity, 0)) AS current_qty
                FROM Holdings
                GROUP BY Securities_Id
            ),
            recommendations AS (
                SELECT
                    sig.Securities_Id,
                    sig.Securities_Name,
                    sig.price_today,
                    sec.Analyst_Rating       AS wall_street_view,
                    sec.Analyst_Target_Price AS analyst_target_price,
                    ROUND((((sec.Analyst_Target_Price / NULLIF(sig.price_today, 0)) - 1) * 100)::numeric, 2) AS upside_pct,
                    COALESCE(ha.current_qty, 0) AS current_qty,
                    CASE
                        WHEN COALESCE(ha.current_qty, 0) > 0 AND (sharpe_ratio < 0 OR quality_score < -5) THEN '🔴 SELL / REDUCE'
                        WHEN sharpe_ratio > 1.2 AND quality_score > 10 THEN '🟢 STRONG BUY'
                        WHEN sharpe_ratio > 0.7 OR quality_score > 8  THEN '🟢 BUY'
                        WHEN sharpe_ratio > 0.3 OR quality_score > 0  THEN '🟡 HOLD'
                        WHEN COALESCE(ha.current_qty, 0) = 0 AND sharpe_ratio > 0.5 THEN '👀 WATCHLIST'
                        ELSE '🟡 HOLD'
                    END AS recommendation_signal
                FROM investment_signals sig
                JOIN Securities sec ON sig.Securities_Id = sec.Securities_Id
                LEFT JOIN holdings_agg ha ON ha.Securities_Id = sig.Securities_Id
            )
            SELECT
                Securities_Id   AS securities_id,
                Securities_Name AS securities_name,
                CASE
                    WHEN recommendation_signal LIKE '🟢%%' AND wall_street_view IN ('buy','strong_buy') AND upside_pct > 20 THEN '🔥 HIGH CONVICTION BUY'
                    WHEN recommendation_signal LIKE '🟢%%' AND wall_street_view = 'strong_buy'          THEN '💎 STRONG CONVICTION'
                    WHEN recommendation_signal LIKE '🟢%%' AND wall_street_view = 'buy'                 THEN '💎 CONVICTION BUY'
                    WHEN recommendation_signal LIKE '🟢%%' AND wall_street_view = 'hold'                THEN '🚀 MOMENTUM BUY'
                    WHEN recommendation_signal LIKE '🟢%%' AND wall_street_view IN ('sell','underperform') THEN '🔍 CONTRARIAN BUY'
                    WHEN recommendation_signal LIKE '🟢%%' AND wall_street_view IS NULL                 THEN '⚙️ ALGO BUY'
                    WHEN recommendation_signal LIKE '🔴%%' AND wall_street_view IN ('sell','underperform') THEN '⚠️ CONVICTION SELL'
                    WHEN recommendation_signal LIKE '🔴%%' AND wall_street_view = 'hold'                THEN '📉 MOMENTUM SELL'
                    WHEN recommendation_signal LIKE '🔴%%' AND wall_street_view IN ('buy','strong_buy') THEN '🔍 CONTRARIAN SELL'
                    WHEN recommendation_signal LIKE '🔴%%' AND wall_street_view IS NULL                 THEN '⚙️ ALGO SELL'
                    WHEN recommendation_signal LIKE '🟡%%' AND wall_street_view IN ('sell','underperform') THEN '⚠️ ANALYST CAUTION'
                    WHEN recommendation_signal LIKE '🟡%%' AND wall_street_view IN ('buy','strong_buy') THEN '📈 ANALYST UPGRADE'
                    WHEN recommendation_signal LIKE '👀%%' AND wall_street_view IN ('buy','strong_buy') THEN '🔬 WATCH: ANALYST BUY'
                    WHEN recommendation_signal LIKE '👀%%' AND wall_street_view IN ('sell','underperform') THEN '🔬 WATCH: ANALYST SELL'
                    ELSE recommendation_signal
                END AS final_signal
            FROM recommendations
            ORDER BY Securities_Name
        """, conn)
        return df
    finally:
        conn.close()


def _get_app_setting_lead_days(setting_key: str, default: int) -> int:
    """Read a numeric lead-days value out of the 'app-settings' preference
    blob (Tools → System → App Settings), falling back to *default* if the
    preference row, table, or key doesn't exist yet.
    """
    try:
        conn = get_connection()
        _ensure_user_preferences_table(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT Pref_Value FROM User_Preferences WHERE Pref_Key = 'app-settings'")
            row = cur.fetchone()
        conn.close()
        if row and row[0] and row[0].get(setting_key) is not None:
            return int(row[0][setting_key])
    except Exception:
        pass
    return default


def _get_bond_event_alerts(lead_days: int = None) -> list:
    """Maturity/coupon heads-up for every currently held bond — no per-bond
    setup required, unlike price/drift Alerts, since Maturity_Date/Coupon_Rate/
    Coupon_Frequency already fully describe the schedule for a held bond.

    lead_days defaults to the user's Tools → System → App Settings
    "Bond Alerts" value (Pref_Key='app-settings'.bondAlertLeadDays), falling
    back to 7 if unset — kept as an explicit override param for callers/tests.
    """
    from dateutil.relativedelta import relativedelta

    if lead_days is None:
        lead_days = _get_app_setting_lead_days('bondAlertLeadDays', 7)

    conn = get_connection()
    try:
        df = pd.read_sql("""
            WITH fx AS (
                SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
                FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC
            )
            SELECT h.Securities_Id AS securities_id, s.Securities_Name AS securities_name,
                   h.Quantity AS quantity, s.Face_Value AS face_value,
                   s.Coupon_Rate AS coupon_rate, s.Coupon_Frequency AS coupon_frequency,
                   s.Maturity_Date AS maturity_date, c.Currencies_ShortName AS currency,
                   COALESCE(fx.FX_Rate, 1) AS fx_rate
            FROM Holdings h
            JOIN Securities s ON s.Securities_Id = h.Securities_Id
            JOIN Currencies c ON c.Currencies_Id = s.Currencies_Id
            LEFT JOIN fx ON fx.Currencies_Id_1 = s.Currencies_Id
            WHERE h.Quantity > 0 AND s.Securities_Type = 'Bond' AND s.Maturity_Date IS NOT NULL
        """, conn)
    finally:
        conn.close()

    if df.empty:
        return []

    today = datetime.now().date()
    period_months = {'Semi-Annual': 6, 'Quarterly': 3, 'Monthly': 1}
    results = []
    for _, row in df.iterrows():
        maturity = pd.Timestamp(row['maturity_date']).date()
        name = row['securities_name']
        days_to_maturity = (maturity - today).days
        if 0 <= days_to_maturity <= lead_days:
            when = 'today' if days_to_maturity == 0 else f"in {days_to_maturity} day{'s' if days_to_maturity != 1 else ''}"
            results.append({
                'level': 'warning',
                'message': f"📅 **Bond Maturity** — {name} matures {when} ({maturity.strftime('%d %b %Y')}).",
                'securities_id': int(row['securities_id']),
                'type': 'bond_maturity',
            })

        freq = row['coupon_frequency']
        rate = row['coupon_rate']
        if freq and freq != 'At Maturity' and rate is not None and maturity >= today:
            months = period_months.get(freq, 12)  # default Annual
            next_coupon = maturity
            while next_coupon - relativedelta(months=months) >= today:
                next_coupon -= relativedelta(months=months)
            days_until = (next_coupon - today).days
            if 0 <= days_until <= lead_days:
                frac = {'Semi-Annual': 0.5, 'Quarterly': 0.25, 'Monthly': 1.0 / 12}.get(freq, 1.0)
                amount_eur = float(row['quantity']) * float(row['face_value'] or 0) * float(rate) / 100 * frac * float(row['fx_rate'])
                when = 'today' if days_until == 0 else f"in {days_until} day{'s' if days_until != 1 else ''}"
                results.append({
                    'level': 'info',
                    'message': (f"💰 **Bond Coupon** — {name} pays a coupon {when} "
                                f"({next_coupon.strftime('%d %b %Y')}), ≈€{amount_eur:,.2f}."),
                    'securities_id': int(row['securities_id']),
                    'type': 'bond_coupon',
                })
    return results


def _get_dividend_alerts(lead_days: int = None) -> list:
    """Upcoming-payment heads-up for every currently held dividend-paying
    security — no per-security setup required, same rationale as bonds.
    Dividend_Pay_Date on Securities is the last date a payment was seen
    (from Yahoo's `dividendDate`), projected forward by Dividend_Frequency
    until it lands on or after today, since Yahoo doesn't expose a full
    forward dividend calendar.

    lead_days defaults to the user's Tools → System → App Settings
    "Dividend Alerts" value (Pref_Key='app-settings'.dividendAlertLeadDays),
    falling back to 3 if unset.
    """
    from dateutil.relativedelta import relativedelta

    if lead_days is None:
        lead_days = _get_app_setting_lead_days('dividendAlertLeadDays', 3)

    conn = get_connection()
    try:
        df = pd.read_sql("""
            WITH fx AS (
                SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
                FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC
            )
            SELECT h.Securities_Id AS securities_id, s.Securities_Name AS securities_name,
                   h.Quantity AS quantity, s.Dividend_Rate AS dividend_rate,
                   s.Dividend_Frequency AS dividend_frequency,
                   s.Dividend_Pay_Date AS dividend_pay_date,
                   c.Currencies_ShortName AS currency, COALESCE(fx.FX_Rate, 1) AS fx_rate
            FROM Holdings h
            JOIN Securities s ON s.Securities_Id = h.Securities_Id
            JOIN Currencies c ON c.Currencies_Id = s.Currencies_Id
            LEFT JOIN fx ON fx.Currencies_Id_1 = s.Currencies_Id
            WHERE h.Quantity > 0 AND s.Dividend_Pay_Date IS NOT NULL
              AND s.Dividend_Frequency IS NOT NULL AND s.Dividend_Rate IS NOT NULL
        """, conn)
    finally:
        conn.close()

    if df.empty:
        return []

    today = datetime.now().date()
    period_months = {'Semi-Annual': 6, 'Quarterly': 3, 'Monthly': 1}
    results = []
    for _, row in df.iterrows():
        last_pay = pd.Timestamp(row['dividend_pay_date']).date()
        freq = row['dividend_frequency']
        months = period_months.get(freq, 12)  # default Annual
        next_pay = last_pay
        while next_pay < today:
            next_pay += relativedelta(months=months)
        days_until = (next_pay - today).days
        if 0 <= days_until <= lead_days:
            frac = {'Semi-Annual': 0.5, 'Quarterly': 0.25, 'Monthly': 1.0 / 12}.get(freq, 1.0)
            amount_eur = float(row['quantity']) * float(row['dividend_rate']) * frac * float(row['fx_rate'])
            when = 'today' if days_until == 0 else f"in {days_until} day{'s' if days_until != 1 else ''}"
            results.append({
                'level': 'info',
                'message': (f"💵 **Dividend Payment** — {row['securities_name']} pays a dividend {when} "
                            f"({next_pay.strftime('%d %b %Y')}), ≈€{amount_eur:,.2f}."),
                'securities_id': int(row['securities_id']),
                'type': 'dividend_payment',
            })
    return results


def check_triggered_alerts() -> list:
    """Return list of triggered alert dicts ({level, message}).
    Never raises — returns [] on any error.
    """
    try:
        _ensure_alerts_table()
        _ensure_signal_notifications_table()
        conn = get_connection()
        alerts_df = pd.read_sql(
            "SELECT * FROM Alerts WHERE Is_Active = TRUE", conn)
        conn.close()

        results = []

        # ── price alerts ──────────────────────────────────────────────────────
        price_rows = alerts_df[
            alerts_df['alert_type'].isin(['price_above', 'price_below'])
            & alerts_df['securities_id'].notna()
        ]
        if not price_rows.empty:
            sec_ids = price_rows['securities_id'].astype(int).tolist()
            conn = get_connection()
            df_p = pd.read_sql("""
                SELECT DISTINCT ON (hp.Securities_Id)
                       hp.Securities_Id AS securities_id,
                       hp.Close         AS current_price,
                       s.Securities_Name AS securities_name
                FROM   Historical_Prices hp
                JOIN   Securities s ON s.Securities_Id = hp.Securities_Id
                WHERE  hp.Securities_Id = ANY(%s)
                ORDER  BY hp.Securities_Id, hp.Date DESC
            """, conn, params=(sec_ids,))
            conn.close()
            prices_map = df_p.set_index('securities_id').to_dict('index')

            for _, row in price_rows.iterrows():
                sid  = int(row['securities_id'])
                info = prices_map.get(sid)
                if not info or row['threshold'] is None:
                    continue
                cur_px = float(info['current_price'])
                thr    = float(row['threshold'])
                name   = info['securities_name']
                suffix = f" _{row['note']}_" if row.get('note') else ''
                if row['alert_type'] == 'price_above' and cur_px > thr:
                    results.append({
                        'level': 'warning',
                        'message': (f"🔔 **Price Alert** — {name} is at "
                                    f"**{cur_px:,.4f}**, above your threshold "
                                    f"of {thr:,.4f}.{suffix}"),
                    })
                elif row['alert_type'] == 'price_below' and cur_px < thr:
                    results.append({
                        'level': 'error',
                        'message': (f"🔻 **Price Alert** — {name} is at "
                                    f"**{cur_px:,.4f}**, below your threshold "
                                    f"of {thr:,.4f}.{suffix}"),
                    })

        # ── allocation drift alerts ───────────────────────────────────────────
        drift_rows = alerts_df[alerts_df['alert_type'] == 'allocation_drift']
        if not drift_rows.empty:
            alloc = get_asset_allocation_data()
            if not alloc.empty:
                for _, row in drift_rows.iterrows():
                    at  = row.get('asset_type')
                    thr = float(row['threshold']) if row['threshold'] is not None else None
                    if not at or thr is None:
                        continue
                    match = alloc[alloc['securities_type'] == at]
                    if match.empty:
                        continue
                    delta = abs(float(match.iloc[0]['delta_pct']))
                    if delta > thr:
                        suffix = f" _{row['note']}_" if row.get('note') else ''
                        results.append({
                            'level': 'warning',
                            'message': (f"⚖️ **Allocation Drift** — {at} is "
                                        f"**{delta:.1f}%** off target "
                                        f"(threshold: {thr:.1f}%).{suffix}"),
                        })

        # ── final signal change notifications (pre-computed by scheduler) ───────
        try:
            conn2 = get_connection()
            pending = pd.read_sql("""
                SELECT sn.Securities_Id AS securities_id,
                       s.Securities_Name AS securities_name,
                       sn.Last_Known_Signal AS current_signal,
                       sn.Previous_Signal AS previous_signal,
                       sn.Changed_At AS changed_at
                FROM Signal_Notifications sn
                JOIN Securities s ON s.Securities_Id = sn.Securities_Id
                WHERE sn.Acknowledged = FALSE
                ORDER BY sn.Changed_At DESC
            """, conn2)
            conn2.close()
            for _, row in pending.iterrows():
                changed_at = row.get('changed_at')
                if changed_at is not None:
                    try:
                        ts = pd.Timestamp(changed_at)
                        when = f"{ts.day} {ts.strftime('%b %Y %H:%M')}"
                    except Exception:
                        when = str(changed_at)[:16]
                else:
                    when = None
                suffix = f" (as of {when})" if when else ''
                results.append({
                    'level': 'info',
                    'message': (f"📊 **Signal Change** — {row['securities_name']}: "
                                f"{row['previous_signal'] or '—'} → **{row['current_signal']}**{suffix}"),
                    'securities_id': int(row['securities_id']),
                    'type': 'signal_change',
                })
        except Exception:
            pass

        # ── bond maturity/coupon heads-up (live-computed, no per-bond setup) ───
        try:
            results.extend(_get_bond_event_alerts())
        except Exception:
            pass

        # ── dividend payment heads-up (live-computed, no per-security setup) ───
        try:
            results.extend(_get_dividend_alerts())
        except Exception:
            pass

        return results
    except Exception:
        return []


# ======================================================
# REBALANCING PLAN
# ======================================================

def get_rebalancing_plan() -> pd.DataFrame:
    """Per-security rebalancing data based on asset-type allocation targets.

    Each row is one currently-held security.  The suggested_delta_eur column
    shows how much EUR-value to buy (positive) or sell (negative) for that
    security, distributed proportionally to its weight within its asset type.
    """
    conn = get_connection()
    df = pd.read_sql("""
        WITH fx AS (
            SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
            FROM   Historical_FX ORDER BY Currencies_Id_1, Date DESC
        ),
        prices AS (
            SELECT DISTINCT ON (Securities_Id) Securities_Id, Close
            FROM   Historical_Prices ORDER BY Securities_Id, Date DESC
        ),
        holding_vals AS (
            SELECT
                s.Securities_Id,
                s.Securities_Name,
                s.Securities_Type::text                                    AS securities_type,
                s.Ticker,
                c.Currencies_ShortName                                     AS currency,
                COALESCE(p.Close, 0)                                       AS current_price,
                COALESCE(fx.FX_Rate, 1)                                    AS fx_rate,
                SUM(h.Quantity)                                            AS current_qty,
                ROUND((SUM(h.Quantity) * COALESCE(p.Close,0)
                       * COALESCE(fx.FX_Rate,1))::numeric, 2)              AS value_eur
            FROM   Holdings h
            JOIN   Securities s  ON s.Securities_Id  = h.Securities_Id
            JOIN   Currencies c  ON c.Currencies_Id  = s.Currencies_Id
            JOIN   Accounts a    ON a.Accounts_Id    = h.Accounts_Id
            LEFT JOIN prices p   ON p.Securities_Id  = h.Securities_Id
            LEFT JOIN fx         ON fx.Currencies_Id_1 = s.Currencies_Id
            WHERE  h.Quantity > 0
            GROUP  BY s.Securities_Id, s.Securities_Name, s.Securities_Type,
                      s.Ticker, c.Currencies_ShortName, p.Close, fx.FX_Rate
        ),
        grand AS (SELECT SUM(value_eur) AS total FROM holding_vals),
        type_vals AS (
            SELECT securities_type, SUM(value_eur) AS type_eur
            FROM   holding_vals GROUP BY securities_type
        )
        SELECT
            hv.Securities_Id                                               AS securities_id,
            hv.Securities_Name                                             AS securities_name,
            hv.securities_type,
            hv.Ticker                                                      AS ticker,
            hv.currency,
            hv.current_qty,
            hv.current_price,
            hv.fx_rate,
            hv.value_eur,
            ROUND((hv.value_eur / NULLIF(g.total,0)*100)::numeric,2)       AS current_pct,
            COALESCE(at.Target_Pct, 0)                                     AS type_target_pct,
            ROUND((tv.type_eur    / NULLIF(g.total,0)*100)::numeric,2)     AS type_actual_pct,
            ROUND((COALESCE(at.Target_Pct,0)
                   - tv.type_eur / NULLIF(g.total,0)*100)::numeric,2)      AS type_delta_pct,
            -- proportional share: security's EUR / type EUR × type delta EUR
            ROUND((hv.value_eur / NULLIF(tv.type_eur,0)
                   * ((COALESCE(at.Target_Pct,0) - tv.type_eur / NULLIF(g.total,0)*100)
                      / 100 * g.total))::numeric, 2)                       AS suggested_delta_eur,
            g.total                                                        AS portfolio_total_eur
        FROM   holding_vals hv
        CROSS  JOIN grand g
        JOIN   type_vals tv        ON tv.securities_type = hv.securities_type
        LEFT JOIN Allocation_Targets at ON at.Securities_Type = hv.securities_type
        ORDER  BY ABS(COALESCE(at.Target_Pct,0)
                      - tv.type_eur / NULLIF(g.total,0)*100) DESC,
                  ABS(hv.value_eur / NULLIF(tv.type_eur,0)
                      * ((COALESCE(at.Target_Pct,0)
                          - tv.type_eur / NULLIF(g.total,0)*100)
                         / 100 * g.total)) DESC
    """, conn)
    conn.close()
    return df


# ======================================================
# FULL DATA EXPORT
# ======================================================

def export_all_data() -> dict:
    """Return {sheet_name: DataFrame} for all major tables (for Excel export)."""
    conn = get_connection()
    _TABLES = {
        "Accounts":           "SELECT * FROM Accounts ORDER BY Accounts_Name",
        "Transactions":       ("SELECT * FROM Transactions "
                               "ORDER BY Date DESC LIMIT 100000"),
        "Splits":             ("SELECT * FROM Splits "
                               "ORDER BY Splits_Id DESC LIMIT 500000"),
        "Investments":        "SELECT * FROM Investments ORDER BY Date DESC",
        "Holdings":           "SELECT * FROM Holdings",
        "Securities":         "SELECT * FROM Securities ORDER BY Securities_Name",
        "Categories":         "SELECT * FROM Categories ORDER BY Categories_Name",
        "Payees":             "SELECT * FROM Payees ORDER BY Payees_Name",
        "Currencies":         "SELECT * FROM Currencies ORDER BY Currencies_ShortName",
        "Institutions":       "SELECT * FROM Institutions ORDER BY Institutions_Name",
        "Prices_2yr":         ("SELECT * FROM Historical_Prices "
                               "WHERE Date >= CURRENT_DATE - INTERVAL '2 years' "
                               "ORDER BY Securities_Id, Date DESC"),
        "FX_2yr":             ("SELECT * FROM Historical_FX "
                               "WHERE Date >= CURRENT_DATE - INTERVAL '2 years' "
                               "ORDER BY Currencies_Id_1, Date DESC"),
        "Goals":              "SELECT * FROM Goals",
        "Budgets":            "SELECT * FROM Budgets",
        "Allocation_Targets": "SELECT * FROM Allocation_Targets",
        "Recurring_Templates":"SELECT * FROM Recurring_Templates",
        "Watchlist":          "SELECT * FROM Watchlist",
        "Alerts":             "SELECT * FROM Alerts",
    }
    result = {}
    for label, sql in _TABLES.items():
        try:
            result[label] = pd.read_sql(sql, conn)
        except Exception:
            pass
    conn.close()
    return result
