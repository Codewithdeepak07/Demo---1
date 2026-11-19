"""Retrieval Agent - finds the most similar chunks in Qdrant, optionally for one invoice only."""

from app.services import vector_store
from app.services.llm_client import embed_text


def retrieve_chunks(question: str, top_k: int, invoice_id: str | None = None) -> list[dict]:
    query_vector = embed_text(question)
    return vector_store.search(query_vector, top_k=top_k, invoice_id=invoice_id)
