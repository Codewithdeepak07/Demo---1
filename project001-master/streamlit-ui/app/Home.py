"""Home page - system status, inbox scan and quick navigation."""

import streamlit as st

import agent_api
from components.ui import esc, hero, html_table, invoice_label, pill, setup_page, show_kpis
from db import queries

setup_page("Home")

hero("Accounts payable · audit desk",
     "Welcome back, auditor 👋",
     "Multilingual invoices are extracted, translated and checked against the ERP automatically. "
     "You review what needs a human eye.")

# ---------------------------------------------------------------
# System status
# ---------------------------------------------------------------
health = agent_api.health()
status_col, action_col = st.columns([3, 1])
with status_col:
    checks = health.get("checks", {})
    badges = []
    for name, value in checks.items():
        ok = value == "ok"
        css = "pill-approve" if ok else "pill-reject"
        badges.append(f'<span class="pill {css}">{"●" if ok else "○"} {esc(name)}</span>')
    overall = health.get("status", "unknown")
    overall_css = "pill-approve" if overall == "ok" else "pill-reject"
    st.markdown(
        f'<div class="card"><h3>System status &nbsp;<span class="pill {overall_css}">{esc(overall)}</span></h3>'
        f'{" ".join(badges) or "No details"}</div>',
        unsafe_allow_html=True,
    )
with action_col:
    st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
    if st.button("📥 Scan inbox now", type="primary", use_container_width=True):
        with st.spinner("Processing new invoices… OCR and translation can take a moment."):
            try:
                result = agent_api.scan_inbox()
                processed = result.get("processed", [])
                queries.clear_cache()
                if processed:
                    st.toast(f"Processed {len(processed)} new file(s)", icon="✅")
                else:
                    st.toast("No new invoices in the inbox", icon="📭")
            except agent_api.AgentError as error:
                st.error(str(error))

# ---------------------------------------------------------------
# Quick numbers
# ---------------------------------------------------------------
try:
    invoices = queries.list_invoices()
except Exception as error:
    st.error(f"Could not read the audit database: {error}")
    st.stop()

needs_review = [i for i in invoices if i["recommendation"] in ("manual_review", "reject")]
show_kpis([
    ("Invoices received", len(invoices), "every detected file", "#2dd4bf"),
    ("Waiting for you", len(needs_review), "manual review or reject", "#fbbf24"),
    ("Auto-approved", sum(1 for i in invoices if i["recommendation"] == "approve"), "no discrepancies", "#4ade80"),
    ("Failed files", sum(1 for i in invoices if i["processing_status"] == "failed"), "could not be processed", "#f87171"),
])

st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)

# ---------------------------------------------------------------
# Work queue + navigation
# ---------------------------------------------------------------
left, right = st.columns([2, 1])
with left:
    st.markdown("#### 🗂️ Your work queue")
    if not needs_review:
        st.success("Nothing waiting for review. 🎉")
    else:
        rows = [[esc(invoice_label(i)), esc(i["po_number"]), pill(i["recommendation"], i["processing_status"]),
                 str(i["open_discrepancies"])] for i in needs_review[:8]]
        html_table(["Invoice", "PO", "Recommendation", "Open issues"], rows, numeric_columns={3})

with right:
    st.markdown("#### 🧭 Go to")
    st.page_link("pages/1_Dashboard.py", label="Dashboard", icon="📊")
    st.page_link("pages/2_Review_and_Correct.py", label="Review & correct invoices", icon="🔍")
    st.page_link("pages/3_Ask_the_Auditor.py", label="Ask the auditor (AI)", icon="💬")
    st.page_link("pages/4_Audit_Trail.py", label="Audit trail", icon="📜")
