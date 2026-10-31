"""Tax-return codes: which line of the return each figure goes on.

The Tax Return summary (Reports -> Investment Tax -> Tax Return) lists every figure a return needs, with the code of the line it goes on. The codes belong to a
country's form, so — like the retirement planner's tax profile — they follow the Country of Usage setting (Tools -> App Settings -> Country of Usage): a country
with an entry in PROFILES gets its codes as starting values, any other country starts with none. Whatever the starting values, every code can be typed over on the
screen; the changes are kept per country (`tax_return_codes:<country>` in the app settings) and win over the starting values.

A row key names the figure: `gains:<tax category>` (net capital gain of a category), `losses:<tax category>` (a taxable category's net loss), `dividends:<category>`,
`interest:<category>`, `bank_interest`, `crypto:purchases`, `crypto:gain`, `crypto:capital`, `crypto:rewards`. For a capital-gains category the code set on the
category itself (Static Data -> Tax Categories -> Gains Tax Code) is used before the country's.

To add a country: add an entry to PROFILES (name, form, codes, and optionally `income`: the codes for interest by source / withholder, see Greece) — nothing else changes. The codes are starting points to check against the form's instructions.
"""
from __future__ import annotations

import json

PROFILES: dict[str, dict] = {
    "GR": {
        "name": "Greece", "form": "E1",
        "dividend_rate": 5.0,                                   # the flat tax on dividends (E1 2025, Table 4Δ1): what a Greek company withholds, and all Greece itself would charge on a foreign one
        "codes": {
            # Table 4Ε, E1 2025 instructions: the loss of the year on securities (shares, bonds, derivatives, CFDs, FX Spot) is 871 – 872, a gain on a foreign issuer's securities 865 – 866 (a Greek issuer's 829 – 830); crypto lines as listed by the TaxApps.gr crypto calculator.
            "crypto:purchases": "743 – 744", "crypto:gain": "865 – 866", "crypto:capital": "781 – 782",
            "losses:*": "871 – 872",
        },
        # Interest on bonds and deposits (E1, Table 4Δ1): the gross interest by where it comes from, the tax withheld by who withheld it; interest that is exempt
        # (Greek government bonds and T-bills) goes to Table 6. Where nothing was withheld the tax codes stay empty and the 15% is settled when the return is assessed.
        "income": {
            "interest_domestic": "667 – 668",                 # interest from a Greek source (Greek corporate bonds, Greek bank deposits)
            "interest_foreign": "669 – 670",                  # interest from abroad (foreign corporate or government bonds)
            "interest_exempt": "659 – 660",                   # tax-exempt interest (Table 6)
            "withheld_interest_domestic": "675 – 676",        # tax withheld by the Greek issuer or bank on Greek-source interest
            "withheld_interest_custodian": "677 – 678",       # tax withheld by a Greek bank or custodian on interest from abroad
            "withheld_interest_abroad": "683 – 684",          # tax withheld abroad (credited against the Greek tax; keep the broker's certificate)
            # Dividends (E1, Table 4Δ1; the codes are those of the 2024 and 2025 instructions): the same split by source and by who withheld.
            "dividend_domestic": "291 – 292",                 # gross dividends of Greek companies
            "dividend_foreign": "295 – 296",                  # gross dividends of foreign companies
            "dividend_exempt": "659 – 660",                   # tax-exempt dividends (Table 6)
            "withheld_dividend_domestic": "293 – 294",        # tax withheld by the Greek company on its dividends
            "withheld_dividend_custodian": "297 – 298",       # tax withheld in Greece (a Greek bank or custodian) on dividends of foreign companies
            "withheld_dividend_abroad": "683 – 684",          # tax withheld abroad
            # Capital gains on securities (Table 4Ε): by where the issuer is, and the losses of earlier years still to offset (the loss of the year is `losses:*` above).
            "gain_domestic": "829 – 830",                     # gain on securities of a Greek issuer (with its tax number)
            "gain_foreign": "865 – 866",                      # gain on securities of a foreign issuer
            "gain_exempt": "659 – 660",                       # gain that is exempt (listed shares under 0.5%, bought before 2009, corporate bonds, Greek / EU funds): Table 6
            "loss_carried": "873 – 874",                      # losses of the previous years not yet offset, kept five years
        },
        # A rate that changes on a date, for income whose rate the Tax Category holds as a single number. Interest on listed corporate bonds of an individual resident in
        # Greece was taxed at 15% up to 11 April 2025 and is taxed at 5% after it (E1 2025, Table 4Δ1, codes 667-668 / 669-670: myAADE asks which of the two applies).
        # Applies to the interest of a taxable (not exempt) security of the Bond category whose issuer is not a government; the Tax Category's own rate is the one before.
        "rate_changes": [
            {"section": "interest", "category": "Bond", "government": False, "from": "2025-04-12", "rate": 5.0,
             "label": "listed corporate bonds: 5% from 12 Apr 2025 (15% up to 11 Apr 2025)"},
        ],
    },
}


def rate_change(cc: str, section: str, category: str, government: bool, on: str) -> dict | None:
    """The dated rate rule of a country that covers an income row, with `applies` set when the row's date `on` (YYYY-MM-DD) is on or after the rule's date; None when no rule covers it."""
    for r in (PROFILES.get((cc or "").strip().upper()) or {}).get("rate_changes", []):
        if r["section"] == section and r["category"] == category and bool(r["government"]) == bool(government):
            return {**r, "applies": str(on)[:10] >= r["from"]}
    return None


def _setting_key(cc: str) -> str:
    return f"tax_return_codes:{cc or 'none'}"


def country_codes(cc: str | None = None) -> dict:
    """The form, the country's starting codes and the user's own overrides for a country (default: the Country of Usage setting)."""
    from database.local_markets import PROFILES as MARKETS, home_country
    from database.queries import get_app_setting
    cc = (cc if cc is not None else home_country()).strip().upper()
    prof = PROFILES.get(cc)
    try:
        mine = json.loads(get_app_setting(_setting_key(cc)) or "{}")
    except Exception:
        mine = {}
    return {"code": cc, "name": (prof or {}).get("name") or (MARKETS.get(cc) or {}).get("name") or "", "form": (prof or {}).get("form") or "",
            "has_profile": prof is not None, "dividend_rate": (prof or {}).get("dividend_rate"), "defaults": dict((prof or {}).get("codes") or {}), "income": dict((prof or {}).get("income") or {}), "overrides": mine}


def resolve(setup: dict, key: str, category_code: str | None = None) -> str:
    """The code for a row: the user's own, then the category's (capital gains), then the country's starting value, else empty."""
    if key in setup["overrides"]:
        return str(setup["overrides"][key])
    if category_code:
        return str(category_code)
    d = setup["defaults"]
    if key in d:
        return d[key]
    if ":" in key and f"{key.split(':', 1)[0]}:*" in d:
        return d[f"{key.split(':', 1)[0]}:*"]
    return ""


def save_overrides(codes: dict, cc: str | None = None) -> dict:
    from database.queries import save_app_setting
    setup = country_codes(cc)
    clean = {str(k): str(v).strip() for k, v in (codes or {}).items() if isinstance(k, str)}
    save_app_setting(_setting_key(setup["code"]), json.dumps(clean))
    return country_codes(setup["code"])
