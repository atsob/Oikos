"""Tax-return codes: which line of the return each figure goes on.

The Tax Return summary (Reports -> Investment Tax -> Tax Return) lists every figure a return needs, with the code of the line it goes on. The codes belong to a
country's form, so — like the retirement planner's tax profile — they follow the Country of Usage setting (Tools -> App Settings -> Country of Usage): a country
with an entry in PROFILES gets its codes as starting values, any other country starts with none. Whatever the starting values, every code can be typed over on the
screen; the changes are kept per country (`tax_return_codes:<country>` in the app settings) and win over the starting values.

A row key names the figure: `gains:<tax category>` (net capital gain of a category), `losses:<tax category>` (a taxable category's net loss), `dividends:<category>`,
`interest:<category>`, `bank_interest`, `crypto:purchases`, `crypto:gain`, `crypto:capital`, `crypto:rewards`. For a capital-gains category the code set on the
category itself (Static Data -> Tax Categories -> Gains Tax Code) is used before the country's.

To add a country: add an entry to PROFILES (name, form, codes) — nothing else changes. The codes are starting points to check against the form's instructions.
"""
from __future__ import annotations

import json

PROFILES: dict[str, dict] = {
    "GR": {
        "name": "Greece", "form": "E1",
        "codes": {
            # As listed by the TaxApps.gr crypto calculator for the return on a year's income, and the loss line of derivatives, CFDs, FX Spot and crypto.
            "crypto:purchases": "743", "crypto:gain": "865 – 866", "crypto:capital": "781 – 782",
            "losses:*": "869 – 870",
        },
    },
}


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
            "has_profile": prof is not None, "defaults": dict((prof or {}).get("codes") or {}), "overrides": mine}


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
