import re

from ddgs import DDGS

# The agent's LLM crafts this query itself from the user's question and its own
# DB-query results — a technical backstop is needed in addition to prompting it
# not to, since a query is the one place this codebase sends free text to an
# external, logging third party (DuckDuckGo) rather than the self-hosted Ollama
# instance everything else uses. Strips anything that looks like a monetary
# amount, account/card/IBAN-style number, or email address before it ever leaves
# the process — independent of whatever the model actually asked for.
_MONEY_RE   = re.compile(r'[€$£]\s?\d[\d,]*\.?\d*|\b\d[\d,]*\.\d{2}\s?(?:EUR|USD|GBP)\b', re.IGNORECASE)
_IBAN_RE    = re.compile(r'\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b')
_LONGNUM_RE = re.compile(r'\b\d{8,}\b')   # account/card numbers, statement refs, etc.
_EMAIL_RE   = re.compile(r'\b[\w.+-]+@[\w-]+\.[\w.-]+\b')


def _scrub_query(query: str) -> str:
    q = _EMAIL_RE.sub('[redacted]', query)
    q = _IBAN_RE.sub('[redacted]', q)
    q = _MONEY_RE.sub('[redacted]', q)
    q = _LONGNUM_RE.sub('[redacted]', q)
    return q


def web_search(query: str, max_results: int = 5) -> str:
    """Search DuckDuckGo and return formatted results."""
    query = _scrub_query(query)
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
