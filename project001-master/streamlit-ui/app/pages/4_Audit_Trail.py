"""Audit Trail - every correction, every validation run and every received file."""

from pathlib import Path

import streamlit as st

from components.ui import esc, fmt, hero, html_table, nice_field, pill, setup_page
from db import queries

setup_page("Audit Trail", "📜")
hero("Permanent record", "Audit trail",
     "Nothing here is ever overwritten: corrections are added, and every re-validation is a new run.")

if st.button("🔄 Refresh"):
    queries.clear_cache()

tab_feedback, tab_runs, tab_files = st.tabs(["✏️ Corrections", "🔁 Validation runs", "📥 Received files"])

with tab_feedback:
    feedback = queries.all_feedback()
    if not feedback:
        st.info("No corrections recorded yet.")
    else:
        rows = [[f"{f['corrected_at']:%Y-%m-%d %H:%M}", esc(f["corrected_by"]),
                 esc(f["invoice_number"] or f["vendor_name"]), esc(nice_field(f["field_name"])),
                 f'<span class="strike">{esc(f["original_value"])}</span>', f'<b>{esc(f["corrected_value"])}</b>',
                 '<span class="pill pill-approve">yes</span>' if f["revalidated"]
                 else '<span class="pill pill-review">pending</span>'] for f in feedback]
        html_table(["When", "Auditor", "Invoice", "Field", "Was", "Corrected to", "Re-validated"], rows)

with tab_runs:
    runs = queries.all_validation_runs()
    if not runs:
        st.info("No validation runs yet.")
    else:
        rows = [[f"{r['created_at']:%Y-%m-%d %H:%M}", esc(r["invoice_number"] or "—"), esc(r["vendor_name"]),
                 f"Run {r['validation_run']}", esc(r["run_type"]), esc(r["validation_status"]),
                 str(r["discrepancies"]), pill(r["recommendation"])] for r in runs]
        html_table(["When", "Invoice", "Vendor", "Run", "Type", "Status", "Issues", "Recommendation"],
                   rows, numeric_columns={6})

with tab_files:
    invoices = queries.list_invoices()
    if not invoices:
        st.info("No files received yet.")
    else:
        rows = []
        for i in invoices:
            if i["processing_status"] == "failed":
                status = f'<span class="pill pill-failed">failed</span><br><span style="color:#94a3b8">{esc(i["error_message"])}</span>'
            else:
                status = f'<span class="pill pill-info">{esc(i["processing_status"])}</span>'
            rows.append([f"{i['processed_at']:%Y-%m-%d %H:%M}", esc(Path(i["file_path"] or "").name),
                         esc((i["source_language"] or "—").upper()),
                         fmt(float(i["translation_confidence"]) * 100, 0) + "%" if i["translation_confidence"] is not None else "—",
                         status])
        html_table(["Received", "File", "Language", "Translation conf.", "Processing"], rows,
                   numeric_columns={3})
