"""Register API endpoints: transaction list per account + CRUD."""
from fastapi import APIRouter, Query, HTTPException
from typing import Optional, Any
from pydantic import BaseModel
import pandas as pd
from database.connection import get_db, get_connection

router = APIRouter()


def _df_to_list(df: pd.DataFrame) -> list:
    import math
    df = df.copy()
    for col in df.select_dtypes(include=["datetime", "datetimetz"]).columns:
        df[col] = df[col].astype(str)
    records = df.where(pd.notnull(df), other=None).to_dict(orient="records")
    return [{k: None if isinstance(v, float) and math.isnan(v) else v for k, v in r.items()} for r in records]


def _refresh_balance(cur, *account_ids: int) -> None:
    """Recalculate Accounts_Balance from confirmed Transactions for each account_id."""
    for acc_id in account_ids:
        if acc_id is None:
            continue
        cur.execute("""
            UPDATE Accounts
               SET Accounts_Balance = COALESCE((
                   SELECT SUM(Total_Amount)
                   FROM Transactions
                   WHERE Accounts_Id = %s AND Is_Draft = FALSE
               ), 0)
             WHERE Accounts_Id = %s
        """, (acc_id, acc_id))


@router.get("/transactions")
def get_transactions(
    account_id: int = Query(...),
    from_date: str = Query("2020-01-01"),
    to_date: str = Query("2099-12-31"),
    status: Optional[str] = Query(None),   # 'cleared' | 'uncleared' | None
    search: Optional[str] = Query(None),
    limit: int = Query(200),
    offset: int = Query(0),
):
    """Paginated transaction list with running balance for one account."""
    status_clause = ""
    if status == "cleared":
        status_clause = "AND t.cleared = TRUE"
    elif status == "uncleared":
        status_clause = "AND t.cleared = FALSE"

    search_clause = ""
    search_param = None
    if search:
        search_clause = "AND (t.description ILIKE %(search)s OR p.payees_name ILIKE %(search)s)"
        search_param = f"%{search}%"

    query = f"""
        WITH RECURSIVE cat_tree AS (
            SELECT categories_id, categories_name::TEXT AS full_path, categories_id_parent
            FROM Categories WHERE categories_id_parent IS NULL
            UNION ALL
            SELECT c.categories_id, ct.full_path || ' : ' || c.categories_name, c.categories_id_parent
            FROM Categories c JOIN cat_tree ct ON c.categories_id_parent = ct.categories_id
        ),
        all_txns AS (
            SELECT transactions_id,
                   SUM(total_amount) OVER (
                       ORDER BY date ASC, transactions_id ASC
                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                   ) AS cumulative_total,
                   SUM(total_amount) OVER () AS grand_total
            FROM Transactions
            WHERE accounts_id = %(account_id)s
        ),
        account_info AS (
            SELECT accounts_balance FROM Accounts WHERE accounts_id = %(account_id)s
        ),
        split_agg AS (
            SELECT
                s.transactions_id,
                COUNT(*) AS split_count,
                (ARRAY_AGG(s.categories_id ORDER BY s.splits_id))[1] AS categories_id,
                (ARRAY_AGG(s.memo ORDER BY s.splits_id))[1] AS memo,
                JSON_AGG(
                    JSON_BUILD_OBJECT('category', sct.full_path, 'amount', s.amount)
                    ORDER BY s.splits_id
                )::text AS splits_detail
            FROM Splits s
            LEFT JOIN cat_tree sct ON sct.categories_id = s.categories_id
            GROUP BY s.transactions_id
        )
        SELECT
            t.transactions_id AS id,
            t.date::text AS date,
            t.description AS description,
            t.total_amount AS amount,
            t.cleared AS cleared,
            t.reconciled AS reconciled,
            t.is_draft AS is_draft,
            t.payees_id AS payees_id,
            t.accounts_id_target AS accounts_id_target,
            t.transfers_id AS transfers_id,
            sa.memo AS memo,
            p.payees_name AS payee,
            ct.full_path AS category,
            COALESCE(sa.split_count, 0) AS split_count,
            sa.splits_detail AS splits,
            ta.accounts_name AS target_account,
            (ai.accounts_balance - at2.grand_total + at2.cumulative_total) AS running_balance
        FROM Transactions t
        JOIN all_txns at2 ON at2.transactions_id = t.transactions_id
        CROSS JOIN account_info ai
        LEFT JOIN Payees p ON t.payees_id = p.payees_id
        LEFT JOIN split_agg sa ON sa.transactions_id = t.transactions_id
        LEFT JOIN cat_tree ct ON ct.categories_id = sa.categories_id
        LEFT JOIN Accounts ta ON t.accounts_id_target = ta.accounts_id
        WHERE t.accounts_id = %(account_id)s
          AND t.date BETWEEN %(from_date)s AND %(to_date)s
          {status_clause}
          {search_clause}
        ORDER BY t.date DESC, t.transactions_id DESC
        LIMIT %(limit)s OFFSET %(offset)s
    """
    params = {
        "account_id": account_id,
        "from_date": from_date,
        "to_date": to_date,
        "limit": limit,
        "offset": offset,
    }
    if search_param:
        params["search"] = search_param

    with get_db() as conn:
        df = pd.read_sql(query, conn, params=params)

    if "splits" in df.columns:
        import json as _json
        df["splits"] = df["splits"].apply(lambda x: _json.loads(x) if isinstance(x, str) else [])

    # Total count for pagination
    count_query = f"""
        SELECT COUNT(*) AS total
        FROM Transactions t
        LEFT JOIN Payees p ON t.payees_id = p.payees_id
        WHERE t.accounts_id = %(account_id)s
          AND t.date BETWEEN %(from_date)s AND %(to_date)s
          {status_clause}
          {search_clause}
    """
    count_params = {k: v for k, v in params.items() if k in ("account_id", "from_date", "to_date")}
    if search_param:
        count_params["search"] = search_param

    with get_db() as conn:
        total = pd.read_sql(count_query, conn, params=count_params).iloc[0]["total"]

    return {"total": int(total), "transactions": _df_to_list(df)}


class TransactionIn(BaseModel):
    accounts_id: int
    date: str
    description: Optional[str] = None
    total_amount: float
    payees_id: Optional[int] = None
    categories_id: Optional[int] = None
    memo: Optional[str] = None
    cleared: bool = False
    reconciled: bool = False
    is_draft: bool = False
    accounts_id_target: Optional[int] = None


@router.post("/transactions")
def create_transaction(tx: TransactionIn):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO Transactions
                (Accounts_Id, Date, Description, Total_Amount, Payees_Id,
                 Cleared, Reconciled, Is_Draft, Accounts_Id_Target)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING Transactions_Id
        """, (tx.accounts_id, tx.date, tx.description, tx.total_amount,
              tx.payees_id, tx.cleared, tx.reconciled, tx.is_draft, tx.accounts_id_target))
        tx_id = cur.fetchone()[0]
        if tx.categories_id is not None or tx.memo is not None:
            cur.execute("""
                INSERT INTO Splits (Transactions_Id, Categories_Id, Amount, Memo)
                VALUES (%s, %s, %s, %s)
            """, (tx_id, tx.categories_id, tx.total_amount, tx.memo))
        # Create mirror leg for transfers
        if tx.accounts_id_target:
            cur.execute("SELECT nextval('transfers_id_seq')")
            shared_tid = cur.fetchone()[0]
            cur.execute("""
                INSERT INTO Transactions
                    (Accounts_Id, Date, Description, Total_Amount, Payees_Id, Cleared, Reconciled, Is_Draft,
                     Accounts_Id_Target, Transfers_Id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (tx.accounts_id_target, tx.date, tx.description, -float(tx.total_amount or 0),
                  tx.payees_id, tx.cleared, tx.reconciled, tx.is_draft, tx.accounts_id, shared_tid))
            cur.execute("UPDATE Transactions SET Transfers_Id = %s WHERE Transactions_Id = %s", (shared_tid, tx_id))
        if not tx.is_draft:
            _refresh_balance(cur, tx.accounts_id, tx.accounts_id_target)
        conn.commit()
        return {"id": tx_id}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.get("/transactions/{tx_id}")
def get_transaction(tx_id: int):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT t.transactions_id AS id,
                   t.accounts_id,
                   t.date::text AS date,
                   t.description,
                   t.total_amount,
                   t.payees_id,
                   t.is_draft,
                   t.cleared,
                   t.reconciled,
                   t.accounts_id_target AS transfer_account_id
            FROM Transactions t
            WHERE t.transactions_id = %s
        """, (tx_id,))
        row = cur.fetchone()
        if row is None:
            raise HTTPException(404, "Transaction not found")
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))
    finally:
        conn.close()


@router.put("/transactions/{tx_id}")
def update_transaction(tx_id: int, data: dict[str, Any]):
    tx_allowed = {"date", "description", "total_amount", "payees_id", "cleared", "reconciled", "is_draft", "accounts_id_target"}
    tx_updates = {k: v for k, v in data.items() if k in tx_allowed}
    split_updates = {k: v for k, v in data.items() if k in {"categories_id", "memo"}}

    if not tx_updates and not split_updates:
        raise HTTPException(400, "No valid fields to update")
    conn = get_connection()
    try:
        cur = conn.cursor()
        # Fetch current state of this transaction
        cur.execute("""
            SELECT Transfers_Id, Accounts_Id, Accounts_Id_Target
            FROM Transactions WHERE Transactions_Id = %s
        """, (tx_id,))
        row = cur.fetchone()
        if row is None:
            raise HTTPException(404, "Transaction not found")
        group_tid, src_account_id, old_target_id = row
        # Resolve the actual paired transaction using the shared Transfers_Id group key
        paired_id = None
        if group_tid:
            cur.execute(
                "SELECT Transactions_Id FROM Transactions WHERE Transfers_Id = %s AND Transactions_Id != %s LIMIT 1",
                (group_tid, tx_id),
            )
            prow = cur.fetchone()
            paired_id = prow[0] if prow else None

        # Update the main transaction
        if tx_updates:
            set_clause = ", ".join(f"{k} = %s" for k in tx_updates)
            cur.execute(f"UPDATE Transactions SET {set_clause} WHERE Transactions_Id = %s",
                        list(tx_updates.values()) + [tx_id])

        if split_updates:
            cur.execute("SELECT splits_id FROM Splits WHERE Transactions_Id = %s ORDER BY splits_id LIMIT 1", (tx_id,))
            srow = cur.fetchone()
            if srow:
                set_clause = ", ".join(f"{k} = %s" for k in split_updates)
                cur.execute(f"UPDATE Splits SET {set_clause} WHERE splits_id = %s",
                            list(split_updates.values()) + [srow[0]])
            else:
                cur.execute("INSERT INTO Splits (Transactions_Id, Categories_Id, Memo) VALUES (%s, %s, %s)",
                            (tx_id, split_updates.get("categories_id"), split_updates.get("memo")))

        # Re-read current state after update to get effective accounts_id_target
        new_target_id = tx_updates.get("accounts_id_target", old_target_id)

        if paired_id:
            if new_target_id is None:
                # Transfer target removed — delete the mirror leg
                cur.execute("DELETE FROM Splits WHERE Transactions_Id = %s", (paired_id,))
                cur.execute("DELETE FROM Transactions WHERE Transactions_Id = %s", (paired_id,))
                cur.execute("UPDATE Transactions SET Transfers_Id = NULL WHERE Transactions_Id = %s", (tx_id,))
            else:
                # Mirror existing paired leg
                mirror = {}
                for k in ("date", "description", "payees_id", "cleared", "reconciled", "is_draft"):
                    if k in tx_updates:
                        mirror[k] = tx_updates[k]
                if "total_amount" in tx_updates:
                    mirror["total_amount"] = -float(tx_updates["total_amount"])
                if "accounts_id_target" in tx_updates and int(new_target_id) != (old_target_id or 0):
                    mirror["accounts_id"] = int(new_target_id)
                    mirror["accounts_id_target"] = src_account_id
                if mirror:
                    set_clause = ", ".join(f"{k} = %s" for k in mirror)
                    cur.execute(f"UPDATE Transactions SET {set_clause} WHERE Transactions_Id = %s",
                                list(mirror.values()) + [paired_id])
        elif new_target_id:
            # No existing mirror but transfer target just set — create mirror leg
            cur.execute("""
                SELECT Date, Description, Total_Amount, Payees_Id, Cleared, Reconciled, Is_Draft
                FROM Transactions WHERE Transactions_Id = %s
            """, (tx_id,))
            tx_row = cur.fetchone()
            if tx_row:
                t_date, t_desc, t_amt, t_payees_id, t_cleared, t_reconciled, t_draft = tx_row
                cur.execute("SELECT nextval('transfers_id_seq')")
                shared_tid = cur.fetchone()[0]
                cur.execute("""
                    INSERT INTO Transactions
                        (Accounts_Id, Date, Description, Total_Amount, Payees_Id, Cleared, Reconciled, Is_Draft,
                         Accounts_Id_Target, Transfers_Id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (new_target_id, t_date, t_desc, -float(t_amt or 0),
                      t_payees_id, t_cleared, t_reconciled, t_draft, src_account_id, shared_tid))
                cur.execute("UPDATE Transactions SET Transfers_Id = %s WHERE Transactions_Id = %s", (shared_tid, tx_id))

        # Refresh balance for all accounts that may have been touched
        new_src = tx_updates.get("accounts_id", src_account_id)
        _refresh_balance(cur, src_account_id, new_src, old_target_id, new_target_id)
        conn.commit()
        return {"updated": tx_id}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.get("/transactions/{tx_id}/splits")
def get_splits(tx_id: int):
    with get_db() as conn:
        df = pd.read_sql("""
            WITH RECURSIVE cat_tree AS (
                SELECT categories_id, categories_name::TEXT AS full_path, categories_id_parent
                FROM Categories WHERE categories_id_parent IS NULL
                UNION ALL
                SELECT c.categories_id, ct.full_path || ' : ' || c.categories_name, c.categories_id_parent
                FROM Categories c JOIN cat_tree ct ON c.categories_id_parent = ct.categories_id
            )
            SELECT s.splits_id AS id, s.categories_id, ct.full_path AS category,
                   s.amount, s.memo
            FROM Splits s
            LEFT JOIN cat_tree ct ON ct.categories_id = s.categories_id
            WHERE s.transactions_id = %(tx_id)s
            ORDER BY s.splits_id
        """, conn, params={"tx_id": tx_id})
    return _df_to_list(df)


@router.put("/transactions/{tx_id}/splits")
def upsert_splits(tx_id: int, splits: list[dict]):
    conn = get_connection()
    try:
        cur = conn.cursor()
        # A non-transfer, non-draft transaction must end up with at least one
        # categorized split — otherwise it'd silently fall out of every spending
        # report. Transfers move money rather than categorize it, and drafts are
        # explicitly pending review, so both are exempt.
        cur.execute("SELECT Transfers_Id, Accounts_Id_Target, Is_Draft FROM Transactions WHERE Transactions_Id = %s", (tx_id,))
        row = cur.fetchone()
        is_transfer = row is not None and (row[0] is not None or row[1] is not None)
        is_draft = row is not None and bool(row[2])
        if not is_transfer and not is_draft and not any(sp.get("categories_id") is not None for sp in splits):
            raise HTTPException(400, "Choose a category before saving — only transfers can be left uncategorized")

        # Delete existing splits and re-insert
        cur.execute("DELETE FROM Splits WHERE Transactions_Id = %s", (tx_id,))
        for sp in splits:
            cur.execute(
                "INSERT INTO Splits (Transactions_Id, Categories_Id, Amount, Memo) VALUES (%s, %s, %s, %s)",
                (tx_id, sp.get("categories_id"), sp.get("amount"), sp.get("memo"))
            )
        conn.commit()
        return {"updated": tx_id}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.delete("/transactions/{tx_id}")
def delete_transaction(tx_id: int):
    conn = get_connection()
    try:
        cur = conn.cursor()
        # Find paired transfer transaction (if any)
        cur.execute("SELECT Transfers_Id FROM Transactions WHERE Transactions_Id = %s", (tx_id,))
        row = cur.fetchone()
        if row is None:
            raise HTTPException(404, "Transaction not found")
        group_tid = row[0]
        # Resolve actual paired leg via shared group key
        paired_id = None
        if group_tid:
            cur.execute(
                "SELECT Transactions_Id FROM Transactions WHERE Transfers_Id = %s AND Transactions_Id != %s LIMIT 1",
                (group_tid, tx_id),
            )
            prow = cur.fetchone()
            paired_id = prow[0] if prow else None
        # Delete paired leg first (to avoid FK issues with Transfers_Id)
        if paired_id:
            cur.execute("DELETE FROM Splits WHERE Transactions_Id = %s", (paired_id,))
            cur.execute("DELETE FROM Transactions WHERE Transactions_Id = %s", (paired_id,))
        # Collect affected accounts before deletion
        cur.execute(
            "SELECT Accounts_Id, Accounts_Id_Target FROM Transactions WHERE Transactions_Id = %s", (tx_id,)
        )
        tx_acc_row = cur.fetchone()
        affected_accounts = list(tx_acc_row) if tx_acc_row else []

        # Delete the requested transaction
        cur.execute("DELETE FROM Splits WHERE Transactions_Id = %s", (tx_id,))
        cur.execute("DELETE FROM Transactions WHERE Transactions_Id = %s", (tx_id,))
        _refresh_balance(cur, *affected_accounts)
        conn.commit()
        return {"deleted": tx_id, "paired_deleted": paired_id}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.post("/transfers")
def create_transfer(data: dict):
    """Create a paired transfer between two accounts (two linked transactions)."""
    from_account = data.get("from_account_id")
    to_account = data.get("to_account_id")
    date = data.get("date")
    amount = data.get("amount")
    description = data.get("description")
    cleared = data.get("cleared", False)
    reconciled = data.get("reconciled", False)
    is_draft = data.get("is_draft", False)
    payees_id = data.get("payees_id")

    if not from_account or not to_account or not date or amount is None:
        raise HTTPException(400, "from_account_id, to_account_id, date and amount are required")

    conn = get_connection()
    try:
        cur = conn.cursor()
        # Get a shared transfer group ID
        cur.execute("SELECT nextval('transfers_id_seq')")
        shared_tid = cur.fetchone()[0]

        # Outflow from source account (negative)
        cur.execute("""
            INSERT INTO Transactions
                (Accounts_Id, Date, Description, Total_Amount, Payees_Id, Cleared, Reconciled, Is_Draft, Accounts_Id_Target, Transfers_Id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING Transactions_Id
        """, (from_account, date, description, -abs(amount), payees_id, cleared, reconciled, is_draft, to_account, shared_tid))
        from_id = cur.fetchone()[0]

        # Inflow to target account (positive)
        cur.execute("""
            INSERT INTO Transactions
                (Accounts_Id, Date, Description, Total_Amount, Payees_Id, Cleared, Reconciled, Is_Draft, Accounts_Id_Target, Transfers_Id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING Transactions_Id
        """, (to_account, date, description, abs(amount), payees_id, cleared, reconciled, is_draft, from_account, shared_tid))
        to_id = cur.fetchone()[0]

        # Both legs carry Accounts_Id_Target, so the balance-maintaining trigger (which
        # predates this two-row transfer model) double-applies the transfer amount to
        # each account — once via the row's own Total_Amount, once via the other row's
        # Accounts_Id_Target branch. An explicit recompute overwrites that with the
        # correct SUM(), same as every other balance-affecting endpoint in this file.
        if not is_draft:
            _refresh_balance(cur, from_account, to_account)
        conn.commit()
        return {"from_id": from_id, "to_id": to_id}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.get("/search")
def search_all_transactions(
    q: str = Query(..., min_length=2),
    limit: int = Query(50),
):
    """Full-text search across all accounts and transactions."""
    query = """
        WITH RECURSIVE cat_tree AS (
            SELECT categories_id, categories_name::TEXT AS full_path, categories_id_parent
            FROM Categories WHERE categories_id_parent IS NULL
            UNION ALL
            SELECT c.categories_id, ct.full_path || ' : ' || c.categories_name, c.categories_id_parent
            FROM Categories c JOIN cat_tree ct ON c.categories_id_parent = ct.categories_id
        ),
        first_split AS (
            SELECT DISTINCT ON (transactions_id)
                   transactions_id, categories_id, memo
            FROM Splits
            ORDER BY transactions_id, splits_id
        )
        SELECT
            t.transactions_id AS id,
            t.accounts_id,
            a.accounts_name AS account_name,
            t.date::text AS date,
            t.description,
            t.total_amount AS amount,
            cur.currencies_shortname AS currency,
            p.payees_name AS payee,
            ct.full_path AS category
        FROM Transactions t
        JOIN Accounts a ON a.accounts_id = t.accounts_id
        LEFT JOIN Currencies cur ON a.currencies_id = cur.currencies_id
        LEFT JOIN Payees p ON t.payees_id = p.payees_id
        LEFT JOIN first_split fs ON fs.transactions_id = t.transactions_id
        LEFT JOIN cat_tree ct ON ct.categories_id = fs.categories_id
        WHERE t.description ILIKE %(q)s
           OR p.payees_name ILIKE %(q)s
           OR ct.full_path ILIKE %(q)s
        ORDER BY t.date DESC, t.transactions_id DESC
        LIMIT %(limit)s
    """
    with get_db() as conn:
        df = pd.read_sql(query, conn, params={"q": f"%{q}%", "limit": limit})
    return _df_to_list(df)


@router.post("/clear")
def clear_transactions(data: dict):
    """Mark all pending (uncleared, non-draft) transactions up to a date as Cleared."""
    account_id = data.get("account_id")
    up_to_date = data.get("up_to_date")
    if not account_id or not up_to_date:
        raise HTTPException(400, "account_id and up_to_date required")
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE Transactions SET Cleared = TRUE
            WHERE Accounts_Id = %s AND Date <= %s AND Cleared = FALSE AND Is_Draft = FALSE
        """, (account_id, up_to_date))
        updated = cur.rowcount
        conn.commit()
        return {"cleared": updated}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.post("/reconcile")
def reconcile_transactions(data: dict):
    """Mark all cleared (non-draft) transactions up to a date as Reconciled."""
    account_id = data.get("account_id")
    up_to_date = data.get("up_to_date")
    if not account_id or not up_to_date:
        raise HTTPException(400, "account_id and up_to_date required")
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE Transactions SET Reconciled = TRUE
            WHERE Accounts_Id = %s AND Date <= %s AND Cleared = TRUE AND Reconciled = FALSE AND Is_Draft = FALSE
        """, (account_id, up_to_date))
        updated = cur.rowcount
        conn.commit()
        return {"reconciled": updated}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()
