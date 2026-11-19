"""
Reporting Agent.

* decide_recommendation() - applies validation_policies from rules.yaml
* build_report()          - one dict with everything an auditor needs
                            (for re-validation it also holds the previous
                            discrepancies, the corrections and the recalculation table)
* write_report_files()    - JSON for machines + HTML for humans
* report_to_text()        - plain text version used for RAG indexing

The LLM (Nova Lite) writes the short executive summary.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.services.llm_client import ask_llm, persona, prompt
from app.settings import REASONING_MODEL, REPORTS_DIR, RULES

log = logging.getLogger(__name__)

TEMPLATES = Environment(
    loader=FileSystemLoader(Path(__file__).resolve().parent.parent / "templates"),
    autoescape=select_autoescape(["html"]),
)


def money(value) -> str:
    """Template filter: 1551 -> '1,551.00', None -> '—'."""
    if value is None or value == "":
        return "—"
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


TEMPLATES.filters["money"] = money

# which policy in rules.yaml decides what to do for each rule
POLICY_FOR_RULE = {
    "missing_field": "missing_field_action",
    "total_mismatch": "total_mismatch_action",
    "invalid_currency": "invalid_currency_action",
}
# "flag" in rules.yaml means: highlight it and send it to a human
ACTION_TO_RECOMMENDATION = {"flag": "manual_review", "manual_review": "manual_review", "reject": "reject"}


# ---------------------------------------------------------------
# Recommendation
# ---------------------------------------------------------------
def decide_recommendation(discrepancies: list[dict], translation_confidence: float) -> dict:
    policies = RULES["validation_policies"]
    threshold = policies["auto_approve_confidence_threshold"]
    reasons = []
    outcomes = set()

    for item in discrepancies:
        policy_name = POLICY_FOR_RULE.get(item["rule"])
        action = policies.get(policy_name, "manual_review") if policy_name else "manual_review"
        outcomes.add(ACTION_TO_RECOMMENDATION.get(action, "manual_review"))
        reasons.append(item["message"])

    if "reject" in outcomes:
        recommendation = "reject"
    elif outcomes:
        recommendation = "manual_review"
    elif translation_confidence >= threshold:
        recommendation = "approve"
        reasons.append(f"No discrepancies and translation confidence "
                       f"{translation_confidence:.2f} ≥ {threshold}.")
    else:
        recommendation = "manual_review"
        reasons.append(f"No discrepancies, but translation confidence "
                       f"{translation_confidence:.2f} is below {threshold}.")

    if not discrepancies:
        status = "passed"
    elif any(item["severity"] == "high" for item in discrepancies):
        status = "failed"
    else:
        status = "partial"

    return {"recommendation": recommendation, "validation_status": status, "reasons": reasons}


# ---------------------------------------------------------------
# Report building
# ---------------------------------------------------------------
def summarize_discrepancies(discrepancies: list[dict]) -> dict:
    by_rule = {}
    for item in discrepancies:
        by_rule[item["rule"]] = by_rule.get(item["rule"], 0) + 1
    return {
        "total": len(discrepancies),
        "high": sum(1 for d in discrepancies if d["severity"] == "high"),
        "medium": sum(1 for d in discrepancies if d["severity"] == "medium"),
        "by_rule": by_rule,
    }


def write_summary(report: dict) -> str:
    invoice = report["invoice"]
    corrections = report.get("revalidation", {}).get("corrections_applied", [])
    try:
        return ask_llm(
            REASONING_MODEL, persona("reporter"),
            prompt("report_summary",
                   invoice_no=invoice.get("invoice_no") or "(no number)",
                   vendor_name=invoice.get("vendor_name") or "(unknown vendor)",
                   po_number=invoice.get("po_number") or "(none)",
                   run_type=report["run_type"],
                   recommendation=report["recommendation"],
                   reasons="; ".join(report["recommendation_reasons"]) or "none",
                   discrepancies="; ".join(d["message"] for d in report["discrepancies"]) or "none",
                   corrections="; ".join(f"{c['field_name']}: {c['original_value']} -> {c['corrected_value']}"
                                         for c in corrections) or "none"),
            max_tokens=400,
        ).strip()
    except Exception as error:
        log.warning("Could not get LLM summary: %s", error)
        return (f"Recommendation: {report['recommendation']}. "
                f"{len(report['discrepancies'])} discrepancy(ies) found.")


def build_report(state: dict) -> dict:
    """Assemble the report dict from the workflow state."""
    invoice = state["invoice"]
    erp = state.get("erp") or {}
    discrepancies = state.get("data_discrepancies", []) + state.get("business_discrepancies", [])
    confidence = state.get("translation_confidence", 0.0)
    decision = decide_recommendation(discrepancies, confidence)
    reporting = RULES["reporting"]

    report = {
        "invoice_id": state["invoice_id"],
        "validation_run": state["validation_run"],
        "run_type": state["run_type"],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "file_path": state.get("file_path"),
        "invoice": {key: invoice.get(key) for key in (
            "invoice_no", "invoice_date", "vendor_id", "vendor_name", "po_number",
            "currency", "subtotal", "tax_rate", "tax_amount", "total_amount")},
        "line_items": invoice.get("line_items", []),
        "line_matches": state.get("line_matches", []),
        "erp": {
            "po_found": erp.get("po") is not None,
            "po": erp.get("po"),
            "vendor": erp.get("vendor"),
        },
        "missing_fields": [d["field_name"] for d in discrepancies if d["rule"] == "missing_field"],
        "discrepancies": discrepancies,
        "data_validation_notes": state.get("data_notes", ""),
        "business_validation_notes": state.get("business_notes", ""),
        "recommendation": decision["recommendation"],
        "validation_status": decision["validation_status"],
        "recommendation_reasons": decision["reasons"],
    }

    if reporting.get("include_translation_confidence", True):
        report["translation"] = {
            "source_language": state.get("source_language"),
            "confidence": confidence,
        }
    if reporting.get("include_discrepancy_summary", True):
        report["discrepancy_summary"] = summarize_discrepancies(discrepancies)

    if state["run_type"] == "revalidation":
        previous = state.get("previous_report") or {}
        report["revalidation"] = {
            "previous_run": previous.get("validation_run"),
            "previous_recommendation": previous.get("recommendation"),
            "previous_discrepancies": previous.get("discrepancies", []),
            "corrections_applied": state.get("corrections_applied", []),
            "recalculation": state.get("recalculation", {}),
        }

    report["summary"] = write_summary(report)
    return report


# ---------------------------------------------------------------
# Files
# ---------------------------------------------------------------
def write_report_files(report: dict) -> tuple[str, str]:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    name = (report["invoice"].get("invoice_no") or report["invoice_id"][:8]).replace("/", "-")
    base = REPORTS_DIR / f"{name}_run{report['validation_run']}"

    json_path = base.with_suffix(".json")
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    html_path = base.with_suffix(".html")
    html_path.write_text(TEMPLATES.get_template("report.html").render(r=report), encoding="utf-8")

    return str(json_path), str(html_path)


def report_to_text(report: dict) -> str:
    """Plain-text version of the report for the RAG index."""
    invoice = report["invoice"]
    lines = [
        f"Validation report for invoice {invoice.get('invoice_no')} "
        f"(run {report['validation_run']}, {report['run_type']}).",
        f"Vendor: {invoice.get('vendor_name')} ({invoice.get('vendor_id')}). PO: {invoice.get('po_number')}.",
        f"Date: {invoice.get('invoice_date')}. Currency: {invoice.get('currency')}. "
        f"Subtotal: {invoice.get('subtotal')}. Tax: {invoice.get('tax_amount')}. "
        f"Total: {invoice.get('total_amount')}.",
        f"Recommendation: {report['recommendation']}. Validation status: {report['validation_status']}.",
        f"Summary: {report.get('summary', '')}",
    ]
    if report.get("translation"):
        lines.append(f"Source language: {report['translation']['source_language']}, "
                     f"translation confidence: {report['translation']['confidence']}.")
    if report["discrepancies"]:
        lines.append("Discrepancies:")
        lines += [f"- {d['field_name']}: {d['message']}" for d in report["discrepancies"]]
    else:
        lines.append("No discrepancies were found.")
    if report["missing_fields"]:
        lines.append("Missing fields: " + ", ".join(report["missing_fields"]))

    revalidation = report.get("revalidation")
    if revalidation:
        lines.append(f"This is a re-validation. Previous recommendation: "
                     f"{revalidation['previous_recommendation']}.")
        for c in revalidation["corrections_applied"]:
            lines.append(f"Auditor {c['corrected_by']} corrected {c['field_name']} "
                         f"from {c['original_value']} to {c['corrected_value']}.")
        totals = revalidation["recalculation"].get("recalculated_totals", {})
        lines.append(f"Recalculated totals: subtotal {totals.get('subtotal')}, "
                     f"tax {totals.get('tax_amount')}, total {totals.get('total_amount')}.")
    return "\n".join(lines)
