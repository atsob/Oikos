"""Server-side Saxo login: the app keeps the tokens, the browser never has to.

Saxo's access token lasts about 20 minutes and its refresh token is short-lived too, and every refresh hands out a
*new* refresh token — so the token must be refreshed regularly, by one party only, or the connection lapses and the
user has to authorise again. This module is that one party: it reads the credentials/tokens saved in app_settings
(written when the user connects), refreshes under a database lock (the web app and the scheduler run in separate
processes), and stores the new tokens. The importer page asks it for a valid access token (`GET /bank/saxo-session`),
and the scheduler's "Saxo Login Keep-alive" job calls it every few minutes so the login never expires.

Settings used: saxo_app_key, saxo_app_secret, saxo_use_sim, saxo_refresh_token, saxo_access_token,
saxo_token_expiry (epoch seconds of the access token), saxo_status ('ok' | 'expired').
"""
from __future__ import annotations

import logging
import time

from database.connection import get_connection
from database.queries import get_app_setting, save_app_setting

log = logging.getLogger(__name__)

_LOCK_KEY = 727_001        # pg_advisory_lock key shared by the web app and the scheduler


class SaxoNotConnected(RuntimeError):
    """No usable Saxo login: never connected, or the refresh token has expired (the user must connect again)."""


def store_tokens(tok: dict, access_fallback: str = "") -> int:
    """Save a token response; returns the access token's expiry (epoch seconds)."""
    expiry = int(time.time()) + int(tok.get("expires_in", 1200))
    save_app_setting("saxo_access_token", tok.get("access_token", access_fallback))
    if tok.get("refresh_token"):
        save_app_setting("saxo_refresh_token", tok["refresh_token"])
    save_app_setting("saxo_token_expiry", str(expiry))
    save_app_setting("saxo_status", "ok")
    return expiry


def status() -> str:
    """'ok', 'expired' or 'none' (never connected) — without touching Saxo."""
    if not get_app_setting("saxo_refresh_token"):
        return "none"
    return "expired" if get_app_setting("saxo_status") == "expired" else "ok"


def ensure_access_token(force: bool = False, min_ttl: int = 120) -> dict:
    """-> {access_token, expires_at, use_sim}. Refreshes when the stored token has less than `min_ttl` seconds left
    (or always, with force=True — the keep-alive). Raises SaxoNotConnected when there is no usable login."""
    from data.saxo_connector import refresh_access_token

    conn = get_connection()                      # a session-level lock lives as long as this connection
    cur = conn.cursor()
    try:
        cur.execute("SELECT pg_advisory_lock(%s)", (_LOCK_KEY,))
        key, secret = get_app_setting("saxo_app_key"), get_app_setting("saxo_app_secret")
        refresh = get_app_setting("saxo_refresh_token")
        use_sim = get_app_setting("saxo_use_sim") == "1"
        if not (key and secret and refresh):
            raise SaxoNotConnected("Saxo is not connected yet.")
        access = get_app_setting("saxo_access_token") or ""
        exp_s = get_app_setting("saxo_token_expiry") or "0"
        expiry = int(exp_s) if exp_s.isdigit() else 0
        if not force and access and expiry - time.time() > min_ttl:
            return {"access_token": access, "expires_at": expiry, "use_sim": use_sim}
        try:
            tok = refresh_access_token(key, secret, refresh, use_sim=use_sim)
        except RuntimeError as e:
            msg = str(e)
            if "401" in msg or "invalid_grant" in msg:       # the refresh token itself was refused
                save_app_setting("saxo_status", "expired")
                save_app_setting("saxo_access_token", "")
                raise SaxoNotConnected("The Saxo login has expired — connect again.") from e
            raise                                # a network/server hiccup: keep the login, try again next time
        expiry = store_tokens(tok)
        return {"access_token": tok["access_token"], "expires_at": expiry, "use_sim": use_sim}
    finally:
        try:
            cur.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_KEY,))
        except Exception:
            pass
        cur.close()
        conn.close()
