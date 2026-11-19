"""
First-run invoice workflow.

    START ─(route by file type)─> extract_pdf | extract_docx | extract_image
          -> translate -> parse_fields -> lookup_erp -> validate_data
          -> validate_business -> write_report -> index -> END
"""

from langgraph.graph import END, START, StateGraph

from app.workflows import nodes


def build_invoice_graph(checkpointer):
    graph = StateGraph(nodes.InvoiceState)

    graph.add_node("extract_pdf", nodes.extract_pdf_node)
    graph.add_node("extract_docx", nodes.extract_docx_node)
    graph.add_node("extract_image", nodes.extract_image_node)
    graph.add_node("translate", nodes.translate_node)
    graph.add_node("parse_fields", nodes.parse_fields_node)
    graph.add_node("lookup_erp", nodes.lookup_erp_node)
    graph.add_node("validate_data", nodes.validate_data_node)
    graph.add_node("validate_business", nodes.validate_business_node)
    graph.add_node("write_report", nodes.report_node)
    graph.add_node("index", nodes.index_node)

    graph.add_conditional_edges(START, nodes.route_by_file_type, {
        "pdf": "extract_pdf",
        "docx": "extract_docx",
        "image": "extract_image",
    })
    for extract_node in ("extract_pdf", "extract_docx", "extract_image"):
        graph.add_edge(extract_node, "translate")

    graph.add_edge("translate", "parse_fields")
    graph.add_edge("parse_fields", "lookup_erp")
    graph.add_edge("lookup_erp", "validate_data")
    graph.add_edge("validate_data", "validate_business")
    graph.add_edge("validate_business", "write_report")
    graph.add_edge("write_report", "index")
    graph.add_edge("index", END)

    return graph.compile(checkpointer=checkpointer)
