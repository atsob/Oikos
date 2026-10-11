"""Telegram alerts: is it set up, and a test message (Tools -> App Settings -> Telegram alerts)."""
from fastapi import APIRouter, HTTPException

from database.notify import alerts_enabled, send_telegram, telegram_configured

router = APIRouter()


@router.get("/status")
def status():
    return {"configured": telegram_configured(), "enabled": alerts_enabled()}


@router.post("/test")
def test_message():
    ok, detail = send_telegram("Oikos: Telegram alerts are working. You will get a message here only when Market Watch or Local Market Watch turns red, or leaves red.")
    if not ok:
        raise HTTPException(400, detail)
    return {"sent": True}
