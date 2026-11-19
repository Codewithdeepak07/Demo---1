"""Dashboard - KPIs, charts and the full invoice list."""

import altair as alt
import pandas as pd
import streamlit as st

from components.ui import (RECOMMENDATION_LABELS, esc, fmt, hero, html_table, invoice_label,
                           pill, setup_page, show_kpis)
from db import queries

setup_page("Dashboard", "📊")
hero("Overview", "Audit dashboard", "Where every invoice stands right now, and what is going wrong most often.")

if st.button("🔄 Refresh"):
    queries.clear_cache()

invoices = queries.list_invoices()
if not invoices:
    st.info("No invoices yet. Drop files into the incoming folder or press **Scan inbox now** on Home.")
    st.stop()

discrepancies = queries.latest_discrepancies_all()
feedback = queries.all_feedback()

processed = [i for i in invoices if i["processing_status"] == "processed"]
confidences = [float(i["translation_confidence"]) for i in processed if i["translation_confidence"] is not None]
avg_conf = f"{sum(confidences) / len(confidences) * 100:.0f}%" if confidences else "—"

show_kpis([
    ("Processed", len(processed), f"of {len(invoices)} received", "#2dd4bf"),
    ("Approved", sum(1 for i in processed if i["recommendation"] == "approve"), "ready to pay", "#4ade80"),
    ("Manual review", sum(1 for i in processed if i["recommendation"] == "manual_review"), "needs an auditor", "#fbbf24"),
    ("Rejected", sum(1 for i in processed if i["recommendation"] == "reject"), "policy violation", "#f87171"),
    ("Avg. translation", avg_conf, "confidence", "#818cf8"),
    ("Corrections", len(feedback), "made by auditors", "#38bdf8"),
])

st.markdown("<div style='height:22px'></div>", unsafe_allow_html=True)

# ---------------------------------------------------------------
# Charts
# ---------------------------------------------------------------
COLORS = {"Approve": "#4ade80", "Manual review": "#fbbf24", "Reject": "#f87171", "Failed": "#94a3b8"}


def styled(chart):
    return (chart.configure_view(strokeWidth=0)
                 .configure_axis(labelColor="#94a3b8", titleColor="#94a3b8", gridColor="#1e293b",
                                 domainColor="#334155")
                 .configure_legend(labelColor="#cbd5e1", titleColor="#94a3b8")
                 .properties(background="transparent"))


status_counts = {}
for invoice in invoices:
    if invoice["processing_status"] == "failed":
        label = "Failed"
    else:
        label = RECOMMENDATION_LABELS.get(invoice["recommendation"], ("Processing",))[0]
    status_counts[label] = status_counts.get(label, 0) + 1
status_df = pd.DataFrame({"Outcome": list(status_counts), "Invoices": list(status_counts.values())})

col_a, col_b = st.columns(2)
with col_a:
    st.markdown("#### Recommendations")
    donut = alt.Chart(status_df).mark_arc(innerRadius=70, cornerRadius=6, padAngle=0.02).encode(
        theta="Invoices:Q",
        color=alt.Color("Outcome:N",
                        scale=alt.Scale(domain=list(COLORS), range=list(COLORS.values())),
                        legend=alt.Legend(orient="bottom")),
        tooltip=["Outcome", "Invoices"],
    ).properties(height=300)
    st.altair_chart(styled(donut), use_container_width=True)

with col_b:
    st.markdown("#### Most common discrepancies")
    if discrepancies:
        rule_df = (pd.DataFrame(discrepancies)["rule"].str.replace("_", " ")
                   .value_counts().rename_axis("Rule").reset_index(name="Count"))
        bars = alt.Chart(rule_df).mark_bar(cornerRadiusEnd=6, color="#2dd4bf").encode(
            x=alt.X("Count:Q", title=None),
            y=alt.Y("Rule:N", sort="-x", title=None),
            tooltip=["Rule", "Count"],
        ).properties(height=300)
        st.altair_chart(styled(bars), use_container_width=True)
    else:
        st.success("No open discrepancies 🎉")

if discrepancies:
    st.markdown("#### Discrepancies by vendor")
    vendor_df = pd.DataFrame(discrepancies).fillna({"vendor_name": "Unknown vendor"})
    vendor_df = vendor_df.groupby(["vendor_name", "severity"]).size().reset_index(name="Count")
    stacked = alt.Chart(vendor_df).mark_bar(cornerRadiusEnd=4).encode(
        x=alt.X("Count:Q", title=None),
        y=alt.Y("vendor_name:N", title=None, sort="-x"),
        color=alt.Color("severity:N", scale=alt.Scale(domain=["high", "medium"], range=["#f87171", "#fbbf24"]),
                        legend=alt.Legend(orient="bottom", title="Severity")),
        tooltip=["vendor_name", "severity", "Count"],
    ).properties(height=max(160, 40 * vendor_df["vendor_name"].nunique()))
    st.altair_chart(styled(stacked), use_container_width=True)

# ---------------------------------------------------------------
# Invoice list
# ---------------------------------------------------------------
st.markdown("#### All invoices")
filter_col, search_col = st.columns([1, 2])
choice = filter_col.selectbox("Show", ["All", "Approve", "Manual review", "Reject", "Failed"])
search = search_col.text_input("Search vendor, invoice or PO", placeholder="e.g. HafenLogistik or PO-1004")

rows = []
for invoice in invoices:
    label = "Failed" if invoice["processing_status"] == "failed" else \
        RECOMMENDATION_LABELS.get(invoice["recommendation"], ("Processing",))[0]
    if choice != "All" and label != choice:
        continue
    haystack = f"{invoice['vendor_name']} {invoice['invoice_number']} {invoice['po_number']} {invoice['file_path']}".lower()
    if search and search.lower() not in haystack:
        continue
    rows.append([
        esc(invoice_label(invoice)),
        esc(invoice["po_number"]),
        esc(invoice["invoice_date"]),
        f"{esc(invoice['currency'])} {fmt(invoice['total_amount'])}",
        esc((invoice["source_language"] or "—").upper()),
        str(invoice["open_discrepancies"]),
        pill(invoice["recommendation"], invoice["processing_status"]),
    ])

if rows:
    html_table(["Invoice", "PO", "Date", "Total", "Lang", "Issues", "Recommendation"], rows,
               numeric_columns={3, 5})
else:
    st.caption("No invoices match this filter.")
