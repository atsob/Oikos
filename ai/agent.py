import re
from sqlalchemy import inspect as sa_inspect
from langchain_community.agent_toolkits import create_sql_agent
from langchain_community.agent_toolkits.sql.toolkit import SQLDatabaseToolkit
from langchain_core.tools import Tool

from ai.web_search import web_search
from ai.finance_tools import spending_by_category, spending_vs_budget, category_transactions, holdings_performance, portfolio_value, net_worth_history, investment_pnl, net_worth_sources, spending_reduction
from ai import more_tools as _more


def _compact_schema(db) -> str:
    """Return a concise schema string: Table(col1, col2, ...) — one line per table.

    Much shorter than get_table_info() which includes full DDL and sample rows,
    keeping the prompt small enough for local models to process quickly.
    """
    try:
        inspector = sa_inspect(db._engine)
        lines = []
        for table in sorted(db.get_usable_table_names()):
            # Exclude 'embedding' — a 768-float vector column that overflows the context window
            cols = [c["name"] for c in inspector.get_columns(table) if c["name"] != "embedding"]
            lines.append(f"{table}({', '.join(cols)})")
        return "\n".join(lines)
    except Exception:
        # Graceful fallback — at least table names
        try:
            return ", ".join(db.get_usable_table_names())
        except Exception:
            return ""


def _clean_sql(sql: str) -> str:
    """Strip markdown fences/backticks and inject a row cap for safety."""
    import re
    sql = sql.strip()
    # ```sql ... ``` or ``` ... ```
    sql = re.sub(r'^```(?:sql)?\s*', '', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\s*```$', '', sql)
    # single backtick wrap `SELECT ...`
    if sql.startswith('`') and sql.endswith('`'):
        sql = sql[1:-1]
    sql = sql.strip().rstrip(';')
    # Refused outright, not left to the prompt: SELECT * on accounts returned each row's 768-float
    # embedding and the model then "analysed" those numbers. StatementError is a SQLAlchemyError, which
    # the SQL tool hands back to the model as "Error: ..." so it rewrites the query.
    from sqlalchemy.exc import StatementError
    if re.search(r'\bSELECT\s+(DISTINCT\s+)?(\w+\.)?\*', sql, re.IGNORECASE) or re.search(r'\bembedding\b', sql, re.IGNORECASE):
        raise StatementError("SELECT * and the embedding column are not allowed — list the columns you need", sql, None, None)
    # Hard cap: inject LIMIT 20 on plain SELECT queries that have no aggregate
    # and no explicit LIMIT — prevents small models returning thousands of raw rows.
    if re.match(r'^\s*SELECT\b', sql, re.IGNORECASE):
        has_aggregate = bool(re.search(r'\b(SUM|COUNT|AVG|MIN|MAX)\s*\(', sql, re.IGNORECASE))
        has_limit = bool(re.search(r'\bLIMIT\b', sql, re.IGNORECASE))
        if not has_aggregate and not has_limit:
            sql = sql + ' LIMIT 20'
    return sql


def with_history(question: str, history: list[dict] | None) -> str:
    """The question with the last few exchanges before it, so a follow-up ("can you give more details?") is about the same thing."""
    turns = [h for h in (history or []) if h.get("text")][-6:]
    if not turns:
        return question
    lines = [f"{'User' if h.get('role') == 'user' else 'Assistant'}: {str(h['text'])[:1500]}" for h in turns]
    return "Conversation so far:\n" + "\n".join(lines) + f"\n\nCurrent question: {question}"


def create_ai_agent(llm, db, rag_engine):
    """Create the AI agent with SQL and RAG tools."""

    schema_info = _compact_schema(db)

    toolkit = SQLDatabaseToolkit(db=db, llm=llm)

    # Sanitize SQL before it hits the DB — small models wrap queries in backticks
    _orig_run = db.run
    db.run = lambda sql, *a, **kw: _orig_run(_clean_sql(sql), *a, **kw)

    rag_tool = Tool(
        name="Financial_Knowledge_Base",
        func=lambda q: str(rag_engine.query(q)),
        # It searches the user's own transactions by meaning — the old description ("financial concepts ...") sent "why are gold
        # and silver falling?" here, which came back with transactions from 2004.
        description="Searches the user's OWN transactions by meaning (e.g. payments for the car service, what I paid the plumber). "
                    "Never for markets, prices, news or general knowledge.",
    )

    budget_tool = Tool(
        name="Spending_vs_Budget",
        func=lambda q: spending_vs_budget(q),
        description=(
            "USE THIS for any question about budgets, overspending or underspending (\"which categories did I overspend\", "
            "\"am I over budget\"). Input: just the period in plain words, e.g. Q1 2026, March 2026, 2026, last month, "
            "or 2026-01-01 to 2026-03-31. Returns the categories over their pro-rated budget, by how much, and the totals."
        ),
        return_direct=True,     # its table is the answer — the model's own summary kept 1 of 46 categories
    )
    category_tool = Tool(
        name="Spending_by_Category",
        func=lambda q: spending_by_category(q),
        description=(
            "USE THIS for spending per category (\"where did my money go\", \"how much did I spend on X\", top categories). "
            "Input: just the period in plain words, e.g. Q1 2026, March 2026, 2026, last month. Returns every expense "
            "category by full path (e.g. Household : Super Market), largest first, with the total."
        ),
        return_direct=True,
    )
    category_tx_tool = Tool(
        name="Category_Transactions",
        func=lambda q: category_transactions(q),
        description=(
            "USE THIS for the transactions behind a category — details of a category's spending or overspending, \"what did I "
            "spend on Car Rental\". Input: the category name, a comma, then the period, e.g. Car Rental, Q1 2026 "
            "(a parent such as Healthcare includes its subcategories)."
        ),
        return_direct=True,
    )

    perf_tool = Tool(
        name="Holdings_Performance",
        func=lambda q: holdings_performance(q),
        description=(
            "USE THIS for how the user's investments / holdings / portfolio performed — best or worst performers, gainers, "
            "losers, \"which of my investments fell today\". Input: plain words with best or worst, how many, and the period: "
            "e.g. worst 10 today, best 5 this month, worst YTD, worst since purchase. In a follow-up keep the count and period "
            "the user asked for before unless they change it."
        ),
        return_direct=True,
    )

    value_tool = Tool(
        name="Portfolio_Value_and_Net_Worth",
        func=lambda q: portfolio_value(q),
        description=(
            "USE THIS for the current value of the user's portfolio / investments, their net worth, or how much they have in "
            "cash, pension or assets — the Dashboard's own figures, with today's, this month's and this year's change. "
            "Input: what was asked, e.g. portfolio value or net worth."
        ),
        return_direct=True,
    )

    pnl_tool = Tool(
        name="Investment_PnL",
        func=lambda q: investment_pnl(q),
        description=(
            "USE THIS for investment profit and loss (P&L, gains, how much the user made or lost) in total and per investment "
            "account — today, this week, month, quarter, year to date or all time. Input: the period, e.g. YTD, this month, all time."
        ),
        return_direct=True,
    )
    nw_history_tool = Tool(
        name="Net_Worth_History",
        func=lambda q: net_worth_history(q),
        description=(
            "USE THIS for how net worth changed over time — month by month over the last N months, this year or the last "
            "year, by cash / investments / pension / assets. Input: the span in plain words, e.g. last 3 months, this year."
        ),
        return_direct=True,
    )

    nw_sources_tool = Tool(
        name="Net_Worth_Sources",
        func=lambda q: net_worth_sources(q),
        description=(
            "USE THIS for where net worth growth came from — income less expenses (savings) versus investment gains versus "
            "the rest, \"is it investments or income vs expenses\", \"how much did I save\". Input: the span, e.g. last 12 "
            "months (the default), this year, last 3 years."
        ),
        return_direct=True,
    )

    reduce_tool = Tool(
        name="Spending_Reduction",
        func=lambda q: spending_reduction(q),
        description=(
            "USE THIS for where spending could be reduced or cut, what to save on, where the user spends too much. Input: the "
            "period, e.g. this year (the default), last year, Q3 2026. Leaves taxes and reimbursed costs out, compares with a "
            "year before and says what a cut would save."
        ),
        return_direct=True,
    )

    # The other ready-made answers (ai/more_tools.py): each returns the finished answer, from the app's own reports.
    more_tools = [Tool(name=name, func=fn, description=desc, return_direct=True) for name, fn, desc in (
        ("Upcoming_Bills", _more.upcoming_bills, "USE THIS for what is due / upcoming bills and payments, the cash flow forecast, "
         "and whether an account will have enough or go below zero. Input: the horizon and any account, e.g. next 30 days, payroll 0095."),
        ("Subscriptions", _more.subscriptions, "USE THIS for subscriptions and recurring charges (what the user pays every month, "
         "standing orders) with the yearly cost and price rises. Input: anything, e.g. subscriptions."),
        ("Compare_Periods", _more.compare_periods, "USE THIS to compare spending between two periods by category (this month vs last "
         "month, October vs September, 2026 vs 2025). Input: the two periods, e.g. October 2026 vs September 2026."),
        ("Payee_Spending", _more.payee_spending, "USE THIS for spending at a payee / shop / merchant (\"how much at Sklavenitis\") "
         "or the payees paid the most. Input: the payee name and the period, e.g. Sklavenitis this year."),
        ("Income", _more.income, "USE THIS for income, salary, earnings, bonuses by source and month, against a year before. "
         "Input: the period, e.g. this year."),
        ("Dividends_and_Interest", _more.dividends_interest, "USE THIS for dividends and interest received and expected. Input: the period, e.g. this year."),
        ("Taxes", _more.taxes, "USE THIS for taxes paid by kind and month, against a year before and as a share of income. Input: the period."),
        ("Credit_Cards", _more.credit_cards, "USE THIS for credit cards: what is owed, limits, available credit, statements and the "
         "next payment. Input: a card name or all."),
        ("Account_Balances", _more.account_balances, "USE THIS for the balance of a bank / cash / card account and whether it agrees "
         "with the bank. Input: the account name or last 4 digits, or all."),
        ("Unusual_Transactions", _more.unusual_transactions, "USE THIS for unusual, suspicious or double charges and spending spikes. "
         "Input: the period, e.g. this month."),
        ("Market_Move", _more.market_move, "USE THIS for why a security, metal, index or market is falling or rising, or its "
         "news: price moves, what the user holds of it, and recent headlines. Input: the security or market, e.g. gold silver."),
        ("Allocation", _more.allocation, "USE THIS for portfolio allocation by asset class against targets, currency and country "
         "exposure, rebalancing. Input: anything, e.g. allocation."),
    )]

    web_search_tool = Tool(
        name="Web_Search",
        func=lambda q: web_search(q),
        description=(
            "Use for anything requiring current, real-world information not in the user's "
            "database or the Financial_Knowledge_Base — e.g. today's price of a stock/crypto, "
            "recent news, or general facts. Input is a plain-text search query sent to an "
            "external, non-private search engine — NEVER include specific amounts, balances, "
            "account/card numbers, or personal names from the user's data. Search only for "
            "generic public information (e.g. 'AAPL stock price today', not '$4,532.10 AAPL "
            "position value')."
        ),
    )

    # IMPORTANT: zero-shot-react-description uses plain ReAct format, NOT JSON.
    # Format must be:
    #   Action: <tool_name>
    #   Action Input: <input>
    # Using JSON format here causes output parsing errors with this agent type.
    custom_prefix = f"""You are a personal finance assistant with direct read access to the user's own financial database.
This is the user's personal data — always answer questions about it freely and helpfully. Never refuse.
The user's primary currency is EUR (€). Always present monetary amounts in EUR with the € symbol.
CRITICAL: The `transactions` table has NO `amount_eur` column. For ANY monetary query (spending, income, balance) you MUST use the view `v_transactions_eur` which has `amount_eur` pre-computed. Never query `transactions` directly for amounts.
View `v_transactions_eur` columns: transactions_id, date, description, total_amount, currency, fx_rate, amount_eur, payees_id, payee, accounts_id, accounts_name, accounts_type, accounts_id_target.
Exclude internal transfers by adding WHERE accounts_id_target IS NULL.

DATABASE SCHEMA:
{schema_info}

Categories are on `splits` (splits.categories_id, splits.amount; join splits.transactions_id = transactions.transactions_id), NOT on transactions or payees — never join payees to categories.
"Overspent" / "over budget" means spending above the user's budget: ALWAYS answer it with the Spending_vs_Budget tool, never with your own SQL.
Where spending could be reduced or cut is the Spending_Reduction tool. Bills due and the cash forecast are Upcoming_Bills; subscriptions are Subscriptions; two periods side by side are Compare_Periods; a payee or shop is Payee_Spending; income is Income; dividends and interest are Dividends_and_Interest; taxes are Taxes; cards are Credit_Cards; an account's balance is Account_Balances; unusual charges are Unusual_Transactions; allocation and exposure are Allocation; why a security or market is moving (and its news) is Market_Move. For spending per category use the Spending_by_Category tool; for the transactions behind a category, Category_Transactions.
The portfolio's value, net worth and cash / pension / asset totals are the Portfolio_Value_and_Net_Worth tool — never computed from holdings yourself (simple_avg_price is the purchase price, not today's).
Where net worth growth came from (income less expenses versus investment gains) is the Net_Worth_Sources tool — Investment_PnL gives only the investment half.
How investments performed (best / worst performers, today or over a period, gains and losses) is the Holdings_Performance tool — never the investments table, which holds trades, not performance.
When a question follows up on the conversation so far ("more details", "why", "which ones"), answer about that same topic and period: after a budget answer, "more details" means Category_Transactions for the categories most over budget.
The user does NOT see tool results — only your Final Answer. Put the figures and rows into the Final Answer itself; never say "listed above".

RULES:
- Privacy: Web_Search sends its input to an external search engine, not the user's private database. NEVER put specific amounts, balances, account/card numbers, or personal names from the database into a Web_Search query — only generic public terms (e.g. a ticker, a company name, a general topic).
- Use only SELECT statements — never INSERT, UPDATE, DELETE, DROP, or DDL.
- NEVER use SELECT * — always list explicit column names.
- NEVER select the 'embedding' column — it is a 768-float vector that will overflow your context window.
- NEVER wrap SQL in backticks or markdown code fences — write raw SQL only.
- For date ranges always use >= / < comparisons: e.g. "May 2026" → WHERE date >= '2026-05-01' AND date < '2026-06-01'. NEVER compare DATE_TRUNC() to a partial date string like '2026-05' — that is invalid SQL.
- For "this year" use DATE_TRUNC('year', CURRENT_DATE). For "this month" use DATE_TRUNC('month', CURRENT_DATE).
- Always give aggregated columns a clear alias (e.g. SUM(amount) AS total).
- Match text filters case-insensitively with ILIKE or LOWER().
- ALWAYS use aggregate functions (SUM, COUNT, AVG) when answering totals or summaries. NEVER return raw individual rows for a totals question — always GROUP BY or use a single aggregate.
- HAVING can only reference aggregated expressions or GROUP BY columns. Never use HAVING with a bare non-aggregated column like `HAVING transactions_id IS NOT NULL` — use WHERE instead.
- NEVER write placeholder values in SQL such as [insert X here] or ? or :param — always write complete, directly executable SQL with real values or remove the filter entirely if the value is unknown.
- For questions about which securities were bought/sold, join: investments i JOIN securities s ON s.Securities_Id = i.Securities_Id. Security name is s.Securities_Name (NOT i.securities_name — investments has no such column). The securities table has: Securities_Id, Securities_Name, Ticker, Securities_Type.
- For investment activity, filter by Action (an enum — only these values exist: Buy, Sell, Dividend, Reinvest, Split, ShrIn, ShrOut, IntInc, CashIn, CashOut, Grant, Vest, Exercise, Expire, MiscExp, RtrnCap, MiscInc): 'Buy' for purchases, 'Sell' for sales, 'Dividend' for dividends. Never filter by accounts_id unless the user explicitly asked about a specific account.
- A typical "which securities did I buy in period X" query: SELECT s.Securities_Name, s.Ticker, SUM(i.Total_Amount_AccCur) AS total FROM investments i JOIN securities s ON s.Securities_Id = i.Securities_Id WHERE i.date >= 'YYYY-MM-DD' AND i.date < 'YYYY-MM-DD' AND i.Action = 'Buy' GROUP BY s.Securities_Name, s.Ticker ORDER BY total DESC LIMIT 20
- Limit to 20 rows for list queries. For totals, return a single aggregated row.
- You have the full schema above — do NOT call sql_db_list_tables or sql_db_schema.

You have access to these tools:"""

    custom_suffix = """Begin!

Question: {input}
{agent_scratchpad}"""

    agent_executor = create_sql_agent(
        llm=llm,
        db=db,
        extra_tools=[budget_tool, category_tool, category_tx_tool, perf_tool, value_tool, nw_history_tool, nw_sources_tool, reduce_tool, *more_tools, pnl_tool, rag_tool, web_search_tool],
        agent_type="zero-shot-react-description",
        verbose=True,
        prefix=custom_prefix,
        suffix=custom_suffix,
        max_iterations=5,
        allow_dangerous_requests=True,
        # handle_parsing_errors/return_intermediate_steps are AgentExecutor
        # constructor fields, not create_sql_agent's own named params — passing
        # them as bare top-level kwargs silently routes them into **kwargs meant
        # for agent *construction* instead, where they're swallowed and do
        # nothing (confirmed: result["intermediate_steps"] came back completely
        # missing despite this flag). agent_executor_kwargs is create_sql_agent's
        # documented escape hatch straight into AgentExecutor's own constructor.
        agent_executor_kwargs={
            # Told what was wrong — the default message made llama3.1:8b repeat the same prose answer three times.
            "handle_parsing_errors": "Your reply had no 'Action:' line. If you already have the answer, reply with 'Final Answer: <the answer>'. Otherwise give 'Action: <tool name>' and 'Action Input: <input>'.",
            "return_intermediate_steps": True,
        },
    )

    return agent_executor