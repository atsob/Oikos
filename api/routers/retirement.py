"""Retirement planner endpoints (database/retirement.py)."""
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from database import retirement as RP

router = APIRouter()


class ProjectIn(BaseModel):
    params: dict[str, Any]
    sims: int = 1000
    detail: bool = True


class PlanIn(BaseModel):
    id: Optional[int] = None
    name: str
    params: dict[str, Any]
    is_base: Optional[bool] = None


class Scenario(BaseModel):
    name: str
    params: dict[str, Any]


class CompareIn(BaseModel):
    scenarios: list[Scenario]
    sims: int = 800


def _bad(e: ValueError):
    raise HTTPException(400, str(e))


@router.get("/starting-plan")
def starting_plan():
    """A plan pre-filled from your accounts, last 12 months and portfolio, with the tax rules the defaults rest on."""
    return RP.starting_plan()


@router.get("/assets")
def assets(account_ids: Optional[str] = None):
    """What you hold in each pot over the accounts of an Account Preset (comma-separated ids), or over all accounts without one."""
    ids = None if account_ids is None else [int(x) for x in account_ids.split(",") if x.strip().isdigit()]     # an empty preset is no accounts, not all
    return RP.assets_for(ids)


@router.post("/project")
def project(body: ProjectIn):
    try:
        return RP.project(body.params, sims=body.sims, detail=body.detail)
    except ValueError as e:
        _bad(e)


@router.post("/levers")
def levers(body: ProjectIn):
    """What it would take to reach the target chance of success: saving more, retiring later or spending less (each on its own)."""
    try:
        return RP.levers(body.params)
    except ValueError as e:
        _bad(e)


@router.post("/compare")
def compare(body: CompareIn):
    return RP.compare([s.model_dump() for s in body.scenarios], sims=body.sims)


@router.get("/plans")
def list_plans():
    return RP.list_plans()


@router.post("/plans")
def save_plan(body: PlanIn):
    try:
        return RP.save_plan(body.id, body.name, body.params, body.is_base)
    except ValueError as e:
        _bad(e)


@router.delete("/plans/{plan_id}")
def delete_plan(plan_id: int):
    RP.delete_plan(plan_id)
    return {"ok": True}
