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
warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy", category=UserWarning)

import logging
import time
import warnings
from datetime import date, datetime, timedelta

warnings.filterwarnings("ignore", message="No runtime found", module="streamlit")
warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy connectable")

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

# ── Market-data refresh config ────────────────────────────────────────────────
MARKET_REFRESH_INTERVAL_MINUTES = 5   # how often to refresh prices & FX (24 × 7)

# ── Daily backup config ───────────────────────────────────────────────────────
BACKUP_HOUR            = 6    # local hour at which the daily backup runs (06:00 AM)
BACKUP_RETENTION_DAYS  = 30   # delete backups older than this many days

# ── Morning maintenance config ────────────────────────────────────────────────
# Runs 15 minutes after backup (backup completes well within 5 minutes).
MAINTENANCE_HOUR   = BACKUP_HOUR  # same hour as backup (06:xx)
MAINTENANCE_MINUTE = 15           # 06:15 AM

# Tick interval — the scheduler wakes up this often to check all jobs.
# Keep it at 60 s so Monday-07:00 is never missed by more than a minute.
TICK_SECONDS = 60


# ── Helpers ───────────────────────────────────────────────────────────────────

def _current_week_start() -> date:
    today = date.today()
    return today - timedelta(days=today.weekday())


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


# ── Jobs ──────────────────────────────────────────────────────────────────────

def _weekly_summary_job():
    logging.info("Running weekly summary job…")
    try:
        run_weekly_summary()
        logging.info("Weekly summary completed.")
    except Exception as e:
        logging.error(f"Weekly summary failed: {e}", exc_info=True)


def _market_data_job():
    logging.info("Running market data refresh…")
    try:
        download_historical_prices_from_yahoo(tsperiod="1d")
        logging.info("Security prices refreshed.")
    except Exception as e:
        logging.error(f"Price refresh failed: {e}", exc_info=True)

    try:
        download_historical_prices_from_tradingview(tsperiod="1d")
        logging.info("TradingView prices refreshed.")
    except Exception as e:
        logging.error(f"TradingView price refresh failed: {e}", exc_info=True)

    try:
        download_bond_prices_from_solidus()
        logging.info("Bond prices refreshed.")
    except Exception as e:
        logging.error(f"Bond price refresh failed: {e}", exc_info=True)

    try:
        download_historical_fx(tsperiod="3d")   # 3 d to catch weekend gaps on Monday
        logging.info("FX rates refreshed.")
    except Exception as e:
        logging.error(f"FX refresh failed: {e}", exc_info=True)


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
    except Exception as e:
        logging.error(f"Securities info refresh failed: {e}", exc_info=True)


def _dividend_history_job():
    """Download full historical dividend records once per week (Sunday at 06:30).

    Runs weekly rather than daily — dividend histories change slowly and each
    call fetches a full time series per ticker (heavier than the daily info fetch).
    """
    logging.info("Running weekly dividend history refresh…")
    try:
        download_dividend_history()
        logging.info("Dividend history refresh complete.")
    except Exception as e:
        logging.error(f"Dividend history refresh failed: {e}", exc_info=True)


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
            return
    except Exception as e:
        logging.error(f"Backup job failed: {e}", exc_info=True)
        return

    # Purge backups older than the retention period
    try:
        cutoff = datetime.now() - timedelta(days=BACKUP_RETENTION_DAYS)
        purged = 0
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


def _recurring_drafts_job():
    """Generate draft transactions for all active templates due today or earlier."""
    logging.info("Running recurring drafts generation…")
    try:
        n = generate_draft_transactions()
        logging.info(f"Recurring drafts: {n} transaction(s) created.")
    except Exception as e:
        logging.error(f"Recurring drafts generation failed: {e}", exc_info=True)


def _morning_maintenance_job():
    """VACUUM ANALYZE the database, then refresh all embeddings."""
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

    # --- Embedding update ---
    logging.info("Updating transaction embeddings…")
    try:
        update_all_embeddings()
        logging.info("Embedding update completed.")
    except Exception as e:
        logging.error(f"Embedding update failed: {e}", exc_info=True)


# ── Main loop ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.info("Scheduler starting.")

    # Run weekly summary immediately if this week's entry is missing
    if not _summary_exists_for_current_week():
        logging.info("No summary for current week — running now.")
        _weekly_summary_job()
    else:
        logging.info("Current week's summary already exists — skipping startup run.")

    # Run market data once at startup if the market is currently open
    _last_market_refresh: datetime = datetime.min
    now = datetime.now()
    if _is_market_open(now):
        logging.info("Market is open — running initial market data refresh.")
        _market_data_job()
        _last_market_refresh = now
    else:
        logging.info(
            f"Market closed at startup (weekday={now.weekday()}, hour={now.hour}) — "
            "skipping initial market data refresh."
        )

    _last_weekly_summary_date: date = date.today() if datetime.now().weekday() == 0 and datetime.now().hour >= 7 else date.min

    # Daily backup: skip today's run if a backup file for today already exists
    # (prevents duplicate backups when the container is restarted mid-day)
    _last_backup_date: date = date.min
    try:
        _today_backups = [
            b for b in DatabaseBackup().get_backup_history()
            if b['modified'].date() == date.today()
        ]
        if _today_backups:
            _last_backup_date = date.today()
            logging.info(
                f"Today's backup already exists ({_today_backups[0]['filename']}) — skipping."
            )
    except Exception:
        pass  # if check fails, let the normal schedule handle it

    # Recurring drafts: run once at startup
    logging.info("Running initial recurring drafts generation.")
    _recurring_drafts_job()
    _last_recurring_drafts_date: date = date.today()

    # Securities info: run once at startup every day
    _last_securities_info_date: date = date.min
    logging.info("Running initial securities info refresh.")
    _securities_info_job()
    _last_securities_info_date = date.today()

    # Dividend history: weekly (Sunday); skip if already ran this week
    _last_dividend_history_week: date = date.min

    # Morning maintenance (VACUUM ANALYZE + embeddings): skip if already ran today
    _last_maintenance_date: date = date.min
    _now_startup = datetime.now()
    if (
        _now_startup.hour > MAINTENANCE_HOUR
        or (_now_startup.hour == MAINTENANCE_HOUR and _now_startup.minute >= MAINTENANCE_MINUTE)
    ):
        # Assume maintenance already ran if we're starting up after its scheduled window
        _last_maintenance_date = date.today()
        logging.info("Past maintenance window at startup — skipping initial run.")

    # Tick loop — wakes every TICK_SECONDS and evaluates each job
    while True:
        time.sleep(TICK_SECONDS)
        now = datetime.now()

        # ── Market data: every N minutes during market hours ──────────────────
        minutes_since_refresh = (now - _last_market_refresh).total_seconds() / 60
        if _is_market_open(now) and minutes_since_refresh >= MARKET_REFRESH_INTERVAL_MINUTES:
            _market_data_job()
            _last_market_refresh = now

        # ── Recurring drafts: once per calendar day ───────────────────────────
        if _last_recurring_drafts_date != date.today():
            _recurring_drafts_job()
            _last_recurring_drafts_date = date.today()

        # ── Securities info: once per calendar day (including weekends) ─────────
        if _last_securities_info_date != date.today():
            _securities_info_job()
            _last_securities_info_date = date.today()

        # ── Daily backup at BACKUP_HOUR (fire once per calendar day) ─────────
        if (
            now.hour == BACKUP_HOUR
            and now.minute < 5          # within the first 5 minutes of the hour
            and _last_backup_date != date.today()
        ):
            _backup_job()
            _last_backup_date = date.today()

        # ── Weekly summary: Monday at 07:00 (fire once per Monday) ───────────
        if (
            now.weekday() == 0          # Monday
            and now.hour == 7
            and now.minute < 5          # within the first 5 minutes of 07:00
            and _last_weekly_summary_date != date.today()
        ):
            _weekly_summary_job()
            _last_weekly_summary_date = date.today()

        # ── Morning maintenance: VACUUM ANALYZE + embeddings at 06:15 ───────────
        if (
            now.hour == MAINTENANCE_HOUR
            and MAINTENANCE_MINUTE <= now.minute < MAINTENANCE_MINUTE + 5
            and _last_maintenance_date != date.today()
        ):
            _morning_maintenance_job()
            _last_maintenance_date = date.today()

        # ── Dividend history: Sunday at 06:30 (once per week) ────────────────
        _this_week_start = _current_week_start()
        if (
            now.weekday() == 6          # Sunday
            and now.hour == 6
            and 30 <= now.minute < 35
            and _last_dividend_history_week != _this_week_start
        ):
            _dividend_history_job()
            _last_dividend_history_week = _this_week_start
