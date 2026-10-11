"""Telegram alerts — a message to the user's phone, only for the moments that call for action (Market Watch / Local Market Watch turning
red, or leaving red).

Configuration is by environment variable, like the other keys (a bot token is a secret, so it is not stored in the database or shown in
the UI):  TELEGRAM_BOT_TOKEN (from @BotFather) and TELEGRAM_CHAT_ID (the chat the bot writes to).  The on/off switch is Tools -> App
Settings -> Telegram alerts (`telegramAlerts`, on by default). Nothing is sent when either variable is empty.
"""
from __future__ import annotations

import logging

import requests

from config.settings import ENV_CONFIG

log = logging.getLogger(__name__)
API = "https://api.telegram.org/bot{token}/sendMessage"


def telegram_configured() -> bool:
    return bool(ENV_CONFIG.get("telegram_bot_token") and ENV_CONFIG.get("telegram_chat_id"))


def alerts_enabled() -> bool:
    """The Telegram alerts switch in App Settings (on unless it was turned off)."""
    try:
        from database.connection import get_connection
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT Pref_Value FROM User_Preferences WHERE Pref_Key = 'app-settings'")
            row = cur.fetchone()
        conn.close()
        if row and row[0] and row[0].get("telegramAlerts") is False:
            return False
    except Exception:
        pass
    return True


def send_telegram(text: str) -> tuple[bool, str]:
    """-> (sent, detail). Plain text (no markup, so nothing needs escaping); a failure is returned, never raised."""
    if not telegram_configured():
        return False, "Telegram is not configured (set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)."
    try:
        r = requests.post(API.format(token=ENV_CONFIG["telegram_bot_token"]),
                          json={"chat_id": ENV_CONFIG["telegram_chat_id"], "text": text[:4000], "disable_web_page_preview": True}, timeout=(5, 15))
        if r.ok:
            return True, "sent"
        try:
            detail = r.json().get("description", r.text)
        except ValueError:
            detail = r.text
        return False, f"Telegram answered {r.status_code}: {str(detail)[:200]}"
    except requests.RequestException as e:
        return False, f"Could not reach Telegram: {str(e)[:150]}"


def notify(text: str) -> bool:
    """Send an alert if Telegram is configured and the alerts are switched on; log (never raise) otherwise."""
    if not telegram_configured() or not alerts_enabled():
        return False
    ok, detail = send_telegram(text)
    if not ok:
        log.warning("Telegram alert not sent: %s", detail)
    return ok
