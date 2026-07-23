from sqlalchemy import inspect as sa_inspect
from langchain_community.agent_toolkits import create_sql_agent
from langchain_community.agent_toolkits.sql.toolkit import SQLDatabaseToolkit
from langchain_core.tools import Tool

from ai.web_search import web_search


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
    # Hard cap: inject LIMIT 20 on plain SELECT queries that have no aggregate
    # and no explicit LIMIT — prevents small models returning thousands of raw rows.
    if re.match(r'^\s*SELECT\b', sql, re.IGNORECASE):
        has_aggregate = bool(re.search(r'\b(SUM|COUNT|AVG|MIN|MAX)\s*\(', sql, re.IGNORECASE))
        has_limit = bool(re.search(r'\bLIMIT\b', sql, re.IGNORECASE))
        if not has_aggregate and not has_limit:
            sql = sql + ' LIMIT 20'
    return sql


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
        description="Use for financial concepts, definitions, or qualitative analysis not answerable from the database.",
    )

    web_search_tool = Tool(
        name="Web_Search",
        func=lambda q: web_search(q),
        description=(
            "Use for anything requiring current, real-world information not in the user's "
            "database or the Financial_Knowledge_Base — e.g. today's price of a stock/crypto, "
            "recent news, or general facts. Input is a plain-text search query."
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

RULES:
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
- For investment activity, filter by Action using ILIKE or exact string: 'Buy'/'BuyX' for purchases, 'Sell'/'SellX' for sales, 'Dividend'/'DivX' for income. Never filter by accounts_id unless the user explicitly asked about a specific account.
- A typical "which securities did I buy in period X" query: SELECT s.Securities_Name, s.Ticker, SUM(i.Total_Amount_AccCur) AS total FROM investments i JOIN securities s ON s.Securities_Id = i.Securities_Id WHERE i.date >= 'YYYY-MM-DD' AND i.date < 'YYYY-MM-DD' AND i.Action IN ('Buy','BuyX') GROUP BY s.Securities_Name, s.Ticker ORDER BY total DESC LIMIT 20
- Limit to 20 rows for list queries. For totals, return a single aggregated row.
- You have the full schema above — do NOT call sql_db_list_tables or sql_db_schema.

You have access to these tools:"""

    custom_suffix = """Begin!

Question: {input}
{agent_scratchpad}"""

    agent_executor = create_sql_agent(
        llm=llm,
        db=db,
        extra_tools=[rag_tool, web_search_tool],
        agent_type="zero-shot-react-description",
        verbose=True,
        prefix=custom_prefix,
        suffix=custom_suffix,
        handle_parsing_errors=True,
        max_iterations=3,
        allow_dangerous_requests=True,
        return_intermediate_steps=True,
    )

    return agent_executor