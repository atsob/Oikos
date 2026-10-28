"""Shared FastAPI dependencies — session-cookie auth.

Sessions are opaque, DB-backed tokens (not a signed cookie / SessionMiddleware)
so logout and future multi-device revocation are real server-side actions, not
just clearing a client-side value. The cookie carries the raw random token;
only its SHA-256 hash is ever stored (see database/queries.py's Sessions
functions), so a leaked DB backup doesn't hand over live sessions directly.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request

from database.queries import get_session

SESSION_COOKIE_NAME = "oikos_session"
SESSION_TTL_DAYS = 30


def mint_token() -> str:
    """A fresh random session token — goes in the cookie, never stored raw."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def session_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)


async def require_auth(request: Request) -> dict:
    """FastAPI dependency — 401s unless a valid, non-expired session cookie is present, and 403s a restricted user (limited, read-only) unless api/access.py's
    rules open the request for them.

    Attached per-router via app.include_router(..., dependencies=[Depends(require_auth)])
    in api/main.py, not globally — that's what keeps /api/health and /api/auth/*
    reachable without a session.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(401, "Not authenticated")
    session = get_session(hash_token(token))
    if not session:
        raise HTTPException(401, "Session expired or invalid")
    if session.get("role", "admin") != "admin":
        # The accounts linked to a mapped account (a brokerage's cash account) come along.
        from api.access import enforce, linked_accounts
        mapped = session.get("accounts") or []
        session["accounts"] = sorted(set(mapped) | linked_accounts(mapped))
        request.state.user = session
        await enforce(request, session)
    request.state.user = session
    return session
