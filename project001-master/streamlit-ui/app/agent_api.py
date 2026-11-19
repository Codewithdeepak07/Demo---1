"""
Calls to the agent service. Every change goes through here - the UI itself
never writes to the database. The agent is addressed by its service name.
"""

import os

import requests

AGENT_URL = os.environ.get("AGENT_URL", "http://agent:8000")


class AgentError(Exception):
    pass


def _post(path: str, payload: dict | None = None, timeout: int = 300) -> dict:
    try:
        response = requests.post(f"{AGENT_URL}{path}", json=payload or {}, timeout=timeout)
    except requests.RequestException as error:
        raise AgentError(f"Could not reach the agent service: {error}") from error
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise AgentError(str(detail))
    return response.json()


def health() -> dict:
    try:
        response = requests.get(f"{AGENT_URL}/health", timeout=5)
        body = response.json()
        return body if response.status_code == 200 else {"status": "degraded", "checks": body.get("detail", {})}
    except requests.RequestException as error:
        return {"status": "unreachable", "checks": {"agent": str(error)}}


def scan_inbox() -> dict:
    return _post("/scan")


def save_corrections(invoice_id: str, corrected_by: str, corrections: list[dict]) -> dict:
    return _post(f"/invoices/{invoice_id}/feedback",
                 {"corrected_by": corrected_by, "corrections": corrections})


def revalidate(invoice_id: str) -> dict:
    return _post(f"/invoices/{invoice_id}/revalidate")


def ask(question: str, invoice_id: str | None = None) -> dict:
    return _post("/ask", {"question": question, "invoice_id": invoice_id})


def index_status() -> dict:
    try:
        response = requests.get(f"{AGENT_URL}/index/status", timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as error:
        return {"error": str(error)}


def rebuild_index() -> dict:
    return _post("/reindex", timeout=900)
