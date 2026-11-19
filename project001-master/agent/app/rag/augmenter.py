"""
Augmentation Agent - rerank the retrieved chunks and assemble the context.

Rerank score = vector similarity + a small bonus for words shared with the question.
Questions about "why" an invoice was flagged get a small bonus for report chunks,
because the report (not the invoice) explains the decision.
"""

import re

KEEP_TOP = 5
WHY_WORDS = {"why", "flag", "flagged", "reject", "rejected", "review", "discrepancy", "mismatch"}


def _words(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9\-]+", text.lower()) if len(word) > 2}


def rerank(question: str, chunks: list[dict], keep: int = KEEP_TOP) -> list[dict]:
    question_words = _words(question)
    asks_why = bool(question_words & WHY_WORDS)

    for chunk in chunks:
        overlap = len(question_words & _words(chunk["chunk_text"]))
        keyword_bonus = 0.05 * min(overlap, 6)
        report_bonus = 0.1 if asks_why and chunk["source_type"] == "report" else 0.0
        chunk["rerank_score"] = round(chunk["score"] + keyword_bonus + report_bonus, 4)

    return sorted(chunks, key=lambda c: c["rerank_score"], reverse=True)[:keep]


def build_context(chunks: list[dict]) -> str:
    parts = []
    for number, chunk in enumerate(chunks, start=1):
        parts.append(f"[{number}] ({chunk['source_type']} | vendor: {chunk['vendor']} | "
                     f"invoice_id: {chunk['invoice_id']})\n{chunk['chunk_text']}")
    return "\n\n".join(parts)
