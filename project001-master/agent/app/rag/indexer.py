"""
Indexing Agent.

Reads text that is ALREADY stored in PostgreSQL (never the original files),
chunks it, embeds it and writes it to Qdrant. Because of that, losing the
Qdrant volume is only a rebuild - no OCR or translation is repeated.
"""

import logging

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.agents.reporter import report_to_text
from app.db import audit_repository as repo
from app.services import vector_store
from app.services.llm_client import embed_text

log = logging.getLogger(__name__)

splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=120)


def index_document(invoice_id: str, source_type: str, text: str, vendor: str, language: str) -> int:
    chunks = splitter.split_text(text)
    vectors = [embed_text(chunk) for chunk in chunks]
    vector_store.delete_chunks(invoice_id, source_type)
    vector_store.upsert_chunks(invoice_id, source_type, chunks, vectors, vendor, language)
    return len(chunks)


def index_invoice(invoice_id: str, include_invoice_text: bool = True) -> None:
    row = repo.get_invoice_row(invoice_id)
    if row is None:
        return
    vendor = row["vendor_name"] or "unknown"
    language = row["source_language"] or "unknown"

    if include_invoice_text:
        # a short header line helps retrieval find the right invoice
        header = (f"Invoice {row['invoice_number'] or '(no number)'} from {vendor}, "
                  f"PO {row['po_number'] or '(none)'}, file {row['file_path']}.\n")
        text = header + (row["translated_text"] or row["raw_extracted_text"] or "")
        count = index_document(invoice_id, "invoice", text, vendor, language)
        log.info("Indexed %d invoice chunks for %s", count, invoice_id)

    report = repo.get_latest_report(invoice_id)
    if report:
        count = index_document(invoice_id, "report", report_to_text(report), vendor, language)
        log.info("Indexed %d report chunks for %s", count, invoice_id)


def rebuild_index() -> dict:
    """Drop and rebuild the whole collection from PostgreSQL (no OCR, no translation)."""
    vector_store.recreate_collection()
    invoices = repo.list_processed_invoices()
    indexed, failed = 0, []
    for row in invoices:
        invoice_id = str(row["invoice_id"])
        try:
            index_invoice(invoice_id)
            indexed += 1
        except Exception as error:
            log.exception("Indexing failed for %s", invoice_id)
            failed.append({"invoice_id": invoice_id, "error": f"{type(error).__name__}: {error}"})
    log.info("Rebuilt RAG index: %d indexed, %d failed", indexed, len(failed))
    return {"invoices_indexed": indexed, "failed": failed, "chunks": vector_store.count_points()}


def index_status() -> dict:
    vector_store.ensure_collection()
    return {
        "chunks_in_qdrant": vector_store.count_points(),
        "processed_invoices": len(repo.list_processed_invoices()),
    }


def rebuild_if_empty() -> None:
    """On startup: if Qdrant lost its data but PostgreSQL still has invoices, rebuild."""
    vector_store.ensure_collection()
    if vector_store.count_points() == 0 and repo.list_processed_invoices():
        log.info("Qdrant collection is empty but PostgreSQL has invoices - rebuilding index")
        rebuild_index()
