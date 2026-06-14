import streamlit as st
from langchain_community.chat_message_histories import StreamlitChatMessageHistory
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_core.chat_history import BaseChatMessageHistory

from config.settings import ENV_CONFIG
from utils.session_state import init_session_state
from utils.helpers import configure_warnings_and_ssl
from ai.llm import init_llm
from ai.rag import PgVectorRagEngine
from ai.agent import create_ai_agent
from database.connection import get_connection, get_sql_database
from ui.dashboard import render_dashboard
from ui.register import render_register
from ui.reports import render_reports
from ui.market_data import render_market_data
from ui.ai_assistant import render_ai_assistant
from ui.tools import render_tools
from ui.importers import render_importers
from ui.static_data import render_static_data
from ui.recurring import render_recurring

@st.cache_resource
def startup_db_maintenance():
    conn = get_connection()
    with conn.cursor() as cursor:
        cursor.execute("ANALYZE;")
        # Normalise sentinel strings that older downloader runs may have written
        # into text columns that should be NULL when no real value exists.
        cursor.execute("""
            UPDATE Securities
            SET Analyst_Rating = NULL
            WHERE LOWER(TRIM(Analyst_Rating)) IN ('none', 'n/a', 'na', '');
        """)
        # Drop Wikipedia columns that were added experimentally and are no longer used.
        cursor.execute("ALTER TABLE Institutions DROP COLUMN IF EXISTS Wikipedia_Title;")
        cursor.execute("ALTER TABLE Institutions DROP COLUMN IF EXISTS Ratings_Updated;")
        # Tax-exempt flag for securities whose income is not subject to income tax
        # (e.g. Hellenic T-Bills purchased at primary market).
        cursor.execute("""
            ALTER TABLE Securities
            ADD COLUMN IF NOT EXISTS Is_Tax_Exempt BOOLEAN DEFAULT FALSE
        """)
        # Instrument type at transaction level — captures the exact traded instrument
        # (e.g. CFDOnETF, CFDOnStock, CFDOnIndex, CFDOnFutures, CFDOnFund) independently of the security master.
        # Useful for Saxo Bank imports where the same underlying security may be traded
        # as a regular ETF on one occasion and a CFD on another.
        cursor.execute("""
            ALTER TABLE Investments
            ADD COLUMN IF NOT EXISTS Instrument_Type VARCHAR(50)
        """)
        # ── Migration 005: Recurring Templates ───────────────────────────────
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS Recurring_Templates (
                Templates_Id        SERIAL PRIMARY KEY,
                Name                VARCHAR(100) NOT NULL,
                Accounts_Id         INTEGER NOT NULL REFERENCES Accounts(Accounts_Id) ON DELETE CASCADE,
                Payees_Id           INTEGER REFERENCES Payees(Payees_Id),
                Description         TEXT,
                Total_Amount        NUMERIC(28, 18),
                Periodicity         VARCHAR(20) NOT NULL DEFAULT 'Monthly',
                Next_Due_Date       DATE NOT NULL,
                End_Date            DATE,
                Auto_Confirm        BOOLEAN DEFAULT FALSE,
                Active              BOOLEAN DEFAULT TRUE,
                Accounts_Id_Target  INTEGER REFERENCES Accounts(Accounts_Id),
                Created_At          TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS Recurring_Template_Splits (
                Splits_Id       SERIAL PRIMARY KEY,
                Templates_Id    INTEGER NOT NULL
                    REFERENCES Recurring_Templates(Templates_Id) ON DELETE CASCADE,
                Categories_Id   INTEGER REFERENCES Categories(Categories_Id),
                Amount          NUMERIC(28, 18),
                Memo            TEXT
            )
        """)
        cursor.execute("""
            ALTER TABLE Transactions
                ADD COLUMN IF NOT EXISTS Is_Draft   BOOLEAN DEFAULT FALSE
        """)
        cursor.execute("""
            ALTER TABLE Transactions
                ADD COLUMN IF NOT EXISTS Templates_Id INTEGER
                    REFERENCES Recurring_Templates(Templates_Id) ON DELETE SET NULL
        """)
        # Draft-aware balance trigger (replaces existing function in-place)
        cursor.execute("""
            CREATE OR REPLACE FUNCTION public.update_accounts_balance_with_transfer()
                RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF (TG_OP = 'INSERT') THEN
                    IF NEW.Is_Draft THEN RETURN NULL; END IF;
                    UPDATE Accounts SET Accounts_Balance = Accounts_Balance + NEW.Total_Amount
                     WHERE Accounts_Id = NEW.Accounts_Id;
                    IF NEW.Accounts_Id_Target IS NOT NULL THEN
                        UPDATE Accounts
                           SET Accounts_Balance = Accounts_Balance + COALESCE(NEW.Total_Amount_Target, -NEW.Total_Amount)
                         WHERE Accounts_Id = NEW.Accounts_Id_Target;
                    END IF;
                ELSIF (TG_OP = 'DELETE') THEN
                    IF OLD.Is_Draft THEN RETURN NULL; END IF;
                    UPDATE Accounts SET Accounts_Balance = Accounts_Balance - OLD.Total_Amount
                     WHERE Accounts_Id = OLD.Accounts_Id;
                    IF OLD.Accounts_Id_Target IS NOT NULL THEN
                        UPDATE Accounts
                           SET Accounts_Balance = Accounts_Balance - COALESCE(OLD.Total_Amount_Target, -OLD.Total_Amount)
                         WHERE Accounts_Id = OLD.Accounts_Id_Target;
                    END IF;
                ELSIF (TG_OP = 'UPDATE') THEN
                    IF OLD.Is_Draft AND NEW.Is_Draft THEN
                        RETURN NULL;
                    ELSIF OLD.Is_Draft AND NOT NEW.Is_Draft THEN
                        UPDATE Accounts SET Accounts_Balance = Accounts_Balance + NEW.Total_Amount
                         WHERE Accounts_Id = NEW.Accounts_Id;
                        IF NEW.Accounts_Id_Target IS NOT NULL THEN
                            UPDATE Accounts
                               SET Accounts_Balance = Accounts_Balance + COALESCE(NEW.Total_Amount_Target, -NEW.Total_Amount)
                             WHERE Accounts_Id = NEW.Accounts_Id_Target;
                        END IF;
                    ELSIF NOT OLD.Is_Draft AND NEW.Is_Draft THEN
                        UPDATE Accounts SET Accounts_Balance = Accounts_Balance - OLD.Total_Amount
                         WHERE Accounts_Id = OLD.Accounts_Id;
                        IF OLD.Accounts_Id_Target IS NOT NULL THEN
                            UPDATE Accounts
                               SET Accounts_Balance = Accounts_Balance - COALESCE(OLD.Total_Amount_Target, -OLD.Total_Amount)
                             WHERE Accounts_Id = OLD.Accounts_Id_Target;
                        END IF;
                    ELSE
                        UPDATE Accounts SET Accounts_Balance = Accounts_Balance - OLD.Total_Amount
                         WHERE Accounts_Id = OLD.Accounts_Id;
                        UPDATE Accounts SET Accounts_Balance = Accounts_Balance + NEW.Total_Amount
                         WHERE Accounts_Id = NEW.Accounts_Id;
                    END IF;
                END IF;
                RETURN NULL;
            END;
            $$
        """)
        # Extend the securities_type ENUM with new instrument categories.
        # ADD VALUE IF NOT EXISTS is safe to run repeatedly; the label must be
        # a literal in the DDL statement (parameters are not accepted here).
        for _new_type in ('CFD', 'Closed-End Fund'):
            cursor.execute(
                f"ALTER TYPE securities_type ADD VALUE IF NOT EXISTS '{_new_type}'"
            )
    conn.commit()
    return True

_APP_CSS = """
<style>
/* ══════════════════════════════════════════════════════════════════════════
   Personal Finance App — Global CSS
   Sections:
     1. Mobile / responsive tweaks (preserved from original)
     2. Sidebar navigation styling
     3. Insight card CSS
     4. General polish (metrics, tabs, buttons, expanders)
   ══════════════════════════════════════════════════════════════════════════ */


/* ── 1. Page layout — reduce Streamlit's default excessive top padding ──── */

/* Streamlit defaults to ~5–6 rem top padding on the main block container.
   Pull the page title up so it sits near the top of the viewport. */
[data-testid="stMainBlockContainer"],
.block-container {
    padding-top: 1.25rem !important;
    padding-bottom: 2rem !important;
}


/* ── 2. Mobile / responsive tweaks ─────────────────────────────────────── */

/* Prevent iOS Safari from auto-zooming on input focus */
input, select, textarea,
.stTextInput > div > div > input,
.stNumberInput input,
.stTextArea textarea,
.stSelectbox > div > div > div,
.stMultiSelect > div > div > div {
    font-size: 16px !important;
}

/* Plotly: allow horizontal scroll on small screens */
[data-testid="stPlotlyChart"] > div {
    overflow-x: auto !important;
    -webkit-overflow-scrolling: touch;
}
.js-plotly-plot { min-width: 0; }

@media (max-width: 768px) {
    .main .block-container {
        padding-left: 0.75rem !important;
        padding-right: 0.75rem !important;
        padding-top: 1rem !important;
    }
    .stButton > button {
        min-height: 44px !important;
        font-size: 15px !important;
        padding: 8px 16px !important;
    }
    .stSelectbox > div,
    .stMultiSelect > div,
    .stTextInput > div,
    .stNumberInput > div { min-height: 44px !important; }
    .stTabs [data-baseweb="tab"] {
        padding: 6px 10px !important;
        font-size: 13px !important;
    }
    [data-testid="collapsedControl"] { width: 44px !important; height: 44px !important; }
    [data-testid="column"] { min-width: 140px; }
}


/* ── 2. Sidebar navigation ──────────────────────────────────────────────── */

/* Sidebar base */
[data-testid="stSidebar"] {
    border-right: 1px solid rgba(128,128,128,0.15) !important;
}
[data-testid="stSidebar"] > div:first-child {
    padding-top: 0.5rem !important;
}

/* App title in sidebar */
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] .stMarkdown h1 {
    font-size: 1.15rem !important;
    font-weight: 700 !important;
    padding: 0.4rem 0.6rem 0.4rem !important;
    border-bottom: 1px solid rgba(128,128,128,0.15) !important;
    margin-bottom: 0.25rem !important;
}

/* Hide ALL radio labels in the sidebar (both main nav and sub-nav) */
[data-testid="stSidebar"] [data-testid="stRadio"] > label {
    display: none !important;
}

/* ── Strip every layer of spacing Streamlit/BaseWeb adds around radio items ── */

/* 1. The radiogroup container itself */
[data-testid="stSidebar"] [role="radiogroup"] {
    gap: 0 !important;
    padding: 0 !important;
}

/* 2. BaseWeb wraps each item in a div — zero it out */
[data-testid="stSidebar"] [role="radiogroup"] > div {
    margin: 0 !important;
    padding: 0 !important;
    min-height: unset !important;
}

/* 3. The [data-baseweb="radio"] container */
[data-testid="stSidebar"] [data-baseweb="radio"] {
    margin: 0 !important;
    padding: 0 !important;
    min-height: unset !important;
    align-items: center !important;
}

/* 4. The label itself — tighten padding, keep the left-border affordance */
[data-testid="stSidebar"] [role="radiogroup"] label {
    display: flex !important;
    align-items: center !important;
    padding: 3px 6px 3px 10px !important;
    margin: 0 4px !important;
    border-radius: 5px !important;
    border-left: 3px solid transparent !important;
    cursor: pointer !important;
    transition: background-color 0.12s ease, border-left-color 0.12s ease !important;
    font-size: 12.5px !important;
    line-height: 1.25 !important;
    min-height: unset !important;
}

/* 5. Hide the radio circle dot (the first child div inside the label) */
[data-testid="stSidebar"] [role="radiogroup"] label > div:first-child {
    display: none !important;
}

/* Hover state — all sidebar radio groups */
[data-testid="stSidebar"] [role="radiogroup"] label:hover {
    background-color: rgba(74,144,217,0.08) !important;
}

/* Active / selected — all sidebar radio groups */
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {
    background-color: rgba(74,144,217,0.14) !important;
    border-left-color: #4a90d9 !important;
    font-weight: 600 !important;
    color: #4a90d9 !important;
}

/* ── Sidebar selectbox (e.g. "Select Report:") ──────────────────────────── */

/* Taller trigger box — only touch min-height to avoid breaking text rendering */
[data-testid="stSidebar"] [data-baseweb="select"] > div:first-child {
    min-height: 42px !important;
    border-radius: 8px !important;
}

/* Sidebar divider */
[data-testid="stSidebar"] hr {
    margin: 0.75rem 0.75rem !important;
    border-color: rgba(128,128,128,0.2) !important;
}

/* Sidebar buttons (Clear Cache, Recalculate P&L, etc.) */
[data-testid="stSidebar"] .stButton > button {
    border-radius: 6px !important;
    font-size: 12px !important;
    padding: 4px 10px !important;
    margin: 0 6px !important;
    min-height: unset !important;
}

/* Sidebar selectbox (Report picker) — compact */
[data-testid="stSidebar"] [data-testid="stSelectbox"] {
    margin: 2px 6px !important;
}
[data-testid="stSidebar"] [data-testid="stSelectbox"] label {
    font-size: 11px !important;
    margin-bottom: 1px !important;
}
[data-testid="stSidebar"] [data-testid="stSelectbox"] > div > div {
    min-height: unset !important;
    padding: 3px 8px !important;
    font-size: 12.5px !important;
}


/* ── 3. Insight cards ───────────────────────────────────────────────────── */

/* Ensure insight card markdown renders inline-style correctly */
.pf-insight-card { border-radius: 10px; }


/* ── 4. General polish ──────────────────────────────────────────────────── */

/* Metric cards: subtle border */
[data-testid="stMetric"] {
    border: 1px solid rgba(128,128,128,0.12);
    border-radius: 10px !important;
    padding: 10px 14px !important;
}

/* Tabs: larger hit target, consistent style */
.stTabs [data-baseweb="tab-list"] {
    gap: 4px !important;
}
.stTabs [data-baseweb="tab"] {
    border-radius: 8px 8px 0 0 !important;
    padding: 8px 18px !important;
    font-weight: 500 !important;
}
.stTabs [aria-selected="true"] {
    font-weight: 700 !important;
}

/* Primary buttons: rounder corners */
.stButton > button[kind="primary"] {
    border-radius: 8px !important;
    font-weight: 600 !important;
}
.stButton > button {
    border-radius: 8px !important;
}

/* Expanders: slightly rounded */
[data-testid="stExpander"] {
    border-radius: 10px !important;
    border: 1px solid rgba(128,128,128,0.15) !important;
}
[data-testid="stExpander"] > div:first-child {
    border-radius: 10px 10px 0 0 !important;
}

/* Data editor / dataframe: keep toolbar accessible */
/* NOTE: do NOT set overflow:auto/hidden on stDataFrame containers */

/* Alerts / info boxes */
[data-testid="stAlert"] {
    border-radius: 10px !important;
}

/* Plotly charts: consistent border */
[data-testid="stPlotlyChart"] {
    border-radius: 10px !important;
    overflow: hidden;
}

/* Page titles: no extra top margin (container padding already handles spacing) */
h1 { margin-top: 0 !important; }
</style>
"""

def main():
    """Main application entry point."""
    # Page configuration
    st.set_page_config(page_title="Personal Finance", layout="wide",
                       page_icon="💰", initial_sidebar_state="expanded")

    # Inject comprehensive app CSS
    st.markdown(_APP_CSS, unsafe_allow_html=True)


    # Initialize
    startup_db_maintenance()
    configure_warnings_and_ssl()
    init_session_state()
    
    # Initialize LLM and database
    # Cache the SQLDatabase so the SQLAlchemy engine (and its connection pool)
    # is reused across Streamlit reruns rather than rebuilt on every interaction.
    @st.cache_resource
    def _cached_sql_database():
        return get_sql_database()

    llm = init_llm()
    db = _cached_sql_database()
    
    # Initialize chat history
    msgs = StreamlitChatMessageHistory(key="sql_agent_history")
    
    def get_session_history(session_id: str) -> BaseChatMessageHistory:
        return msgs
    
    rag_engine = PgVectorRagEngine()

    agent_executor = create_ai_agent(llm, db, rag_engine)
    agent_with_history = RunnableWithMessageHistory(
        agent_executor,
        get_session_history,
        input_messages_key="input",
        history_messages_key="chat_history",
    )
    
    # ── Sidebar navigation ────────────────────────────────────────────────────
    st.sidebar.title("💰 Personal Finance")

    menu = st.sidebar.radio(
        "Menu",
        [
            "🏛️ Dashboard",
            "📝 Register",
            "🔁 Recurring",
            "⏳ Reports",
            "📋 Static Data",
            "🌍 Market Data",
            "📥 Importers",
            "🛠️ Tools",
            "🧠 AI Assistant",
        ],
        key="main_nav_menu",
    )

    st.sidebar.divider()

    if st.sidebar.button("🗑️ Clear Cache", key="clear_cache_btn", use_container_width=True):
        st.cache_data.clear()
        st.sidebar.success("Cache cleared.")

    # ── Page slot: st.empty() guarantees a clean slate on every navigation ───
    # st.empty() sends an explicit "clear this slot" delta to the frontend
    # before writing new content — unlike st.container() which relies on
    # React's reconciliation and can leave orphaned DOM nodes from complex
    # tab layouts (e.g. the 10-tab Investment Performance report).
    if "_page_slot" not in st.session_state:
        st.session_state["_page_slot"] = st.empty()
    _page_slot = st.session_state["_page_slot"]

    # Database connection for page rendering
    conn = get_connection()

    try:
        with _page_slot.container():
            if menu == "🏛️ Dashboard":
                render_dashboard(conn)
            elif menu == "📝 Register":
                render_register()
            elif menu == "⏳ Reports":
                render_reports()
            elif menu == "📋 Static Data":
                render_static_data()
            elif menu == "🌍 Market Data":
                render_market_data()
            elif menu == "📥 Importers":
                render_importers()
            elif menu == "🔁 Recurring":
                render_recurring()
            elif menu == "🛠️ Tools":
                render_tools(conn)
            elif menu == "🧠 AI Assistant":
                render_ai_assistant(llm, agent_with_history, rag_engine, db=db)

    finally:
        conn.close()

if __name__ == "__main__":
    main()