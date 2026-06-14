from duckduckgo_search import DDGS

def web_search(query: str, max_results: int = 5) -> str:
    """Search DuckDuckGo and return formatted results."""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        if not results:
            return "No web results found."
        return "\n\n".join(
            f"[{r['title']}]\n{r['body']}\nSource: {r['href']}"
            for r in results
        )
    except Exception as e:
        return f"Web search failed: {e}"