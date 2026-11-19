"""
Invoice Data Validation Agent.

Rule checks (plain Python, driven by rules.yaml):
    1. required fields present (header + every line item)
    2. data types (dates are dates, numbers are numbers)
    3. currency is in the accepted list
    4. arithmetic: qty x price = line total, lines add up to the subtotal,
       tax = subtotal x tax rate, subtotal + tax = total

The LLM (Nova Lite) then explains the problems in plain language.
"""

import logging
from datetime import date

from app.agents.common import deviation_percent, make_discrepancy
from app.services.llm_client import ask_llm, persona, prompt
from app.settings import REASONING_MODEL

log = logging.getLogger(__name__)

MONEY_TOLERANCE = 0.01   # one cent


def _is_missing(value) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


# ---------------------------------------------------------------
# 1. Required fields
# ---------------------------------------------------------------
def check_required_fields(invoice: dict, rules: dict) -> list[dict]:
    issues = []
    for field in rules["required_fields"]["header"]:
        if _is_missing(invoice.get(field)):
            issues.append(make_discrepancy(
                "data", "missing_field", field,
                f"Required field '{field}' is missing from the invoice.",
            ))

    lines = invoice.get("line_items") or []
    if not lines:
        issues.append(make_discrepancy(
            "data", "missing_field", "line_items", "The invoice has no line items.",
            severity="high",
        ))

    for line in lines:
        number = line["line_number"]
        for field in rules["required_fields"]["line_item"]:
            if _is_missing(line.get(field)):
                issues.append(make_discrepancy(
                    "data", "missing_field", f"line_{number}.{field}",
                    f"Line {number}: required field '{field}' is missing.",
                    line_number=number,
                ))
    return issues


# ---------------------------------------------------------------
# 2. Data types
# ---------------------------------------------------------------
def _has_valid_type(value, expected_type: str) -> bool:
    if expected_type == "date":
        try:
            date.fromisoformat(str(value))
            return True
        except ValueError:
            return False
    if expected_type == "float":
        try:
            float(value)
            return True
        except (TypeError, ValueError):
            return False
    return isinstance(value, str)


def check_data_types(invoice: dict, rules: dict) -> list[dict]:
    issues = []
    types = rules["data_types"]
    line_fields = set(rules["required_fields"]["line_item"])

    for field, expected_type in types.items():
        if field in line_fields:
            continue
        value = invoice.get(field)
        if not _is_missing(value) and not _has_valid_type(value, expected_type):
            issues.append(make_discrepancy(
                "data", "invalid_type", field,
                f"'{field}' should be a {expected_type} but the invoice shows '{value}'.",
                invoice_value=value,
            ))

    for line in invoice.get("line_items") or []:
        for field, expected_type in types.items():
            if field not in line_fields:
                continue
            value = line.get(field)
            if not _is_missing(value) and not _has_valid_type(value, expected_type):
                issues.append(make_discrepancy(
                    "data", "invalid_type", f"line_{line['line_number']}.{field}",
                    f"Line {line['line_number']}: '{field}' should be a {expected_type}.",
                    invoice_value=value, line_number=line["line_number"],
                ))
    return issues


# ---------------------------------------------------------------
# 3. Currency
# ---------------------------------------------------------------
def check_currency(invoice: dict, rules: dict) -> list[dict]:
    currency = invoice.get("currency")
    accepted = rules["accepted_currencies"]
    if _is_missing(currency) or currency in accepted:
        return []
    return [make_discrepancy(
        "data", "invalid_currency", "currency",
        f"Currency '{currency}' is not accepted (accepted: {', '.join(accepted)}).",
        invoice_value=currency, erp_value=", ".join(accepted), severity="high",
    )]


# ---------------------------------------------------------------
# 4. Arithmetic
# ---------------------------------------------------------------
def check_arithmetic(invoice: dict, rules: dict) -> list[dict]:
    issues = []
    lines = invoice.get("line_items") or []

    # 4a. qty x unit price = line total, line by line
    for line in lines:
        qty, price, total = line.get("qty"), line.get("unit_price"), line.get("total")
        if None in (qty, price, total):
            continue
        expected = round(qty * price, 2)
        if abs(expected - total) > MONEY_TOLERANCE:
            issues.append(make_discrepancy(
                "data", "total_mismatch", f"line_{line['line_number']}.total",
                f"Line {line['line_number']}: {qty} x {price} = {expected}, but the invoice shows {total}.",
                invoice_value=total, erp_value=expected,
                deviation_pct=deviation_percent(total, expected),
                severity="high", line_number=line["line_number"],
            ))

    # 4b. sum of line totals = subtotal
    line_sum = round(sum(line["total"] for line in lines if line.get("total") is not None), 2)
    subtotal = invoice.get("subtotal")
    if subtotal is not None and lines and abs(line_sum - subtotal) > MONEY_TOLERANCE:
        issues.append(make_discrepancy(
            "data", "total_mismatch", "subtotal",
            f"Line totals add up to {line_sum}, but the subtotal shows {subtotal}.",
            invoice_value=subtotal, erp_value=line_sum,
            deviation_pct=deviation_percent(subtotal, line_sum), severity="high",
        ))

    base = subtotal if subtotal is not None else line_sum
    tax_amount = invoice.get("tax_amount")
    tax_rate = invoice.get("tax_rate")

    # 4c. tax = taxable amount x tax rate (only when the invoice prints a rate).
    #     Some invoices tax only part of the charges and print that base
    #     (e.g. "VAT 5.26% on 6,280,000"); use it instead of the whole subtotal.
    taxable_base = invoice.get("taxable_base")
    tax_base = taxable_base if taxable_base else base
    if tax_rate is not None and tax_amount is not None and tax_base:
        expected_tax = round(tax_base * tax_rate / 100, 2)
        deviation = deviation_percent(tax_amount, expected_tax)
        tolerance = rules["tolerances"]["tax_difference_percent"]
        if deviation is not None and abs(deviation) > tolerance:
            issues.append(make_discrepancy(
                "data", "tax_mismatch", "tax_amount",
                f"Tax should be {tax_rate}% of {tax_base} = {expected_tax}, but the invoice shows {tax_amount}.",
                invoice_value=tax_amount, erp_value=expected_tax,
                deviation_pct=deviation, severity="high",
            ))

    # 4d. subtotal + tax = total
    total_amount = invoice.get("total_amount")
    if total_amount is not None and base:
        expected_total = round(base + (tax_amount or 0), 2)
        if abs(expected_total - total_amount) > MONEY_TOLERANCE:
            issues.append(make_discrepancy(
                "data", "total_mismatch", "total_amount",
                f"Subtotal + tax = {expected_total}, but the invoice total is {total_amount}.",
                invoice_value=total_amount, erp_value=expected_total,
                deviation_pct=deviation_percent(total_amount, expected_total), severity="high",
            ))
    return issues


# ---------------------------------------------------------------
# Main entry points
# ---------------------------------------------------------------
def validate_invoice_data(invoice: dict, rules: dict) -> list[dict]:
    issues = []
    issues += check_required_fields(invoice, rules)
    issues += check_data_types(invoice, rules)
    issues += check_currency(invoice, rules)
    issues += check_arithmetic(invoice, rules)
    return issues


def explain_data_issues(invoice: dict, issues: list[dict]) -> str:
    """Ask the LLM for a short plain-language explanation of the data problems."""
    if not issues:
        return "All required fields are present and the invoice arithmetic is consistent."
    issue_lines = "\n".join(f"- {item['message']}" for item in issues)
    try:
        return ask_llm(REASONING_MODEL, persona("data_validator"),
                       prompt("data_validation_notes",
                              invoice_no=invoice.get("invoice_no") or "(no number)",
                              issues=issue_lines),
                       max_tokens=300).strip()
    except Exception as error:   # the rule results are what matter; the note is a nice-to-have
        log.warning("Could not get LLM explanation for data issues: %s", error)
        return issue_lines