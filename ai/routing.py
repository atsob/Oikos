"""Questions the AI Assistant answers straight from a ready-made tool (ai/finance_tools.py), without the model.

Kept apart from ai/agent.py so a routed question does not wait for LangChain to load.
"""
import re

from ai.finance_tools import (holdings_performance, investment_pnl, net_worth_history, net_worth_sources, portfolio_value,
                              spending_by_category, spending_reduction, spending_vs_budget)
from ai.more_tools import (account_balances, allocation, compare_periods, credit_cards, dividends_interest, income,
                           market_move, payee_spending, subscriptions, taxes, unusual_transactions,
                           upcoming_bills)

_MONTHS = r"(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)"
_PERIOD = rf"(month|year|week|quarter|q[1-4]|ytd|(19|20)\d\d|{_MONTHS})"

# Questions whose tool is clear from their words go straight to it, without the model: llama3.1:8b sent "What was my
# total spending last month?" to Spending_vs_Budget and "and my net worth?" (after a portfolio question) to its own SQL
# on accounts, even with the right tool named in the prompt. First match wins, so budget comes before plain spending.
_ROUTES = [
    ("Spending_vs_Budget", spending_vs_budget,
     r"overspen|over[- ]?budget|under[- ]?budget|\bbudget"),
    ("Unusual_Transactions", unusual_transactions,
     r"unusual|strange|suspicious|\bodd\b|anomal|out of the ordinary|stands? out|double[- ]charg|charged twice|duplicate (payment|charge|transaction)"),
    ("Subscriptions", subscriptions,
     r"subscriptions?|recurring (charges?|payments?|bills?|costs?)|standing orders?|direct debits?|(pay|charged) (every|each) month|monthly (charges|payments|bills)"),
    # Cards before upcoming bills: "when is the next credit card payment due?" is about the card.
    ("Credit_Cards", credit_cards,
     r"credit ?cards?|\bcards?\b.*\b(owe|owed|balance|due|limit|statement|payment)|\b(owe|limit|statement)\b.*\bcards?\b|mastercard|\bvisa\b"),
    ("Upcoming_Bills", upcoming_bills,
     r"\bwhat('s| is| are)? (due|coming up)|\bupcoming\b|\bdue (next|this|in|soon|before)|(bills?|payments?) (due|coming)|"
     r"(next|coming) (week|month|\d+ days)('s)? (bills|payments)|cash ?flow forecast|(will|would) i have enough|enough (money|cash)|"
     r"run (out|short)|go (below zero|negative|overdrawn)|can i afford"),
    # Where spending could be cut — before plain spending by category, which listed taxes first and gave no comparison.
    ("Spending_Reduction", spending_reduction,
     r"\b(reduc|cut|lower|trim|sav)\w*\b.*\b(spend|spent|spending|expenses?|costs?|money)|(spend|spent|spending|expenses?|costs?)\b.*\b(reduc|cut|lower|trim)\w*"
     r"|cut back|spend less|(spent|spend|spending) (too )?much|where (can|could|should) i save"),
    # Where the money came from: income less expenses vs investment gains — before the history and P&L routes, which
    # answered "where did it come from, investments or income vs expenses?" with the investment half only.
    ("Net_Worth_Sources", net_worth_sources,
     r"(where|what)\b.*\b(come|came|coming|comes|from|driv|caus)\w*\b.*\b(worth|wealth|grow|growth|increase|gain|change)"
     r"|(worth|wealth|growth|increase)\b.*\b(come|came|comes|from)\b"
     r"|income (vs\.?|versus|or|and|minus|less) (expenses|spending)|savings? (vs\.?|or|versus) invest|invest\w* (performance|gains?|returns?) (vs\.?|or|versus)"
     r"|how much (did|have) i saved?\b|savings rate"),
    ("Compare_Periods", compare_periods,
     rf"\b(vs\.?|versus|compared (to|with)|against)\b.*\b{_PERIOD}|(more|less)\b.*\b(than|compared to) (last|the previous|previous|before|in)\b|"
     rf"\bcompare\b.*\b{_PERIOD}"),
    ("Dividends_and_Interest", dividends_interest,
     r"dividends?|interest (income|earned|received|paid|i (got|received|earned))|how much interest"),
    ("Taxes", taxes,
     r"^(?!.*(tax[- ]loss|harvest)).*\btax(es)?\b"),
    ("Income", income,
     r"^(?!.*\b(invest\w*|portfolio|stocks?|shares|crypto|holdings?|p&l|pnl|expenses?|spending)\b).*"
     r"\b(earn|earned|earning|earnings|income|salary|salaries|wages?|bonus(es)?|gross pay|paid me|commissions?)\b"),
    ("Account_Balances", account_balances,
     r"\bbalances?\b|how much (is|do i have|have i got) (in|on) |match(es)? the bank|agree with the bank|(bank|banks)('s)? (balance|figures)"),
    ("Market_Move", market_move,
     r"^(?!.*\b(spend|spent|spending|expenses?|costs?|income|salary|net ?worth|budget|bills?|balances?|savings?)\b)"
     r"(?!.*\b(my|our) (investments?|holdings?|portfolio|stocks?|shares|positions?)\b)"
     r"(\bwhy\b.*\b(fall|fell|drop|down|declin|plung|slump|crash|sink|sank|rise|rising|rose|up\b|rall|jump|surg|soar|gain|los(e|ing)|mov)\w*"
     r"|what('s| is) (happening|going on) with|\bnews (on|about|for)\b)"),
    ("Allocation", allocation,
     r"allocat|asset mix|exposure|diversif|rebalanc|(portfolio|investments) (split|spread|distributed)|by (currency|country|region)|asset class"),
    ("Payee_Spending", payee_spending,
     r"\b(spen[dt]|paid|pay|bought|buy)\b.*\bat (?!least|all|most|the (start|end|beginning))\w|(top|biggest|largest|most)( \d+)? (shops?|stores?|merchants?|payees?|vendors?)|"
     r"which (shops?|stores?|merchants?|payees?)|who (did )?i pay (the )?most"),
    ("Net_Worth_History", net_worth_history,
     r"net[- ]?worth\b.*\b(chang|grow|grew|evolv|trend|history|develop|over the|since|last \d+|past \d+|this year|month)|(chang|grow|grew|trend|history)\w*\b.*\bnet[- ]?worth"),
    ("Portfolio_Value_and_Net_Worth", portfolio_value,
     r"net[- ]?worth|portfolio (value|worth)|value of (my|the) (portfolio|investments|holdings)|(portfolio|investments|holdings) (are |is )?worth|total wealth|how much (money )?do i have"),
    ("Investment_PnL", investment_pnl,
     r"\bp\s*&\s*l\b|\bpnl\b|\bprofits?\b|gains? (and|&) loss|how much (did|have) i (make|made|earn|earned|lose|lost)|(realized|realised|unrealized|unrealised) gains?"),
    ("Holdings_Performance", holdings_performance,
     r"(best|worst|top|bottom|biggest)\b.*\b(perform|gainers?|losers?|movers?|investments?|holdings?|stocks?|securities|positions?)|perform(ed|ing|ance)\b.*\b(investments?|holdings?|portfolio|stocks?)|(investments?|holdings?|stocks?|portfolio|positions?) .*(fell|fall\w*|drop\w*|rose|ris\w*|gained|lost|down|up)\b"),
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
