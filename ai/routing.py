"""Questions the AI Assistant answers straight from a ready-made tool (ai/finance_tools.py), without the model.

Kept apart from ai/agent.py so a routed question does not wait for LangChain to load.
"""
import re

from ai.finance_tools import (holdings_performance, investment_pnl, net_worth_history, portfolio_value, spending_by_category,
                              spending_vs_budget)

# Questions whose tool is clear from their words go straight to it, without the model: llama3.1:8b sent "What was my
# total spending last month?" to Spending_vs_Budget and "and my net worth?" (after a portfolio question) to its own SQL
# on accounts, even with the right tool named in the prompt. First match wins, so budget comes before plain spending.
_ROUTES = [
    ("Spending_vs_Budget", spending_vs_budget,
     r"overspen|over[- ]?budget|under[- ]?budget|\bbudget"),
    ("Net_Worth_History", net_worth_history,
     r"net[- ]?worth\b.*\b(chang|grow|grew|evolv|trend|history|develop|over the|since|last \d+|past \d+|this year|month)|(chang|grow|grew|trend|history)\w*\b.*\bnet[- ]?worth"),
    ("Portfolio_Value_and_Net_Worth", portfolio_value,
     r"net[- ]?worth|portfolio (value|worth)|value of (my|the) (portfolio|investments|holdings)|(portfolio|investments|holdings) (are |is )?worth|total wealth|how much (money )?do i have"),
    ("Investment_PnL", investment_pnl,
     r"\bp\s*&\s*l\b|\bpnl\b|\bprofits?\b|gains? (and|&) loss|how much (did|have) i (make|made|earn|earned|lose|lost)|(realized|realised|unrealized|unrealised) gains?"),
    ("Holdings_Performance", holdings_performance,
     r"(best|worst|top|bottom|biggest)\b.*\b(perform|gainers?|losers?|movers?|investments?|holdings?|stocks?|securities|positions?)|perform(ed|ing|ance)\b.*\b(investments?|holdings?|portfolio|stocks?)|(investments?|holdings?|stocks?) .*(fell|dropped|rose|gained|lost)"),
    ("Spending_by_Category", spending_by_category,
     r"((total|how much)\b.*\b(spend|spent|spending|expenses)\b(?!.*\bon\b))|spending by category|where did (my|the) money go|(top|biggest|largest) (spending |expense )?categor"),
]


def route(question: str, history: list[dict] | None = None):
    """(tool name, function, input) for a question one of the ready-made tools answers, else None. A short follow-up
    ("and the best ones this month?") with no match of its own takes the tool of the previous question, and its count."""
    q = question.lower()
    for name, fn, pat in _ROUTES:
        if re.search(pat, q):
            return name, fn, question
    prev = next((h["text"] for h in reversed(history or []) if h.get("role") == "user"), None)
    if prev and len(q.split()) <= 8 and re.match(r"\s*(and|what about|how about|same|now|also|ok)\b", q):
        hit = route(prev)
        if hit:
            n = re.search(r"\b(\d{1,2})\b", re.sub(r"(19|20)\d{2}", "", prev))
            text = question if not n or re.search(r"\b\d{1,2}\b", question) else f"{question} {n.group(1)}"
            return hit[0], hit[1], text
    return None
