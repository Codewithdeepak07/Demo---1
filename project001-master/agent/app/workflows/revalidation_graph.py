"""
Re-validation workflow (runs when the auditor clicks "Re-validate").

    START -> load_corrected_invoice   (original extraction + human corrections)
          -> lookup_erp               (fresh PO / vendor / SKU data from mock-erp)
          -> recalculate              (every line total, tax and total rebuilt from the start)
          -> validate_data -> validate_business
          -> write_report             (old discrepancies + corrections + recalculation + new result)
          -> index -> END

No extraction or translation happens here - those were done once and are reused.
"""

from langgraph.graph import END, START, StateGraph

from app.workflows import nodes


def build_revalidation_graph(checkpointer):
    graph = StateGraph(nodes.InvoiceState)

    graph.add_node("load_corrected_invoice", nodes.load_corrected_invoice_node)
    graph.add_node("lookup_erp", nodes.lookup_erp_node)
    graph.add_node("recalculate", nodes.recalculate_node)
    graph.add_node("validate_data", nodes.validate_data_node)
    graph.add_node("validate_business", nodes.validate_business_node)
    graph.add_node("write_report", nodes.report_node)
    graph.add_node("index", nodes.index_node)

    graph.add_edge(START, "load_corrected_invoice")
    graph.add_edge("load_corrected_invoice", "lookup_erp")
    graph.add_edge("lookup_erp", "recalculate")
    graph.add_edge("recalculate", "validate_data")
    graph.add_edge("validate_data", "validate_business")
    graph.add_edge("validate_business", "write_report")
    graph.add_edge("write_report", "index")
    graph.add_edge("index", END)

    return graph.compile(checkpointer=checkpointer)
