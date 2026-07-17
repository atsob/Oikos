"""Static Data API endpoints: institutions, categories, payees, accounts."""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional
import logging
import math
import pandas as pd
from database.connection import get_db

log = logging.getLogger(__name__)

router = APIRouter()


def _df(df: pd.DataFrame) -> list:
    df = df.copy()
    for col in df.select_dtypes(include=["datetime", "datetimetz"]).columns:
        df[col] = df[col].astype(str)
    records = df.where(pd.notnull(df), other=None).to_dict(orient="records")
    return [{k: None if isinstance(v, float) and math.isnan(v) else v for k, v in r.items()} for r in records]


@router.get("/institutions")
def get_institutions(search: Optional[str] = Query(None)):
    clause = "AND (LOWER(Institutions_Name) LIKE %(s)s OR LOWER(Institutions_Type) LIKE %(s)s)" if search else ""
    params: dict = {}
    if search:
        params["s"] = f"%{search.lower()}%"
    with get_db() as conn:
        df = pd.read_sql(f"""
            SELECT Institutions_Id AS id, Institutions_Name AS name,
                   Institutions_Type AS type, BIC_Code AS bic,
                   Moodys AS moodys, S_P AS sp, Fitch AS fitch,
                   Contact AS contact, Phone AS phone,
                   Email AS email, Website AS website, Notes AS notes
            FROM Institutions
            WHERE 1=1 {clause}
            ORDER BY Institutions_Name ASC
        """, conn, params=params if params else None)
    return _df(df)


@router.get("/categories")
def get_categories(search: Optional[str] = Query(None)):
    clause = "AND LOWER(ch.Full_Path) LIKE %(s)s" if search else ""
    params: dict = {}
    if search:
        params["s"] = f"%{search.lower()}%"
    with get_db() as conn:
        df = pd.read_sql(f"""
            WITH RECURSIVE CategoryHierarchy AS (
                SELECT Categories_Id, Categories_Name::TEXT AS Full_Path,
                       Categories_Type::TEXT AS Categories_Type,
                       Categories_Id_Parent, 0 AS Level
                FROM Categories WHERE Categories_Id_Parent IS NULL
                UNION ALL
                SELECT c.Categories_Id, ch.Full_Path || ' : ' || c.Categories_Name,
                       c.Categories_Type::TEXT, c.Categories_Id_Parent, ch.Level + 1
                FROM Categories c JOIN CategoryHierarchy ch ON c.Categories_Id_Parent = ch.Categories_Id
            ),
            SplitCounts AS (
                SELECT Categories_Id, COUNT(*) AS cnt FROM Splits GROUP BY Categories_Id
            )
            SELECT ch.Categories_Id AS id, ch.Full_Path AS full_path,
                   ch.Categories_Type AS type, ch.Level AS level,
                   ch.Categories_Id_Parent AS parent_id,
                   COALESCE(sc.cnt, 0) AS transactions_count
            FROM CategoryHierarchy ch
            LEFT JOIN SplitCounts sc ON sc.Categories_Id = ch.Categories_Id
            WHERE 1=1 {clause}
            ORDER BY ch.Full_Path ASC
        """, conn, params=params if params else None)
    return _df(df)


@router.get("/payees")
def get_payees(search: Optional[str] = Query(None)):
    clause = "AND LOWER(p.Payees_Name) LIKE %(s)s" if search else ""
    params: dict = {}
    if search:
        params["s"] = f"%{search.lower()}%"
    with get_db() as conn:
        df = pd.read_sql(f"""
            WITH RECURSIVE ch AS (
                SELECT Categories_Id, Categories_Name::TEXT AS full_path
                FROM Categories WHERE Categories_Id_Parent IS NULL
                UNION ALL
                SELECT c.Categories_Id, ch.full_path || ' : ' || c.Categories_Name
                FROM Categories c JOIN ch ON c.Categories_Id_Parent = ch.Categories_Id
            )
            SELECT p.Payees_Id AS id, p.Payees_Name AS name,
                   p.Categories_Id_Default AS categories_id,
                   ch.full_path AS default_category,
                   p.Track_For_News AS track_for_news,
                   COUNT(t.Transactions_Id) AS transactions_count,
                   MAX(t.Date) AS last_transaction
            FROM Payees p
            LEFT JOIN Transactions t ON t.Payees_Id = p.Payees_Id
            LEFT JOIN ch ON ch.Categories_Id = p.Categories_Id_Default
            WHERE 1=1 {clause}
            GROUP BY p.Payees_Id, p.Payees_Name, ch.full_path, p.Track_For_News
            ORDER BY p.Payees_Name ASC
        """, conn, params=params if params else None)
    return _df(df)


@router.get("/payees/{pid}/top-categories")
def get_payee_top_categories(pid: int, limit: int = Query(5)):
    with get_db() as conn:
        df = pd.read_sql("""
            WITH RECURSIVE ch AS (
                SELECT Categories_Id, Categories_Name::TEXT AS full_path
                FROM Categories WHERE Categories_Id_Parent IS NULL
                UNION ALL
                SELECT c.Categories_Id, ch.full_path || ' : ' || c.Categories_Name
                FROM Categories c JOIN ch ON c.Categories_Id_Parent = ch.Categories_Id
            )
            SELECT s.Categories_Id AS id, ch.full_path, COUNT(*) AS usage_count
            FROM Transactions t
            JOIN Splits s ON t.Transactions_Id = s.Transactions_Id
            JOIN ch ON ch.Categories_Id = s.Categories_Id
            WHERE t.Payees_Id = %(pid)s AND s.Categories_Id IS NOT NULL
            GROUP BY s.Categories_Id, ch.full_path
            ORDER BY usage_count DESC
            LIMIT %(limit)s
        """, conn, params={"pid": pid, "limit": limit})
    return _df(df)


@router.get("/payees/{pid}/transactions")
def get_payee_transactions(pid: int):
    with get_db() as conn:
        df = pd.read_sql("""
            SELECT t.transactions_id AS id,
                   t.date,
                   a.accounts_name AS account,
                   t.description,
                   SUM(s.amount) AS amount,
                   c.currencies_shortname AS currency
            FROM transactions t
            JOIN accounts a ON a.accounts_id = t.accounts_id
            JOIN splits s ON s.transactions_id = t.transactions_id
            LEFT JOIN currencies c ON c.currencies_id = a.currencies_id
            WHERE t.payees_id = %(pid)s
            GROUP BY t.transactions_id, t.date, a.accounts_name,
                     t.description, c.currencies_shortname
            ORDER BY t.date DESC
        """, conn, params={"pid": pid})
    return _df(df)


@router.get("/categories/{cid}/transactions")
def get_category_transactions(cid: int):
    with get_db() as conn:
        df = pd.read_sql("""
            SELECT t.transactions_id AS id,
                   t.date,
                   a.accounts_name AS account,
                   p.payees_name AS payee,
                   t.description,
                   s.amount,
                   c.currencies_shortname AS currency
            FROM splits s
            JOIN transactions t ON t.transactions_id = s.transactions_id
            JOIN accounts a ON a.accounts_id = t.accounts_id
            LEFT JOIN payees p ON p.payees_id = t.payees_id
            LEFT JOIN currencies c ON c.currencies_id = a.currencies_id
            WHERE s.categories_id = %(cid)s
            ORDER BY t.date DESC
        """, conn, params={"cid": cid})
    return _df(df)


@router.post("/payees")
def upsert_payee(data: dict):
    conn_obj = __import__('database.connection', fromlist=['get_connection']).get_connection()
    try:
        cur = conn_obj.cursor()
        pid = data.get('id')
        name = data.get('name', '')
        cat_id = data.get('categories_id') or None
        track_for_news = bool(data.get('track_for_news', False))
        if pid:
            cur.execute(
                "UPDATE Payees SET Payees_Name=%s, Categories_Id_Default=%s, Track_For_News=%s WHERE Payees_Id=%s",
                (name, cat_id, track_for_news, pid)
            )
        else:
            cur.execute(
                "INSERT INTO Payees (Payees_Name, Categories_Id_Default, Track_For_News) VALUES (%s, %s, %s) RETURNING Payees_Id",
                (name, cat_id, track_for_news)
            )
            pid = cur.fetchone()[0]
        conn_obj.commit()
        return {"id": pid}
    except Exception as e:
        conn_obj.rollback()
        raise __import__('fastapi', fromlist=['HTTPException']).HTTPException(500, str(e))
    finally:
        conn_obj.close()


@router.post("/payees/merge")
def merge_payees(data: dict):
    from database.connection import get_connection
    from fastapi import HTTPException
    source_id = data.get('source_id')
    target_id = data.get('target_id')
    if not source_id or not target_id or source_id == target_id:
        raise HTTPException(400, "source_id and target_id required and must differ")
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE Transactions SET Payees_Id=%s WHERE Payees_Id=%s", (target_id, source_id))
        cur.execute("DELETE FROM Payees WHERE Payees_Id=%s", (source_id,))
        conn.commit()
        return {"merged": source_id, "into": target_id}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.delete("/payees/{pid}")
def delete_payee(pid: int):
    from fastapi import HTTPException
    conn_obj = __import__('database.connection', fromlist=['get_connection']).get_connection()
    try:
        cur = conn_obj.cursor()
        cur.execute("SELECT COUNT(*) FROM Transactions WHERE Payees_Id=%s", (pid,))
        tx = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM Recurring_Templates WHERE Payees_Id=%s", (pid,))
        rt = cur.fetchone()[0]
        if tx or rt:
            raise HTTPException(400, f"Cannot delete: payee is used in {tx} transaction(s) and {rt} recurring template(s). Use Merge to reassign them first.")
        cur.execute("DELETE FROM Payee_Rules WHERE Payees_Id=%s", (pid,))
        cur.execute("DELETE FROM Payees WHERE Payees_Id=%s", (pid,))
        conn_obj.commit()
        return {"deleted": pid}
    except HTTPException:
        conn_obj.rollback()
        raise
    except Exception as e:
        conn_obj.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn_obj.close()


@router.post("/categories")
def upsert_category(data: dict):
    from database.connection import get_connection
    from fastapi import HTTPException
    conn = get_connection()
    try:
        cur = conn.cursor()
        cid = data.get('id')
        if cid:
            cur.execute(
                "UPDATE Categories SET Categories_Name=%s, Categories_Type=%s, Categories_Id_Parent=%s WHERE Categories_Id=%s",
                (data.get('name'), data.get('type'), data.get('parent_id') or None, cid))
        else:
            cur.execute(
                "INSERT INTO Categories (Categories_Name, Categories_Id_Parent, Categories_Type) VALUES (%s, %s, %s) RETURNING Categories_Id",
                (data.get('name'), data.get('parent_id') or None, data.get('type', 'Expense')))
            cid = cur.fetchone()[0]
        conn.commit()
        return {"id": cid}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.post("/categories/merge")
def merge_categories(data: dict):
    from database.connection import get_connection
    from fastapi import HTTPException
    source_id = data.get('source_id')
    target_id = data.get('target_id')
    if not source_id or not target_id or source_id == target_id:
        raise HTTPException(400, "source_id and target_id required and must differ")
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE Splits SET Categories_Id=%s WHERE Categories_Id=%s", (target_id, source_id))
        cur.execute("DELETE FROM Annual_Budgets WHERE Categories_Id=%s", (source_id,))
        cur.execute("DELETE FROM Budgets WHERE Categories_Id=%s", (source_id,))
        cur.execute("DELETE FROM Categories WHERE Categories_Id=%s", (source_id,))
        conn.commit()
        return {"merged": source_id, "into": target_id}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.delete("/categories/{cid}")
def delete_category(cid: int):
    from fastapi import HTTPException
    conn_obj = __import__('database.connection', fromlist=['get_connection']).get_connection()
    try:
        cur = conn_obj.cursor()
        cur.execute("SELECT COUNT(*) FROM Splits WHERE Categories_Id=%s", (cid,))
        splits = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM Recurring_Template_Splits WHERE Categories_Id=%s", (cid,))
        rt = cur.fetchone()[0]
        if splits or rt:
            raise HTTPException(400, f"Cannot delete: category is used in {splits} split(s) and {rt} recurring template split(s). Use Merge to reassign them first.")
        cur.execute("DELETE FROM Payee_Rules WHERE Categories_Id=%s", (cid,))
        cur.execute("DELETE FROM Annual_Budgets WHERE Categories_Id=%s", (cid,))
        cur.execute("DELETE FROM Budgets WHERE Categories_Id=%s", (cid,))
        cur.execute("DELETE FROM Categories WHERE Categories_Id=%s", (cid,))
        conn_obj.commit()
        return {"deleted": cid}
    except HTTPException:
        conn_obj.rollback()
        raise
    except Exception as e:
        conn_obj.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn_obj.close()


@router.post("/institutions")
def upsert_institution(data: dict):
    iid = data.get('id')
    name = (data.get('name') or '').strip()
    inst_type = (data.get('type') or '').strip()

    if not name:
        raise HTTPException(422, "Institution name is required")
    if not inst_type:
        raise HTTPException(422, "Institution type is required")

    col_map = {
        'name':    'Institutions_Name',
        'type':    'Institutions_Type',
        'bic':     'BIC_Code',
        'moodys':  'Moodys',
        'sp':      'S_P',
        'fitch':   'Fitch',
        'website': 'Website',
        'contact': 'Contact',
        'phone':   'Phone',
        'email':   'Email',
        'notes':   'Notes',
    }
    # Coerce empty strings to None for all optional fields
    fields = {}
    for k in col_map:
        v = data.get(k)
        fields[k] = v if (v not in ('', None) or k in ('name', 'type')) else None
    fields['name'] = name
    fields['type'] = inst_type

    try:
        with get_db() as conn:
            cur = conn.cursor()
            if iid:
                sets = ", ".join(f"{col_map[k]}=%s" for k in fields)
                vals = [fields[k] for k in fields] + [iid]
                cur.execute(f"UPDATE Institutions SET {sets} WHERE Institutions_Id=%s", vals)
            else:
                cols = ", ".join(col_map[k] for k in fields)
                phs  = ", ".join("%s" for _ in fields)
                vals = [fields[k] for k in fields]
                cur.execute(
                    f"INSERT INTO Institutions ({cols}) VALUES ({phs}) RETURNING Institutions_Id",
                    vals,
                )
                iid = cur.fetchone()[0]
            return {"id": iid}
    except HTTPException:
        raise
    except Exception as e:
        log.exception("upsert_institution failed: %s", e)
        raise HTTPException(500, str(e))


@router.delete("/institutions/{iid}")
def delete_institution(iid: int):
    from fastapi import HTTPException
    conn_obj = __import__('database.connection', fromlist=['get_connection']).get_connection()
    try:
        cur = conn_obj.cursor()
        cur.execute("SELECT COUNT(*) FROM Accounts WHERE Institutions_Id=%s", (iid,))
        accts = cur.fetchone()[0]
        if accts:
            raise HTTPException(400, f"Cannot delete: institution is linked to {accts} account(s). Unlink them first.")
        cur.execute("DELETE FROM Institutions WHERE Institutions_Id=%s", (iid,))
        conn_obj.commit()
        return {"deleted": iid}
    except HTTPException:
        conn_obj.rollback()
        raise
    except Exception as e:
        conn_obj.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn_obj.close()


@router.post("/accounts")
def upsert_account(data: dict):
    from database.connection import get_connection
    from fastapi import HTTPException
    conn = get_connection()
    try:
        cur = conn.cursor()
        aid = data.get('id')
        if aid:
            cur.execute("""
                UPDATE Accounts SET
                    Accounts_Name=%s, Accounts_Type=%s, IBAN=%s, Is_Active=%s,
                    Institutions_Id=%s, Currencies_Id=%s, Credit_Limit=%s, Accounts_Id_Linked=%s
                WHERE Accounts_Id=%s
            """, (data.get('name'), data.get('type'), data.get('iban') or None,
                  data.get('is_active', True),
                  data.get('institutions_id') or None,
                  data.get('currencies_id') or None,
                  data.get('credit_limit') or 0,
                  data.get('accounts_id_linked') or None,
                  aid))
        else:
            cur.execute("""
                INSERT INTO Accounts
                    (Accounts_Name, Accounts_Type, IBAN, Is_Active, Institutions_Id, Currencies_Id, Credit_Limit, Accounts_Id_Linked)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING Accounts_Id
            """, (data.get('name'), data.get('type'), data.get('iban') or None,
                  data.get('is_active', True),
                  data.get('institutions_id') or None,
                  data.get('currencies_id') or None,
                  data.get('credit_limit') or 0,
                  data.get('accounts_id_linked') or None))
            aid = cur.fetchone()[0]
        conn.commit()
        return {"id": aid}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.delete("/accounts/{aid}")
def delete_account(aid: int):
    from fastapi import HTTPException
    conn_obj = __import__('database.connection', fromlist=['get_connection']).get_connection()
    try:
        cur = conn_obj.cursor()
        cur.execute("SELECT COUNT(*) FROM Transactions WHERE Accounts_Id=%s OR Accounts_Id_Target=%s", (aid, aid))
        tx = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM Investments WHERE Accounts_Id=%s", (aid,))
        inv = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM Holdings WHERE Accounts_Id=%s", (aid,))
        hld = cur.fetchone()[0]
        if tx or inv or hld:
            parts = []
            if tx: parts.append(f"{tx} transaction(s)")
            if inv: parts.append(f"{inv} investment(s)")
            if hld: parts.append(f"{hld} holding(s)")
            raise HTTPException(400, f"Cannot delete: account has {', '.join(parts)}. Deactivate it instead (edit → uncheck Active).")
        # Safe to delete — clean up ancillary references first
        cur.execute("DELETE FROM Reconciliation_Sessions WHERE Accounts_Id=%s", (aid,))
        cur.execute("DELETE FROM Import_Statement_History WHERE Accounts_Id=%s", (aid,))
        cur.execute("DELETE FROM Recurring_Templates WHERE Accounts_Id=%s OR Accounts_Id_Target=%s", (aid, aid))
        cur.execute("DELETE FROM Transfer_Issues WHERE Accounts_Id_A=%s OR Accounts_Id_B=%s", (aid, aid))
        cur.execute("UPDATE Accounts SET Accounts_Id_Linked=NULL WHERE Accounts_Id_Linked=%s", (aid,))
        cur.execute("DELETE FROM Accounts WHERE Accounts_Id=%s", (aid,))
        conn_obj.commit()
        return {"deleted": aid}
    except HTTPException:
        conn_obj.rollback()
        raise
    except Exception as e:
        conn_obj.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn_obj.close()


@router.get("/accounts-master")
def get_accounts_master(search: Optional[str] = Query(None)):
    """Full account master data (all fields)."""
    clause = "AND (LOWER(a.Accounts_Name) LIKE %(s)s OR LOWER(a.Accounts_Type) LIKE %(s)s)" if search else ""
    params: dict = {}
    if search:
        params["s"] = f"%{search.lower()}%"
    with get_db() as conn:
        df = pd.read_sql(f"""
            SELECT a.Accounts_Id AS id, a.Accounts_Name AS name,
                   a.Accounts_Type AS type, a.Accounts_Balance AS balance,
                   a.Is_Active AS is_active, c.Currencies_ShortName AS currency,
                   a.Currencies_Id AS currencies_id,
                   i.Institutions_Name AS institution,
                   a.Institutions_Id AS institutions_id,
                   a.IBAN AS iban,
                   a.Credit_Limit AS credit_limit,
                   a.Accounts_Id_Linked AS accounts_id_linked,
                   la.Accounts_Name AS linked_account_name
            FROM Accounts a
            JOIN Currencies c ON a.Currencies_Id = c.Currencies_Id
            LEFT JOIN Institutions i ON a.Institutions_Id = i.Institutions_Id
            LEFT JOIN Accounts la ON a.Accounts_Id_Linked = la.Accounts_Id
            WHERE 1=1 {clause}
            ORDER BY a.Accounts_Type, a.Accounts_Name
        """, conn, params=params if params else None)
    return _df(df)


# ── Securities ────────────────────────────────────────────────────────────────

@router.get("/securities")
def get_securities_master(search: Optional[str] = Query(None)):
    clause = "AND (LOWER(s.Ticker) LIKE %(s)s OR LOWER(s.Securities_Name) LIKE %(s)s)" if search else ""
    params: dict = {}
    if search:
        params["s"] = f"%{search.lower()}%"
    with get_db() as conn:
        df = pd.read_sql(f"""
            SELECT s.Securities_Id AS id, s.Ticker AS ticker,
                   s.Securities_Name AS name, s.Securities_Type AS type,
                   c.Currencies_Id AS currency_id, c.Currencies_ShortName AS currency,
                   s.ISIN AS isin, s.Sector AS sector, s.Industry AS industry,
                   s.Is_Active AS is_active, s.Is_Tax_Exempt AS is_tax_exempt,
                   s.Tax_Category AS tax_category,
                   s.Yahoo_Ticker AS yahoo_ticker, s.TV_Symbol AS tv_symbol, s.TV_Exchange AS tv_exchange,
                   s.Maturity_Date AS maturity_date, s.Coupon_Rate AS coupon_rate,
                   s.Coupon_Frequency AS coupon_frequency, s.Face_Value AS face_value,
                   s.Dividend_Yield AS dividend_yield, s.Dividend_Rate AS dividend_rate,
                   s.Dividend_Frequency AS dividend_frequency,
                   s.Ex_Dividend_Date AS ex_dividend_date, s.Dividend_Pay_Date AS dividend_pay_date,
                   s.Payout_Ratio AS payout_ratio, s.Five_Year_Avg_Yield AS five_year_avg_yield,
                   s.Analyst_Rating AS analyst_rating, s.Analyst_Target_Price AS analyst_target_price,
                   COALESCE((SELECT Close FROM Historical_Prices WHERE Securities_Id = s.Securities_Id ORDER BY Date DESC LIMIT 1), 0) AS latest_price,
                   COALESCE((SELECT Date::text FROM Historical_Prices WHERE Securities_Id = s.Securities_Id ORDER BY Date DESC LIMIT 1), NULL) AS price_date,
                   COUNT(DISTINCT h.Holdings_Id) FILTER (WHERE h.Quantity != 0) AS held_in_accounts
            FROM Securities s
            JOIN Currencies c ON s.Currencies_Id = c.Currencies_Id
            LEFT JOIN Holdings h ON h.Securities_Id = s.Securities_Id
            WHERE 1=1 {clause}
            GROUP BY s.Securities_Id, s.Ticker, s.Securities_Name, s.Securities_Type,
                     c.Currencies_Id, c.Currencies_ShortName,
                     s.ISIN, s.Sector, s.Industry, s.Is_Active, s.Is_Tax_Exempt, s.Tax_Category,
                     s.Yahoo_Ticker, s.TV_Symbol, s.TV_Exchange,
                     s.Maturity_Date, s.Coupon_Rate, s.Coupon_Frequency, s.Face_Value,
                     s.Dividend_Yield, s.Dividend_Rate, s.Dividend_Frequency,
                     s.Ex_Dividend_Date, s.Dividend_Pay_Date,
                     s.Payout_Ratio, s.Five_Year_Avg_Yield,
                     s.Analyst_Rating, s.Analyst_Target_Price
            ORDER BY s.Ticker ASC
        """, conn, params=params if params else None)
    return _df(df)


@router.post("/securities")
def upsert_security(data: dict):
    from database.connection import get_connection
    from fastapi import HTTPException
    conn = get_connection()
    try:
        cur = conn.cursor()
        sid = data.get('id')
        def _s(k): return data.get(k) or None          # str → NULL if empty
        def _n(k): v = data.get(k); return float(v) if v not in (None, '', 'None') else None
        def _b(k, default=True): return str(data.get(k, str(default))).lower() not in ('false', '0', '')
        vals = (
            _s('ticker'), _s('name'), _s('type'),
            data.get('currencies_id') or None,
            _b('is_active'), _b('is_tax_exempt', False),
            _s('isin'), _s('sector'), _s('industry'),
            _s('yahoo_ticker'), _s('tv_symbol'), _s('tv_exchange'),
            _s('maturity_date'), _n('coupon_rate'), _s('coupon_frequency'), _n('face_value'),
            _n('dividend_yield'), _n('dividend_rate'), _s('dividend_frequency'),
            _s('ex_dividend_date'), _s('dividend_pay_date'),
            _n('payout_ratio'), _n('five_year_avg_yield'),
            _s('analyst_rating'), _n('analyst_target_price'),
            _s('tax_category'),
        )
        cols = """Ticker, Securities_Name, Securities_Type, Currencies_Id,
                  Is_Active, Is_Tax_Exempt, ISIN, Sector, Industry,
                  Yahoo_Ticker, TV_Symbol, TV_Exchange,
                  Maturity_Date, Coupon_Rate, Coupon_Frequency, Face_Value,
                  Dividend_Yield, Dividend_Rate, Dividend_Frequency,
                  Ex_Dividend_Date, Dividend_Pay_Date,
                  Payout_Ratio, Five_Year_Avg_Yield,
                  Analyst_Rating, Analyst_Target_Price,
                  Tax_Category"""
        placeholders = ','.join(['%s'] * len(vals))
        if sid:
            set_clause = ', '.join(f"{c.strip()}=%s" for c in cols.split(','))
            cur.execute(f"UPDATE Securities SET {set_clause} WHERE Securities_Id=%s", vals + (sid,))
        else:
            cur.execute(f"INSERT INTO Securities ({cols}) VALUES ({placeholders}) RETURNING Securities_Id", vals)
            sid = cur.fetchone()[0]
        conn.commit()
        return {"id": sid}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.delete("/securities/{sid}")
def delete_security(sid: int):
    from database.connection import get_connection
    from fastapi import HTTPException
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM Investments WHERE Securities_Id=%s", (sid,))
        inv = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM Holdings WHERE Securities_Id=%s", (sid,))
        hld = cur.fetchone()[0]
        if inv or hld:
            parts = []
            if inv: parts.append(f"{inv} investment transaction(s)")
            if hld: parts.append(f"{hld} holding(s)")
            raise HTTPException(400, f"Cannot delete: security has {', '.join(parts)}.")
        # safe to delete — cascade-clean ancillary tables first
        cur.execute("DELETE FROM Historical_Prices WHERE Securities_Id=%s", (sid,))
        cur.execute("DELETE FROM Securities_Dividends WHERE Securities_Id=%s", (sid,))
        cur.execute("DELETE FROM Alerts WHERE Securities_Id=%s", (sid,))
        cur.execute("DELETE FROM Watchlist WHERE Securities_Id=%s", (sid,))
        cur.execute("DELETE FROM Corporate_Actions WHERE Securities_Id=%s", (sid,))
        cur.execute("DELETE FROM Securities WHERE Securities_Id=%s", (sid,))
        conn.commit()
        return {"deleted": sid}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


# ── Currencies ────────────────────────────────────────────────────────────────

@router.get("/currencies")
def get_currencies_master():
    with get_db() as conn:
        df = pd.read_sql("""
            SELECT c.Currencies_Id AS id, c.Currencies_ShortName AS code,
                   c.Currencies_Name AS name, NULL AS symbol,
                   COALESCE((SELECT FX_Rate FROM Historical_FX WHERE Currencies_Id_1 = c.Currencies_Id ORDER BY Date DESC LIMIT 1), NULL) AS latest_rate,
                   COALESCE((SELECT Date::text FROM Historical_FX WHERE Currencies_Id_1 = c.Currencies_Id ORDER BY Date DESC LIMIT 1), NULL) AS rate_date
            FROM Currencies c
            ORDER BY c.Currencies_ShortName
        """, conn)
    return _df(df)


@router.post("/currencies")
def upsert_currency(data: dict):
    from database.connection import get_connection
    from fastapi import HTTPException
    conn = get_connection()
    try:
        cur = conn.cursor()
        cid = data.get('id')
        if cid:
            cur.execute("""
                UPDATE Currencies SET Currencies_ShortName=%s, Currencies_Name=%s
                WHERE Currencies_Id=%s
            """, (data.get('code'), data.get('name'), cid))
        else:
            cur.execute("""
                INSERT INTO Currencies (Currencies_ShortName, Currencies_Name)
                VALUES (%s, %s) RETURNING Currencies_Id
            """, (data.get('code'), data.get('name')))
            cid = cur.fetchone()[0]
        conn.commit()
        return {"id": cid}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.delete("/currencies/{cid}")
def delete_currency(cid: int):
    from database.connection import get_connection
    from fastapi import HTTPException
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM Accounts WHERE Currencies_Id=%s", (cid,))
        accts = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM Securities WHERE Currencies_Id=%s", (cid,))
        secs = cur.fetchone()[0]
        if accts or secs:
            parts = []
            if accts: parts.append(f"{accts} account(s)")
            if secs: parts.append(f"{secs} security/ies")
            raise HTTPException(400, f"Cannot delete: currency is used by {', '.join(parts)}.")
        cur.execute("DELETE FROM Historical_FX WHERE Currencies_Id_1=%s OR Currencies_Id_2=%s", (cid, cid))
        cur.execute("DELETE FROM Currencies WHERE Currencies_Id=%s", (cid,))
        conn.commit()
        return {"deleted": cid}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


# ── Tax Category Rules ────────────────────────────────────────────────────────

@router.get("/tax-category-rules")
def get_tax_category_rules():
    with get_db() as conn:
        df = pd.read_sql("""
            SELECT tax_category, display_name,
                   gains_taxable, gains_rate, gains_tax_code,
                   dividend_local_tax_rate, dividend_wht_creditable, reinvest_taxable,
                   income_tax_rate, show_in_capital_gains, notes
            FROM Tax_Category_Rules
            ORDER BY CASE tax_category
                WHEN 'Local Listed'   THEN 1 WHEN 'Foreign Listed' THEN 2
                WHEN 'UCITS'          THEN 3 WHEN 'Non-UCITS'      THEN 4
                WHEN 'CD'             THEN 5 WHEN 'Bond'           THEN 6
                WHEN 'Crypto'         THEN 7 WHEN 'Other'          THEN 8
                ELSE 9 END
        """, conn)
    return _df(df)


@router.post("/tax-category-rules")
def create_tax_category_rule(data: dict):
    from database.connection import get_connection
    conn = get_connection()
    try:
        cur = conn.cursor()
        def _n(k): v = data.get(k); return float(v) if v not in (None, '', 'None') else None
        def _b(k): v = data.get(k); return bool(v) if v is not None else None
        cur.execute("""
            INSERT INTO Tax_Category_Rules
                (tax_category, display_name, gains_taxable, gains_rate, gains_tax_code,
                 dividend_local_tax_rate, dividend_wht_creditable, reinvest_taxable,
                 income_tax_rate, show_in_capital_gains, notes)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            data.get('tax_category'), data.get('display_name'),
            _b('gains_taxable'), _n('gains_rate'), data.get('gains_tax_code') or None,
            _n('dividend_local_tax_rate'), _b('dividend_wht_creditable'), _b('reinvest_taxable'),
            _n('income_tax_rate'), data.get('show_in_capital_gains', True), data.get('notes') or None,
        ))
        conn.commit()
        return {"ok": True}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.put("/tax-category-rules/{tax_category}")
def update_tax_category_rule(tax_category: str, data: dict):
    from database.connection import get_connection
    conn = get_connection()
    try:
        cur = conn.cursor()
        def _n(k): v = data.get(k); return float(v) if v not in (None, '', 'None') else None
        def _b(k): v = data.get(k); return bool(v) if v is not None else None
        cur.execute("""
            UPDATE Tax_Category_Rules
            SET display_name            = %s,
                gains_taxable           = %s,
                gains_rate              = %s,
                gains_tax_code          = %s,
                dividend_local_tax_rate = %s,
                dividend_wht_creditable = %s,
                reinvest_taxable        = %s,
                income_tax_rate         = %s,
                show_in_capital_gains   = %s,
                notes                   = %s
            WHERE tax_category = %s
        """, (
            data.get('display_name'),
            _b('gains_taxable'), _n('gains_rate'), data.get('gains_tax_code') or None,
            _n('dividend_local_tax_rate'), _b('dividend_wht_creditable'), _b('reinvest_taxable'),
            _n('income_tax_rate'), data.get('show_in_capital_gains', True), data.get('notes') or None,
            tax_category,
        ))
        conn.commit()
        return {"ok": True}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.get("/instrument-type-overrides")
def get_instrument_type_overrides():
    with get_db() as conn:
        df = pd.read_sql("""
            SELECT instrument_type, tax_category_override, notes
            FROM Instrument_Type_Tax_Override
            ORDER BY instrument_type
        """, conn)
    return _df(df)


@router.post("/instrument-type-overrides")
def create_instrument_type_override(data: dict):
    from database.connection import get_connection
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO Instrument_Type_Tax_Override (instrument_type, tax_category_override, notes)
            VALUES (%s, %s, %s)
        """, (data.get('instrument_type'), data.get('tax_category_override') or None, data.get('notes') or None))
        conn.commit()
        return {"ok": True}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.put("/instrument-type-overrides/{instrument_type}")
def update_instrument_type_override(instrument_type: str, data: dict):
    from database.connection import get_connection
    conn = get_connection()
    try:
        cur = conn.cursor()
        override = data.get('tax_category_override') or None
        cur.execute("""
            UPDATE Instrument_Type_Tax_Override
            SET tax_category_override = %s,
                notes                 = %s
            WHERE instrument_type = %s
        """, (override, data.get('notes') or None, instrument_type))
        conn.commit()
        return {"ok": True}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()
