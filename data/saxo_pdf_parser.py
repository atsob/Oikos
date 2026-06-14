"""SAXO Bank Transaction and Balance Report — PDF parser.

Parses the manually-downloaded PDF (generated via Saxo's Account → Reports →
Transaction and Balance) and extracts non-trade account entries:

  • CFDFinance          — overnight TOM-Next financing charges
  • Cashdividend        — cash dividends from stock positions
  • CFDcashadjustment   — dividend equivalents for CFD holders
  • CustodyFee          — monthly account maintenance fee (→ CashOut)
  • VAT                 — value-added tax on certain fees (→ CashOut)
  • FinancingCost       — interest on cash balance (→ CashOut)
  • Deposit             — cash deposit (→ CashIn)
  • OtherEvent          — miscellaneous account bookings

The returned records are compatible with
``data.saxo_connector.run_charges_import()`` so they can be imported directly
or used for reconciliation against API-fetched charges.

Requires: pdfplumber  (pip install pdfplumber)

PDF Layout Notes
----------------
The SAXO Transaction Report uses a multi-row cell layout for instrument names.
Each charge entry spans up to three pdfplumber rows:

  [pre-row]   instrument name fragment (camelCase-concatenated, e.g. "iSharesSilverTrust")
  [date-row]  DD-Mon-YYYY  DD-Mon-YYYY  -  <Product>  [InstrCol]  <Ccy>  <ChargeType>  amounts…
  [post-row]  instrument name continuation (e.g. "ETF")

Column x0 boundaries (empirically measured from diagnostic runs):
  Trade Date / Value Date  : x0 <  110
  Trade ID                 : 110 <= x0 < 165   ("-" for charges, numeric for trades)
  Product                  : 165 <= x0 < 200   ("CFDETF", "Cash", "Stock", "CFDFund"…)
  Instrument (in-row)      : 200 <= x0 < 295   (sometimes has name fragment)
  Instrument Currency      : 295 <= x0 < 330   ("USD", "EUR"…)
  Charge Type              : 330 <= x0 < 475   ("CFDFinance", "CustodyFee", "VAT"…)
  Amounts                  : x0 >= 475

CFD Cash Adjustment exception
------------------------------
This entry type is split across three PDF rows with the charge-type keyword
split between the pre-row and the post-row:

  [pre-row]   InstrName_part1  |  "CFDcash"@x_type
  [date-row]  DD-Mon-YYYY  …  Product  Currency  (no type token)  amounts
  [post-row]  InstrName_part2  |  "adjustment"@x_type

The parser detects this pattern by recognising "CFDcash" as a charge-type
prefix on a non-date row and then looking ahead for the suffix on the next
non-date row after the date row.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MONTH: dict[str, int] = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4,
    "May": 5, "Jun": 6, "Jul": 7, "Aug": 8,
    "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

# SAXO PDF entry type → DB Investments.Action
# Cash-product entries (CustodyFee, VAT, FinancingCost) map to CashOut;
# Deposit maps to CashIn.  Instrument-linked charges keep MiscExp / MiscInc.
_PDF_CHARGE_MAP: dict[str, str] = {
    # Dividends
    "Cashdividend":        "Dividend",
    "Dividend":            "Dividend",
    # CFD cash adjustments (dividend equivalent for CFD holders)
    "CFDcashadjustment":   "MiscInc",
    "CFDCashAdjustment":   "MiscInc",
    # CFD overnight financing charges
    "CFDFinance":          "MiscExp",
    "CfdFinance":          "MiscExp",
    # Account-level Cash entries → CashOut
    "CustodyFee":          "CashOut",
    "VAT":                 "CashOut",
    "FinancingCost":       "CashOut",
    # Deposits and withdrawals are recorded in the linked cash account
    # via bank import — exclude here to avoid duplicates.
    # "Deposit":           intentionally excluded
    # "Withdrawal":        intentionally excluded
    # Miscellaneous events (may be instrument-linked)
    "OtherEvent":          "MiscExp",
    "DepositoryCharges":   "MiscExp",
    "ExchangeFee":         "MiscExp",
    "WithholdingTax":      "MiscExp",
}

# Sorted longest → shortest so "CFDcashadjustment" matches before "CFDcash"
_PDF_CHARGE_KEYS: list[str] = sorted(
    _PDF_CHARGE_MAP.keys(), key=len, reverse=True
)

# Charge types that are purely account-level (Cash product, no instrument).
# These get the Saxo Bank placeholder security and a ||date|amount dedup key.
_ACCT_LEVEL_TYPES: frozenset[str] = frozenset({
    "CustodyFee", "VAT", "FinancingCost",
})

# Date pattern: DD-Mon-YYYY (case-insensitive for robustness)
_DATE_RE = re.compile(
    r"^\d{2}-(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-\d{4}$",
    re.IGNORECASE,
)

_SAXO_PREFIX  = "SAXO|"
_CHARGE_PREFIX = f"{_SAXO_PREFIX}CHARGE|"

# ---------------------------------------------------------------------------
# Column x-position boundaries
# (measured empirically from pdfplumber diagnostic on real SAXO PDFs)
# ---------------------------------------------------------------------------

_X_DATE_MAX    = 110   # Trade Date / Value Date
_X_TRADEID_MAX = 165   # Trade ID column ("-" for charges, numeric for trades)
_X_PRODUCT_MAX = 200   # Product column (CFDETF, Cash, Stock, CFDFund…)
_X_INSTR_MAX   = 295   # In-row Instrument name column
_X_CCY_MAX     = 330   # Instrument Currency column
_X_TYPE_MAX    = 475   # Charge Type column (ends before Open/Close at ~477)
# x0 >= _X_TYPE_MAX  → amount columns


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_date(s: str) -> date | None:
    """Convert a DD-Mon-YYYY string to a date object, or None on failure."""
    try:
        d, m, y = s.split("-")
        return date(int(y), _MONTH[m.capitalize()[:3]], int(d))
    except (ValueError, KeyError):
        return None


def _to_float(s: str) -> float | None:
    """Parse a float string, returning None on failure."""
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _detect_charge_type(tokens: list[str]) -> str | None:
    """Return the first recognised charge type found in *tokens* (joined).

    The check is substring-based and case-insensitive so that both
    ``"CFDFinance"`` and ``"cfdfinance"`` match the key ``"CFDFinance"``.
    """
    joined = "".join(tokens).lower()          # squash spaces for safety
    for key in _PDF_CHARGE_KEYS:
        if key.lower() in joined:
            return key
    return None


def _is_charge_type_prefix(s: str) -> bool:
    """Return True if ``s`` (squash-lowered) is a strict prefix of any charge key.

    Used to detect the CFD Cash Adjustment pre-row pattern where ``"CFDcash"``
    appears in the Type column of a non-date row.
    """
    sq = s.lower().replace(" ", "")
    for key in _PDF_CHARGE_KEYS:
        kl = key.lower()
        if kl.startswith(sq) and sq != kl:
            return True
    return False


# ---------------------------------------------------------------------------
# PDF word-position extractor
# ---------------------------------------------------------------------------

def _extract_page_rows(page) -> list[list[dict]]:
    """Return words grouped into horizontal rows by y-position proximity.

    Each row is a list of word dicts (keys: text, x0, top).
    Within a row words are sorted left-to-right.
    """
    words = page.extract_words(
        x_tolerance=3,
        y_tolerance=3,
        keep_blank_chars=False,
        use_text_flow=False,
    )
    if not words:
        return []

    rows: list[list[dict]] = []
    current: list[dict]    = [words[0]]
    cur_y:   float         = words[0]["top"]

    for w in words[1:]:
        if abs(w["top"] - cur_y) <= 4:
            current.append(w)
        else:
            rows.append(sorted(current, key=lambda x: x["x0"]))
            current = [w]
            cur_y   = w["top"]

    if current:
        rows.append(sorted(current, key=lambda x: x["x0"]))

    return rows


# ---------------------------------------------------------------------------
# Transaction page detector
# ---------------------------------------------------------------------------

def _is_transaction_page(page) -> bool:
    """Return True if this page contains the Transactions section."""
    sample = (page.extract_text() or "")[:500]
    return "Transactions -" in sample


# ---------------------------------------------------------------------------
# Column extraction helpers
# ---------------------------------------------------------------------------

def _col(row: list[dict], x_lo: float, x_hi: float) -> list[str]:
    """Return text tokens from *row* whose x0 falls in [x_lo, x_hi)."""
    return [w["text"] for w in row if x_lo <= w["x0"] < x_hi]


def _is_date_row(row: list[dict]) -> bool:
    """True if the row contains at least one date token at Trade-Date position."""
    return any(
        w["x0"] < _X_DATE_MAX and _DATE_RE.match(w["text"])
        for w in row
    )


# ---------------------------------------------------------------------------
# Public parser
# ---------------------------------------------------------------------------

def parse_saxo_transactions_pdf(
    pdf_path:             str | Path,
    account_id_override:  str = "",
) -> list[dict]:
    """Parse a SAXO Transaction and Balance Report PDF.

    Returns charge records compatible with
    ``data.saxo_connector.run_charges_import()``.

    Parameters
    ----------
    pdf_path            : Path to the downloaded SAXO PDF report.
    account_id_override : SAXO AccountId to assign to all records
                          (auto-extracted from the PDF header if omitted).
    """
    try:
        import pdfplumber
    except ImportError as e:
        raise ImportError(
            "pdfplumber is required for PDF import. "
            "Install with: pip install pdfplumber"
        ) from e

    records:    list[dict] = []
    account_id: str        = account_id_override

    with pdfplumber.open(str(pdf_path)) as pdf:

        # ── Extract account ID from cover page ──────────────────────────────
        if not account_id:
            cover = (
                (pdf.pages[0].extract_text() or "")
                + (pdf.pages[1].extract_text() or "")
            )
            m = re.search(r"Account\(s\):\s*(\S+)", cover)
            if m:
                account_id = m.group(1)

        # ── Process transaction pages ────────────────────────────────────────
        for page in pdf.pages:
            if not _is_transaction_page(page):
                continue

            rows      = _extract_page_rows(page)
            n_rows    = len(rows)
            skip_rows: set[int] = set()

            # Per-page parser state
            prev_name:            str  = ""   # instrument name from most-recent pre-row
            pending_type_prefix:  str  = ""   # partial charge-type key (CFD cash adj)

            for row_idx, row in enumerate(rows):
                if row_idx in skip_rows:
                    continue

                # ── Non-date row: instrument name fragment ──────────────────
                if not _is_date_row(row):
                    instr_frag = " ".join(_col(row, _X_PRODUCT_MAX, _X_INSTR_MAX)).strip()
                    instr_frag = instr_frag.rstrip("-").strip()

                    if instr_frag and len(instr_frag) < 80:
                        prev_name = instr_frag   # REPLACE (always use the latest fragment)

                    # Check for CFD Cash Adjustment type-prefix in the Type column
                    type_frags = _col(row, _X_CCY_MAX, _X_TYPE_MAX)
                    if type_frags:
                        joined_type = "".join(type_frags)
                        if _is_charge_type_prefix(joined_type):
                            pending_type_prefix = joined_type
                    continue

                # ── Date row ─────────────────────────────────────────────────
                date_words = [
                    w for w in row
                    if w["x0"] < _X_DATE_MAX and _DATE_RE.match(w["text"])
                ]
                trade_date = _parse_date(date_words[0]["text"]) if date_words else None
                if trade_date is None:
                    prev_name           = ""
                    pending_type_prefix = ""
                    continue

                # Skip trade-execution rows (Trade ID is a number, not "-")
                tradeid_toks = _col(row, _X_DATE_MAX, _X_TRADEID_MAX)
                if tradeid_toks and tradeid_toks[0] != "-":
                    prev_name           = ""
                    pending_type_prefix = ""
                    continue

                # ── Detect charge type ────────────────────────────────────────
                type_toks   = _col(row, _X_CCY_MAX, _X_TYPE_MAX)
                charge_type = _detect_charge_type(type_toks)

                # CFD Cash Adjustment pattern:
                # The type keyword is split across the pre-row ("CFDcash") and
                # the post-row ("adjustment").  The date-row's type column is empty.
                # _resolve_split_charge_type returns the charge-type key AND the
                # instrument fragment from the suffix row so it is not lost.
                split_post_part = ""
                if charge_type is None and pending_type_prefix:
                    charge_type, split_post_part = _resolve_split_charge_type(
                        rows, row_idx, n_rows, skip_rows,
                        pending_type_prefix,
                    )

                pending_type_prefix = ""   # consumed (whether we found a type or not)

                if charge_type is None:
                    prev_name = ""
                    continue

                action = _PDF_CHARGE_MAP.get(charge_type)
                if action is None:
                    prev_name = ""
                    continue

                # ── In-row instrument name (Instrument column) ────────────────
                instr_in_row = " ".join(_col(row, _X_PRODUCT_MAX, _X_INSTR_MAX)).strip()

                # ── Determine if Cash-product (account-level) entry ───────────
                # IMPORTANT: check this BEFORE calling _find_post_part.
                # For Cash-product entries (CustodyFee, VAT, FinancingCost,
                # Deposit) there is no instrument, and the look-ahead would
                # otherwise steal the next instrument entry's pre-row.
                product_toks    = _col(row, _X_TRADEID_MAX, _X_PRODUCT_MAX)
                is_cash_product = "Cash" in product_toks

                if is_cash_product or charge_type in _ACCT_LEVEL_TYPES:
                    # Account-level entry — no instrument, no post-row look-ahead
                    instr_name = charge_type
                else:
                    # ── Post-row instrument name continuation ─────────────────
                    # Prefer the fragment already captured by split detection;
                    # otherwise search the next non-date, non-type-prefix row.
                    post_part = split_post_part or _find_post_part(
                        rows, row_idx, n_rows, skip_rows
                    )

                    # Assemble: [pre-row] + [in-row instr] + [post-row]
                    # All three can contribute to the full instrument name.
                    # Example: pre="iSharesDiversified", in-row="CommoditySwap",
                    #          post="UCITSETF" → "iSharesDiversified CommoditySwap UCITSETF"
                    parts = []
                    if prev_name:
                        parts.append(prev_name)
                    if instr_in_row:
                        parts.append(instr_in_row)
                    if post_part:
                        parts.append(post_part)

                    instr_name = " ".join(parts).strip().rstrip("-").strip()

                # ── Amount columns ────────────────────────────────────────────
                # The PDF can have up to three numeric columns right of the Type
                # column (x0 >= _X_TYPE_MAX), left-to-right:
                #   [1] Amount in instrument currency  (only present for FX entries)
                #   [2] Conversion Rate                (only present for FX entries)
                #   [3] Amount in account currency (EUR) — always the rightmost
                #
                # Collect all non-zero floats with their x-positions, sorted L→R.
                amt_words = sorted(
                    [w for w in row if w["x0"] >= _X_TYPE_MAX],
                    key=lambda w: w["x0"],
                )
                amt_vals: list[tuple[float, float]] = []   # (x0, value)
                for w in amt_words:
                    v = _to_float(w["text"])
                    if v is not None and v != 0.0:
                        amt_vals.append((w["x0"], v))

                if not amt_vals:
                    log.debug(
                        "saxo_pdf_parser: no non-zero amount on row %d; tokens=%s",
                        row_idx, [w["text"] for w in amt_words],
                    )
                    prev_name = ""
                    continue

                # Rightmost value = account-currency (EUR) total
                amount = amt_vals[-1][1]

                # Derive security-currency amount and FX rate from column count
                # by computing the ratio: our FX_Rate = |EUR amount| / |sec amount|
                # (acc/sec direction, e.g. EUR per USD).
                _pdf_sec_amt: float | None = None
                _pdf_fx_rate: float | None = None

                if len(amt_vals) >= 3:
                    # [sec_amount, conv_rate_token, eur_amount, ...]
                    # Use the leftmost value as sec_amount and compute the FX rate
                    # from the ratio (avoids direction ambiguity with the rate token).
                    _pdf_sec_amt = abs(amt_vals[0][1])
                    if _pdf_sec_amt:
                        _pdf_fx_rate = round(abs(amount) / _pdf_sec_amt, 8)
                elif len(amt_vals) == 2:
                    # Charge rows with exactly 2 captured values have the layout
                    # [ConversionRate, BookedAmount_EUR].  The intermediate
                    # "Amount in sec currency" column is empty/zero for most charges
                    # (e.g. CFD Cash Adjustment).  Treat the first value as the rate
                    # and derive sec_amount = eur_amount / rate.
                    _pdf_fx_rate = abs(amt_vals[0][1])
                    if _pdf_fx_rate and _pdf_fx_rate != 1.0:
                        _pdf_sec_amt = round(abs(amount) / _pdf_fx_rate, 8)
                    else:
                        # Same-currency (rate = 1.0): no conversion needed
                        _pdf_fx_rate = None
                        _pdf_sec_amt = None
                # Single value → EUR-only entry; sec_amt and fx_rate stay None

                # ── Currency ──────────────────────────────────────────────────
                ccy_toks = _col(row, _X_INSTR_MAX, _X_CCY_MAX)
                currency = ccy_toks[0] if ccy_toks else "EUR"

                # ── Dedup key ─────────────────────────────────────────────────
                charge_key = charge_type.upper().replace(" ", "").replace("-", "")
                date_str   = trade_date.isoformat()

                if (
                    charge_type not in _ACCT_LEVEL_TYPES
                    and instr_name
                    and instr_name != charge_type
                ):
                    # Instrument-linked charge: slug-based key
                    instr_slug = re.sub(r"\s+", "", instr_name[:20]).upper()
                    desc = f"{_CHARGE_PREFIX}{charge_key}|{instr_slug}|{date_str}"
                else:
                    # Account-level fee: amount-based key to distinguish same-date entries
                    amt_str = f"{abs(amount):.4f}".replace(".", "_")
                    desc    = f"{_CHARGE_PREFIX}{charge_key}||{date_str}|{amt_str}"

                records.append({
                    "record_type":     "investment",
                    "source":          "SAXO_PDF",
                    "desc":            desc,
                    "symbol":          "",
                    "name":            instr_name,
                    "isin":            "",
                    "currency":        currency,
                    "asset_category":  "Other",
                    "instrument_type": "Other",
                    "saxo_asset_type": "",
                    "date":            trade_date,
                    "action":          action,
                    "quantity":        0.0,
                    "price":           0.0,
                    "commission":      0.0,
                    "total_eur":       round(abs(amount), 2),
                    # Multi-currency fields — populated when Conversion Rate is present
                    "total_sec_cur":   round(_pdf_sec_amt, 8) if _pdf_sec_amt else None,
                    "fx_rate_db":      _pdf_fx_rate,
                    "exchange":        "",
                    "account_id_str":  account_id,
                    "charge_type":     charge_type,
                    "pdf_raw_amount":  amount,
                })

                prev_name = ""   # consumed; reset for next entry

    log.info(
        "saxo_pdf_parser: extracted %d charge records from %s",
        len(records), pdf_path,
    )
    return records


# ---------------------------------------------------------------------------
# Parser helpers (factored out of main loop for readability)
# ---------------------------------------------------------------------------

def _resolve_split_charge_type(
    rows:                 list[list[dict]],
    date_row_idx:         int,
    n_rows:               int,
    skip_rows:            set[int],
    pending_type_prefix:  str,
) -> tuple[str | None, str]:
    """Attempt to complete a split charge type (CFD Cash Adjustment pattern).

    The pre-row has ``"CFDcash"`` in the Type column; the date-row has no
    Type token; the post-row has ``"adjustment"`` in the Type column.

    When found, the post-row index is added to *skip_rows*, and the function
    returns ``(charge_type_key, instr_fragment_from_post_row)``.

    Returns ``(None, "")`` if no matching post-row is found.
    """
    for nxt_idx in range(date_row_idx + 1, min(date_row_idx + 4, n_rows)):
        if nxt_idx in skip_rows:
            continue
        nxt = rows[nxt_idx]
        if _is_date_row(nxt):
            break   # hit a date row — give up
        nxt_type_toks = _col(nxt, _X_CCY_MAX, _X_TYPE_MAX)
        if not nxt_type_toks:
            continue
        combined = (
            pending_type_prefix.replace(" ", "")
            + "".join(nxt_type_toks).replace(" ", "")
        )
        ct = _detect_charge_type([combined])
        if ct:
            skip_rows.add(nxt_idx)
            instr_frag = " ".join(_col(nxt, _X_PRODUCT_MAX, _X_INSTR_MAX)).strip()
            return ct, instr_frag
    return None, ""


def _find_post_part(
    rows:      list[list[dict]],
    row_idx:   int,
    n_rows:    int,
    skip_rows: set[int],
) -> str:
    """Look ahead for an instrument name continuation row.

    Returns the instrument-column fragment from the first non-date, non-skipped
    row after *row_idx* that has no Type-column token (so we don't accidentally
    consume a CFD Cash Adjustment pre-row for the NEXT entry).

    The matched row is added to *skip_rows* to prevent it from being used as
    a pre-row for the subsequent charge entry.
    """
    for nxt_idx in range(row_idx + 1, min(row_idx + 4, n_rows)):
        if nxt_idx in skip_rows:
            continue
        nxt = rows[nxt_idx]
        if _is_date_row(nxt):
            break
        # If this row has a Type-column token it is a CFD Cash Adj pre-row for
        # the NEXT entry — do not consume it as our continuation.
        if _col(nxt, _X_CCY_MAX, _X_TYPE_MAX):
            break
        instr_frag = " ".join(_col(nxt, _X_PRODUCT_MAX, _X_INSTR_MAX)).strip()
        if instr_frag:
            skip_rows.add(nxt_idx)
            return instr_frag
        break   # empty row — stop searching
    return ""


# ---------------------------------------------------------------------------
# Trade-row commission extractor
# ---------------------------------------------------------------------------

def parse_trade_commissions(pdf_path: str | Path) -> dict[str, float]:
    """Extract Booked Costs (commission) for each trade from a SAXO PDF.

    Parses transaction rows that have a numeric Trade ID (stock/ETF/FX trades)
    and returns a mapping of TradeID → commission_eur (always positive).

    The PDF Transaction table columns (right side):
      ... | ConversionRate | RealizedP/L | Booked amount | Booked Costs | Total costs

    Booked Costs is the second-to-last numeric value in each trade row.
    """
    try:
        import pdfplumber
    except ImportError as e:
        raise ImportError("pdfplumber is required. pip install pdfplumber") from e

    result: dict[str, float] = {}

    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            if not _is_transaction_page(page):
                continue

            rows = _extract_page_rows(page)
            for row in rows:
                if not _is_date_row(row):
                    continue

                # Only trade rows have a numeric Trade ID
                tradeid_toks = _col(row, _X_DATE_MAX, _X_TRADEID_MAX)
                if not tradeid_toks or tradeid_toks[0] == "-":
                    continue
                trade_id = tradeid_toks[0]
                if not trade_id.isdigit():
                    continue

                # Collect all numeric tokens to the right of the Type column.
                # Layout: ... ConversionRate | RealizedP/L | BookedAmt | BookedCosts | TotalCosts
                # BookedCosts is always the second-to-last numeric value.
                amt_words = sorted(
                    [w for w in row if w["x0"] >= _X_TYPE_MAX],
                    key=lambda w: w["x0"],
                )
                nums: list[float] = []
                for w in amt_words:
                    v = _to_float(w["text"])
                    if v is not None:
                        nums.append(v)

                # Need at least: BookedAmt + BookedCosts + TotalCosts (last 3)
                if len(nums) < 3:
                    continue

                booked_costs = nums[-2]   # second-to-last = Booked Costs
                if booked_costs != 0.0:
                    result[trade_id] = round(abs(booked_costs), 4)

    log.info(
        "saxo_pdf_parser: extracted commissions for %d trades from %s",
        len(result), pdf_path,
    )
    return result


# ---------------------------------------------------------------------------
# Reconciliation helper
# ---------------------------------------------------------------------------

def reconcile_charges(
    api_records:  list[dict],
    pdf_records:  list[dict],
    amount_tol:   float = 0.02,
) -> list[dict]:
    """Match PDF charge records against API-fetched (or DB-loaded) records.

    Parameters
    ----------
    api_records  : records from ``parse_charges()`` (or loaded from DB)
    pdf_records  : records from ``parse_saxo_transactions_pdf()``
    amount_tol   : tolerance for amount comparison (default 0.02 EUR)

    Returns
    -------
    Annotated list of *pdf_records*, each with an added ``recon_status`` key:
      "✅ Matched"      — found a matching API record (date + type + amount ≈)
      "⚠️ Amt mismatch" — date + type match but amount differs > tol
      "🆕 Missing"      — no API record matches
    and an ``api_amount`` key containing the matched API record's total_eur
    (or None if unmatched).
    """
    # Build lookup: (date, normalised_charge_type) → [total_eur, …]
    api_index: dict[tuple, list[float]] = {}
    for r in api_records:
        key = (r["date"], r.get("charge_type", "").upper())
        api_index.setdefault(key, []).append(float(r["total_eur"]))

    result = []
    for r in pdf_records:
        key     = (r["date"], r.get("charge_type", "").upper())
        amts    = api_index.get(key, [])
        pdf_amt = float(r["total_eur"])
        api_amt = None

        if not amts:
            status = "🆕 Missing"
        else:
            closest = min(amts, key=lambda a: abs(a - pdf_amt))
            api_amt = closest
            if abs(closest - pdf_amt) <= amount_tol:
                status = "✅ Matched"
                amts.remove(closest)
                if not amts:
                    del api_index[key]
                else:
                    api_index[key] = amts
            else:
                status = "⚠️ Amt mismatch"

        result.append({**r, "recon_status": status, "api_amount": api_amt})

    return result
