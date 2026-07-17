"""News API endpoints: securities/watchlist, institutions, and opted-in payees."""
from fastapi import APIRouter, Query, HTTPException
from typing import Optional
import math
import threading
import pandas as pd
from database.connection import get_db, get_connection

router = APIRouter()

# Tracks whether a fetch is currently running, so a second click while one is
# in progress just reports back instead of starting a duplicate.
_generating = {"news": False}


def _df(df: pd.DataFrame) -> list:
    df = df.copy()
    for col in df.select_dtypes(include=["datetime", "datetimetz"]).columns:
        df[col] = df[col].astype(str)
    records = df.where(pd.notnull(df), other=None).to_dict(orient="records")
    return [{k: None if isinstance(v, float) and math.isnan(v) else v for k, v in r.items()} for r in records]


@router.get("")
def get_news(
    source_type: Optional[str] = Query(None, description="Security | Institution | Payee"),
    source_id: Optional[int] = Query(None, description="Restrict to one specific source, e.g. one security"),
    unread_only: bool = Query(False),
    limit: int = Query(100),
    offset: int = Query(0),
):
    clauses = []
    params: dict = {"limit": limit, "offset": offset}
    if source_type:
        clauses.append("n.Source_Type = %(source_type)s")
        params["source_type"] = source_type
    if source_id is not None:
        clauses.append("n.Source_Id = %(source_id)s")
        params["source_id"] = source_id
    if unread_only:
        clauses.append("n.Is_Read = FALSE")
    where = ("AND " + " AND ".join(clauses)) if clauses else ""

    with get_db() as conn:
        df = pd.read_sql(f"""
            SELECT
                n.News_Id AS id, n.Source_Type AS source_type, n.Source_Id AS source_id,
                n.Title AS title, n.Url AS url, n.Publisher AS publisher, n.Summary AS summary,
                n.Published_At AS published_at, n.Fetched_At AS fetched_at, n.Is_Read AS is_read,
                COALESCE(sec.Securities_Name, inst.Institutions_Name, pay.Payees_Name) AS source_name,
                sec.Ticker AS ticker
            FROM News_Items n
            LEFT JOIN Securities   sec  ON n.Source_Type = 'Security'    AND sec.Securities_Id  = n.Source_Id
            LEFT JOIN Institutions inst ON n.Source_Type = 'Institution' AND inst.Institutions_Id = n.Source_Id
            LEFT JOIN Payees       pay  ON n.Source_Type = 'Payee'       AND pay.Payees_Id       = n.Source_Id
            WHERE 1=1 {where}
            ORDER BY n.Published_At DESC NULLS LAST, n.Fetched_At DESC
            LIMIT %(limit)s OFFSET %(offset)s
        """, conn, params=params)
    return _df(df)


@router.get("/search")
def search_news(q: str = Query(..., min_length=1), limit: int = Query(15)):
    """Ad-hoc lookup for a security/institution/company by name, independent of what's
    tracked. Not persisted to News_Items — there's no tracked source to attribute it to.

    Prefers yfinance (higher-quality, ticker-based) when *q* matches a known security;
    otherwise falls back to a live DuckDuckGo news search on the raw query, same as the
    Institution/Payee tiers.
    """
    from ai.news_fetch import _normalize_yf_news_item, _normalize_ddg_news_item, _ddg_news_search
    from ai.llm import get_custom_session
    import yfinance as yf

    q_stripped = q.strip()
    with get_db() as conn:
        match = pd.read_sql("""
            SELECT Securities_Id AS id, Securities_Name AS name, Yahoo_Ticker AS yahoo_ticker
            FROM Securities
            WHERE Yahoo_Ticker IS NOT NULL AND Yahoo_Ticker <> ''
              AND (LOWER(Ticker) = LOWER(%(q)s) OR LOWER(Securities_Name) LIKE LOWER(%(qlike)s))
            ORDER BY (LOWER(Ticker) = LOWER(%(q)s)) DESC
            LIMIT 1
        """, conn, params={"q": q_stripped, "qlike": f"%{q_stripped}%"})

    try:
        if not match.empty:
            row = match.iloc[0]
            session = get_custom_session()
            raw_items = yf.Ticker(row["yahoo_ticker"], session=session).news or []
            items = [x for x in (_normalize_yf_news_item(r) for r in raw_items) if x][:limit]
            source_name = row["name"]
            source_type = "Security"
            source_id = int(row["id"])
        else:
            raw_items = _ddg_news_search(q_stripped, limit)
            items = [x for x in (_normalize_ddg_news_item(r) for r in raw_items) if x]
            source_name = q_stripped
            source_type = None
            source_id = None
    except Exception as e:
        raise HTTPException(502, f"Search failed: {e}")

    return [
        {**it, "source_name": source_name, "source_type": source_type, "source_id": source_id}
        for it in items
    ]


@router.post("/generate")
def generate_news():
    """Kick off a news fetch in a background thread; returns immediately."""
    if _generating["news"]:
        return {"status": "generating"}

    def _run():
        from ai.news_fetch import run as run_news_fetch
        _generating["news"] = True
        try:
            run_news_fetch()
        except Exception:
            pass
        finally:
            _generating["news"] = False

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "generating"}


@router.patch("/{news_id}/read")
def mark_news_read(news_id: int, data: dict):
    is_read = bool(data.get("is_read", True))
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE News_Items SET Is_Read = %s WHERE News_Id = %s", (is_read, news_id))
            if cur.rowcount == 0:
                raise HTTPException(404, "News item not found")
        conn.commit()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(500, str(e))
    finally:
        conn.close()
