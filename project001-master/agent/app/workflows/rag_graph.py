"""
RAG question-answering workflow (LangGraph).

    START -> route_question ──"sql"──> sql_answer ──(answered)──────────────> END
                 │                 └──(SQL could not answer)──┐
                 └──"rag"──────────────────────────────────> retrieve
    retrieve -> augment -> generate -> reflect
    reflect ──(average triad score > 0.7, or retries used up)──> finish -> END
            └──(score too low, retries left)──> retrieve (wider search)

The same question asked again is answered straight from Redis.
"""

import logging
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.rag import augmenter, generator, reflector, retriever, sql_answerer
from app.services.cache import cache_get, cache_set, make_key
from app.settings import ANSWER_CACHE_TTL_SECONDS, RAG_MAX_RETRIES, RAG_PASS_SCORE

log = logging.getLogger(__name__)

FIRST_TOP_K = 8
TOP_K_STEP = 6     # each retry searches wider


class RagState(TypedDict, total=False):
    question: str
    invoice_id: str | None
    route: str
    attempt: int
    top_k: int
    chunks: list
    context: str
    answer: str
    scores: dict
    best: dict
    attempts_log: list
    result: dict


# ---------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------
def route_node(state: RagState) -> dict:
    return {"route": sql_answerer.route_question(state["question"]),
            "attempt": 0, "top_k": FIRST_TOP_K, "attempts_log": []}


def sql_answer_node(state: RagState) -> dict:
    answer = sql_answerer.answer_with_sql(state["question"], state.get("invoice_id"))
    if answer is None:
        return {"route": "rag"}          # fall back to vector search
    return {"result": {
        "answer": answer["answer"],
        "answered_by": "sql",
        "sql": answer["sql"],
        "rows": answer["rows"][:20],
        "sources": [],
        "scores": None,
        "passed": True,
        "attempts": 0,
    }}


def retrieve_node(state: RagState) -> dict:
    chunks = retriever.retrieve_chunks(state["question"], state["top_k"], state.get("invoice_id"))
    return {"chunks": chunks}


def augment_node(state: RagState) -> dict:
    best_chunks = augmenter.rerank(state["question"], state["chunks"])
    return {"chunks": best_chunks, "context": augmenter.build_context(best_chunks)}


def generate_node(state: RagState) -> dict:
    if not state["chunks"]:
        return {"answer": "I could not find that in the processed invoices."}
    return {"answer": generator.generate_answer(state["question"], state["context"])}


def reflect_node(state: RagState) -> dict:
    scores = reflector.evaluate_answer(state["question"], state["context"], state["answer"])
    attempt = state["attempt"] + 1
    log.info("RAG attempt %d: triad average %.2f", attempt, scores["average"])

    current = {
        "answer": state["answer"],
        "scores": scores,
        "sources": [
            {"invoice_id": c["invoice_id"], "source_type": c["source_type"], "vendor": c["vendor"],
             "score": c.get("rerank_score"), "text": c["chunk_text"][:300]}
            for c in state["chunks"]
        ],
    }
    best = state.get("best")
    if best is None or scores["average"] > best["scores"]["average"]:
        best = current

    return {
        "attempt": attempt,
        "top_k": state["top_k"] + TOP_K_STEP,
        "scores": scores,
        "best": best,
        "attempts_log": state["attempts_log"] + [{"attempt": attempt, **scores}],
    }


def finish_node(state: RagState) -> dict:
    best = state["best"]
    passed = best["scores"]["average"] > RAG_PASS_SCORE
    answer = best["answer"]
    if not passed:
        answer += ("\n\n_Note: this answer did not reach the quality threshold after "
                   f"{RAG_MAX_RETRIES} retries, so please verify it against the report._")
    return {"result": {
        "answer": answer,
        "answered_by": "rag",
        "sources": best["sources"],
        "scores": best["scores"],
        "passed": passed,
        "attempts": state["attempt"],
        "attempts_log": state["attempts_log"],
    }}


# ---------------------------------------------------------------
# Edges
# ---------------------------------------------------------------
def after_route(state: RagState) -> str:
    return state["route"]


def after_sql(state: RagState) -> str:
    return "done" if state.get("result") else "retrieve"


def after_reflect(state: RagState) -> str:
    passed = state["scores"]["average"] > RAG_PASS_SCORE
    retries_used = state["attempt"] - 1
    if passed or retries_used >= RAG_MAX_RETRIES:
        return "finish"
    return "retry"


def build_rag_graph():
    graph = StateGraph(RagState)
    graph.add_node("route_question", route_node)
    graph.add_node("sql_answer", sql_answer_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("augment", augment_node)
    graph.add_node("generate", generate_node)
    graph.add_node("reflect", reflect_node)
    graph.add_node("finish", finish_node)

    graph.add_edge(START, "route_question")
    graph.add_conditional_edges("route_question", after_route, {"sql": "sql_answer", "rag": "retrieve"})
    graph.add_conditional_edges("sql_answer", after_sql, {"done": END, "retrieve": "retrieve"})
    graph.add_edge("retrieve", "augment")
    graph.add_edge("augment", "generate")
    graph.add_edge("generate", "reflect")
    graph.add_conditional_edges("reflect", after_reflect, {"finish": "finish", "retry": "retrieve"})
    graph.add_edge("finish", END)
    return graph.compile()


rag_graph = build_rag_graph()


def ask_question(question: str, invoice_id: str | None = None) -> dict:
    """Entry point used by the API. Repeated questions come straight from Redis."""
    cache_key = make_key("answer", question.strip().lower(), invoice_id or "all")
    cached = cache_get(cache_key)
    if cached:
        cached["from_cache"] = True
        return cached

    final_state = rag_graph.invoke({"question": question, "invoice_id": invoice_id},
                                   config={"recursion_limit": 40})
    result = final_state["result"]
    result["from_cache"] = False
    cache_set(cache_key, result, ANSWER_CACHE_TTL_SECONDS)
    return result
