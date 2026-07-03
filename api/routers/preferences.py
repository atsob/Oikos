"""User Preferences API: persists UI settings/state (decimal format, saved
filters, last-used tabs, etc.) server-side so they survive across devices,
browsers, and origins — unlike localStorage, which is scoped per-origin and
can be evicted by the browser (e.g. Safari's storage eviction)."""
from fastapi import APIRouter

from database.queries import get_user_preferences, set_user_preference

router = APIRouter()


@router.get("")
def list_preferences():
    return get_user_preferences()


@router.put("/{key}")
def upsert_preference(key: str, body: dict):
    set_user_preference(key, body.get("value"))
    return {"ok": True}
