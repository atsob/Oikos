"""Country names for the exposure-by-country report (ISO 3166-1 alpha-2 codes).

A security's country is stored as its two-letter code. `country_name` turns it back into a name for
display; `parse_country` reads a code or a name (as copied from a fund provider's website, with the
usual aliases — "USA", "UK", "Korea", …) back into a code.
"""
from __future__ import annotations

COUNTRIES: dict[str, str] = {
    "AE": "United Arab Emirates", "AR": "Argentina", "AT": "Austria", "AU": "Australia", "BD": "Bangladesh",
    "BE": "Belgium", "BG": "Bulgaria", "BH": "Bahrain", "BM": "Bermuda", "BR": "Brazil", "CA": "Canada",
    "CH": "Switzerland", "CL": "Chile", "CN": "China", "CO": "Colombia", "CY": "Cyprus", "CZ": "Czech Republic",
    "DE": "Germany", "DK": "Denmark", "DO": "Dominican Republic", "EE": "Estonia", "EG": "Egypt", "ES": "Spain",
    "FI": "Finland", "FR": "France", "GB": "United Kingdom", "GR": "Greece", "HK": "Hong Kong", "HR": "Croatia",
    "HU": "Hungary", "ID": "Indonesia", "IE": "Ireland", "IL": "Israel", "IN": "India", "IS": "Iceland",
    "IT": "Italy", "JP": "Japan", "KE": "Kenya", "KR": "South Korea", "KW": "Kuwait", "KY": "Cayman Islands",
    "LT": "Lithuania", "LU": "Luxembourg", "LV": "Latvia", "MA": "Morocco", "MT": "Malta", "MX": "Mexico",
    "MY": "Malaysia", "NG": "Nigeria", "NL": "Netherlands", "NO": "Norway", "NZ": "New Zealand", "OM": "Oman",
    "PA": "Panama", "PE": "Peru", "PH": "Philippines", "PK": "Pakistan", "PL": "Poland", "PT": "Portugal",
    "QA": "Qatar", "RO": "Romania", "RS": "Serbia", "RU": "Russia", "SA": "Saudi Arabia", "SE": "Sweden",
    "SG": "Singapore", "SI": "Slovenia", "SK": "Slovakia", "TH": "Thailand", "TR": "Turkey", "TW": "Taiwan",
    "UA": "Ukraine", "US": "United States", "UY": "Uruguay", "VE": "Venezuela", "VN": "Vietnam", "ZA": "South Africa",
    "JE": "Jersey", "GG": "Guernsey", "IM": "Isle of Man", "LI": "Liechtenstein", "MC": "Monaco", "MO": "Macau",
    "BS": "Bahamas", "VG": "British Virgin Islands", "PR": "Puerto Rico", "JO": "Jordan", "LB": "Lebanon",
    "KZ": "Kazakhstan", "GE": "Georgia", "AM": "Armenia", "BY": "Belarus", "LK": "Sri Lanka", "TN": "Tunisia",
    "GH": "Ghana", "EC": "Ecuador", "CR": "Costa Rica", "UZ": "Uzbekistan", "AZ": "Azerbaijan", "BA": "Bosnia and Herzegovina",
    "MK": "North Macedonia", "AL": "Albania", "ME": "Montenegro", "MD": "Moldova", "XX": "Other / supranational",
    "AD": "Andorra", "GA": "Gabon", "MU": "Mauritius", "PY": "Paraguay", "BO": "Bolivia", "SV": "El Salvador",
    "GT": "Guatemala", "JM": "Jamaica", "TT": "Trinidad and Tobago", "SN": "Senegal", "CI": "Ivory Coast",
    "TZ": "Tanzania", "ZM": "Zambia", "AO": "Angola", "ET": "Ethiopia", "IQ": "Iraq", "MN": "Mongolia", "KH": "Cambodia",
    "HN": "Honduras", "NA": "Namibia", "BW": "Botswana", "RW": "Rwanda", "CG": "Congo", "MG": "Madagascar", "LY": "Libya",
}

_ALIASES = {
    "usa": "US", "u.s.": "US", "u.s.a.": "US", "united states of america": "US", "america": "US",
    "uk": "GB", "u.k.": "GB", "great britain": "GB", "britain": "GB", "england": "GB",
    "korea": "KR", "south korea": "KR", "republic of korea": "KR", "korea, republic of": "KR",
    "czechia": "CZ", "czech": "CZ", "russian federation": "RU", "turkiye": "TR", "türkiye": "TR",
    "uae": "AE", "emirates": "AE", "hong kong sar": "HK", "china (mainland)": "CN", "peoples republic of china": "CN",
    "holland": "NL", "the netherlands": "NL", "slovak republic": "SK", "viet nam": "VN",
    "supranational": "XX", "supranationals": "XX", "other": "XX", "others": "XX", "european union": "XX",
    "korea (south)": "KR", "croatia (hrvatska)": "HR", "cote d'ivoire": "CI", "côte d'ivoire": "CI",
    "russian federation": "RU", "taiwan, china": "TW", "hong kong, china": "HK",
}
_BY_NAME = {name.lower(): code for code, name in COUNTRIES.items()} | _ALIASES


def country_name(code: str | None) -> str:
    if not code:
        return "Unknown"
    return COUNTRIES.get(code.upper(), code.upper())


def parse_country(text: str | None) -> str | None:
    """'France' / 'FR' / 'USA' -> 'FR' / 'FR' / 'US'; None when it isn't recognised."""
    if not text:
        return None
    t = text.strip().strip('"').strip()
    if not t:
        return None
    if len(t) == 2 and t.upper() in COUNTRIES:
        return t.upper()
    return _BY_NAME.get(t.lower())


def isin_country(isin: str | None) -> str | None:
    """The country in an ISIN's two-letter prefix, or None for the supranational prefixes (XS, EU)
    and anything that isn't a country code. For a stock or bond this is its issuer's home market;
    for a fund it is only the fund's domicile (IE, LU …) and must not be used."""
    if not isin or len(isin) < 2:
        return None
    p = isin[:2].upper()
    return p if p in COUNTRIES and p != "XX" else None


_EURO = "AT BE BG CY DE EE ES FI FR GR HR IE IT LT LU LV MT NL PT SI SK".split()
COUNTRY_CURRENCY: dict[str, str] = {c: "EUR" for c in _EURO} | {
    "US": "USD", "JP": "JPY", "GB": "GBP", "CH": "CHF", "CA": "CAD", "AU": "AUD", "NZ": "NZD", "SE": "SEK", "NO": "NOK",
    "DK": "DKK", "PL": "PLN", "CZ": "CZK", "HU": "HUF", "RO": "RON", "RS": "RSD", "IS": "ISK", "CN": "CNY", "HK": "HKD",
    "TW": "TWD", "KR": "KRW", "IN": "INR", "ID": "IDR", "TH": "THB", "MY": "MYR", "SG": "SGD", "PH": "PHP", "VN": "VND",
    "PK": "PKR", "BD": "BDT", "BR": "BRL", "MX": "MXN", "CL": "CLP", "CO": "COP", "PE": "PEN", "AR": "ARS", "UY": "UYU",
    "ZA": "ZAR", "EG": "EGP", "NG": "NGN", "KE": "KES", "MA": "MAD", "TR": "TRY", "SA": "SAR", "AE": "AED", "QA": "QAR",
    "KW": "KWD", "BH": "BHD", "OM": "OMR", "IL": "ILS", "RU": "RUB", "UA": "UAH", "KZ": "KZT", "MO": "MOP", "JE": "GBP",
    "GG": "GBP", "IM": "GBP", "LI": "CHF", "MC": "EUR", "AD": "EUR", "KY": "USD", "BM": "USD", "VG": "USD", "PR": "USD",
    "PA": "USD", "BS": "USD", "EC": "USD", "SV": "USD",
}


def country_currency(code: str | None) -> str | None:
    return COUNTRY_CURRENCY.get((code or "").upper())
