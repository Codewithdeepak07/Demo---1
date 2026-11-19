"""
Field parsing step (LLM: Nova Lite).

Turns the English invoice text into structured fields:
header (invoice_no, date, vendor, PO, currency, totals) + line items.
"""

from app.agents.common import to_number
from app.services.llm_client import ask_llm_json, persona, prompt
from app.settings import REASONING_MODEL, RULES


def normalize_currency(value) -> str | None:
    """'$' -> 'USD', '€' -> 'EUR', 'usd' -> 'USD'. Unknown text is kept (upper-cased) so it can be flagged."""
    if value is None or str(value).strip() == "":
        return None
    value = str(value).strip()
    symbol_map = RULES.get("currency_symbol_map", {})
    if value in symbol_map:
        return symbol_map[value]
    return value.upper()


def clean_text(value) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def parse_invoice_fields(english_text: str) -> dict:
    data = ask_llm_json(REASONING_MODEL, persona("field_parser"),
                        prompt("parse_fields", text=english_text), max_tokens=3000)

    line_items = []
    for number, line in enumerate(data.get("line_items") or [], start=1):
        line_items.append({
            "line_number": number,
            "item_code": clean_text(line.get("item_code")),
            "description": clean_text(line.get("description")),
            "qty": to_number(line.get("qty")),
            "unit_price": to_number(line.get("unit_price")),
            "total": to_number(line.get("total")),
        })

    return {
        "invoice_no": clean_text(data.get("invoice_no")),
        "invoice_date": clean_text(data.get("invoice_date")),
        "vendor_id": None,                      # resolved later from the PO via mock-erp
        "vendor_name": clean_text(data.get("vendor_name")),
        "po_number": clean_text(data.get("po_number")),
        "currency_as_printed": clean_text(data.get("currency")),
        "currency": normalize_currency(data.get("currency")),
        "subtotal": to_number(data.get("subtotal")),
        "tax_rate": to_number(data.get("tax_rate")),
        "taxable_base": to_number(data.get("taxable_base")),
        "tax_amount": to_number(data.get("tax_amount")),
        "total_amount": to_number(data.get("total_amount")),
        "extras": data.get("extras") or {},
        "line_items": line_items,
    }