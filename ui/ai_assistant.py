import re
import decimal
import datetime
import streamlit as st
from ai.agent import _compact_schema
from database.connection import get_connection
from langchain_community.chat_message_histories import StreamlitChatMessageHistory
from ai.rag import semantic_search
from ai.web_search import web_search
from ai.update_vector import update_all_embeddings
from config.settings import ENV_CONFIG

def _classify_question(user_input: str) -> str:
    """Classify without an LLM call — saves a full round-trip on slow hardware."""
    text = user_input.lower()

    web_keywords = [
        "current price", "stock price", "share price", "today's price",
        "market news", "latest news", "news about", "eur/usd", "usd/eur",
        "exchange rate today", "crypto", "bitcoin", "live price",
        "what is the price", "how is the market",
    ]
    if any(kw in text for kw in web_keywords):
        return "WEB"

    general_keywords = [
        "hello", "hi ", "hey ", "good morning", "good afternoon", "good evening",
        "thank", "what can you", "how are you", "nice to meet", "help me understand",
        "what is a", "what is an", "explain ", "define ",
        # financial advice / open-ended questions — no DB query will help here
        "advice", "tips ", "tip for", "options for", "explore option",
        "saving money", "save money", "how to save", "how should i", "what should i",
        "suggest", "recommend", "strategies", "improve my finance", "improve my budget",
        "help me plan", "financial plan", "financial goal", "reduce expense",
        "cut cost", "cut spending", "increase income", "pay off debt",
        "should i invest", "is it a good idea", "what do you think",
        # conversational / control phrases
        "re-try", "retry", "try again", "please try", "can you try",
        "please re", "rephrase", "help me", "can you help",
    ]
    if any(kw in text for kw in general_keywords):
        return "GENERAL"

    # Everything else is treated as a database question — better to try SQL and fail
    # gracefully than to incorrectly refuse a finance question.
    return "SQL"


def _fmt_cell(v) -> str:
    """Format a single cell value for display."""
    if v is None:
        return ""
    if isinstance(v, decimal.Decimal):
        return f"{v:,.2f}"
    if isinstance(v, (datetime.date, datetime.datetime)):
        return str(v)
    return str(v)


def _to_markdown_table(colnames: list, rows: list, max_rows: int = 200) -> str:
    """Render query results as a GitHub-flavoured markdown table."""
    if not rows:
        return "No matching records found."
    truncated = len(rows) > max_rows
    display = rows[:max_rows]
    header = "| " + " | ".join(colnames) + " |"
    sep    = "| " + " | ".join(["---"] * len(colnames)) + " |"
    body   = "\n".join(
        "| " + " | ".join(_fmt_cell(c) for c in row) + " |"
        for row in display
    )
    note = f"\n\n*Showing first {max_rows} of {len(rows)} rows.*" if truncated else ""
    return f"{header}\n{sep}\n{body}{note}"


def _run_sql(user_input: str, full_date: str, llm, db) -> str:
    """Generate SQL → execute with real cursor → format result. At most 2 LLM calls."""
    if db is None:
        return "Sorry, I couldn't reach the database."

    schema_info = _compact_schema(db)

    sql_prompt = (
        f"Today is {full_date}.\n"
        f"PostgreSQL expert. Write ONE SELECT query for the question below.\n"
        f"Rules:\n"
        f"- ILIKE for text matching\n"
        f"- DATE_TRUNC for period filters\n"
        f"- Always JOIN to get human-readable names "
        f"(e.g. JOIN payees ON payees.payees_id = transactions.payees_id to show payees_name)\n"
        f"- Alias every aggregate column\n"
        f"- LIMIT 200 unless the user asks for everything\n"
        f"- NEVER select the 'embedding' column — it is a 768-float vector and will break output\n"
        f"- NEVER use SELECT * — always list explicit columns\n"
        f"Output the SQL query ONLY — no prose, no markdown fences.\n\n"
        f"SCHEMA:\n{schema_info}\n\n"
        f"QUESTION: {user_input}"
    )

    try:
        sql = llm.invoke(sql_prompt).content.strip()
        sql = re.sub(r"^```[a-z]*\n?", "", sql, flags=re.IGNORECASE)
        sql = re.sub(r"\n?```$", "", sql)
        sql = sql.strip()
    except Exception as e:
        return f"I couldn't generate a SQL query: {str(e)[:150]}"

    # Execute with a real cursor so we get column names
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(sql)
        colnames = [desc[0] for desc in cur.description]
        rows = cur.fetchall()
        cur.close()
    except Exception as e:
        return f"I tried to query the database but encountered an error: {str(e)[:200]}"
    finally:
        if conn:
            conn.close()

    if not rows:
        return "No matching records found."

    # Single scalar (e.g. SUM, COUNT) — plain answer, no LLM needed
    if len(rows) == 1 and len(colnames) == 1:
        return f"**{_fmt_cell(rows[0][0])}**"

    # Small aggregation (a few labelled values) — plain answer
    if len(rows) <= 5 and len(colnames) <= 2:
        lines = [" | ".join(_fmt_cell(c) for c in row) for row in rows]
        return "\n".join(lines)

    # List / detail result — render as markdown table, no LLM needed
    return _to_markdown_table(colnames, rows)


def run_ai_assistant(user_input: str, llm, agent_with_history, rag_engine, db=None) -> str:
    """Route question to appropriate handler."""
    import datetime as dt_lib
    full_date = dt_lib.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    full_prompt = f"Today is {full_date}. {user_input}"

    # Keyword-based routing — zero LLM calls, instant
    decision = _classify_question(user_input)

    if decision == "SQL":
        st.info("🗄️ Querying your database...")
        result = _run_sql(user_input, full_date, llm, db)
        if not result.startswith("I tried to query") and not result.startswith("Sorry"):
            return result

        # SQL failed → semantic search fallback
        st.info("🔍 Trying semantic search...")
        semantic_results = semantic_search(user_input)
        if not semantic_results.startswith("No ") and not semantic_results.startswith("Could not") and not semantic_results.startswith("Semantic"):
            synthesis_prompt = (
                f"Today is {full_date}.\n"
                f"The user asked: {user_input}\n\n"
                f"Here are the most relevant transactions found:\n{semantic_results}\n\n"
                f"Using only the transactions above, answer the user's question concisely."
            )
            try:
                return llm.invoke(synthesis_prompt).content
            except Exception:
                return semantic_results  # Return raw results if LLM synthesis fails

        # Last resort: direct LLM with context about what failed
        st.warning("Could not find a precise database answer — answering from general knowledge...")
        fallback_prompt = (
            f"You are a personal finance assistant. Today is {full_date}.\n"
            f"The user asked: {user_input}\n\n"
            f"You could not find a specific database answer. "
            f"Give a helpful response: if this is a question about their personal data "
            f"(transactions, balances, investments), ask them to be more specific "
            f"(e.g. mention an account name, date range, or category). "
            f"If it is a general finance question, answer it directly."
        )
        try:
            return llm.invoke(fallback_prompt).content
        except Exception as e:
            return (
                "I wasn't able to find a precise answer in the database. "
                "Try rephrasing with specific details — for example: "
                "'Show my expenses in January 2026' or 'What is the balance of account X?'"
            )

    elif decision == "WEB":
        st.info("🌐 Searching the web...")
        web_results = web_search(user_input)
        synthesis_prompt = (
            f"Today is {full_date}.\n"
            f"User asked: {user_input}\n\n"
            f"Web results:\n{web_results}\n\n"
            f"Answer clearly and concisely."
        )
        return llm.invoke(synthesis_prompt).content

    else:
        st.info("🤖 Answering directly...")
        general_prompt = (
            f"You are a personal finance assistant helping a user manage their finances. "
            f"Today is {full_date}.\n"
            f"Answer helpfully and stay focused on personal finance topics. "
            f"If greeted, introduce yourself as a personal finance assistant and briefly list what you can help with "
            f"(e.g. querying transactions, portfolio analysis, market prices, budgeting advice).\n\n"
            f"User: {user_input}"
        )
        return llm.invoke(general_prompt).content


def render_ai_assistant(llm, agent_with_history, rag_engine, db=None):
    """Render the AI Assistant page."""
    st.title("🧠 AI Assistant")
    st.caption(f"Powered by {ENV_CONFIG['ollama_model']} · Fully local · Your data stays on your Pi")
    
    # Add helpful examples
    with st.expander("📋 Example Questions"):
        st.markdown("""
        **Database Questions (SQL):**
        - Show me all my bank transactions from last month
        - What's my current net worth?
        - List all my investments in my brokerage account
        - Show me my expenses by category for this year
        
        **Web Search Questions:**
        - What's the current price of AAPL stock?
        - Latest news about the stock market
        - Latest news about the crypto market
        - EUR/USD exchange rate today
        
        **General Questions:**
        - What can you help me with?
        - Analyze my investment portfolio
        - Analyze my income and expense transactions
        - Give me financial advice based on my data
        """)
    
    # Embeddings status + update button
    try:
        from database.connection import get_connection as _gc
        _conn = _gc()
        _cur = _conn.cursor()
        # Count only transactions that are actually embeddable: must have at least
        # one non-zero split (same inner-join + filter logic as update_embeddings).
        _cur.execute("""
            SELECT COUNT(DISTINCT t.transactions_id)
            FROM Transactions t
            JOIN Splits s ON s.transactions_id = t.transactions_id
            WHERE t.total_amount <> 0
              AND s.Amount       <> 0
        """)
        _total = _cur.fetchone()[0]
        _cur.execute("""
            SELECT COUNT(*)
            FROM Transactions t
            JOIN Splits s ON s.transactions_id = t.transactions_id
            WHERE t.embedding  IS NOT NULL
              AND t.total_amount <> 0
              AND s.Amount        <> 0
        """)
        _embedded = _cur.fetchone()[0]
        _cur.close()
        _conn.close()
        if _embedded == 0:
            st.warning(
                f"⚠️ No transaction embeddings found ({_total:,} embeddable transactions). "
                "Click 'Update Embeddings' to enable semantic search fallback."
            )
        elif _embedded < _total:
            st.info(f"🔍 Semantic search ready — {_embedded:,}/{_total:,} transactions indexed.")
        else:
            st.success(f"✅ Semantic search ready — all {_total:,} transactions indexed.")
    except Exception:
        pass

    st.button("🔄 Update Embeddings", on_click=update_all_embeddings,
              help="Run after importing new transactions to keep semantic search up to date.")
    
    col1, col2 = st.columns([6, 1])
    with col2:
        if st.button("🗑️ Clear chat"):
            msgs = StreamlitChatMessageHistory(key="sql_agent_history")
            msgs.clear()
            st.rerun()
    
    msgs = StreamlitChatMessageHistory(key="sql_agent_history")
    for msg in msgs.messages:
        st.chat_message(msg.type).write(msg.content)
    
    user_input = st.chat_input("Ask about your finances, holdings, transactions, or market news...")
    
    if user_input:
        st.chat_message("user").write(user_input)
        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                try:
                    answer = run_ai_assistant(user_input, llm, agent_with_history, rag_engine, db=db)
                    st.write(answer)
                    
                    # Save to history
                    msgs.add_user_message(user_input)
                    msgs.add_ai_message(answer)
                except Exception as e:
                    error_msg = f"Sorry, I encountered an error: {str(e)[:200]}"
                    st.error(error_msg)
                    msgs.add_user_message(user_input)
                    msgs.add_ai_message(error_msg)