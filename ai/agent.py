from sqlalchemy import inspect as sa_inspect
from langchain_community.agent_toolkits import create_sql_agent
from langchain_community.agent_toolkits.sql.toolkit import SQLDatabaseToolkit
from langchain_core.tools import Tool


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


def create_ai_agent(llm, db, rag_engine):
    """Create the AI agent with SQL and RAG tools."""

    schema_info = _compact_schema(db)

    toolkit = SQLDatabaseToolkit(db=db, llm=llm)

    rag_tool = Tool(
        name="Financial_Knowledge_Base",
        func=lambda q: str(rag_engine.query(q)),
        description="Use for financial concepts, definitions, or qualitative analysis not answerable from the database.",
    )

    # IMPORTANT: zero-shot-react-description uses plain ReAct format, NOT JSON.
    # Format must be:
    #   Action: <tool_name>
    #   Action Input: <input>
    # Using JSON format here causes output parsing errors with this agent type.
    custom_prefix = f"""You are a personal finance assistant with direct read access to the user's own financial database.
This is the user's personal data — always answer questions about it freely and helpfully. Never refuse.

DATABASE SCHEMA:
{schema_info}

RULES:
- Use only SELECT statements — never INSERT, UPDATE, DELETE, DROP, or DDL.
- NEVER use SELECT * — always list explicit column names.
- NEVER select the 'embedding' column — it is a 768-float vector that will overflow your context window.
- NEVER wrap SQL in backticks or markdown code fences — write raw SQL only.
- For "this year" use DATE_TRUNC('year', CURRENT_DATE). For "this month" use DATE_TRUNC('month', CURRENT_DATE).
- Always give aggregated columns a clear alias (e.g. SUM(amount) AS total).
- Match text filters case-insensitively with ILIKE or LOWER().
- Limit to 100 rows unless the user asks for everything.
- You have the full schema above — do NOT call sql_db_list_tables or sql_db_schema.

You have access to these tools:"""

    custom_suffix = """Begin!

Question: {input}
{agent_scratchpad}"""

    agent_executor = create_sql_agent(
        llm=llm,
        db=db,
        extra_tools=[rag_tool],
        agent_type="zero-shot-react-description",
        verbose=True,
        prefix=custom_prefix,
        suffix=custom_suffix,
        handle_parsing_errors=True,
        max_iterations=3,
        allow_dangerous_requests=True,
    )

    return agent_executor