"""
Read-only queries on the `audit` schema.

The UI never writes to the database: every change (corrections, re-validation)
goes through the agent API. The session is also forced read-only at the
PostgreSQL level, and the `erp` schema is never touched (ERP data lives behind mock-erp).
"""

import os

import psycopg
import streamlit as st
from psycopg.rows import dict_row

CACHE_SECONDS = 5


def _connect():
    return psycopg.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        options="-c default_transaction_read_only=on",
        row_factory=dict_row,
        autocommit=True,
        connect_timeout=5,
    )


def _fetch_all(sql: str, params: tuple = ()) -> list[dict]:
    with _connect() as conn:
        return conn.execute(sql, params).fetchall()


def _fetch_one(sql: str, params: tuple = ()) -> dict | None:
    with _connect() as conn:
        return conn.execute(sql, params).fetchone()


# ---------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------
@st.cache_data(ttl=CACHE_SECONDS)
def list_invoices() -> list[dict]:
    return _fetch_all(
        """
        SELECT i.invoice_id::text AS invoice_id, i.file_path, i.invoice_number, i.vendor_name,
               i.po_number, i.invoice_date, i.currency, i.total_amount, i.source_language,
               i.translation_confidence, i.validation_status, i.recommendation,
               i.processing_status, i.error_message, i.processed_at,
               COALESCE(r.latest_run, 0) AS latest_run,
               (SELECT COUNT(*) FROM audit.discrepancies d
                 WHERE d.invoice_id = i.invoice_id AND d.validation_run = r.latest_run) AS open_discrepancies
        FROM audit.invoice_audit i
        LEFT JOIN (SELECT invoice_id, MAX(validation_run) AS latest_run
                   FROM audit.validation_reports GROUP BY invoice_id) r
               ON r.invoice_id = i.invoice_id
        ORDER BY i.processed_at DESC
        """
    )


@st.cache_data(ttl=CACHE_SECONDS)
def get_invoice(invoice_id: str) -> dict | None:
    return _fetch_one(
        "SELECT *, invoice_id::text AS invoice_id FROM audit.invoice_audit WHERE invoice_id = %s",
        (invoice_id,),
    )


@st.cache_data(ttl=CACHE_SECONDS)
def get_line_items(invoice_id: str) -> list[dict]:
    return _fetch_all(
        """
        SELECT line_number, item_code, description, quantity, unit_price, line_total
        FROM audit.invoice_line_items WHERE invoice_id = %s ORDER BY line_number
        """,
        (invoice_id,),
    )


@st.cache_data(ttl=CACHE_SECONDS)
def get_discrepancies(invoice_id: str, validation_run: int) -> list[dict]:
    return _fetch_all(
        """
        SELECT check_type, rule, field_name, line_number, invoice_value, erp_value,
               deviation_pct, severity, message, detected_at
        FROM audit.discrepancies
        WHERE invoice_id = %s AND validation_run = %s
        ORDER BY id
        """,
        (invoice_id, validation_run),
    )


@st.cache_data(ttl=CACHE_SECONDS)
def get_feedback(invoice_id: str) -> list[dict]:
    return _fetch_all(
        """
        SELECT field_name, original_value, corrected_value, corrected_by, corrected_at, revalidated
        FROM audit.human_feedback WHERE invoice_id = %s ORDER BY corrected_at, id
        """,
        (invoice_id,),
    )


@st.cache_data(ttl=CACHE_SECONDS)
def get_reports(invoice_id: str) -> list[dict]:
    return _fetch_all(
        """
        SELECT validation_run, run_type, validation_status, recommendation,
               report_json, json_path, html_path, created_at
        FROM audit.validation_reports WHERE invoice_id = %s ORDER BY validation_run
        """,
        (invoice_id,),
    )


# ---------------------------------------------------------------
# Dashboard / audit trail
# ---------------------------------------------------------------
@st.cache_data(ttl=CACHE_SECONDS)
def latest_discrepancies_all() -> list[dict]:
    """Discrepancies of the latest validation run of every invoice."""
    return _fetch_all(
        """
        SELECT d.rule, d.severity, d.field_name, i.vendor_name, i.invoice_number
        FROM audit.discrepancies d
        JOIN audit.invoice_audit i ON i.invoice_id = d.invoice_id
        WHERE d.validation_run = (SELECT MAX(validation_run) FROM audit.discrepancies x
                                  WHERE x.invoice_id = d.invoice_id)
        """
    )


@st.cache_data(ttl=CACHE_SECONDS)
def all_feedback() -> list[dict]:
    return _fetch_all(
        """
        SELECT f.corrected_at, f.corrected_by, i.invoice_number, i.vendor_name, f.field_name,
               f.original_value, f.corrected_value, f.revalidated
        FROM audit.human_feedback f
        JOIN audit.invoice_audit i ON i.invoice_id = f.invoice_id
        ORDER BY f.corrected_at DESC
        """
    )


@st.cache_data(ttl=CACHE_SECONDS)
def all_validation_runs() -> list[dict]:
    return _fetch_all(
        """
        SELECT r.created_at, i.invoice_number, i.vendor_name, r.validation_run, r.run_type,
               r.validation_status, r.recommendation,
               (SELECT COUNT(*) FROM audit.discrepancies d
                 WHERE d.invoice_id = r.invoice_id AND d.validation_run = r.validation_run) AS discrepancies
        FROM audit.validation_reports r
        JOIN audit.invoice_audit i ON i.invoice_id = r.invoice_id
        ORDER BY r.created_at DESC
        """
    )


def clear_cache() -> None:
    st.cache_data.clear()
