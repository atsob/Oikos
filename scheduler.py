"""
Background scheduler — runs as a separate Docker service (see docker-compose.yml).

Jobs
────
• Market data       : every MARKET_REFRESH_INTERVAL_MINUTES (24 × 7).
                      Downloads the latest prices for all securities and FX rates.
• Daily backup      : 06:00 AM — pg_dump + retention purge.
• Morning maint.    : 06:15 AM — VACUUM ANALYZE + full embedding update.
• Weekly summary    : Monday 07:00 — also fires at startup if missing for this week.
• Securities info   : once per calendar day (at startup).
"""

import warnings
warnings.filterwarnings("ignore", message="No runtime found", category=UserWarning)
warnings.filterwarnings("ignore", message="No runtime found", module="streamlit")
warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy", category=UserWarning)
warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy connectable")

import logging
import re
import time
from datetime import date, datetime, timedelta

from ai.weekly_summary import run as run_weekly_summary
from ai.monthly_summary import run as run_monthly_summary
from data.downloaders import (
    download_historical_prices_from_yahoo,
    download_historical_prices_from_tradingview,
    download_bond_prices_from_solidus,
    download_historical_fx,
    download_securities_info_from_yahoo,
    download_securities_info_from_tradingview,
    download_dividend_history,
)
from ai.update_vector import update_all_embeddings
from database.backup import DatabaseBackup
from database.connection import get_connection
from database.crud import generate_draft_transactions
from database.queries import refresh_signal_notifications

import os as _os
_log_dir  = _os.getenv("APP_DATA_DIR", ".")
_log_path = _os.path.join(_log_dir, "scheduler.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [scheduler] %(message)s",
    handlers=[
        # Write to APP_DATA_DIR/scheduler.log (shared volume) so the Log Viewer
        # can read it; also keep stdout so `docker logs` continues to work.
        logging.FileHandler(_log_path, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)

# ── Fallback defaults (used when DB row is missing or unparseable) ────────────
MARKET_REFRESH_INTERVAL_MINUTES = 5   # Every 5 min, 24×7
BACKUP_HOUR            = 6    # Daily at 06:00
BACKUP_RETENTION_DAYS  = 30
MAINTENANCE_HOUR       = 6    # Daily at 06:15
MAINTENANCE_MINUTE     = 15
WEEKLY_SUMMARY_WEEKDAY = 0    # Monday at 07:00
WEEKLY_SUMMARY_HOUR    = 7
WEEKLY_SUMMARY_MINUTE  = 0
MONTHLY_SUMMARY_DAY    = 1    # 1st of month at 07:00
MONTHLY_SUMMARY_HOUR   = 7
MONTHLY_SUMMARY_MINUTE = 0
DIVIDEND_HISTORY_WEEKDAY = 6  # Sunday at 06:30
DIVIDEND_HISTORY_HOUR    = 6
DIVIDEND_HISTORY_MINUTE  = 30
SIGNAL_REFRESH_INTERVAL_MINUTES = 30  # Every 30 min, 24×7

# Tick interval — the scheduler wakes up this often to check all jobs.
TICK_SECONDS = 60


# ── Helpers ───────────────────────────────────────────────────────────────────

def _current_week_start() -> date:
    """Return Monday of the just-finished week — matches what weekly_summary.run() stores."""
    today = date.today()
    return today - timedelta(days=today.weekday() + 7)


def _summary_exists_for_current_week() -> bool:
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM AI_Weekly_Summaries WHERE Week_Start = %s",
                (_current_week_start(),),
            )
            exists = cur.fetchone() is not None
        conn.close()
        return exists
    except Exception:
        return False


def _is_market_open(now: datetime) -> bool:
    """Always True — market data is refreshed 24 × 7."""
    return True


# ── Job status persistence ────────────────────────────────────────────────────

def _record_job(job_id: str, status: str, message: str = ""):
    """Write last_run / last_status back to Scheduler_Jobs so the UI stays accurate."""
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO Scheduler_Jobs (job_id, name, last_run, last_status, last_message)
                VALUES (%s, %s, NOW(), %s, %s)
                ON CONFLICT (job_id) DO UPDATE
                    SET last_run = NOW(), last_status = EXCLUDED.last_status,
                        last_message = EXCLUDED.last_message
            """, (job_id, job_id, status, message[:500]))
        conn.commit()
        conn.close()
    except Exception as exc:
        logging.warning(f"Could not record job status for '{job_id}': {exc}")


_DAYS = {'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
         'friday': 4, 'saturday': 5, 'sunday': 6}

_schedule_cache: dict[str, str] = {}
_schedule_cache_ts: datetime = datetime.min


def _get_all_schedules() -> dict[str, str]:
    """Read all job schedules from DB, cached for one tick interval."""
    global _schedule_cache, _schedule_cache_ts
    if (datetime.now() - _schedule_cache_ts).total_seconds() < TICK_SECONDS:
        return _schedule_cache
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT job_id, schedule FROM Scheduler_Jobs")
            _schedule_cache = {r[0]: str(r[1] or '') for r in cur.fetchall()}
        conn.close()
        _schedule_cache_ts = datetime.now()
    except Exception as exc:
        logging.warning(f"Could not read schedules from DB: {exc}")
    return _schedule_cache


def _parse_interval(s: str, default: int) -> int:
    """'Every N min…' → N, else default."""
    m = re.search(r'every\s+(\d+)\s*min', s, re.IGNORECASE)
    return int(m.group(1)) if m else default


def _parse_daily(s: str, dh: int, dm: int) -> tuple[int, int]:
    """'… at HH:MM' → (hour, minute), else (dh, dm)."""
    m = re.search(r'\bat\s+(\d{1,2}):(\d{2})', s, re.IGNORECASE)
    return (int(m.group(1)), int(m.group(2))) if m else (dh, dm)


def _parse_weekly(s: str, dwd: int, dh: int, dm: int) -> tuple[int, int, int]:
    """'Monday at HH:MM' → (weekday, hour, minute), else defaults."""
    m = re.search(
        r'(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s+at\s+(\d{1,2}):(\d{2})',
        s, re.IGNORECASE)
    if m:
        return (_DAYS[m.group(1).lower()], int(m.group(2)), int(m.group(3)))
    return (dwd, dh, dm)


def _parse_monthly(s: str, dd: int, dh: int, dm: int) -> tuple[int, int, int]:
    """'Nth of month at HH:MM' → (day, hour, minute), else defaults."""
    m = re.search(r'(\d+)(?:st|nd|rd|th)?\s+of\s+month\s+at\s+(\d{1,2}):(\d{2})', s, re.IGNORECASE)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return (dd, dh, dm)


def _in_window(now: datetime, hour: int, minute: int, window: int = 5) -> bool:
    """True if now is within `window` minutes of HH:MM on the same day."""
    return now.hour == hour and minute <= now.minute < minute + window


# ── Jobs ──────────────────────────────────────────────────────────────────────

def _monthly_summary_job():
    logging.info("Running monthly summary job…")
    try:
        run_monthly_summary()
        logging.info("Monthly summary completed.")
        _record_job("monthly_summary", "success", "Completed OK")
    except Exception as e:
        logging.error(f"Monthly summary failed: {e}", exc_info=True)
        _record_job("monthly_summary", "error", str(e))


def _weekly_summary_job():
    logging.info("Running weekly summary job…")
    try:
        run_weekly_summary()
        logging.info("Weekly summary completed.")
        _record_job("weekly_summary", "success", "Completed OK")
    except Exception as e:
        logging.error(f"Weekly summary failed: {e}", exc_info=True)
        _record_job("weekly_summary", "error", str(e))


def _market_data_job():
    logging.info("Running market data refresh…")
    errors = []
    try:
        download_historical_prices_from_yahoo(tsperiod="1d")
        logging.info("Security prices refreshed.")
    except Exception as e:
        logging.error(f"Price refresh failed: {e}", exc_info=True)
        errors.append(str(e))

    try:
        download_historical_prices_from_tradingview(tsperiod="1d")
        logging.info("TradingView prices refreshed.")
    except Exception as e:
        logging.error(f"TradingView price refresh failed: {e}", exc_info=True)
        errors.append(str(e))

    try:
        download_bond_prices_from_solidus()
        logging.info("Bond prices refreshed.")
    except Exception as e:
        logging.error(f"Bond price refresh failed: {e}", exc_info=True)
        errors.append(str(e))

    try:
        download_historical_fx(tsperiod="3d")   # 3 d to catch weekend gaps on Monday
        logging.info("FX rates refreshed.")
    except Exception as e:
        logging.error(f"FX refresh failed: {e}", exc_info=True)
        errors.append(str(e))

    if errors:
        _record_job("market_data", "error", "; ".join(errors))
    else:
        _record_job("market_data", "success", "Completed OK")


def _securities_info_job():
    """Download securities metadata (sector, industry, rating, target price,
    dividend summary) once per day.

    Yahoo Finance runs first (broker analyst consensus + dividend fields);
    TradingView fills any remaining NULL fields (sector/industry/target price)
    for securities not covered by Yahoo (e.g. ATHEX stocks).
    """
    logging.info("Running securities info refresh…")
    try:
        download_securities_info_from_yahoo()
        download_securities_info_from_tradingview()
        logging.info("Securities info refreshed.")
        _record_job("securities_info", "success", "Completed OK")
    except Exception as e:
        logging.error(f"Securities info refresh failed: {e}", exc_info=True)
        _record_job("securities_info", "error", str(e))


def _dividend_history_job():
    """Download full historical dividend records once per week (Sunday at 06:30).

    Runs weekly rather than daily — dividend histories change slowly and each
    call fetches a full time series per ticker (heavier than the daily info fetch).
    """
    logging.info("Running weekly dividend history refresh…")
    try:
        download_dividend_history()
        logging.info("Dividend history refresh complete.")
        _record_job("dividend_history", "success", "Completed OK")
    except Exception as e:
        logging.error(f"Dividend history refresh failed: {e}", exc_info=True)
        _record_job("dividend_history", "error", str(e))


def _backup_job():
    """Create a daily database backup and purge files older than BACKUP_RETENTION_DAYS."""
    logging.info("Running daily database backup…")
    bm = DatabaseBackup()
    try:
        result = bm.create_backup()
        if result['success']:
            logging.info(
                f"Backup created: {result['filename']} ({result['size_mb']:.1f} MB)"
            )
        else:
            logging.error(f"Backup failed: {result['message']}")
            _record_job("daily_backup", "error", result['message'])
            return
    except Exception as e:
        logging.error(f"Backup job failed: {e}", exc_info=True)
        _record_job("daily_backup", "error", str(e))
        return

    # Purge backups older than the retention period
    purged = 0
    try:
        cutoff = datetime.now() - timedelta(days=BACKUP_RETENTION_DAYS)
        for backup in bm.get_backup_history():
            if backup['modified'] < cutoff:
                del_result = bm.delete_backup(backup['filename'])
                if del_result['success']:
                    purged += 1
                    logging.info(f"Purged old backup: {backup['filename']}")
                else:
                    logging.warning(
                        f"Could not purge {backup['filename']}: {del_result['message']}"
                    )
        logging.info(
            f"Retention purge complete — {purged} backup(s) removed "
            f"(retention: {BACKUP_RETENTION_DAYS} days)."
        )
    except Exception as e:
        logging.error(f"Backup retention purge failed: {e}", exc_info=True)

    _record_job("daily_backup", "success",
                f"{result['filename']} ({result['size_mb']:.1f} MB); {purged} old backup(s) purged")


def _recurring_drafts_job():
    """Generate draft transactions for all active templates due today or earlier."""
    logging.info("Running recurring drafts generation…")
    try:
        n = generate_draft_transactions()
        logging.info(f"Recurring drafts: {n} transaction(s) created.")
        _record_job("recurring_drafts", "success", f"{n} transaction(s) created")
    except Exception as e:
        logging.error(f"Recurring drafts generation failed: {e}", exc_info=True)
        _record_job("recurring_drafts", "error", str(e))


def _signal_notifications_job():
    """Compute final signals for all held securities and record any changes."""
    logging.info("Running signal notifications refresh…")
    try:
        refresh_signal_notifications()
        logging.info("Signal notifications refreshed.")
        _record_job("signal_notifications", "success", "Completed OK")
    except Exception as e:
        logging.error(f"Signal notification refresh failed: {e}", exc_info=True)
        _record_job("signal_notifications", "error", str(e))


def _morning_maintenance_job():
    """VACUUM ANALYZE the database, then refresh all embeddings."""
    errors = []
    # --- VACUUM ANALYZE ---
    logging.info("Running VACUUM ANALYZE…")
    try:
        conn = get_connection()
        conn.autocommit = True          # VACUUM cannot run inside a transaction
        with conn.cursor() as cur:
            cur.execute("VACUUM ANALYZE")
        conn.close()
        logging.info("VACUUM ANALYZE completed.")
    except Exception as e:
        logging.error(f"VACUUM ANALYZE failed: {e}", exc_info=True)
        errors.append(str(e))

    # --- Embedding update ---
    logging.info("Updating transaction embeddings…")
    try:
        update_all_embeddings()
        logging.info("Embedding update completed.")
    except Exception as e:
        logging.error(f"Embedding update failed: {e}", exc_info=True)
        errors.append(str(e))

    if errors:
        _record_job("morning_maintenance", "error", "; ".join(errors))
    else:
        _record_job("morning_maintenance", "success", "VACUUM ANALYZE + embeddings OK")


# ── Main loop ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.info("Scheduler starting.")

    # Read schedules once at startup so startup-skip logic uses configured values
    _sc = _get_all_schedules()
    _ws_wd, _ws_h, _ws_m   = _parse_weekly(_sc.get('weekly_summary', ''),   WEEKLY_SUMMARY_WEEKDAY,  WEEKLY_SUMMARY_HOUR,  WEEKLY_SUMMARY_MINUTE)
    _mnt_h, _mnt_m          = _parse_daily (_sc.get('morning_maintenance',''), MAINTENANCE_HOUR, MAINTENANCE_MINUTE)

    # Run weekly summary immediately if this week's entry is missing
    if not _summary_exists_for_current_week():
        logging.info("No summary for current week — running now.")
        _weekly_summary_job()
    else:
        logging.info("Current week's summary already exists — skipping startup run.")

    # Run market data once at startup
    _last_market_refresh: datetime = datetime.min
    now = datetime.now()
    if _is_market_open(now):
        logging.info("Market is open — running initial market data refresh.")
        _market_data_job()
        _last_market_refresh = now

    # Skip weekly summary if already past the scheduled window today
    _last_weekly_summary_date: date = (
        date.today() if now.weekday() == _ws_wd and now.hour >= _ws_h else date.min
    )

    # Daily backup: skip if a backup already exists for today
    _last_backup_date: date = date.min
    try:
        _today_backups = [b for b in DatabaseBackup().get_backup_history()
                          if b['modified'].date() == date.today()]
        if _today_backups:
            _last_backup_date = date.today()
            logging.info(f"Today's backup already exists ({_today_backups[0]['filename']}) — skipping.")
    except Exception:
        pass

    # Recurring drafts: run once at startup
    logging.info("Running initial recurring drafts generation.")
    _recurring_drafts_job()
    _last_recurring_drafts_date: date = date.today()

    # Securities info: run once at startup
    logging.info("Running initial securities info refresh.")
    _securities_info_job()
    _last_securities_info_date: date = date.today()

    # Dividend history: weekly — skip if already ran this week
    _last_dividend_history_week: date = date.min

    # Monthly summary: skip if already ran this month
    _last_monthly_summary_month: int = -1

    # Signal notifications: first run deferred to tick loop
    _last_signal_refresh: datetime = datetime.min

    # Morning maintenance: skip if already past the scheduled window today
    _last_maintenance_date: date = date.min
    _now_startup = datetime.now()
    if _now_startup.hour > _mnt_h or (_now_startup.hour == _mnt_h and _now_startup.minute >= _mnt_m):
        _last_maintenance_date = date.today()
        logging.info("Past maintenance window at startup — skipping initial run.")

    # ── Tick loop ─────────────────────────────────────────────────────────────
    while True:
        time.sleep(TICK_SECONDS)
        now = datetime.now()
        sc = _get_all_schedules()

        # ── Market data: every N minutes ──────────────────────────────────────
        minutes_since_refresh = (now - _last_market_refresh).total_seconds() / 60
        if _is_market_open(now) and minutes_since_refresh >= _parse_interval(sc.get('market_data', ''), MARKET_REFRESH_INTERVAL_MINUTES):
            _market_data_job()
            _last_market_refresh = now

        # ── Recurring drafts: once per calendar day ───────────────────────────
        if _last_recurring_drafts_date != date.today():
            _recurring_drafts_job()
            _last_recurring_drafts_date = date.today()

        # ── Securities info: once per calendar day ────────────────────────────
        if _last_securities_info_date != date.today():
            _securities_info_job()
            _last_securities_info_date = date.today()

        # ── Daily backup ──────────────────────────────────────────────────────
        bkp_h, bkp_m = _parse_daily(sc.get('daily_backup', ''), BACKUP_HOUR, 0)
        if _in_window(now, bkp_h, bkp_m) and _last_backup_date != date.today():
            _backup_job()
            _last_backup_date = date.today()

        # ── Morning maintenance ───────────────────────────────────────────────
        mnt_h, mnt_m = _parse_daily(sc.get('morning_maintenance', ''), MAINTENANCE_HOUR, MAINTENANCE_MINUTE)
        if _in_window(now, mnt_h, mnt_m) and _last_maintenance_date != date.today():
            _morning_maintenance_job()
            _last_maintenance_date = date.today()

        # ── Weekly summary ────────────────────────────────────────────────────
        ws_wd, ws_h, ws_m = _parse_weekly(sc.get('weekly_summary', ''), WEEKLY_SUMMARY_WEEKDAY, WEEKLY_SUMMARY_HOUR, WEEKLY_SUMMARY_MINUTE)
        if now.weekday() == ws_wd and _in_window(now, ws_h, ws_m) and _last_weekly_summary_date != date.today():
            _weekly_summary_job()
            _last_weekly_summary_date = date.today()

        # ── Monthly summary ───────────────────────────────────────────────────
        ms_d, ms_h, ms_m = _parse_monthly(sc.get('monthly_summary', ''), MONTHLY_SUMMARY_DAY, MONTHLY_SUMMARY_HOUR, MONTHLY_SUMMARY_MINUTE)
        if now.day == ms_d and _in_window(now, ms_h, ms_m) and _last_monthly_summary_month != now.month:
            _monthly_summary_job()
            _last_monthly_summary_month = now.month

        # ── Dividend history: weekly ──────────────────────────────────────────
        _this_week_start = _current_week_start()
        dh_wd, dh_h, dh_m = _parse_weekly(sc.get('dividend_history', ''), DIVIDEND_HISTORY_WEEKDAY, DIVIDEND_HISTORY_HOUR, DIVIDEND_HISTORY_MINUTE)
        if now.weekday() == dh_wd and _in_window(now, dh_h, dh_m) and _last_dividend_history_week != _this_week_start:
            _dividend_history_job()
            _last_dividend_history_week = _this_week_start

        # ── Signal notifications: every N minutes ─────────────────────────────
        minutes_since_signal = (now - _last_signal_refresh).total_seconds() / 60
        if minutes_since_signal >= _parse_interval(sc.get('signal_notifications', ''), SIGNAL_REFRESH_INTERVAL_MINUTES):
            _signal_notifications_job()
            _last_signal_refresh = now
