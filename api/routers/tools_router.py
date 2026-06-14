"""Admin tools endpoints: vacuum, backup, SQL shell, scheduler status."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import pandas as pd
from database.connection import get_db, get_connection

router = APIRouter()


@router.post("/vacuum")
def vacuum():
    conn = get_connection()
    try:
        conn.set_isolation_level(0)
        cur = conn.cursor()
        cur.execute("VACUUM ANALYZE")
        return {"message": "VACUUM ANALYZE completed"}
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.post("/backup")
def backup():
    try:
        from database.backup import run_backup
        result = run_backup()
        return {"message": result or "Backup completed"}
    except ImportError:
        raise HTTPException(503, "Backup module not available")
    except Exception as e:
        raise HTTPException(500, str(e))


class SqlRequest(BaseModel):
    sql: str


@router.post("/run-sql")
def run_sql(req: SqlRequest):
    sql = req.sql.strip()
    if not sql:
        raise HTTPException(400, "Empty SQL")
    forbidden = ("drop ", "truncate ", "delete from ", "alter table ")
    if any(sql.lower().startswith(f) for f in forbidden):
        raise HTTPException(400, "Destructive DDL/DML not allowed via this endpoint")
    try:
        with get_db() as conn:
            df = pd.read_sql(sql, conn)
        df = df.copy()
        for col in df.select_dtypes(include=["datetime", "datetimetz"]).columns:
            df[col] = df[col].astype(str)
        return df.where(pd.notnull(df), None).to_dict(orient="records")
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/sync-balances")
def sync_balances(data: dict = {}):
    """Run one or more balance sync jobs.

    Pass `target` in body: 'cash' | 'investment' | 'pension' | 'holdings' | 'all' (default).
    """
    target = (data or {}).get("target", "all")
    results = {}
    try:
        from database.crud import (
            update_accounts_balances,
            update_investment_balances,
            update_pension_balances,
            update_holdings,
        )
        if target in ("cash", "all"):
            update_accounts_balances()
            results["cash"] = "ok"
        if target in ("investment", "all"):
            update_investment_balances()
            results["investment"] = "ok"
        if target in ("pension", "all"):
            update_pension_balances()
            results["pension"] = "ok"
        if target in ("holdings", "all"):
            update_holdings()
            results["holdings"] = "ok"
        return {"synced": results}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/scheduler-status")
def scheduler_status():
    try:
        import schedule
        jobs = []
        for j in schedule.jobs:
            jobs.append({
                "job": str(j),
                "next_run": str(j.next_run) if j.next_run else None,
                "last_run": str(j.last_run) if j.last_run else None,
            })
        return {"jobs": jobs}
    except Exception:
        return {"jobs": [], "note": "Scheduler not running in this process"}
