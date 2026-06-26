"""Admin tools endpoints — mirrors ui/tools.py functionality."""
import re
import io
import json
import math
import os
import threading
from typing import Optional, List
from fastapi import APIRouter, HTTPException, UploadFile, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import pandas as pd
from database.connection import get_db, get_connection

router = APIRouter()

_DDL_PATTERN = re.compile(
    r"^\s*(DROP|TRUNCATE|ALTER|CREATE|REPLACE|MERGE|GRANT|REVOKE|CALL|DO)\b",
    re.IGNORECASE,
)
_DML_PATTERN = re.compile(r"^\s*(INSERT|UPDATE|DELETE)\b", re.IGNORECASE)


def _df_records(df: pd.DataFrame) -> list:
    df = df.copy()
    for col in df.select_dtypes(include=["datetime", "datetimetz"]).columns:
        df[col] = df[col].astype(str)
    for col in df.select_dtypes(include=["object"]).columns:
        df[col] = df[col].where(pd.notnull(df[col]), None)
    records = df.where(pd.notnull(df), None).to_dict(orient="records")
    # Replace any remaining float nan/inf that survive to_dict (JSON incompatible)
    for rec in records:
        for k, v in rec.items():
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                rec[k] = None
    return records


# ── DB Health ──────────────────────────────────────────────────────────────────

@router.get("/db-health")
def db_health():
    with get_db() as conn:
        df_health = pd.read_sql("""
            SELECT
                relname AS table_name,
                pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
                pg_size_pretty(pg_relation_size(relid)) AS table_size,
                pg_size_pretty(pg_total_relation_size(relid) - pg_relation_size(relid)) AS index_size,
                n_live_tup AS live_rows,
                n_dead_tup AS dead_rows,
                CASE WHEN n_live_tup > 0
                     THEN ROUND(n_dead_tup::numeric / n_live_tup * 100, 1)
                     ELSE 0 END AS dead_pct,
                last_vacuum::text,
                last_autovacuum::text,
                last_analyze::text,
                last_autoanalyze::text
            FROM pg_stat_user_tables
            ORDER BY n_dead_tup DESC, pg_total_relation_size(relid) DESC
        """, conn)
        df_idx = pd.read_sql("""
            SELECT
                relname AS table_name,
                indexrelname AS index_name,
                pg_size_pretty(pg_relation_size(indexrelid)) AS index_size,
                idx_scan AS scans,
                idx_tup_read AS tuples_read,
                idx_tup_fetch AS tuples_fetched
            FROM pg_stat_user_indexes
            ORDER BY idx_scan ASC, pg_relation_size(indexrelid) DESC
        """, conn)
    return {"tables": _df_records(df_health), "indexes": _df_records(df_idx)}


# ── DB Maintenance ─────────────────────────────────────────────────────────────

class MaintenanceRequest(BaseModel):
    operation: str
    table: Optional[str] = None
    db_name: Optional[str] = None

VALID_OPS = {"ANALYZE", "VACUUM", "VACUUM ANALYZE", "VACUUM FULL", "REINDEX TABLE", "REINDEX DATABASE"}

@router.post("/db-maintenance")
def db_maintenance(req: MaintenanceRequest):
    if req.operation not in VALID_OPS:
        raise HTTPException(400, f"Unknown operation. Valid: {sorted(VALID_OPS)}")
    if req.operation == "REINDEX DATABASE":
        db_name = req.db_name or ""
        sql = f'REINDEX DATABASE "{db_name}";' if db_name else "REINDEX DATABASE;"
    elif req.operation == "REINDEX TABLE":
        if not req.table:
            raise HTTPException(400, "table required for REINDEX TABLE")
        sql = f'REINDEX TABLE "{req.table}";'
    elif req.table:
        sql = f'{req.operation} "{req.table}";'
    else:
        sql = f"{req.operation};"
    conn = get_connection()
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(sql)
        return {"message": f"{req.operation} completed successfully"}
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        conn.close()


# ── Referential Integrity ──────────────────────────────────────────────────────

@router.get("/referential-integrity")
def referential_integrity():
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                tc.constraint_name,
                tc.table_name AS child_table,
                kcu.column_name AS child_col,
                ccu.table_name AS parent_table,
                ccu.column_name AS parent_col
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
                ON kcu.constraint_name = tc.constraint_name AND kcu.table_schema = tc.table_schema
            JOIN information_schema.constraint_column_usage ccu
                ON ccu.constraint_name = tc.constraint_name AND ccu.table_schema = tc.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public'
            ORDER BY tc.table_name, tc.constraint_name
        """)
        fks = cur.fetchall()
        results = []
        for row in fks:
            constraint, child_t, child_c, parent_t, parent_c = row
            try:
                cur.execute(f"""
                    SELECT COUNT(*) FROM "{child_t}" c
                    WHERE c."{child_c}" IS NOT NULL
                      AND NOT EXISTS (SELECT 1 FROM "{parent_t}" p WHERE p."{parent_c}" = c."{child_c}")
                """)
                orphan_count = cur.fetchone()[0]
            except Exception as e:
                orphan_count = f"error: {e}"
            results.append({
                "constraint": constraint,
                "child_table": child_t,
                "child_col": child_c,
                "parent_table": parent_t,
                "parent_col": parent_c,
                "orphaned_rows": orphan_count,
            })
        return results
    finally:
        cur.close()
        conn.close()


# ── SQL Interface ──────────────────────────────────────────────────────────────

class SqlRequest(BaseModel):
    sql: str

@router.post("/run-sql")
def run_sql(req: SqlRequest):
    sql = req.sql.strip()
    if not sql:
        raise HTTPException(400, "Empty SQL")
    if _DDL_PATTERN.match(sql):
        raise HTTPException(400, "DDL statements (DROP, TRUNCATE, ALTER, CREATE, …) are not allowed.")
    if _DML_PATTERN.match(sql):
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(sql)
            rows_affected = cur.rowcount
            conn.commit()
            return {"type": "dml", "rows_affected": rows_affected}
        except Exception as e:
            conn.rollback()
            raise HTTPException(400, str(e))
        finally:
            conn.close()
    else:
        try:
            with get_db() as conn:
                df = pd.read_sql(sql, conn)
            return {"type": "select", "rows": _df_records(df)}
        except Exception as e:
            raise HTTPException(400, str(e))


# ── Data Export ────────────────────────────────────────────────────────────────

@router.get("/export-excel")
def export_excel():
    from database.queries import export_all_data
    data = export_all_data()
    if not data:
        raise HTTPException(500, "No data returned")
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for sheet_name, df in data.items():
            df.to_excel(writer, sheet_name=sheet_name[:31], index=False)
    buf.seek(0)
    import datetime
    fname = f"oikos_export_{datetime.date.today()}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ── Vacuum / Backup ────────────────────────────────────────────────────────────

@router.post("/vacuum")
def vacuum():
    conn = get_connection()
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("VACUUM ANALYZE")
        return {"message": "VACUUM ANALYZE completed"}
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        conn.close()


@router.get("/backup/db-info")
def backup_db_info():
    """Return DB connection details and per-table sizes for the Backup UI."""
    try:
        from database.backup import DatabaseBackup
        bm = DatabaseBackup()
        df = bm.get_table_sizes()
        tables = df[['table_name', 'size', 'size_bytes']].to_dict(orient='records')
        total_mb = round(df['size_bytes'].sum() / (1024 * 1024), 2)
        return {
            "db_name": bm.db_config['dbname'],
            "db_user": bm.db_config['user'],
            "db_host": bm.db_config['host'],
            "db_port": bm.db_config['port'],
            "total_mb": total_mb,
            "tables": tables,
        }
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/backup")
def backup(custom_name: str = "", exclude_blobs: bool = False):
    try:
        from database.backup import DatabaseBackup
        bm = DatabaseBackup()
        result = bm.create_backup(
            backup_name=custom_name or None,
            include_blobs=not exclude_blobs,
        )
        if result.get("success"):
            return {
                "success": True,
                "filename": result["filename"],
                "size_mb": round(result["size_mb"], 2),
                "message": result.get("message", ""),
            }
        raise HTTPException(500, result.get("message", "Backup failed"))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/backup/list")
def list_backups():
    try:
        from database.backup import DatabaseBackup
        backups = DatabaseBackup().get_backup_history()
        return [
            {
                "filename": b["filename"],
                "size_mb": round(b["size_mb"], 2),
                "modified": b["modified"].isoformat(),
            }
            for b in backups
        ]
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/backup/download/{filename}")
def download_backup(filename: str):
    from fastapi.responses import FileResponse
    import re
    if not re.match(r'^[\w.\- ]+\.dump$', filename):
        raise HTTPException(400, "Invalid filename")
    try:
        from database.backup import DatabaseBackup
        bm = DatabaseBackup()
        import os
        path = os.path.join(bm.backup_dir, filename)
        if not os.path.isfile(path):
            raise HTTPException(404, "Backup not found")
        return FileResponse(path, media_type="application/octet-stream", filename=filename)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.delete("/backup/{filename}")
def delete_backup(filename: str):
    import re
    if not re.match(r'^[\w.\- ]+\.dump$', filename):
        raise HTTPException(400, "Invalid filename")
    try:
        from database.backup import DatabaseBackup
        result = DatabaseBackup().delete_backup(filename)
        if result.get("success"):
            return {"message": result["message"]}
        raise HTTPException(400, result.get("message", "Delete failed"))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/backup/restore/{filename}")
def restore_backup(filename: str):
    import re
    if not re.match(r'^[\w.\- ]+\.dump$', filename):
        raise HTTPException(400, "Invalid filename")
    try:
        from database.backup import DatabaseBackup
        import os
        bm = DatabaseBackup()
        path = os.path.join(bm.backup_dir, filename)
        if not os.path.isfile(path):
            raise HTTPException(404, "Backup not found")
        result = bm.restore_backup(path)
        if result.get("success"):
            return {"message": result["message"]}
        raise HTTPException(500, result.get("message", "Restore failed"))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/backup/restore-upload")
async def restore_backup_upload(file: UploadFile):
    import tempfile, os
    if not file.filename or not file.filename.endswith(".dump"):
        raise HTTPException(400, "File must be a .dump file")
    try:
        from database.backup import DatabaseBackup
        data = await file.read()
        with tempfile.NamedTemporaryFile(suffix=".dump", delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            result = DatabaseBackup().restore_backup(tmp_path)
        finally:
            os.unlink(tmp_path)
        if result.get("success"):
            return {"message": result["message"]}
        raise HTTPException(500, result.get("message", "Restore failed"))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Sync Balances ──────────────────────────────────────────────────────────────

@router.post("/sync-balances")
def sync_balances(data: dict = {}):
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


# ── Scheduler Status ───────────────────────────────────────────────────────────

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


# ── Price Anomalies ────────────────────────────────────────────────────────────

@router.get("/price-anomalies")
def price_anomalies(threshold: float = 100.0):
    from database.queries import get_price_anomalies
    df = get_price_anomalies(threshold)
    return _df_records(df)


class DeletePricesRequest(BaseModel):
    rows: List[dict]  # [{securities_id, date}]

@router.delete("/historical-prices")
def delete_historical_prices_endpoint(req: DeletePricesRequest):
    from database.crud import delete_historical_prices
    deleted = delete_historical_prices(req.rows)
    return {"deleted": deleted}


# ── Missing TX Prices ──────────────────────────────────────────────────────────

@router.get("/missing-tx-prices")
def missing_tx_prices():
    from database.queries import get_missing_tx_prices
    df = get_missing_tx_prices()
    return _df_records(df)


class InsertPricesRequest(BaseModel):
    rows: List[dict]  # [{securities_id, date, price}]

@router.post("/insert-missing-prices")
def insert_missing_prices(req: InsertPricesRequest):
    from database.crud import insert_prices_from_transactions
    inserted = insert_prices_from_transactions(req.rows)
    return {"inserted": inserted}


# ── Normalize Investments ──────────────────────────────────────────────────────

@router.get("/dummy-prices")
def dummy_prices(tolerance_pct: float = 10.0):
    from database.queries import get_investments_with_dummy_prices
    df = get_investments_with_dummy_prices(price_tolerance_pct=tolerance_pct)
    return _df_records(df)


class NormalizeRequest(BaseModel):
    ids: List[int]

@router.post("/normalize-investments")
def normalize_investments(req: NormalizeRequest):
    from database.crud import normalize_investment_prices
    updated = normalize_investment_prices(req.ids)
    return {"updated": updated}


# ── Import Prices from File ────────────────────────────────────────────────────

@router.post("/import-prices-from-file")
async def import_prices_from_file(
    file: UploadFile,
    securities_id: int = Form(...),
    on_conflict: str = Form("skip"),
):
    content = await file.read()
    text = content.decode("utf-8-sig", errors="replace")

    # Auto-detect separator
    sep = "\t" if "\t" in text.split("\n")[0] else ","

    # Find header row containing 'Date' (skip leading metadata lines)
    lines = text.splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        cols = [c.strip().lower() for c in line.split(sep)]
        if "date" in cols:
            header_idx = i
            break
    if header_idx is None:
        raise HTTPException(400, "No header row with 'Date' column found in file")

    data_text = "\n".join(lines[header_idx:])
    df = pd.read_csv(io.StringIO(data_text), sep=sep, dtype=str)
    df.columns = [c.strip().lower() for c in df.columns]

    if "date" not in df.columns:
        raise HTTPException(400, "Column 'Date' not found after parsing")

    # Accept 'close', 'price', or second numeric column as the price
    price_col = next((c for c in ["close", "price", "adj close", "adj. close"] if c in df.columns), None)
    if price_col is None:
        # Fall back to first non-date numeric column
        for c in df.columns:
            if c != "date":
                try:
                    pd.to_numeric(df[c].dropna(), errors="raise")
                    price_col = c
                    break
                except Exception:
                    continue
    if price_col is None:
        raise HTTPException(400, "No price column found (expected 'Close' or 'Price')")

    df = df[["date", price_col]].copy()
    df.columns = ["date", "close"]
    df["date"] = pd.to_datetime(df["date"], dayfirst=False, errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["date", "close"])

    if df.empty:
        raise HTTPException(400, "No valid date/price rows found after parsing")

    rows = [{"date": r["date"].date(), "close": float(r["close"])} for _, r in df.iterrows()]

    inserted = 0
    skipped = 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            for row in rows:
                if on_conflict == "overwrite":
                    cur.execute(
                        """INSERT INTO Historical_Prices (Securities_Id, Date, Close, Source, Downloaded_At)
                           VALUES (%s, %s, %s, 'manual', NOW())
                           ON CONFLICT (Securities_Id, Date) DO UPDATE
                           SET Close = EXCLUDED.Close, Source = EXCLUDED.Source, Downloaded_At = EXCLUDED.Downloaded_At""",
                        (securities_id, row["date"], row["close"]),
                    )
                    inserted += 1
                else:
                    cur.execute(
                        """INSERT INTO Historical_Prices (Securities_Id, Date, Close, Source, Downloaded_At)
                           VALUES (%s, %s, %s, 'manual', NOW())
                           ON CONFLICT (Securities_Id, Date) DO NOTHING""",
                        (securities_id, row["date"], row["close"]),
                    )
                    inserted += cur.rowcount if cur.rowcount > 0 else 0
                    skipped += 1 - (cur.rowcount if cur.rowcount > 0 else 0)
        conn.commit()

    return {"inserted": inserted, "skipped": skipped, "total_rows": len(rows)}


@router.post("/refresh-holdings")
def refresh_holdings():
    from database.crud import update_holdings
    update_holdings()
    return {"message": "Holdings recalculated"}


# ── Investment Consistency ─────────────────────────────────────────────────────

def _detect_inv_anomaly(row: dict) -> tuple:
    ATOL, PTOL = 0.10, 0.005
    TRADEABLE = {"Buy", "Sell", "Reinvest", "ShrIn", "ShrOut"}
    issues, recs = [], []
    qty      = float(row.get("quantity") or 0)
    price    = float(row.get("price") or 0)
    comm_raw = float(row.get("commission") or 0)
    comm     = abs(comm_raw)
    t_acc    = row.get("total_acc")
    t_sec    = row.get("total_sec")
    fx       = float(row.get("fx_rate") or 1)
    action   = str(row.get("action") or "")
    same     = row.get("acc_currency") == row.get("sec_currency")
    notional = qty * price
    if t_acc is None:
        issues.append("NULL total_acc")
        if t_sec is not None:
            recs.append(f"Set total_acc = {float(t_sec) * fx:.2f}")
    if t_sec is None and qty and price and action in TRADEABLE:
        issues.append("NULL total_sec")
        recs.append(f"Set total_sec ≈ {notional:.2f}")
    if same and abs(fx - 1.0) > 0.001:
        issues.append(f"Same ccy but FX_Rate={fx:.4f}")
        recs.append("Set FX_Rate = 1.0")
    if not same and fx and abs(fx - 1.0) < 0.001:
        issues.append("Cross-ccy but FX_Rate=1.0")
        recs.append("Look up correct FX rate for trade date")
    if t_acc is not None and t_sec is not None and fx:
        t_acc_f, t_sec_f = float(t_acc), float(t_sec)
        expected = t_sec_f * fx
        delta = abs(t_acc_f - expected)
        tol = max(ATOL, abs(expected) * PTOL)
        if delta > tol:
            issues.append(f"total_acc ({t_acc_f:.2f}) != total_sec×fx ({expected:.2f})")
            recs.append(f"Set total_acc = {expected:.2f} or fx = {t_acc_f/t_sec_f:.6f}")
    if t_sec is not None and qty and price and action in TRADEABLE:
        t_sec_f = float(t_sec)
        diff = abs(t_sec_f - notional)
        tol = max(ATOL, comm + abs(notional) * PTOL)
        if diff > tol:
            issues.append(f"total_sec ({t_sec_f:.2f}) != qty×price ({notional:.2f})")
            exp = notional + comm_raw if action in ("Buy", "ShrIn", "Reinvest") else max(0.0, notional + comm_raw)
            recs.append(f"Expected total_sec ~= {exp:.2f}")
    if action in TRADEABLE:
        if not qty:
            issues.append("Quantity is 0 / NULL")
        if not price:
            issues.append("Price is 0 / NULL")
    return "; ".join(issues), "; ".join(recs)


@router.get("/investment-consistency")
def investment_consistency(account_ids: Optional[str] = None):
    from database.queries import get_investment_consistency_data
    ids = [int(x) for x in account_ids.split(",") if x.strip()] if account_ids else None
    df = get_investment_consistency_data(ids)
    if not df.empty:
        df["date"] = df["date"].astype(str)
        anom, rec = zip(*[_detect_inv_anomaly(r) for r in df.to_dict("records")])
        df["anomalies"] = list(anom)
        df["recommendations"] = list(rec)
    return _df_records(df)


class UpdateInvRowRequest(BaseModel):
    investments_id: int
    fields: dict

@router.put("/investment-row")
def update_investment_row_endpoint(req: UpdateInvRowRequest):
    from database.queries import update_investment_row
    update_investment_row(req.investments_id, req.fields)
    return {"updated": req.investments_id}


# ── Missing Transfer Mirrors ───────────────────────────────────────────────────

_MISSING_MIRRORS_SQL = """
    SELECT
        t.transactions_id,
        a_src.accounts_name AS source_account,
        t.accounts_id AS src_acc_id,
        t.date::text AS date,
        COALESCE(p.payees_name, '') AS payee,
        t.description,
        t.total_amount AS source_amount,
        t.total_amount_target,
        a_tgt.accounts_name AS target_account,
        t.accounts_id_target AS tgt_acc_id,
        t.payees_id,
        t.cleared,
        t.transfers_id,
        CASE WHEN t.transfers_id IS NULL THEN 'No Transfers_Id' ELSE 'Mirror missing' END AS issue_type
    FROM Transactions t
    JOIN Accounts a_src ON a_src.accounts_id = t.accounts_id
    JOIN Accounts a_tgt ON a_tgt.accounts_id = t.accounts_id_target
    LEFT JOIN Payees p ON p.payees_id = t.payees_id
    LEFT JOIN Transactions mirror
           ON mirror.transfers_id = t.transfers_id
           AND mirror.accounts_id = t.accounts_id_target
           AND mirror.transactions_id != t.transactions_id
    WHERE t.accounts_id_target IS NOT NULL
      AND (
          (t.transfers_id IS NOT NULL AND mirror.transactions_id IS NULL)
          OR t.transfers_id IS NULL
      )
      AND NOT (
          a_src.accounts_type IN ('Brokerage', 'Pension', 'Other Investment', 'Margin')
          AND a_src.accounts_id_linked IS NOT NULL
      )
      AND NOT (
          a_tgt.accounts_type IN ('Brokerage', 'Pension', 'Other Investment', 'Margin')
          AND a_tgt.accounts_id_linked IS NOT NULL
      )
    ORDER BY t.date DESC, t.transactions_id DESC
"""

@router.get("/missing-transfer-mirrors")
def missing_transfer_mirrors():
    with get_db() as conn:
        df = pd.read_sql(_MISSING_MIRRORS_SQL, conn)
    return _df_records(df)


class FixMirrorsRequest(BaseModel):
    ids: List[int]  # transactions_ids to fix

@router.post("/fix-transfer-mirrors")
def fix_transfer_mirrors(req: FixMirrorsRequest):
    from database.crud import update_accounts_balances
    # Fetch full rows for the selected IDs
    with get_db() as conn:
        df = pd.read_sql(_MISSING_MIRRORS_SQL, conn)
    sel = df[df["transactions_id"].isin(req.ids)]

    created = 0
    errors = []
    affected: set = set()

    conn = get_connection()
    cur = conn.cursor()
    try:
        for _, row in sel.iterrows():
            src_tx_id = int(row["transactions_id"])
            src_acc_id = int(row["src_acc_id"])
            tgt_acc_id = int(row["tgt_acc_id"])
            tx_date = row["date"]
            payees_id = int(row["payees_id"]) if pd.notna(row["payees_id"]) else None
            description = row["description"]
            src_amount = float(row["source_amount"]) if pd.notna(row["source_amount"]) else 0.0
            tgt_raw = row["total_amount_target"]
            cleared = bool(row["cleared"])
            transfers_id = int(row["transfers_id"]) if pd.notna(row["transfers_id"]) else None

            if pd.notna(tgt_raw) and float(tgt_raw) != 0:
                raw = abs(float(tgt_raw))
                mirror_amount = raw if src_amount <= 0 else -raw
            else:
                mirror_amount = -src_amount
            mirror_tgt_amount = abs(src_amount)

            if transfers_id is None:
                cur.execute("SELECT nextval('transfers_id_seq')")
                transfers_id = cur.fetchone()[0]
                cur.execute(
                    "UPDATE Transactions SET transfers_id = %s WHERE transactions_id = %s",
                    (transfers_id, src_tx_id),
                )

            try:
                cur.execute("""
                    INSERT INTO Transactions
                        (Accounts_Id, Date, Payees_Id, Description, Total_Amount, Cleared,
                         Accounts_Id_Target, Total_Amount_Target, Transfers_Id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING Transactions_Id
                """, (tgt_acc_id, tx_date, payees_id, description, mirror_amount, cleared,
                      src_acc_id, mirror_tgt_amount, transfers_id))
                mirror_tx_id = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO Splits (Transactions_Id, Categories_Id, Amount, Memo) VALUES (%s, NULL, %s, 'Transfer')",
                    (mirror_tx_id, mirror_amount),
                )
                affected.add(src_acc_id)
                affected.add(tgt_acc_id)
                created += 1
            except Exception as e:
                errors.append(f"TX #{src_tx_id}: {e}")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        cur.close()
        conn.close()

    for acc_id in affected:
        update_accounts_balances(acc_id)

    return {"created": created, "errors": errors}


# ── Unlinked Transfer Pairs ────────────────────────────────────────────────────

_UNLINKED_SQL = """
    SELECT
        t.transactions_id AS src_tx_id,
        a_src.accounts_name AS source_account,
        t.accounts_id AS src_acc_id,
        t.date::text AS date,
        COALESCE(p.payees_name, '') AS payee,
        t.description,
        t.total_amount AS source_amount,
        t.total_amount_target,
        a_tgt.accounts_name AS target_account,
        t.accounts_id_target AS tgt_acc_id,
        t.transfers_id,
        cand.transactions_id AS candidate_tx_id,
        cand.total_amount AS candidate_amount,
        cand.description AS candidate_desc
    FROM Transactions t
    JOIN Accounts a_src ON a_src.accounts_id = t.accounts_id
    JOIN Accounts a_tgt ON a_tgt.accounts_id = t.accounts_id_target
    LEFT JOIN Payees p ON p.payees_id = t.payees_id
    LEFT JOIN Transactions mirror
           ON mirror.transfers_id = t.transfers_id
           AND mirror.accounts_id = t.accounts_id_target
           AND mirror.transactions_id != t.transactions_id
    JOIN Transactions cand
           ON cand.accounts_id = t.accounts_id_target
           AND cand.date = t.date
           AND ABS(cand.total_amount) = ABS(COALESCE(t.total_amount_target, t.total_amount))
           AND cand.transactions_id != t.transactions_id
           AND (cand.transfers_id IS NULL OR cand.transfers_id != t.transfers_id)
    WHERE t.accounts_id_target IS NOT NULL
      AND t.transfers_id IS NOT NULL
      AND mirror.transactions_id IS NULL
      AND NOT (
          a_src.accounts_type IN ('Brokerage','Pension','Other Investment','Margin')
          AND a_src.accounts_id_linked IS NOT NULL
      )
      AND NOT (
          a_tgt.accounts_type IN ('Brokerage','Pension','Other Investment','Margin')
          AND a_tgt.accounts_id_linked IS NOT NULL
      )
    ORDER BY t.date DESC, t.transactions_id DESC
"""

@router.get("/unlinked-transfer-pairs")
def unlinked_transfer_pairs():
    with get_db() as conn:
        df = pd.read_sql(_UNLINKED_SQL, conn)
    return _df_records(df)


class LinkPairsRequest(BaseModel):
    pairs: List[dict]  # [{src_tx_id, candidate_tx_id, transfers_id, src_acc_id, tgt_acc_id}]

@router.post("/link-transfer-pairs")
def link_transfer_pairs(req: LinkPairsRequest):
    from database.crud import update_accounts_balances

    # Deduplicate: (A→B) and (B→A) are the same pair. Keep only the one with the lower src_tx_id.
    seen: set = set()
    unique_pairs = []
    for pair in req.pairs:
        a, b = int(pair["src_tx_id"]), int(pair["candidate_tx_id"])
        key = (min(a, b), max(a, b))
        if key not in seen:
            seen.add(key)
            unique_pairs.append(pair)

    linked = 0
    errors = []
    affected: set = set()
    conn = get_connection()
    cur = conn.cursor()
    try:
        for pair in unique_pairs:
            src_id  = int(pair["src_tx_id"])
            cand_id = int(pair["candidate_tx_id"])
            src_acc = int(pair["src_acc_id"])
            tgt_acc = int(pair["tgt_acc_id"])
            try:
                # Generate a fresh shared Transfers_Id from the sequence
                cur.execute("SELECT nextval('transfers_id_seq')")
                shared_tid = cur.fetchone()[0]

                # Update BOTH legs to the same shared Transfers_Id and cross-link targets
                cur.execute("""
                    UPDATE Transactions
                       SET Transfers_Id       = %s,
                           Accounts_Id_Target = %s
                     WHERE Transactions_Id = %s
                """, (shared_tid, tgt_acc, src_id))
                cur.execute("""
                    UPDATE Transactions
                       SET Transfers_Id       = %s,
                           Accounts_Id_Target = %s
                     WHERE Transactions_Id = %s
                """, (shared_tid, src_acc, cand_id))

                linked += 1
                affected.add(src_acc)
                affected.add(tgt_acc)
            except Exception as e:
                errors.append(f"Pair ({src_id}↔{cand_id}): {e}")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        cur.close()
        conn.close()

    for acc_id in affected:
        update_accounts_balances(acc_id)

    return {"linked": linked, "errors": errors}


# ── Transfer Sign Mismatches ───────────────────────────────────────────────────

_SIGN_MISMATCH_SQL = """
    SELECT
        t1.transactions_id AS tx1_id,
        t2.transactions_id AS tx2_id,
        t1.transfers_id,
        t1.date::text AS date,
        a1.accounts_name AS account1,
        a2.accounts_name AS account2,
        t1.accounts_id AS acc1_id,
        t2.accounts_id AS acc2_id,
        t1.total_amount AS amount1,
        t2.total_amount AS amount2,
        COALESCE(p.payees_name, '') AS payee,
        t1.description,
        CASE
            WHEN t1.total_amount > 0 AND t2.total_amount > 0 THEN 'Both credit (+)'
            WHEN t1.total_amount < 0 AND t2.total_amount < 0 THEN 'Both debit (−)'
            ELSE 'Same sign'
        END AS mismatch_type
    FROM Transactions t1
    JOIN Transactions t2
           ON t2.transfers_id = t1.transfers_id
           AND t2.transactions_id > t1.transactions_id
    JOIN Accounts a1 ON a1.accounts_id = t1.accounts_id
    JOIN Accounts a2 ON a2.accounts_id = t2.accounts_id
    LEFT JOIN Payees p ON p.payees_id = t1.payees_id
    WHERE t1.transfers_id IS NOT NULL
      AND t1.total_amount != 0
      AND t2.total_amount != 0
      AND SIGN(t1.total_amount) = SIGN(t2.total_amount)
    ORDER BY t1.date DESC, t1.transfers_id DESC
"""

@router.get("/transfer-sign-mismatches")
def transfer_sign_mismatches():
    with get_db() as conn:
        df = pd.read_sql(_SIGN_MISMATCH_SQL, conn)
    return _df_records(df)


class FlipSignRequest(BaseModel):
    tx_ids: List[int]
    all_acc_ids: List[int]

@router.post("/fix-transfer-sign")
def fix_transfer_sign(req: FlipSignRequest):
    from database.crud import update_accounts_balances
    flipped = 0
    errors = []
    conn = get_connection()
    cur = conn.cursor()
    try:
        for tx_id in req.tx_ids:
            try:
                cur.execute("""
                    UPDATE Transactions
                    SET total_amount = -total_amount,
                        total_amount_target = CASE WHEN total_amount_target IS NOT NULL THEN -total_amount_target ELSE NULL END
                    WHERE transactions_id = %s
                """, (tx_id,))
                cur.execute("UPDATE Splits SET amount = -amount WHERE transactions_id = %s", (tx_id,))
                flipped += 1
            except Exception as e:
                errors.append(f"TX #{tx_id}: {e}")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        cur.close()
        conn.close()

    for acc_id in req.all_acc_ids:
        update_accounts_balances(int(acc_id))

    return {"flipped": flipped, "errors": errors}


# ── Missing Investment Cash Links ──────────────────────────────────────────────

_MISSING_INV_CASH_SQL = """
    SELECT
        i.investments_id,
        a_inv.accounts_name AS investment_account,
        i.accounts_id AS inv_acc_id,
        a_cash.accounts_id AS cash_acc_id,
        a_cash.accounts_name AS cash_account,
        i.date::text AS date,
        i.action,
        COALESCE(s.securities_name, '—') AS security,
        ABS(i.total_amount_acccur) AS inv_amount,
        t.transactions_id AS candidate_tx_id,
        t.total_amount AS candidate_amount,
        COALESCE(p.payees_name, '') AS candidate_payee,
        t.description AS candidate_description
    FROM Investments i
    JOIN Accounts a_inv ON a_inv.accounts_id = i.accounts_id
    JOIN Accounts a_cash ON a_cash.accounts_id = a_inv.accounts_id_linked
    LEFT JOIN Securities s ON s.securities_id = i.securities_id
    JOIN Transactions t
          ON t.accounts_id = a_inv.accounts_id_linked
          AND t.date = i.date
          AND ROUND(ABS(t.total_amount)::numeric, 2) = ROUND(ABS(i.total_amount_acccur)::numeric, 2)
          AND NOT EXISTS (SELECT 1 FROM Investments i2 WHERE i2.transactions_id = t.transactions_id)
          AND NOT (
              t.transfers_id IS NOT NULL
              AND EXISTS (SELECT 1 FROM Transactions mirror WHERE mirror.transfers_id = t.transfers_id AND mirror.transactions_id != t.transactions_id)
          )
    LEFT JOIN Payees p ON p.payees_id = t.payees_id
    WHERE i.transactions_id IS NULL
      AND i.action IN ('Buy', 'Sell', 'Dividend', 'IntInc', 'RtrnCap', 'MiscExp')
    ORDER BY i.date DESC, i.investments_id, t.transactions_id
"""

@router.get("/missing-investment-cash-links")
def missing_investment_cash_links():
    with get_db() as conn:
        df = pd.read_sql(_MISSING_INV_CASH_SQL, conn)
    return _df_records(df)


class FixInvCashLinksRequest(BaseModel):
    pairs: List[dict]  # [{investments_id, candidate_tx_id}]

@router.post("/fix-investment-cash-links")
def fix_investment_cash_links(req: FixInvCashLinksRequest):
    from database.crud import update_investment_balances, update_accounts_balances
    linked = 0
    errors = []
    tx_ids = []
    conn = get_connection()
    cur = conn.cursor()
    try:
        for pair in req.pairs:
            try:
                cur.execute(
                    "UPDATE Investments SET Transactions_Id = %s WHERE Investments_Id = %s",
                    (int(pair["candidate_tx_id"]), int(pair["investments_id"])),
                )
                linked += 1
                tx_ids.append(int(pair["candidate_tx_id"]))
            except Exception as e:
                errors.append(f"Inv #{pair['investments_id']}: {e}")
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        cur.close()
        conn.close()

    if linked:
        update_investment_balances()
        if tx_ids:
            ph = ", ".join(["%s"] * len(tx_ids))
            _conn = get_connection()
            try:
                _cur = _conn.cursor()
                _cur.execute(f"SELECT DISTINCT accounts_id FROM Transactions WHERE transactions_id IN ({ph})", tx_ids)
                for (acc_id,) in _cur.fetchall():
                    update_accounts_balances(acc_id)
            finally:
                _cur.close()
                _conn.close()

    return {"linked": linked, "errors": errors}


# ── Fix Investment Account Target ───────────────────────────────────────────────

_MISSING_INV_ACCOUNT_TARGET_SQL = """
    SELECT
        t.transactions_id,
        t.date::text AS date,
        a_cash.accounts_name AS cash_account,
        i.action,
        s.securities_name AS security,
        t.total_amount,
        a_inv.accounts_name AS investment_account,
        i.investments_id
    FROM Investments i
    JOIN Transactions t ON i.transactions_id = t.transactions_id
    JOIN Accounts a_cash ON t.accounts_id = a_cash.accounts_id
    JOIN Accounts a_inv  ON i.accounts_id  = a_inv.accounts_id
    LEFT JOIN Securities s ON i.securities_id = s.securities_id
    WHERE t.accounts_id_target IS NULL
      AND i.action IN ('Dividend','IntInc','RtrnCap','MiscInc','CashIn','Sell')
    ORDER BY t.date DESC
"""

@router.get("/missing-inv-account-target")
def missing_inv_account_target():
    with get_db() as conn:
        df = pd.read_sql(_MISSING_INV_ACCOUNT_TARGET_SQL, conn)
    return _df_records(df)


@router.post("/fix-inv-account-target")
def fix_inv_account_target():
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE Transactions t
            SET accounts_id_target = i.accounts_id
            FROM Investments i
            WHERE i.transactions_id = t.transactions_id
              AND t.accounts_id_target IS NULL
              AND i.action IN ('Dividend','IntInc','RtrnCap','MiscInc','CashIn','Sell')
        """)
        updated = cur.rowcount
        conn.commit()
        cur.close()
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()
    return {"updated": updated}


# ── Log Viewer ─────────────────────────────────────────────────────────────────

@router.get("/logs")
def get_logs(lines: int = 500, level: Optional[str] = None, search: Optional[str] = None, file: str = "all"):
    import os
    level_filters = [l.strip() for l in level.split(",") if l.strip()] if level else []

    app_data_dir = os.getenv("APP_DATA_DIR", ".")
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

    def _find(name: str) -> Optional[str]:
        for p in [
            os.path.join(app_data_dir, name),
            os.path.join(repo_root, name),
            name,
        ]:
            if os.path.exists(os.path.abspath(p)):
                return os.path.abspath(p)
        return None

    def _read(path: str) -> list[str]:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                return fh.readlines()
        except Exception:
            return []

    app_path  = _find("app.log")
    sched_path = _find("scheduler.log")

    sources_found = {k: v for k, v in {"app": app_path, "scheduler": sched_path}.items() if v}

    if file == "app":
        raw = _read(app_path) if app_path else []
        used = [app_path] if app_path else []
    elif file == "scheduler":
        raw = _read(sched_path) if sched_path else []
        used = [sched_path] if sched_path else []
    else:  # "all" — merge both, preserve order (they share the same timestamp format)
        raw = sorted(_read(app_path or "") + _read(sched_path or ""))
        used = [p for p in [app_path, sched_path] if p]

    tail = raw[-lines:]
    if level_filters:
        tail = [l for l in tail if any(lvl in l for lvl in level_filters)]
    if search:
        needle = search.lower()
        tail = [l for l in tail if needle in l.lower()]

    return {
        "text": "".join(tail),
        "sources_found": sources_found,
        "sources_used": used,
        "lines": len(tail),
    }


# ── Scheduler Jobs ─────────────────────────────────────────────────────────────

# Built-in job IDs that map to actual Python functions in the scheduler.
# These are seeded into the DB on first call; custom jobs can be added there too.
_BUILTIN_JOB_IDS = {
    "market_data", "daily_backup", "morning_maintenance",
    "weekly_summary", "monthly_summary", "securities_info",
    "dividend_history", "recurring_drafts",
}

_SEED_JOBS = [
    ("market_data",        "Market Data Refresh",    "Downloads latest security prices (Yahoo Finance, TradingView, Solidus) and FX rates.",                         "Every 5 min, 24×7",              True),
    ("daily_backup",       "Daily Backup",           "pg_dump database backup + purge of backups older than the configured retention period.",                         "Daily at 06:00",                 True),
    ("morning_maintenance","Morning Maintenance",     "VACUUM ANALYZE the database, then refresh all AI transaction embeddings.",                                        "Daily at 06:15",                 True),
    ("weekly_summary",     "Weekly AI Summary",      "Generates the AI weekly financial summary. Also runs at startup if the current week's entry is missing.",         "Monday at 07:00",                True),
    ("monthly_summary",    "Monthly AI Summary",     "Generates the AI monthly financial summary for the previous month.",                                               "1st of month at 07:00",          True),
    ("securities_info",    "Securities Info",        "Downloads securities metadata (sector, industry, analyst targets, dividends) from Yahoo Finance and TradingView.", "Once per calendar day",          True),
    ("dividend_history",   "Dividend History",       "Downloads full historical dividend records for all tracked securities (heavy — runs weekly).",                     "Sunday at 06:30",                True),
    ("recurring_drafts",   "Recurring Drafts",       "Generates draft transactions for all active recurring templates due today or earlier.",                             "Once per calendar day",          True),
    ("signal_notifications", "Signal Notifications", "Computes final signals for all held securities and records any changes for dashboard notifications.",               "Every 30 min, 24×7",             True),
]


def _ensure_scheduler_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS Scheduler_Jobs (
            job_id       VARCHAR(80) PRIMARY KEY,
            name         VARCHAR(200) NOT NULL,
            description  TEXT,
            schedule     VARCHAR(200),
            enabled      BOOLEAN NOT NULL DEFAULT TRUE,
            last_run     TIMESTAMPTZ,
            last_status  VARCHAR(20),
            last_message TEXT
        )
    """)
    for job_id, name, desc, schedule, enabled in _SEED_JOBS:
        cur.execute("""
            INSERT INTO Scheduler_Jobs (job_id, name, description, schedule, enabled)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (job_id) DO UPDATE
                SET name        = EXCLUDED.name,
                    description = EXCLUDED.description
                -- schedule and enabled are NOT overwritten so user edits are preserved
        """, (job_id, name, desc, schedule, enabled))


def _run_scheduler_job_fn(job_id: str):
    import logging
    conn = get_connection()
    try:
        if job_id == "market_data":
            from data.downloaders import (
                download_historical_prices_from_yahoo,
                download_historical_prices_from_tradingview,
                download_bond_prices_from_solidus,
                download_historical_fx,
            )
            download_historical_prices_from_yahoo(tsperiod="1d")
            download_historical_prices_from_tradingview(tsperiod="1d")
            download_bond_prices_from_solidus()
            download_historical_fx(tsperiod="3d")
        elif job_id == "daily_backup":
            from datetime import datetime, timedelta
            from database.backup import DatabaseBackup
            bm = DatabaseBackup()
            bm.create_backup()
            cutoff = datetime.now() - timedelta(days=30)
            for b in bm.get_backup_history():
                if b["modified"] < cutoff:
                    bm.delete_backup(b["filename"])
        elif job_id == "morning_maintenance":
            from ai.update_vector import update_all_embeddings
            mc = get_connection()
            mc.autocommit = True
            with mc.cursor() as cur:
                cur.execute("VACUUM ANALYZE")
            mc.close()
            update_all_embeddings()
        elif job_id == "weekly_summary":
            from ai.weekly_summary import run as run_weekly_summary
            run_weekly_summary()
        elif job_id == "monthly_summary":
            from ai.monthly_summary import run as run_monthly_summary
            run_monthly_summary()
        elif job_id == "securities_info":
            from data.downloaders import (
                download_securities_info_from_yahoo,
                download_securities_info_from_tradingview,
            )
            download_securities_info_from_yahoo()
            download_securities_info_from_tradingview()
        elif job_id == "dividend_history":
            from data.downloaders import download_dividend_history
            download_dividend_history()
        elif job_id == "recurring_drafts":
            from database.crud import generate_draft_transactions
            generate_draft_transactions()
        elif job_id == "signal_notifications":
            from database.queries import refresh_signal_notifications
            refresh_signal_notifications()
        else:
            raise ValueError(f"No runnable function for custom job '{job_id}'")
        # Record success
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE Scheduler_Jobs
                SET last_run = NOW(), last_status = 'success', last_message = 'Completed OK'
                WHERE job_id = %s
            """, (job_id,))
        conn.commit()
    except Exception as e:
        logging.error(f"Scheduler job failed [{job_id}]: {e}", exc_info=True)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE Scheduler_Jobs
                    SET last_run = NOW(), last_status = 'error', last_message = %s
                    WHERE job_id = %s
                """, (str(e)[:500], job_id))
            conn.commit()
        except Exception:
            pass
    finally:
        conn.close()


@router.get("/scheduler-jobs")
def get_scheduler_jobs():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_scheduler_table(cur)
            conn.commit()
            cur.execute("""
                SELECT job_id, name, description, schedule, enabled,
                       last_run::text, last_status, last_message
                FROM Scheduler_Jobs ORDER BY job_id
            """)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        return rows
    finally:
        conn.close()


@router.post("/scheduler-jobs")
def create_scheduler_job(data: dict):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_scheduler_table(cur)
            cur.execute("""
                INSERT INTO Scheduler_Jobs (job_id, name, description, schedule, enabled)
                VALUES (%s, %s, %s, %s, %s)
            """, (
                data.get("job_id"), data.get("name"),
                data.get("description"), data.get("schedule"),
                bool(data.get("enabled", True)),
            ))
        conn.commit()
        return {"created": data.get("job_id")}
    except Exception as e:
        conn.rollback()
        raise HTTPException(400, str(e))
    finally:
        conn.close()


@router.put("/scheduler-jobs/{job_id}")
def update_scheduler_job(job_id: str, data: dict):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE Scheduler_Jobs
                SET name=%s, description=%s, schedule=%s, enabled=%s
                WHERE job_id=%s
            """, (
                data.get("name"), data.get("description"),
                data.get("schedule"), bool(data.get("enabled", True)),
                job_id,
            ))
            if cur.rowcount == 0:
                raise HTTPException(404, "Job not found")
        conn.commit()
        return {"updated": job_id}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(400, str(e))
    finally:
        conn.close()


@router.delete("/scheduler-jobs/{job_id}")
def delete_scheduler_job(job_id: str):
    if job_id in _BUILTIN_JOB_IDS:
        raise HTTPException(400, "Built-in jobs cannot be deleted.")
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM Scheduler_Jobs WHERE job_id = %s", (job_id,))
            if cur.rowcount == 0:
                raise HTTPException(404, "Job not found")
        conn.commit()
        return {"deleted": job_id}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(400, str(e))
    finally:
        conn.close()


@router.post("/run-scheduler-job/{job_id}")
def trigger_scheduler_job(job_id: str):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_scheduler_table(cur)
            cur.execute("SELECT 1 FROM Scheduler_Jobs WHERE job_id = %s", (job_id,))
            if not cur.fetchone():
                raise HTTPException(404, f"Unknown job: {job_id}")
        conn.commit()
    finally:
        conn.close()
    t = threading.Thread(target=_run_scheduler_job_fn, args=(job_id,), daemon=True)
    t.start()
    return {"triggered": job_id, "message": f"Job '{job_id}' started in background — check the Log Viewer for progress."}
