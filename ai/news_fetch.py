"""
News fetching
=============
Populates the `News_Items` table from three sources:

  - Securities currently held (Holdings) or watched (Watchlist), via yfinance's
    per-ticker news feed.
  - Institutions the user has an active account with, via a DuckDuckGo news
    search on the institution's name.
  - Payees opted in for tracking (Payees.Track_For_News = TRUE — e.g. an
    employer), via the same DuckDuckGo news search.

Run this script (or schedule it via scheduler.py) periodically.

Usage:
    python -m ai.news_fetch
"""

import logging
import time
import warnings
from datetime import datetime, timezone

warnings.filterwarnings("ignore", message="No runtime found", module="streamlit")
warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy connectable")

import yfinance as yf
from ddgs import DDGS
from dateutil import parser as date_parser

from database.connection import get_connection
from ai.llm import get_custom_session

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Pause between DDG queries, and retry/backoff on any failure — the
# unofficial API throttles hard (HTTP 403 "Ratelimit") after just a handful of
# queries in quick succession, and can also fail with a plain connection
# TimeoutError under load, which silently starves whichever tier runs later
# in a batch (Institution before Payee) unless we back off and retry.
_DDG_PAUSE_SECONDS = 3.0
_DDG_MAX_RETRIES = 2
_DDG_RETRY_BACKOFF_SECONDS = 15


def _ddg_news_search(query: str, max_results: int) -> list:
    """DDGS().news() with retry-with-backoff on any failure (rate-limit, timeout, etc.)."""
    delay = _DDG_RETRY_BACKOFF_SECONDS
    for attempt in range(_DDG_MAX_RETRIES + 1):
        try:
            with DDGS() as ddgs:
                return list(ddgs.news(query, max_results=max_results))
        except Exception as exc:
            if attempt < _DDG_MAX_RETRIES:
                logging.warning(f"DDG search failed for '{query}' ({exc}) — retrying in {delay}s...")
                time.sleep(delay)
                delay *= 2
                continue
            raise


# ──────────────────────────────────────────────────────────────────────────────
# NORMALIZATION
# ──────────────────────────────────────────────────────────────────────────────

def _parse_published_at(raw) -> str | None:
    """Best-effort parse of a news item's date into an ISO timestamp.

    ddgs aggregates across multiple engines (DuckDuckGo, Bing, Yahoo), and
    they don't agree on date format — some return a clean ISO/RFC datetime,
    others a relative string like "6 days ago" or even "Opinion6 days ago"
    (a section label glued onto the age with no separator). Deliberately
    *not* using fuzzy parsing: fuzzy mode happily misreads "6 days ago" as
    "the 6th of this month" — a wrong-but-plausible date is worse than no
    date, so anything not already a real absolute date/time string falls
    back to None (the UI falls back to Fetched_At when this is unset).
    """
    if not raw:
        return None
    try:
        return date_parser.parse(str(raw)).isoformat()
    except (ValueError, OverflowError, TypeError):
        return None


def _normalize_yf_news_item(raw: dict) -> dict | None:
    """yfinance has shipped both a flat and a nested ('content') news shape
    across versions — handle either."""
    content = raw.get("content") if isinstance(raw.get("content"), dict) else None
    if content:
        title = content.get("title")
        url = (content.get("canonicalUrl") or {}).get("url") \
            or (content.get("clickThroughUrl") or {}).get("url")
        publisher = (content.get("provider") or {}).get("displayName")
        summary = content.get("summary")
        published_at = content.get("pubDate")
    else:
        title = raw.get("title")
        url = raw.get("link")
        publisher = raw.get("publisher")
        summary = None
        ts = raw.get("providerPublishTime")
        published_at = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None

    if not title or not url:
        return None
    return {"title": title, "url": url, "publisher": publisher, "summary": summary, "published_at": _parse_published_at(published_at)}


def _normalize_ddg_news_item(raw: dict) -> dict | None:
    title = raw.get("title")
    url = raw.get("url")
    if not title or not url:
        return None
    return {
        "title": title,
        "url": url,
        "publisher": raw.get("source"),
        "summary": raw.get("body"),
        "published_at": _parse_published_at(raw.get("date")),
    }


# ──────────────────────────────────────────────────────────────────────────────
# PERSISTENCE
# ──────────────────────────────────────────────────────────────────────────────

def _upsert_news_items(conn, source_type: str, source_id: int, items: list) -> int:
    """Insert *items* for (source_type, source_id), skipping ones already seen
    (same URL). Returns the number of new rows inserted.

    Each item commits (or rolls back) on its own — a single malformed row
    (bad date, oversized field, etc.) must not abort the whole batch, since
    this connection is reused across every institution/payee in the same
    fetch run and a still-open failed transaction would silently break every
    insert after it.
    """
    inserted = 0
    with conn.cursor() as cur:
        for it in items:
            try:
                cur.execute("""
                    INSERT INTO News_Items (Source_Type, Source_Id, Title, Url, Publisher, Summary, Published_At)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (Source_Type, Source_Id, Url) DO NOTHING
                """, (
                    source_type, source_id, it["title"][:2000], it["url"][:2000],
                    (it.get("publisher") or None), it.get("summary"), it.get("published_at"),
                ))
                conn.commit()
                inserted += cur.rowcount
            except Exception as exc:
                conn.rollback()
                logging.warning(f"Skipped one news item for {source_type} {source_id} ({it.get('url')}): {exc}")
    return inserted


# ──────────────────────────────────────────────────────────────────────────────
# FETCHERS
# ──────────────────────────────────────────────────────────────────────────────

def fetch_security_news(max_per_security: int = 8) -> int:
    """Tier 1: news for every held or watchlisted security with a Yahoo_Ticker."""
    conn = get_connection()
    total = 0
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT s.Securities_Id, s.Yahoo_Ticker
                FROM Securities s
                WHERE s.Yahoo_Ticker IS NOT NULL AND s.Yahoo_Ticker <> ''
                  -- Excludes ISINs stored in Yahoo_Ticker in place of a real ticker (e.g. Hellenic
                  -- T-Bills, which have no Yahoo symbol at all) — always 12 chars, never a valid ticker,
                  -- and would otherwise fail this same yfinance call on every run forever.
                  AND LENGTH(s.Yahoo_Ticker) <> 12
                  AND (
                    s.Securities_Id IN (SELECT Securities_Id FROM Holdings WHERE Quantity > 0)
                    OR s.Securities_Id IN (SELECT Securities_Id FROM Watchlist)
                  )
            """)
            targets = cur.fetchall()

        session = get_custom_session()
        for sec_id, ticker in targets:
            try:
                raw_items = yf.Ticker(ticker, session=session).news or []
                items = [x for x in (_normalize_yf_news_item(r) for r in raw_items) if x][:max_per_security]
                n = _upsert_news_items(conn, "Security", sec_id, items)
                total += n
            except Exception as exc:
                logging.warning(f"Security news fetch failed for {ticker}: {exc}")
    finally:
        conn.close()
    logging.info(f"Security news: {total} new item(s).")
    return total


def fetch_institution_news(max_per_institution: int = 5) -> int:
    """Tier 2a: news for every institution with at least one account."""
    conn = get_connection()
    total = 0
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT i.Institutions_Id, i.Institutions_Name
                FROM Institutions i
                JOIN Accounts a ON a.Institutions_Id = i.Institutions_Id
            """)
            targets = cur.fetchall()

        for inst_id, name in targets:
            try:
                raw_items = _ddg_news_search(name, max_per_institution)
                items = [x for x in (_normalize_ddg_news_item(r) for r in raw_items) if x]
                total += _upsert_news_items(conn, "Institution", inst_id, items)
            except Exception as exc:
                logging.warning(f"Institution news fetch failed for '{name}': {exc}")
            time.sleep(_DDG_PAUSE_SECONDS)
    finally:
        conn.close()
    logging.info(f"Institution news: {total} new item(s).")
    return total


def fetch_payee_news(max_per_payee: int = 5) -> int:
    """Tier 2b: news for payees explicitly opted in via Track_For_News."""
    conn = get_connection()
    total = 0
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT Payees_Id, Payees_Name FROM Payees WHERE Track_For_News = TRUE")
            targets = cur.fetchall()

        for payee_id, name in targets:
            try:
                raw_items = _ddg_news_search(name, max_per_payee)
                items = [x for x in (_normalize_ddg_news_item(r) for r in raw_items) if x]
                total += _upsert_news_items(conn, "Payee", payee_id, items)
            except Exception as exc:
                logging.warning(f"Payee news fetch failed for '{name}': {exc}")
            time.sleep(_DDG_PAUSE_SECONDS)
    finally:
        conn.close()
    logging.info(f"Payee news: {total} new item(s).")
    return total


# ──────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ──────────────────────────────────────────────────────────────────────────────

def run() -> dict:
    logging.info("Starting news fetch...")
    counts = {
        "security": fetch_security_news(),
        "institution": fetch_institution_news(),
        "payee": fetch_payee_news(),
    }
    logging.info(f"News fetch complete: {counts}")
    return counts


if __name__ == "__main__":
    run()
