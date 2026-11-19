"""Reusable UI pieces: theme, hero banner, KPI cards, status pills and tables."""

import html
from pathlib import Path

import streamlit as st

CSS_FILE = Path(__file__).with_name("theme.css")

RECOMMENDATION_LABELS = {
    "approve": ("Approve", "pill-approve"),
    "manual_review": ("Manual review", "pill-review"),
    "reject": ("Reject", "pill-reject"),
}

FIELD_LABELS = {
    "invoice_no": "Invoice number",
    "invoice_date": "Invoice date",
    "vendor_id": "Vendor ID",
    "vendor_name": "Vendor name",
    "po_number": "PO number",
    "currency": "Currency",
    "subtotal": "Subtotal",
    "tax_amount": "Tax amount",
    "total_amount": "Total amount",
    "item_code": "Item code",
    "description": "Description",
    "qty": "Quantity",
    "unit_price": "Unit price",
    "total": "Line total",
}


def setup_page(title: str, icon: str = "🧾") -> None:
    """Call first on every page: page config, CSS and the sidebar brand."""
    st.set_page_config(page_title=f"{title} · AI Invoice Auditor", page_icon=icon, layout="wide")
    st.markdown(f"<style>{CSS_FILE.read_text()}</style>", unsafe_allow_html=True)
    with st.sidebar:
        st.markdown(
            """
            <div class="brand">
              <div class="brand-logo">🧾</div>
              <div><div class="brand-name">AI Invoice Auditor</div>
                   <div class="brand-sub">Human-in-the-loop</div></div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def hero(eyebrow: str, title: str, subtitle: str) -> None:
    st.markdown(
        f"""<div class="hero"><div class="eyebrow">{html.escape(eyebrow)}</div>
        <h1>{html.escape(title)}</h1><p>{html.escape(subtitle)}</p></div>""",
        unsafe_allow_html=True,
    )


def kpi(label: str, value, hint: str = "", tone: str = "#2dd4bf") -> str:
    """Returns the HTML for one KPI card (use inside st.columns)."""
    return (f'<div class="kpi" style="--tone:{tone}"><div class="label">{html.escape(label)}</div>'
            f'<div class="value">{html.escape(str(value))}</div>'
            f'<div class="hint">{html.escape(hint)}</div></div>')


def show_kpis(cards: list[tuple]) -> None:
    columns = st.columns(len(cards))
    for column, card in zip(columns, cards):
        column.markdown(kpi(*card), unsafe_allow_html=True)


def pill(recommendation: str | None, processing_status: str | None = None) -> str:
    if processing_status == "failed":
        return '<span class="pill pill-failed">Failed</span>'
    if not recommendation:
        return '<span class="pill pill-info">Processing</span>'
    label, css = RECOMMENDATION_LABELS.get(recommendation, (recommendation, "pill-info"))
    return f'<span class="pill {css}">{label}</span>'


def severity_pill(severity: str | None) -> str:
    severity = severity or "medium"
    return f'<span class="pill pill-{html.escape(severity)}">{html.escape(severity)}</span>'


def nice_field(field_name: str) -> str:
    """'line_2.unit_price' -> 'Line 2 · Unit price'."""
    if field_name and field_name.startswith("line_") and "." in field_name:
        line, field = field_name.split(".", 1)
        return f"Line {line.replace('line_', '')} · {FIELD_LABELS.get(field, field)}"
    return FIELD_LABELS.get(field_name, field_name or "")


def fmt(value, decimals: int = 2) -> str:
    if value is None or value == "":
        return "—"
    try:
        return f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return str(value)


def html_table(headers: list[str], rows: list[list], numeric_columns: set[int] = frozenset()) -> None:
    """Render a styled table. Cell values are inserted as-is, so escape user text before passing it."""
    head = "".join(f'<th class="{"num" if i in numeric_columns else ""}">{h}</th>'
                   for i, h in enumerate(headers))
    body = ""
    for row in rows:
        cells = "".join(f'<td class="{"num" if i in numeric_columns else ""}">{cell}</td>'
                        for i, cell in enumerate(row))
        body += f"<tr>{cells}</tr>"
    st.markdown(f'<table class="pretty"><tr>{head}</tr>{body}</table>', unsafe_allow_html=True)


def esc(value) -> str:
    return "—" if value is None else html.escape(str(value))


def invoice_label(row: dict) -> str:
    number = row.get("invoice_number") or "no number"
    vendor = row.get("vendor_name") or Path(row.get("file_path") or "").name
    return f"{number} · {vendor}"
