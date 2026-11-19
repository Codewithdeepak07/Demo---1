"""Small helpers shared by the validation, recalculation and reporting agents."""

import re


def to_number(value) -> float | None:
    """Turn '1,470.00', '$12', 3 or None into a float (or None)."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[^\d.\-]", "", str(value))
    try:
        return float(cleaned)
    except ValueError:
        return None


def deviation_percent(actual: float, expected: float) -> float | None:
    """How far `actual` is from `expected`, in percent. None if expected is zero.

    Clamped to ±99_999_999.9999 so it always fits in NUMERIC(12,4).
    A result beyond that range still means "wildly wrong" — the clamp
    just prevents a PostgreSQL NumericValueOutOfRange on insert.
    """
    if expected in (None, 0) or actual is None:
        return None
    raw = (actual - expected) / expected * 100
    # NUMERIC(12,4) max absolute value is 99_999_999.9999
    clamped = max(-99_999_999.9999, min(99_999_999.9999, raw))
    return round(clamped, 4)


def make_discrepancy(check_type: str, rule: str, field_name: str, message: str,
                     invoice_value=None, erp_value=None, deviation_pct=None,
                     severity: str = "medium", line_number: int | None = None) -> dict:
    """One discrepancy row - one per mismatched field, with both values and the deviation."""
    return {
        "check_type": check_type,          # data | business
        "rule": rule,                      # e.g. missing_field, price_mismatch
        "field_name": field_name,          # e.g. currency, line_2.unit_price
        "line_number": line_number,
        "invoice_value": invoice_value,
        "erp_value": erp_value,
        "deviation_pct": deviation_pct,
        "severity": severity,              # high | medium
        "message": message,
    }