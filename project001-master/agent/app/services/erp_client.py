"""
HTTP client for the mock ERP service.

This is the agent's ONLY route to ERP data. We call mock-erp by its
service name on its container port (mock-erp:8000), never the database.
"""

import requests

from app.settings import ERP_BASE_URL

TIMEOUT_SECONDS = 10


def _get(path: str) -> dict | None:
    """Return the JSON body, or None when the ERP says 404 (record not found)."""
    response = requests.get(f"{ERP_BASE_URL}{path}", timeout=TIMEOUT_SECONDS)
    if response.status_code == 404:
        return None
    response.raise_for_status()   # anything else is a real failure - let it surface
    return response.json()


def get_purchase_order(po_number: str) -> dict | None:
    return _get(f"/po/{po_number}")


def get_vendor(vendor_id: str) -> dict | None:
    return _get(f"/vendor/{vendor_id}")


def get_sku(item_code: str) -> dict | None:
    return _get(f"/sku/{item_code}")


def is_healthy() -> bool:
    try:
        return requests.get(f"{ERP_BASE_URL}/health", timeout=3).status_code == 200
    except requests.RequestException:
        return False
