"""
All reads and writes on the `audit` schema live in this file.

Vocabulary used in Python (same names as rules.yaml):
    header: invoice_no, invoice_date, vendor_id, vendor_name, po_number,
            currency, subtotal, tax_rate, tax_amount, total_amount
    line:   line_number, item_code, description, qty, unit_price, total

Database column names differ slightly (invoice_number, quantity, line_total),
so the mapping is done here and nowhere else.
"""

import json
import uuid
from datetime import date
from decimal import Decimal

from psycopg.types.json import Jsonb

from app.db.connection import get_connection


# ---------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------
def to_money(value) -> Decimal | None:
    """Convert a float to a 2-decimal Decimal so NUMERIC columns never get float noise."""
    if value is None or value == "":
        return None
    return Decimal(str(round(float(value), 2)))


def to_number(value, places: int = 4) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(round(float(value), places)))


def to_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def to_currency_code(value) -> str | None:
    """The column is CHAR(3). Anything that is not a 3-letter code stays only in extracted_raw."""
    if value and len(str(value)) == 3:
        return str(value).upper()
    return None


def to_float(value) -> float | None:
    return None if value is None else float(value)


def _json_safe(data):
    """Make dicts with Decimal/date values safe for JSONB."""
    return json.loads(json.dumps(data, default=str))


# ---------------------------------------------------------------
# Invoice monitor
# ---------------------------------------------------------------
def checksum_exists(checksum: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM audit.invoice_audit WHERE file_checksum = %s", (checksum,)
        ).fetchone()
    return row is not None


def register_invoice(file_path: str, checksum: str, email_meta: dict | None) -> str:
    """Record a newly detected file. Every detected file gets a row, even if it fails later."""
    invoice_id = str(uuid.uuid4())
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO audit.invoice_audit
                (invoice_id, file_path, file_checksum, processing_status, extracted_raw, thread_id)
            VALUES (%s, %s, %s, 'detected', %s, %s)
            """,
            (invoice_id, file_path, checksum, Jsonb({"email_meta": email_meta or {}}), invoice_id),
        )
    return invoice_id


def mark_failed(invoice_id: str, error_message: str) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE audit.invoice_audit
            SET processing_status = 'failed', error_message = %s
            WHERE invoice_id = %s
            """,
            (error_message[:2000], invoice_id),
        )


# ---------------------------------------------------------------
# Extraction / translation / parsing (written once, during the first run)
# ---------------------------------------------------------------
def save_extraction(invoice_id: str, method: str, raw_text: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE audit.invoice_audit SET extraction_method = %s, raw_extracted_text = %s "
            "WHERE invoice_id = %s",
            (method, raw_text, invoice_id),
        )


def save_translation(invoice_id: str, translated_text: str, language: str, confidence: float) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE audit.invoice_audit
            SET translated_text = %s, source_language = %s, translation_confidence = %s
            WHERE invoice_id = %s
            """,
            (translated_text, language[:8], to_number(confidence, 3), invoice_id),
        )


def save_parsed_invoice(invoice_id: str, invoice: dict) -> None:
    """Store header fields in typed columns and each line item as its own row."""
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE audit.invoice_audit
            SET invoice_number = %s, invoice_date = %s, vendor_name = %s, po_number = %s,
                currency = %s, subtotal = %s, tax_amount = %s, total_amount = %s,
                extracted_raw = extracted_raw || %s
            WHERE invoice_id = %s
            """,
            (
                invoice.get("invoice_no"),
                to_date(invoice.get("invoice_date")),
                invoice.get("vendor_name"),
                invoice.get("po_number"),
                to_currency_code(invoice.get("currency")),
                to_money(invoice.get("subtotal")),
                to_money(invoice.get("tax_amount")),
                to_money(invoice.get("total_amount")),
                Jsonb(_json_safe({
                    "extras": invoice.get("extras", {}),
                    "tax_rate": invoice.get("tax_rate"),
                    "taxable_base": invoice.get("taxable_base"),
                    "currency_as_printed": invoice.get("currency_as_printed"),
                    "invoice_date_as_parsed": invoice.get("invoice_date"),
                })),
                invoice_id,
            ),
        )
        for line in invoice.get("line_items", []):
            conn.execute(
                """
                INSERT INTO audit.invoice_line_items
                    (invoice_id, line_number, item_code, description, quantity, unit_price, line_total)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    invoice_id,
                    line["line_number"],
                    line.get("item_code"),
                    line.get("description"),
                    to_number(line.get("qty"), 3),
                    to_number(line.get("unit_price"), 4),
                    to_money(line.get("total")),
                ),
            )


def save_vendor_id(invoice_id: str, vendor_id: str | None) -> None:
    """vendor_id is not printed on invoices - it is resolved from the PO via mock-erp."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE audit.invoice_audit SET vendor_id = %s WHERE invoice_id = %s",
            (vendor_id, invoice_id),
        )


# ---------------------------------------------------------------
# Discrepancies, outcome and reports
# ---------------------------------------------------------------
def save_discrepancies(invoice_id: str, validation_run: int, discrepancies: list[dict]) -> None:
    with get_connection() as conn:
        for item in discrepancies:
            conn.execute(
                """
                INSERT INTO audit.discrepancies
                    (invoice_id, validation_run, line_number, check_type, rule, field_name,
                     invoice_value, erp_value, deviation_pct, severity, message)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    invoice_id,
                    validation_run,
                    item.get("line_number"),
                    item.get("check_type"),
                    item.get("rule"),
                    item.get("field_name"),
                    None if item.get("invoice_value") is None else str(item["invoice_value"]),
                    None if item.get("erp_value") is None else str(item["erp_value"]),
                    to_number(item.get("deviation_pct"), 4),
                    item.get("severity"),
                    item.get("message"),
                ),
            )


def save_outcome(invoice_id: str, validation_status: str, recommendation: str) -> None:
    """Store the latest decision. This is a decision, not an extracted value, so it may change."""
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE audit.invoice_audit
            SET validation_status = %s, recommendation = %s,
                processing_status = 'processed', error_message = NULL
            WHERE invoice_id = %s
            """,
            (validation_status, recommendation, invoice_id),
        )


def save_report(invoice_id: str, report: dict, json_path: str, html_path: str) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO audit.validation_reports
                (invoice_id, validation_run, run_type, validation_status, recommendation,
                 report_json, json_path, html_path)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                invoice_id,
                report["validation_run"],
                report["run_type"],
                report["validation_status"],
                report["recommendation"],
                Jsonb(_json_safe(report)),
                json_path,
                html_path,
            ),
        )


# ---------------------------------------------------------------
# Reading an invoice back (used by re-validation and RAG indexing)
# ---------------------------------------------------------------
def get_invoice_row(invoice_id: str) -> dict | None:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM audit.invoice_audit WHERE invoice_id = %s", (invoice_id,)
        ).fetchone()


def load_original_invoice(invoice_id: str) -> dict | None:
    """Rebuild the invoice dict exactly as it was first extracted (no corrections)."""
    row = get_invoice_row(invoice_id)
    if row is None:
        return None

    extracted_raw = row.get("extracted_raw") or {}
    with get_connection() as conn:
        lines = conn.execute(
            """
            SELECT line_number, item_code, description, quantity, unit_price, line_total
            FROM audit.invoice_line_items
            WHERE invoice_id = %s
            ORDER BY line_number
            """,
            (invoice_id,),
        ).fetchall()

    return {
        "invoice_no": row["invoice_number"],
        "invoice_date": row["invoice_date"].isoformat() if row["invoice_date"] else None,
        "vendor_id": row["vendor_id"],
        "vendor_name": row["vendor_name"],
        "po_number": row["po_number"],
        "currency": row["currency"],
        "subtotal": to_float(row["subtotal"]),
        "tax_rate": extracted_raw.get("tax_rate"),
        "taxable_base": extracted_raw.get("taxable_base"),
        "tax_amount": to_float(row["tax_amount"]),
        "total_amount": to_float(row["total_amount"]),
        "extras": extracted_raw.get("extras", {}),
        "line_items": [
            {
                "line_number": line["line_number"],
                "item_code": line["item_code"],
                "description": line["description"],
                "qty": to_float(line["quantity"]),
                "unit_price": to_float(line["unit_price"]),
                "total": to_float(line["line_total"]),
            }
            for line in lines
        ],
    }


def get_latest_run_number(invoice_id: str) -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(validation_run), 0) AS run FROM audit.validation_reports "
            "WHERE invoice_id = %s",
            (invoice_id,),
        ).fetchone()
    return int(row["run"])


def get_report(invoice_id: str, validation_run: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT report_json FROM audit.validation_reports "
            "WHERE invoice_id = %s AND validation_run = %s",
            (invoice_id, validation_run),
        ).fetchone()
    return row["report_json"] if row else None


# ---------------------------------------------------------------
# Human feedback (INSERT only - the original values are never overwritten)
# ---------------------------------------------------------------
def add_feedback(invoice_id: str, field_name: str, original_value, corrected_value,
                 corrected_by: str) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO audit.human_feedback
                (invoice_id, field_name, original_value, corrected_value, corrected_by)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                invoice_id,
                field_name,
                None if original_value is None else str(original_value),
                None if corrected_value is None else str(corrected_value),
                corrected_by,
            ),
        )


def get_feedback(invoice_id: str) -> list[dict]:
    """All corrections for an invoice, oldest first (so later ones win when applied)."""
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT id, field_name, original_value, corrected_value, corrected_by,
                   corrected_at, revalidated
            FROM audit.human_feedback
            WHERE invoice_id = %s
            ORDER BY corrected_at, id
            """,
            (invoice_id,),
        ).fetchall()


def mark_feedback_revalidated(invoice_id: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE audit.human_feedback SET revalidated = TRUE "
            "WHERE invoice_id = %s AND revalidated = FALSE",
            (invoice_id,),
        )


# ---------------------------------------------------------------
# RAG support
# ---------------------------------------------------------------
def list_processed_invoices() -> list[dict]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT invoice_id FROM audit.invoice_audit WHERE processing_status = 'processed'"
        ).fetchall()


def get_latest_report(invoice_id: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT report_json FROM audit.validation_reports
            WHERE invoice_id = %s ORDER BY validation_run DESC LIMIT 1
            """,
            (invoice_id,),
        ).fetchone()
    return row["report_json"] if row else None


def run_read_only_query(sql: str, max_rows: int = 50) -> list[dict]:
    """Run a generated SELECT inside a READ ONLY transaction with a timeout."""
    with get_connection() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '5s'")
        rows = conn.execute(f"SELECT * FROM ({sql}) AS answer LIMIT {int(max_rows)}").fetchall()
        conn.rollback()
    return _json_safe(rows)