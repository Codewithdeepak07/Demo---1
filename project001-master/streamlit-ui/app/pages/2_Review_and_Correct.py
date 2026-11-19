"""
Review & Correct - the human-in-the-loop page.

1. Pick an invoice and review its discrepancies.
2. Correct extracted values (saved as NEW rows in human_feedback - nothing is overwritten).
3. Click Re-validate: the agent recalculates the invoice line by line from the start
   with the corrected values, re-checks it against the ERP and produces a new report.
"""

import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import agent_api
from components.ui import (esc, fmt, hero, html_table, invoice_label, nice_field, pill,
                           setup_page, severity_pill)
from db import queries

setup_page("Review & Correct", "🔍")
REPORTS_DIR = Path(os.environ.get("REPORTS_DIR", "/app/outputs/reports"))

HEADER_FIELDS = [            # (field name used by the agent, DB column, label)
    ("invoice_no", "invoice_number", "Invoice number"),
    ("invoice_date", "invoice_date", "Invoice date (YYYY-MM-DD)"),
    ("vendor_name", "vendor_name", "Vendor name"),
    ("po_number", "po_number", "PO number"),
    ("currency", "currency", "Currency (ISO code)"),
]
LINE_FIELDS = ["item_code", "description", "qty", "unit_price"]


# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------
def to_text(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if isinstance(value, float):
        return f"{value:g}"
    return str(value).strip()


def same_value(old, new) -> bool:
    try:
        return abs(float(old) - float(new)) < 1e-9
    except (TypeError, ValueError):
        return to_text(old) == to_text(new)


def current_values(invoice: dict, lines: list[dict], feedback: list[dict]) -> tuple[dict, list[dict]]:
    """What the auditor should see: the original extraction with the latest corrections on top."""
    header = {field: to_text(invoice.get(column)) for field, column, _ in HEADER_FIELDS}
    current_lines = [{
        "line_number": line["line_number"],
        "item_code": to_text(line["item_code"]),
        "description": to_text(line["description"]),
        "qty": float(line["quantity"]) if line["quantity"] is not None else None,
        "unit_price": float(line["unit_price"]) if line["unit_price"] is not None else None,
        "extracted_total": float(line["line_total"]) if line["line_total"] is not None else None,
    } for line in lines]

    for row in feedback:                      # oldest first, so the latest wins
        field = row["field_name"]
        if field.startswith("line_"):
            number, name = field.split(".", 1)
            for line in current_lines:
                if line["line_number"] == int(number.replace("line_", "")):
                    line[name] = float(row["corrected_value"]) if name in ("qty", "unit_price") \
                        and row["corrected_value"] not in (None, "") else row["corrected_value"]
        elif field in header:
            header[field] = to_text(row["corrected_value"])
    return header, current_lines


def show_discrepancy_table(items: list[dict]) -> None:
    if not items:
        st.success("No discrepancies. ✅")
        return
    rows = [[severity_pill(d.get("severity")), esc(nice_field(d.get("field_name"))),
             esc(d.get("invoice_value")), esc(d.get("erp_value")),
             fmt(d.get("deviation_pct")) if d.get("deviation_pct") is not None else "—",
             esc(d.get("message"))] for d in items]
    html_table(["Severity", "Field", "Invoice", "ERP / expected", "Dev. %", "Details"], rows,
               numeric_columns={4})


def show_revalidation(report: dict) -> None:
    """Before/after view of a re-validation report."""
    reval = report.get("revalidation") or {}
    recalc = reval.get("recalculation") or {}

    st.markdown(
        f'<div class="card"><h3>Result of re-validation (run {report["validation_run"]})</h3>'
        f'<div class="change">{pill(reval.get("previous_recommendation"))}'
        f'<span class="arrow">➜</span>{pill(report["recommendation"])}'
        f'<span style="color:#94a3b8">· {len(reval.get("previous_discrepancies", []))} issue(s) before, '
        f'{len(report["discrepancies"])} now</span></div>'
        f'<p style="margin-top:12px;color:#cbd5e1">{esc(report.get("summary"))}</p></div>',
        unsafe_allow_html=True,
    )

    st.markdown("##### ✏️ Corrections applied")
    corrections = reval.get("corrections_applied", [])
    if corrections:
        html_table(["Field", "Original", "Corrected", "By"],
                   [[esc(nice_field(c["field_name"])), f'<span class="strike">{esc(c["original_value"])}</span>',
                     f'<b>{esc(c["corrected_value"])}</b>', esc(c["corrected_by"])] for c in corrections])
    else:
        st.caption("No corrections - validation was simply re-run.")

    st.markdown("##### 🧮 Line-by-line recalculation vs ERP")
    rows = []
    for line in recalc.get("lines", []):
        status_css = "up" if line["status"] == "matches ERP" else "down"
        rows.append([
            str(line["line_number"]),
            f'{esc(line.get("item_code") or line.get("erp_item_code"))}<br>'
            f'<span style="color:#64748b">{esc(line.get("description"))}</span>',
            f'{fmt(line["qty"], 0)} / {fmt(line["erp_qty"], 0)}',
            f'{fmt(line["unit_price"])} / {fmt(line["erp_unit_price"])}',
            f'<span class="strike">{fmt(line["original_line_total"])}</span>',
            f'<b>{fmt(line["recalculated_line_total"])}</b>',
            fmt(line["erp_line_total"]),
            f'{fmt(line["gst_rate"], 0)}%',
            f'{fmt(line["recalculated_tax"])} / {fmt(line["erp_tax"])}',
            f'<span class="{status_css}">{esc(line["status"])}</span>',
        ])
    html_table(["#", "Item", "Qty inv/ERP", "Price inv/ERP", "Original", "Recalculated", "ERP total",
                "GST", "Tax recalc/ERP", "Status"], rows, numeric_columns={2, 3, 4, 5, 6, 7, 8})

    original, new, erp = (recalc.get("original_totals", {}), recalc.get("recalculated_totals", {}),
                          recalc.get("erp_totals", {}))
    st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
    html_table(["", "Original invoice", "Recalculated", "As per ERP"],
               [[label, fmt(original.get(key)), f"<b>{fmt(new.get(key))}</b>", fmt(erp.get(key))]
                for label, key in (("Subtotal", "subtotal"), ("Tax", "tax_amount"), ("Total", "total_amount"))],
               numeric_columns={1, 2, 3})

    before, after = st.columns(2)
    with before:
        st.markdown(f"##### 🕑 Discrepancies before (run {reval.get('previous_run')})")
        show_discrepancy_table(reval.get("previous_discrepancies", []))
    with after:
        st.markdown(f"##### ✅ Discrepancies now (run {report['validation_run']})")
        show_discrepancy_table(report["discrepancies"])


# ---------------------------------------------------------------
# Pick an invoice
# ---------------------------------------------------------------
hero("Human in the loop", "Review & correct",
     "Fix what the machine misread, then re-validate. Your corrections are stored as a permanent audit record.")

invoices = queries.list_invoices()
if not invoices:
    st.info("No invoices have been processed yet.")
    st.stop()

options = {i["invoice_id"]: i for i in invoices}
selected_id = st.selectbox(
    "Invoice",
    list(options),
    format_func=lambda key: f'{invoice_label(options[key])}  —  '
                            f'{(options[key]["recommendation"] or options[key]["processing_status"]).replace("_", " ")}',
)
invoice = queries.get_invoice(selected_id)

if invoice["processing_status"] == "failed":
    st.error(f"This file could not be processed: {invoice['error_message']}")
    st.caption(f"File: {invoice['file_path']}")
    st.stop()
if invoice["processing_status"] != "processed":
    st.info("This invoice is still being processed. Refresh in a moment.")
    st.stop()

lines = queries.get_line_items(selected_id)
feedback = queries.get_feedback(selected_id)
reports = queries.get_reports(selected_id)
latest_report = reports[-1]["report_json"] if reports else {}
latest_run = reports[-1]["validation_run"] if reports else 1

# ---------------------------------------------------------------
# Header card
# ---------------------------------------------------------------
confidence = float(invoice["translation_confidence"] or 0)
info = st.columns([1.2, 1, 1, 1, 1.2])
cells = [
    ("Vendor", invoice["vendor_name"]), ("Invoice", invoice["invoice_number"]),
    ("PO", invoice["po_number"]), ("Date", invoice["invoice_date"]),
    ("Total", f'{invoice["currency"] or ""} {fmt(invoice["total_amount"])}'),
]
for column, (label, value) in zip(info, cells):
    column.markdown(f'<div class="field-label">{label}</div><div class="field-value">{esc(value)}</div>',
                    unsafe_allow_html=True)

badge_col, conf_col = st.columns([1, 2])
badge_col.markdown(f'{pill(invoice["recommendation"])} &nbsp; '
                   f'<span class="pill pill-info">run {latest_run}</span> &nbsp; '
                   f'<span class="pill pill-info">{esc((invoice["source_language"] or "?").upper())}</span>',
                   unsafe_allow_html=True)
conf_col.progress(min(confidence, 1.0), text=f"Translation confidence {confidence * 100:.0f}%")

tab_overview, tab_correct, tab_docs, tab_reports = st.tabs(
    ["🧾 Overview", "✏️ Correct & re-validate", "📄 Extracted text", "📑 Reports"])

# ---------------------------------------------------------------
# Overview
# ---------------------------------------------------------------
with tab_overview:
    if latest_report.get("summary"):
        st.markdown(f'<div class="card"><h3>🤖 Audit summary</h3>{esc(latest_report["summary"])}</div>',
                    unsafe_allow_html=True)

    st.markdown(f"#### Discrepancies (run {latest_run})")
    current_issues = queries.get_discrepancies(selected_id, latest_run)
    show_discrepancy_table(current_issues)

    flagged_lines = {d["line_number"] for d in current_issues if d["line_number"] is not None}
    st.markdown("#### Line items as extracted")
    html_table(["#", "Item code", "Description", "Qty", "Unit price", "Line total", ""],
               [[str(l["line_number"]), esc(l["item_code"]), esc(l["description"]), fmt(l["quantity"], 0),
                 fmt(l["unit_price"]), fmt(l["line_total"]),
                 "⚠️" if l["line_number"] in flagged_lines else "✓"] for l in lines],
               numeric_columns={3, 4, 5})

    if latest_report.get("revalidation"):
        st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
        with st.expander("Last re-validation details", expanded=False):
            show_revalidation(latest_report)

# ---------------------------------------------------------------
# Correct & re-validate
# ---------------------------------------------------------------
with tab_correct:
    header, current_lines = current_values(invoice, lines, feedback)

    st.markdown("Edit any value that was read wrongly. Totals are **not** editable - "
                "they are recalculated line by line when you re-validate.")

    with st.form("corrections_form"):
        st.markdown("##### Invoice header")
        header_cols = st.columns(len(HEADER_FIELDS))
        edited_header = {}
        for column, (field, _, label) in zip(header_cols, HEADER_FIELDS):
            edited_header[field] = column.text_input(label, value=header[field], key=f"h_{selected_id}_{field}")

        st.markdown("##### Line items")
        edited_lines = st.data_editor(
            pd.DataFrame(current_lines),
            key=f"lines_{selected_id}",
            hide_index=True,
            use_container_width=True,
            num_rows="fixed",
            disabled=["line_number", "extracted_total"],
            column_config={
                "line_number": st.column_config.NumberColumn("#", width="small"),
                "item_code": st.column_config.TextColumn("Item code"),
                "description": st.column_config.TextColumn("Description", width="large"),
                "qty": st.column_config.NumberColumn("Quantity", format="%.2f"),
                "unit_price": st.column_config.NumberColumn("Unit price", format="%.4f"),
                "extracted_total": st.column_config.NumberColumn("Extracted total", format="%.2f"),
            },
        )
        corrected_by = st.text_input("Your name", value=st.session_state.get("auditor_name", ""),
                                     placeholder="e.g. Priya (AP team)")
        submitted = st.form_submit_button("💾 Save corrections", use_container_width=True)

    if submitted:
        corrections = []
        for field, _, _ in HEADER_FIELDS:
            if not same_value(header[field], edited_header[field]):
                corrections.append({"field_name": field, "original_value": header[field] or None,
                                     "corrected_value": edited_header[field].strip() or None})
        for before, after in zip(current_lines, edited_lines.to_dict("records")):
            for field in LINE_FIELDS:
                if not same_value(before[field], after[field]):
                    corrections.append({
                        "field_name": f"line_{before['line_number']}.{field}",
                        "original_value": to_text(before[field]) or None,
                        "corrected_value": to_text(after[field]) or None,
                    })

        if not corrected_by.strip():
            st.warning("Please enter your name so the correction can be attributed.")
        elif not corrections:
            st.info("Nothing changed - no corrections to save.")
        else:
            try:
                agent_api.save_corrections(selected_id, corrected_by.strip(), corrections)
                st.session_state["auditor_name"] = corrected_by.strip()
                queries.clear_cache()
                feedback = queries.get_feedback(selected_id)
                st.success(f"Saved {len(corrections)} correction(s). Click **Re-validate** to recalculate.")
            except agent_api.AgentError as error:
                st.error(f"Could not save: {error}")

    pending = [f for f in feedback if not f["revalidated"]]
    st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
    action_col, info_col = st.columns([1, 2])
    with info_col:
        if pending:
            st.markdown(f'<span class="pill pill-review">{len(pending)} correction(s) waiting for re-validation</span>',
                        unsafe_allow_html=True)
            st.caption(", ".join(nice_field(f["field_name"]) for f in pending))
        else:
            st.caption("No pending corrections. You can still re-run validation against the latest ERP data.")

    with action_col:
        run_now = st.button("🔁 Re-validate", type="primary", use_container_width=True)

    if run_now:
        with st.spinner("Recalculating every line and re-checking against the ERP…"):
            try:
                new_report = agent_api.revalidate(selected_id)
                queries.clear_cache()
                st.toast(f"Re-validation done: {new_report['recommendation'].replace('_', ' ')}", icon="✅")
                show_revalidation(new_report)
            except agent_api.AgentError as error:
                st.error(f"Re-validation failed: {error}")
    elif latest_report.get("revalidation"):
        st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
        show_revalidation(latest_report)

# ---------------------------------------------------------------
# Extracted text
# ---------------------------------------------------------------
with tab_docs:
    st.caption(f"File: `{invoice['file_path']}` · extraction method: **{invoice['extraction_method']}**")
    raw_col, translated_col = st.columns(2)
    with raw_col:
        st.markdown(f"##### Original text ({(invoice['source_language'] or '?').upper()})")
        st.code(invoice["raw_extracted_text"] or "", language=None)
    with translated_col:
        st.markdown("##### English translation")
        st.code(invoice["translated_text"] or "", language=None)

    email_meta = (invoice.get("extracted_raw") or {}).get("email_meta")
    if email_meta:
        with st.expander("📧 Email header"):
            st.json(email_meta)
    extras = (invoice.get("extracted_raw") or {}).get("extras")
    if extras:
        with st.expander("🧩 Other fields found on the invoice"):
            st.json(extras)

# ---------------------------------------------------------------
# Reports
# ---------------------------------------------------------------
with tab_reports:
    if not reports:
        st.info("No reports yet.")
    else:
        runs = {r["validation_run"]: r for r in reports}
        run = st.radio("Validation run", list(runs), index=len(runs) - 1, horizontal=True,
                       format_func=lambda n: f"Run {n} · {runs[n]['run_type']}")
        chosen = runs[run]
        st.markdown(f'{pill(chosen["recommendation"])} &nbsp; status **{chosen["validation_status"]}** '
                    f'· created {chosen["created_at"]:%Y-%m-%d %H:%M}', unsafe_allow_html=True)

        html_file = REPORTS_DIR / Path(chosen["html_path"] or "").name
        download_col, json_col = st.columns(2)
        json_col.download_button("⬇️ Download JSON report",
                                 data=json.dumps(chosen["report_json"], indent=2, default=str),
                                 file_name=f"report_run{run}.json", mime="application/json",
                                 use_container_width=True)
        if html_file.is_file():
            html_text = html_file.read_text(encoding="utf-8")
            download_col.download_button("⬇️ Download HTML report", data=html_text,
                                         file_name=html_file.name, mime="text/html",
                                         use_container_width=True)
            components.html(html_text, height=900, scrolling=True)
        else:
            download_col.caption("HTML file not found on the reports volume.")
