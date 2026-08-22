"""Reports API endpoints: income/expense, P&L, savings rate."""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional
import math
import datetime as _dt
import pandas as pd
from dateutil.relativedelta import relativedelta
from database.connection import get_db, get_connection

router = APIRouter()


def _df_to_list(df: pd.DataFrame) -> list:
    df = df.copy()
    for col in df.select_dtypes(include=["datetime", "datetimetz"]).columns:
        df[col] = df[col].astype(str)
    records = df.where(pd.notnull(df), other=None).to_dict(orient="records")
    return [{k: None if isinstance(v, float) and math.isnan(v) else v for k, v in r.items()} for r in records]


def _fnum(v, default: float = 0.0) -> float:
    """float(v) but NaN/None-safe (unlike `float(v or default)`, which is wrong for NaN since NaN is truthy)."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return default
    return float(v)


# ── Manual account interest rate schedules (shared by Cash Flow Forecast and the
# Savings tab's own Forecast view) ──────────────────────────────────────────────

def _load_manual_rate_schedules(conn, account_types: list) -> pd.DataFrame:
    """Schedule+tier rows (one row per tier) for every account of the given type(s)
    that has at least one manually-defined interest rate schedule (Static Data ->
    Accounts -> %), joined with that account's current balance/currency/FX rate."""
    types_sql = ", ".join(f"'{t}'" for t in account_types)
    return pd.read_sql(f"""
        WITH NonEURAccounts AS (
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
        )
        SELECT
            s.Account_Interest_Rate_Schedules_Id AS schedule_id, s.Accounts_Id AS accounts_id,
            s.Effective_From AS effective_from, s.Compounding_Frequency AS compounding_frequency,
            s.Tiering_Method AS tiering_method,
            t.Tier_Min_Balance AS tier_min, t.Tier_Max_Balance AS tier_max,
            t.Effective_Annual_Yield_Pct AS eay_pct,
            a.Accounts_Name AS accounts_name, a.Accounts_Balance AS current_balance,
            c.Currencies_ShortName AS currency, COALESCE(fx.FX_Rate, 1) AS fx_rate
        FROM Account_Interest_Rate_Schedules s
        JOIN Account_Interest_Rate_Tiers t ON t.Account_Interest_Rate_Schedules_Id = s.Account_Interest_Rate_Schedules_Id
        JOIN Accounts a ON a.Accounts_Id = s.Accounts_Id
        JOIN Currencies c ON c.Currencies_Id = a.Currencies_Id
        LEFT JOIN Last_FXRates fx ON fx.Accounts_Id = a.Accounts_Id
        WHERE a.Accounts_Type IN ({types_sql})
        ORDER BY s.Accounts_Id, s.Effective_From, t.Tier_Min_Balance
    """, conn)


def _group_rate_schedules(df_rate_schedules: pd.DataFrame) -> dict:
    """{accounts_id: {'schedules': [...], 'balance', 'currency', 'fx', 'name'}} — each
    schedule dict is {'effective_from', 'frequency', 'method', 'tiers': [(min, max, eay), ...]}."""
    out: dict = {}
    if df_rate_schedules.empty:
        return out
    df = df_rate_schedules.copy()
    df['effective_from'] = pd.to_datetime(df['effective_from']).dt.date
    for accounts_id, grp in df.groupby('accounts_id'):
        first = grp.iloc[0]
        schedules = []
        for (eff_from, freq, method), sgrp in grp.groupby(['effective_from', 'compounding_frequency', 'tiering_method']):
            tiers = sorted(
                ((_fnum(r['tier_min']), (None if pd.isna(r['tier_max']) else _fnum(r['tier_max'])), _fnum(r['eay_pct']))
                 for _, r in sgrp.iterrows()),
                key=lambda x: x[0]
            )
            schedules.append({'effective_from': eff_from, 'frequency': str(freq), 'method': str(method), 'tiers': tiers})
        schedules.sort(key=lambda s: s['effective_from'])
        out[int(accounts_id)] = {
            'schedules': schedules,
            'balance': _fnum(first.get('current_balance')),
            'currency': str(first['currency']),
            'fx': _fnum(first.get('fx_rate'), default=1.0),
            'name': str(first['accounts_name']),
        }
    return out


def _schedule_for_date(schedules: list, d):
    """The schedule with the latest Effective_From <= d, or None if none is effective yet."""
    active = None
    for sch in schedules:
        if sch['effective_from'] <= d:
            active = sch
        else:
            break
    return active


_RATE_FREQ_MONTHS = {'Monthly': 1, 'Quarterly': 3, 'Semi-Annual': 6, 'Annual': 12}


def _tiered_interest(schedule: dict, balance: float, period_days: int) -> float:
    """Interest earned over period_days, applying the schedule's tiering method.
    'Marginal' (tax-bracket style): each tier's own portion of the balance earns that
    tier's own rate. 'Whole Balance': the entire balance earns whichever single tier
    it currently falls into — banks use both depending on the product."""
    if balance <= 0 or period_days <= 0:
        return 0.0
    if schedule['method'] == 'Marginal':
        total = 0.0
        for tmin, tmax, eay in schedule['tiers']:
            if balance <= tmin:
                continue
            portion = (min(balance, tmax) - tmin) if tmax is not None else (balance - tmin)
            if portion > 0 and eay > 0:
                total += portion * ((1 + eay / 100) ** (period_days / 365) - 1)
        return total
    eay = None
    for tmin, _tmax, e in schedule['tiers']:
        if balance >= tmin:
            eay = e
    if not eay or eay <= 0:
        return 0.0
    return balance * ((1 + eay / 100) ** (period_days / 365) - 1)


def _project_schedule_payments(schedules: list, balance: float, currency: str, fx: float,
                                accounts_name: str, accounts_id: int, today, cutoff,
                                real_last_date=None, real_cadence_days: Optional[int] = None) -> list:
    """Walk a manual rate schedule forward from `today` to `cutoff`, returning one row
    per projected interest payment. Anchored to the account's own real, historically-
    observed posting cadence (real_last_date/real_cadence_days) when known — e.g. an
    annually-compounding account's real anniversary date — rather than a schedule's
    Effective_From, since that's just when a rate became valid, not necessarily when
    the account actually capitalizes. Falls back to an Effective_From-anchored cycle
    (fixed to a real calendar pattern, not floating from whenever this runs) only for
    accounts with no real interest-posting history at all."""
    if balance <= 0 or not schedules:
        return []

    if real_last_date is not None and real_cadence_days and real_cadence_days > 0:
        def step(d): return d + _dt.timedelta(days=real_cadence_days)
        anchor = real_last_date
        freq_label = f'Every {real_cadence_days}d'
    else:
        freq_months = _RATE_FREQ_MONTHS.get(schedules[0]['frequency'], 1)
        def step(d): return d + relativedelta(months=freq_months)
        anchor = schedules[0]['effective_from']
        freq_label = schedules[0]['frequency']

    cycle_dt = anchor
    while step(cycle_dt) <= today:
        cycle_dt = step(cycle_dt)
    period_start, next_dt = cycle_dt, step(cycle_dt)

    rows: list = []
    running_balance = balance
    while next_dt <= cutoff:
        active = _schedule_for_date(schedules, next_dt)
        if active is None:
            # No schedule effective yet as of this date — no interest accrues before
            # its Effective_From.
            period_start = next_dt
            next_dt = step(next_dt)
            continue
        # Clamp to the active schedule's own start: period_start can predate it (a
        # handoff between vintages), and days before a rate took effect don't accrue.
        accrual_start = max(period_start, active['effective_from'])
        period_days = (next_dt - accrual_start).days
        payment = _tiered_interest(active, running_balance, period_days)
        if payment > 0:
            running_balance += payment
            rows.append({
                'date': next_dt.isoformat(),
                'payees_name': accounts_name,
                'accounts_id': int(accounts_id),
                'amount': round(payment, 2),
                'amount_eur': round(payment * fx, 2),
                'currency': currency,
                'frequency': freq_label,
            })
        period_start = next_dt
        next_dt = step(next_dt)
    return rows


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
def get_portfolio_summary(account_ids: Optional[str] = Query(None)):
    """Current holdings with value in EUR grouped by account."""
    acct_clause = _acct_clause(_parse_account_ids(account_ids), "h.Accounts_Id")
    query = f"""
    SELECT
        a.Accounts_Id AS accounts_id,
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
    WHERE h.Quantity != 0{acct_clause}
    ORDER BY value_eur DESC
    """
    with get_db() as conn:
        df = pd.read_sql(query, conn)
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
        -- Cumulative split/reverse-split ratio per security, for splits that happened
        -- strictly between a reference date and today — see the matching comment in
        -- get_pnl_period below for the full rationale. Only DTD/WTD/MTD/QTD/YTD need
        -- this (their reference date can predate a split within the same window);
        -- All-Time is untouched since it's computed from actual recorded cash flows,
        -- never from a reference-date quantity × price snapshot.
        --
        -- Collapses same-security, same-ratio records less than 10 days apart into
        -- one application before taking the product — some downloaded corporate
        -- actions (seen: MIG, Piraeus, CrediaBank) carry the same real split twice
        -- under two nearby dates (record vs. effective date, most likely), and
        -- applying the ratio once per duplicate would compound it (e.g. a real
        -- 1-for-30 reverse split wrongly applied as 1-for-900).
        security_splits AS (
            SELECT Securities_Id, ratio, MIN(Effective_Date) AS Effective_Date
            FROM (
                SELECT
                    Securities_Id, Effective_Date, ratio,
                    SUM(is_new_cluster) OVER (PARTITION BY Securities_Id, ratio ORDER BY Effective_Date) AS cluster_id
                FROM (
                    SELECT
                        Securities_Id, Effective_Date, ratio,
                        CASE WHEN LAG(Effective_Date) OVER (PARTITION BY Securities_Id, ratio ORDER BY Effective_Date) IS NULL
                               OR Effective_Date - LAG(Effective_Date) OVER (PARTITION BY Securities_Id, ratio ORDER BY Effective_Date) > 10
                             THEN 1 ELSE 0 END AS is_new_cluster
                    FROM (
                        SELECT Securities_Id, Effective_Date, Ratio_New / NULLIF(Ratio_Old, 0) AS ratio
                        FROM Corporate_Actions
                        WHERE Action_Type IN ('Split', 'Reverse Split')
                    ) base
                ) flagged
            ) clustered
            GROUP BY Securities_Id, ratio, cluster_id
        ),
        historical_holdings AS (
            SELECT
                p.*,
                he.Accounts_Id, he.Securities_Id,
                COALESCE(inv.qty_today, 0) as qty_today,
                COALESCE(inv.qty_dtd, 0) * COALESCE(mult.mult_dtd, 1) as qty_dtd,
                COALESCE(inv.qty_wtd, 0) * COALESCE(mult.mult_wtd, 1) as qty_wtd,
                COALESCE(inv.qty_mtd, 0) * COALESCE(mult.mult_mtd, 1) as qty_mtd,
                COALESCE(inv.qty_qtd, 0) * COALESCE(mult.mult_qtd, 1) as qty_qtd,
                COALESCE(inv.qty_ytd, 0) * COALESCE(mult.mult_ytd, 1) as qty_ytd
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
            LEFT JOIN LATERAL (
                SELECT
                    EXP(SUM(LN(ratio)) FILTER (WHERE Effective_Date > p.dtd_start)) AS mult_dtd,
                    EXP(SUM(LN(ratio)) FILTER (WHERE Effective_Date > p.wtd_start)) AS mult_wtd,
                    EXP(SUM(LN(ratio)) FILTER (WHERE Effective_Date > p.mtd_start)) AS mult_mtd,
                    EXP(SUM(LN(ratio)) FILTER (WHERE Effective_Date > p.qtd_start)) AS mult_qtd,
                    EXP(SUM(LN(ratio)) FILTER (WHERE Effective_Date > p.ytd_start)) AS mult_ytd
                FROM security_splits
                WHERE Securities_Id = he.Securities_Id AND Effective_Date <= p.today
            ) mult ON true
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
            -- Every cf_* window above only has a lower bound (i.Date > period_start) —
            -- without this upper bound, a future-dated transaction (e.g. a dividend
            -- pre-recorded ahead of its actual pay date) leaks into every window
            -- including All-Time, inflating "today's" P&L by an event that hasn't
            -- happened yet. Matches qty_today's own "WHERE Date <= p.today" bound above.
            WHERE i.Date <= (SELECT today FROM periods)
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
              AND i.Date <= (SELECT today FROM periods)
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
              AND i.Date <= (SELECT today FROM periods)
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
                   AND t.Date <= (SELECT today FROM periods)
            LEFT JOIN Historical_FX fxl
                   ON fxl.Currencies_Id_1 = al.Currencies_Id
                  AND fxl.Date = t.Date
            GROUP BY a.Accounts_Id
        )
        SELECT
            pf.Accounts_Id AS accounts_id,
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


@router.get("/pnl-period")
def get_pnl_period(years: int = Query(..., ge=1, le=10)):
    """Single-period P&L (since N years ago) per (account, security) — same formula as
    get_pnl's YTD window, just measured from a different reference date.

    Deliberately kept as its own endpoint rather than folded into get_pnl, which already
    computes six period variants (DTD/WTD/MTD/QTD/YTD/All-Time) in one query that's
    live-refetched on every P&L/Performance tab load — adding a 7th+ variant there would
    make that query (and its polling) proportionally heavier for everyone, all the time.
    This one only runs when a long-term period (1Y/3Y/5Y) is actually selected in the UI."""
    query = f"""
    WITH periods AS (
        SELECT (CURRENT_DATE - INTERVAL '{years} years')::date AS ref_start, CURRENT_DATE AS today
    ),
    historical_entities AS (
        SELECT Accounts_Id, Securities_Id FROM Holdings
        UNION
        SELECT Accounts_Id, Securities_Id FROM Investments
    ),
    -- Cumulative split/reverse-split ratio per security, for splits that happened
    -- strictly between a reference date and today. Historical_Prices comes back
    -- from both Yahoo and TradingView already split-adjusted — continuous across
    -- a split, priced in today's-share-equivalent terms even for dates before the
    -- split (confirmed empirically: neither provider's history endpoint exposes a
    -- raw/unadjusted series for this app to fall back to) — while Investments.
    -- Quantity stays in as-transacted, raw terms by design (a split is recorded as
    -- a forward ShrIn/ShrOut delta on the effective date, not a rescale of old
    -- lots). Multiplying a reference-date's raw quantity by this ratio converts it
    -- to the same today's-share basis the (adjusted) reference-date price is
    -- already on, so qty_ref * price_ref is a real comparable value instead of a
    -- phantom one inflated/deflated by the split ratio.
    --
    -- Collapses same-security, same-ratio records less than 10 days apart into
    -- one application before taking the product — some downloaded corporate
    -- actions (seen: MIG, Piraeus, CrediaBank) carry the same real split twice
    -- under two nearby dates (record vs. effective date, most likely), and
    -- applying the ratio once per duplicate would compound it (e.g. a real
    -- 1-for-30 reverse split wrongly applied as 1-for-900).
    security_splits AS (
        SELECT Securities_Id, ratio, MIN(Effective_Date) AS Effective_Date
        FROM (
            SELECT
                Securities_Id, Effective_Date, ratio,
                SUM(is_new_cluster) OVER (PARTITION BY Securities_Id, ratio ORDER BY Effective_Date) AS cluster_id
            FROM (
                SELECT
                    Securities_Id, Effective_Date, ratio,
                    CASE WHEN LAG(Effective_Date) OVER (PARTITION BY Securities_Id, ratio ORDER BY Effective_Date) IS NULL
                           OR Effective_Date - LAG(Effective_Date) OVER (PARTITION BY Securities_Id, ratio ORDER BY Effective_Date) > 10
                         THEN 1 ELSE 0 END AS is_new_cluster
                FROM (
                    SELECT Securities_Id, Effective_Date, Ratio_New / NULLIF(Ratio_Old, 0) AS ratio
                    FROM Corporate_Actions
                    WHERE Action_Type IN ('Split', 'Reverse Split')
                ) base
            ) flagged
        ) clustered
        GROUP BY Securities_Id, ratio, cluster_id
    ),
    historical_holdings AS (
        SELECT
            p.*, he.Accounts_Id, he.Securities_Id,
            COALESCE(inv.qty_today, 0) AS qty_today,
            COALESCE(inv.qty_ref, 0) * COALESCE(mult.mult_ref, 1) AS qty_ref
        FROM periods p
        CROSS JOIN historical_entities he
        LEFT JOIN LATERAL (
            SELECT
                SUM(CASE WHEN Action IN ('Buy','Reinvest','ShrIn') THEN Quantity
                         WHEN Action IN ('Sell','ShrOut') THEN -Quantity ELSE 0 END)
                    FILTER (WHERE Date <= p.today)     AS qty_today,
                SUM(CASE WHEN Action IN ('Buy','Reinvest','ShrIn') THEN Quantity
                         WHEN Action IN ('Sell','ShrOut') THEN -Quantity ELSE 0 END)
                    FILTER (WHERE Date <= p.ref_start) AS qty_ref
            FROM Investments
            WHERE Accounts_Id = he.Accounts_Id AND Securities_Id = he.Securities_Id
        ) inv ON true
        LEFT JOIN LATERAL (
            SELECT EXP(SUM(LN(ratio)) FILTER (WHERE Effective_Date > p.ref_start)) AS mult_ref
            FROM security_splits
            WHERE Securities_Id = he.Securities_Id AND Effective_Date <= p.today
        ) mult ON true
    ),
    prices_fx AS (
        SELECT
            hh.*,
            hp_today.Close AS price_today,
            hp_ref.Close   AS price_ref,
            fx_today.FX_Rate AS fx_today,
            fx_ref.FX_Rate   AS fx_ref,
            s.Currencies_Id AS sec_curr_id
        FROM historical_holdings hh
        JOIN Securities s ON hh.Securities_Id = s.Securities_Id
        LEFT JOIN LATERAL (
            SELECT
                MAX(Date) FILTER (WHERE Date <= hh.today)     AS d_today,
                MAX(Date) FILTER (WHERE Date <= hh.ref_start) AS d_ref
            FROM Historical_Prices WHERE Securities_Id = hh.Securities_Id
        ) pd ON true
        LEFT JOIN Historical_Prices hp_today ON hp_today.Securities_Id = hh.Securities_Id AND hp_today.Date = pd.d_today
        LEFT JOIN Historical_Prices hp_ref   ON hp_ref.Securities_Id   = hh.Securities_Id AND hp_ref.Date   = pd.d_ref
        LEFT JOIN LATERAL (
            SELECT
                MAX(Date) FILTER (WHERE Date <= hh.today)     AS d_today,
                MAX(Date) FILTER (WHERE Date <= hh.ref_start) AS d_ref
            FROM Historical_FX WHERE Currencies_Id_1 = s.Currencies_Id
        ) fxd ON true
        LEFT JOIN Historical_FX fx_today ON fx_today.Currencies_Id_1 = s.Currencies_Id AND fx_today.Date = fxd.d_today
        LEFT JOIN Historical_FX fx_ref   ON fx_ref.Currencies_Id_1   = s.Currencies_Id AND fx_ref.Date   = fxd.d_ref
    ),
    cash_flows AS (
        SELECT
            i.Accounts_Id, i.Securities_Id,
            SUM(CASE WHEN i.Date > (SELECT ref_start FROM periods) THEN
                (CASE WHEN i.Action IN ('Buy', 'MiscExp', 'ShrIn')
                        THEN COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share + COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close + COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                      WHEN i.Action IN ('Sell', 'Dividend', 'IntInc', 'Reinvest', 'RtrnCap', 'ShrOut')
                        THEN -COALESCE(NULLIF(i.Total_Amount_AccCur, 0) * COALESCE(hfx.FX_Rate, 1), NULLIF(i.Total_Amount_SecCur, 0) * COALESCE(hfx_sec.FX_Rate, 1), CASE WHEN i.Price_Per_Share > 0 THEN i.Quantity * i.Price_Per_Share - COALESCE(i.Commission, 0) END * COALESCE(hfx_sec.FX_Rate, 1), (i.Quantity * hist_price.Close - COALESCE(i.Commission, 0)) * COALESCE(hfx_sec.FX_Rate, 1))
                      ELSE 0 END)
            ELSE 0 END) AS cf_ref_eur
        FROM Investments i
        JOIN Accounts a ON i.Accounts_Id = a.Accounts_Id
        JOIN Securities s ON i.Securities_Id = s.Securities_Id
        LEFT JOIN Historical_FX hfx     ON hfx.Currencies_Id_1     = a.Currencies_Id AND hfx.Date     = i.Date
        LEFT JOIN Historical_FX hfx_sec ON hfx_sec.Currencies_Id_1 = s.Currencies_Id AND hfx_sec.Date = i.Date
        LEFT JOIN LATERAL (
            SELECT hp.Close FROM Historical_Prices hp
            WHERE hp.Securities_Id = i.Securities_Id AND hp.Date <= i.Date
            ORDER BY hp.Date DESC LIMIT 1
        ) hist_price ON i.Action = 'Reinvest'
                     AND (i.Price_Per_Share = 0 OR i.Price_Per_Share IS NULL)
                     AND (i.Total_Amount_SecCur = 0 OR i.Total_Amount_SecCur IS NULL)
        -- See the matching comment in get_pnl's cash_flows CTE — without this upper
        -- bound, a future-dated transaction leaks into "today's" P&L.
        WHERE i.Date <= (SELECT today FROM periods)
        GROUP BY i.Accounts_Id, i.Securities_Id
    )
    SELECT
        pf.Accounts_Id AS accounts_id, pf.Securities_Id AS securities_id,
        ((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)) - (pf.qty_ref * pf.price_ref * COALESCE(pf.fx_ref, 1)) - COALESCE(cf.cf_ref_eur, 0)) AS pnl_eur,
        CASE WHEN (pf.qty_ref * pf.price_ref * COALESCE(pf.fx_ref, 1)) = 0 THEN 0
             ELSE (((pf.qty_today * pf.price_today * COALESCE(pf.fx_today, 1)) - (pf.qty_ref * pf.price_ref * COALESCE(pf.fx_ref, 1)) - COALESCE(cf.cf_ref_eur, 0)) / (pf.qty_ref * pf.price_ref * COALESCE(pf.fx_ref, 1))) * 100
        END AS pnl_percent
    FROM prices_fx pf
    LEFT JOIN cash_flows cf ON pf.Accounts_Id = cf.Accounts_Id AND pf.Securities_Id = cf.Securities_Id
    WHERE (pf.qty_today != 0 OR cf.cf_ref_eur IS NOT NULL)
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
                a.Accounts_Id,
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
                     a.Accounts_Id, a.Accounts_Name, a.Currencies_Id, i.Action
        )
        SELECT
            i.Date AS date,
            i.month,
            i.Securities_Id AS securities_id,
            i.Securities_Name AS securities_name,
            i.Securities_Type AS securities_type,
            i.Accounts_Id AS accounts_id,
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

    # Inclusive month count spanned by the period (e.g. Jan-Jul = 7), matching the
    # number of bars shown in the monthly chart, not just the months that had income.
    months_in_period = max((ed.year - sd.year) * 12 + (ed.month - sd.month) + 1, 1)

    summary = {
        "total_income_eur": round(total_income, 2),
        "securities_paying": len(df_t12),
        "avg_yoc_pct": round(avg_yoc, 4) if avg_yoc is not None else None,
        "avg_monthly_income_eur": round(total_income / months_in_period, 2),
    }

    disp_cols = ["securities_id", "securities_name", "securities_type", "period_income_eur", "cost_basis_eur",
                 "yoc_pct", "fwd_yield_pct", "ex_div_date", "div_frequency"]
    detail_cols = ["date", "month", "securities_id", "securities_name", "accounts_id", "accounts_name", "action", "income_eur"]

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
def get_dividends_forecast(period: str = Query("12m", pattern="^(eoy|6m|12m)$")):
    """Projected dividend income for currently-held dividend-paying securities, over
    a selectable horizon: to end of this calendar year, the next 6 months, or the
    next 12 months (default, matches the original always-12-month behaviour)."""
    import calendar as _cal
    from datetime import date as _date, timedelta as _timedelta

    today = _date.today()

    def _add_months(d: _date, n: int) -> _date:
        m = d.month + n
        y = d.year + (m - 1) // 12
        m = (m - 1) % 12 + 1
        return d.replace(year=y, month=m, day=min(d.day, _cal.monthrange(y, m)[1]))

    if period == "eoy":
        cutoff_date = _date(today.year, 12, 31)
    elif period == "6m":
        cutoff_date = _add_months(today, 6)
    else:
        cutoff_date = _add_months(today, 12)

    _FREQ_MAP = {"monthly": 12, "quarterly": 4, "semi-annual": 2, "bi-annual": 2, "annual": 1, "yearly": 1}

    def _ppy(freq_str) -> int:
        if not freq_str or (isinstance(freq_str, float) and pd.isna(freq_str)):
            return 4
        return _FREQ_MAP.get(str(freq_str).strip().lower(), 4)

    def _next_dates(anchor, freq_str, cutoff: _date) -> list:
        ppy = _ppy(freq_str)
        interval = max(round(12 / ppy), 1)
        try:
            d = pd.Timestamp(anchor).date() if anchor and not (isinstance(anchor, float) and pd.isna(anchor)) else today
        except Exception:
            d = today
        while d <= today:
            d = _add_months(d, interval)
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
        mv   = _fnum(r.get("market_value_eur"))
        cost = _fnum(r.get("cost_basis_eur"))
        qty  = _fnum(r.get("total_qty"))
        fx   = _fnum(r.get("fx_rate"), default=1.0)
        dr   = r.get("dividend_rate")
        dy   = r.get("dividend_yield")
        t12  = _fnum(r.get("trailing_12m_income_eur"))

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
        # Always compute the *next* upcoming date over a full 13-month lookahead
        # (matches the previous always-12-month behaviour) so "next expected"
        # keeps showing a date even when it falls outside a short selected period
        # (e.g. an annual payer next paying in March isn't due before an EOY
        # cutoff in October) — the period-bounded lists below are what actually
        # drive the in-period total/table filtering.
        next_ex_dates  = _next_dates(ex_anchor,  freq, cutoff=_add_months(today, 13))
        next_pay_dates = _next_dates(pay_anchor, freq, cutoff=_add_months(today, 13))
        ex_dates   = [d for d in next_ex_dates  if d <= cutoff_date]
        pay_dates  = [d for d in next_pay_dates if d <= cutoff_date]

        rows.append({
            "securities_id":            int(r["securities_id"]),
            "securities_name":          str(r["securities_name"]),
            "securities_type":          str(r["securities_type"]),
            "total_qty":                round(qty, 4),
            "market_value_eur":         round(mv, 2),
            "cost_basis_eur":           round(cost, 2),
            "annual_forecast_eur":      round(annual, 2),
            "period_forecast_eur":      round(per_pmt * len(pay_dates), 2),
            "per_payment_eur":          round(per_pmt, 2),
            "payments_per_year":        ppy,
            "frequency":                str(freq) if freq and not (isinstance(freq, float) and pd.isna(freq)) else "Quarterly (assumed)",
            "method":                   method,
            "dividend_yield":           float(dy) if pd.notna(dy) else None,
            "next_expected_ex_date":    str(next_ex_dates[0])  if next_ex_dates  else None,
            "next_expected_pay_date":   str(next_pay_dates[0]) if next_pay_dates else None,
            "last_known_ex_date":       str(pd.Timestamp(raw_ex).date())  if pd.notna(raw_ex)  else None,
            "last_known_pay_date":      str(pd.Timestamp(raw_pay).date()) if pd.notna(raw_pay) else None,
            "pay_lag_days":             _lag if _lag else None,
            "_ex_dates":                [str(d) for d in ex_dates],
            "_pay_dates":               [str(d) for d in pay_dates],
            # Full 13-month lookahead, independent of the selected period — backs
            # "Upcoming payments (next 3 months)", which stays a fixed near-term
            # view regardless of whether the broader forecast period is EOY/6m/12m.
            "_ex_dates_full":           [str(d) for d in next_ex_dates],
            "_pay_dates_full":          [str(d) for d in next_pay_dates],
        })

    if not rows:
        return {"summary": {}, "monthly_forecast": [], "by_security": [], "upcoming": [], "period": period}

    # Portfolio Yield-on-Cost is a standard *annualized* metric — computed from
    # every forecastable holding's full run-rate/cost basis regardless of which
    # period is selected, so it doesn't swing based on how short a window you pick.
    total_annual  = sum(r["annual_forecast_eur"] for r in rows)
    total_cost    = sum(r["cost_basis_eur"] for r in rows)
    portfolio_yoc = (total_annual / total_cost * 100) if total_cost > 0 else 0

    # The headline figure and monthly chart, in contrast, are bounded to the
    # selected period — the sum of actual projected payments landing within it
    # (respecting each security's real payment dates/frequency), not a naive
    # pro-rata slice of the annual rate.
    total_period  = sum(r["period_forecast_eur"] for r in rows)
    months_in_period = max(1, round((cutoff_date - today).days / 30.44))
    total_monthly = total_period / months_in_period

    monthly_map: dict = {}
    for r in rows:
        for d in r["_pay_dates"]:
            key = str(_date.fromisoformat(d).replace(day=1))
            monthly_map[key] = round(monthly_map.get(key, 0) + r["per_payment_eur"], 2)
    monthly_forecast = sorted(
        [{"month": k, "income_eur": v} for k, v in monthly_map.items()],
        key=lambda x: x["month"]
    )

    # "Upcoming payments" is always a fixed 3-month near-term look-ahead,
    # independent of the broader EOY/6m/12m period selector — uses the full
    # 13-month date lists rather than the period-bounded ones above.
    cutoff_3m = str(_add_months(today, 3))
    upcoming: list = []
    for r in rows:
        for ex_d, pay_d in zip(r["_ex_dates_full"], r["_pay_dates_full"]):
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

    # The by-security table is scoped to the selected period: only securities
    # with at least one projected payment inside it are shown (an annual payer
    # due in March isn't relevant to an EOY forecast made in October), ranked
    # by how much of that period's total each contributes.
    by_security = sorted(
        [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows if r["_pay_dates"]],
        key=lambda x: x["period_forecast_eur"], reverse=True
    )

    return {
        "period": period,
        "summary": {
            "total_period_eur":  round(total_period, 2),
            "total_annual_eur":  round(total_annual, 2),
            "total_monthly_eur": round(total_monthly, 2),
            "securities_count":  len(by_security),
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
    """All buy/sell investment transactions with EUR amounts for FIFO/LIFO lot matching.

    Excludes future-dated rows (i.Date <= CURRENT_DATE) — a Sell captured ahead of
    its actual trade date shouldn't show up as an already-realized gain, and letting
    it into the lot walk would also skew the cost basis carried into later, genuinely-
    realized sales. Same principle as update_holdings() in database/crud.py and P&L's
    own cash-flow windows (get_pnl/get_pnl_period)."""
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
              AND i.Date <= CURRENT_DATE
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
                'accounts_id':     int(row['accounts_id']),
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

    cols = ['securities_id','security','ticker','accounts_id','account','date','action','quantity',
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
                'accounts_id':     int(row['accounts_id']),
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

    cols = ['securities_id','security','ticker','accounts_id','account','date','action','quantity',
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
    account_ids: Optional[str] = Query(None),
):
    """
    Full cash-flow forecast replicating the Streamlit view, plus recurring templates:
    - Explicitly scheduled future transactions (Date > today, within horizon)
    - Active Recurring Templates, projected forward from their own next_due_date/periodicity
    - Recurring patterns detected from last N complete months, projected forward
      (payees already covered by a scheduled transaction OR an active template are excluded,
      to avoid the same bill being counted twice)
    account_ids (comma-separated), when given, scopes every source to just those accounts —
    same Account Preset mechanism as Net Worth/Inv. Portfolio/Inv. Performance.
    Returns: { scheduled, templates, recurring, metrics }
    """
    import datetime as _dt
    from dateutil.relativedelta import relativedelta
    from database.queries import _ensure_account_interest_rate_schema

    today = _dt.date.today()
    cutoff = today + _dt.timedelta(days=days)
    _PERIOD_STEP = {
        'Daily': relativedelta(days=1), 'Weekly': relativedelta(weeks=1),
        'Bi-Weekly': relativedelta(weeks=2), 'Monthly': relativedelta(months=1),
        'Bi-Monthly': relativedelta(months=2), 'Quarterly': relativedelta(months=3),
        'Semi-Annual': relativedelta(months=6), 'Annual': relativedelta(years=1),
    }
    # Upper bound raised from 6 to 12 to accommodate the frontend's YTD option (complete
    # calendar months elapsed this year), which can be as high as 11 in December.
    mb = max(2, min(12, int(months_back)))
    acct_ids = _parse_account_ids(account_ids)
    acct_clause_a = _acct_clause(acct_ids, 'a.Accounts_Id')

    with get_db() as conn:
        df_future = pd.read_sql(f"""
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
                a.Accounts_Id,
                a.Accounts_Name,
                a.Accounts_Type,
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
              {acct_clause_a}
            ORDER BY t.Date ASC
        """, conn)

        df_templates = pd.read_sql(f"""
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
                a.Accounts_Id AS accounts_id,
                a.Accounts_Name AS accounts_name,
                a.Accounts_Type AS accounts_type,
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
              {acct_clause_a}
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
                  {acct_clause_a}
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

        df_div = pd.read_sql(f"""
            WITH fx_latest AS (
                SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
                FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC
            ),
            price_latest AS (
                SELECT DISTINCT ON (Securities_Id) Securities_Id, Close
                FROM Historical_Prices ORDER BY Securities_Id, Date DESC
            ),
            holdings_agg AS (
                SELECT h.Securities_Id, SUM(h.Quantity) AS total_qty
                FROM Holdings h
                JOIN Accounts a ON a.Accounts_Id = h.Accounts_Id
                WHERE h.Quantity > 0
                  {acct_clause_a}
                GROUP BY h.Securities_Id
            ),
            last_div AS (
                SELECT DISTINCT ON (Securities_Id) Securities_Id, Ex_Date AS last_ex_date
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
                  {acct_clause_a}
                GROUP BY i.Securities_Id
            )
            SELECT s.Securities_Id AS securities_id, s.Securities_Name AS securities_name,
                ha.total_qty,
                ROUND((ha.total_qty * COALESCE(pl.Close, 0) * COALESCE(fx2.FX_Rate, 1))::numeric, 2) AS market_value_eur,
                COALESCE(fx2.FX_Rate, 1) AS fx_rate,
                s.Dividend_Yield AS dividend_yield, s.Dividend_Rate AS dividend_rate,
                s.Ex_Dividend_Date AS ex_dividend_date, s.Dividend_Pay_Date AS dividend_pay_date,
                s.Dividend_Frequency AS dividend_frequency,
                ld.last_ex_date,
                COALESCE(ti.trailing_12m_income_eur, 0) AS trailing_12m_income_eur
            FROM Securities s
            JOIN holdings_agg ha ON ha.Securities_Id = s.Securities_Id
            LEFT JOIN price_latest pl  ON pl.Securities_Id  = s.Securities_Id
            LEFT JOIN fx_latest    fx2 ON fx2.Currencies_Id_1 = s.Currencies_Id
            LEFT JOIN last_div     ld  ON ld.Securities_Id  = s.Securities_Id
            LEFT JOIN trailing_income ti ON ti.Securities_Id = s.Securities_Id
            WHERE (s.Dividend_Yield IS NOT NULL OR s.Dividend_Rate IS NOT NULL OR ti.trailing_12m_income_eur > 0)
        """, conn)

        df_savings = _savings_last_period_df(conn)
        if acct_ids and not df_savings.empty:
            df_savings = df_savings[df_savings['accounts_id'].astype(int).isin(acct_ids)].copy()

        _ensure_account_interest_rate_schema()
        df_rate_schedules = _load_manual_rate_schedules(conn, ['Savings', 'Checking'])
        if acct_ids and not df_rate_schedules.empty:
            df_rate_schedules = df_rate_schedules[df_rate_schedules['accounts_id'].astype(int).isin(acct_ids)].copy()

        df_bonds = pd.read_sql(f"""
            WITH fx AS (
                SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate
                FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC
            )
            SELECT h.Securities_Id AS securities_id, s.Securities_Name AS securities_name,
                   h.Accounts_Id AS accounts_id, a.Accounts_Name AS accounts_name,
                   a.Accounts_Type AS accounts_type, h.Quantity AS quantity,
                   s.Maturity_Date AS maturity_date, s.Coupon_Rate AS coupon_rate,
                   s.Face_Value AS face_value, s.Coupon_Frequency AS coupon_frequency,
                   c.Currencies_ShortName AS currency, COALESCE(fx.FX_Rate, 1) AS fx_rate
            FROM Holdings h
            JOIN Securities s ON h.Securities_Id = s.Securities_Id
            JOIN Accounts a ON h.Accounts_Id = a.Accounts_Id
            JOIN Currencies c ON s.Currencies_Id = c.Currencies_Id
            LEFT JOIN fx ON fx.Currencies_Id_1 = s.Currencies_Id
            WHERE h.Quantity > 0 AND s.Securities_Type = 'Bond' AND s.Maturity_Date IS NOT NULL
              {acct_clause_a}
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
                    'accounts_id': int(row['accounts_id']),
                    'accounts_name': str(row['accounts_name'] or ''),
                    'accounts_type': str(row['accounts_type'] or ''),
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

    # Project dividend income for currently-held dividend-paying securities, using
    # the same annual-run-rate / payments-per-year logic as the Dividend Tracker's
    # Forecast view (Dividend Rate > Fwd Yield > Trailing 12m actual income),
    # bounded by this endpoint's own day-based horizon.
    _DIV_FREQ_MAP = {"monthly": 12, "quarterly": 4, "semi-annual": 2, "bi-annual": 2, "annual": 1, "yearly": 1}

    def _div_ppy(freq_str) -> int:
        if not freq_str or (isinstance(freq_str, float) and pd.isna(freq_str)):
            return 4
        return _DIV_FREQ_MAP.get(str(freq_str).strip().lower(), 4)

    dividend_rows = []
    for _, r in df_div.iterrows():
        mv  = _fnum(r.get("market_value_eur"))
        qty = _fnum(r.get("total_qty"))
        fx  = _fnum(r.get("fx_rate"), default=1.0)
        dr  = r.get("dividend_rate")
        dy  = r.get("dividend_yield")
        t12 = _fnum(r.get("trailing_12m_income_eur"))

        if pd.notna(dr) and float(dr) > 0 and qty > 0:
            annual = float(dr) * qty * fx
        elif pd.notna(dy) and float(dy) > 0 and mv > 0:
            annual = mv * float(dy) / 100
        elif t12 > 0:
            annual = t12
        else:
            continue
        if annual <= 0:
            continue

        freq = r.get("dividend_frequency")
        ppy = _div_ppy(freq)
        interval = relativedelta(months=max(round(12 / ppy), 1))
        per_pmt = annual / ppy

        raw_ex  = r.get("ex_dividend_date")
        raw_pay = r.get("dividend_pay_date")
        ex_anchor = raw_ex if pd.notna(raw_ex) else r.get("last_ex_date")

        # Same stale-pay-date guard as the Dividend Tracker forecast: only trust
        # Dividend_Pay_Date as its own anchor when it's a plausible lag after the
        # ex-date, otherwise derive the pay anchor from the (reliable) ex-date.
        _lag = None
        if pd.notna(raw_ex) and pd.notna(raw_pay):
            candidate_lag = (pd.Timestamp(raw_pay).date() - pd.Timestamp(raw_ex).date()).days
            if 0 <= candidate_lag <= 90:
                _lag = candidate_lag
        pay_anchor = (
            (pd.Timestamp(ex_anchor).date() + _dt.timedelta(days=_lag))
            if (_lag is not None and pd.notna(ex_anchor)) else ex_anchor
        )

        try:
            d = pd.Timestamp(pay_anchor).date() if pay_anchor and not (isinstance(pay_anchor, float) and pd.isna(pay_anchor)) else today
        except Exception:
            d = today
        while d <= today:
            d = d + interval
        while d <= cutoff:
            dividend_rows.append({
                'date': d.isoformat(),
                'payees_name': str(r['securities_name']),
                'securities_id': int(r['securities_id']),
                'amount_eur': round(per_pmt, 2),
                'currency': 'EUR',
                'frequency': str(freq) if freq and not (isinstance(freq, float) and pd.isna(freq)) else 'Quarterly (assumed)',
            })
            d = d + interval

    dividend_rows.sort(key=lambda x: x['date'])

    # Project interest income. Accounts with a user-defined rate schedule (Static Data
    # → Accounts → Interest Rates) use that — a balance-tiered %, dated by
    # Effective_From, compounded at the schedule's own frequency. All other Savings
    # accounts fall back to the last-real-interest-period APY%/cadence compounding
    # logic used by the Savings tab's own Forecast view (/savings-forecast). Either
    # way, compounding happens in the account's own currency; only the resulting
    # payments are converted to EUR here, for a consistent cash-flow total.
    interest_rows = []

    schedules_by_account = _group_rate_schedules(df_rate_schedules)
    manual_account_ids = set(schedules_by_account.keys())

    # Real, historically-observed posting cadence per account (Savings only — Checking
    # accounts never have one), used to anchor a manual schedule's projection to the
    # account's actual capitalization dates rather than its Effective_From.
    real_cadence: dict = {}
    if not df_savings.empty:
        for _, r in df_savings.iterrows():
            hd, ld = r.get('holding_days_last'), r.get('last_interest_date')
            if pd.notna(hd) and pd.notna(ld) and int(hd) > 0:
                real_cadence[int(r['accounts_id'])] = (pd.Timestamp(ld).date(), int(hd))

    for accounts_id, info in schedules_by_account.items():
        real_last, real_cad = real_cadence.get(accounts_id, (None, None))
        interest_rows.extend(_project_schedule_payments(
            info['schedules'], info['balance'], info['currency'], info['fx'],
            info['name'], accounts_id, today, cutoff,
            real_last_date=real_last, real_cadence_days=real_cad,
        ))

    if not df_savings.empty:
        for _, r in df_savings.iterrows():
            if int(r['accounts_id']) in manual_account_ids:
                continue

            balance = _fnum(r.get("current_balance"))
            apy = _fnum(r.get("apy_pct_last"))
            hd = r.get("holding_days_last")
            cadence_days = int(hd) if pd.notna(hd) else 0
            last_date = r.get("last_interest_date")
            fx = _fnum(r.get("fx_rate"), default=1.0)

            if balance <= 0 or apy <= 0 or cadence_days <= 0 or pd.isna(last_date):
                continue

            next_dt = pd.Timestamp(last_date).date()
            while next_dt <= today:
                next_dt += _dt.timedelta(days=cadence_days)

            running_balance = balance
            while next_dt <= cutoff:
                payment = running_balance * ((1 + apy / 100) ** (cadence_days / 365) - 1)
                running_balance += payment
                interest_rows.append({
                    'date': next_dt.isoformat(),
                    'payees_name': str(r['accounts_name']),
                    'accounts_id': int(r['accounts_id']),
                    'amount_eur': round(payment * fx, 2),
                    'currency': str(r['currency']),
                    'frequency': f'Every {cadence_days}d',
                })
                next_dt += _dt.timedelta(days=cadence_days)

    interest_rows.sort(key=lambda x: x['date'])

    # Project bond coupon payments and maturity (face value) redemptions for
    # currently-held bonds, same fields/conventions as the Bond Schedule tab
    # (/xray-style — see get_bond_schedule): 'At Maturity' frequency means the
    # whole return is embedded in the discount purchase price rather than paid
    # as a separate coupon (e.g. Hellenic T-Bills), so only the face-value
    # redemption is projected for those, not a periodic coupon on top of it.
    # Coupon dates are anchored to the bond's own Maturity_Date, stepping back
    # by one coupon period at a time — there's no stored issue/settlement date
    # to anchor forward from instead.
    _COUPON_STEP = {
        'Monthly':     (relativedelta(months=1), 12),
        'Quarterly':   (relativedelta(months=3), 4),
        'Semi-Annual': (relativedelta(months=6), 2),
    }
    _DEFAULT_COUPON_STEP = (relativedelta(years=1), 1)  # Annual, or any other non-"At Maturity" value

    bond_rows = []
    for _, r in df_bonds.iterrows():
        qty = _fnum(r.get('quantity'))
        face = _fnum(r.get('face_value'))
        raw_maturity = r.get('maturity_date')
        if qty <= 0 or face <= 0 or pd.isna(raw_maturity):
            continue
        maturity_d = pd.Timestamp(raw_maturity).date()
        if maturity_d <= today or maturity_d > cutoff:
            continue

        coupon_rate = _fnum(r.get('coupon_rate'))
        freq = str(r.get('coupon_frequency') or 'Annual')
        fx = _fnum(r.get('fx_rate'), default=1.0)
        total_face_native = qty * face
        currency = str(r.get('currency') or 'EUR')
        name = str(r.get('securities_name') or '')
        sec_id = int(r['securities_id'])
        acct_id = int(r['accounts_id'])
        acct_name = str(r.get('accounts_name') or '')
        acct_type = str(r.get('accounts_type') or '')

        if freq != 'At Maturity' and coupon_rate > 0:
            step, periods_per_year = _COUPON_STEP.get(freq, _DEFAULT_COUPON_STEP)
            coupon_native = total_face_native * coupon_rate / 100 / periods_per_year
            d = maturity_d
            while d > today:
                d -= step
            d += step
            while d <= cutoff:
                if d > today and d < maturity_d:  # the maturity-date coupon is folded into the redemption row below
                    bond_rows.append({
                        'date': d.isoformat(), 'kind': 'Coupon',
                        'payees_name': name, 'securities_id': sec_id,
                        'accounts_id': acct_id, 'accounts_name': acct_name, 'accounts_type': acct_type,
                        'amount_eur': round(coupon_native * fx, 2),
                        'currency': currency, 'frequency': freq,
                    })
                d += step
            final_coupon_native = coupon_native
        else:
            final_coupon_native = 0.0

        bond_rows.append({
            'date': maturity_d.isoformat(), 'kind': 'Maturity',
            'payees_name': name, 'securities_id': sec_id,
            'accounts_id': acct_id, 'accounts_name': acct_name, 'accounts_type': acct_type,
            'amount_eur': round((total_face_native + final_coupon_native) * fx, 2),
            'currency': currency, 'frequency': freq,
        })

    bond_rows.sort(key=lambda x: x['date'])

    # Build scheduled list
    scheduled = []
    if not df_f.empty:
        for _, row in df_f.iterrows():
            scheduled.append({
                'date': str(row['date'])[:10],
                'payees_name': str(row['payees_name'] or ''),
                'accounts_id': int(row['accounts_id']),
                'accounts_name': str(row['accounts_name'] or ''),
                'accounts_type': str(row['accounts_type'] or ''),
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
    div_in    = sum(r['amount_eur'] for r in dividend_rows)
    int_in    = sum(r['amount_eur'] for r in interest_rows)
    bond_in   = sum(r['amount_eur'] for r in bond_rows)

    return {
        'scheduled': scheduled,
        'templates': template_rows,
        'recurring': recur_rows,
        'dividends': dividend_rows,
        'interest': interest_rows,
        'bonds': bond_rows,
        'metrics': {
            'sched_in':  round(sched_in,  2),
            'sched_out': round(sched_out, 2),
            'tmpl_in':   round(tmpl_in,   2),
            'tmpl_out':  round(tmpl_out,  2),
            'recur_in':  round(recur_in,  2),
            'recur_out': round(recur_out, 2),
            'div_in':    round(div_in,    2),
            'int_in':    round(int_in,    2),
            'bond_in':   round(bond_in,   2),
            'net_total': round(sched_in + sched_out + tmpl_in + tmpl_out + recur_in + recur_out + div_in + int_in + bond_in, 2),
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
    -- Just the "natural" bucket-end dates (unchanged from before the Start Date baseline
    -- feature) — used below to tell a genuine display bucket apart from period_dates' extra
    -- start_date-only row.
    natural_periods AS (
        SELECT (gs - INTERVAL '1 day')::date AS period_end
        FROM generate_series(
            date_trunc('{trunc_unit}', '{start_date}'::date) + '{pg_interval}'::interval,
            date_trunc('{trunc_unit}', {eff_end}),
            '{pg_interval}'::interval
        ) gs
        UNION SELECT {eff_end}
    ),
    period_dates AS (
        -- Also includes the exact start_date as its own period (not just bucket-end dates), so
        -- the earliest period is the same real anchor point regardless of grouping — without
        -- this, Year grouping's first bucket lands ~1 year after start_date while Month's lands
        -- only ~1 month after it, making the "change since start_date" KPI silently measure
        -- from a different date per grouping. A plain single-column UNION so that when
        -- start_date already falls exactly on a bucket-end date (e.g. 2020-12-31 with Year
        -- grouping), it collapses back to one row rather than two identical-period_end rows —
        -- two rows here would make every downstream `(SELECT fx_rate FROM daily_fx WHERE
        -- period_end=... )` scalar subquery return more than one row and error out.
        SELECT period_end FROM natural_periods
        UNION SELECT '{start_date}'::date ORDER BY 1
    ),
    daily_fx AS (
        SELECT p.period_end, cur.Currencies_Id,
            (SELECT FX_Rate FROM Historical_FX WHERE Currencies_Id_1=cur.Currencies_Id AND Date<=p.period_end ORDER BY Date DESC LIMIT 1) AS fx_rate
        FROM period_dates p CROSS JOIN Currencies cur WHERE cur.Currencies_ShortName != 'EUR'
    ),
    cash_bal AS (
        SELECT p.period_end, a.Accounts_Id, a.Accounts_Name, a.Accounts_Type, a.Is_Active,
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
        SELECT p.period_end, a.Accounts_Id, a.Accounts_Name, a.Accounts_Type, a.Is_Active,
            SUM(GREATEST(COALESCE((
                SELECT SUM(CASE WHEN Action IN ('Buy','Reinvest','ShrIn') THEN Quantity WHEN Action IN ('Sell','ShrOut') THEN -Quantity ELSE 0 END)
                FROM Investments i2 WHERE i2.Securities_Id=iu.Securities_Id AND i2.Accounts_Id=iu.Accounts_Id AND i2.Date<=p.period_end
            ),0),0) * COALESCE((SELECT Close FROM Historical_Prices WHERE Securities_Id=iu.Securities_Id AND Date<=p.period_end ORDER BY Date DESC LIMIT 1),0)
            * COALESCE((SELECT fx_rate FROM daily_fx WHERE period_end=p.period_end AND Currencies_Id=s.Currencies_Id),1)) AS balance_eur
        FROM period_dates p CROSS JOIN inv_universe iu
        JOIN Accounts a ON iu.Accounts_Id=a.Accounts_Id
        JOIN Securities s ON iu.Securities_Id=s.Securities_Id
        WHERE a.Accounts_Type IN ('Brokerage','Margin','Pension','Other Investment')
        GROUP BY p.period_end, a.Accounts_Id, a.Accounts_Name, a.Accounts_Type, a.Is_Active
    ),
    pension_bal AS (
        SELECT p.period_end, a.Accounts_Id, a.Accounts_Name, a.Accounts_Type, a.Is_Active,
            GREATEST(0, a.Accounts_Balance - COALESCE((
                SELECT SUM(CASE WHEN Action IN ('CashIn','IntInc') THEN Total_Amount_AccCur WHEN Action='CashOut' THEN -Total_Amount_AccCur ELSE 0 END)
                FROM Investments WHERE Accounts_Id=a.Accounts_Id AND Date>p.period_end
            ),0)) * COALESCE((SELECT fx_rate FROM daily_fx WHERE period_end=p.period_end AND Currencies_Id=a.Currencies_Id),1) AS balance_eur
        FROM period_dates p CROSS JOIN Accounts a
        WHERE a.Accounts_Type IN ('Pension','Other Investment')
          AND a.Accounts_Id NOT IN (SELECT Accounts_Id FROM inv_accounts)
    )
    SELECT period_end::text AS period,
           (period_end = '{start_date}'::date) AS is_baseline,
           EXISTS(SELECT 1 FROM natural_periods np WHERE np.period_end = combined.period_end) AS is_display_period,
           accounts_id, accounts_name, accounts_type, is_active,
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
def get_investment_positions_history(start_date: str = Query("2020-01-01"), account_ids: Optional[str] = Query(None)):
    acct_clause = _acct_clause(_parse_account_ids(account_ids), "Accounts_Id")
    query = f"""
    WITH RECURSIVE months AS (
        SELECT (date_trunc('month', %(start_date)s::date) + INTERVAL '1 month' - INTERVAL '1 day')::date AS d
        UNION ALL
        SELECT (date_trunc('month', d + INTERVAL '1 month') + INTERVAL '1 month' - INTERVAL '1 day')::date
        FROM months WHERE d < date_trunc('month', CURRENT_DATE)
    ),
    dates AS (SELECT d FROM months WHERE d <= CURRENT_DATE UNION SELECT CURRENT_DATE::date),
    inv_universe AS (SELECT DISTINCT Securities_Id, Accounts_Id FROM Investments WHERE Action IN ('Buy','Reinvest','ShrIn','Sell','ShrOut'){acct_clause}),
    qty_at AS (
        SELECT dt.d AS date_pt, iu.Securities_Id, iu.Accounts_Id,
            GREATEST(COALESCE((
                SELECT SUM(CASE WHEN Action IN ('Buy','Reinvest','ShrIn') THEN Quantity WHEN Action IN ('Sell','ShrOut') THEN -Quantity ELSE 0 END)
                FROM Investments WHERE Securities_Id=iu.Securities_Id AND Accounts_Id=iu.Accounts_Id AND Date<=dt.d
            ),0),0) AS qty_at_date
        FROM dates dt CROSS JOIN inv_universe iu
    )
    SELECT qa.date_pt::text AS date, a.Accounts_Id AS accounts_id, a.Accounts_Name AS accounts_name,
        SUM(qa.qty_at_date
            * COALESCE((SELECT Close FROM Historical_Prices WHERE Securities_Id=qa.Securities_Id AND Date<=qa.date_pt ORDER BY Date DESC LIMIT 1),0)
            * COALESCE((SELECT FX_Rate FROM Historical_FX WHERE Currencies_Id_1=s.Currencies_Id AND Date<=qa.date_pt ORDER BY Date DESC LIMIT 1),1)
        ) AS value_eur
    FROM qty_at qa
    JOIN Accounts a ON qa.Accounts_Id=a.Accounts_Id
    JOIN Securities s ON qa.Securities_Id=s.Securities_Id
    WHERE qa.qty_at_date > 0
    GROUP BY qa.date_pt, a.Accounts_Id, a.Accounts_Name
    ORDER BY qa.date_pt ASC, a.Accounts_Name ASC
    """
    with get_db() as conn:
        df = pd.read_sql(query, conn, params={"start_date": start_date})
    return _df_to_list(df)


@router.get("/holdings-snapshot")
def get_holdings_snapshot(as_of: str = Query(None), account_ids: Optional[str] = Query(None)):
    """Per-security holdings snapshot at a given date (default: today)."""
    as_of_date = as_of or "CURRENT_DATE"
    acct_clause = _acct_clause(_parse_account_ids(account_ids), "Accounts_Id")
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
        FROM Investments WHERE Action IN ('Buy','Reinvest','ShrIn','Sell','ShrOut'){acct_clause}
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
        a.Accounts_Id                                              AS accounts_id,
        a.Accounts_Name                                            AS account,
        a.Accounts_Type                                            AS account_type,
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


# ── FX Exposure ───────────────────────────────────────────────────────────────
@router.get("/fx-exposure")
def get_fx_exposure(account_ids: Optional[str] = Query(None)):
    # A selected preset becomes the complete account universe for both buckets —
    # cash exposure isn't just "everything non-investment" anymore once a specific
    # set of accounts has been chosen, same as the investment side.
    acct_ids = _parse_account_ids(account_ids)
    cash_clause = _acct_clause(acct_ids, "a.Accounts_Id")
    inv_clause = _acct_clause(acct_ids, "h.Accounts_Id")
    query = f"""
    WITH fx AS (SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC),
    prices AS (SELECT DISTINCT ON (Securities_Id) Securities_Id, Close FROM Historical_Prices ORDER BY Securities_Id, Date DESC),
    cash_exp AS (
        SELECT a.Currencies_Id, SUM(a.Accounts_Balance) AS native_exposure
        FROM Accounts a WHERE a.Is_Active=TRUE AND a.Accounts_Type NOT IN ('Brokerage','Margin'){cash_clause} GROUP BY a.Currencies_Id
    ),
    inv_exp AS (
        SELECT s.Currencies_Id, SUM(h.Quantity * COALESCE(p.Close,0)) AS native_exposure
        FROM Holdings h JOIN Securities s ON h.Securities_Id=s.Securities_Id
        LEFT JOIN prices p ON p.Securities_Id=h.Securities_Id
        WHERE h.Quantity > 0{inv_clause} GROUP BY s.Currencies_Id
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


# ── Portfolio X-Ray ──────────────────────────────────────────────────────────
# Look-through ETF/Mutual Fund composition (cached in Fund_Composition /
# Fund_Top_Holdings by data/downloaders.py::download_fund_composition, sourced
# from yfinance's get_funds_data()) blended with direct stock/bond holdings,
# so allocation/sector/overlap views reflect what's actually inside the funds
# rather than treating each fund as an opaque single bucket. Every blended
# view adds an 'Uncovered Fund Exposure' bucket for held funds with no/partial
# Fund_Composition row, so totals still reconcile to ~100% of portfolio value.

@router.get("/xray/sector-weighting")
def get_xray_sector_weighting(account_ids: Optional[str] = Query(None), compare_date: Optional[str] = Query(None)):
    """Sector summary, plus a per-security detail breakdown (which security
    contributed how much/what % to each sector, and which industry within that
    sector for direct holdings — Yahoo's fund sector weightings have no
    industry-level breakdown, so fund-attributed rows carry a NULL industry)
    for the UI's click-to-drill-down: sector -> industry -> securities.
    compare_date, when given, additionally computes the same breakdown as of that
    past date (point-in-time holdings/prices — see _pit_ctes) under "compare"/
    "compare_date". Sector weightings themselves always reflect today's fund
    data — Oikos has no historical version of a fund's own internal makeup."""
    acct_clause = _acct_clause(_parse_account_ids(account_ids), "h.Accounts_Id")

    def _run(as_of: Optional[str]):
        fx_cte, prices_cte, h_src_cte = _pit_ctes(as_of)
        pit_params = {"as_of": as_of} if as_of else None
        holdings_cte = f"""
    WITH {fx_cte},
    {prices_cte},
    {h_src_cte},
    holdings_value AS (
        SELECT h.Securities_Id, s.Securities_Type::text AS sec_type,
               COALESCE(
                   NULLIF(TRIM(s.Sector),''),
                   CASE s.Securities_Type::text WHEN 'Crypto' THEN 'Crypto' WHEN 'Commodity' THEN 'Commodities' END,
                   'Other / Unknown'
               ) AS sector,
               SUM(h.Quantity * COALESCE(p.Close,0) * COALESCE(fx.FX_Rate,1)) AS value_eur
        FROM h_src h JOIN Securities s ON h.Securities_Id=s.Securities_Id
        LEFT JOIN prices p ON p.Securities_Id=h.Securities_Id
        LEFT JOIN fx ON fx.Currencies_Id_1=s.Currencies_Id
        WHERE h.Quantity > 0{acct_clause}
        GROUP BY h.Securities_Id, s.Securities_Type, sector
    )
    """
        detail_cte = """
    , direct_detail AS (
        SELECT hv.sector,
               COALESCE(NULLIF(TRIM(s.Industry),''), 'Other / Unknown') AS industry,
               hv.Securities_Id AS securities_id, s.Securities_Name AS name, s.Ticker AS ticker, hv.value_eur
        FROM holdings_value hv JOIN Securities s ON s.Securities_Id = hv.Securities_Id
        WHERE hv.sec_type NOT IN ('ETF','Mutual Fund')
    ),
    fund_detail_split AS (
        -- Yahoo's own sector-weighting keys are underscore_separated except
        -- 'realestate' (no underscore) — special-cased so it reads "Real Estate"
        -- like the GICS sector name direct holdings already use, instead of
        -- "Realestate" landing as a separate, near-duplicate bucket. Yahoo's
        -- fund sector weightings have no industry-level breakdown, so these
        -- rows carry a NULL industry — the UI groups them into their own
        -- "Fund Look-Through" bucket rather than dropping them silently.
        SELECT CASE WHEN je.key = 'realestate' THEN 'Real Estate' ELSE INITCAP(REPLACE(je.key,'_',' ')) END AS sector,
               NULL::text AS industry,
               hv.Securities_Id AS securities_id, s.Securities_Name AS name, s.Ticker AS ticker,
               hv.value_eur * je.value::numeric AS value_eur
        FROM holdings_value hv
        JOIN Securities s ON s.Securities_Id = hv.Securities_Id
        JOIN Fund_Composition fc ON fc.Securities_Id = hv.Securities_Id
        CROSS JOIN LATERAL jsonb_each_text(fc.Sector_Weightings) AS je(key, value)
        WHERE hv.sec_type IN ('ETF','Mutual Fund') AND fc.Sector_Weightings IS NOT NULL
    ),
    fund_detail_override AS (
        -- Yahoo has no sector weightings for this fund, but the user already
        -- manually classified it via the Asset Class Override (e.g. a physical
        -- commodity ETC) — reuse that instead of leaving it "Uncovered".
        SELECT fc.Asset_Class_Override AS sector, NULL::text AS industry, hv.Securities_Id AS securities_id,
               s.Securities_Name AS name, s.Ticker AS ticker, hv.value_eur
        FROM holdings_value hv
        JOIN Securities s ON s.Securities_Id = hv.Securities_Id
        JOIN Fund_Composition fc ON fc.Securities_Id = hv.Securities_Id
        WHERE hv.sec_type IN ('ETF','Mutual Fund') AND fc.Sector_Weightings IS NULL AND fc.Asset_Class_Override IS NOT NULL
    ),
    fund_detail_bond_fallback AS (
        -- Yahoo doesn't provide GICS-style sector weightings for bond funds at
        -- all (sector weighting is an equity-fund concept) — a bond-dominant
        -- fund isn't really "uncovered", it just has no sector breakdown to
        -- give. Bucketed separately from Bond Quality's own credit-rating
        -- split (Fund_Composition.Bond_Ratings), which is a different, more
        -- reliable signal than trying to infer a single sector from it.
        SELECT 'Bonds (No Sector Data)' AS sector, NULL::text AS industry, hv.Securities_Id AS securities_id,
               s.Securities_Name AS name, s.Ticker AS ticker, hv.value_eur
        FROM holdings_value hv
        JOIN Securities s ON s.Securities_Id = hv.Securities_Id
        JOIN Fund_Composition fc ON fc.Securities_Id = hv.Securities_Id
        WHERE hv.sec_type IN ('ETF','Mutual Fund') AND fc.Sector_Weightings IS NULL AND fc.Asset_Class_Override IS NULL
          AND COALESCE(fc.Asset_Bond_Pct,0) >= 0.7
    ),
    fund_detail AS (
        SELECT * FROM fund_detail_split
        UNION ALL SELECT * FROM fund_detail_override
        UNION ALL SELECT * FROM fund_detail_bond_fallback
    ),
    uncovered_detail AS (
        SELECT 'Uncovered Fund Exposure' AS sector, NULL::text AS industry, hv.Securities_Id AS securities_id, s.Securities_Name AS name, s.Ticker AS ticker, hv.value_eur
        FROM holdings_value hv JOIN Securities s ON s.Securities_Id = hv.Securities_Id
        WHERE hv.sec_type IN ('ETF','Mutual Fund')
          AND NOT EXISTS (
              SELECT 1 FROM Fund_Composition fc WHERE fc.Securities_Id=hv.Securities_Id
                AND (fc.Sector_Weightings IS NOT NULL OR fc.Asset_Class_Override IS NOT NULL OR COALESCE(fc.Asset_Bond_Pct,0) >= 0.7)
          )
    ),
    detail_combined AS (
        SELECT * FROM direct_detail
        UNION ALL SELECT * FROM fund_detail
        UNION ALL SELECT * FROM uncovered_detail
    )
    """
        summary_query = holdings_cte + detail_cte + """
    , totals AS (SELECT SUM(value_eur) AS grand_total FROM detail_combined)
    SELECT sector, ROUND(SUM(value_eur)::numeric,2) AS value_eur,
           ROUND((SUM(value_eur)/NULLIF((SELECT grand_total FROM totals),0)*100)::numeric,2) AS pct
    FROM detail_combined GROUP BY sector ORDER BY value_eur DESC
    """
        detail_query = holdings_cte + detail_cte + """
    , sector_totals AS (SELECT sector, SUM(value_eur) AS sector_total FROM detail_combined GROUP BY sector)
    SELECT dc.sector, dc.industry, dc.securities_id, dc.name, dc.ticker,
           ROUND(SUM(dc.value_eur)::numeric,2) AS value_eur,
           ROUND((SUM(dc.value_eur)/NULLIF(st.sector_total,0)*100)::numeric,2) AS pct
    FROM detail_combined dc JOIN sector_totals st ON st.sector = dc.sector
    GROUP BY dc.sector, dc.industry, dc.securities_id, dc.name, dc.ticker, st.sector_total
    ORDER BY dc.sector, value_eur DESC
    """
        with get_db() as conn:
            summary_df = pd.read_sql(summary_query, conn, params=pit_params)
            detail_df = pd.read_sql(detail_query, conn, params=pit_params)
        return {"summary": _df_to_list(summary_df), "detail": _df_to_list(detail_df)}

    result = _run(None)
    if compare_date:
        result["compare"] = _run(compare_date)
        result["compare_date"] = compare_date
    return result


@router.get("/xray/asset-allocation")
def get_xray_asset_allocation(account_ids: Optional[str] = Query(None), compare_date: Optional[str] = Query(None)):
    """Asset-class summary, plus a per-security detail breakdown (which security
    contributed how much/what % to each class) for the UI's click-to-drill-down.
    Cash/Bank accounts (Cash, Checking, Savings, Credit Card) in the preset also
    contribute their balance to the 'Cash' bucket — Holdings alone would
    otherwise never surface real cash, only a fund's own cash sleeve.
    compare_date, when given, additionally computes the same breakdown as of that
    past date (point-in-time holdings/prices/cash balances — see _pit_ctes/
    _pit_cash_balance_expr) and returns it under "compare"/"compare_date". Fund
    composition (the split within fund_detail_split below) always reflects today's
    data, since Oikos has no historical version of a fund's own internal makeup."""
    parsed_acct_ids = _parse_account_ids(account_ids)
    acct_clause = _acct_clause(parsed_acct_ids, "h.Accounts_Id")
    cash_clause = _acct_clause(parsed_acct_ids, "a.Accounts_Id")

    def _run(as_of: Optional[str]):
        fx_cte, prices_cte, h_src_cte = _pit_ctes(as_of)
        cash_balance_expr = _pit_cash_balance_expr(as_of)
        pit_params = {"as_of": as_of} if as_of else None
        holdings_cte = f"""
        WITH {fx_cte},
        {prices_cte},
        {h_src_cte},
        holdings_value AS (
            SELECT h.Securities_Id, s.Securities_Type::text AS sec_type,
                   SUM(h.Quantity * COALESCE(p.Close,0) * COALESCE(fx.FX_Rate,1)) AS value_eur
            FROM h_src h JOIN Securities s ON h.Securities_Id=s.Securities_Id
            LEFT JOIN prices p ON p.Securities_Id=h.Securities_Id
            LEFT JOIN fx ON fx.Currencies_Id_1=s.Currencies_Id
            WHERE h.Quantity > 0{acct_clause}
            GROUP BY h.Securities_Id, s.Securities_Type
        )
        """
        detail_cte = f"""
        , direct_cash AS (
            SELECT 'Cash' AS asset_class, NULL::integer AS securities_id, a.Accounts_Name AS name, NULL::text AS ticker,
                   {cash_balance_expr} * COALESCE(fx.FX_Rate,1) AS value_eur
            FROM Accounts a
            LEFT JOIN fx ON fx.Currencies_Id_1 = a.Currencies_Id
            WHERE a.Accounts_Type NOT IN ('Brokerage','Pension','Other Investment','Margin','Real Estate','Vehicle','Asset','Liability')
              AND a.Is_Active = TRUE AND a.Accounts_Balance != 0{cash_clause}
        ),
    direct_detail AS (
        SELECT CASE
                 WHEN hv.sec_type = 'Stock' THEN 'Stocks'
                 WHEN hv.sec_type = 'Bond' THEN 'Bonds'
                 WHEN hv.sec_type = 'CD' THEN 'Cash'
                 WHEN hv.sec_type = 'Crypto' THEN 'Crypto'
                 WHEN hv.sec_type = 'Commodity' THEN 'Commodities'
                 ELSE 'Other'
               END AS asset_class,
               hv.Securities_Id AS securities_id, s.Securities_Name AS name, s.Ticker AS ticker, hv.value_eur
        FROM holdings_value hv JOIN Securities s ON s.Securities_Id = hv.Securities_Id
        WHERE hv.sec_type NOT IN ('ETF','Mutual Fund')
    ),
    fund_detail_override AS (
        -- Manual override (e.g. a physical commodity ETC Yahoo lumps entirely
        -- into its generic "other" bucket) — routes the fund's whole value to
        -- one class instead of splitting across Yahoo's 6 asset buckets.
        SELECT fc.Asset_Class_Override AS asset_class, hv.Securities_Id AS securities_id,
               s.Securities_Name AS name, s.Ticker AS ticker, hv.value_eur
        FROM holdings_value hv
        JOIN Securities s ON s.Securities_Id = hv.Securities_Id
        JOIN Fund_Composition fc ON fc.Securities_Id = hv.Securities_Id
        WHERE hv.sec_type IN ('ETF','Mutual Fund') AND fc.Asset_Class_Override IS NOT NULL
    ),
    fund_detail_split AS (
        SELECT b.asset_class, hv.Securities_Id AS securities_id, s.Securities_Name AS name, s.Ticker AS ticker,
               hv.value_eur * b.pct AS value_eur
        FROM holdings_value hv
        JOIN Securities s ON s.Securities_Id = hv.Securities_Id
        JOIN Fund_Composition fc ON fc.Securities_Id = hv.Securities_Id
        CROSS JOIN LATERAL (VALUES
            ('Stocks',      fc.Asset_Stock_Pct),
            ('Bonds',       fc.Asset_Bond_Pct),
            ('Cash',        fc.Asset_Cash_Pct),
            ('Preferred',   fc.Asset_Preferred_Pct),
            ('Convertible', fc.Asset_Convertible_Pct),
            ('Other',       fc.Asset_Other_Pct)
        ) AS b(asset_class, pct)
        WHERE hv.sec_type IN ('ETF','Mutual Fund') AND fc.Asset_Class_Override IS NULL
          AND b.pct IS NOT NULL AND b.pct > 0
    ),
    fund_detail AS (
        SELECT * FROM fund_detail_override
        UNION ALL SELECT * FROM fund_detail_split
    ),
    uncovered_detail AS (
        SELECT 'Uncovered Fund Exposure' AS asset_class,
               hv.Securities_Id AS securities_id, s.Securities_Name AS name, s.Ticker AS ticker, hv.value_eur
        FROM holdings_value hv JOIN Securities s ON s.Securities_Id = hv.Securities_Id
        WHERE hv.sec_type IN ('ETF','Mutual Fund')
          AND NOT EXISTS (
              SELECT 1 FROM Fund_Composition fc WHERE fc.Securities_Id=hv.Securities_Id
                AND fc.Asset_Class_Override IS NOT NULL
          )
          AND NOT EXISTS (
              SELECT 1 FROM Fund_Composition fc WHERE fc.Securities_Id=hv.Securities_Id
                AND (fc.Asset_Stock_Pct IS NOT NULL OR fc.Asset_Bond_Pct IS NOT NULL OR fc.Asset_Cash_Pct IS NOT NULL)
          )
    ),
    detail_combined AS (
        SELECT * FROM direct_cash
        UNION ALL SELECT * FROM direct_detail
        UNION ALL SELECT * FROM fund_detail
        UNION ALL SELECT * FROM uncovered_detail
    )
    """
        summary_query = holdings_cte + detail_cte + """
    , totals AS (SELECT SUM(value_eur) AS grand_total FROM detail_combined)
    , by_class AS (SELECT asset_class, SUM(value_eur) AS value_eur FROM detail_combined GROUP BY asset_class)
    SELECT bc.asset_class, ROUND(bc.value_eur::numeric,2) AS value_eur,
           ROUND((bc.value_eur/NULLIF(t.grand_total,0)*100)::numeric,2) AS pct,
           COALESCE(xat.Target_Pct, 0) AS target_pct,
           ROUND((bc.value_eur/NULLIF(t.grand_total,0)*100 - COALESCE(xat.Target_Pct,0))::numeric,2) AS delta_pct,
           ROUND(((COALESCE(xat.Target_Pct,0) - bc.value_eur/NULLIF(t.grand_total,0)*100)/100*t.grand_total)::numeric,2) AS rebalance_eur
    FROM by_class bc
    CROSS JOIN totals t
    LEFT JOIN XRay_Allocation_Targets xat ON xat.Asset_Class = bc.asset_class
    ORDER BY bc.value_eur DESC
    """
        detail_query = holdings_cte + detail_cte + """
    , class_totals AS (SELECT asset_class, SUM(value_eur) AS class_total FROM detail_combined GROUP BY asset_class)
    SELECT dc.asset_class, dc.securities_id, dc.name, dc.ticker,
           ROUND(SUM(dc.value_eur)::numeric,2) AS value_eur,
           ROUND((SUM(dc.value_eur)/NULLIF(ct.class_total,0)*100)::numeric,2) AS pct
    FROM detail_combined dc JOIN class_totals ct ON ct.asset_class = dc.asset_class
    GROUP BY dc.asset_class, dc.securities_id, dc.name, dc.ticker, ct.class_total
    ORDER BY dc.asset_class, value_eur DESC
    """
        with get_db() as conn:
            summary_df = pd.read_sql(summary_query, conn, params=pit_params)
            detail_df = pd.read_sql(detail_query, conn, params=pit_params)
        return {"summary": _df_to_list(summary_df), "detail": _df_to_list(detail_df)}

    result = _run(None)
    if compare_date:
        result["compare"] = _run(compare_date)
        result["compare_date"] = compare_date
    return result


@router.get("/xray/asset-allocation-targets")
def get_xray_asset_allocation_targets():
    """Saved target percentages for X-Ray Asset Allocation's look-through classes."""
    with get_db() as conn:
        df = pd.read_sql(
            "SELECT Asset_Class AS asset_class, Target_Pct AS target_pct FROM XRay_Allocation_Targets ORDER BY Asset_Class",
            conn,
        )
    return _df_to_list(df)


@router.post("/xray/asset-allocation-targets")
def save_xray_asset_allocation_targets(payload: dict):
    """Upsert {asset_class: target_pct} map into XRay_Allocation_Targets."""
    with get_db() as conn:
        cur = conn.cursor()
        for asset_class, pct in payload.items():
            cur.execute(
                """INSERT INTO XRay_Allocation_Targets (Asset_Class, Target_Pct)
                   VALUES (%s, %s)
                   ON CONFLICT (Asset_Class)
                   DO UPDATE SET Target_Pct = EXCLUDED.Target_Pct""",
                (asset_class, float(pct)),
            )
    return {"ok": True}


@router.get("/xray/style-box")
def get_xray_style_box(account_ids: Optional[str] = Query(None), compare_date: Optional[str] = Query(None)):
    """Style-box summary, plus a per-security detail breakdown for the UI's
    click-to-drill-down. A fund with no Morningstar category (and no manual
    override) is bucketed by its own dominant asset mix (e.g. 'Equity Fund
    (Uncategorized)') instead of a single opaque 'N/A' bucket, using the same
    Fund_Composition asset-class data the Asset Allocation X-Ray already has;
    only funds with no asset-mix data at all fall to 'N/A (no data)'. Cash &
    Savings accounts in the preset also contribute to a 'Cash' bucket, same
    account-type exclusion list the Asset Allocation X-Ray's cash bucket uses.
    compare_date, when given, additionally computes the same breakdown as of that
    past date (point-in-time holdings/prices/cash balances — see _pit_ctes/
    _pit_cash_balance_expr) under "compare"/"compare_date". Category/asset-mix
    data itself always reflects today's fund data (no historical version exists)."""
    parsed_acct_ids = _parse_account_ids(account_ids)
    acct_clause = _acct_clause(parsed_acct_ids, "h.Accounts_Id")
    cash_clause = _acct_clause(parsed_acct_ids, "a.Accounts_Id")

    def _run(as_of: Optional[str]):
        fx_cte, prices_cte, h_src_cte = _pit_ctes(as_of)
        cash_balance_expr = _pit_cash_balance_expr(as_of)
        pit_params = {"as_of": as_of} if as_of else None
        holdings_cte = f"""
    WITH {fx_cte},
    {prices_cte},
    {h_src_cte},
    holdings_value AS (
        SELECT h.Securities_Id, s.Securities_Type::text AS sec_type,
               SUM(h.Quantity * COALESCE(p.Close,0) * COALESCE(fx.FX_Rate,1)) AS value_eur
        FROM h_src h JOIN Securities s ON h.Securities_Id=s.Securities_Id
        LEFT JOIN prices p ON p.Securities_Id=h.Securities_Id
        LEFT JOIN fx ON fx.Currencies_Id_1=s.Currencies_Id
        WHERE h.Quantity > 0{acct_clause}
        GROUP BY h.Securities_Id, s.Securities_Type
    )
    """
        detail_cte = f"""
    , direct_cash AS (
        SELECT 'Cash' AS style, NULL::integer AS securities_id, a.Accounts_Name AS name, NULL::text AS ticker,
               {cash_balance_expr} * COALESCE(fx.FX_Rate,1) AS value_eur
        FROM Accounts a
        LEFT JOIN fx ON fx.Currencies_Id_1 = a.Currencies_Id
        WHERE a.Accounts_Type NOT IN ('Brokerage','Pension','Other Investment','Margin','Real Estate','Vehicle','Asset','Liability')
          AND a.Is_Active = TRUE AND a.Accounts_Balance != 0{cash_clause}
    ),
    direct_detail AS (
        SELECT CASE
                 WHEN hv.sec_type = 'Stock' THEN 'Direct Stocks'
                 WHEN hv.sec_type = 'Bond' THEN 'Direct Bonds'
                 ELSE 'Direct Other'
               END AS style,
               hv.Securities_Id AS securities_id, s.Securities_Name AS name, s.Ticker AS ticker, hv.value_eur
        FROM holdings_value hv JOIN Securities s ON s.Securities_Id = hv.Securities_Id
        WHERE hv.sec_type NOT IN ('ETF','Mutual Fund')
    ),
    fund_detail AS (
        SELECT COALESCE(
                 fc.Category_Override, fc.Category_Name,
                 CASE
                   WHEN GREATEST(COALESCE(fc.Asset_Stock_Pct,0), COALESCE(fc.Asset_Bond_Pct,0), COALESCE(fc.Asset_Cash_Pct,0),
                                 COALESCE(fc.Asset_Preferred_Pct,0), COALESCE(fc.Asset_Convertible_Pct,0), COALESCE(fc.Asset_Other_Pct,0)) = 0
                        AND fc.Asset_Class_Override IS NULL THEN 'N/A (no data)'
                   WHEN fc.Asset_Class_Override IS NOT NULL THEN fc.Asset_Class_Override || ' Fund (Uncategorized)'
                   WHEN COALESCE(fc.Asset_Stock_Pct,0) >= 0.7 THEN 'Equity Fund (Uncategorized)'
                   WHEN COALESCE(fc.Asset_Bond_Pct,0) >= 0.7 THEN 'Bond Fund (Uncategorized)'
                   WHEN COALESCE(fc.Asset_Cash_Pct,0) >= 0.7 THEN 'Cash Fund (Uncategorized)'
                   WHEN COALESCE(fc.Asset_Stock_Pct,0) > 0 AND COALESCE(fc.Asset_Bond_Pct,0) > 0 THEN 'Allocation Fund (Uncategorized)'
                   ELSE 'Other Fund (Uncategorized)'
                 END
               ) AS style,
               hv.Securities_Id AS securities_id, s.Securities_Name AS name, s.Ticker AS ticker, hv.value_eur
        FROM holdings_value hv
        JOIN Securities s ON s.Securities_Id = hv.Securities_Id
        LEFT JOIN Fund_Composition fc ON fc.Securities_Id = hv.Securities_Id
        WHERE hv.sec_type IN ('ETF','Mutual Fund')
    ),
    detail_combined AS (
        SELECT * FROM direct_cash
        UNION ALL SELECT * FROM direct_detail
        UNION ALL SELECT * FROM fund_detail
    )
    """
        summary_query = holdings_cte + detail_cte + """
    , totals AS (SELECT SUM(value_eur) AS grand_total FROM detail_combined)
    SELECT style, ROUND(SUM(value_eur)::numeric,2) AS value_eur,
           ROUND((SUM(value_eur)/NULLIF((SELECT grand_total FROM totals),0)*100)::numeric,2) AS pct
    FROM detail_combined GROUP BY style ORDER BY value_eur DESC
    """
        detail_query = holdings_cte + detail_cte + """
    , style_totals AS (SELECT style, SUM(value_eur) AS style_total FROM detail_combined GROUP BY style)
    SELECT dc.style, dc.securities_id, dc.name, dc.ticker,
           ROUND(SUM(dc.value_eur)::numeric,2) AS value_eur,
           ROUND((SUM(dc.value_eur)/NULLIF(st.style_total,0)*100)::numeric,2) AS pct
    FROM detail_combined dc JOIN style_totals st ON st.style = dc.style
    GROUP BY dc.style, dc.securities_id, dc.name, dc.ticker, st.style_total
    ORDER BY dc.style, value_eur DESC
    """
        with get_db() as conn:
            summary_df = pd.read_sql(summary_query, conn, params=pit_params)
            detail_df = pd.read_sql(detail_query, conn, params=pit_params)
        return {"summary": _df_to_list(summary_df), "detail": _df_to_list(detail_df)}

    result = _run(None)
    if compare_date:
        result["compare"] = _run(compare_date)
        result["compare_date"] = compare_date
    return result


@router.get("/xray/bond-quality")
def get_xray_bond_quality(account_ids: Optional[str] = Query(None), compare_date: Optional[str] = Query(None)):
    """Credit-quality + duration blend, plus a per-security detail breakdown for the
    UI's click-to-drill-down. Direct-bond duration is a years-to-maturity
    approximation (Maturity_Date - the as-of date, or today), not modified
    duration like the fund side. compare_date, when given, additionally computes
    the same breakdown as of that past date (point-in-time holdings/prices — see
    _pit_ctes) under "compare"/"compare_date". Credit-rating/duration data itself
    always reflects today's fund data — Oikos has no historical version of it."""
    acct_clause = _acct_clause(_parse_account_ids(account_ids), "h.Accounts_Id")

    def _run(as_of: Optional[str]):
        fx_cte, prices_cte, h_src_cte = _pit_ctes(as_of)
        as_of_expr = "%(as_of)s::date" if as_of else "CURRENT_DATE"
        pit_params = {"as_of": as_of} if as_of else None
        holdings_cte = f"""
    WITH {fx_cte},
    {prices_cte},
    {h_src_cte},
    holdings_value AS (
        SELECT h.Securities_Id, s.Securities_Type::text AS sec_type, s.Maturity_Date, s.Issuer_Id,
               s.Securities_Name AS name, s.Ticker AS ticker,
               SUM(h.Quantity * COALESCE(p.Close,0) * COALESCE(fx.FX_Rate,1)) AS value_eur
        FROM h_src h JOIN Securities s ON h.Securities_Id=s.Securities_Id
        LEFT JOIN prices p ON p.Securities_Id=h.Securities_Id
        LEFT JOIN fx ON fx.Currencies_Id_1=s.Currencies_Id
        WHERE h.Quantity > 0{acct_clause}
        GROUP BY h.Securities_Id, s.Securities_Type, s.Maturity_Date, s.Issuer_Id, s.Securities_Name, s.Ticker
    )
    """
        detail_cte = f"""
    , fund_bond AS (
        -- Normalizes Yahoo's lowercase snake_case rating keys (aaa, bbb, us_government, ...)
        -- onto the same labels direct_bond hardcodes below, so e.g. a fund's 'bbb' bucket
        -- merges with a direct bond's 'BBB' instead of showing up as a separate 'Bbb' row.
        SELECT CASE UPPER(je.key)
                 WHEN 'AAA' THEN 'AAA'
                 WHEN 'AA' THEN 'AA'
                 WHEN 'A' THEN 'A'
                 WHEN 'BBB' THEN 'BBB'
                 WHEN 'BB' THEN 'BB'
                 WHEN 'B' THEN 'B'
                 WHEN 'BELOW_B' THEN 'Below B'
                 WHEN 'US_GOVERNMENT' THEN 'Us Government'
                 WHEN 'OTHER' THEN 'Other'
                 ELSE INITCAP(REPLACE(je.key,'_',' '))
               END AS quality,
               hv.Securities_Id AS securities_id, hv.name, hv.ticker,
               hv.value_eur * je.value::numeric AS value_eur,
               fc.Bond_Duration AS duration_years
        FROM holdings_value hv
        JOIN Fund_Composition fc ON fc.Securities_Id = hv.Securities_Id
        CROSS JOIN LATERAL jsonb_each_text(fc.Bond_Ratings) AS je(key, value)
        WHERE hv.sec_type IN ('ETF','Mutual Fund') AND fc.Bond_Ratings IS NOT NULL
    ),
    direct_bond AS (
        -- Maps a linked Issuer's Moody's notch straight to a quality bucket;
        -- a bond with no issuer, or an issuer with no rating set, falls
        -- through to 'Direct / Unrated' (the prior always-on default).
        SELECT CASE
                 WHEN iss.Moodys = 'Aaa' THEN 'AAA'
                 WHEN iss.Moodys IN ('Aa1','Aa2','Aa3') THEN 'AA'
                 WHEN iss.Moodys IN ('A1','A2','A3') THEN 'A'
                 WHEN iss.Moodys IN ('Baa1','Baa2','Baa3') THEN 'BBB'
                 WHEN iss.Moodys IN ('Ba1','Ba2','Ba3') THEN 'BB'
                 WHEN iss.Moodys IN ('B1','B2','B3') THEN 'B'
                 WHEN iss.Moodys IN ('Caa1','Caa2','Caa3','Ca','C') THEN 'Below B'
                 ELSE 'Direct / Unrated'
               END AS quality,
               hv.Securities_Id AS securities_id, hv.name, hv.ticker, hv.value_eur,
               CASE WHEN hv.Maturity_Date IS NOT NULL
                    THEN EXTRACT(EPOCH FROM (hv.Maturity_Date::timestamp - {as_of_expr}::timestamp)) / (365.25*86400)
               END AS duration_years
        FROM holdings_value hv
        LEFT JOIN Issuers iss ON iss.Issuers_Id = hv.Issuer_Id
        WHERE hv.sec_type = 'Bond'
    ),
    uncovered AS (
        -- Only funds that actually hold bonds but lack a ratings breakdown belong here.
        -- Excludes funds we already know hold no bonds at all (e.g. commodity ETCs with
        -- Asset_Class_Override set, or Asset_Bond_Pct=0) so they don't inflate this bucket.
        SELECT 'Uncovered Fund Bond Exposure' AS quality,
               hv.Securities_Id AS securities_id, hv.name, hv.ticker, hv.value_eur,
               NULL::numeric AS duration_years
        FROM holdings_value hv
        LEFT JOIN Fund_Composition fc ON fc.Securities_Id = hv.Securities_Id
        WHERE hv.sec_type IN ('ETF','Mutual Fund')
          AND fc.Bond_Ratings IS NULL
          AND (fc.Asset_Class_Override IS NULL OR fc.Asset_Class_Override = 'Bonds')
          AND COALESCE(fc.Asset_Bond_Pct, 1) > 0
    ),
    detail_combined AS (
        SELECT * FROM fund_bond
        UNION ALL SELECT * FROM direct_bond
        UNION ALL SELECT * FROM uncovered
    )
    """
        # "Us Government" isn't a rating rung alongside AAA/AA/A/BBB/... — it's Yahoo's
        # own issuer-type flag (government vs. corporate) living in the same flat
        # bond_ratings dict as the credit-rating buckets, and it doesn't sum to 100%
        # against them (e.g. a fund reporting a=59%, bbb=32%, aaa=9%, us_government=100%
        # all at once). Summing it into the same total as the rating buckets double-
        # counts exposure, so it's excluded from the rated total/percentages here and
        # surfaced separately as an informational stat instead.
        def _quality_rank(col: str) -> str:
            return f"""
            CASE {col}
                WHEN 'AAA' THEN 1 WHEN 'AA' THEN 2 WHEN 'A' THEN 3 WHEN 'BBB' THEN 4
                WHEN 'BB' THEN 5 WHEN 'B' THEN 6 WHEN 'Below B' THEN 7 WHEN 'Other' THEN 8
                WHEN 'Direct / Unrated' THEN 9 WHEN 'Uncovered Fund Bond Exposure' THEN 10
                ELSE 11
            END
        """
        summary_query = holdings_cte + detail_cte + f"""
    , rated AS (SELECT * FROM detail_combined WHERE quality != 'Us Government')
    , totals AS (SELECT SUM(value_eur) AS grand_total FROM rated)
    SELECT quality, ROUND(SUM(value_eur)::numeric,2) AS value_eur,
           ROUND((SUM(value_eur)/NULLIF((SELECT grand_total FROM totals),0)*100)::numeric,2) AS pct,
           ROUND(AVG(duration_years)::numeric,2) AS avg_duration_years
    FROM rated GROUP BY quality ORDER BY {_quality_rank('quality')}
    """
        us_gov_query = holdings_cte + detail_cte + """
    , rated AS (SELECT * FROM detail_combined WHERE quality != 'Us Government')
    , totals AS (SELECT SUM(value_eur) AS grand_total FROM rated)
    SELECT ROUND(SUM(value_eur)::numeric,2) AS value_eur,
           ROUND((SUM(value_eur)/NULLIF((SELECT grand_total FROM totals),0)*100)::numeric,2) AS pct,
           ROUND(AVG(duration_years)::numeric,2) AS avg_duration_years
    FROM detail_combined WHERE quality = 'Us Government'
    """
        detail_query = holdings_cte + detail_cte + f"""
    , quality_totals AS (SELECT quality, SUM(value_eur) AS quality_total FROM detail_combined GROUP BY quality)
    SELECT dc.quality, dc.securities_id, dc.name, dc.ticker,
           ROUND(SUM(dc.value_eur)::numeric,2) AS value_eur,
           ROUND((SUM(dc.value_eur)/NULLIF(qt.quality_total,0)*100)::numeric,2) AS pct,
           ROUND(AVG(dc.duration_years)::numeric,2) AS duration_years
    FROM detail_combined dc JOIN quality_totals qt ON qt.quality = dc.quality
    GROUP BY dc.quality, dc.securities_id, dc.name, dc.ticker, qt.quality_total
    ORDER BY dc.quality = 'Us Government', {_quality_rank('dc.quality')}, value_eur DESC
    """
        with get_db() as conn:
            summary_df = pd.read_sql(summary_query, conn, params=pit_params)
            us_gov_df = pd.read_sql(us_gov_query, conn, params=pit_params)
            detail_df = pd.read_sql(detail_query, conn, params=pit_params)
        us_gov_row = _df_to_list(us_gov_df)
        us_gov = us_gov_row[0] if us_gov_row and us_gov_row[0].get("value_eur") is not None else None
        return {"summary": _df_to_list(summary_df), "detail": _df_to_list(detail_df), "us_government": us_gov}

    result = _run(None)
    if compare_date:
        result["compare"] = _run(compare_date)
        result["compare_date"] = compare_date
    return result


@router.get("/xray/stock-overlap")
def get_xray_stock_overlap(account_ids: Optional[str] = Query(None), compare_date: Optional[str] = Query(None)):
    """Ungrouped rows — one per direct-stock holding, one per fund constituent (top-10
    only). Frontend groups by symbol to compute true cross-portfolio concentration.
    compare_date, when given, additionally computes the same rows as of that past
    date (point-in-time holdings/prices — see _pit_ctes) under "compare"/
    "compare_date". Fund top-holdings/weights themselves always reflect today's
    fund data — Oikos has no historical version of a fund's own constituents."""
    acct_clause = _acct_clause(_parse_account_ids(account_ids), "h.Accounts_Id")

    def _run(as_of: Optional[str]):
        fx_cte, prices_cte, h_src_cte = _pit_ctes(as_of)
        pit_params = {"as_of": as_of} if as_of else None
        query = f"""
    WITH {fx_cte},
    {prices_cte},
    {h_src_cte},
    holdings_value AS (
        SELECT h.Securities_Id, s.Securities_Type::text AS sec_type, s.Securities_Name, s.Yahoo_Ticker,
               SUM(h.Quantity * COALESCE(p.Close,0) * COALESCE(fx.FX_Rate,1)) AS value_eur
        FROM h_src h JOIN Securities s ON s.Securities_Id=h.Securities_Id
        LEFT JOIN prices p ON p.Securities_Id=h.Securities_Id
        LEFT JOIN fx ON fx.Currencies_Id_1=s.Currencies_Id
        WHERE h.Quantity>0{acct_clause}
        GROUP BY h.Securities_Id, s.Securities_Type, s.Securities_Name, s.Yahoo_Ticker
    ),
    totals AS (SELECT SUM(value_eur) AS grand_total FROM holdings_value),
    direct_stock AS (
        SELECT Yahoo_Ticker AS symbol, Securities_Name AS name, 'Direct' AS source_type,
               NULL::text AS source_label, value_eur, Securities_Id AS securities_id
        FROM holdings_value
        WHERE sec_type NOT IN ('ETF','Mutual Fund') AND Yahoo_Ticker IS NOT NULL AND Yahoo_Ticker <> ''
    ),
    fund_constituents AS (
        -- A fund's top-10 constituent is "registered" if its symbol matches an existing
        -- Securities row (e.g. you also hold it directly, or previously imported it from
        -- here) — lets the frontend offer a one-click "Import from Yahoo" only where a
        -- security genuinely doesn't exist yet, instead of guessing from Yahoo_Ticker text.
        SELECT fth.Symbol AS symbol, fth.Holding_Name AS name, 'Fund' AS source_type,
               s.Securities_Name AS source_label, hv.value_eur * fth.Weight_Pct AS value_eur,
               sec2.Securities_Id AS securities_id
        FROM Fund_Top_Holdings fth
        JOIN holdings_value hv ON hv.Securities_Id = fth.Securities_Id
        JOIN Securities s ON s.Securities_Id = fth.Securities_Id
        LEFT JOIN Securities sec2 ON sec2.Yahoo_Ticker = fth.Symbol
        WHERE hv.sec_type IN ('ETF','Mutual Fund')
    )
    SELECT symbol, name, source_type, source_label, ROUND(value_eur::numeric,2) AS value_eur, securities_id,
           (SELECT grand_total FROM totals) AS total_portfolio_eur
    FROM direct_stock
    UNION ALL
    SELECT symbol, name, source_type, source_label, ROUND(value_eur::numeric,2) AS value_eur, securities_id,
           (SELECT grand_total FROM totals) AS total_portfolio_eur
    FROM fund_constituents
    ORDER BY value_eur DESC
    """
        with get_db() as conn:
            df = pd.read_sql(query, conn, params=pit_params)
        return _df_to_list(df)

    result = {"rows": _run(None)}
    if compare_date:
        result["compare_rows"] = _run(compare_date)
        result["compare_date"] = compare_date
    return result


@router.get("/xray/expense-ratio")
def get_xray_expense_ratio(account_ids: Optional[str] = Query(None), compare_date: Optional[str] = Query(None)):
    """Weighted-average expense ratio across held funds, plus coverage_pct (fund
    €-value with a known expense ratio ÷ total fund €-value) so the UI can show
    e.g. '0.12% (covers 94% of fund holdings by value)' rather than implying completeness.
    Also returns a per-fund breakdown (value, % of fund holdings, own expense ratio).
    compare_date, when given, additionally computes the same breakdown as of that
    past date (point-in-time holdings/prices — see _pit_ctes) under "compare"/
    "compare_date". Each fund's own expense ratio always reflects today's data —
    Oikos has no historical version of a fund's own expense ratio."""
    acct_clause = _acct_clause(_parse_account_ids(account_ids), "h.Accounts_Id")

    def _run(as_of: Optional[str]):
        fx_cte, prices_cte, h_src_cte = _pit_ctes(as_of)
        pit_params = {"as_of": as_of} if as_of else None
        holdings_cte = f"""
    WITH {fx_cte},
    {prices_cte},
    {h_src_cte},
    holdings_value AS (
        SELECT h.Securities_Id, s.Securities_Type::text AS sec_type,
               SUM(h.Quantity * COALESCE(p.Close,0) * COALESCE(fx.FX_Rate,1)) AS value_eur
        FROM h_src h JOIN Securities s ON h.Securities_Id=s.Securities_Id
        LEFT JOIN prices p ON p.Securities_Id=h.Securities_Id
        LEFT JOIN fx ON fx.Currencies_Id_1=s.Currencies_Id
        WHERE h.Quantity > 0{acct_clause} AND s.Securities_Type IN ('ETF','Mutual Fund')
        GROUP BY h.Securities_Id, s.Securities_Type
    )
    """
        summary_query = holdings_cte + """
    SELECT
        ROUND((SUM(CASE WHEN fc.Expense_Ratio_Pct IS NOT NULL THEN hv.value_eur * fc.Expense_Ratio_Pct ELSE 0 END)
               / NULLIF(SUM(CASE WHEN fc.Expense_Ratio_Pct IS NOT NULL THEN hv.value_eur ELSE 0 END),0) * 100)::numeric,4) AS weighted_expense_ratio_pct,
        ROUND((SUM(CASE WHEN fc.Expense_Ratio_Pct IS NOT NULL THEN hv.value_eur ELSE 0 END)
               / NULLIF(SUM(hv.value_eur),0) * 100)::numeric,2) AS coverage_pct,
        ROUND(SUM(hv.value_eur)::numeric,2) AS total_fund_value_eur
    FROM holdings_value hv
    LEFT JOIN Fund_Composition fc ON fc.Securities_Id = hv.Securities_Id
    """
        funds_query = holdings_cte + """
    , totals AS (SELECT SUM(value_eur) AS grand_total FROM holdings_value)
    SELECT s.Securities_Id AS securities_id, s.Securities_Name AS name, s.Ticker AS ticker,
           ROUND(hv.value_eur::numeric,2) AS value_eur,
           ROUND((hv.value_eur / NULLIF((SELECT grand_total FROM totals),0) * 100)::numeric,2) AS pct,
           ROUND((fc.Expense_Ratio_Pct * 100)::numeric,4) AS expense_ratio_pct
    FROM holdings_value hv
    JOIN Securities s ON s.Securities_Id = hv.Securities_Id
    LEFT JOIN Fund_Composition fc ON fc.Securities_Id = hv.Securities_Id
    ORDER BY hv.value_eur DESC
    """
        with get_db() as conn:
            summary_df = pd.read_sql(summary_query, conn, params=pit_params)
            funds_df = pd.read_sql(funds_query, conn, params=pit_params)
        summary = _df_to_list(summary_df)
        return {"summary": summary[0] if summary else None, "funds": _df_to_list(funds_df)}

    result = _run(None)
    if compare_date:
        result["compare"] = _run(compare_date)
        result["compare_date"] = compare_date
    return result


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
            amt = _fnum(row["amount_eur"])
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
                "amount_eur": _fnum(row["amount_eur"]),
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
        a.Accounts_Id                       AS accounts_id,
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
        a.Accounts_Id                   AS accounts_id,
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
    holdings_agg AS (SELECT Securities_Id, SUM(Quantity) AS qty FROM Holdings GROUP BY Securities_Id),
    periods AS (
        SELECT
           (CURRENT_DATE - INTERVAL '1 day')::date AS dtd,
           (date_trunc('week', CURRENT_DATE) - INTERVAL '1 day')::date AS wtd,
           (date_trunc('month', CURRENT_DATE) - INTERVAL '1 day')::date AS mtd,
           (date_trunc('quarter', CURRENT_DATE) - INTERVAL '1 day')::date AS qtd,
           (date_trunc('year', CURRENT_DATE) - INTERVAL '1 day')::date AS ytd,
           (CURRENT_DATE - INTERVAL '6 months')::date AS semi,
           (CURRENT_DATE - INTERVAL '1 year')::date AS y1,
           (CURRENT_DATE - INTERVAL '2 years')::date AS y2,
           (CURRENT_DATE - INTERVAL '3 years')::date AS y3,
           (CURRENT_DATE - INTERVAL '5 years')::date AS y5
    )
    SELECT s.Securities_Id AS securities_id, s.Securities_Name, s.Ticker, s.Securities_Type::text,
           l.price_today,
           ROUND(((l.price_today - p_dtd.Close)/NULLIF(p_dtd.Close,0)*100)::numeric,2) AS dtd_pct,
           ROUND(((l.price_today - p_wtd.Close)/NULLIF(p_wtd.Close,0)*100)::numeric,2) AS wtd_pct,
           ROUND(((l.price_today - p_mtd.Close)/NULLIF(p_mtd.Close,0)*100)::numeric,2) AS mtd_pct,
           ROUND(((l.price_today - p_qtd.Close)/NULLIF(p_qtd.Close,0)*100)::numeric,2) AS qtd_pct,
           ROUND(((l.price_today - p_ytd.Close)/NULLIF(p_ytd.Close,0)*100)::numeric,2) AS ytd_pct,
           ROUND(((l.price_today - p_semi.Close)/NULLIF(p_semi.Close,0)*100)::numeric,2) AS semi_pct,
           ROUND(((l.price_today - p_y1.Close)/NULLIF(p_y1.Close,0)*100)::numeric,2) AS y1_pct,
           ROUND(((l.price_today - p_y2.Close)/NULLIF(p_y2.Close,0)*100)::numeric,2) AS y2_pct,
           ROUND(((l.price_today - p_y3.Close)/NULLIF(p_y3.Close,0)*100)::numeric,2) AS y3_pct,
           ROUND(((l.price_today - p_y5.Close)/NULLIF(p_y5.Close,0)*100)::numeric,2) AS y5_pct,
           ROUND((COALESCE(ha.qty,0) * l.price_today * COALESCE(fx.FX_Rate,1))::numeric,2) AS value_eur,
           (COALESCE(ha.qty,0) > 0) AS is_held
    FROM Securities s
    JOIN latest l ON l.Securities_Id=s.Securities_Id
    LEFT JOIN holdings_agg ha ON ha.Securities_Id=s.Securities_Id
    CROSS JOIN periods per
    LEFT JOIN LATERAL (SELECT Close FROM Historical_Prices WHERE Securities_Id=s.Securities_Id AND Date<=per.dtd ORDER BY Date DESC LIMIT 1) p_dtd ON true
    LEFT JOIN LATERAL (SELECT Close FROM Historical_Prices WHERE Securities_Id=s.Securities_Id AND Date<=per.wtd ORDER BY Date DESC LIMIT 1) p_wtd ON true
    LEFT JOIN LATERAL (SELECT Close FROM Historical_Prices WHERE Securities_Id=s.Securities_Id AND Date<=per.mtd ORDER BY Date DESC LIMIT 1) p_mtd ON true
    LEFT JOIN LATERAL (SELECT Close FROM Historical_Prices WHERE Securities_Id=s.Securities_Id AND Date<=per.qtd ORDER BY Date DESC LIMIT 1) p_qtd ON true
    LEFT JOIN LATERAL (SELECT Close FROM Historical_Prices WHERE Securities_Id=s.Securities_Id AND Date<=per.ytd ORDER BY Date DESC LIMIT 1) p_ytd ON true
    LEFT JOIN LATERAL (SELECT Close FROM Historical_Prices WHERE Securities_Id=s.Securities_Id AND Date<=per.semi ORDER BY Date DESC LIMIT 1) p_semi ON true
    LEFT JOIN LATERAL (SELECT Close FROM Historical_Prices WHERE Securities_Id=s.Securities_Id AND Date<=per.y1 ORDER BY Date DESC LIMIT 1) p_y1 ON true
    LEFT JOIN LATERAL (SELECT Close FROM Historical_Prices WHERE Securities_Id=s.Securities_Id AND Date<=per.y2 ORDER BY Date DESC LIMIT 1) p_y2 ON true
    LEFT JOIN LATERAL (SELECT Close FROM Historical_Prices WHERE Securities_Id=s.Securities_Id AND Date<=per.y3 ORDER BY Date DESC LIMIT 1) p_y3 ON true
    LEFT JOIN LATERAL (SELECT Close FROM Historical_Prices WHERE Securities_Id=s.Securities_Id AND Date<=per.y5 ORDER BY Date DESC LIMIT 1) p_y5 ON true
    LEFT JOIN LATERAL (SELECT FX_Rate FROM Historical_FX WHERE Currencies_Id_1=s.Currencies_Id ORDER BY Date DESC LIMIT 1) fx ON true
    WHERE s.Is_Active
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
def _savings_last_period_df(conn) -> pd.DataFrame:
    """Per-savings-account current balance and most recent real interest period's
    YOC%/APY% — independent of any selected date range. Shared basis for the Savings
    tab's "Detail for Last Interest Period" table, the Forecast tab's projections
    (/savings-forecast), and the Recommendations tab's account ranking
    (/savings-recommendations)."""
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
        COALESCE(fx3.FX_Rate, 1) AS fx_rate,
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
    LEFT JOIN Last_FXRates fx3 ON fx3.Accounts_Id = a.Accounts_Id
    WHERE a.Accounts_Type = 'Savings'
    ORDER BY a.Accounts_Name
    """
    df = pd.read_sql(query, conn)
    if df.empty:
        return df

    for dc in ["first_tx_date", "last_tx_date", "last_interest_date", "prior_interest_date"]:
        df[dc] = pd.to_datetime(df[dc], errors="coerce")

    period_start = df["prior_interest_date"].fillna(df["first_tx_date"])
    df["period_start_date"] = period_start
    df["holding_days_last"] = (df["last_interest_date"] - period_start).dt.days.clip(lower=1)

    avg_p_safe = df["avg_period_balance"].replace(0, float("nan"))
    df["avg_principal_last"] = avg_p_safe
    df["annual_interest_cash_last"] = df["last_interest_sum"] / df["holding_days_last"] * 365
    df["annual_yoc_pct_last"] = (df["annual_interest_cash_last"] / avg_p_safe * 100).fillna(0)

    r_last = df["last_interest_sum"] / avg_p_safe
    df["apy_pct_last"] = (((1 + r_last) ** (365 / df["holding_days_last"]) - 1) * 100).fillna(0)
    return df


@router.get("/savings-accounts")
def get_savings_accounts(
    period: str = Query("All Time"),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
):
    """Per-savings-account interest income and yield for a selected period (the
    Dividend-Tracker-style Actual view), plus each account's most recent real interest
    period, which stays independent of the selected period since it's also the basis
    Forecast (/savings-forecast) projects forward from."""
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

    splits_query = """
    WITH NonEURAccounts AS (
        SELECT DISTINCT a.Accounts_Id, a.Currencies_Id
        FROM Accounts a
        WHERE a.Accounts_Type = 'Savings'
          AND a.Currencies_Id NOT IN (SELECT Currencies_Id FROM Currencies WHERE Currencies_ShortName = 'EUR')
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
    )
    SELECT t.Accounts_Id AS accounts_id, t.Date::text AS date,
           CASE WHEN t.Transfers_Id IS NOT NULL THEN t.Total_Amount ELSE s.Amount END AS amount,
           CASE WHEN t.Transfers_Id IS NOT NULL THEN 'Principal'
                WHEN cat.Categories_Type = 'Interest' THEN 'Interest'
                ELSE 'Principal' END AS kind,
           COALESCE(fx.FX_Rate, 1) AS fx_rate
    FROM Transactions t
    LEFT JOIN Splits s ON s.Transactions_Id = t.Transactions_Id
    LEFT JOIN Categories cat ON cat.Categories_Id = s.Categories_Id
    JOIN Accounts a ON a.Accounts_Id = t.Accounts_Id
    LEFT JOIN Last_FXRates fx ON fx.Accounts_Id = t.Accounts_Id
    WHERE a.Accounts_Type = 'Savings'
    ORDER BY t.Accounts_Id, t.Date
    """
    accounts_query = """
        SELECT a.Accounts_Id AS accounts_id, a.Accounts_Name AS accounts_name,
               a.Accounts_Type AS accounts_type, c.Currencies_ShortName AS currency,
               a.Accounts_Balance AS current_balance
        FROM Accounts a JOIN Currencies c ON c.Currencies_Id = a.Currencies_Id
        WHERE a.Accounts_Type = 'Savings'
        ORDER BY a.Accounts_Name
    """
    with get_db() as conn:
        last_period_df = _savings_last_period_df(conn)
        splits_df = pd.read_sql(splits_query, conn)
        accounts_df = pd.read_sql(accounts_query, conn)

    if accounts_df.empty:
        return {"period": period, "period_label": period_label, "summary": {}, "detail": [], "detail_last": [], "monthly": []}

    detail_cols_last = [
        "accounts_id", "accounts_name", "accounts_type", "currency", "current_balance",
        "avg_principal_last", "last_interest_sum", "annual_interest_cash_last",
        "annual_yoc_pct_last", "apy_pct_last",
        "holding_days_last", "period_start_date", "last_interest_date",
    ]
    detail_last_records = _df_to_list(last_period_df[detail_cols_last].copy()) if not last_period_df.empty else []

    # Period-scoped Detail: a time-weighted walk of each account's running-balance step
    # function across [sd, ed], instead of a per-calendar-day SQL scan — cheap even for
    # an "All Time" window spanning decades, since it only visits actual transaction
    # dates rather than every day in between.
    splits_df["date"] = pd.to_datetime(splits_df["date"]).dt.date
    detail_rows: list = []
    monthly_totals: dict = {}
    for _, acc in accounts_df.iterrows():
        aid = int(acc["accounts_id"])
        acc_splits = splits_df[splits_df["accounts_id"] == aid].sort_values("date")
        if acc_splits.empty:
            continue

        in_period = acc_splits[(acc_splits["date"] >= sd) & (acc_splits["date"] <= ed)]
        principal_rows = in_period[in_period["kind"] == "Principal"]
        interest_rows  = in_period[in_period["kind"] == "Interest"]
        principal_period     = float(principal_rows["amount"].sum())
        principal_period_eur = float((principal_rows["amount"] * principal_rows["fx_rate"]).sum())
        interest_period       = float(interest_rows["amount"].sum())
        interest_period_eur   = float((interest_rows["amount"] * interest_rows["fx_rate"]).sum())
        if interest_period == 0 and principal_period == 0:
            continue

        balance = float(acc_splits.loc[acc_splits["date"] < sd, "amount"].sum())
        day_events = in_period.groupby("date")["amount"].sum()
        cursor = sd
        weighted_sum = 0.0
        for ev_date, amt in day_events.items():
            weighted_sum += balance * (ev_date - cursor).days
            balance += float(amt)
            cursor = ev_date
        weighted_sum += balance * ((ed - cursor).days + 1)
        holding_days_period = max((ed - sd).days + 1, 1)
        avg_balance_period = weighted_sum / holding_days_period

        annual_interest_cash = interest_period / holding_days_period * 365
        avg_safe = avg_balance_period if avg_balance_period > 0 else None
        annual_yoc_pct = (annual_interest_cash / avg_safe * 100) if avg_safe else 0.0
        r = (interest_period / avg_safe) if avg_safe else 0.0
        apy_pct = (((1 + r) ** (365 / holding_days_period) - 1) * 100) if avg_safe and r > -1 else 0.0

        detail_rows.append({
            "accounts_id": aid, "accounts_name": acc["accounts_name"],
            "accounts_type": acc["accounts_type"], "currency": acc["currency"],
            "principal": round(principal_period, 2), "principal_eur": round(principal_period_eur, 2),
            "total_interest": round(interest_period, 2), "total_interest_eur": round(interest_period_eur, 2),
            "annual_interest_cash": round(annual_interest_cash, 2),
            "avg_balance": round(avg_balance_period, 2),
            "current_balance": float(acc["current_balance"]),
            "annual_yoc_pct": round(annual_yoc_pct, 4), "apy_pct": round(apy_pct, 4),
            "holding_days": holding_days_period,
        })

        for _, ev in interest_rows.iterrows():
            key = str(ev["date"].replace(day=1))
            monthly_totals[key] = monthly_totals.get(key, 0.0) + float(ev["amount"]) * float(ev["fx_rate"])

    total_principal_eur = sum(r["principal_eur"] for r in detail_rows)
    total_interest_eur = sum(r["total_interest_eur"] for r in detail_rows)
    yocs = [r["annual_yoc_pct"] for r in detail_rows if r["annual_yoc_pct"] != 0]
    apys = [r["apy_pct"] for r in detail_rows if r["apy_pct"] != 0]
    avg_yoc = sum(yocs) / len(yocs) if yocs else None
    avg_apy = sum(apys) / len(apys) if apys else None

    # Inclusive calendar-month count spanned by [sd, ed] (e.g. Jan-Jul = 7), matching
    # the number of bars the monthly chart could show, not just the months that
    # actually had interest — same convention as Dividend Tracker's own Actual view.
    months_in_period = max((ed.year - sd.year) * 12 + (ed.month - sd.month) + 1, 1)

    summary = {
        "savings_accounts_count": len(detail_rows),
        "total_principal_eur": round(total_principal_eur, 2),
        "total_interest_eur": round(total_interest_eur, 2),
        "avg_monthly_interest_eur": round(total_interest_eur / months_in_period, 2),
        "avg_yoc_pct": round(avg_yoc, 4) if avg_yoc is not None else None,
        "avg_apy_pct": round(avg_apy, 4) if avg_apy is not None else None,
    }
    monthly = sorted([{"month": k, "income_eur": round(v, 2)} for k, v in monthly_totals.items()], key=lambda x: x["month"])

    return {
        "period": period,
        "period_label": period_label,
        "summary": summary,
        "detail": detail_rows,
        "detail_last": detail_last_records,
        "monthly": monthly,
    }


@router.get("/savings-forecast")
def get_savings_forecast(period: str = Query("12m", pattern="^(eoy|6m|12m)$")):
    """Projects future interest income for savings accounts. An account with a
    user-defined rate schedule (Static Data -> Accounts -> %) uses that — a
    balance-tiered %, anchored to the account's own real posting cadence where one is
    known; otherwise, compounds the current balance forward at its last real interest
    period's APY% and payment cadence — the savings equivalent of Dividend Tracker
    Forecast's "project from what you actually have," using APY (compounding) since
    savings accounts have no stated forward yield to fall back on."""
    import calendar as _cal
    from datetime import date as _date, timedelta as _td

    today = _date.today()

    def _add_months(d: _date, n: int) -> _date:
        m = d.month + n
        y = d.year + (m - 1) // 12
        m = (m - 1) % 12 + 1
        return d.replace(year=y, month=m, day=min(d.day, _cal.monthrange(y, m)[1]))

    if period == "eoy":
        cutoff = _date(today.year, 12, 31)
    elif period == "6m":
        cutoff = _add_months(today, 6)
    else:
        cutoff = _add_months(today, 12)

    with get_db() as conn:
        df = _savings_last_period_df(conn)
        from database.queries import _ensure_account_interest_rate_schema
        _ensure_account_interest_rate_schema()
        df_rate_schedules = _load_manual_rate_schedules(conn, ['Savings'])
    schedules_by_account = _group_rate_schedules(df_rate_schedules)

    if df.empty:
        return {"period": period, "summary": {}, "monthly_forecast": [], "by_account": []}

    monthly_map: dict = {}
    rows: list = []
    for _, r in df.iterrows():
        accounts_id = int(r["accounts_id"])
        balance = _fnum(r.get("current_balance"))
        apy = _fnum(r.get("apy_pct_last"))
        hd = r.get("holding_days_last")
        cadence_days = int(hd) if pd.notna(hd) else 0
        last_date = r.get("last_interest_date")
        fx = _fnum(r.get("fx_rate"), default=1.0)

        manual = schedules_by_account.get(accounts_id)
        if manual and balance > 0:
            real_last = pd.Timestamp(last_date).date() if pd.notna(last_date) else None
            payments = _project_schedule_payments(
                manual['schedules'], balance, str(r['currency']), fx,
                str(r['accounts_name']), accounts_id, today, cutoff,
                real_last_date=real_last, real_cadence_days=cadence_days or None,
            )
            if not payments:
                continue
            for p in payments:
                key = p['date'][:7] + '-01'
                monthly_map[key] = monthly_map.get(key, 0.0) + p['amount_eur']
            period_total_eur = sum(p['amount_eur'] for p in payments)
            active_now = _schedule_for_date(manual['schedules'], today) or manual['schedules'][0]
            display_apy = _tiered_interest(active_now, balance, 365) / balance * 100
            rows.append({
                "accounts_id":            accounts_id,
                "accounts_name":          str(r["accounts_name"]),
                "accounts_type":          str(r["accounts_type"]),
                "currency":               str(r["currency"]),
                "current_balance":        round(balance, 2),
                "current_balance_eur":    round(balance * fx, 2),
                "apy_pct":                round(display_apy, 4),
                "cadence_days":           cadence_days or None,
                "period_forecast_eur":    round(period_total_eur, 2),
                "projected_balance_eur":  round(balance * fx + period_total_eur, 2),
                "next_payment_date":      payments[0]['date'],
                "payments_in_period":     len(payments),
            })
            continue

        if balance <= 0 or apy <= 0 or cadence_days <= 0 or pd.isna(last_date):
            continue

        next_date = pd.Timestamp(last_date).date()
        while next_date <= today:
            next_date += _td(days=cadence_days)

        # Compounding happens in the account's own currency (balance, APY%, and
        # cadence are all intrinsic to it) — only the resulting payments are
        # converted to EUR afterward, for cross-currency totals/monthly chart.
        running_balance = balance
        period_total = 0.0
        payments: list = []
        while next_date <= cutoff:
            payment = running_balance * ((1 + apy / 100) ** (cadence_days / 365) - 1)
            running_balance += payment
            period_total += payment
            payments.append((next_date, payment))
            next_date += _td(days=cadence_days)

        if not payments:
            continue

        for pay_date, amt in payments:
            key = str(pay_date.replace(day=1))
            monthly_map[key] = monthly_map.get(key, 0.0) + amt * fx

        rows.append({
            "accounts_id":            accounts_id,
            "accounts_name":          str(r["accounts_name"]),
            "accounts_type":          str(r["accounts_type"]),
            "currency":               str(r["currency"]),
            "current_balance":        round(balance, 2),
            "current_balance_eur":    round(balance * fx, 2),
            "apy_pct":                round(apy, 4),
            "cadence_days":           cadence_days,
            "period_forecast_eur":    round(period_total * fx, 2),
            "projected_balance_eur":  round(running_balance, 2),
            "next_payment_date":      str(payments[0][0]),
            "payments_in_period":     len(payments),
        })

    if not rows:
        return {"period": period, "summary": {}, "monthly_forecast": [], "by_account": []}

    total_period    = sum(r["period_forecast_eur"] for r in rows)
    total_balance   = sum(r["current_balance_eur"] for r in rows)
    total_annual    = sum(r["current_balance_eur"] * r["apy_pct"] / 100 for r in rows)
    months_in_period = max(1, round((cutoff - today).days / 30.44))
    total_monthly   = total_period / months_in_period
    portfolio_apy   = (total_annual / total_balance * 100) if total_balance > 0 else 0

    by_account = sorted(rows, key=lambda x: x["period_forecast_eur"], reverse=True)
    monthly_forecast = sorted([{"month": k, "income_eur": round(v, 2)} for k, v in monthly_map.items()], key=lambda x: x["month"])

    return {
        "period": period,
        "summary": {
            "total_period_eur":  round(total_period, 2),
            "total_annual_eur":  round(total_annual, 2),
            "total_monthly_eur": round(total_monthly, 2),
            "accounts_count":    len(by_account),
            "portfolio_apy_pct": round(portfolio_apy, 2),
        },
        "monthly_forecast": monthly_forecast,
        "by_account":       by_account,
    }


@router.get("/savings-recommendations")
def get_savings_recommendations():
    """Ranks your existing savings accounts by expected APY% — a manually-defined rate
    schedule (Static Data -> Accounts -> %) where one exists, since that reflects the
    bank's currently-published rate rather than what a past period happened to pay;
    otherwise the last-real-period APY% — and flags idle balances sitting in 0%-yield
    Cash/Checking accounts with a suggestion to move them into your best-performing
    savings account in the same currency instead. There's no external market of
    savings accounts to recommend opening — only your own."""
    MATERIALITY_EUR = 50  # ignore idle balances too small to bother moving

    with get_db() as conn:
        savings_df = _savings_last_period_df(conn)
        from database.queries import _ensure_account_interest_rate_schema
        _ensure_account_interest_rate_schema()
        schedules_by_account = _group_rate_schedules(_load_manual_rate_schedules(conn, ['Savings']))
        idle_df = pd.read_sql("""
            SELECT a.Accounts_Id AS accounts_id, a.Accounts_Name AS accounts_name,
                   a.Accounts_Type AS accounts_type, c.Currencies_ShortName AS currency,
                   a.Accounts_Balance AS balance
            FROM Accounts a JOIN Currencies c ON c.Currencies_Id = a.Currencies_Id
            WHERE a.Accounts_Type IN ('Cash','Checking') AND a.Accounts_Balance > 0
            ORDER BY a.Accounts_Balance DESC
        """, conn)
        fx_df = pd.read_sql("""
            SELECT c.Currencies_ShortName AS currency,
                   COALESCE((
                       SELECT hfx.FX_Rate FROM Historical_FX hfx
                       WHERE hfx.Currencies_Id_1 = c.Currencies_Id
                         AND hfx.Currencies_Id_2 = (SELECT Currencies_Id FROM Currencies WHERE Currencies_ShortName='EUR')
                       ORDER BY hfx.Date DESC LIMIT 1
                   ), 1) AS fx_rate
            FROM Currencies c
        """, conn)
    fx_map = dict(zip(fx_df["currency"], fx_df["fx_rate"]))
    fx_map.setdefault("EUR", 1.0)

    ranking: list = []
    if not savings_df.empty:
        for _, r in savings_df.iterrows():
            accounts_id = int(r["accounts_id"])
            balance = _fnum(r.get("current_balance"))
            apy_pct = _fnum(r.get("apy_pct_last"))
            yoc_pct = _fnum(r.get("annual_yoc_pct_last"))
            manual = schedules_by_account.get(accounts_id)
            manual_rate = False
            if manual and balance > 0:
                active = _schedule_for_date(manual['schedules'], _dt.date.today()) or manual['schedules'][0]
                blended = _tiered_interest(active, balance, 365) / balance * 100
                apy_pct = yoc_pct = blended
                manual_rate = True
            ranking.append({
                "accounts_id":        accounts_id,
                "accounts_name":      str(r["accounts_name"]),
                "currency":           str(r["currency"]),
                "current_balance":    round(balance, 2),
                "apy_pct":            round(apy_pct, 4),
                "annual_yoc_pct":     round(yoc_pct, 4),
                "last_interest_date": str(r["last_interest_date"].date()) if pd.notna(r.get("last_interest_date")) else None,
                "manual_rate":        manual_rate,
            })
        ranking.sort(key=lambda x: x["apy_pct"], reverse=True)

    # Best savings account per currency — an idle EUR balance can only usefully move
    # into a EUR savings account, not a GBP one, without introducing FX risk/cost.
    best_by_currency: dict = {}
    for r in ranking:
        cur = r["currency"]
        if r["apy_pct"] > 0 and (cur not in best_by_currency or r["apy_pct"] > best_by_currency[cur]["apy_pct"]):
            best_by_currency[cur] = r

    idle_opportunities: list = []
    for _, r in idle_df.iterrows():
        cur = str(r["currency"])
        balance = _fnum(r.get("balance"))
        best = best_by_currency.get(cur)
        if not best or balance < MATERIALITY_EUR:
            continue
        potential_gain = balance * best["apy_pct"] / 100
        idle_opportunities.append({
            "accounts_id":               int(r["accounts_id"]),
            "accounts_name":             str(r["accounts_name"]),
            "accounts_type":             str(r["accounts_type"]),
            "currency":                  cur,
            "balance":                   round(balance, 2),
            "target_accounts_id":        best["accounts_id"],
            "target_accounts_name":      best["accounts_name"],
            "target_apy_pct":            best["apy_pct"],
            "potential_annual_gain":     round(potential_gain, 2),
            "potential_annual_gain_eur": round(potential_gain * fx_map.get(cur, 1), 2),
        })
    idle_opportunities.sort(key=lambda x: x["potential_annual_gain_eur"], reverse=True)

    return {
        "ranking": ranking,
        "idle_opportunities": idle_opportunities,
        "total_potential_gain_eur": round(sum(o["potential_annual_gain_eur"] for o in idle_opportunities), 2),
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


# ── Point-in-time holdings/prices/cash — shared by the X-Ray (Portfolio Analysis)
# endpoints' "compare vs a past date" support ────────────────────────────────────
# There's no historical snapshot table for Holdings (it's a live-only cache, one
# row per position, overwritten as trades happen) or for Fund_Composition (sector
# weights/asset mix/bond ratings/category/expense ratio — one row per security,
# always "as of today"). So a past date can only ever make the *holdings quantity
# and price/FX* side of these breakdowns point-in-time; fund composition stays
# current, the same approximation the "live" view already makes for today. This
# mirrors the replay-from-Investments-ledger technique /holdings-snapshot already
# uses for Detail Analysis's own "As of date", and the "current balance minus
# transactions after the date" technique /net-worth-report already uses for
# Cash/Checking/Savings/Credit Card accounts.
def _pit_ctes(as_of: Optional[str]) -> tuple[str, str, str]:
    """(fx_cte, prices_cte, h_src_cte) text for the WITH clause — h_src exposes
    (Securities_Id, Accounts_Id, Quantity) either from the live Holdings table
    (as_of=None) or replayed from Investments up to as_of. Bind %(as_of)s via
    params={'as_of': as_of} when as_of is given."""
    if not as_of:
        return (
            "fx AS (SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate FROM Historical_FX ORDER BY Currencies_Id_1, Date DESC)",
            "prices AS (SELECT DISTINCT ON (Securities_Id) Securities_Id, Close FROM Historical_Prices ORDER BY Securities_Id, Date DESC)",
            "h_src AS (SELECT Securities_Id, Accounts_Id, Quantity FROM Holdings)",
        )
    return (
        "fx AS (SELECT DISTINCT ON (Currencies_Id_1) Currencies_Id_1, FX_Rate FROM Historical_FX WHERE Date <= %(as_of)s ORDER BY Currencies_Id_1, Date DESC)",
        "prices AS (SELECT DISTINCT ON (Securities_Id) Securities_Id, Close FROM Historical_Prices WHERE Date <= %(as_of)s ORDER BY Securities_Id, Date DESC)",
        """h_src AS (
            SELECT Securities_Id, Accounts_Id,
                   SUM(CASE WHEN Action IN ('Buy','Reinvest','ShrIn') THEN Quantity
                            WHEN Action IN ('Sell','ShrOut') THEN -Quantity ELSE 0 END) AS Quantity
            FROM Investments
            WHERE Date <= %(as_of)s
            GROUP BY Securities_Id, Accounts_Id
            HAVING SUM(CASE WHEN Action IN ('Buy','Reinvest','ShrIn') THEN Quantity
                             WHEN Action IN ('Sell','ShrOut') THEN -Quantity ELSE 0 END) > 0.00000001
        )""",
    )


def _pit_cash_balance_expr(as_of: Optional[str]) -> str:
    """SQL expression (referencing alias `a` for Accounts) for a cash-like account's
    balance as of a date — its current balance minus every transaction posted after
    that date — or the live balance when as_of is None."""
    if not as_of:
        return "a.Accounts_Balance"
    return """(a.Accounts_Balance - COALESCE((
        SELECT SUM(Total_Amount) FROM Transactions WHERE Accounts_Id = a.Accounts_Id AND Date > %(as_of)s
    ), 0))"""


# ── Portfolio Presets ───────────────────────────────────────────────────────────
# Report_Scope keeps each report section's preset names in their own namespace
# ('inv_performance' | 'net_worth' | 'inv_positions') — see the ALTER TABLE in
# database/connection.py's _run_startup_migrations for how an existing table
# (created before Report_Scope existed) gets upgraded.
def _ensure_presets_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS Portfolio_Presets (
            Preset_Id    SERIAL PRIMARY KEY,
            Report_Scope VARCHAR(32) NOT NULL DEFAULT 'inv_performance',
            Preset_Name  VARCHAR(100) NOT NULL,
            Account_Ids  INTEGER[] NOT NULL DEFAULT '{}',
            Created_At   TIMESTAMP DEFAULT NOW(),
            Updated_At   TIMESTAMP DEFAULT NOW(),
            UNIQUE (Report_Scope, Preset_Name)
        )
    """)


@router.get("/portfolio-presets")
def get_portfolio_presets(report_scope: str = Query("inv_performance")):
    conn = get_connection()
    try:
        cur = conn.cursor()
        _ensure_presets_table(cur)
        conn.commit()
        df = pd.read_sql("""
            SELECT Preset_Id AS preset_id, Preset_Name AS preset_name, Account_Ids AS account_ids
            FROM Portfolio_Presets WHERE Report_Scope = %(report_scope)s ORDER BY Preset_Name
        """, conn, params={"report_scope": report_scope})
        return _df_to_list(df)
    finally:
        conn.close()


@router.post("/portfolio-presets")
def upsert_portfolio_preset(data: dict):
    name = (data.get("name") or "").strip()
    account_ids = data.get("account_ids") or []
    report_scope = data.get("report_scope") or "inv_performance"
    if not name:
        raise HTTPException(400, "Preset name is required")
    conn = get_connection()
    try:
        cur = conn.cursor()
        _ensure_presets_table(cur)
        cur.execute("""
            INSERT INTO Portfolio_Presets (Report_Scope, Preset_Name, Account_Ids, Updated_At)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (Report_Scope, Preset_Name) DO UPDATE
                SET Account_Ids = EXCLUDED.Account_Ids, Updated_At = NOW()
        """, (report_scope, name, account_ids))
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
def _account_weighted_index(conn, acct_clause: str, lookback_days: int) -> Optional["pd.Series"]:
    """A (Holdings-weighted, today's-weights-held-constant) daily return index for the
    given account scope, indexed to 100 at the start of the window — the same NAV-style
    approximation used for the primary "portfolio" side of /benchmark, factored out so
    it can also stand in as the comparison side (another account instead of a market
    index/security)."""
    prices_df = pd.read_sql(f"""
        WITH held AS (
            SELECT DISTINCT h.Securities_Id FROM Holdings h WHERE h.Quantity > 0{acct_clause}
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
        WHERE h.Quantity > 0{acct_clause}
        GROUP BY s.Securities_Name
        HAVING SUM(h.Quantity * COALESCE(lp.Close,0) * CASE WHEN c.Currencies_ShortName='EUR' THEN 1 ELSE COALESCE(fx.FX_Rate,1) END) > 0
    """, conn)

    if prices_df.empty or weights_df.empty:
        return None

    prices_df["date"] = pd.to_datetime(prices_df["date"])
    wide = prices_df.pivot_table(index="date", columns="ticker", values="close", aggfunc="mean")
    total = weights_df["value_eur"].sum()
    weights_df["weight"] = weights_df["value_eur"] / total
    w = weights_df.set_index("ticker")["weight"]
    common = wide.columns.intersection(w.index)
    wide = wide[common]
    w = w[common]
    if w.sum() == 0:
        return None
    w = w / w.sum()

    wide_ffill = wide.ffill()
    ret = wide_ffill.pct_change().fillna(0)
    port_ret = ret.dot(w)
    port_idx = (1 + port_ret).cumprod() * 100
    port_idx.iloc[0] = 100
    return port_idx


@router.get("/benchmark")
def get_benchmark(
    benchmark_id: Optional[int] = Query(None),
    lookback_days: int = Query(252),
    account_ids: Optional[str] = Query(None),
    compare_account_ids: Optional[str] = Query(None),
    resample: str = Query("Daily"),
    ytd: bool = Query(False),
):
    """Portfolio (weighted avg of holdings) vs either a benchmark security/index or
    another account's own weighted portfolio, both indexed to 100. Pass exactly one of
    benchmark_id (a Securities_Id, e.g. a market index) or compare_account_ids
    (comma-separated account ids) as the comparison side — compare_account_ids wins if
    both are somehow given. ytd=true overrides lookback_days to "since Jan 1 this year"."""
    if ytd:
        from datetime import date as _date
        _today = _date.today()
        lookback_days = (_today - _date(_today.year, 1, 1)).days + 1

    acct_ids = _parse_account_ids(account_ids)
    held_clause = _acct_clause(acct_ids, "h.Accounts_Id")

    with get_db() as conn:
        port_idx = _account_weighted_index(conn, held_clause, lookback_days)

        cmp_acct_ids = _parse_account_ids(compare_account_ids)
        if cmp_acct_ids:
            cmp_clause = _acct_clause(cmp_acct_ids, "h.Accounts_Id")
            bench_idx_raw = _account_weighted_index(conn, cmp_clause, lookback_days)
            bench_idx = bench_idx_raw if bench_idx_raw is not None else None
        elif benchmark_id:
            bench_df = pd.read_sql("""
                SELECT Date AS date, Close AS close FROM Historical_Prices
                WHERE Securities_Id = %(bid)s AND Date >= CURRENT_DATE - (%(lb)s || ' days')::INTERVAL
                ORDER BY Date
            """, conn, params={"bid": benchmark_id, "lb": lookback_days})
            if bench_df.empty:
                bench_idx = None
            else:
                bench_df["date"] = pd.to_datetime(bench_df["date"])
                bench_idx = bench_df.set_index("date")["close"]
        else:
            bench_idx = None

    if port_idx is None or bench_idx is None:
        return []

    bench_s = bench_idx.reindex(port_idx.index).ffill().bfill()
    first_bench = bench_s.iloc[0] if not pd.isna(bench_s.iloc[0]) else bench_s.dropna().iloc[0] if not bench_s.dropna().empty else None
    bench_norm = bench_s / first_bench * 100 if first_bench else pd.Series(index=port_idx.index, dtype=float)

    combined = pd.DataFrame({"portfolio": port_idx, "benchmark": bench_norm})

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
