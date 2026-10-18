"""Support / resistance levels — one implementation for the price charts (via
GET /market-data/technical/levels) and the Dashboard's Support / Resistance alerts.

Method, for a date-sorted series of closes (with daily high/low where stored):
  1. Turning points (zig-zag): a peak counts once the price has since fallen from it by
     at least a reversal threshold, a trough once it has risen by that much — so daily
     noise is ignored and only real swings are kept. The threshold scales with the
     series' own volatility: 5x its average daily move, between 1% and 15% (about 2%
     for a currency, 5-8% for a typical stock). The first three bars are where the
     series starts, not a turn the price made, so they don't count.
  2. Grouping: peaks and troughs are pooled (a broken resistance tends to become
     support and vice versa) and grouped when they lie within a zone half the
     threshold wide (at least 0.5% of the last price), measured from the zone's lowest
     point so zones never chain into one wide band.
  3. A level is a zone's average price; its strength is how many turning points it
     holds ("touches"). The period's own high and low always count as levels.
  4. Each level is support if below the last price, resistance if above; the
     `per_side` nearest on each side are returned.
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

MIN_POINTS = 20


def _clean(points: Sequence[dict]) -> list[dict]:
    out = []
    for p in points:
        c = p.get("close")
        if c is None or not isinstance(c, (int, float)) or not math.isfinite(c) or c <= 0:
            continue
        out.append(p)
    return out


def _side_value(p: dict, key: str) -> float:
    v = p.get(key)
    if v is None or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
        return float(p["close"])
    return float(v)


def analyse(points: Sequence[dict]) -> Optional[dict]:
    """All levels of a series plus the parameters they came from:
    {"last", "threshold", "zone", "levels": [{price, touches, last_touch, extreme}]},
    or None when the series is too short. Unsorted and unclassified — see levels()."""
    pts = _clean(points)
    n = len(pts)
    if n < MIN_POINTS:
        return None
    hi = [_side_value(p, "high") for p in pts]
    lo = [_side_value(p, "low") for p in pts]
    last = float(pts[-1]["close"])

    move = sum(abs(float(pts[i]["close"]) / float(pts[i - 1]["close"]) - 1) for i in range(1, n))
    threshold = min(0.15, max(0.01, 5 * move / (n - 1)))

    swings: list[tuple[float, str, int]] = []  # (price, date, index)
    direction = 0
    ex_hi = ex_lo = 0
    for i in range(1, n):
        if direction == 0:
            if hi[i] > hi[ex_hi]:
                ex_hi = i
            if lo[i] < lo[ex_lo]:
                ex_lo = i
            if lo[i] <= hi[ex_hi] * (1 - threshold):
                swings.append((hi[ex_hi], pts[ex_hi]["date"], ex_hi)); direction = -1; ex_lo = i
            elif hi[i] >= lo[ex_lo] * (1 + threshold):
                swings.append((lo[ex_lo], pts[ex_lo]["date"], ex_lo)); direction = 1; ex_hi = i
        elif direction == 1:
            if hi[i] >= hi[ex_hi]:
                ex_hi = i
            elif lo[i] <= hi[ex_hi] * (1 - threshold):
                swings.append((hi[ex_hi], pts[ex_hi]["date"], ex_hi)); direction = -1; ex_lo = i
        else:
            if lo[i] <= lo[ex_lo]:
                ex_lo = i
            elif hi[i] >= lo[ex_lo] * (1 + threshold):
                swings.append((lo[ex_lo], pts[ex_lo]["date"], ex_lo)); direction = 1; ex_hi = i

    zone = last * max(0.005, threshold / 2)
    clusters: list[dict] = []
    for price, d, _i in sorted((s for s in swings if s[2] > 2), key=lambda s: s[0]):
        c = clusters[-1] if clusters else None
        if c and price - c["start"] <= zone:
            c["sum"] += price; c["count"] += 1
            if d > c["last"]:
                c["last"] = d
        else:
            clusters.append({"start": price, "sum": price, "count": 1, "last": d})
    levels = [{"price": c["sum"] / c["count"], "touches": c["count"], "last_touch": c["last"], "extreme": None}
              for c in clusters]

    i_max = max(range(n), key=lambda i: hi[i])
    i_min = min(range(n), key=lambda i: lo[i])
    for price, d, extreme in ((hi[i_max], pts[i_max]["date"], "high"), (lo[i_min], pts[i_min]["date"], "low")):
        near = next((l for l in levels if abs(l["price"] - price) <= zone), None)
        if near:
            near["extreme"] = extreme
        else:
            levels.append({"price": price, "touches": 1, "last_touch": d, "extreme": extreme})
    return {"last": last, "threshold": threshold, "zone": zone, "levels": levels}


def classify(levels: list[dict], last: float, per_side: int = 3) -> list[dict]:
    """Support below / resistance above `last`, the `per_side` nearest on each side,
    with distance_pct from it."""
    out = [{**l, "kind": "support" if l["price"] < last else "resistance",
            "distance_pct": (l["price"] / last - 1) * 100}
           for l in levels if l["price"] != last]
    res = []
    for kind in ("support", "resistance"):
        side = sorted((l for l in out if l["kind"] == kind), key=lambda l: abs(l["distance_pct"]))
        res.extend(side[:per_side])
    return res


def levels(points: Sequence[dict], per_side: int = 3) -> list[dict]:
    """Nearest support/resistance levels of a series (what the charts draw)."""
    a = analyse(points)
    return classify(a["levels"], a["last"], per_side) if a else []
