"""Plan: dated to-do steps with a morning Telegram reminder (database/plan.py)."""
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from database import plan as P

router = APIRouter()


class ItemIn(BaseModel):
    plan: Optional[str] = None
    due_date: str
    title: str
    details: Optional[str] = None


class StatusIn(BaseModel):
    status: str


class ImportIn(BaseModel):
    text: str
    plan: Optional[str] = None


def _bad(e: ValueError):
    raise HTTPException(400, str(e))


@router.get("/items")
def items(plan: Optional[str] = Query(None), status: Optional[str] = Query(None)):
    return P.list_items(plan, status)


@router.get("/plans")
def plans():
    return P.plans()


@router.get("/due")
def due(days: int = Query(7, ge=0, le=60)):
    """The open steps: overdue, due today and due in the next `days` days — what the Dashboard block shows."""
    return P.due_summary(ahead_days=days)


@router.post("/items")
def create(body: ItemIn):
    try:
        return P.create_item(body.plan, body.due_date, body.title, body.details)
    except ValueError as e:
        _bad(e)


@router.put("/items/{item_id}")
def update(item_id: int, body: ItemIn):
    try:
        row = P.update_item(item_id, body.plan, body.due_date, body.title, body.details)
    except ValueError as e:
        _bad(e)
    if not row:
        raise HTTPException(404, "No such step")
    return row


@router.post("/items/{item_id}/status")
def status(item_id: int, body: StatusIn):
    try:
        row = P.set_status(item_id, body.status)
    except ValueError as e:
        _bad(e)
    if not row:
        raise HTTPException(404, "No such step")
    return row


@router.delete("/items/{item_id}")
def delete(item_id: int):
    if not P.delete_item(item_id):
        raise HTTPException(404, "No such step")
    return {"ok": True}


@router.delete("/plans")
def delete_plan(name: str = Query(...)):
    return {"deleted": P.delete_plan(name)}


@router.post("/import")
def import_text(body: ImportIn):
    return P.import_text(body.text, body.plan)


@router.post("/remind-now")
def remind_now():
    ok, detail = P.send_reminders(force=True)
    if not ok:
        raise HTTPException(400, detail)
    return {"sent": True, "detail": detail}
