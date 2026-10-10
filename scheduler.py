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
• News fetch        : every NEWS_FETCH_INTERVAL_MINUTES (24 × 7).
                      Held/watchlisted securities, institutions, and opted-in payees.
• Interest rates    : every INTEREST_RATES_INTERVAL_MINUTES (24 × 7).
                      €STR / ECB deposit rate (ECB), SOFR / EFFR / Fed target (NY Fed).

Every job runs under a watchdog (see _guard): a job that hangs — typically a network call that
never answers — is abandoned after its time limit, recorded as an error, and the loop carries on;
if it is still stuck well past the limit the process exits so Docker (restart: unless-stopped)
starts a clean one.
"""

import warnings
warnings.filterwarnings("ignore", message="No runtime found", category=UserWarning)
warnings.filterwarnings("ignore", message="No runtime found", module="streamlit")
warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy", category=UserWarning)
warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy connectable")

import sys
import logging
import re
import threading
import time
from datetime import date, datetime, timedelta

# On a non-UTF-8 console (e.g. run directly on Windows rather than in the
# Docker service's Linux/UTF-8 environment) the ✔/⚠️/❌ progress characters
# the downloaders print can't be encoded, raising UnicodeEncodeError mid-job
# and silently discarding whatever it had already fetched. Reconfigure to
# UTF-8 so those prints can't crash a run — see api/main.py for the same fix.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="backslashreplace")

from ai.weekly_summary import run as run_weekly_summary
from ai.monthly_summary import run as run_monthly_summary
from ai.news_fetch import run as run_news_fetch
from data.downloaders import (
    download_historical_prices_from_yahoo,
    download_historical_prices_from_tradingview,
    download_bond_prices_from_solidus,
    download_historical_fx,
    download_securities_info_from_yahoo,
    download_securities_info_from_tradingview,
    download_dividend_history,
    download_stock_splits,
    download_fund_composition,
    download_securities_fundamentals,
    download_shiller_cape,
    download_country_cape_ratios,
    download_interest_rates,
)
from ai.update_vector import update_all_embeddings
from database.backup import DatabaseBackup
from database.connection import get_connection
from database.crud import generate_draft_transactions
from database.queries import refresh_signal_notifications, refresh_fundamentals_notifications

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
STOCK_SPLITS_WEEKDAY    = 6   # Sunday at 07:00 — offset from dividend_history to avoid contention
STOCK_SPLITS_HOUR       = 7
STOCK_SPLITS_MINUTE     = 0
FUND_COMPOSITION_DAY     = 2  # 2nd of month at 07:30 — avoids colliding with monthly_summary (day 1)
FUND_COMPOSITION_HOUR    = 7
FUND_COMPOSITION_MINUTE  = 30
FUND_COUNTRIES_DAY       = 5  # 5th of month at 07:30 — after fund_composition (2nd) and fundamentals (3rd)
FUND_COUNTRIES_HOUR      = 7
FUND_COUNTRIES_MINUTE    = 30
FUNDAMENTALS_DAY         = 3  # 3rd of month at 07:30 — avoids colliding with fund_composition (day 2)
FUNDAMENTALS_HOUR        = 7
FUNDAMENTALS_MINUTE      = 30
SHILLER_CAPE_DAY         = 4  # 4th of month at 07:30 — avoids colliding with fundamentals (day 3)
SHILLER_CAPE_HOUR        = 7
SHILLER_CAPE_MINUTE      = 30
SIGNAL_REFRESH_INTERVAL_MINUTES = 30  # Every 30 min, 24×7
NEWS_FETCH_INTERVAL_MINUTES = 240     # Every 4 hours, 24×7
INTEREST_RATES_INTERVAL_MINUTES = 120 # Every 2 hours, 24×7 — €STR/SOFR publish once a morning, so this catches them promptly

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


# ── Watchdog ──────────────────────────────────────────────────────────────────
# The tick loop is single-threaded, so one job that never returns (a network call with no
# answer, a lock that is never released) used to stop *every* job behind it — market data
# included — until someone restarted the container. Each job now runs in a worker thread that
# the loop waits on only up to a limit.

DEFAULT_JOB_TIMEOUT_MIN = 60
JOB_TIMEOUT_MIN = {                   # job_id -> minutes a single run may take
    "market_data": 35, "signal_notifications": 15, "news_fetch": 15, "interest_rates": 10,
    "recurring_drafts": 10, "credit_card_payments": 10, "saxo_token_refresh": 5, "shiller_cape": 15, "daily_backup": 45,
    "securities_info": 90, "dividend_history": 120, "stock_splits": 90, "fund_composition": 120, "fund_countries": 60,
    "fundamentals": 180, "morning_maintenance": 180, "weekly_summary": 90, "monthly_summary": 90,
}
# Market-data sub-steps are also limited one by one, so a stuck Yahoo call does not cost the
# TradingView, bond and FX updates behind it.
STEP_TIMEOUT_MIN = {"yahoo": 10, "tradingview": 10, "solidus": 3, "fx": 5}
# A run abandoned at its limit but still alive after this many times the limit means the process
# can't be trusted any more (stuck threads pile up, a DB lock may be held): exit and let Docker restart.
HARD_EXIT_FACTOR = 2

_workers: dict[str, tuple[threading.Thread, datetime, int]] = {}   # job_id -> (thread, started, limit_min)


def _call_with_timeout(name: str, fn, timeout_min: float):
    """Run fn() in a daemon thread and wait at most timeout_min. Returns (finished, error).
    A call that does not finish is abandoned (a thread cannot be killed) — its thread is left
    in _workers so the watchdog can see it is still alive."""
    box: dict = {}

    def _target():
        try:
            fn()
        except BaseException as exc:                 # noqa: BLE001 — report, never kill the thread silently
            box["error"] = exc

    th = threading.Thread(target=_target, name=f"job-{name}", daemon=True)
    th.start()
    th.join(timeout_min * 60)
    if th.is_alive():
        _workers[name] = (th, datetime.now(), timeout_min)
        return False, None
    _workers.pop(name, None)
    return True, box.get("error")


def _check_stuck():
    """Exit the process when an abandoned job is still running long past its limit."""
    for name, (th, started, limit) in list(_workers.items()):
        if not th.is_alive():
            _workers.pop(name, None)
            continue
        age_min = (datetime.now() - started).total_seconds() / 60
        if age_min >= limit * (HARD_EXIT_FACTOR - 1):
            logging.critical(f"Job '{name}' has been stuck for {age_min + limit:.0f} min "
                             f"(limit {limit:.0f} min) — exiting so the container restarts.")
            _record_job(name, "error", f"stuck for over {age_min + limit:.0f} min — scheduler restarted")
            logging.shutdown()
            _os._exit(1)


def _guard(job_id: str, fn) -> bool:
    """Run a job under the watchdog. False when it was skipped or did not finish in time."""
    prev = _workers.get(job_id)
    if prev and prev[0].is_alive():
        logging.error(f"Skipping '{job_id}': the previous run is still stuck.")
        return False
    limit = JOB_TIMEOUT_MIN.get(job_id, DEFAULT_JOB_TIMEOUT_MIN)
    done, _err = _call_with_timeout(job_id, fn, limit)
    if not done:
        msg = f"timed out after {limit} min — abandoned, will retry on the next run"
        logging.error(f"Job '{job_id}' {msg}.")
        _record_job(job_id, "error", msg)
        return False
    if _err is not None:                      # a job normally records its own failure; this catches one that didn't
        logging.error(f"Job '{job_id}' failed: {_err}", exc_info=_err)
        _record_job(job_id, "error", str(_err))
        return False
    return True


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

    def _step(label: str, key: str, fn):
        prev = _workers.get(f"market_data:{key}")
        if prev and prev[0].is_alive():
            logging.error(f"{label}: the previous run is still stuck — skipped.")
            errors.append(f"{label}: previous run still stuck")
            return
        done, err = _call_with_timeout(f"market_data:{key}", fn, STEP_TIMEOUT_MIN[key])
        if not done:
            logging.error(f"{label} timed out after {STEP_TIMEOUT_MIN[key]} min — skipped.")
            errors.append(f"{label} timed out after {STEP_TIMEOUT_MIN[key]} min")
        elif err is not None:
            logging.error(f"{label} failed: {err}", exc_info=err)
            errors.append(str(err))
        else:
            logging.info(f"{label} refreshed.")

    _step("Security prices", "yahoo", lambda: download_historical_prices_from_yahoo(tsperiod="1d"))
    _step("TradingView prices", "tradingview", lambda: download_historical_prices_from_tradingview(tsperiod="1d"))
    _step("Bond prices", "solidus", download_bond_prices_from_solidus)
    _step("FX rates", "fx", lambda: download_historical_fx(tsperiod="3d"))   # 3 d to catch weekend gaps on Monday

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


def _stock_splits_job():
    """Download stock split history once per week (Sunday at 07:00).

    Runs weekly, same cadence as dividend history — split events are rare and
    each call fetches a full time series per ticker.
    """
    logging.info("Running weekly stock split refresh…")
    try:
        download_stock_splits()
        logging.info("Stock split refresh complete.")
        _record_job("stock_splits", "success", "Completed OK")
    except Exception as e:
        logging.error(f"Stock split refresh failed: {e}", exc_info=True)
        _record_job("stock_splits", "error", str(e))


def _fund_composition_job():
    """Download ETF/Mutual Fund look-through composition once per month.

    Runs monthly rather than weekly — fund composition (sector weights,
    top holdings, expense ratio) drifts slowly and each call is a heavier
    fetch than the dividend refresh, so weekly would be needless request volume.
    """
    logging.info("Running monthly fund composition refresh…")
    try:
        download_fund_composition()
        logging.info("Fund composition refresh complete.")
        _record_job("fund_composition", "success", "Completed OK")
    except Exception as e:
        logging.error(f"Fund composition refresh failed: {e}", exc_info=True)
        _record_job("fund_composition", "error", str(e))


def _fund_countries_job():
    """Refresh every held fund's country breakdown from its provider, once a month."""
    logging.info("Running fund country exposure refresh…")
    try:
        from data.country_exposure_downloader import download_country_exposure
        res = download_country_exposure()
        for fnd in res["funds"]:
            logging.info(f"Fund country exposure: {fnd['name']}: {fnd['status']} — {fnd['message']}")
        msg = f"{res['updated']} updated, {res['skipped']} kept, {res['errors']} failed"
        bad = "; ".join(f"{x['name']}: {x['message']}" for x in res["funds"] if x["status"] == "error")
        _record_job("fund_countries", "error" if res["errors"] else "success", msg + (": " + bad if bad else ""))
    except Exception as e:
        logging.error(f"Fund country exposure refresh failed: {e}", exc_info=True)
        _record_job("fund_countries", "error", str(e))


def _fundamentals_job():
    """Download stock financial statements (balance sheet, income statement, cash
    flow) once per month, feeding the Piotroski F-Score / Altman Z-Score shown in
    Securities Analysis.

    Runs monthly, same cadence and reasoning as fund composition — the underlying
    filings only change quarterly at most, and each call is 3 statement fetches
    per ticker (heavier than the daily info/quote refresh).
    """
    logging.info("Running monthly securities fundamentals refresh…")
    try:
        download_securities_fundamentals()
        logging.info("Securities fundamentals refresh complete.")
        _record_job("fundamentals", "success", "Completed OK")
    except Exception as e:
        logging.error(f"Securities fundamentals refresh failed: {e}", exc_info=True)
        _record_job("fundamentals", "error", str(e))


def _shiller_cape_job():
    """Download U.S. Shiller CAPE (shillerdata.com) and country-level CAPE
    ratios (Siblis Research's free API) once per month, feeding the Dashboard's
    market valuation tile and Market Data -> CAPE Ratios. Both sources only
    publish new snapshots monthly at most, so a monthly cadence is all a more
    frequent run could ever surface as new. A failure in one doesn't skip the
    other — they're independent sources."""
    logging.info("Running monthly CAPE (market valuation) refresh…")
    errors = []
    try:
        result = download_shiller_cape()
        if result.get("error"):
            errors.append(f"Shiller CAPE: {result['error']}")
        else:
            logging.info(f"Shiller CAPE refresh complete ({result.get('rows', 0)} rows).")
    except Exception as e:
        logging.error(f"Shiller CAPE refresh failed: {e}", exc_info=True)
        errors.append(f"Shiller CAPE: {e}")
    try:
        result = download_country_cape_ratios()
        logging.info(f"Country CAPE refresh complete ({result.get('rows', 0)} snapshots).")
        errors.extend(result.get("errors", []))
    except Exception as e:
        logging.error(f"Country CAPE refresh failed: {e}", exc_info=True)
        errors.append(f"Country CAPE: {e}")
    if errors:
        _record_job("shiller_cape", "error", "; ".join(errors))
    else:
        _record_job("shiller_cape", "success", "Completed OK")


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


def _credit_card_payments_job():
    """Recalculate every credit-card payment template's amount from its card's latest statement."""
    logging.info("Running credit-card payment amounts refresh…")
    try:
        from database.card_statements import refresh_card_payment_templates
        res = refresh_card_payment_templates()
        for line in res["details"]:
            logging.info(f"Card payment: {line}")
        msg = f"{res['updated']} updated, {res['unchanged']} unchanged" + (f", {res['skipped']} skipped" if res["skipped"] else "")
        _record_job("credit_card_payments", "error" if res["skipped"] else "success", msg + (": " + "; ".join(res["details"][:3]) if res["skipped"] else ""))
    except Exception as e:
        logging.error(f"Credit-card payment refresh failed: {e}", exc_info=True)
        _record_job("credit_card_payments", "error", str(e))


def _saxo_token_refresh_job():
    """Keep the Saxo login alive: refresh the token before the refresh token lapses (see data/saxo_session.py)."""
    try:
        from data.saxo_session import ensure_access_token, SaxoNotConnected
        try:
            ensure_access_token(force=True)
            _record_job("saxo_token_refresh", "success", "Token refreshed")
        except SaxoNotConnected as e:
            _record_job("saxo_token_refresh", "success" if "not connected yet" in str(e) else "error", str(e))
    except Exception as e:
        logging.warning(f"Saxo token refresh failed: {e}")
        _record_job("saxo_token_refresh", "error", str(e)[:300])


def _signal_notifications_job():
    """Compute final signals for all held securities and record any changes.
    Also checks Altman Z-Score risk zones (Safe/Grey/Distress) for the same kind
    of change — cheap (reads already-cached fundamentals, no heavy query) and a
    zone can move between the monthly fundamentals refresh and now since it also
    depends on today's market cap, so it rides this same frequent cadence rather
    than the monthly download."""
    logging.info("Running signal notifications refresh…")
    try:
        refresh_signal_notifications()
        refresh_fundamentals_notifications()
        logging.info("Signal notifications refreshed.")
        _record_job("signal_notifications", "success", "Completed OK")
    except Exception as e:
        logging.error(f"Signal notification refresh failed: {e}", exc_info=True)
        _record_job("signal_notifications", "error", str(e))


def _news_fetch_job():
    logging.info("Running news fetch…")
    try:
        counts = run_news_fetch()
        logging.info(f"News fetch completed: {counts}")
        _record_job("news_fetch", "success",
                     f"{sum(counts.values())} new item(s) "
                     f"(security {counts['security']}, institution {counts['institution']}, payee {counts['payee']})")
    except Exception as e:
        logging.error(f"News fetch failed: {e}", exc_info=True)
        _record_job("news_fetch", "error", str(e))


def _interest_rates_job():
    """Refresh €STR / ECB deposit rate (ECB) and SOFR / EFFR / Fed target range (NY Fed).
    The Dashboard's rate alerts are computed live from what this stores."""
    logging.info("Running interest rates refresh…")
    try:
        result = download_interest_rates()
        if result["errors"]:
            _record_job("interest_rates", "error", "; ".join(result["errors"]))
        else:
            _record_job("interest_rates", "success", f"{result['rows']} rows updated")
    except Exception as e:
        logging.error(f"Interest rates refresh failed: {e}", exc_info=True)
        _record_job("interest_rates", "error", str(e))


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
        _guard('weekly_summary', _weekly_summary_job)
    else:
        logging.info("Current week's summary already exists — skipping startup run.")

    # Run market data once at startup
    _last_market_refresh: datetime = datetime.min
    now = datetime.now()
    if _is_market_open(now):
        logging.info("Market is open — running initial market data refresh.")
        _guard('market_data', _market_data_job)
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

    # Credit-card payment amounts: refresh at startup, then daily (before drafts are generated)
    _guard('credit_card_payments', _credit_card_payments_job)
    _last_card_payments_date: date = date.today()

    # Recurring drafts: run once at startup
    logging.info("Running initial recurring drafts generation.")
    _guard('recurring_drafts', _recurring_drafts_job)
    _last_recurring_drafts_date: date = date.today()

    # Securities info: run once at startup
    logging.info("Running initial securities info refresh.")
    _guard('securities_info', _securities_info_job)
    _last_securities_info_date: date = date.today()

    # Dividend history: weekly — skip if already ran this week
    _last_dividend_history_week: date = date.min

    # Stock splits: weekly — skip if already ran this week
    _last_stock_splits_week: date = date.min

    # Monthly summary: skip if already ran this month
    _last_monthly_summary_month: int = -1

    # Fund country exposure: skip if already ran this month
    _last_fund_countries_month: int = -1

    # Fund composition: skip if already ran this month
    _last_fund_composition_month: int = -1

    # Fundamentals (F-Score/Z-Score): skip if already ran this month
    _last_fundamentals_month: int = -1

    # Shiller CAPE: skip if already ran this month
    _last_shiller_cape_month: int = -1

    # Signal notifications: first run deferred to tick loop
    _last_signal_refresh: datetime = datetime.min

    # News fetch: first run deferred to tick loop
    _last_news_fetch: datetime = datetime.min

    # Interest rates: first run deferred to tick loop
    _last_interest_rates: datetime = datetime.min

    # Saxo login keep-alive: first run deferred to tick loop
    _last_saxo_refresh: datetime = datetime.min

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
        _check_stuck()

        # ── Market data: every N minutes ──────────────────────────────────────
        minutes_since_refresh = (now - _last_market_refresh).total_seconds() / 60
        if _is_market_open(now) and minutes_since_refresh >= _parse_interval(sc.get('market_data', ''), MARKET_REFRESH_INTERVAL_MINUTES):
            _guard('market_data', _market_data_job)
            _last_market_refresh = now

        # ── Credit-card payment amounts: once per calendar day, from the configured time ──
        cc_h, cc_m = _parse_daily(sc.get('credit_card_payments', ''), 5, 45)
        if _last_card_payments_date != date.today() and (now.hour, now.minute) >= (cc_h, cc_m):
            _guard('credit_card_payments', _credit_card_payments_job)
            _last_card_payments_date = date.today()

        # ── Recurring drafts: once per calendar day ───────────────────────────
        if _last_recurring_drafts_date != date.today():
            _guard('recurring_drafts', _recurring_drafts_job)
            _last_recurring_drafts_date = date.today()

        # ── Securities info: once per calendar day ────────────────────────────
        if _last_securities_info_date != date.today():
            _guard('securities_info', _securities_info_job)
            _last_securities_info_date = date.today()

        # ── Daily backup ──────────────────────────────────────────────────────
        bkp_h, bkp_m = _parse_daily(sc.get('daily_backup', ''), BACKUP_HOUR, 0)
        if _in_window(now, bkp_h, bkp_m) and _last_backup_date != date.today():
            _guard('daily_backup', _backup_job)
            _last_backup_date = date.today()

        # ── Morning maintenance ───────────────────────────────────────────────
        mnt_h, mnt_m = _parse_daily(sc.get('morning_maintenance', ''), MAINTENANCE_HOUR, MAINTENANCE_MINUTE)
        if _in_window(now, mnt_h, mnt_m) and _last_maintenance_date != date.today():
            _guard('morning_maintenance', _morning_maintenance_job)
            _last_maintenance_date = date.today()

        # ── Weekly summary ────────────────────────────────────────────────────
        ws_wd, ws_h, ws_m = _parse_weekly(sc.get('weekly_summary', ''), WEEKLY_SUMMARY_WEEKDAY, WEEKLY_SUMMARY_HOUR, WEEKLY_SUMMARY_MINUTE)
        if now.weekday() == ws_wd and _in_window(now, ws_h, ws_m) and _last_weekly_summary_date != date.today():
            _guard('weekly_summary', _weekly_summary_job)
            _last_weekly_summary_date = date.today()

        # ── Monthly summary ───────────────────────────────────────────────────
        ms_d, ms_h, ms_m = _parse_monthly(sc.get('monthly_summary', ''), MONTHLY_SUMMARY_DAY, MONTHLY_SUMMARY_HOUR, MONTHLY_SUMMARY_MINUTE)
        if now.day == ms_d and _in_window(now, ms_h, ms_m) and _last_monthly_summary_month != now.month:
            _guard('monthly_summary', _monthly_summary_job)
            _last_monthly_summary_month = now.month

        # ── Dividend history: weekly ──────────────────────────────────────────
        _this_week_start = _current_week_start()
        dh_wd, dh_h, dh_m = _parse_weekly(sc.get('dividend_history', ''), DIVIDEND_HISTORY_WEEKDAY, DIVIDEND_HISTORY_HOUR, DIVIDEND_HISTORY_MINUTE)
        if now.weekday() == dh_wd and _in_window(now, dh_h, dh_m) and _last_dividend_history_week != _this_week_start:
            _guard('dividend_history', _dividend_history_job)
            _last_dividend_history_week = _this_week_start

        # ── Stock splits: weekly ───────────────────────────────────────────────
        ss_wd, ss_h, ss_m = _parse_weekly(sc.get('stock_splits', ''), STOCK_SPLITS_WEEKDAY, STOCK_SPLITS_HOUR, STOCK_SPLITS_MINUTE)
        if now.weekday() == ss_wd and _in_window(now, ss_h, ss_m) and _last_stock_splits_week != _this_week_start:
            _guard('stock_splits', _stock_splits_job)
            _last_stock_splits_week = _this_week_start

        # ── Fund country exposure: monthly ────────────────────────────────────
        fx_d, fx_h, fx_m = _parse_monthly(sc.get('fund_countries', ''), FUND_COUNTRIES_DAY, FUND_COUNTRIES_HOUR, FUND_COUNTRIES_MINUTE)
        if now.day == fx_d and _in_window(now, fx_h, fx_m) and _last_fund_countries_month != now.month:
            _guard('fund_countries', _fund_countries_job)
            _last_fund_countries_month = now.month

        # ── Fund composition (Portfolio X-Ray): monthly ───────────────────────
        fc_d, fc_h, fc_m = _parse_monthly(sc.get('fund_composition', ''), FUND_COMPOSITION_DAY, FUND_COMPOSITION_HOUR, FUND_COMPOSITION_MINUTE)
        if now.day == fc_d and _in_window(now, fc_h, fc_m) and _last_fund_composition_month != now.month:
            _guard('fund_composition', _fund_composition_job)
            _last_fund_composition_month = now.month

        # ── Fundamentals (F-Score/Z-Score): monthly ───────────────────────────
        fn_d, fn_h, fn_m = _parse_monthly(sc.get('fundamentals', ''), FUNDAMENTALS_DAY, FUNDAMENTALS_HOUR, FUNDAMENTALS_MINUTE)
        if now.day == fn_d and _in_window(now, fn_h, fn_m) and _last_fundamentals_month != now.month:
            _guard('fundamentals', _fundamentals_job)
            _last_fundamentals_month = now.month

        # ── Shiller CAPE: monthly ──────────────────────────────────────────────
        sh_d, sh_h, sh_m = _parse_monthly(sc.get('shiller_cape', ''), SHILLER_CAPE_DAY, SHILLER_CAPE_HOUR, SHILLER_CAPE_MINUTE)
        if now.day == sh_d and _in_window(now, sh_h, sh_m) and _last_shiller_cape_month != now.month:
            _guard('shiller_cape', _shiller_cape_job)
            _last_shiller_cape_month = now.month

        # ── Signal notifications: every N minutes ─────────────────────────────
        minutes_since_signal = (now - _last_signal_refresh).total_seconds() / 60
        if minutes_since_signal >= _parse_interval(sc.get('signal_notifications', ''), SIGNAL_REFRESH_INTERVAL_MINUTES):
            _guard('signal_notifications', _signal_notifications_job)
            _last_signal_refresh = now

        # ── News fetch: every N minutes ───────────────────────────────────────
        minutes_since_news = (now - _last_news_fetch).total_seconds() / 60
        if minutes_since_news >= _parse_interval(sc.get('news_fetch', ''), NEWS_FETCH_INTERVAL_MINUTES):
            _guard('news_fetch', _news_fetch_job)
            _last_news_fetch = now

        # ── Saxo login keep-alive: every N minutes (the refresh token is short-lived) ──
        if (now - _last_saxo_refresh).total_seconds() / 60 >= _parse_interval(sc.get('saxo_token_refresh', ''), 10):
            _guard('saxo_token_refresh', _saxo_token_refresh_job)
            _last_saxo_refresh = now

        # ── Interest rates (€STR, ECB, Fed, SOFR): every N minutes ────────────
        minutes_since_rates = (now - _last_interest_rates).total_seconds() / 60
        if minutes_since_rates >= _parse_interval(sc.get('interest_rates', ''), INTEREST_RATES_INTERVAL_MINUTES):
            _guard('interest_rates', _interest_rates_job)
            _last_interest_rates = now
