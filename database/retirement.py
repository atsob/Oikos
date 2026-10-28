"""Retirement planner: projects your money to the end of your life and asks whether it lasts.

One plan = a set of inputs (`DEFAULTS`): who you are (birth year, retirement age, planning horizon), what you hold in three pots (cash,
investments, pension pots), what you add to them until you retire, what you spend afterwards, the income that comes in (state and occupational
pensions, rent, one-off sums), expected returns and inflation, and the tax rules. The tax profile follows the Country of Usage setting (Tools -> App Settings); every number is editable.

How a year works (annual steps, from this year to the planning age; everything is carried in nominal euros and shown in today's euros too):
  until retirement  the pots grow, then your contributions are added at the year end (they grow by their own yearly increase);
  from retirement   the year's spending (today's euros × inflation) less the pension / other income that arrives (after the tax on it)
                    is withdrawn at the start of the year — cash first, then investments, then pension pots, in the order you set — grossed
                    up for the tax the withdrawal itself triggers; what is left grows. If the pots cannot cover a year, that is a shortfall.
Tax: pension-type income is taxed on the progressive scale after the health contribution (with the employee / pensioner credit, and the
brackets indexed with inflation by default — the real-terms reading of a scale that is adjusted from time to time); dividends and interest
carry a yearly drag; sales of investments are taxed on the gain share of what is withdrawn (0% for listed securities by default, 15% if you
set it); pension-pot withdrawals are taxed at a flat rate or on the scale, and can be paid out as a lump sum at retirement.

The same code runs the straight-line projection (one path, the expected return each year) and the Monte Carlo (many paths with random yearly
returns around it — the chance the money lasts, and the spread of outcomes). Inflation is a fixed assumption in both.
"""
from __future__ import annotations

import json
import logging
from copy import deepcopy
from datetime import date

import numpy as np

from database.connection import get_connection

log = logging.getLogger(__name__)

SOURCES = ("cash", "investments", "pot")
MAX_SIMS = 5000

# Tax and pension rules by country. The planner follows the Country of Usage setting (Tools -> App Settings -> Country of Usage, the same one Local
# Market Watch uses): a country with an entry in PROFILES gets its rules as the starting values; any other country starts from NEUTRAL_TAX — no
# tax at all — with a warning on screen until the rates are entered, rather than borrowing another country's. Income tax is not harmonised in the EU,
# so a country is its own entry. Every figure stays editable in the plan, and a saved plan keeps the rules it was saved with.
#
# To add a country: add an entry to PROFILES (name, the statutory pension age, `tax`, `hints` for the screen and `rules`, the list of what the
# defaults assume) — nothing else changes. The values are starting points to check against current law, not advice.
NEUTRAL_TAX = {
    "country": "",
    "brackets": [[None, 0.0]],     # upper bound of each band (None = no limit) and its rate in %, on employment / pension income
    "credit": 0.0,                 # a yearly tax credit …
    "credit_taper_from": 0.0,      # … reduced by this much per 1,000 of income above this
    "credit_taper_per_1000": 0.0,
    "health_pct": 0.0,             # health contribution withheld from a pension
    "index_brackets": True,        # keep the scale, the credit and the threshold constant in real terms (a scale is adjusted from time to time)
    "div_pct": 0.0,                # tax on dividends and fund distributions
    "interest_pct": 0.0,           # tax on interest
    "gains_pct": 0.0,              # tax on realised gains from securities
    "pot_mode": "flat",            # tax on pension-pot withdrawals: "flat" (pot_flat_pct) or "income" (progressive scale)
    "pot_flat_pct": 0.0,
    "pot_payout": "drawdown",      # "drawdown": drawn like the other pots; "lump": the whole pot paid out at retirement into investments
}

GREEK_TAX = {**NEUTRAL_TAX,
    "country": "GR",
    # The 2026 scale (Law 5246/2025) for employment and pension income.
    "brackets": [[10000, 9.0], [20000, 20.0], [30000, 26.0], [40000, 34.0], [60000, 39.0], [None, 44.0]],
    "credit": 777.0,               # employee / pensioner tax credit (no children) …
    "credit_taper_from": 12000.0,  # … reduced by €20 per 1,000 of income above €12,000
    "credit_taper_per_1000": 20.0,
    "health_pct": 6.0,             # health contribution withheld from a pension (main and supplementary)
    "div_pct": 15.0,               # 5% on Greek-listed dividends, 15% on foreign ones: a blend is a fair default for a mostly foreign portfolio
    "interest_pct": 15.0,          # final
    "gains_pct": 0.0,              # exempt for an individual holding under 0.5% of a listed company (some recent sources say 15%)
    "pot_mode": "flat",
    "pot_flat_pct": 15.0,          # an assumption: it depends on the fund and on when you contributed
}

GREEK_RULES = [
    ("Income tax scale", "9% to €10k, 20% to €20k, 26% to €30k, 34% to €40k, 39% to €60k, 44% above — the 2026 scale for employment and pension income.",
     "Check the bands against the current law; sources disagree on the middle ones."),
    ("Tax credit", "€777 for an employee or pensioner without children, reduced by €20 for each €1,000 of income above €12,000.", "Verify with your tax return."),
    ("Health contribution", "6% of a pension is withheld, and is not taxed.", "Main and supplementary pensions."),
    ("Dividends and interest", "15% (5% on Greek-listed dividends; a blend is a fair default for a mostly foreign portfolio).", "Foreign withholding is credited."),
    ("Gains on listed securities and ETFs", "0%: exempt for an individual holding under 0.5% of a company.",
     "Some recent sources say 15% applies from 2022 — switch it if your accountant says so."),
    ("Pension pots (occupational funds, private pension plans)", "15% flat on what is withdrawn.", "An assumption — it depends on the fund and on when you contributed."),
    ("State pension", "Enter the EFKA estimate (gov.gr / efka.gov.gr pension estimate) as an income stream from age 67.", "Indexed with inflation by default."),
    ("Other EU countries", "Income tax and pensions are national, not EU-wide. Pension rights earned in several EU countries add up (totalisation) but are paid — and taxed — separately.",
     "Enter each country's pension as its own stream; use 'taxed at source' if it is withheld abroad and not taxed again."),
]

PROFILES: dict[str, dict] = {
    "GR": {
        "name": "Greece", "retire_age": 67, "tax": GREEK_TAX, "rules": GREEK_RULES,
        "hints": {
            "retire_age": "The statutory pension age in Greece is 67 (62 with 40 years of contributions). Early retirement means fewer years of saving and more years to fund.",
            "state_pension": "State pension (EFKA)",
            "state_pension_help": "Your state pension estimate is on the EFKA / gov.gr pension simulator.",
            "credit": "Employee / pensioner credit: €777 without children.",
            "health": "Withheld from a pension in Greece: 6%.",
            "div": "5% on Greek-listed dividends, 15% on foreign ones and fund distributions.",
            "gains": "0 for listed shares and ETFs of an individual under 0.5% of a company. Some recent sources say 15% — set it if your accountant confirms.",
        },
    },
}

# The statutory pension age, about, for the countries the app knows — the default retirement age where there is no profile. Check yours.
STATUTORY_AGE = {"GR": 67, "FR": 64, "DE": 67, "IT": 67, "ES": 67, "NL": 67, "PT": 67, "GB": 68, "US": 67}

GENERIC_HINTS = {
    "retire_age": "The statutory pension age of your country (about {age}); check it. Early retirement means fewer years of saving and more years to fund.",
    "state_pension": "State pension",
    "state_pension_help": "Your pension authority's estimate of your state pension goes here.",
    "credit": "A yearly tax credit for employees / pensioners, if your country has one.",
    "health": "A health or social contribution withheld from a pension, if any.",
    "div": "Tax on dividends and fund distributions.",
    "gains": "Tax on realised gains from securities (0 if they are exempt for you).",
}

GENERIC_RULES = [
    ("No tax profile for this country yet", "Taxes start at zero: enter your country's income-tax scale, the tax on dividends, interest and gains, and on pension withdrawals under “Tax rules”.",
     "A country is one entry in database/retirement.py; its rates are not guessed from another country's."),
    ("Other EU countries", "Income tax and pensions are national, not EU-wide. Pension rights earned in several EU countries add up (totalisation) but are paid — and taxed — separately.",
     "Enter each country's pension as its own stream; use 'taxed at source' if it is withheld abroad and not taxed again."),
]


def country_setup(cc: str | None = None) -> dict:
    """The tax profile and screen texts for a country (default: the Country of Usage setting)."""
    from database.local_markets import PROFILES as MARKETS, home_country
    cc = (cc if cc is not None else home_country()).strip().upper()
    prof = PROFILES.get(cc)
    name = (prof or {}).get("name") or (MARKETS.get(cc) or {}).get("name") or ""
    age = (prof or {}).get("retire_age") or STATUTORY_AGE.get(cc, 67)
    tax = deepcopy((prof or {}).get("tax") or NEUTRAL_TAX)
    tax["country"] = cc
    hints = (prof or {}).get("hints") or {k: v.format(age=age) for k, v in GENERIC_HINTS.items()}
    return {"code": cc, "name": name, "has_tax_profile": prof is not None, "retire_age": age, "tax": tax, "hints": hints,
            "rules": [{"label": a, "value": b, "note": c} for a, b, c in ((prof or {}).get("rules") or GENERIC_RULES)]}


DEFAULTS = {
    "birth_year": date.today().year - 45,
    "retire_age": 67,              # replaced by the country's statutory age in a starting plan
    "end_age": 90,                 # plan until this age; life expectancy at 65 is about 20 years, so 90 covers most outcomes
    "assets": {"cash": 0.0, "investments": 0.0, "pot": 0.0, "investments_cost": None},     # today's euros; cost basis of the investments
    "contrib": {"cash": 0.0, "investments": 0.0, "pot": 0.0, "growth_pct": 2.5},           # per year until retirement, today's euros; yearly increase
    "spend": {"annual": 24000.0, "real_change_pct": 0.0},                                    # per year in retirement, today's euros; drift vs inflation
    "streams": [],       # {name, annual, start_age, end_age|None, indexation: "inflation"|"fixed"|<% a year>, tax: "income"|"source"|"exempt", source_rate}
    "lumps": [],         # {name, age, amount, tax_pct}: a one-off sum (an inheritance, a property sale) into investments, today's euros
    "returns": {"cash_pct": 2.0, "pre_pct": 6.0, "post_pct": 4.5, "vol_pre_pct": 14.0, "vol_post_pct": 10.0, "yield_pct": 2.0},
    "inflation_pct": 2.5,
    "tax": NEUTRAL_TAX,
    "order": list(SOURCES),
    "target_success_pct": 80.0,
}


# ── parameters ───────────────────────────────────────────────────────────────────────────────────

def _merge(base: dict, over: dict) -> dict:
    out = deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = deepcopy(v)
    return out


def _num(v, default: float = 0.0) -> float:
    try:
        x = float(v)
        return x if np.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def normalise(params: dict | None) -> dict:
    """Defaults filled in, numbers coerced and bounds enforced. Raises ValueError for a plan that cannot be run."""
    p = _merge(DEFAULTS, params or {})
    p["birth_year"] = int(_num(p["birth_year"], DEFAULTS["birth_year"]))
    p["retire_age"] = int(_num(p["retire_age"], 67))
    p["end_age"] = int(_num(p["end_age"], 90))
    cur = date.today().year - p["birth_year"]
    if not 15 <= cur <= 100:
        raise ValueError("Check the birth year: the plan needs an age between 15 and 100.")
    if p["end_age"] > 120 or p["end_age"] <= cur:
        raise ValueError("The planning age must be above your current age and at most 120.")
    p["retire_age"] = max(min(p["retire_age"], p["end_age"] - 1), 1)
    for grp in ("assets", "contrib", "spend", "returns"):
        for k in list(p[grp]):
            if k == "investments_cost":
                p[grp][k] = None if p[grp][k] in (None, "") else max(_num(p[grp][k]), 0.0)
            else:
                p[grp][k] = _num(p[grp][k])
    for k in ("cash", "investments", "pot"):
        p["assets"][k] = max(p["assets"][k], 0.0)
        p["contrib"][k] = max(p["contrib"][k], 0.0)
    p["spend"]["annual"] = max(p["spend"]["annual"], 0.0)
    p["inflation_pct"] = min(max(_num(p["inflation_pct"], 2.5), -2.0), 20.0)
    p["target_success_pct"] = min(max(_num(p["target_success_pct"], 80.0), 1.0), 99.0)
    t = p["tax"]
    for k in ("credit", "credit_taper_from", "credit_taper_per_1000", "health_pct", "div_pct", "interest_pct", "gains_pct", "pot_flat_pct"):
        t[k] = min(max(_num(t[k]), 0.0), 100.0 if k.endswith("_pct") else 1e9)
    t["index_brackets"] = bool(t.get("index_brackets", True))
    t["pot_mode"] = "income" if t.get("pot_mode") == "income" else "flat"
    t["pot_payout"] = "lump" if t.get("pot_payout") == "lump" else "drawdown"
    br = []
    for ub, rate in t.get("brackets") or []:
        br.append([None if ub in (None, "") else _num(ub), min(max(_num(rate), 0.0), 100.0)])
    br.sort(key=lambda b: float("inf") if b[0] is None else b[0])
    if not br or br[-1][0] is not None:
        br.append([None, br[-1][1] if br else 0.0])
    t["brackets"] = br
    order = [s for s in (p.get("order") or []) if s in SOURCES]
    p["order"] = order + [s for s in SOURCES if s not in order]
    streams = []
    for s in p.get("streams") or []:
        end = s.get("end_age")
        idx = s.get("indexation", "inflation")
        streams.append({
            "name": str(s.get("name") or "Income"), "annual": max(_num(s.get("annual")), 0.0), "start_age": int(_num(s.get("start_age"), p["retire_age"])),
            "end_age": None if end in (None, "") else int(_num(end)),
            "indexation": idx if idx in ("inflation", "fixed") else _num(idx, 0.0),
            "tax": s.get("tax") if s.get("tax") in ("income", "source", "exempt") else "income",
            "source_rate": min(max(_num(s.get("source_rate")), 0.0), 100.0),
        })
    p["streams"] = streams
    p["lumps"] = [{"name": str(l.get("name") or "One-off"), "age": int(_num(l.get("age"), p["retire_age"])), "amount": _num(l.get("amount")),
                   "tax_pct": min(max(_num(l.get("tax_pct")), 0.0), 100.0)} for l in (p.get("lumps") or [])]
    return p


def current_age(p: dict) -> int:
    return date.today().year - p["birth_year"]


# ── tax ──────────────────────────────────────────────────────────────────────────────────────────

def _tax_scale(income, p: dict, infl) -> np.ndarray:
    """Income tax on the progressive scale less the credit. `income` and the result are nominal; with indexed brackets the scale is applied
    to the income in today's euros (income / inflation) and scaled back."""
    t = p["tax"]
    scale = infl if t["index_brackets"] else 1.0
    x = np.maximum(np.asarray(income, dtype=float), 0.0) / scale
    tax = np.zeros_like(x)
    lo = 0.0
    for ub, rate in t["brackets"]:
        hi = np.inf if ub is None else float(ub)
        tax = tax + rate / 100.0 * np.clip(x - lo, 0.0, hi - lo)
        lo = hi
    credit = np.maximum(t["credit"] - t["credit_taper_per_1000"] * np.maximum(x - t["credit_taper_from"], 0.0) / 1000.0, 0.0)
    return np.where(x > 0, np.maximum(tax - credit, 0.0), 0.0) * scale


def _stream_amount(s: dict, t: int, age: int, infl: float) -> float:
    if age < s["start_age"] or (s["end_age"] is not None and age > s["end_age"]):
        return 0.0
    idx = infl if s["indexation"] == "inflation" else 1.0 if s["indexation"] == "fixed" else (1 + s["indexation"] / 100.0) ** t
    return s["annual"] * idx


# ── the simulation ───────────────────────────────────────────────────────────────────────────────

def _returns(p: dict, n: int, sims: int, seed: int = 42) -> np.ndarray:
    """Yearly gross returns of the risky pots, shape (sims, n): the expected return every year for sims == 1, else lognormal around it."""
    cur = current_age(p)
    pre = (cur + np.arange(n)) < p["retire_age"]
    r = np.where(pre, p["returns"]["pre_pct"], p["returns"]["post_pct"]) / 100.0
    if sims <= 1:
        return r[None, :]
    sig = np.where(pre, p["returns"]["vol_pre_pct"], p["returns"]["vol_post_pct"]) / 100.0
    z = np.random.default_rng(seed).standard_normal((sims, n))
    return np.exp(np.log1p(r) - sig ** 2 / 2.0 + sig * z) - 1.0


def _simulate(p: dict, R: np.ndarray, adjust_at_retire: float = 0.0, detail: bool = False, scale_at_retire: float = 1.0) -> dict:
    S, N = R.shape
    cur = current_age(p)
    ret = p["retire_age"]
    inf = p["inflation_pct"] / 100.0
    A, K, T, rt = p["assets"], p["contrib"], p["tax"], p["returns"]
    C = np.full(S, A["cash"])
    V = np.full(S, A["investments"])
    B = np.full(S, min(A["investments_cost"], A["investments"]) if A["investments_cost"] is not None else A["investments"])
    P = np.full(S, A["pot"])
    cash_net = rt["cash_pct"] / 100.0 * (1 - T["interest_pct"] / 100.0)
    drag = rt["yield_pct"] / 100.0 * T["div_pct"] / 100.0
    gains = T["gains_pct"] / 100.0
    t0 = max(ret - cur, 0)
    total = np.zeros((N + 1, S))
    short = np.zeros((N, S))
    rows: list[dict] = []
    infl_series = (1 + inf) ** np.arange(N + 1)

    for t in range(N):
        age, infl = cur + t, infl_series[t]
        total[t] = C + V + P
        row = {"age": age, "year": date.today().year + t, "infl": float(infl), "phase": "retired" if age >= ret else "saving",
               "cash": float(C[0]), "investments": float(V[0]), "pot": float(P[0]), "contrib": 0.0, "spend": 0.0, "income": 0.0, "health": 0.0,
               "income_tax": 0.0, "wd_cash": 0.0, "wd_investments": 0.0, "wd_pot": 0.0, "tax_gains": 0.0, "tax_pot": 0.0, "tax_drag": 0.0,
               "surplus": 0.0, "shortfall": 0.0} if detail else None

        for l in p["lumps"]:                                       # one-off sums
            if l["age"] == age:
                net = l["amount"] * infl * (1 - l["tax_pct"] / 100.0)
                V = V + net
                B = B + net
        if t == t0:                                                # the first retired year: a pot paid out as a lump sum, and the solver's adjustment
            if scale_at_retire != 1.0:
                C, V, B, P = C * scale_at_retire, V * scale_at_retire, B * scale_at_retire, P * scale_at_retire
            if T["pot_payout"] == "lump":
                lump_tax = _tax_scale(P, p, infl) if T["pot_mode"] == "income" else P * T["pot_flat_pct"] / 100.0
                V, B = V + P - lump_tax, B + P - lump_tax
                if detail:
                    row["tax_pot"] += float(lump_tax[0]); row["wd_pot"] += float(P[0])
                P = np.zeros(S)
            if adjust_at_retire:
                V, B = V + adjust_at_retire * infl, B + adjust_at_retire * infl

        if age >= ret:
            spend = p["spend"]["annual"] * infl * (1 + p["spend"]["real_change_pct"] / 100.0) ** (age - ret)
            gi = src_net = exempt = 0.0
            for s in p["streams"]:
                a = _stream_amount(s, t, age, infl)
                if s["tax"] == "income":
                    gi += a
                elif s["tax"] == "source":
                    src_net += a * (1 - s["source_rate"] / 100.0)
                else:
                    exempt += a
            health = gi * T["health_pct"] / 100.0
            ti = gi - health
            tax_i = float(_tax_scale(ti, p, infl))
            net_stream = gi - health - tax_i + src_net + exempt
            rem = np.full(S, spend - net_stream)
            surplus = np.maximum(-rem, 0.0)
            C = C + surplus
            rem = np.maximum(rem, 0.0)
            w_cash = w_inv = w_pot = tg = tp = np.zeros(S)
            for src in p["order"]:
                if src == "cash":
                    w_cash = np.minimum(C, rem); C = C - w_cash; rem = rem - w_cash
                elif src == "investments":
                    gs = np.where(V > 0, np.clip(1 - B / np.maximum(V, 1e-9), 0, 1), 0.0)
                    f = 1 - gs * gains
                    net = np.minimum(rem, V * f)
                    w_inv = np.where(f > 0, net / np.maximum(f, 1e-9), 0.0)
                    tg = w_inv - net
                    B = B * (1 - np.where(V > 0, w_inv / np.maximum(V, 1e-9), 0.0))
                    V = V - w_inv; rem = rem - net
                else:
                    if T["pot_mode"] == "flat":
                        f = 1 - T["pot_flat_pct"] / 100.0
                        net = np.minimum(rem, P * f)
                        w_pot = net / max(f, 1e-9)
                    else:
                        inc = lambda w: _tax_scale(ti + w, p, infl) - tax_i
                        w = rem.copy()
                        for _ in range(12):
                            w = np.minimum(rem + inc(w), P)
                        w_pot = w
                        net = np.minimum(rem, w - inc(w))
                    tp = w_pot - net; P = P - w_pot; rem = rem - net
            short[t] = rem
            if detail:
                row.update(spend=float(spend), income=float(gi + src_net + exempt), health=float(health), income_tax=tax_i,
                           wd_cash=float(w_cash[0]), wd_investments=float(w_inv[0]), wd_pot=row["wd_pot"] + float(w_pot[0]),
                           tax_gains=float(tg[0]), tax_pot=row["tax_pot"] + float(tp[0]), surplus=float(surplus[0]), shortfall=float(rem[0]))

        g = np.maximum(1 + R[:, t] - drag, 0.0)
        if detail:
            row["tax_drag"] = float(V[0] * drag + C[0] * rt["cash_pct"] / 100.0 * T["interest_pct"] / 100.0)
        C, V, P = C * (1 + cash_net), V * g, P * np.maximum(1 + R[:, t], 0.0)
        if age < ret:
            c_cash, c_inv, c_pot = (K[k] * (1 + K["growth_pct"] / 100.0) ** t for k in ("cash", "investments", "pot"))
            C, V, B, P = C + c_cash, V + c_inv, B + c_inv, P + c_pot
            if detail:
                row["contrib"] = float(c_cash + c_inv + c_pot)
        if detail:
            rows.append(row)
    total[N] = C + V + P
    if detail:                                                     # the balances shown are the start of each year; the last row is the end of the plan
        rows.append({"age": cur + N, "year": date.today().year + N, "infl": float(infl_series[N]), "phase": "end", "cash": float(C[0]),
                     "investments": float(V[0]), "pot": float(P[0])})
    return {"total": total, "infl": infl_series, "short": short, "rows": rows, "t0": t0, "cur": cur}


def _first_failure(short: np.ndarray, tol: float = 1.0) -> np.ndarray:
    """Per path, the index of the first year with a shortfall, or -1."""
    bad = short > tol
    return np.where(bad.any(axis=0), bad.argmax(axis=0), -1)


def _needed_at_retirement(p: dict) -> float | None:
    """The capital needed on the day of retirement (today's euros) for the straight-line projection to last to the end: if the plan already
    lasts, how far every pot could be scaled down; if not, how much must be added to the investments. None when no sum is enough."""
    N = p["end_age"] - current_age(p)
    R = _returns(p, N, 1)
    base = _simulate(p, R)
    t0 = base["t0"]
    at_ret = float(base["total"][t0, 0] / base["infl"][t0])

    def ok(**kw) -> bool:
        return not (_simulate(p, R, **kw)["short"] > 1.0).any()

    if ok():
        if ok(scale_at_retire=0.0):
            return 0.0
        lo, hi = 0.0, 1.0
        for _ in range(30):
            mid = (lo + hi) / 2
            lo, hi = (lo, mid) if ok(scale_at_retire=mid) else (mid, hi)
        return at_ret * hi
    top = max(1e6, 80 * (p["spend"]["annual"] + 1))
    if not ok(adjust_at_retire=top):
        return None
    lo, hi = 0.0, top
    for _ in range(40):
        mid = (lo + hi) / 2
        lo, hi = (lo, mid) if ok(adjust_at_retire=mid) else (mid, hi)
    return at_ret + hi


def project(params: dict | None, sims: int = 1000, detail: bool = True) -> dict:
    """The straight-line projection, and — for sims > 1 — the Monte Carlo around it. Everything is returned in nominal euros with the price
    index of each year (`infl`), so the screen can show today's euros by dividing."""
    p = normalise(params)
    sims = int(min(max(sims, 0), MAX_SIMS))
    cur, N, ret = current_age(p), p["end_age"] - current_age(p), p["retire_age"]
    base = _simulate(p, _returns(p, N, 1), detail=detail)
    t0 = base["t0"]
    infl = base["infl"]
    fail = _first_failure(base["short"])[0]
    at_ret = float(base["total"][t0, 0] / infl[t0])
    needed = _needed_at_retirement(p)
    out = {
        "params": p, "ages": [cur + i for i in range(N + 1)], "retire_index": t0,
        "balance_at_retirement": at_ret, "needed_at_retirement": needed,
        "gap_at_retirement": None if needed is None else at_ret - needed,
        "depletion_age": None if fail < 0 else cur + int(fail),
        "survives": bool(fail < 0),
        "end_balance_real": float(base["total"][N, 0] / infl[N]),
        "rows": base["rows"] if detail else [],
        "real_total": (base["total"][:, 0] / infl).tolist(),
        "mc": None,
    }
    rows = base["rows"]
    if detail and rows:
        first = next((r for r in rows if r["phase"] == "retired"), None)
        if first:
            wd = first["wd_cash"] + first["wd_investments"] + first["wd_pot"]
            out["first_year"] = {"age": first["age"], "spend_real": first["spend"] / first["infl"], "income_real": (first["income"] - first["health"] - first["income_tax"]) / first["infl"],
                                 "withdrawal_real": wd / first["infl"], "income_tax_real": first["income_tax"] / first["infl"],
                                 "withdrawal_rate_pct": wd / max(at_ret * first["infl"], 1.0) * 100.0}
    if sims > 1:
        sim = _simulate(p, _returns(p, N, sims))
        f = _first_failure(sim["short"])
        real = sim["total"] / infl[:, None]
        alive = np.array([(~((f >= 0) & (f <= i))).mean() * 100.0 for i in range(N)] + [(f < 0).mean() * 100.0])
        out["mc"] = {
            "sims": sims, "success_pct": float((f < 0).mean() * 100.0),
            "p10": np.percentile(real, 10, axis=1).tolist(), "p50": np.percentile(real, 50, axis=1).tolist(), "p90": np.percentile(real, 90, axis=1).tolist(),
            "solvent_by_age": alive.tolist(), "end_p10": float(np.percentile(real[N], 10)), "end_p50": float(np.percentile(real[N], 50)),
            "median_at_retirement": float(np.percentile(real[t0], 50)),
        }
    out["insights"] = _insights(out)
    return out


def _eur(v: float) -> str:
    return f"€{v:,.0f}"


def _insights(o: dict) -> list[str]:
    p, say = o["params"], []
    cur, ret, end = current_age(p), p["retire_age"], p["end_age"]
    lead = f"Retiring at {ret} you would have" if ret > cur else "You are retired and have"
    say.append(f"{lead} about {_eur(o['balance_at_retirement'])} (today's euros)"
               + (f"; the straight-line projection needs {_eur(o['needed_at_retirement'])} to last to {end}." if o["needed_at_retirement"] is not None else
                  f"; no amount of savings is enough for the spending set, so lower it or add income."))
    fy = o.get("first_year")
    if fy:
        covered = min(fy["income_real"] / fy["spend_real"] * 100.0, 100.0) if fy["spend_real"] else 100.0
        say.append(f"In the first year of retirement you spend {_eur(fy['spend_real'])}; pensions and other income cover {covered:.0f}% of it after tax, "
                   f"so {_eur(fy['withdrawal_real'])} is drawn from your pots — {fy['withdrawal_rate_pct']:.1f}% of what you have then"
                   + (" (above the 4% rule of thumb: the money is at risk in a long retirement)." if fy["withdrawal_rate_pct"] > 4.5 else "."))
    if o["survives"]:
        say.append(f"On the straight-line projection the money lasts to {end}, leaving {_eur(o['end_balance_real'])} (today's euros).")
    else:
        say.append(f"On the straight-line projection the money runs out at age {o['depletion_age']}.")
    m = o["mc"]
    if m:
        verdict = "on track" if m["success_pct"] >= p["target_success_pct"] else "below your target"
        say.append(f"Across {m['sims']:,} simulated market paths the money lasts to {end} in {m['success_pct']:.0f}% of them — {verdict} "
                   f"(target {p['target_success_pct']:.0f}%). A poor run of returns early in retirement does the most damage.")
    return say


# ── what would fix it ────────────────────────────────────────────────────────────────────────────

def _success(p: dict, sims: int = 800, adjust: float = 0.0) -> float:
    N = p["end_age"] - current_age(p)
    return float((_first_failure(_simulate(p, _returns(p, N, sims), adjust_at_retire=adjust)["short"]) < 0).mean() * 100.0)


def levers(params: dict | None, sims: int = 800) -> dict:
    """What it would take to reach the target chance: more saving, a later retirement, or less spending — each on its own."""
    p = normalise(params)
    target = p["target_success_pct"]
    now = _success(p, sims)
    out = {"success_pct": now, "target_pct": target, "reached": now >= target, "save_more": None, "retire_later": None, "spend_less": None,
           "capital_needed": None, "capital_projected": None}
    N = p["end_age"] - current_age(p)
    base = _simulate(p, _returns(p, N, 1))
    out["capital_projected"] = float(base["total"][base["t0"], 0] / base["infl"][base["t0"]])
    if now >= target:
        return out
    cur = current_age(p)
    top = max(1e6, 80 * (p["spend"]["annual"] + 1))                # the capital at retirement that gives the target chance, not just a plan that lasts on average returns
    if _success(p, sims, adjust=top) >= target:
        lo, hi = 0.0, top
        for _ in range(18):
            mid = (lo + hi) / 2
            lo, hi = (lo, mid) if _success(p, sims, adjust=mid) >= target else (mid, hi)
        out["capital_needed"] = out["capital_projected"] + hi

    def with_(mod) -> dict:
        q = deepcopy(p); mod(q); return q

    if p["retire_age"] > cur:                                     # saving more only helps before retirement
        def extra(x):
            return with_(lambda q: q["contrib"].__setitem__("investments", q["contrib"]["investments"] + x))
        lo, hi = 0.0, 200000.0
        if _success(extra(hi), sims) >= target:
            for _ in range(18):
                mid = (lo + hi) / 2
                lo, hi = (lo, mid) if _success(extra(mid), sims) >= target else (mid, hi)
            out["save_more"] = {"extra_per_year": hi, "extra_per_month": hi / 12.0, "success_pct": _success(extra(hi), sims)}
    for k in range(1, 16):
        if p["retire_age"] + k >= p["end_age"]:
            break
        q = with_(lambda q: q.__setitem__("retire_age", q["retire_age"] + k))
        s = _success(q, sims)
        if s >= target:
            out["retire_later"] = {"years": k, "age": q["retire_age"], "success_pct": s}
            break
    def cut(x):
        return with_(lambda q: q["spend"].__setitem__("annual", q["spend"]["annual"] * (1 - x)))
    if _success(cut(0.6), sims) >= target:
        lo, hi = 0.0, 0.6
        for _ in range(14):
            mid = (lo + hi) / 2
            lo, hi = (lo, mid) if _success(cut(mid), sims) >= target else (mid, hi)
        out["spend_less"] = {"cut_pct": hi * 100.0, "annual": p["spend"]["annual"] * (1 - hi), "success_pct": _success(cut(hi), sims)}
    return out


def compare(scenarios: list[dict], sims: int = 800) -> list[dict]:
    """Side-by-side headline numbers for several plans: [{name, params}] -> [{name, ...}]."""
    rows = []
    for sc in scenarios:
        try:
            o = project(sc.get("params"), sims=sims, detail=True)
        except ValueError as e:
            rows.append({"name": sc.get("name") or "Plan", "error": str(e)}); continue
        p, fy = o["params"], o.get("first_year") or {}
        rows.append({
            "name": sc.get("name") or "Plan", "retire_age": p["retire_age"], "end_age": p["end_age"], "balance_at_retirement": o["balance_at_retirement"],
            "needed_at_retirement": o["needed_at_retirement"], "gap_at_retirement": o["gap_at_retirement"], "survives": o["survives"],
            "depletion_age": o["depletion_age"], "end_balance_real": o["end_balance_real"], "success_pct": (o["mc"] or {}).get("success_pct"),
            "target_pct": p["target_success_pct"], "withdrawal_rate_pct": fy.get("withdrawal_rate_pct"), "spend_real": fy.get("spend_real"),
        })
    return rows


# ── your data as the starting point ───────────────────────────────────────────────────────────────

def assets_for(account_ids: list[int] | None = None) -> dict:
    """What you hold in each pot — cash and savings, investments (with their cost basis), pension accounts — over all active accounts, or only
    the ones in `account_ids` (an Account Preset). Real estate, vehicles, cards and loans are not counted."""
    out: dict = {"assets": {}, "notes": [], "accounts": 0}
    try:
        from api.routers.dashboard import get_accounts
        ids = None if account_ids is None else {int(i) for i in account_ids}
        accts = [a for a in get_accounts(False) if a.get("is_active") is not False and (ids is None or int(a["id"]) in ids)]
        bal = lambda types: float(sum(float(a.get("balance_eur") or 0) for a in accts if a.get("type") in types))
        out["accounts"] = len(accts)
        out["assets"] = {"cash": max(bal(("Cash", "Checking", "Savings")), 0.0), "investments": max(bal(("Brokerage", "Margin", "Other Investment")), 0.0),
                         "pot": max(bal(("Pension",)), 0.0)}
        inv_ids = [int(a["id"]) for a in accts if a.get("type") in ("Brokerage", "Margin", "Other Investment")]
        cost = 0.0
        if inv_ids:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT COALESCE(SUM(Quantity * COALESCE(Fifo_Avg_Cost_EUR, 0)), 0) FROM Holdings WHERE Quantity > 0 AND Accounts_Id = ANY(%s)", (inv_ids,))
                    cost = float(cur.fetchone()[0] or 0)
            finally:
                conn.close()
        out["assets"]["investments_cost"] = cost if cost > 0 else None
        if out["assets"]["investments"] > 0 and cost <= 0:
            out["notes"].append("Some of your investments have no recorded cost basis, so the gain on them is unknown; it is treated as if there were none.")
    except Exception as e:  # a missing piece never stops the planner opening
        log.warning("retirement assets: %s", e)
    return out


def suggestions() -> dict:
    """Starting values from the data Oikos already holds: what you have in each pot, what you earned, saved and spent over the last 12 months,
    and your portfolio's own volatility. Nothing here is saved until you save a plan."""
    from datetime import timedelta
    a = assets_for(None)
    out: dict = {"assets": a["assets"], "flows": {}, "calibration": None, "notes": a["notes"]}
    try:
        from api.routers.reports import get_income_expense
        end = date.today().replace(day=1) - timedelta(days=1)
        start = end.replace(day=1)
        for _ in range(11):
            start = (start - timedelta(days=1)).replace(day=1)
        rows = get_income_expense(start_date=str(start), end_date=str(end))
        tot = lambda k: float(sum(float(r.get(k) or 0) for r in rows))
        inc, intr, exp, tax = tot("income"), tot("interest"), tot("expense"), tot("tax")
        out["flows"] = {"from": str(start), "to": str(end), "income": inc + intr, "expense": exp, "tax": tax, "saved": max(inc + intr - exp - tax, 0.0)}
    except Exception as e:
        log.warning("retirement suggestions (flows): %s", e)
    try:
        from api.routers.reports import get_monte_carlo
        c = get_monte_carlo(years_ahead=1, num_sims=10, monthly_contrib=0.0, lookback_days=756, account_ids=None, initial_value=None,
                            override_return_pct=None, override_vol_pct=None)["calibration"]
        out["calibration"] = {"past_return_pct": c["ann_return_pct"], "vol_pct": c["ann_vol_pct"]}
    except Exception as e:
        log.warning("retirement suggestions (calibration): %s", e)
    return out


def starting_plan() -> dict:
    """A plan pre-filled from your data (see `suggestions`) on top of the defaults."""
    s = suggestions()
    c = country_setup()
    p = deepcopy(DEFAULTS)
    p["tax"] = deepcopy(c["tax"])
    p["retire_age"] = c["retire_age"]
    p["assets"].update({k: round(v, 2) for k, v in (s.get("assets") or {}).items() if v is not None})
    fl = s.get("flows") or {}
    if fl:
        p["contrib"]["investments"] = round(fl.get("saved", 0.0), 2)
        if fl.get("expense"):
            p["spend"]["annual"] = round(fl["expense"], 2)
    cal = s.get("calibration")
    if cal and cal.get("vol_pct"):
        # Your portfolio's own volatility, but never below what a 6% / 4.5% return plan needs: a book full of bonds and T-bills has been calm,
        # which says little about the next thirty years of a retirement portfolio.
        v = min(max(cal["vol_pct"], 12.0), 25.0)
        p["returns"]["vol_pre_pct"] = round(v, 1)
        p["returns"]["vol_post_pct"] = round(max(v * 0.75, 8.0), 1)
    return {"params": p, "suggestions": s, "country": {k: c[k] for k in ("code", "name", "has_tax_profile", "hints")}, "rules": c["rules"]}


# ── saved plans ──────────────────────────────────────────────────────────────────────────────────

def ensure_table() -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS Retirement_Plans (
                    Retirement_Plans_Id SERIAL PRIMARY KEY,
                    Name        TEXT NOT NULL,
                    Params      JSONB NOT NULL,
                    Is_Base     BOOLEAN NOT NULL DEFAULT FALSE,
                    Created_At  TIMESTAMP DEFAULT NOW(),
                    Updated_At  TIMESTAMP DEFAULT NOW()
                )""")
        conn.commit()
    finally:
        conn.close()


def list_plans() -> list[dict]:
    ensure_table()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT Retirement_Plans_Id, Name, Params, Is_Base, Updated_At::text FROM Retirement_Plans ORDER BY Is_Base DESC, Retirement_Plans_Id")
            return [{"id": r[0], "name": r[1], "params": r[2], "is_base": r[3], "updated_at": r[4]} for r in cur.fetchall()]
    finally:
        conn.close()


def save_plan(plan_id: int | None, name: str, params: dict, is_base: bool | None = None) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("Give the plan a name.")
    normalise(params)                                              # reject a plan that cannot be run
    ensure_table()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if plan_id:
                cur.execute("UPDATE Retirement_Plans SET Name = %s, Params = %s, Updated_At = NOW() WHERE Retirement_Plans_Id = %s RETURNING Retirement_Plans_Id",
                            (name, json.dumps(params), plan_id))
                row = cur.fetchone()
                if not row:
                    raise ValueError("That plan no longer exists.")
            else:
                cur.execute("SELECT COUNT(*) FROM Retirement_Plans")
                first = cur.fetchone()[0] == 0
                cur.execute("INSERT INTO Retirement_Plans (Name, Params, Is_Base) VALUES (%s, %s, %s) RETURNING Retirement_Plans_Id", (name, json.dumps(params), first))
                row = cur.fetchone()
            pid = row[0]
            if is_base:
                cur.execute("UPDATE Retirement_Plans SET Is_Base = (Retirement_Plans_Id = %s)", (pid,))
        conn.commit()
    finally:
        conn.close()
    return next(p for p in list_plans() if p["id"] == pid)


def delete_plan(plan_id: int) -> None:
    ensure_table()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM Retirement_Plans WHERE Retirement_Plans_Id = %s", (plan_id,))
            cur.execute("UPDATE Retirement_Plans SET Is_Base = TRUE WHERE Retirement_Plans_Id = (SELECT MIN(Retirement_Plans_Id) FROM Retirement_Plans) "
                        "AND NOT EXISTS (SELECT 1 FROM Retirement_Plans WHERE Is_Base)")
        conn.commit()
    finally:
        conn.close()
