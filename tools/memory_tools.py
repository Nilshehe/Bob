from langchain_core.tools import tool
from funktioner.memory_store import ltm

@tool
def remember(text: str, tags: str = "") -> str:
    """Spara viktig info i långtidsminnet för framtida sessioner."""
    doc_id = ltm.store(text, source="agent", tags=tags.split(","))
    return f"Sparat: {doc_id}"

@tool
def recall(query: str) -> str:
    """Sök i långtidsminnet efter relevant tidigare info."""
    results = ltm.query(query, n_results=5)
    docs = results["documents"][0]
    return "\n---\n".join(docs) if docs else "Inget hittades."