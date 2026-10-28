"""Private funds (venture, private equity, …): commitments, capital calls, distributions and NAVs (database/private_funds.py)."""
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from database import private_funds as PF

router = APIRouter()


class FundIn(BaseModel):
    name: str
    manager: Optional[str] = None
    fund_type: Optional[str] = None
    vintage_year: Optional[int] = None
    currency: Optional[str] = "EUR"
    commitment: float = 0
    accounts_id: Optional[int] = None
    status: Optional[str] = "active"
    notes: Optional[str] = None


class EventIn(BaseModel):
    date: str
    type: str
    amount: float
    note: Optional[str] = None


def _bad(e: ValueError):
    raise HTTPException(400, str(e))


@router.get("")
def list_funds():
    return PF.list_funds()


@router.post("")
def create_fund(body: FundIn):
    try:
        fid = PF.save_fund(None, body.model_dump())
    except ValueError as e:
        _bad(e)
    return PF.get_fund(fid)


@router.get("/{fund_id}")
def get_fund(fund_id: int):
    f = PF.get_fund(fund_id)
    if not f:
        raise HTTPException(404, "No such fund")
    return f


@router.put("/{fund_id}")
def update_fund(fund_id: int, body: FundIn):
    try:
        PF.save_fund(fund_id, body.model_dump())
    except ValueError as e:
        _bad(e)
    return PF.get_fund(fund_id)


@router.delete("/{fund_id}")
def delete_fund(fund_id: int):
    PF.delete_fund(fund_id)
    return {"ok": True}


@router.post("/{fund_id}/events")
def add_event(fund_id: int, body: EventIn):
    if not PF.get_fund(fund_id):
        raise HTTPException(404, "No such fund")
    try:
        PF.save_event(fund_id, None, body.model_dump())
    except ValueError as e:
        _bad(e)
    return PF.get_fund(fund_id)


@router.put("/{fund_id}/events/{event_id}")
def update_event(fund_id: int, event_id: int, body: EventIn):
    try:
        PF.save_event(fund_id, event_id, body.model_dump())
    except ValueError as e:
        _bad(e)
    return PF.get_fund(fund_id)


@router.delete("/{fund_id}/events/{event_id}")
def delete_event(fund_id: int, event_id: int):
    PF.delete_event(event_id)
    return PF.get_fund(fund_id)
