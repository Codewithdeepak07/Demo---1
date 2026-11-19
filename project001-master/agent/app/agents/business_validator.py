"""
Business Validation Agent.

1. fetch_erp_records()   - calls mock-erp over HTTP (/po, /vendor, /sku)
2. validate_against_erp() - compares quantity, unit price, currency and tax
                            with the matching PO, using the tolerances in rules.yaml
3. explain_business_issues() - short LLM (Nova Lite) explanation

One discrepancy row is produced per mismatched field, with both values and the deviation.
"""

import logging
from difflib import SequenceMatcher

from app.agents.common import deviation_percent, make_discrepancy
from app.services import erp_client
from app.services.llm_client import ask_llm, persona, prompt
from app.settings import REASONING_MODEL

log = logging.getLogger(__name__)

DESCRIPTION_MATCH_THRESHOLD = 0.6


# ---------------------------------------------------------------
# ERP lookups (HTTP only - never the erp schema directly)
# ---------------------------------------------------------------
def fetch_erp_records(invoice: dict) -> dict:
    erp = {"po": None, "vendor": None, "skus": {}, "po_number": invoice.get("po_number")}

    if invoice.get("po_number"):
        erp["po"] = erp_client.get_purchase_order(invoice["po_number"])

    if erp["po"]:
        erp["vendor"] = erp_client.get_vendor(erp["po"]["vendor_id"])

    # every SKU mentioned on the invoice or on the PO
    codes = {line["item_code"] for line in invoice.get("line_items", []) if line.get("item_code")}
    if erp["po"]:
        codes |= {line["item_code"] for line in erp["po"]["line_items"]}
    for code in sorted(codes):
        sku = erp_client.get_sku(code)
        if sku:
            erp["skus"][code.upper()] = sku

    return erp


# ---------------------------------------------------------------
# Line matching: by item code first, by description when the code is missing
# ---------------------------------------------------------------
def _similarity(text_a: str, text_b: str) -> float:
    return SequenceMatcher(None, (text_a or "").lower(), (text_b or "").lower()).ratio()


def match_po_line(invoice_line: dict, po_lines: list[dict]) -> tuple[dict | None, str | None]:
    """Return (po_line, matched_by) where matched_by is 'item_code' or 'description'."""
    code = (invoice_line.get("item_code") or "").strip().upper()
    if code:
        for po_line in po_lines:
            if po_line["item_code"].upper() == code:
                return po_line, "item_code"
        return None, None     # a code was printed but it is not on the PO

    best_line, best_score = None, 0.0
    for po_line in po_lines:
        score = _similarity(invoice_line.get("description"), po_line.get("description"))
        if score > best_score:
            best_line, best_score = po_line, score
    if best_line and best_score >= DESCRIPTION_MATCH_THRESHOLD:
        return best_line, "description"
    return None, None


def gst_rate_for(item_code: str | None, erp: dict) -> float | None:
    if not item_code:
        return None
    sku = erp["skus"].get(item_code.upper())
    return float(sku["gst_rate"]) if sku else None


# ---------------------------------------------------------------
# Comparison against the PO
# ---------------------------------------------------------------
def validate_against_erp(invoice: dict, erp: dict, rules: dict) -> tuple[list[dict], list[dict]]:
    """Returns (discrepancies, line_matches)."""
    issues, line_matches = [], []
    tolerances = rules["tolerances"]

    # --- the PO itself --------------------------------------------------
    if not invoice.get("po_number"):
        issues.append(make_discrepancy(
            "business", "po_missing", "po_number",
            "The invoice does not reference a purchase order, so it cannot be matched in the ERP.",
            severity="high",
        ))
        return issues, line_matches

    po = erp.get("po")
    if po is None:
        issues.append(make_discrepancy(
            "business", "po_not_found", "po_number",
            f"Purchase order {invoice['po_number']} does not exist in the ERP.",
            invoice_value=invoice["po_number"], severity="high",
        ))
        return issues, line_matches

    if erp.get("vendor") is None:
        issues.append(make_discrepancy(
            "business", "vendor_not_found", "vendor_id",
            f"Vendor {po['vendor_id']} on the PO is not in the vendor master.",
            erp_value=po["vendor_id"], severity="high",
        ))

    # --- currency ------------------------------------------------------
    po_currency = po["line_items"][0]["currency"] if po["line_items"] else None
    if invoice.get("currency") and po_currency and invoice["currency"] != po_currency:
        issues.append(make_discrepancy(
            "business", "currency_mismatch", "currency",
            f"Invoice is in {invoice['currency']} but PO {po['po_number']} is in {po_currency}.",
            invoice_value=invoice["currency"], erp_value=po_currency, severity="high",
        ))

    # --- line by line --------------------------------------------------
    expected_tax = 0.0
    tax_known = False

    for line in invoice.get("line_items", []):
        number = line["line_number"]
        po_line, matched_by = match_po_line(line, po["line_items"])
        line_matches.append({
            "line_number": number,
            "po_item_code": po_line["item_code"] if po_line else None,
            "matched_by": matched_by,
        })

        if po_line is None:
            issues.append(make_discrepancy(
                "business", "item_not_on_po", f"line_{number}.item_code",
                f"Line {number} ({line.get('item_code') or line.get('description')}) "
                f"is not on PO {po['po_number']}.",
                invoice_value=line.get("item_code") or line.get("description"),
                severity="high", line_number=number,
            ))
            continue

        # quantity
        if line.get("qty") is not None:
            deviation = deviation_percent(line["qty"], float(po_line["qty"]))
            if deviation is not None and abs(deviation) > tolerances["quantity_difference_percent"]:
                issues.append(make_discrepancy(
                    "business", "qty_mismatch", f"line_{number}.qty",
                    f"Line {number}: invoiced quantity {line['qty']} differs from PO quantity {po_line['qty']}.",
                    invoice_value=line["qty"], erp_value=po_line["qty"],
                    deviation_pct=deviation, severity="high", line_number=number,
                ))

        # unit price
        if line.get("unit_price") is not None:
            deviation = deviation_percent(line["unit_price"], float(po_line["unit_price"]))
            if deviation is not None and abs(deviation) > tolerances["price_difference_percent"]:
                issues.append(make_discrepancy(
                    "business", "price_mismatch", f"line_{number}.unit_price",
                    f"Line {number}: unit price {line['unit_price']} is {deviation:+.2f}% "
                    f"from the PO price {po_line['unit_price']} "
                    f"(allowed ±{tolerances['price_difference_percent']}%).",
                    invoice_value=line["unit_price"], erp_value=po_line["unit_price"],
                    deviation_pct=deviation, severity="high", line_number=number,
                ))

        # SKU master / tax
        gst_rate = gst_rate_for(po_line["item_code"], erp)
        if gst_rate is None:
            issues.append(make_discrepancy(
                "business", "sku_not_found", f"line_{number}.item_code",
                f"SKU {po_line['item_code']} is not in the SKU master.",
                invoice_value=po_line["item_code"], line_number=number,
            ))
        else:
            line_total = line.get("total")
            if line_total is None and None not in (line.get("qty"), line.get("unit_price")):
                line_total = line["qty"] * line["unit_price"]
            if line_total is not None:
                expected_tax += line_total * gst_rate / 100
                tax_known = True

    # --- tax amount vs SKU GST rates --------------------------------------
    tax_amount = invoice.get("tax_amount")
    if tax_known and tax_amount is not None:
        expected_tax = round(expected_tax, 2)
        deviation = deviation_percent(tax_amount, expected_tax)
        if deviation is not None and abs(deviation) > tolerances["tax_difference_percent"]:
            issues.append(make_discrepancy(
                "business", "tax_mismatch", "tax_amount",
                f"Tax on the invoice is {tax_amount}; ERP GST rates give {expected_tax} "
                f"(allowed ±{tolerances['tax_difference_percent']}%).",
                invoice_value=tax_amount, erp_value=expected_tax,
                deviation_pct=deviation, severity="high",
            ))

    return issues, line_matches


def explain_business_issues(invoice: dict, issues: list[dict]) -> str:
    if not issues:
        return "Quantities, unit prices, currency and tax all agree with the ERP purchase order."
    issue_lines = "\n".join(f"- {item['message']}" for item in issues)
    try:
        return ask_llm(REASONING_MODEL, persona("business_validator"),
                       prompt("business_validation_notes",
                              invoice_no=invoice.get("invoice_no") or "(no number)",
                              po_number=invoice.get("po_number") or "(none)",
                              issues=issue_lines),
                       max_tokens=300).strip()
    except Exception as error:
        log.warning("Could not get LLM explanation for business issues: %s", error)
        return issue_lines
