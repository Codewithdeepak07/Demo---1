"""
Glue between the API, the folder monitor and the LangGraph workflows.

    process_file()       - one new invoice through the full workflow
    scan_incoming()      - one pass over the incoming folder
    monitor_loop()       - background thread polling every POLL_INTERVAL_SECONDS
    revalidate_invoice() - re-run validation with the auditor's corrections
"""

import logging
import threading
from pathlib import Path

from app.agents import monitor
from app.db import audit_repository as repo
from app.settings import POLL_INTERVAL_SECONDS

log = logging.getLogger(__name__)

# compiled graphs are set by main.py on startup (they need the checkpointer)
GRAPHS = {"invoice": None, "revalidation": None}

# one invoice at a time keeps the audit trail simple and avoids double processing
processing_lock = threading.Lock()


def process_file(file_path: Path) -> dict:
    checksum = monitor.compute_checksum(file_path)
    if repo.checksum_exists(checksum):
        return {"file": file_path.name, "status": "skipped (already audited)"}

    email_meta = monitor.load_email_meta(file_path)
    invoice_id = repo.register_invoice(str(file_path), checksum, email_meta)
    log.info("New invoice detected: %s -> %s", file_path.name, invoice_id)

    file_type = monitor.detect_file_type(file_path)
    if file_type is None:
        repo.mark_failed(invoice_id, f"Unsupported file type: {file_path.suffix}")
        return {"file": file_path.name, "invoice_id": invoice_id, "status": "failed (unsupported type)"}

    state = {
        "invoice_id": invoice_id,
        "file_path": str(file_path),
        "file_type": file_type,
        "email_meta": email_meta,
        "validation_run": 1,
        "run_type": "initial",
    }
    try:
        final_state = GRAPHS["invoice"].invoke(
            state, config={"configurable": {"thread_id": invoice_id}})
        recommendation = final_state["report"]["recommendation"]
        return {"file": file_path.name, "invoice_id": invoice_id, "status": "processed",
                "recommendation": recommendation}
    except Exception as error:
        log.exception("Processing failed for %s", file_path.name)
        repo.mark_failed(invoice_id, f"{type(error).__name__}: {error}")
        return {"file": file_path.name, "invoice_id": invoice_id, "status": f"failed: {error}"}


def scan_incoming() -> list[dict]:
    results = []
    with processing_lock:
        for file_path in monitor.list_invoice_files():
            result = process_file(file_path)
            if not result["status"].startswith("skipped"):
                results.append(result)
    return results


def monitor_loop(stop_event: threading.Event) -> None:
    log.info("Invoice monitor started - polling every %s seconds", POLL_INTERVAL_SECONDS)
    while not stop_event.is_set():
        try:
            for result in scan_incoming():
                log.info("Monitor: %s", result)
        except Exception:
            log.exception("Monitor pass failed - will try again next poll")
        stop_event.wait(POLL_INTERVAL_SECONDS)


def revalidate_invoice(invoice_id: str) -> dict:
    row = repo.get_invoice_row(invoice_id)
    if row is None:
        raise LookupError(f"Invoice {invoice_id} not found")
    if row["processing_status"] != "processed":
        raise ValueError("Only successfully processed invoices can be re-validated")

    with processing_lock:
        run_number = repo.get_latest_run_number(invoice_id) + 1
        state = {
            "invoice_id": invoice_id,
            "validation_run": run_number,
            "run_type": "revalidation",
        }
        final_state = GRAPHS["revalidation"].invoke(
            state, config={"configurable": {"thread_id": f"{invoice_id}-run{run_number}"}})
    return final_state["report"]
