from langchain_core.tools import tool
from ddgs import DDGS


@tool
def web_search(query: str, max_results: int = 5) -> str:
    """Search the web for current information on a topic.

    Use this when you need facts, news, or information that you
    don't already know or that may have changed.

    Args:
        query: The search query, e.g. "latest Minecraft version"
        max_results: Number of results to return (default 5)
    """
    try:
        results = DDGS().text(query, max_results=max_results)
    except Exception as e:
        return f"Search failed: {e}"

    if not results:
        return "No results found."

    formatted = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "No title")
        body = r.get("body", "")
        href = r.get("href", "")
        formatted.append(f"{i}. {title}\n   {body}\n   Link: {href}")

    return "\n\n".join(formatted)


if __name__ == "__main__":
    # Quick test
    print(web_search.invoke({"query": "Ryzen 5 5650X specs", "max_results": 3}))