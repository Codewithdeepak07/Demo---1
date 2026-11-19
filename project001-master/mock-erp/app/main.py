"""
Mock ERP service.

Serves vendor, purchase order and SKU master data from the `erp` schema
in PostgreSQL. Container port 8000 (published on host port 8001).

    GET /po/{po_number}     -> PO header and line items
    GET /vendor/{vendor_id} -> vendor master record
    GET /sku/{sku_code}     -> SKU master record
    GET /health             -> database reachability
"""

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from app.database import fetch_all, fetch_one, pool
from app.schemas import POLineItem, PurchaseOrder, Sku, Vendor

logging.basicConfig(stream=sys.stdout, level=logging.INFO,
                    format="%(asctime)s [mock-erp] %(levelname)s %(message)s")
log = logging.getLogger("mock-erp")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail clearly and immediately if PostgreSQL is not reachable.
    log.info("Connecting to PostgreSQL ...")
    pool.open(wait=True, timeout=30)
    fetch_one("SELECT 1 AS ok")
    log.info("PostgreSQL connection OK")
    yield
    pool.close()


app = FastAPI(title="Mock ERP", version="1.0", lifespan=lifespan)


@app.get("/health")
def health():
    try:
        fetch_one("SELECT 1 AS ok")
        return {"status": "ok", "database": "reachable"}
    except Exception as error:
        raise HTTPException(status_code=503, detail=f"database unreachable: {error}")


@app.get("/vendor/{vendor_id}", response_model=Vendor)
def get_vendor(vendor_id: str):
    row = fetch_one(
        "SELECT vendor_id, vendor_name, country, currency FROM erp.vendors WHERE vendor_id = %s",
        (vendor_id.strip().upper(),),
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"Vendor {vendor_id} not found")
    return row


@app.get("/po/{po_number}", response_model=PurchaseOrder)
def get_purchase_order(po_number: str):
    po_number = po_number.strip().upper()
    header = fetch_one(
        "SELECT po_number, vendor_id FROM erp.purchase_orders WHERE po_number = %s",
        (po_number,),
    )
    if header is None:
        raise HTTPException(status_code=404, detail=f"Purchase order {po_number} not found")

    lines = fetch_all(
        """
        SELECT line_number, item_code, description, qty, unit_price, currency
        FROM erp.po_line_items
        WHERE po_number = %s
        ORDER BY line_number
        """,
        (po_number,),
    )
    return PurchaseOrder(
        po_number=header["po_number"],
        vendor_id=header["vendor_id"],
        line_items=[POLineItem(**line) for line in lines],
    )


@app.get("/sku/{sku_code}", response_model=Sku)
def get_sku(sku_code: str):
    row = fetch_one(
        "SELECT item_code, category, uom, gst_rate FROM erp.sku_master WHERE item_code = %s",
        (sku_code.strip().upper(),),
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"SKU {sku_code} not found")
    return row
