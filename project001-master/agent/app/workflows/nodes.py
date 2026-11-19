"""
LangGraph nodes.

Each node is a small function: read what it needs from the state, call one
agent, save the result to PostgreSQL, and return the new state keys.
The same validation / reporting nodes are reused by the first run and by
re-validation.
"""

import logging
from typing import TypedDict

from app.agents import business_validator, data_validator, extractor, recalculator, reporter, translator
from app.agents.field_parser import parse_invoice_fields
from app.db import audit_repository as repo
from app.rag import indexer
from app.settings import RULES

log = logging.getLogger(__name__)


class InvoiceState(TypedDict, total=False):
    # identity
    invoice_id: str
    file_path: str
    file_type: str
    email_meta: dict
    validation_run: int
    run_type: str                     # initial | revalidation
    # extraction + translation
    raw_text: str
    extraction_method: str
    translated_text: str
    source_language: str
    translation_confidence: float
    # structured invoice + ERP data
    invoice: dict
    erp: dict
    line_matches: list
    # validation
    data_discrepancies: list
    business_discrepancies: list
    data_notes: str
    business_notes: str
    # re-validation only
    corrections_applied: list
    previous_report: dict
    recalculation: dict
    # output
    report: dict


# ---------------------------------------------------------------
# Routing + extraction (first run only)
# ---------------------------------------------------------------
def route_by_file_type(state: InvoiceState) -> str:
    """Monitor agent routing decision: which extraction path this file takes."""
    return state["file_type"]


def _extract(state: InvoiceState) -> dict:
    raw_text, method = extractor.extract_text(state["file_path"], state["file_type"])
    repo.save_extraction(state["invoice_id"], method, raw_text)
    log.info("Extracted %d characters from %s using %s", len(raw_text), state["file_path"], method)
    return {"raw_text": raw_text, "extraction_method": method}


def extract_pdf_node(state: InvoiceState) -> dict:
    return _extract(state)


def extract_docx_node(state: InvoiceState) -> dict:
    return _extract(state)


def extract_image_node(state: InvoiceState) -> dict:
    return _extract(state)


def translate_node(state: InvoiceState) -> dict:
    language_hint = (state.get("email_meta") or {}).get("language")
    result = translator.translate_invoice(state["raw_text"], language_hint)
    repo.save_translation(state["invoice_id"], result["translated_text"],
                          result["source_language"], result["translation_confidence"])
    log.info("Language %s, translation confidence %.2f",
             result["source_language"], result["translation_confidence"])
    return result


def parse_fields_node(state: InvoiceState) -> dict:
    invoice = parse_invoice_fields(state["translated_text"])
    repo.save_parsed_invoice(state["invoice_id"], invoice)
    return {"invoice": invoice}


# ---------------------------------------------------------------
# Re-validation only
# ---------------------------------------------------------------
def load_corrected_invoice_node(state: InvoiceState) -> dict:
    """Original extraction + the auditor's latest corrections (from human_feedback)."""
    invoice_id = state["invoice_id"]
    original = repo.load_original_invoice(invoice_id)
    corrected, applied = recalculator.apply_corrections(original, repo.get_feedback(invoice_id))

    row = repo.get_invoice_row(invoice_id)
    previous_run = state["validation_run"] - 1
    return {
        "invoice": corrected,
        "corrections_applied": applied,
        "previous_report": repo.get_report(invoice_id, previous_run) or {},
        "file_path": row["file_path"],
        "source_language": row["source_language"],
        "translation_confidence": float(row["translation_confidence"] or 0),
    }


def recalculate_node(state: InvoiceState) -> dict:
    invoice, table = recalculator.recalculate(state["invoice"], state["erp"])
    return {"invoice": invoice, "recalculation": table}


# ---------------------------------------------------------------
# Validation (both runs)
# ---------------------------------------------------------------
def lookup_erp_node(state: InvoiceState) -> dict:
    """Business validation agent, part 1: fetch PO, vendor and SKUs from mock-erp."""
    invoice = dict(state["invoice"])
    erp = business_validator.fetch_erp_records(invoice)

    # vendor_id is not printed on the invoices - take it from the matching PO
    invoice["vendor_id"] = erp["po"]["vendor_id"] if erp["po"] else None
    if state["run_type"] == "initial":
        repo.save_vendor_id(state["invoice_id"], invoice["vendor_id"])
    return {"invoice": invoice, "erp": erp}


def validate_data_node(state: InvoiceState) -> dict:
    issues = data_validator.validate_invoice_data(state["invoice"], RULES)
    notes = data_validator.explain_data_issues(state["invoice"], issues)
    return {"data_discrepancies": issues, "data_notes": notes}


def validate_business_node(state: InvoiceState) -> dict:
    issues, matches = business_validator.validate_against_erp(state["invoice"], state["erp"], RULES)
    notes = business_validator.explain_business_issues(state["invoice"], issues)

    all_issues = state.get("data_discrepancies", []) + issues
    repo.save_discrepancies(state["invoice_id"], state["validation_run"], all_issues)
    return {"business_discrepancies": issues, "business_notes": notes, "line_matches": matches}


# ---------------------------------------------------------------
# Reporting + indexing (both runs)
# ---------------------------------------------------------------
def report_node(state: InvoiceState) -> dict:
    report = reporter.build_report(state)
    json_path, html_path = reporter.write_report_files(report)

    repo.save_outcome(state["invoice_id"], report["validation_status"], report["recommendation"])
    repo.save_report(state["invoice_id"], report, json_path, html_path)
    if state["run_type"] == "revalidation":
        repo.mark_feedback_revalidated(state["invoice_id"])

    log.info("Invoice %s run %s -> %s", state["invoice_id"], state["validation_run"],
             report["recommendation"])
    return {"report": report}


def index_node(state: InvoiceState) -> dict:
    """Index invoice text (first run) and the latest report into Qdrant."""
    try:
        indexer.index_invoice(state["invoice_id"],
                              include_invoice_text=(state["run_type"] == "initial"))
    except Exception as error:
        # The audit record is already safe in PostgreSQL; the index can be rebuilt via /reindex.
        log.warning("RAG indexing failed for %s (rebuild later with /reindex): %s",
                    state["invoice_id"], error)
    return {}
