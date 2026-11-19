"""
Recalculation Agent - used when an auditor clicks "Re-validate".

Step 1  apply_corrections(): start from the ORIGINAL extracted invoice and lay the
        auditor's latest correction for each field on top (corrections come from
        audit.human_feedback - the original rows are never changed).

Step 2  recalculate(): rebuild every number from the start, line by line:
            line total = qty x unit price
            line tax   = line total x GST rate of the SKU (from mock-erp)
            subtotal   = sum of line totals
            tax        = sum of line taxes
            total      = subtotal + tax
        Next to each line we also calculate what the ERP purchase order says
        (ERP qty x ERP price, ERP tax) so the report can show both side by side.
"""

import copy

from app.agents.business_validator import gst_rate_for, match_po_line
from app.agents.common import deviation_percent, to_number
from app.agents.field_parser import normalize_currency
from app.settings import RULES

NUMBER_FIELDS = {"qty", "unit_price"}
EDITABLE_HEADER_FIELDS = {"invoice_no", "invoice_date", "vendor_name", "po_number", "currency"}
EDITABLE_LINE_FIELDS = {"item_code", "description", "qty", "unit_price"}


def _convert(field: str, value):
    """Feedback is stored as text - turn it back into the right Python type."""
    if value is None or str(value).strip() == "":
        return None
    if field in NUMBER_FIELDS:
        return to_number(value)
    if field == "currency":
        return normalize_currency(value)
    return str(value).strip()


def apply_corrections(original: dict, feedback_rows: list[dict]) -> tuple[dict, list[dict]]:
    """Returns (corrected_invoice, corrections_applied)."""
    corrected = copy.deepcopy(original)

    # the latest correction for each field wins (rows arrive oldest first)
    latest = {}
    for row in feedback_rows:
        latest[row["field_name"]] = row

    applied = []
    for field_name, row in latest.items():
        new_value = row["corrected_value"]

        if field_name.startswith("line_"):
            # e.g. "line_2.unit_price"
            line_part, field = field_name.split(".", 1)
            line_number = int(line_part.replace("line_", ""))
            line = next((l for l in corrected["line_items"] if l["line_number"] == line_number), None)
            if line is None or field not in EDITABLE_LINE_FIELDS:
                continue
            original_line = next(l for l in original["line_items"] if l["line_number"] == line_number)
            original_value = original_line.get(field)
            line[field] = _convert(field, new_value)
            converted = line[field]
        else:
            if field_name not in EDITABLE_HEADER_FIELDS:
                continue
            original_value = original.get(field_name)
            corrected[field_name] = _convert(field_name, new_value)
            converted = corrected[field_name]

        applied.append({
            "field_name": field_name,
            "original_value": original_value,
            "corrected_value": converted,
            "corrected_by": row.get("corrected_by"),
            "corrected_at": str(row.get("corrected_at")),
        })

    return corrected, applied


def recalculate(invoice: dict, erp: dict) -> tuple[dict, dict]:
    """
    Returns (recalculated_invoice, recalculation_table).
    The recalculated invoice has its line totals, subtotal, tax and total rebuilt.
    """
    result = copy.deepcopy(invoice)
    po_lines = erp["po"]["line_items"] if erp.get("po") else []
    tolerances = RULES["tolerances"]

    rows = []
    for line in result["line_items"]:
        qty, price = line.get("qty"), line.get("unit_price")
        original_total = line.get("total")

        # --- the invoice, recalculated -----------------------------------
        new_total = round(qty * price, 2) if None not in (qty, price) else None

        po_line, matched_by = match_po_line(line, po_lines)
        sku_code = line.get("item_code") or (po_line["item_code"] if po_line else None)

        gst_rate = gst_rate_for(sku_code, erp)
        gst_source = "ERP SKU master"
        if gst_rate is None:
            gst_rate = invoice.get("tax_rate") or 0.0
            gst_source = "invoice" if invoice.get("tax_rate") else "none"

        new_tax = round(new_total * gst_rate / 100, 2) if new_total is not None else None

        # --- what the ERP purchase order says ----------------------------
        erp_qty = float(po_line["qty"]) if po_line else None
        erp_price = float(po_line["unit_price"]) if po_line else None
        erp_total = round(erp_qty * erp_price, 2) if po_line else None
        erp_tax = round(erp_total * gst_rate / 100, 2) if erp_total is not None else None

        qty_dev = deviation_percent(qty, erp_qty)
        price_dev = deviation_percent(price, erp_price)
        if po_line is None:
            status = "not on PO"
        elif (qty_dev is not None and abs(qty_dev) > tolerances["quantity_difference_percent"]) or \
             (price_dev is not None and abs(price_dev) > tolerances["price_difference_percent"]):
            status = "differs from ERP"
        else:
            status = "matches ERP"

        rows.append({
            "line_number": line["line_number"],
            "item_code": line.get("item_code"),
            "erp_item_code": po_line["item_code"] if po_line else None,
            "matched_by": matched_by,
            "description": line.get("description"),
            "qty": qty,
            "erp_qty": erp_qty,
            "unit_price": price,
            "erp_unit_price": erp_price,
            "original_line_total": original_total,
            "recalculated_line_total": new_total,
            "erp_line_total": erp_total,
            "gst_rate": gst_rate,
            "gst_source": gst_source,
            "recalculated_tax": new_tax,
            "erp_tax": erp_tax,
            "status": status,
        })

        line["total"] = new_total

    def total_of(key):
        values = [row[key] for row in rows if row[key] is not None]
        return round(sum(values), 2) if values else None

    subtotal = total_of("recalculated_line_total")
    tax = total_of("recalculated_tax")
    # invoice taxes only part of its charges (printed taxable base) and the ERP has no
    # SKU rates for these lines: recalculate tax on that base, not on every line
    if invoice.get("taxable_base") and invoice.get("tax_rate") and \
            all(row["gst_source"] != "ERP SKU master" for row in rows):
        tax = round(invoice["taxable_base"] * invoice["tax_rate"] / 100, 2)
    erp_subtotal = total_of("erp_line_total")
    erp_tax = total_of("erp_tax")

    result["subtotal"] = subtotal
    result["tax_amount"] = tax
    result["total_amount"] = round((subtotal or 0) + (tax or 0), 2) if subtotal is not None else None
    rates = {row["gst_rate"] for row in rows}
    result["tax_rate"] = rates.pop() if len(rates) == 1 else invoice.get("tax_rate")

    table = {
        "lines": rows,
        "original_totals": {
            "subtotal": invoice.get("subtotal"),
            "tax_amount": invoice.get("tax_amount"),
            "total_amount": invoice.get("total_amount"),
        },
        "recalculated_totals": {
            "subtotal": result["subtotal"],
            "tax_amount": result["tax_amount"],
            "total_amount": result["total_amount"],
        },
        "erp_totals": {
            "subtotal": erp_subtotal,
            "tax_amount": erp_tax,
            "total_amount": round((erp_subtotal or 0) + (erp_tax or 0), 2) if erp_subtotal is not None else None,
        },
    }
    return result, table