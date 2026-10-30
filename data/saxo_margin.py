"""Saxo's own margin figures for an account — margin utilization, margin available, cash available, account value — read live from Saxo's balances endpoint.

Oikos cannot work these out: the margin Saxo requires depends on each instrument's leverage and on Saxo's risk rules. So the figures are asked for, with the login the
importer already keeps (data/saxo_session.py), and shown as Saxo gives them. Read only: nothing is stored.

GET /port/v1/balances?ClientKey=&AccountKey= answers with a BalanceModel. The fields used (first one present wins, since Saxo's models differ a little between account types):
  cash                  CashBalance
  cash_available        CashAvailableForTrading, else MarginAvailableForTrading
  account_value         TotalValue
  net_equity            NetEquityForMargin
  margin_used           MarginUsedByCurrentPositions                (InitialMargin.MarginUsedByCurrentPositions)
  margin_available      InitialMargin.MarginAvailable, else MarginAvailableForTrading
  margin_utilization    MarginUtilizationPct                        (InitialMargin.MarginUtilizationPct)
"""
from __future__ import annotations

import json
import time

from database.queries import get_app_setting


class SaxoMarginUnavailable(RuntimeError):
    """The account is not a Saxo account Oikos is connected to, or Saxo is not connected."""


def _first(d: dict, *paths):
    """The first value present among dotted paths ('InitialMargin.MarginAvailable'); None when none is."""
    for p in paths:
        cur = d
        for part in p.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
            if cur is None:
                break
        if cur is not None:
            return cur
    return None


def saxo_account_id_for(oikos_account_id: int) -> str | None:
    """The Saxo AccountId an Oikos account is mapped to (Importers → Saxo Bank → account mapping), or None."""
    try:
        mapping = json.loads(get_app_setting("saxo_account_map") or "{}")
    except ValueError:
        return None
    for saxo_id, app_id in mapping.items():
        try:
            if int(app_id) == int(oikos_account_id):
                return str(saxo_id)
        except (TypeError, ValueError):
            continue
    return None


def normalise(b: dict) -> dict:
    """The figures the Investments page shows, from a BalanceModel."""
    util = _first(b, "MarginUtilizationPct", "InitialMargin.MarginUtilizationPct")
    return {
        "currency": b.get("Currency"),
        "cash": _first(b, "CashBalance"),
        "cash_available": _first(b, "CashAvailableForTrading", "MarginAvailableForTrading", "InitialMargin.MarginAvailable"),
        "account_value": _first(b, "TotalValue"),
        "net_equity": _first(b, "NetEquityForMargin", "InitialMargin.NetEquityForMargin"),
        "margin_used": _first(b, "MarginUsedByCurrentPositions", "InitialMargin.MarginUsedByCurrentPositions"),
        "margin_available": _first(b, "InitialMargin.MarginAvailable", "MarginAvailableForTrading"),
        "margin_utilization_pct": util,
        "unrealised_margin_pl": _first(b, "UnrealizedMarginProfitLoss"),
        "open_positions": _first(b, "OpenPositionsCount"),
    }


def fetch_margin(oikos_account_id: int) -> dict:
    """Saxo's margin figures for the Oikos account, live. Raises SaxoMarginUnavailable when the account is not a connected Saxo account."""
    from data.saxo_connector import _api_base, _api_get, fetch_accounts, fetch_client_key
    from data.saxo_session import SaxoNotConnected, ensure_access_token

    saxo_id = saxo_account_id_for(oikos_account_id)
    if not saxo_id:
        raise SaxoMarginUnavailable("This account is not mapped to a Saxo account (Importers → Saxo Bank).")
    try:
        sess = ensure_access_token()
    except SaxoNotConnected as e:
        raise SaxoMarginUnavailable(str(e)) from e
    token, use_sim = sess["access_token"], bool(sess.get("use_sim"))
    account = next((a for a in fetch_accounts(token, use_sim=use_sim) if str(a["AccountId"]) == saxo_id), None)
    if not account:
        raise SaxoMarginUnavailable(f"Saxo does not list the account {saxo_id} for this login.")
    client_key = fetch_client_key(token, use_sim=use_sim)
    raw = _api_get(token, f"{_api_base(use_sim)}/port/v1/balances",
                   {"ClientKey": client_key, "AccountKey": account["AccountKey"]})
    return {"saxo_account": saxo_id, "fetched_at": int(time.time()), **normalise(raw), "raw": raw}
