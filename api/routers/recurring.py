"""Recurring transaction template endpoints."""
from fastapi import APIRouter, HTTPException
from typing import Optional
import math
import pandas as pd
from database.connection import get_db, get_connection

router = APIRouter()


def _df(df: pd.DataFrame) -> list:
    df = df.copy()
    for col in df.select_dtypes(include=["datetime", "datetimetz"]).columns:
        df[col] = df[col].astype(str)
    records = df.where(pd.notnull(df), other=None).to_dict(orient="records")
    return [{k: None if isinstance(v, float) and math.isnan(v) else v for k, v in r.items()} for r in records]


@router.get("/templates")
def get_templates():
    with get_db() as conn:
        df = pd.read_sql("""
            SELECT
                rt.templates_id AS id,
                rt.name AS name,
                rt.periodicity AS frequency,
                rt.active AS is_active,
                rt.next_due_date::text AS next_date,
                rt.end_date::text AS end_date,
                rt.total_amount AS total_amount,
                rt.description AS description,
                rt.auto_confirm AS auto_confirm,
                rt.accounts_id AS account_id,
                rt.payees_id AS payee_id,
                rt.accounts_id_target AS accounts_id_target,
                a.accounts_name AS account_name,
                p.payees_name AS payee_name
            FROM Recurring_Templates rt
            LEFT JOIN Accounts a ON rt.accounts_id = a.accounts_id
            LEFT JOIN Payees p ON rt.payees_id = p.payees_id
            ORDER BY rt.active DESC, rt.next_due_date ASC NULLS LAST
        """, conn)
    return _df(df)


@router.get("/templates/{template_id}/splits")
def get_template_splits(template_id: int):
    with get_db() as conn:
        df = pd.read_sql("""
            SELECT rts.splits_id AS id, rts.categories_id,
                   c.categories_name AS category_name, rts.amount, rts.memo
            FROM Recurring_Template_Splits rts
            LEFT JOIN Categories c ON c.categories_id = rts.categories_id
            WHERE rts.templates_id = %(tid)s
            ORDER BY rts.splits_id
        """, conn, params={"tid": template_id})
    return _df(df)


@router.post("/templates")
def create_template(data: dict):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO Recurring_Templates
                (name, accounts_id, payees_id, description, total_amount, periodicity,
                 next_due_date, end_date, auto_confirm, active, accounts_id_target)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING templates_id
        """, (
            data.get("name"), data.get("accounts_id"), data.get("payees_id"),
            data.get("description"), data.get("total_amount"), data.get("periodicity"),
            data.get("next_due_date") or None, data.get("end_date") or None,
            data.get("auto_confirm", False), data.get("active", True),
            data.get("accounts_id_target"),
        ))
        tid = cur.fetchone()[0]
        splits = data.get("splits", [])
        for sp in splits:
            cur.execute(
                "INSERT INTO Recurring_Template_Splits (templates_id, categories_id, amount, memo) VALUES (%s, %s, %s, %s)",
                (tid, sp.get("categories_id"), sp.get("amount"), sp.get("memo"))
            )
        conn.commit()
        return {"id": tid}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.put("/templates/{template_id}")
def update_template(template_id: int, data: dict):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE Recurring_Templates SET
                name=%s, accounts_id=%s, payees_id=%s, description=%s,
                total_amount=%s, periodicity=%s, next_due_date=%s,
                end_date=%s, auto_confirm=%s, active=%s, accounts_id_target=%s
            WHERE templates_id=%s
        """, (
            data.get("name"), data.get("accounts_id"), data.get("payees_id"),
            data.get("description"), data.get("total_amount"), data.get("periodicity"),
            data.get("next_due_date") or None, data.get("end_date") or None,
            data.get("auto_confirm", False), data.get("active", True),
            data.get("accounts_id_target"), template_id,
        ))
        splits = data.get("splits")
        if splits is not None:
            cur.execute("DELETE FROM Recurring_Template_Splits WHERE templates_id = %s", (template_id,))
            for sp in splits:
                cur.execute(
                    "INSERT INTO Recurring_Template_Splits (templates_id, categories_id, amount, memo) VALUES (%s, %s, %s, %s)",
                    (template_id, sp.get("categories_id"), sp.get("amount"), sp.get("memo"))
                )
        conn.commit()
        return {"updated": template_id}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.delete("/templates/{template_id}")
def delete_template(template_id: int):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM Recurring_Template_Splits WHERE templates_id = %s", (template_id,))
        cur.execute("DELETE FROM Recurring_Templates WHERE templates_id = %s", (template_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Not found")
        conn.commit()
        return {"deleted": template_id}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.post("/templates/{template_id}/run")
def run_template(template_id: int):
    """Manually trigger a recurring template to create a transaction."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        # Fetch template
        cur.execute("""
            SELECT rt.*
            FROM Recurring_Templates rt
            WHERE rt.templates_id = %s AND rt.active = TRUE
        """, (template_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Template not found or inactive")

        cols = [d[0].lower() for d in cur.description]
        tmpl = dict(zip(cols, row))

        # Insert draft transaction (no categories_id on Transactions — goes in Splits if needed)
        cur.execute("""
            INSERT INTO Transactions
                (accounts_id, date, description, total_amount, payees_id, templates_id, is_draft, cleared)
            VALUES (%s, CURRENT_DATE, %s, %s, %s, %s, TRUE, FALSE)
            RETURNING transactions_id
        """, (
            tmpl["accounts_id"],
            tmpl.get("description") or tmpl.get("name"),
            tmpl.get("total_amount"),
            tmpl.get("payees_id"),
            template_id,
        ))
        tx_id = cur.fetchone()[0]
        # no last_run column in schema
        conn.commit()
        return {"transaction_id": tx_id, "message": "Draft transaction created"}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.get("/drafts")
def get_drafts():
    """All pending draft transactions (Is_Draft=TRUE) linked to recurring templates."""
    with get_db() as conn:
        df = pd.read_sql("""
            SELECT t.Transactions_Id AS id,
                   t.Date::text AS date,
                   t.Total_Amount AS amount,
                   t.Description AS description,
                   t.Accounts_Id AS accounts_id,
                   t.Payees_Id AS payees_id,
                   a.Accounts_Name AS account,
                   p.Payees_Name AS payee,
                   rt.name AS template_name,
                   rt.periodicity AS template_periodicity,
                   t.templates_id AS template_id,
                   t.Accounts_Id_Target AS accounts_id_target
            FROM Transactions t
            LEFT JOIN Accounts a ON a.Accounts_Id = t.Accounts_Id
            LEFT JOIN Payees p ON p.Payees_Id = t.Payees_Id
            LEFT JOIN Recurring_Templates rt ON rt.templates_id = t.templates_id
            WHERE t.Is_Draft = TRUE
            ORDER BY t.Date ASC, t.Transactions_Id ASC
        """, conn)
    return _df(df)


@router.post("/generate-drafts")
def generate_drafts():
    """Generate draft transactions for all templates due today or earlier."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT templates_id, accounts_id, payees_id, description, name,
                   total_amount, periodicity, next_due_date, auto_confirm, accounts_id_target
            FROM Recurring_Templates
            WHERE active = TRUE
              AND (next_due_date IS NULL OR next_due_date <= CURRENT_DATE)
              AND (end_date IS NULL OR end_date >= CURRENT_DATE)
        """)
        templates = cur.fetchall()
        cols = [d[0] for d in cur.description]
        generated = 0
        for row in templates:
            tmpl = dict(zip(cols, row))
            tid = tmpl["templates_id"]
            is_auto = bool(tmpl.get("auto_confirm"))
            # Insert draft (or confirmed if auto_confirm)
            cur.execute("""
                INSERT INTO Transactions
                    (Accounts_Id, Date, Payees_Id, Description, Total_Amount,
                     Cleared, Is_Draft, templates_id, Accounts_Id_Target)
                VALUES (%s, CURRENT_DATE, %s, %s, %s, FALSE, %s, %s, %s)
                RETURNING Transactions_Id
            """, (
                tmpl["accounts_id"],
                tmpl.get("payees_id"),
                tmpl.get("description") or tmpl.get("name"),
                tmpl.get("total_amount"),
                not is_auto,
                tid,
                tmpl.get("accounts_id_target"),
            ))
            tx_id = cur.fetchone()[0]
            # Copy splits from template
            cur.execute("""
                INSERT INTO Splits (Transactions_Id, Categories_Id, Amount, Memo)
                SELECT %s, rts.Categories_Id, rts.Amount, rts.Memo
                FROM Recurring_Template_Splits rts WHERE rts.templates_id = %s
            """, (tx_id, tid))
            # Advance next_due_date
            cur.execute("""
                UPDATE Recurring_Templates SET next_due_date = CASE periodicity
                    WHEN 'Daily'        THEN next_due_date + INTERVAL '1 day'
                    WHEN 'Weekly'       THEN next_due_date + INTERVAL '1 week'
                    WHEN 'Bi-Weekly'    THEN next_due_date + INTERVAL '2 weeks'
                    WHEN 'Monthly'      THEN next_due_date + INTERVAL '1 month'
                    WHEN 'Bi-Monthly'   THEN next_due_date + INTERVAL '2 months'
                    WHEN 'Quarterly'    THEN next_due_date + INTERVAL '3 months'
                    WHEN 'Semi-Annual'  THEN next_due_date + INTERVAL '6 months'
                    WHEN 'Annual'       THEN next_due_date + INTERVAL '1 year'
                    ELSE NULL END
                WHERE templates_id = %s
            """, (tid,))
            generated += 1
        conn.commit()
        return {"generated": generated}
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.put("/drafts/{tx_id}")
def update_draft(tx_id: int, data: dict):
    """Update editable fields of a pending draft, including splits."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE Transactions
            SET date=%s, description=%s, total_amount=%s, payees_id=%s, accounts_id=%s, accounts_id_target=%s
            WHERE Transactions_Id=%s AND Is_Draft=TRUE
        """, (
            data.get("date") or None,
            data.get("description") or None,
            data.get("amount") if data.get("amount") is not None else None,
            data.get("payees_id") or None,
            data.get("accounts_id") or None,
            data.get("accounts_id_target") or None,
            tx_id,
        ))
        if cur.rowcount == 0:
            raise HTTPException(404, "Draft not found")
        splits = data.get("splits")
        if splits is not None:
            cur.execute("DELETE FROM Splits WHERE Transactions_Id = %s", (tx_id,))
            for sp in splits:
                cur.execute(
                    "INSERT INTO Splits (Transactions_Id, Categories_Id, Amount, Memo) VALUES (%s, %s, %s, %s)",
                    (tx_id, sp.get("categories_id") or None, sp.get("amount"), sp.get("memo") or None),
                )
        conn.commit()
        return {"updated": tx_id}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.post("/drafts/{tx_id}/confirm")
def confirm_draft(tx_id: int):
    """Confirm a single draft transaction. For transfers, creates the mirror leg."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT Accounts_Id, Date, Description, Total_Amount, Payees_Id, Accounts_Id_Target, Transfers_Id
            FROM Transactions WHERE Transactions_Id = %s AND Is_Draft = TRUE
        """, (tx_id,))
        row = cur.fetchone()
        if row is None:
            raise HTTPException(404, "Draft not found")
        src_account, date, description, amount, payees_id, target_account, existing_transfers_id = row

        cur.execute("UPDATE Transactions SET Is_Draft = FALSE WHERE Transactions_Id = %s", (tx_id,))

        if target_account and not existing_transfers_id:
            cur.execute("SELECT nextval('transfers_id_seq')")
            shared_tid = cur.fetchone()[0]
            # Create the mirror inflow leg in the target account
            cur.execute("""
                INSERT INTO Transactions
                    (Accounts_Id, Date, Description, Total_Amount, Payees_Id, Cleared, Reconciled, Is_Draft, Accounts_Id_Target, Transfers_Id)
                VALUES (%s, %s, %s, %s, %s, FALSE, FALSE, FALSE, %s, %s)
            """, (target_account, date, description, abs(float(amount or 0)), payees_id, src_account, shared_tid))
            cur.execute("UPDATE Transactions SET Transfers_Id = %s WHERE Transactions_Id = %s", (shared_tid, tx_id))

        conn.commit()
        return {"confirmed": tx_id}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.delete("/drafts/{tx_id}")
def delete_draft(tx_id: int):
    """Delete a pending draft transaction."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM Splits WHERE Transactions_Id = %s", (tx_id,))
        cur.execute("DELETE FROM Transactions WHERE Transactions_Id = %s AND Is_Draft = TRUE", (tx_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Draft not found")
        conn.commit()
        return {"deleted": tx_id}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()
