"""
Structured questions ("how many invoices were rejected?") are answered straight
from PostgreSQL with SQL - no vector search needed.

Safety rules for the generated SQL:
  * a single SELECT (or WITH ... SELECT) statement
  * only the audit schema - never erp (service boundary) or langgraph
  * executed in a READ ONLY transaction with a timeout and a row limit
"""

import logging
import re

from app.db.audit_repository import run_read_only_query
from app.services.llm_client import ask_llm, ask_llm_json, persona, prompt
from app.settings import RAG_MODEL, REASONING_MODEL

log = logging.getLogger(__name__)

FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|call|do)\b|"
    r"\berp\.|\blanggraph\.|pg_catalog|information_schema",
    re.IGNORECASE,
)


def route_question(question: str) -> str:
    try:
        result = ask_llm_json(REASONING_MODEL, persona("rag_router"),
                              prompt("rag_route", question=question), max_tokens=50)
        return "sql" if result.get("route") == "sql" else "rag"
    except Exception as error:
        log.warning("Routing failed, using RAG: %s", error)
        return "rag"


def is_safe_sql(sql: str) -> bool:
    cleaned = sql.strip().rstrip(";").strip()
    if ";" in cleaned:
        return False
    if not re.match(r"^(select|with)\b", cleaned, re.IGNORECASE):
        return False
    if FORBIDDEN.search(cleaned):
        return False
    return "audit." in cleaned.lower()


def answer_with_sql(question: str, invoice_id: str | None = None) -> dict | None:
    """Returns {"answer", "sql", "rows"} or None if SQL could not answer (caller falls back to RAG)."""
    invoice_filter = ""
    if invoice_id:
        invoice_filter = f"Only consider invoice_id = '{invoice_id}'."

    try:
        generated = ask_llm_json(REASONING_MODEL, persona("sql_writer"),
                                 prompt("sql_generate", question=question, invoice_filter=invoice_filter),
                                 max_tokens=400)
        sql = str(generated.get("sql", "")).strip().rstrip(";")
        if not is_safe_sql(sql):
            log.warning("Rejected unsafe or invalid SQL: %s", sql)
            return None

        rows = run_read_only_query(sql)
        answer = ask_llm(RAG_MODEL, persona("rag_generator"),
                         prompt("sql_answer", question=question, sql=sql, rows=rows),
                         max_tokens=400).strip()
        return {"answer": answer, "sql": sql, "rows": rows}
    except Exception as error:
        log.warning("SQL answer failed, falling back to RAG: %s", error)
        return None
