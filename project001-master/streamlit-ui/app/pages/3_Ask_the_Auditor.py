"""Ask the Auditor - natural-language questions about processed invoices (SQL or RAG)."""

import streamlit as st

import agent_api
from components.ui import esc, hero, invoice_label, setup_page
from db import queries

setup_page("Ask the Auditor", "💬")
hero("AI assistant", "Ask the auditor",
     "Counting and listing questions are answered with SQL; open questions search invoice and report text "
     "and are checked with the RAG Triad before you see them.")

# ---------------------------------------------------------------
# Scope + example questions
# ---------------------------------------------------------------
invoices = [i for i in queries.list_invoices() if i["processing_status"] == "processed"]
scope_options = {"": "All processed invoices"} | {i["invoice_id"]: invoice_label(i) for i in invoices}

with st.sidebar:
    st.markdown("#### Search scope")
    scope = st.selectbox("Limit answers to", list(scope_options), format_func=scope_options.get)
    if st.button("🧹 Clear conversation", use_container_width=True):
        st.session_state["chat"] = []

    # --- RAG index health + rebuild ------------------------------------
    st.markdown("#### 🧠 Knowledge index")
    status = agent_api.index_status()
    if "error" in status:
        st.error(f"Index status unavailable: {status['error']}")
    else:
        chunks, total = status["chunks_in_qdrant"], status["processed_invoices"]
        css = "pill-approve" if chunks > 0 else "pill-reject"
        st.markdown(f'<span class="pill {css}">{chunks} chunks</span> &nbsp;'
                    f'<span class="pill pill-info">{total} invoices in DB</span>',
                    unsafe_allow_html=True)
        if chunks == 0 and total > 0:
            st.warning("The index is empty, so open questions cannot be answered. Rebuild it below.")

    st.caption("Rebuilds Qdrant from text already stored in PostgreSQL - no OCR or translation is repeated.")
    if st.button("♻️ Rebuild index", use_container_width=True):
        with st.spinner("Re-embedding invoice and report text…"):
            try:
                result = agent_api.rebuild_index()
                st.success(f"Indexed {result['invoices_indexed']} invoice(s), {result['chunks']} chunks.")
                for failure in result.get("failed", []):
                    st.error(f"{failure['invoice_id'][:8]}…: {failure['error']}")
            except agent_api.AgentError as error:
                st.error(f"Rebuild failed: {error}")

examples = [
    "How many invoices need manual review?",
    "Why was invoice INV-1001 flagged?",
    "Which vendors have price mismatches?",
    "What is the total amount of approved invoices in EUR?",
]
st.markdown("**Try asking:**")
example_cols = st.columns(len(examples))
clicked = None
for column, example in zip(example_cols, examples):
    if column.button(example, use_container_width=True):
        clicked = example

st.session_state.setdefault("chat", [])


# ---------------------------------------------------------------
# Rendering one answer
# ---------------------------------------------------------------
def show_answer(result: dict) -> None:
    st.markdown(result["answer"])

    badges = []
    if result.get("from_cache"):
        badges.append('<span class="pill pill-info">⚡ from cache</span>')
    source = "🗄️ SQL" if result.get("answered_by") == "sql" else "🔎 RAG"
    badges.append(f'<span class="pill pill-info">{source}</span>')
    if result.get("answered_by") == "rag":
        passed = result.get("passed")
        badges.append(f'<span class="pill {"pill-approve" if passed else "pill-review"}">'
                      f'{"quality check passed" if passed else "low confidence"}</span>')
    st.markdown(" ".join(badges), unsafe_allow_html=True)

    scores = result.get("scores")
    if scores:
        st.markdown(
            f'<span class="score">answer relevance {scores["answer_relevance"]:.2f}</span>'
            f'<span class="score">groundedness {scores["groundedness"]:.2f}</span>'
            f'<span class="score">context relevance {scores["context_relevance"]:.2f}</span>'
            f'<span class="score"><b>average {scores["average"]:.2f}</b></span>'
            f'<span class="score">attempts {result.get("attempts", 1)}</span>',
            unsafe_allow_html=True,
        )

    if result.get("sql"):
        with st.expander("SQL used"):
            st.code(result["sql"], language="sql")
            if result.get("rows"):
                st.dataframe(result["rows"], use_container_width=True)
    if result.get("sources"):
        with st.expander(f"Sources ({len(result['sources'])})"):
            for source_chunk in result["sources"]:
                st.markdown(f"**{esc(source_chunk['source_type'])}** · {esc(source_chunk['vendor'])} · "
                            f"score {source_chunk['score']}")
                st.caption(source_chunk["text"])


# ---------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------
for message in st.session_state["chat"]:
    with st.chat_message(message["role"], avatar="🧑‍💼" if message["role"] == "user" else "🤖"):
        if message["role"] == "user":
            st.markdown(message["content"])
        else:
            show_answer(message["content"])

question = st.chat_input("Ask about vendors, discrepancies, totals…") or clicked
if question:
    st.session_state["chat"].append({"role": "user", "content": question})
    with st.chat_message("user", avatar="🧑‍💼"):
        st.markdown(question)

    with st.chat_message("assistant", avatar="🤖"):
        with st.spinner("Thinking… retrieving, answering and self-checking"):
            try:
                result = agent_api.ask(question, scope or None)
                show_answer(result)
                st.session_state["chat"].append({"role": "assistant", "content": result})
            except agent_api.AgentError as error:
                st.error(f"Could not get an answer: {error}")
