"""
Agent service (container port 8000).

    GET  /health                          - is the agent and everything it needs reachable?
    POST /scan                            - process new files in the incoming folder right now
    POST /invoices/{invoice_id}/feedback  - store auditor corrections (INSERT only)
    POST /invoices/{invoice_id}/revalidate- recalculate + re-validate with the corrections
    POST /ask                             - natural-language question (SQL or RAG)
    GET  /index/status                    - chunks in Qdrant vs processed invoices
    POST /reindex                         - rebuild the Qdrant index from PostgreSQL

The agent is the only service that writes application data.
"""

import logging
import re
import threading
import time
from contextlib import asynccontextmanager

import redis
from fastapi import FastAPI, HTTPException
from langgraph.checkpoint.postgres import PostgresSaver
from pydantic import BaseModel, Field

from app import pipeline
from app.agents.recalculator import EDITABLE_HEADER_FIELDS, EDITABLE_LINE_FIELDS
from app.db import audit_repository as repo
from app.db.connection import checkpoint_pool, close_pools, get_connection, open_pools
from app.rag import indexer
from app.services import erp_client, vector_store
from app.services.cache import redis_client
from app.workflows.invoice_graph import build_invoice_graph
from app.workflows.rag_graph import ask_question
from app.workflows.revalidation_graph import build_revalidation_graph

log = logging.getLogger("agent")
stop_monitor = threading.Event()


# ---------------------------------------------------------------
# Startup: fail clearly if a dependency is missing
# ---------------------------------------------------------------
def wait_for(name: str, check, attempts: int = 15, delay: float = 2.0) -> None:
    for attempt in range(1, attempts + 1):
        try:
            if check() is not False:
                log.info("%s is reachable", name)
                return
        except Exception as error:
            log.info("Waiting for %s (%d/%d): %s", name, attempt, attempts, error)
        time.sleep(delay)
    raise RuntimeError(f"{name} is not reachable - refusing to start in a broken state")


def ping_postgres() -> None:
    with get_connection() as conn:
        conn.execute("SELECT 1")


def check_dependencies() -> None:
    open_pools()                                        # raises after 30s if PostgreSQL is unreachable
    wait_for("PostgreSQL", ping_postgres)
    wait_for("Redis", redis_client.ping)
    wait_for("Qdrant", lambda: vector_store.qdrant.get_collections())
    wait_for("mock-erp", erp_client.is_healthy)


@asynccontextmanager
async def lifespan(app: FastAPI):
    check_dependencies()

    checkpointer = PostgresSaver(checkpoint_pool)
    checkpointer.setup()                                # creates tables in the langgraph schema
    pipeline.GRAPHS["invoice"] = build_invoice_graph(checkpointer)
    pipeline.GRAPHS["revalidation"] = build_revalidation_graph(checkpointer)

    vector_store.ensure_collection()
    # rebuild the RAG index in the background if Qdrant lost its data
    threading.Thread(target=_safe_rebuild_if_empty, daemon=True).start()
    threading.Thread(target=pipeline.monitor_loop, args=(stop_monitor,), daemon=True).start()

    yield

    stop_monitor.set()
    close_pools()


def _safe_rebuild_if_empty():
    try:
        indexer.rebuild_if_empty()
    except Exception:
        log.exception("Automatic RAG index rebuild failed - call POST /reindex to retry")


app = FastAPI(title="AI Invoice Auditor - Agent", version="1.0", lifespan=lifespan)


# ---------------------------------------------------------------
# Request models
# ---------------------------------------------------------------
class Correction(BaseModel):
    field_name: str
    original_value: str | float | None = None
    corrected_value: str | float | None = None


class FeedbackRequest(BaseModel):
    corrected_by: str = Field(min_length=1)
    corrections: list[Correction]


class AskRequest(BaseModel):
    question: str = Field(min_length=2)
    invoice_id: str | None = None


def is_editable_field(field_name: str) -> bool:
    if field_name in EDITABLE_HEADER_FIELDS:
        return True
    match = re.fullmatch(r"line_(\d+)\.(\w+)", field_name)
    return bool(match) and match.group(2) in EDITABLE_LINE_FIELDS


# ---------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------
@app.get("/health")
def health():
    checks = {}
    try:
        ping_postgres()
        checks["postgres"] = "ok"
    except Exception as error:
        checks["postgres"] = f"error: {error}"
    try:
        redis_client.ping()
        checks["redis"] = "ok"
    except redis.RedisError as error:
        checks["redis"] = f"error: {error}"     # cache only - agent still works without it
    checks["qdrant"] = "ok" if vector_store.is_healthy() else "error"
    checks["mock_erp"] = "ok" if erp_client.is_healthy() else "error"

    critical_ok = all(checks[name] == "ok" for name in ("postgres", "qdrant", "mock_erp"))
    if not critical_ok:
        raise HTTPException(status_code=503, detail=checks)
    return {"status": "ok", "checks": checks}


@app.post("/scan")
def scan_now():
    return {"processed": pipeline.scan_incoming()}


@app.post("/invoices/{invoice_id}/feedback")
def save_feedback(invoice_id: str, request: FeedbackRequest):
    if repo.get_invoice_row(invoice_id) is None:
        raise HTTPException(status_code=404, detail="Invoice not found")

    saved = 0
    for correction in request.corrections:
        if not is_editable_field(correction.field_name):
            raise HTTPException(status_code=400,
                                detail=f"Field '{correction.field_name}' cannot be corrected")
        repo.add_feedback(invoice_id, correction.field_name, correction.original_value,
                          correction.corrected_value, request.corrected_by)
        saved += 1
    return {"saved": saved}


@app.post("/invoices/{invoice_id}/revalidate")
def revalidate(invoice_id: str):
    try:
        report = pipeline.revalidate_invoice(invoice_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return report


@app.post("/ask")
def ask(request: AskRequest):
    try:
        return ask_question(request.question, request.invoice_id)
    except Exception as error:
        # show the real reason in the UI (e.g. Bedrock model access, empty index)
        log.exception("Question failed: %s", request.question)
        raise HTTPException(status_code=502, detail=f"{type(error).__name__}: {error}")


@app.get("/index/status")
def index_status():
    try:
        return indexer.index_status()
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"{type(error).__name__}: {error}")


@app.post("/reindex")
def reindex():
    try:
        return indexer.rebuild_index()
    except Exception as error:
        log.exception("Re-indexing failed")
        raise HTTPException(status_code=502, detail=f"{type(error).__name__}: {error}")
