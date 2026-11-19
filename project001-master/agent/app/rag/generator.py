"""Generation Agent - answers from the assembled context (Cohere Command R+ through LiteLLM)."""

from app.services.llm_client import ask_llm, persona, prompt
from app.settings import RAG_MODEL


def generate_answer(question: str, context: str) -> str:
    return ask_llm(RAG_MODEL, persona("rag_generator"),
                   prompt("rag_generate", question=question, context=context),
                   max_tokens=700).strip()
