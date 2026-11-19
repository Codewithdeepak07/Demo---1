"""
Reflection Agent - scores an answer with the RAG Triad (Nova Lite):
    answer_relevance, groundedness, context_relevance  (each 0.0 - 1.0)
"""

import logging

from app.services.llm_client import ask_llm_json, persona, prompt
from app.settings import REASONING_MODEL

log = logging.getLogger(__name__)

TRIAD = ("answer_relevance", "groundedness", "context_relevance")


def evaluate_answer(question: str, context: str, answer: str) -> dict:
    try:
        result = ask_llm_json(REASONING_MODEL, persona("rag_reflector"),
                              prompt("rag_reflect", question=question, context=context, answer=answer),
                              max_tokens=300)
        scores = {name: max(0.0, min(float(result.get(name, 0)), 1.0)) for name in TRIAD}
        comment = result.get("comment", "")
    except Exception as error:
        log.warning("Reflection failed, scoring as 0: %s", error)
        scores = {name: 0.0 for name in TRIAD}
        comment = "Reflection failed."

    scores["average"] = round(sum(scores[name] for name in TRIAD) / len(TRIAD), 3)
    scores["comment"] = comment
    return scores
