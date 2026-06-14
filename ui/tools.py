import re
import streamlit as st
import pandas as pd
from database.backup import render_backup_restore
from database.connection import get_connection
from database.queries import (get_price_anomalies, get_missing_tx_prices, get_investments_with_dummy_prices,
                               get_investment_consistency_data, update_investment_row,
                               get_all_securities, get_split_preview, export_all_data)
from database.crud import delete_historical_prices, insert_prices_from_transactions, normalize_investment_prices, update_accounts_balances, update_investment_balances, apply_stock_split
from ui.components import copy_df_button


# DDL operations that are always blocked (schema-destructive)
_DDL_PATTERN = re.compile(
    r"^\s*(DROP|TRUNCATE|ALTER|CREATE|REPLACE|MERGE|GRANT|REVOKE|CALL|DO)\b",
    re.IGNORECASE,
)

# DML operations that are allowed (data modifications, committed immediately)
_DML_PATTERN = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE)\b",
    re.IGNORECASE,
)


def _run_maintenance(sql: str, label: str):
    """Execute a maintenance command (VACUUM / REINDEX / ANALYZE) with autocommit."""
    conn = get_connection()
    try:
        conn.autocommit = True          # required — these cannot run inside a transaction
        with conn.cursor() as cur:
            cur.execute(sql)
        st.success(f"✅ {label} completed successfully.")
    except Exception as e:
        st.error(f"❌ {label} failed: {e}")
    finally:
        conn.close()


def _render_db_maintenance():
    st.subheader("🔧 Database Maintenance")
    st.caption(
        "VACUUM reclaims storage from dead tuples. "
        "ANALYZE updates planner statistics. "
        "REINDEX rebuilds indexes to remove bloat. "
        "All operations run with autocommit — no transaction wrapper needed."
    )

    # ── Table health overview ──────────────────────────────────────────────────
    st.markdown("#### 📊 Table Health")
    conn = get_connection()
    df_health = pd.read_sql("""
        SELECT
            relname                                         AS table_name,
            pg_size_pretty(pg_total_relation_size(relid))  AS total_size,
            pg_size_pretty(pg_relation_size(relid))        AS table_size,
            pg_size_pretty(pg_total_relation_size(relid)
                         - pg_relation_size(relid))        AS index_size,
            n_live_tup                                     AS live_rows,
            n_dead_tup                                     AS dead_rows,
            CASE WHEN n_live_tup > 0
                 THEN ROUND(n_dead_tup::numeric / n_live_tup * 100, 1)
                 ELSE 0 END                                AS dead_pct,
            last_vacuum,
            last_autovacuum,
            last_analyze,
            last_autoanalyze
        FROM pg_stat_user_tables
        ORDER BY n_dead_tup DESC, pg_total_relation_size(relid) DESC
    """, conn)
    conn.close()

    if not df_health.empty:
        for col in ['last_vacuum', 'last_autovacuum', 'last_analyze', 'last_autoanalyze']:
            df_health[col] = pd.to_datetime(df_health[col]).dt.strftime('%Y-%m-%d %H:%M').fillna('—')

        bloat_tables = df_health[df_health['dead_pct'] > 10]
        if not bloat_tables.empty:
            st.warning(
                f"⚠️ {len(bloat_tables)} table(s) have >10 % dead rows — consider running VACUUM ANALYZE."
            )

        st.dataframe(
            df_health,
            hide_index=True,
            width='stretch',
            column_config={
                'table_name':      st.column_config.TextColumn('Table'),
                'total_size':      'Total Size',
                'table_size':      'Table Size',
                'index_size':      'Index Size',
                'live_rows':       st.column_config.NumberColumn('Live Rows',  format='%,d'),
                'dead_rows':       st.column_config.NumberColumn('Dead Rows',  format='%,d'),
                'dead_pct':        st.column_config.NumberColumn('Dead %',     format='%.1f%%'),
                'last_vacuum':     'Last Vacuum',
                'last_autovacuum': 'Last Auto-Vacuum',
                'last_analyze':    'Last Analyze',
                'last_autoanalyze':'Last Auto-Analyze',
            },
        )
        copy_df_button(df_health, key="dl_tools_health")

    # ── Index health ───────────────────────────────────────────────────────────
    with st.expander("🗂 Index Usage"):
        conn = get_connection()
        df_idx = pd.read_sql("""
            SELECT
                relname                                         AS table_name,
                indexrelname                                    AS index_name,
                pg_size_pretty(pg_relation_size(indexrelid))   AS index_size,
                idx_scan                                        AS scans,
                idx_tup_read                                    AS tuples_read,
                idx_tup_fetch                                   AS tuples_fetched
            FROM pg_stat_user_indexes
            ORDER BY idx_scan ASC, pg_relation_size(indexrelid) DESC
        """, conn)
        conn.close()
        st.caption("Indexes sorted by scan count ascending — low-scan large indexes may be candidates for review.")
        st.dataframe(
            df_idx, hide_index=True, width='stretch',
            column_config={
                'table_name':     'Table',
                'index_name':     'Index',
                'index_size':     'Size',
                'scans':          st.column_config.NumberColumn('Scans',           format='%,d'),
                'tuples_read':    st.column_config.NumberColumn('Tuples Read',     format='%,d'),
                'tuples_fetched': st.column_config.NumberColumn('Tuples Fetched',  format='%,d'),
            },
        )
        copy_df_button(df_idx, key="dl_tools_idx")

    st.divider()

    # ── Database-wide operations ───────────────────────────────────────────────
    st.markdown("#### ⚡ Database-Wide Operations")
    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown("**ANALYZE**")
        st.caption("Updates planner statistics for all tables. Fast, safe, no locking.")
        if st.button("▶ Run ANALYZE", key="maint_analyze", width='stretch'):
            _run_maintenance("ANALYZE;", "ANALYZE")
            st.rerun()

    with col2:
        st.markdown("**VACUUM ANALYZE**")
        st.caption("Reclaims dead rows and updates statistics. Safe to run on a live database.")
        if st.button("▶ Run VACUUM ANALYZE", key="maint_vacuum_analyze", width='stretch'):
            _run_maintenance("VACUUM ANALYZE;", "VACUUM ANALYZE")
            st.rerun()

    with col3:
        st.markdown("**REINDEX DATABASE**")
        st.caption("Rebuilds all indexes. May take minutes on large databases.")
        if st.button("▶ Run REINDEX DATABASE", key="maint_reindex_db", width='stretch'):
            from config.settings import ENV_CONFIG
            _run_maintenance(
                f'REINDEX DATABASE "{ENV_CONFIG["db_name"]}";',
                "REINDEX DATABASE"
            )
            st.rerun()

    st.divider()

    # ── Balance recalculation ──────────────────────────────────────────────────
    st.markdown("#### 💰 Recalculate Account Balances")
    st.caption(
        "Recomputes `Accounts_Balance` for all accounts from their transactions and investment entries. "
        "Run this if any account balance looks incorrect — e.g. after importing data, running fix tools, "
        "or if an 'Other Investment' or Pension account shows zero in the Net Worth Report."
    )
    rb_col1, rb_col2, rb_col3 = st.columns(3)
    with rb_col1:
        st.markdown("**Cash / Bank / Assets**")
        st.caption("Updates all non-investment accounts from their Transactions.")
        if st.button("▶ Recalculate Cash Balances", key="maint_recalc_cash", width="stretch"):
            update_accounts_balances()
            st.success("✅ Cash / Bank / Asset balances recalculated.")
    with rb_col2:
        st.markdown("**Brokerage / Other Investment**")
        st.caption("Updates Brokerage, Other Investment, and Margin accounts from Investments + Transactions.")
        if st.button("▶ Recalculate Investment Balances", key="maint_recalc_inv", width="stretch"):
            update_investment_balances()
            st.success("✅ Investment account balances recalculated.")
    with rb_col3:
        st.markdown("**Pension**")
        st.caption("Updates Pension accounts from their CashIn / CashOut investment entries.")
        if st.button("▶ Recalculate Pension Balances", key="maint_recalc_pen", width="stretch"):
            from database.crud import update_pension_balances
            update_pension_balances()
            st.success("✅ Pension account balances recalculated.")

    st.divider()

    # ── Per-table operations ───────────────────────────────────────────────────
    st.markdown("#### 🎯 Per-Table Operations")

    if not df_health.empty:
        table_names = df_health['table_name'].tolist()
        sel_col, op_col, run_col = st.columns([3, 2, 1])

        with sel_col:
            sel_table = st.selectbox(
                "Table", table_names, key="maint_table_sel",
                label_visibility="collapsed",
            )
        with op_col:
            sel_op = st.selectbox(
                "Operation",
                ["VACUUM ANALYZE", "VACUUM", "ANALYZE", "REINDEX TABLE", "VACUUM FULL"],
                key="maint_op_sel",
                label_visibility="collapsed",
            )
        with run_col:
            run_btn = st.button("▶ Run", key="maint_run_table", type="primary", width='stretch')

        if sel_op == "VACUUM FULL":
            st.warning(
                "⚠️ **VACUUM FULL** rewrites the entire table and holds an exclusive lock "
                "for the duration — no reads or writes are possible during this time. "
                "Only use it when dead-row bloat is severe and downtime is acceptable."
            )

        if run_btn:
            if sel_op == "REINDEX TABLE":
                sql = f'REINDEX TABLE "{sel_table}";'
            else:
                sql = f'{sel_op} "{sel_table}";'
            _run_maintenance(sql, f"{sel_op} on {sel_table}")
            st.rerun()
    else:
        st.info("No table statistics available.")

    st.divider()

    # ── Referential integrity check ────────────────────────────────────────────
    st.markdown("#### 🔗 Referential Integrity Check")
    st.caption(
        "Scans every foreign-key constraint in the public schema and counts orphaned rows "
        "(child rows whose referenced parent row no longer exists)."
    )

    ri_col, _ = st.columns([1, 4])
    with ri_col:
        run_ri = st.button("🔍 Run Integrity Check", key="maint_ri_check", width='stretch')

    if run_ri:
        conn = get_connection()
        try:
            # Discover all FK constraints (single-column FKs — covers all standard cases)
            df_fks = pd.read_sql("""
                SELECT
                    tc.constraint_name,
                    tc.table_name        AS child_table,
                    kcu.column_name      AS child_col,
                    ccu.table_name       AS parent_table,
                    ccu.column_name      AS parent_col
                FROM information_schema.table_constraints        tc
                JOIN information_schema.key_column_usage         kcu
                    ON  kcu.constraint_name = tc.constraint_name
                    AND kcu.table_schema    = tc.table_schema
                JOIN information_schema.constraint_column_usage  ccu
                    ON  ccu.constraint_name = tc.constraint_name
                    AND ccu.table_schema    = tc.table_schema
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND tc.table_schema    = 'public'
                ORDER BY tc.table_name, tc.constraint_name
            """, conn)

            results = []
            cur = conn.cursor()
            for _, fk in df_fks.iterrows():
                child_t  = fk['child_table']
                child_c  = fk['child_col']
                parent_t = fk['parent_table']
                parent_c = fk['parent_col']
                try:
                    cur.execute(f"""
                        SELECT COUNT(*) FROM "{child_t}" c
                        WHERE c."{child_c}" IS NOT NULL
                          AND NOT EXISTS (
                              SELECT 1 FROM "{parent_t}" p
                              WHERE p."{parent_c}" = c."{child_c}"
                          )
                    """)
                    orphan_count = cur.fetchone()[0]
                except Exception as e:
                    orphan_count = f"error: {e}"

                results.append({
                    'constraint':   fk['constraint_name'],
                    'child_table':  child_t,
                    'child_col':    child_c,
                    'parent_table': parent_t,
                    'parent_col':   parent_c,
                    'orphaned_rows': orphan_count,
                })
            cur.close()
        finally:
            conn.close()

        df_ri = pd.DataFrame(results)

        # Separate issues from clean constraints
        df_issues = df_ri[df_ri['orphaned_rows'].apply(
            lambda x: isinstance(x, int) and x > 0
        )]
        df_errors = df_ri[df_ri['orphaned_rows'].apply(
            lambda x: isinstance(x, str)
        )]
        df_clean  = df_ri[df_ri['orphaned_rows'].apply(
            lambda x: isinstance(x, int) and x == 0
        )]

        if df_issues.empty and df_errors.empty:
            st.success(
                f"✅ All {len(df_clean)} foreign-key constraints are satisfied — no orphaned rows found."
            )
        else:
            if not df_issues.empty:
                st.error(
                    f"❌ {len(df_issues)} constraint(s) have orphaned rows:"
                )
                st.dataframe(
                    df_issues,
                    hide_index=True,
                    width='stretch',
                    column_config={
                        'constraint':    'Constraint',
                        'child_table':   'Child Table',
                        'child_col':     'Child Column',
                        'parent_table':  'Parent Table',
                        'parent_col':    'Parent Column',
                        'orphaned_rows': st.column_config.NumberColumn(
                            'Orphaned Rows', format='%,d'
                        ),
                    },
                )
                copy_df_button(df_issues, key="dl_tools_ri_issues")

            if not df_errors.empty:
                st.warning(f"⚠️ {len(df_errors)} constraint(s) could not be checked (see below):")
                st.dataframe(df_errors, hide_index=True, width='stretch')
                copy_df_button(df_errors, key="dl_tools_ri_errors")

        if not df_clean.empty:
            with st.expander(f"✅ {len(df_clean)} clean constraint(s)"):
                st.dataframe(
                    df_clean,
                    hide_index=True,
                    width='stretch',
                    column_config={
                        'constraint':    'Constraint',
                        'child_table':   'Child Table',
                        'child_col':     'Child Column',
                        'parent_table':  'Parent Table',
                        'parent_col':    'Parent Column',
                        'orphaned_rows': 'Orphaned Rows',
                    },
                )
                copy_df_button(df_clean, key="dl_tools_ri_clean")


def _render_sql_interface():
    st.subheader("🛢 SQL Query Interface")
    st.caption("SELECT, INSERT, UPDATE and DELETE are allowed. DROP, TRUNCATE, ALTER, CREATE and other DDL statements are blocked.")

    default_sql = "SELECT table_name\nFROM information_schema.tables\nWHERE table_schema = 'public'\nORDER BY table_name;"

    # Handle clear request from the PREVIOUS render cycle.
    # We must pop sql_query BEFORE the widget is instantiated — Streamlit raises
    # StreamlitAPIException if you write to a widget-bound key after it renders.
    if st.session_state.pop("sql_clear_requested", False):
        st.session_state.pop("sql_query",    None)
        st.session_state.pop("sql_result",   None)
        st.session_state.pop("sql_error",    None)
        st.session_state.pop("sql_dml_info", None)

    sql = st.text_area(
        "SQL Query",
        value=st.session_state.get("sql_query", default_sql),
        height=200,
        key="sql_query",
        label_visibility="collapsed",
        placeholder="SELECT … / INSERT … / UPDATE … / DELETE …",
    )

    col_run, col_clear, col_export = st.columns([1, 1, 1])

    with col_run:
        run = st.button("▶ Run Query", type="primary", width="stretch")
    with col_clear:
        if st.button("✖ Clear", width="stretch"):
            # Cannot write to "sql_query" here — the widget is already rendered.
            # Set a flag and rerun; the key is popped at the top of the next render.
            st.session_state["sql_clear_requested"] = True
            st.rerun()
    with col_export:
        export_placeholder = st.empty()

    if run:
        query = sql.strip()
        if not query:
            st.warning("Please enter a SQL query.")
        elif _DDL_PATTERN.match(query):
            st.error("DDL statements (DROP, TRUNCATE, ALTER, CREATE, …) are not allowed.")
        elif _DML_PATTERN.match(query):
            # ── DML: execute with commit, report rows affected ────────────────
            try:
                conn = get_connection()
                cur = conn.cursor()
                cur.execute(query)
                rows_affected = cur.rowcount
                conn.commit()
                cur.close()
                conn.close()
                st.session_state["sql_dml_info"] = f"✅ Query executed successfully — {rows_affected} row(s) affected."
                st.session_state.pop("sql_result", None)
                st.session_state.pop("sql_error", None)
            except Exception as e:
                st.session_state["sql_error"] = str(e)
                st.session_state.pop("sql_result", None)
                st.session_state.pop("sql_dml_info", None)
        else:
            # ── SELECT / WITH: return result set ──────────────────────────────
            try:
                conn = get_connection()
                df = pd.read_sql(query, conn)
                conn.close()
                st.session_state["sql_result"] = df
                st.session_state.pop("sql_error", None)
                st.session_state.pop("sql_dml_info", None)
            except Exception as e:
                st.session_state["sql_error"] = str(e)
                st.session_state.pop("sql_result", None)
                st.session_state.pop("sql_dml_info", None)

    if "sql_error" in st.session_state:
        st.error(st.session_state["sql_error"])

    if "sql_dml_info" in st.session_state:
        st.success(st.session_state["sql_dml_info"])

    if "sql_result" in st.session_state:
        df = st.session_state["sql_result"]
        rows, cols = df.shape
        st.caption(f"{rows:,} row(s) · {cols} column(s)")
        st.dataframe(df, width="stretch", hide_index=True)
        copy_df_button(df, key="dl_tools_sql")
        with col_export:
            csv = df.to_csv(index=False).encode("utf-8")
            export_placeholder.download_button(
                "⬇ Export CSV",
                data=csv,
                file_name="query_result.csv",
                mime="text/csv",
                width="stretch",
            )


def _render_price_quality():
    st.subheader("🔍 Price Data Quality")
    st.caption(
        "Flags prices that changed by more than the chosen threshold vs the previous or next "
        "trading day for the same security. The nearest buy/sell transaction is shown for context. "
        "**Source** and **Downloaded At** columns identify which downloader produced each price."
    )

    # ── Threshold ─────────────────────────────────────────────────────────
    threshold = st.slider(
        "Flag when move exceeds (%):",
        min_value=10, max_value=1000, value=100, step=10,
        help="100 % = flag any price that is more than 2× or less than ½ of its neighbour, "
             "or that deviates from the nearest transaction by the same factor",
    )

    # ── Fetch all anomalies at this threshold ─────────────────────────────
    with st.spinner("Scanning price history…"):
        df_all = get_price_anomalies(float(threshold))

    if df_all.empty:
        st.success(f"No prices flagged at the {threshold} % threshold.")
        return

    # ── Security filter — only securities that actually have issues ────────
    sec_counts    = df_all.groupby('security_name').size().to_dict()
    sec_names     = sorted(sec_counts.keys())
    selected_secs = st.multiselect(
        "Filter by security (showing only securities with issues):",
        options=sec_names,
        default=[],
        format_func=lambda x: f"{x}  ({sec_counts[x]} suspicious price{'s' if sec_counts[x] != 1 else ''})",
        key="pq_sec_filter",
    )
    df = df_all[df_all['security_name'].isin(selected_secs)] if selected_secs else df_all

    st.warning(f"{len(df):,} suspicious price record(s) — {df['security_name'].nunique()} security/ies.")

    # ── Editable table with Delete checkbox ───────────────────────────────
    df['date'] = pd.to_datetime(df['date']).dt.date
    df['tx_date'] = pd.to_datetime(df['tx_date'], errors='coerce').dt.date
    df.insert(0, 'Delete', False)

    edited = st.data_editor(
        df,
        column_config={
            'Delete':        st.column_config.CheckboxColumn('🗑 Delete', default=False, pinned=True),
            'securities_id': None,
            'security_name': st.column_config.TextColumn('Security', pinned=True),
            'date':          st.column_config.DateColumn('Date'),
            'price':         st.column_config.NumberColumn('Price', format='%.4f'),
            'prev_close':    st.column_config.NumberColumn('Prev Close', format='%.4f'),
            'next_close':    st.column_config.NumberColumn('Next Close', format='%.4f'),
            'pct_vs_prev':   st.column_config.NumberColumn('% vs Prev', format='%+.1f %%'),
            'pct_vs_next':   st.column_config.NumberColumn('% vs Next', format='%+.1f %%'),
            'tx_date':       st.column_config.DateColumn('Nearest Tx Date'),
            'tx_action':     st.column_config.TextColumn('Tx Action'),
            'tx_price':      st.column_config.NumberColumn('Tx Price', format='%.4f'),
            'days_diff':     st.column_config.NumberColumn('Days to Tx', format='%d'),
            'pct_vs_tx':     st.column_config.NumberColumn('% vs Tx', format='%+.1f %%'),
            'source':        st.column_config.TextColumn('Source'),
            'downloaded_at': st.column_config.DatetimeColumn('Downloaded At', format='YYYY-MM-DD HH:mm'),
        },
        disabled=[c for c in df.columns if c != 'Delete'],
        hide_index=True,
        width="stretch",
        key="pq_editor",
    )
    copy_df_button(df, key="dl_tools_price_quality")

    to_delete = edited[edited['Delete']]
    n_selected = len(to_delete)
    n_visible  = len(df)

    col_del, col_del_all, col_info = st.columns([1, 1, 3])

    with col_del:
        del_btn = st.button(
            f"🗑 Delete {n_selected} selected" if n_selected else "🗑 Delete selected",
            type="primary" if n_selected else "secondary",
            disabled=(n_selected == 0),
            width="stretch",
            key="pq_delete_btn",
        )

    with col_del_all:
        del_all_btn = st.button(
            f"🗑 Delete all {n_visible} listed",
            type="primary",
            width="stretch",
            key="pq_delete_all_btn",
        )

    with col_info:
        if n_selected:
            st.info(f"{n_selected} row(s) checked — or use 'Delete all listed' to remove every visible row.")

    if del_btn and n_selected:
        st.session_state['pq_del_confirm'] = True
    if del_all_btn:
        st.session_state['pq_del_all_confirm'] = True

    if st.session_state.get('pq_del_confirm'):
        st.warning(f"⚠️ Delete **{n_selected}** selected price record(s)? This cannot be undone.")
        _cn, _cy, _ = st.columns([1, 1, 3])
        with _cn:
            if st.button("✖ Cancel", key="pq_del_cancel", width="stretch"):
                st.session_state['pq_del_confirm'] = False
                st.rerun()
        with _cy:
            if st.button("✔ Yes, delete", type="primary", key="pq_del_yes", width="stretch"):
                rows = to_delete[['securities_id', 'date']].to_dict('records')
                deleted = delete_historical_prices(rows)
                get_price_anomalies.clear()
                st.session_state['pq_del_confirm'] = False
                st.success(f"Deleted {deleted} price record(s).")
                st.rerun()

    if st.session_state.get('pq_del_all_confirm'):
        st.warning(f"⚠️ Delete **all {n_visible}** listed price record(s)? This cannot be undone.")
        _cn, _cy, _ = st.columns([1, 1, 3])
        with _cn:
            if st.button("✖ Cancel", key="pq_del_all_cancel", width="stretch"):
                st.session_state['pq_del_all_confirm'] = False
                st.rerun()
        with _cy:
            if st.button("✔ Yes, delete all", type="primary", key="pq_del_all_yes", width="stretch"):
                rows = df[['securities_id', 'date']].to_dict('records')
                deleted = delete_historical_prices(rows)
                get_price_anomalies.clear()
                st.session_state['pq_del_all_confirm'] = False
                st.success(f"Deleted {deleted} price record(s).")
                st.rerun()


def _render_fill_missing_prices():
    st.subheader("📥 Fill Missing Prices from Transactions")
    st.caption(
        "Finds investment transactions (Buy / Sell / Reinvest / ShrIn / ShrOut) "
        "whose date has no entry in Historical Prices and uses the transaction's "
        "Price Per Share (averaged when multiple transactions exist on the same date) "
        "to fill the gap."
    )

    with st.spinner("Scanning for gaps…"):
        df = get_missing_tx_prices()

    if df.empty:
        st.success("No missing prices found — every transaction date already has a Historical Price entry.")
        return

    # Security filter
    sec_counts = df.groupby('security_name').size().to_dict()
    sec_names  = sorted(sec_counts.keys())
    selected_secs = st.multiselect(
        "Filter by security (leave empty = all):",
        options=sec_names,
        default=[],
        format_func=lambda x: f"{x}  ({sec_counts[x]} missing date{'s' if sec_counts[x] != 1 else ''})",
        key="fmp_sec_filter",
    )
    df_view = df[df['security_name'].isin(selected_secs)] if selected_secs else df

    st.info(
        f"{len(df_view):,} missing price record(s) across "
        f"{df_view['security_name'].nunique()} security/ies."
    )

    df_display = df_view.copy()
    df_display['date'] = pd.to_datetime(df_display['date']).dt.date
    st.dataframe(
        df_display,
        column_config={
            'securities_id':  None,
            'security_name':  st.column_config.TextColumn('Security'),
            'date':           st.column_config.DateColumn('Date'),
            'price':          st.column_config.NumberColumn('Avg Tx Price', format='%.4f'),
            'actions':        st.column_config.TextColumn('Actions'),
            'tx_count':       st.column_config.NumberColumn('Tx Count', format='%d'),
        },
        hide_index=True,
        width="stretch",
    )
    copy_df_button(df_display, key="dl_tools_missing_prices")

    col_ins, col_ins_all, col_info = st.columns([1, 1, 3])

    with col_ins:
        if st.button(
            f"📥 Insert for filtered ({len(df_view):,})" if selected_secs else "📥 Insert all",
            type="primary",
            width="stretch",
            key="fmp_insert_btn",
        ):
            st.session_state['fmp_insert_confirm'] = True

    with col_ins_all:
        if selected_secs and st.button(
            f"📥 Insert all ({len(df):,})",
            type="secondary",
            width="stretch",
            key="fmp_insert_all_btn",
        ):
            st.session_state['fmp_insert_all_confirm'] = True

    with col_info:
        if selected_secs:
            st.caption("First button inserts only the filtered rows; second inserts all gaps regardless of filter.")

    if st.session_state.get('fmp_insert_confirm'):
        _n = len(df_view)
        st.warning(f"⚠️ Insert **{_n:,}** missing price record(s) from transaction data? Existing prices will not be overwritten.")
        _cn, _cy, _ = st.columns([1, 1, 3])
        with _cn:
            if st.button("✖ Cancel", key="fmp_insert_cancel", width="stretch"):
                st.session_state['fmp_insert_confirm'] = False
                st.rerun()
        with _cy:
            if st.button("✔ Yes, insert", type="primary", key="fmp_insert_yes", width="stretch"):
                rows = df_view[['securities_id', 'date', 'price']].to_dict('records')
                inserted = insert_prices_from_transactions(rows)
                get_missing_tx_prices.clear()
                get_price_anomalies.clear()
                st.session_state['fmp_insert_confirm'] = False
                st.success(f"Inserted {inserted} price record(s).")
                st.rerun()

    if st.session_state.get('fmp_insert_all_confirm'):
        _n = len(df)
        st.warning(f"⚠️ Insert **all {_n:,}** missing price records from transaction data? Existing prices will not be overwritten.")
        _cn, _cy, _ = st.columns([1, 1, 3])
        with _cn:
            if st.button("✖ Cancel", key="fmp_insert_all_cancel", width="stretch"):
                st.session_state['fmp_insert_all_confirm'] = False
                st.rerun()
        with _cy:
            if st.button("✔ Yes, insert all", type="primary", key="fmp_insert_all_yes", width="stretch"):
                rows = df[['securities_id', 'date', 'price']].to_dict('records')
                inserted = insert_prices_from_transactions(rows)
                get_missing_tx_prices.clear()
                get_price_anomalies.clear()
                st.session_state['fmp_insert_all_confirm'] = False
                st.success(f"Inserted {inserted} price record(s).")
                st.rerun()


def _render_normalize_investments():
    st.subheader("⚖ Normalize Investment Prices")
    st.caption(
        "Finds Buy / Sell / Reinvest / ShrIn / ShrOut transactions whose "
        "Price Per Share or Quantity appears to be a placeholder (whole-number dummy) "
        "while a real Historical Price exists for that date. "
        "Updates Price Per Share → actual close price and recalculates Quantity = "
        "Total Amount ÷ Price, leaving Total Amount unchanged so P&L is preserved."
    )

    price_tolerance_pct = st.slider(
        "Price tolerance vs. historical close (%)",
        min_value=5.0, max_value=50.0, value=10.0, step=1.0,
        help=(
            "Transactions where Quantity is a whole number and the recorded price "
            "differs from the historical close by **less than this %** are considered "
            "legitimate execution-price variation and will NOT be flagged. "
            "Raise this if real trades are incorrectly appearing; lower it to catch "
            "more aggressive placeholders."
        ),
        key="ni_tol_pct",
    )

    with st.spinner("Scanning investments…"):
        df = get_investments_with_dummy_prices(price_tolerance_pct=price_tolerance_pct)

    if df.empty:
        st.success("No investments with dummy prices found — all transactions already use actual prices.")
        return

    # ── Account filter (first level) ─────────────────────────────────────────
    acc_counts = df.groupby('account_name').size().to_dict()
    acc_names = sorted(acc_counts.keys())
    selected_accs = st.multiselect(
        "Filter by account (leave empty = all):",
        options=acc_names,
        default=[],
        format_func=lambda x: f"{x}  ({acc_counts[x]} row{'s' if acc_counts[x] != 1 else ''})",
        key="ni_acc_filter",
    )
    df_acc = df[df['account_name'].isin(selected_accs)] if selected_accs else df

    # ── Security filter (second level, scoped to selected accounts) ──────────
    sec_counts = df_acc.groupby('security_name').size().to_dict()
    sec_names = sorted(sec_counts.keys())
    selected_secs = st.multiselect(
        "Filter by security (leave empty = all):",
        options=sec_names,
        default=[],
        format_func=lambda x: f"{x}  ({sec_counts[x]} row{'s' if sec_counts[x] != 1 else ''})",
        key="ni_sec_filter",
    )
    df_view = df_acc[df_acc['security_name'].isin(selected_secs)] if selected_secs else df_acc

    is_filtered = bool(selected_accs or selected_secs)
    st.info(
        f"{len(df_view):,} transaction(s) with dummy prices across "
        f"{df_view['account_name'].nunique()} account(s) · "
        f"{df_view['security_name'].nunique()} security/ies."
    )

    # ── Position-closure sanity check ────────────────────────────────────────
    # Warn only when the flagged set contains BOTH buys and sells for the same
    # (account, security) and they don't net to zero — that means the position
    # won't close correctly after normalization.  Pure-buy or pure-sell groups
    # are open positions and a non-zero net qty is expected.
    pos_check = df_view.copy()
    pos_check['is_buy']  = pos_check['action'].isin(('Buy', 'Reinvest', 'ShrIn'))
    pos_check['is_sell'] = pos_check['action'].isin(('Sell', 'ShrOut'))
    pos_check['signed_new_qty'] = pos_check.apply(
        lambda r: r['new_qty'] if r['is_buy'] else -r['new_qty'],
        axis=1,
    )
    grp = pos_check.groupby(['account_name', 'security_name'])
    mixed_groups = [
        name for name, g in grp
        if g['is_buy'].any() and g['is_sell'].any()
    ]
    if mixed_groups:
        net_pos = (
            pos_check[pos_check.apply(
                lambda r: (r['account_name'], r['security_name']) in mixed_groups, axis=1
            )]
            .groupby(['account_name', 'security_name'])['signed_new_qty']
            .sum()
            .reset_index()
            .rename(columns={'signed_new_qty': 'net_qty'})
        )
        non_zero = net_pos[net_pos['net_qty'].abs() > 0.0001]
        if not non_zero.empty:
            msg = "**Position closure warning** — these have both buys and sells flagged, but they don't net to zero after normalization:\n"
            for _, row in non_zero.iterrows():
                msg += f"- {row['account_name']} / {row['security_name']}: net qty = {row['net_qty']:+.6f}\n"
            st.warning(msg)

    df_display = df_view.copy()
    df_display['date'] = pd.to_datetime(df_display['date']).dt.date
    df_display['Δ Price'] = df_display['new_price'] - df_display['current_price']
    df_display['Δ Qty']   = df_display['new_qty']   - df_display['current_qty']

    n_price_change = (df_display['Δ Price'].abs() > 0.00001).sum()
    n_qty_change   = (df_display['Δ Qty'].abs()   > 0.00001).sum()
    n_no_change    = ((df_display['Δ Price'].abs() <= 0.00001) & (df_display['Δ Qty'].abs() <= 0.00001)).sum()

    # Only show rows that will actually change
    df_display = df_display[
        (df_display['Δ Price'].abs() > 0.00001) | (df_display['Δ Qty'].abs() > 0.00001)
    ]

    _sum_cols = st.columns(3)
    _sum_cols[0].metric("Price will change",    n_price_change, help="Rows where Price/Share will be updated")
    _sum_cols[1].metric("Quantity will change",  n_qty_change,   help="Rows where Quantity will be recalculated")
    _sum_cols[2].metric("Skipped (already correct)", n_no_change, help="Rows where current values already match the historical price — excluded from the table below")

    if df_display.empty:
        st.success("All flagged transactions already match their historical price — nothing to normalize.")
        return

    st.dataframe(
        df_display,
        column_config={
            'investments_id':  None,
            'accounts_id':     None,
            'securities_id':   None,
            'account_name':    st.column_config.TextColumn('Account'),
            'security_name':   st.column_config.TextColumn('Security'),
            'date':            st.column_config.DateColumn('Date'),
            'action':          st.column_config.TextColumn('Action'),
            'total_amount':    st.column_config.NumberColumn('Total Amount', format='%.4f'),
            'current_qty':     st.column_config.NumberColumn('Current Qty', format='%.6f'),
            'current_price':   st.column_config.NumberColumn('Current Price', format='%.4f'),
            'hist_price':      st.column_config.NumberColumn('Hist. Close', format='%.4f',
                                   help='Historical close price on that date'),
            'new_qty':         st.column_config.NumberColumn('New Qty', format='%.6f',
                                   help='Buys: total/hist_price  •  Sells: proportional from buy qty'),
            'new_price':       st.column_config.NumberColumn('New Price', format='%.4f',
                                   help='Buys: hist close  •  Sells: effective realised price (total/qty)'),
            'Δ Price':         st.column_config.NumberColumn('Δ Price', format='%+.4f',
                                   help='Difference between New Price and Current Price. Zero = no change.'),
            'Δ Qty':           st.column_config.NumberColumn('Δ Qty',   format='%+.6f',
                                   help='Difference between New Qty and Current Qty. Zero = no change.'),
        },
        hide_index=True,
        width="stretch",
    )
    copy_df_button(df_display, key="dl_tools_norm_prices")
    st.caption(
        "**Buys**: Price ← hist close, Qty ← Total ÷ hist close.  "
        "**Sells**: Qty is distributed from the total buy quantity so the position closes; "
        "Price is the effective realised price (Total ÷ Qty), not the hist close.  "
        "Rows where both Δ Price and Δ Qty are 0 are already correct and will not be changed."
    )

    col_norm, col_norm_all, col_info = st.columns([1, 1, 3])

    with col_norm:
        label = (
            f"⚖ Normalize filtered ({len(df_view):,})" if is_filtered else "⚖ Normalize all"
        )
        if st.button(label, type="primary", width="stretch", key="ni_norm_btn"):
            st.session_state['ni_norm_confirm'] = True

    with col_norm_all:
        if is_filtered and st.button(
            f"⚖ Normalize all ({len(df):,})",
            type="secondary",
            width="stretch",
            key="ni_norm_all_btn",
        ):
            st.session_state['ni_norm_all_confirm'] = True

    with col_info:
        if is_filtered:
            st.caption("First button normalizes only filtered rows; second normalizes all flagged rows.")

    if st.session_state.get('ni_norm_confirm'):
        _n = len(df_view)
        st.warning(f"⚠️ This will overwrite prices and quantities for **{_n:,}** investment row(s). This cannot be undone.")
        _cn, _cy, _ = st.columns([1, 1, 3])
        with _cn:
            if st.button("✖ Cancel", key="ni_norm_cancel", width="stretch"):
                st.session_state['ni_norm_confirm'] = False
                st.rerun()
        with _cy:
            if st.button("✔ Yes, normalize", type="primary", key="ni_norm_yes", width="stretch"):
                ids = df_view['investments_id'].tolist()
                updated = normalize_investment_prices(ids)
                get_investments_with_dummy_prices.clear()
                st.session_state['ni_norm_confirm'] = False
                st.success(f"Updated {updated} investment row(s).")
                st.rerun()

    if st.session_state.get('ni_norm_all_confirm'):
        _n = len(df)
        st.warning(f"⚠️ This will overwrite prices and quantities for **{_n:,}** investment row(s) (all flagged). This cannot be undone.")
        _cn, _cy, _ = st.columns([1, 1, 3])
        with _cn:
            if st.button("✖ Cancel", key="ni_norm_all_cancel", width="stretch"):
                st.session_state['ni_norm_all_confirm'] = False
                st.rerun()
        with _cy:
            if st.button("✔ Yes, normalize all", type="primary", key="ni_norm_all_yes", width="stretch"):
                ids = df['investments_id'].tolist()
                updated = normalize_investment_prices(ids)
                get_investments_with_dummy_prices.clear()
                st.session_state['ni_norm_all_confirm'] = False
                st.success(f"Updated {updated} investment row(s).")
                st.rerun()

    # Import here to avoid circular dependency at module level
    from database.crud import update_holdings as _update_holdings
    st.divider()
    col_rh, col_rh_info = st.columns([1, 4])
    with col_rh:
        if st.button("🔄 Refresh Holdings", width="stretch", key="ni_refresh_holdings"):
            _update_holdings()
            st.success("Holdings recalculated.")
    with col_rh_info:
        st.caption("Recalculates the Holdings table from Investments data. Run this after normalization to update portfolio quantities and P&L.")


def _render_stock_split():
    """Record a stock split or reverse split as ShrIn / ShrOut delta entries."""
    st.subheader("Split / Reverse Split")
    st.caption(
        "Records a stock split by inserting a **ShrIn** (forward split) or **ShrOut** (reverse split) "
        "entry for the *delta* shares on the effective date. "
        "All existing broker records are left untouched. Holdings are refreshed automatically."
    )

    sec_df = get_all_securities()
    sec_display = [
        f"{r['ticker']}  {r['securities_name']}" if r["ticker"] else r["securities_name"]
        for _, r in sec_df.iterrows()
    ]
    sec_id_map = dict(zip(sec_display, sec_df["securities_id"]))

    col1, col2 = st.columns(2)
    sel_sec = col1.selectbox("Security", ["(select...)"] + sec_display, key="ss_sec")
    if sel_sec == "(select...)":
        return

    securities_id = sec_id_map[sel_sec]

    from database.queries import get_all_accounts_for_nwr
    all_accts = get_all_accounts_for_nwr()
    acct_name_to_id = dict(zip(all_accts["accounts_name"], all_accts["accounts_id"]))
    sel_acct_names = col2.multiselect(
        "Limit to accounts (leave empty = all)",
        sorted(acct_name_to_id),
        key="ss_accts",
    )
    account_ids = [acct_name_to_id[n] for n in sel_acct_names] or None

    st.markdown("#### Split ratio")
    rc1, rc2, rc3 = st.columns([1, 1, 3])
    new_shares = rc1.number_input("New shares", min_value=0.001, value=2.0, step=1.0, key="ss_new")
    old_shares = rc2.number_input("Old shares", min_value=0.001, value=1.0, step=1.0, key="ss_old")
    ratio = new_shares / old_shares
    split_type = "Reverse Split" if new_shares < old_shares else "Stock Split"
    rc3.markdown(
        f"<br><span style='font-size:1.1em'>= <b>{ratio:.6g}x</b> ratio | "
        f"<b>{split_type}</b> ({new_shares:.4g} : {old_shares:.4g})</span>",
        unsafe_allow_html=True,
    )

    import datetime
    split_date = st.date_input("Effective date", value=datetime.date.today(), key="ss_date")

    custom_desc = st.text_input(
        "Description (leave blank for auto)",
        value="",
        key="ss_desc",
        placeholder=f"{int(new_shares)}:{int(old_shares)} {split_type}",
    )

    if st.button("Preview", key="ss_preview"):
        df_prev = get_split_preview(securities_id, split_date, account_ids)
        st.session_state["ss_preview_df"] = df_prev

    if "ss_preview_df" not in st.session_state:
        return

    df_prev = st.session_state["ss_preview_df"].copy()
    if df_prev.empty:
        st.warning("No holdings found for this security before the selected date.")
        return

    is_forward = new_shares >= old_shares
    action_label = "ShrIn" if is_forward else "ShrOut"
    df_prev["delta_qty"] = df_prev["current_qty"].apply(
        lambda q: round(q * (ratio - 1), 6) if is_forward else round(q * (1 - ratio), 6)
    )
    df_prev["new_total"] = df_prev["current_qty"] + (df_prev["delta_qty"] if is_forward else -df_prev["delta_qty"])
    df_prev["action"] = action_label

    st.markdown(f"The following **{action_label}** entries will be inserted on **{split_date}**:")
    st.dataframe(
        df_prev[["account", "current_qty", "delta_qty", "new_total", "action"]],
        column_config={
            "account":     st.column_config.TextColumn("Account"),
            "current_qty": st.column_config.NumberColumn("Current Qty", format="%.6f"),
            "delta_qty":   st.column_config.NumberColumn(f"{action_label} Qty (delta)", format="%.6f"),
            "new_total":   st.column_config.NumberColumn("New Total Qty", format="%.6f"),
            "action":      st.column_config.TextColumn("Action"),
        },
        hide_index=True,
        width='stretch',
    )

    st.divider()
    confirm_col, apply_col, _ = st.columns([2, 1, 3])
    confirmed = confirm_col.checkbox("I understand, apply the split", key="ss_confirm")
    if apply_col.button("Apply", type="primary", key="ss_apply", disabled=not confirmed):
        try:
            holdings = df_prev[["accounts_id", "current_qty"]].to_dict("records")
            desc = custom_desc.strip() or ""
            n = apply_stock_split(
                securities_id=securities_id,
                split_date=split_date,
                new_shares=new_shares,
                old_shares=old_shares,
                holdings_by_account=holdings,
                description=desc,
            )
            st.success(f"Done: {n} {action_label} row(s) inserted. Holdings refreshed.")
            st.session_state.pop("ss_preview_df", None)
            st.session_state["ss_confirm"] = False
            st.rerun()
        except Exception as e:
            st.error(f"Error: {e}")


def _render_investment_data_quality():
    """Investment data consistency / anomaly checker with inline edit & save."""
    from database.queries import get_all_accounts_for_nwr

    st.subheader("🩺 Investment Data Quality")
    st.caption(
        "Detects anomalies in Quantity × Price, Commission, Total (Acc/Sec ccy) and FX Rate. "
        "Edit any field inline and click **Save Changes** to persist."
    )

    # ── Anomaly detection helper (used twice: pre-filter + display) ───────────
    ATOL   = 0.10
    PTOL   = 0.005
    TRADEABLE = {"Buy", "Sell", "Reinvest", "ShrIn", "ShrOut"}

    def _detect(row):
        issues, recs = [], []
        qty     = row["quantity"]   or 0.0
        price   = row["price"]      or 0.0
        comm_raw = row["commission"] or 0.0   # signed (usually negative)
        comm     = abs(comm_raw)              # unsigned, used for tolerance
        t_acc   = row["total_acc"]
        t_sec   = row["total_sec"]
        fx      = row["fx_rate"]    or 1.0
        action  = row["action"]
        same    = row["acc_currency"] == row["sec_currency"]
        notional = qty * price

        if t_acc is None:
            issues.append("NULL total_acc")
            if t_sec is not None:
                recs.append(f"Set total_acc = {t_sec * fx:.2f} (total_sec × fx)")
        if t_sec is None and qty and price and action in TRADEABLE:
            issues.append("NULL total_sec")
            recs.append(f"Set total_sec ≈ {notional:.2f} (qty × price)")

        if same and abs(fx - 1.0) > 0.001:
            issues.append(f"Same ccy but FX_Rate={fx:.4f}")
            recs.append("Set FX_Rate = 1.0")
        if not same and fx and abs(fx - 1.0) < 0.001:
            issues.append(f"Cross-ccy ({row['sec_currency']}/{row['acc_currency']}) but FX_Rate=1.0")
            recs.append("Look up correct FX rate for trade date")

        if t_acc is not None and t_sec is not None and fx:
            expected_acc = t_sec * fx
            delta = abs(t_acc - expected_acc)
            tol   = max(ATOL, abs(expected_acc) * PTOL)
            if delta > tol:
                issues.append(f"total_acc ({t_acc:.2f}) != total_sec x fx ({expected_acc:.2f}), delta={delta:.2f}")
                recs.append(
                    f"Set total_acc = {expected_acc:.2f}"
                    f"  OR  fx = {(t_acc / t_sec):.6f}"
                    f"  OR  total_sec = {(t_acc / fx):.8f}"
                )

        if t_sec is not None and qty and price and action in TRADEABLE:
            diff = abs(t_sec - notional)
            tol  = max(ATOL, comm + abs(notional) * PTOL)
            if diff > tol:
                issues.append(f"total_sec ({t_sec:.2f}) != qty x price ({notional:.2f}), delta={diff:.2f}")
                expected_sec = notional + comm_raw if action in ("Buy","ShrIn","Reinvest") \
                               else max(0.0, notional + comm_raw)
                recs.append(f"Expected total_sec ~= {expected_sec:.2f}")

        if action in TRADEABLE:
            if not qty:
                issues.append("Quantity is 0 / NULL")
            if not price:
                issues.append("Price is 0 / NULL")

        return "; ".join(issues) if issues else "", "; ".join(recs) if recs else ""

    # ── Load ALL data first to detect which accounts have findings ────────────
    with st.spinner("Scanning all investment records…"):
        df_all = get_investment_consistency_data(account_ids=None)

    if df_all.empty:
        st.info("No investment records found.")
        return

    df_all[["anomalies", "recommendations"]] = df_all.apply(
        _detect, axis=1, result_type="expand"
    )

    # Accounts that have at least one anomaly (honour the exclude-zero-price preference)
    _excl_zero = st.session_state.get("ic_excl_zero_price", True)
    df_all_anom = df_all["anomalies"].copy()
    if _excl_zero:
        df_all_anom = df_all_anom.str.replace(
            r"(?:;\s*)?Price is 0 / NULL", "", regex=True
        ).str.replace(r"^;\s*", "", regex=True).str.strip("; ")
    accts_with_findings = set(
        df_all.loc[df_all_anom != "", "account"].unique()
    )

    # Build account map from full data (not from get_all_accounts_for_nwr)
    all_accts_df = get_all_accounts_for_nwr()
    acct_name_to_id = dict(zip(all_accts_df["accounts_name"], all_accts_df["accounts_id"]))

    # Only show accounts with at least one finding
    selectable_accts = sorted(a for a in accts_with_findings if a in acct_name_to_id)

    # ── Filters ──────────────────────────────────────────────────────────────
    f1, f2, f3, f4 = st.columns(4)

    sel_acct_names = f1.multiselect(
        "Accounts (with findings only)",
        selectable_accts,
        key="ic_accts",
        help="Only accounts that have at least one anomaly are listed here.",
    )
    account_ids = [acct_name_to_id[n] for n in sel_acct_names] or None

    all_actions = ["Buy","Sell","Dividend","IntInc","RtrnCap",
                   "CashIn","CashOut","MiscExp","Reinvest","ShrIn","ShrOut"]
    sel_actions = f2.multiselect("Actions", all_actions, key="ic_actions")

    anomalies_only = f3.toggle("Anomalies only", value=True, key="ic_anom_only")
    exclude_zero_price = f4.toggle("Exclude 'Price is 0 / NULL'", value=True, key="ic_excl_zero_price")
    _, f5 = st.columns([3, 1])
    exclude_zero_qty = f5.toggle("Exclude 'Quantity is 0 / NULL'", value=False, key="ic_excl_zero_qty")

    if st.button("🔍 Run Check", type="primary", key="ic_run"):
        _df = get_investment_consistency_data(account_ids)
        _df[["anomalies", "recommendations"]] = _df.apply(_detect, axis=1, result_type="expand")
        st.session_state["ic_df_raw"] = _df

    if "ic_df_raw" not in st.session_state:
        # Show summary metrics from the pre-scanned full data
        n_total  = len(df_all)
        n_issues = (df_all["anomalies"] != "").sum()
        c1, c2, c3 = st.columns(3)
        c1.metric("Total records", n_total)
        c2.metric("⚠️ Anomalies",  n_issues)
        c3.metric("✅ Clean",       n_total - n_issues)
        st.info("Select accounts / actions above and click **Run Check** to view details.")
        return

    df = st.session_state["ic_df_raw"].copy()
    # Re-apply detection in case session state was stored without anomaly columns
    if "anomalies" not in df.columns:
        df[["anomalies", "recommendations"]] = df.apply(_detect, axis=1, result_type="expand")
    if sel_actions:
        df = df[df["action"].isin(sel_actions)]
    if account_ids:
        df = df[df["account"].isin(sel_acct_names)]

    if df.empty:
        st.info("No investment records found for the selected filters.")
        return

    if exclude_zero_price:
        df["anomalies"] = df["anomalies"].str.replace(
            r"(?:;\s*)?Price is 0 / NULL", "", regex=True
        ).str.replace(r"^;\s*", "", regex=True).str.strip("; ")
    if exclude_zero_qty:
        df["anomalies"] = df["anomalies"].str.replace(
            r"(?:;\s*)?Quantity is 0 / NULL", "", regex=True
        ).str.replace(r"^;\s*", "", regex=True).str.strip("; ")

    df_show = df if not anomalies_only else df[df["anomalies"] != ""]

    n_total  = len(df)
    n_issues = (df["anomalies"] != "").sum()
    c1, c2, c3 = st.columns(3)
    c1.metric("Total records", n_total)
    c2.metric("⚠️ Anomalies",  n_issues)
    c3.metric("✅ Clean",       n_total - n_issues)

    if df_show.empty:
        st.success("No anomalies found — all investment records look consistent!")
        return

    st.markdown(f"Showing **{len(df_show)}** record(s).")

    edit_cols = ["investments_id", "date", "account", "security", "action",
                 "quantity", "price", "commission",
                 "total_acc", "total_sec", "fx_rate",
                 "acc_currency", "sec_currency",
                 "anomalies", "recommendations"]
    df_edit = df_show[edit_cols].copy()
    df_edit["date"] = df_edit["date"].dt.strftime("%Y-%m-%d")

    edited = st.data_editor(
        df_edit,
        disabled=["investments_id", "date", "account", "security", "action",
                  "acc_currency", "sec_currency", "anomalies", "recommendations"],
        column_config={
            "investments_id":  st.column_config.NumberColumn("ID",           format="%d", width="small"),
            "date":            st.column_config.TextColumn("Date",           width="small"),
            "account":         st.column_config.TextColumn("Account"),
            "security":        st.column_config.TextColumn("Security"),
            "action":          st.column_config.TextColumn("Action",         width="small"),
            "quantity":        st.column_config.NumberColumn("Qty",           format="%.6f"),
            "price":           st.column_config.NumberColumn("Price",         format="%.6f"),
            "commission":      st.column_config.NumberColumn("Commission",    format="%.4f"),
            "total_acc":       st.column_config.NumberColumn("Total (acc)",   format="%.4f"),
            "total_sec":       st.column_config.NumberColumn("Total (sec)",   format="%.4f"),
            "fx_rate":         st.column_config.NumberColumn("FX Rate",       format="%.6f"),
            "acc_currency":    st.column_config.TextColumn("Acc Ccy",         width="small"),
            "sec_currency":    st.column_config.TextColumn("Sec Ccy",         width="small"),
            "anomalies":       st.column_config.TextColumn("⚠️ Anomalies",    width="large"),
            "recommendations": st.column_config.TextColumn("💡 Recommendations", width="large"),
        },
        hide_index=True,
        width='stretch',
        key="ic_editor",
        num_rows="fixed",
    )

    copy_df_button(df_edit, key="dl_ic_report")

    _editable = ["quantity", "price", "commission", "total_acc", "total_sec", "fx_rate"]

    def _has_changes():
        for col in _editable:
            orig = df_edit[col].fillna(0.0)
            curr = edited[col].fillna(0.0)
            if not orig.equals(curr):
                return True
        return False

    if st.button("💾 Save Changes", key="ic_save", disabled=not _has_changes()):
        saved, errors = 0, []
        for i, row in edited.iterrows():
            orig = df_edit.loc[i]
            changes = {}
            for col in _editable:
                ov = orig[col]
                nv = row[col]
                if pd.isna(ov) and pd.isna(nv):
                    continue
                if pd.isna(ov) != pd.isna(nv) or (not pd.isna(ov) and abs(float(ov) - float(nv)) > 1e-9):
                    changes[col] = None if pd.isna(nv) else float(nv)
            if changes:
                try:
                    update_investment_row(int(row["investments_id"]), changes)
                    saved += 1
                except Exception as e:
                    errors.append(f"Row {i+1} (ID {int(row['investments_id'])}): {e}")
        for e in errors:
            st.error(e)
        if saved:
            st.success(f"Saved {saved} row(s). Re-run the check to refresh anomaly flags.")
            st.session_state.pop("ic_df_raw", None)


def _render_fix_missing_transfer_mirrors():
    st.subheader("🔄 Fix Missing Transfer Mirrors")
    st.caption(
        "Detects transactions that point to a target account "
        "(**Accounts_Id_Target** is set) but have no corresponding mirror "
        "transaction recorded on that target account. This happens when cash "
        "accounts are created after the original transfers were imported, or "
        "when only one leg of a transfer was saved. "
        "Transfers involving investment accounts that are linked to a cash account "
        "(BuyX / SellX / DivX pattern) are excluded automatically. "
        "The tool creates the missing mirror row and a matching Split, then "
        "recalculates affected account balances."
    )

    # ── Detection SQL (no filter — always load full result set) ───────────────
    # Two cases:
    #   1. Transfers_Id is set but the target account has no row with that same ID.
    #   2. Accounts_Id_Target is set but Transfers_Id is NULL (half-entry).
    # Investment accounts linked to a cash account are excluded on both sides.
    DETECTION_SQL = """
        SELECT
            t.transactions_id,
            a_src.accounts_name              AS source_account,
            t.accounts_id                    AS src_acc_id,
            t.date,
            COALESCE(p.payees_name, '')      AS payee,
            t.description,
            t.total_amount                   AS source_amount,
            t.total_amount_target,
            a_tgt.accounts_name              AS target_account,
            t.accounts_id_target             AS tgt_acc_id,
            t.payees_id,
            t.cleared,
            t.transfers_id,
            CASE
                WHEN t.transfers_id IS NULL THEN 'No Transfers_Id'
                ELSE 'Mirror missing'
            END AS issue_type
        FROM  Transactions t
        JOIN  Accounts a_src ON a_src.accounts_id = t.accounts_id
        JOIN  Accounts a_tgt ON a_tgt.accounts_id = t.accounts_id_target
        LEFT  JOIN Payees p  ON p.payees_id        = t.payees_id
        LEFT  JOIN Transactions mirror
               ON  mirror.transfers_id    = t.transfers_id
               AND mirror.accounts_id     = t.accounts_id_target
               AND mirror.transactions_id != t.transactions_id
        WHERE t.accounts_id_target IS NOT NULL
          AND (
              (t.transfers_id IS NOT NULL AND mirror.transactions_id IS NULL)
              OR t.transfers_id IS NULL
          )
          -- Exclude investment accounts that have a linked cash account (both sides)
          AND NOT (
              a_src.accounts_type IN ('Brokerage', 'Pension', 'Other Investment', 'Margin')
              AND a_src.accounts_id_linked IS NOT NULL
          )
          AND NOT (
              a_tgt.accounts_type IN ('Brokerage', 'Pension', 'Other Investment', 'Margin')
              AND a_tgt.accounts_id_linked IS NOT NULL
          )
        ORDER BY t.date DESC, t.transactions_id DESC
    """

    conn = get_connection()
    df   = pd.read_sql(DETECTION_SQL, conn)
    conn.close()

    if df.empty:
        st.success("✅ No missing transfer mirrors found.")
        return

    # ── Account filter — built from actual results ─────────────────────────────
    tgt_ids   = sorted(df["tgt_acc_id"].unique().tolist())
    tgt_names = df[["tgt_acc_id", "target_account"]].drop_duplicates().set_index("tgt_acc_id")["target_account"].to_dict()

    filter_acc = st.selectbox(
        "Filter by target account (account where mirrors are missing):",
        options=[None] + tgt_ids,
        format_func=lambda x: "All accounts" if x is None else tgt_names.get(x, str(x)),
        key="fix_mirror_target_filter",
    )

    df_view   = df[df["tgt_acc_id"] == filter_acc] if filter_acc is not None else df
    all_count = len(df_view)

    st.warning(f"⚠️ {all_count:,} transaction(s) are missing their mirror leg.")

    # ── Preview / selection table ──────────────────────────────────────────────
    df_display = df_view.copy()
    df_display["date"] = pd.to_datetime(df_display["date"]).dt.date
    df_display.insert(0, "Fix", False)

    edited = st.data_editor(
        df_display[[
            "Fix", "transactions_id", "issue_type",
            "date", "source_account", "payee", "description",
            "source_amount", "total_amount_target", "target_account", "transfers_id",
        ]],
        column_config={
            "Fix":                 st.column_config.CheckboxColumn("Fix", default=False, pinned=True),
            "transactions_id":     st.column_config.NumberColumn("TX ID", format="%d"),
            "issue_type":          st.column_config.TextColumn("Issue"),
            "date":                st.column_config.DateColumn("Date"),
            "source_account":      st.column_config.TextColumn("Source Account"),
            "payee":               st.column_config.TextColumn("Payee"),
            "description":         st.column_config.TextColumn("Description"),
            "source_amount":       st.column_config.NumberColumn("Source Amount", format="%,.2f"),
            "total_amount_target": st.column_config.NumberColumn("Target Amount", format="%,.2f"),
            "target_account":      st.column_config.TextColumn("Target Account (mirror missing)"),
            "transfers_id":        st.column_config.NumberColumn("Transfers_Id"),
        },
        disabled=[
            "transactions_id", "issue_type", "date", "source_account", "payee",
            "description", "source_amount", "total_amount_target",
            "target_account", "transfers_id",
        ],
        hide_index=True,
        width="stretch",
        key="fix_mirror_editor",
    )

    to_fix    = edited[edited["Fix"]]
    sel_count = len(to_fix)

    copy_df_button(df_display.drop(columns=["Fix"]), key="dl_fix_mirror")

    st.divider()

    # ── Action buttons ─────────────────────────────────────────────────────────
    # "Fix All" is only shown when a specific account is selected.
    if filter_acc is not None:
        btn_c1, btn_c2, _ = st.columns([2, 2, 3])
    else:
        btn_c1, _ = st.columns([2, 5])
        btn_c2    = None

    with btn_c1:
        fix_sel_btn = st.button(
            f"🔄 Fix {sel_count:,} Selected" if sel_count else "🔄 Fix Selected",
            type="secondary" if filter_acc is not None else "primary",
            disabled=(sel_count == 0),
            key="fix_mirror_sel_btn",
            width="stretch",
        )

    fix_all_btn = False
    if btn_c2 is not None:
        with btn_c2:
            fix_all_btn = st.button(
                f"🔄 Fix All {all_count:,} for this Account",
                type="primary",
                key="fix_mirror_all_btn",
                width="stretch",
            )

    # ── Stage pending work on button click (shows confirmation next render) ────
    if fix_sel_btn and sel_count > 0:
        st.session_state["fix_mirror_pending"] = {
            "ids":   to_fix["transactions_id"].tolist(),
            "label": (
                f"create **{sel_count:,}** mirror transaction(s) "
                "for the **selected** rows"
            ),
        }
        st.rerun()

    if fix_all_btn:
        acc_name = tgt_names.get(filter_acc, str(filter_acc))
        st.session_state["fix_mirror_pending"] = {
            "ids":   df_view["transactions_id"].tolist(),
            "label": (
                f"create **{all_count:,}** mirror transaction(s) "
                f"for **all** listed rows on account **{acc_name}**"
            ),
        }
        st.rerun()

    # ── Confirmation panel ─────────────────────────────────────────────────────
    pending = st.session_state.get("fix_mirror_pending")
    if not pending:
        return

    n_pending = len(pending["ids"])
    st.warning(
        f"⚠️ Please confirm: {pending['label']}.\n\n"
        f"This will INSERT **{n_pending:,}** new Transactions row(s), "
        "add matching Splits, and recalculate affected account balances. "
        "**This action cannot be undone automatically.**"
    )

    conf_yes, conf_no, _ = st.columns([1, 1, 4])
    with conf_yes:
        confirmed = st.button(
            "✅ Yes, create",
            type="primary",
            key="fix_mirror_confirm_yes",
            width="stretch",
        )
    with conf_no:
        if st.button("❌ Cancel", key="fix_mirror_confirm_no", width="stretch"):
            del st.session_state["fix_mirror_pending"]
            st.rerun()

    if not confirmed:
        return

    # ── Execute ────────────────────────────────────────────────────────────────
    del st.session_state["fix_mirror_pending"]

    sel_rows          = df[df["transactions_id"].isin(pending["ids"])]
    created           = 0
    errors: list[str] = []
    affected_accounts: set[int] = set()

    conn = get_connection()
    cur  = conn.cursor()
    try:
        for _, row in sel_rows.iterrows():
            src_tx_id    = int(row["transactions_id"])
            src_acc_id   = int(row["src_acc_id"])
            tgt_acc_id   = int(row["tgt_acc_id"])
            tx_date      = row["date"]
            payees_id    = int(row["payees_id"]) if pd.notna(row["payees_id"]) else None
            description  = row["description"]
            src_amount   = float(row["source_amount"]) if pd.notna(row["source_amount"]) else 0.0
            tgt_raw      = row["total_amount_target"]
            cleared      = bool(row["cleared"])
            transfers_id = int(row["transfers_id"]) if pd.notna(row["transfers_id"]) else None

            # Mirror amount: use Total_Amount_Target from source when available
            # (covers cross-currency transfers); fall back to negating source.
            # Always take the absolute value of tgt_raw first — some importers
            # store it with the same sign as the source, others as positive.
            # Then apply the opposite direction to the source leg.
            if pd.notna(tgt_raw) and float(tgt_raw) != 0:
                raw           = abs(float(tgt_raw))
                mirror_amount = raw if src_amount <= 0 else -raw
            else:
                mirror_amount = -src_amount

            # Mirror's Total_Amount_Target = absolute value of the source leg
            mirror_tgt_amount = abs(src_amount)

            # Ensure Transfers_Id exists — create one when missing and stamp
            # it on the source leg too so both legs are properly linked.
            if transfers_id is None:
                cur.execute("SELECT nextval('transfers_id_seq')")
                transfers_id = cur.fetchone()[0]
                cur.execute(
                    "UPDATE Transactions SET transfers_id = %s WHERE transactions_id = %s",
                    (transfers_id, src_tx_id),
                )

            try:
                cur.execute(
                    """
                    INSERT INTO Transactions
                        (Accounts_Id, Date, Payees_Id, Description,
                         Total_Amount, Cleared,
                         Accounts_Id_Target, Total_Amount_Target, Transfers_Id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING Transactions_Id
                    """,
                    (
                        tgt_acc_id, tx_date, payees_id, description,
                        mirror_amount, cleared,
                        src_acc_id, mirror_tgt_amount, transfers_id,
                    ),
                )
                mirror_tx_id = cur.fetchone()[0]

                cur.execute(
                    "INSERT INTO Splits (Transactions_Id, Categories_Id, Amount, Memo) "
                    "VALUES (%s, NULL, %s, 'Transfer')",
                    (mirror_tx_id, mirror_amount),
                )

                affected_accounts.add(src_acc_id)
                affected_accounts.add(tgt_acc_id)
                created += 1

            except Exception as row_err:
                errors.append(f"TX #{src_tx_id}: {row_err}")

        conn.commit()

    except Exception as outer_err:
        conn.rollback()
        st.error(f"❌ Commit failed: {outer_err}")
        return
    finally:
        cur.close()
        conn.close()

    # Recalculate balances from scratch (SUM of Total_Amount per account),
    # which corrects any double-counting the INSERT trigger may have introduced.
    for acc_id in affected_accounts:
        update_accounts_balances(acc_id)

    if errors:
        st.warning(f"⚠️ {len(errors)} row(s) could not be processed:")
        for err in errors:
            st.caption(err)

    if created:
        st.success(
            f"✅ Created {created:,} mirror transaction(s). "
            f"Balances recalculated for {len(affected_accounts)} account(s)."
        )
        st.rerun()

    # ══════════════════════════════════════════════════════════════════════════
    # SECTION 2 — Unlinked existing mirror pairs
    # Both legs exist independently but are not linked via Transfers_Id.
    # Source tx has Accounts_Id_Target + Transfers_Id set; a candidate on the
    # target account matches by date + |amount| but lacks the same Transfers_Id.
    # Fix = UPDATE the candidate: stamp Transfers_Id + set Accounts_Id_Target.
    # ══════════════════════════════════════════════════════════════════════════
    st.divider()
    st.subheader("🔗 Unlinked Transfer Pairs")
    st.caption(
        "Finds cases where **both legs already exist** but are not linked — the source "
        "transaction has `Accounts_Id_Target` and `Transfers_Id` set, while the matching "
        "transaction on the target account has no `Transfers_Id` back-link. "
        "Matching is done by **date + absolute amount**. "
        "The fix **updates** the existing target transaction (stamps `Transfers_Id` and "
        "`Accounts_Id_Target`) — no new rows are created."
    )

    UNLINKED_SQL = """
        SELECT
            t.transactions_id                   AS src_tx_id,
            a_src.accounts_name                 AS source_account,
            t.accounts_id                       AS src_acc_id,
            t.date,
            COALESCE(p.payees_name, '')          AS payee,
            t.description,
            t.total_amount                      AS source_amount,
            t.total_amount_target,
            a_tgt.accounts_name                 AS target_account,
            t.accounts_id_target                AS tgt_acc_id,
            t.transfers_id,
            cand.transactions_id                AS candidate_tx_id,
            cand.total_amount                   AS candidate_amount,
            cand.description                    AS candidate_desc
        FROM  Transactions t
        JOIN  Accounts a_src ON a_src.accounts_id = t.accounts_id
        JOIN  Accounts a_tgt ON a_tgt.accounts_id = t.accounts_id_target
        LEFT  JOIN Payees p  ON p.payees_id        = t.payees_id
        -- Confirm the proper mirror (same Transfers_Id on target) is missing
        LEFT  JOIN Transactions mirror
               ON  mirror.transfers_id    = t.transfers_id
               AND mirror.accounts_id     = t.accounts_id_target
               AND mirror.transactions_id != t.transactions_id
        -- Find a candidate on the target account matching by date + |amount|
        JOIN  Transactions cand
               ON  cand.accounts_id       = t.accounts_id_target
               AND cand.date              = t.date
               AND ABS(cand.total_amount) = ABS(COALESCE(t.total_amount_target, t.total_amount))
               AND cand.transactions_id  != t.transactions_id
               AND (cand.transfers_id IS NULL OR cand.transfers_id != t.transfers_id)
        WHERE t.accounts_id_target IS NOT NULL
          AND t.transfers_id       IS NOT NULL
          AND mirror.transactions_id IS NULL
          AND NOT (
              a_src.accounts_type IN ('Brokerage','Pension','Other Investment','Margin')
              AND a_src.accounts_id_linked IS NOT NULL
          )
          AND NOT (
              a_tgt.accounts_type IN ('Brokerage','Pension','Other Investment','Margin')
              AND a_tgt.accounts_id_linked IS NOT NULL
          )
        ORDER BY t.date DESC, t.transactions_id DESC
    """

    conn2 = get_connection()
    df_ul = pd.read_sql(UNLINKED_SQL, conn2)
    conn2.close()

    if df_ul.empty:
        st.success("✅ No unlinked transfer pairs found.")
    else:
        # Account filter
        ul_tgt_names = df_ul[["tgt_acc_id","target_account"]].drop_duplicates().set_index("tgt_acc_id")["target_account"].to_dict()
        ul_filter = st.selectbox(
            "Filter by target account:",
            options=[None] + sorted(df_ul["tgt_acc_id"].unique().tolist()),
            format_func=lambda x: "All accounts" if x is None else ul_tgt_names.get(x, str(x)),
            key="ul_mirror_filter",
        )
        df_ul_view = df_ul[df_ul["tgt_acc_id"] == ul_filter] if ul_filter is not None else df_ul
        st.warning(f"⚠️ {len(df_ul_view):,} unlinked pair(s) found.")

        df_ul_display = df_ul_view.copy()
        df_ul_display["date"] = pd.to_datetime(df_ul_display["date"]).dt.date
        df_ul_display.insert(0, "Link", False)

        edited_ul = st.data_editor(
            df_ul_display[[
                "Link", "src_tx_id", "date", "source_account", "payee",
                "description", "source_amount",
                "target_account", "candidate_tx_id", "candidate_amount", "candidate_desc",
                "transfers_id",
            ]],
            column_config={
                "Link":            st.column_config.CheckboxColumn("Link", default=False, pinned=True),
                "src_tx_id":       st.column_config.NumberColumn("Source TX", format="%d"),
                "date":            st.column_config.DateColumn("Date"),
                "source_account":  st.column_config.TextColumn("Source Account"),
                "payee":           st.column_config.TextColumn("Payee"),
                "description":     st.column_config.TextColumn("Description"),
                "source_amount":   st.column_config.NumberColumn("Source Amount", format="%,.2f"),
                "target_account":  st.column_config.TextColumn("Target Account"),
                "candidate_tx_id": st.column_config.NumberColumn("Candidate TX", format="%d"),
                "candidate_amount":st.column_config.NumberColumn("Candidate Amount", format="%,.2f"),
                "candidate_desc":  st.column_config.TextColumn("Candidate Description"),
                "transfers_id":    st.column_config.NumberColumn("Transfers_Id"),
            },
            disabled=[
                "src_tx_id","date","source_account","payee","description",
                "source_amount","target_account","candidate_tx_id",
                "candidate_amount","candidate_desc","transfers_id",
            ],
            hide_index=True,
            width='stretch',
            key="ul_mirror_editor",
        )

        copy_df_button(df_ul_display.drop(columns=["Link"]), key="dl_ul_mirror")

        to_link     = edited_ul[edited_ul["Link"]]
        ul_sel_count = len(to_link)

        lc1, lc2, _ = st.columns([2, 2, 3])
        link_sel_btn = lc1.button(
            f"🔗 Link {ul_sel_count:,} Selected" if ul_sel_count else "🔗 Link Selected",
            disabled=(ul_sel_count == 0),
            key="ul_link_sel_btn",
            width="stretch",
        )
        link_all_btn = False
        if ul_filter is not None:
            link_all_btn = lc2.button(
                f"🔗 Link All {len(df_ul_view):,} for this Account",
                type="primary",
                key="ul_link_all_btn",
                width="stretch",
            )

        if link_sel_btn and ul_sel_count > 0:
            st.session_state["ul_mirror_pending"] = {
                "pairs": list(zip(to_link["src_tx_id"], to_link["candidate_tx_id"],
                                  to_link["transfers_id"], to_link["src_acc_id"],
                                  to_link["tgt_acc_id"])),
                "label": f"link **{ul_sel_count:,}** selected pair(s)",
            }
            st.rerun()

        if link_all_btn:
            st.session_state["ul_mirror_pending"] = {
                "pairs": list(zip(df_ul_view["src_tx_id"], df_ul_view["candidate_tx_id"],
                                  df_ul_view["transfers_id"], df_ul_view["src_acc_id"],
                                  df_ul_view["tgt_acc_id"])),
                "label": f"link **{len(df_ul_view):,}** pair(s) for this account",
            }
            st.rerun()

        ul_pending = st.session_state.get("ul_mirror_pending")
        if ul_pending:
            n_ul = len(ul_pending["pairs"])
            st.warning(
                f"⚠️ Please confirm: {ul_pending['label']}.\n\n"
                f"This will **UPDATE** {n_ul:,} existing transaction(s) on the target account "
                "to stamp `Transfers_Id` and `Accounts_Id_Target`. "
                "**This action cannot be undone automatically.**"
            )
            ul_yes, ul_no, _ = st.columns([1, 1, 4])
            with ul_yes:
                ul_confirmed = st.button("✅ Yes, link", type="primary",
                                         key="ul_confirm_yes", width="stretch")
            with ul_no:
                if st.button("❌ Cancel", key="ul_confirm_no", width="stretch"):
                    del st.session_state["ul_mirror_pending"]
                    st.rerun()

            if ul_confirmed:
                del st.session_state["ul_mirror_pending"]
                ul_linked, ul_errors = 0, []
                ul_affected: set[int] = set()
                conn3 = get_connection()
                cur3  = conn3.cursor()
                try:
                    for src_id, cand_id, xfr_id, src_acc, tgt_acc in ul_pending["pairs"]:
                        try:
                            # Stamp the candidate with the source's Transfers_Id
                            # and set Accounts_Id_Target pointing back to source
                            cur3.execute("""
                                UPDATE Transactions
                                   SET Transfers_Id       = %s,
                                       Accounts_Id_Target = %s
                                 WHERE Transactions_Id    = %s
                            """, (int(xfr_id), int(src_acc), int(cand_id)))
                            ul_linked += 1
                            ul_affected.add(int(src_acc))
                            ul_affected.add(int(tgt_acc))
                        except Exception as row_err:
                            ul_errors.append(f"Pair ({src_id}↔{cand_id}): {row_err}")
                    conn3.commit()
                except Exception as outer:
                    conn3.rollback()
                    st.error(f"❌ Commit failed: {outer}")
                finally:
                    cur3.close()
                    conn3.close()

                for acc_id in ul_affected:
                    update_accounts_balances(acc_id)

                for e in ul_errors:
                    st.warning(e)
                if ul_linked:
                    st.success(
                        f"✅ Linked {ul_linked:,} pair(s). "
                        f"Balances recalculated for {len(ul_affected)} account(s)."
                    )
                    st.rerun()


def _render_fix_missing_investment_cash_links():
    st.subheader("🔗 Fix Missing Investment Cash Links")
    st.caption(
        "Detects investment entries with no linked cash transaction "
        "(**Transactions_Id IS NULL**) but where a matching transaction already exists "
        "on the account's linked cash account (matched on date + absolute amount). "
        "Useful after account reorganisation when both sides exist but were never "
        "cross-referenced.  Only updates **Investments.Transactions_Id** — no new rows "
        "are created.  To create brand-new cash transactions instead, use "
        "**Investment Register → Manage Linked Cash Account**."
    )

    # ── Detection SQL (no filter — always load full result set) ──────────────
    # Finds unlinked investment entries where a cash transaction on the linked
    # account matches on (date, ABS(amount), not already linked elsewhere).
    DETECTION_SQL = """
        SELECT
            i.investments_id,
            a_inv.accounts_name                AS investment_account,
            i.accounts_id                      AS inv_acc_id,
            a_cash.accounts_id                 AS cash_acc_id,
            a_cash.accounts_name               AS cash_account,
            i.date,
            i.action,
            COALESCE(s.securities_name, '—')   AS security,
            ABS(i.total_amount_acccur)         AS inv_amount,
            t.transactions_id                  AS candidate_tx_id,
            t.total_amount                     AS candidate_amount,
            COALESCE(p.payees_name, '')        AS candidate_payee,
            t.description                      AS candidate_description
        FROM  Investments i
        JOIN  Accounts a_inv  ON a_inv.accounts_id  = i.accounts_id
        JOIN  Accounts a_cash ON a_cash.accounts_id = a_inv.accounts_id_linked
        LEFT  JOIN Securities s ON s.securities_id  = i.securities_id
        JOIN  Transactions t
              ON  t.accounts_id = a_inv.accounts_id_linked
              AND t.date        = i.date
              AND ROUND(ABS(t.total_amount)::numeric, 2)
                  = ROUND(ABS(i.total_amount_acccur)::numeric, 2)
              -- Exclude transactions already linked to another investment entry
              AND NOT EXISTS (
                  SELECT 1 FROM Investments i2
                  WHERE  i2.transactions_id = t.transactions_id
              )
              -- Exclude transactions that are already part of a complete
              -- cash-to-cash transfer pair (transfers_id set + mirror exists)
              AND NOT (
                  t.transfers_id IS NOT NULL
                  AND EXISTS (
                      SELECT 1 FROM Transactions mirror
                      WHERE  mirror.transfers_id    = t.transfers_id
                        AND  mirror.transactions_id != t.transactions_id
                  )
              )
        LEFT  JOIN Payees p ON p.payees_id = t.payees_id
        WHERE i.transactions_id IS NULL
          AND i.action IN ('Buy', 'Sell', 'Dividend', 'IntInc', 'RtrnCap', 'MiscExp')
        ORDER BY i.date DESC, i.investments_id, t.transactions_id
    """

    conn = get_connection()
    df   = pd.read_sql(DETECTION_SQL, conn)
    conn.close()

    if df.empty:
        st.success(
            "✅ No linkable pairs found. Either all investment entries are already linked, "
            "or no matching cash transactions exist on the linked cash account. "
            "For the latter, use **Investment Register → Manage Linked Cash Account** "
            "to create new cash transactions."
        )
        return

    # ── Account filter — built from actual results ────────────────────────────
    inv_accs = (
        df[["inv_acc_id", "investment_account", "cash_account"]]
        .drop_duplicates("inv_acc_id")
        .sort_values("investment_account")
    )
    acc_lookup_inv = {
        r["inv_acc_id"]: f"{r['investment_account']}  →  {r['cash_account']}"
        for _, r in inv_accs.iterrows()
    }

    filter_acc = st.selectbox(
        "Filter by investment account:",
        options=[None] + inv_accs["inv_acc_id"].tolist(),
        format_func=lambda x: "All accounts" if x is None else acc_lookup_inv.get(x, str(x)),
        key="fix_inv_link_filter",
    )

    df_view    = df[df["inv_acc_id"] == filter_acc] if filter_acc is not None else df
    all_count  = len(df_view)
    unique_inv = df_view["investments_id"].nunique()
    multi_note = (
        f"  ({unique_inv} investment entries — some have multiple candidates.)"
        if unique_inv < all_count else ""
    )
    st.warning(f"⚠️ {all_count:,} potential link(s) found.{multi_note}")

    # ── Preview table ─────────────────────────────────────────────────────────
    df_display = df_view.copy()
    df_display["date"] = pd.to_datetime(df_display["date"]).dt.date
    df_display.insert(0, "Link", False)

    edited = st.data_editor(
        df_display[[
            "Link", "investments_id",
            "investment_account", "date", "action", "security", "inv_amount",
            "candidate_tx_id", "cash_account",
            "candidate_amount", "candidate_payee", "candidate_description",
        ]],
        column_config={
            "Link":                  st.column_config.CheckboxColumn("Link", default=False, pinned=True),
            "investments_id":        st.column_config.NumberColumn("Inv ID",       format="%d"),
            "investment_account":    st.column_config.TextColumn("Inv Account"),
            "date":                  st.column_config.DateColumn("Date"),
            "action":                st.column_config.TextColumn("Action"),
            "security":              st.column_config.TextColumn("Security"),
            "inv_amount":            st.column_config.NumberColumn("Inv Amount",   format="%,.2f"),
            "candidate_tx_id":       st.column_config.NumberColumn("Cash TX ID",   format="%d"),
            "cash_account":          st.column_config.TextColumn("Cash Account"),
            "candidate_amount":      st.column_config.NumberColumn("Cash Amount",  format="%,.2f"),
            "candidate_payee":       st.column_config.TextColumn("Payee"),
            "candidate_description": st.column_config.TextColumn("Cash Description"),
        },
        disabled=[
            "investments_id", "investment_account", "date", "action", "security",
            "inv_amount", "candidate_tx_id", "cash_account",
            "candidate_amount", "candidate_payee", "candidate_description",
        ],
        hide_index=True,
        width="stretch",
        key="fix_inv_link_editor",
    )

    to_link   = edited[edited["Link"]]
    sel_count = len(to_link)

    copy_df_button(df_display.drop(columns=["Link"]), key="dl_fix_inv_link")

    st.divider()

    # ── Action buttons ────────────────────────────────────────────────────────
    if filter_acc is not None:
        btn_c1, btn_c2, _ = st.columns([2, 2, 3])
    else:
        btn_c1, _ = st.columns([2, 5])
        btn_c2    = None

    with btn_c1:
        link_sel_btn = st.button(
            f"🔗 Link {sel_count:,} Selected" if sel_count else "🔗 Link Selected",
            type="secondary" if filter_acc is not None else "primary",
            disabled=(sel_count == 0),
            key="fix_inv_link_sel_btn",
            width="stretch",
        )

    link_all_btn = False
    if btn_c2 is not None:
        with btn_c2:
            link_all_btn = st.button(
                f"🔗 Link All {unique_inv:,} for this Account",
                type="primary",
                key="fix_inv_link_all_btn",
                width="stretch",
            )

    # ── Stage pending ─────────────────────────────────────────────────────────
    if link_sel_btn and sel_count > 0:
        dupes = to_link[to_link.duplicated("investments_id", keep=False)]
        if not dupes.empty:
            st.error(
                f"⚠️ {dupes['investments_id'].nunique()} investment entr(y/ies) selected "
                "more than once (ambiguous). Uncheck all but one candidate per investment entry."
            )
        else:
            st.session_state["fix_inv_link_pending"] = {
                "pairs": list(zip(
                    to_link["investments_id"].astype(int).tolist(),
                    to_link["candidate_tx_id"].astype(int).tolist(),
                )),
                "label": f"link **{sel_count:,}** investment entr(y/ies) to existing cash transactions",
            }
            st.rerun()

    if link_all_btn:
        # Auto-resolve: for each investment entry take the lowest TX ID (oldest / first candidate)
        df_first = (
            df_view.sort_values("candidate_tx_id")
                   .drop_duplicates("investments_id", keep="first")
        )
        pairs    = list(zip(
            df_first["investments_id"].astype(int).tolist(),
            df_first["candidate_tx_id"].astype(int).tolist(),
        ))
        acc_name = acc_lookup_inv.get(filter_acc, str(filter_acc))
        st.session_state["fix_inv_link_pending"] = {
            "pairs": pairs,
            "label": f"link **{len(pairs):,}** investment entries for **{acc_name}**",
        }
        st.rerun()

    # ── Confirmation panel ────────────────────────────────────────────────────
    pending = st.session_state.get("fix_inv_link_pending")
    if not pending:
        return

    n_pending = len(pending["pairs"])
    st.warning(
        f"⚠️ Please confirm: {pending['label']}.\n\n"
        f"This will **UPDATE {n_pending:,}** `Investments.Transactions_Id` value(s) to point "
        "to the matched cash transaction. No new rows will be created. "
        "**This action cannot be undone automatically.**"
    )

    conf_yes, conf_no, _ = st.columns([1, 1, 4])
    with conf_yes:
        confirmed = st.button(
            "✅ Yes, link",
            type="primary",
            key="fix_inv_link_confirm_yes",
            width="stretch",
        )
    with conf_no:
        if st.button("❌ Cancel", key="fix_inv_link_confirm_no", width="stretch"):
            del st.session_state["fix_inv_link_pending"]
            st.rerun()

    if not confirmed:
        return

    # ── Execute ───────────────────────────────────────────────────────────────
    del st.session_state["fix_inv_link_pending"]

    linked            = 0
    errors: list[str] = []

    conn = get_connection()
    cur  = conn.cursor()
    try:
        for inv_id, tx_id in pending["pairs"]:
            try:
                cur.execute(
                    "UPDATE Investments SET Transactions_Id = %s "
                    "WHERE Investments_Id = %s",
                    (tx_id, inv_id),
                )
                linked += 1
            except Exception as row_err:
                errors.append(f"Inv #{inv_id} → TX #{tx_id}: {row_err}")

        conn.commit()

    except Exception as outer_err:
        conn.rollback()
        st.error(f"❌ Commit failed: {outer_err}")
        return
    finally:
        cur.close()
        conn.close()

    if errors:
        st.warning(f"⚠️ {len(errors)} row(s) could not be processed:")
        for err in errors:
            st.caption(err)

    if linked:
        # Refresh investment-account balances (linking changes which entries are
        # "unlinked" in the balance formula) and the affected cash accounts.
        update_investment_balances()
        _linked_tx_ids = [tx_id for _, tx_id in pending["pairs"]]
        if _linked_tx_ids:
            _ph = ", ".join(["%s"] * len(_linked_tx_ids))
            _conn_b = get_connection()
            try:
                _cur_b = _conn_b.cursor()
                _cur_b.execute(
                    f"SELECT DISTINCT accounts_id FROM Transactions "
                    f"WHERE transactions_id IN ({_ph})",
                    _linked_tx_ids,
                )
                for (_cash_acc_id,) in _cur_b.fetchall():
                    update_accounts_balances(_cash_acc_id)
            finally:
                _cur_b.close()
                _conn_b.close()
        st.success(f"✅ Linked {linked:,} investment entr(y/ies) to existing cash transactions.")
        st.rerun()


def _render_fix_transfer_sign_mismatches():
    st.subheader("🔀 Fix Transfer Sign Mismatches")
    st.caption(
        "Finds linked transfer pairs (sharing the same **Transfers_Id**) where both legs "
        "have the **same sign** — i.e. both appear as credit or both as debit. "
        "For a correct transfer one leg must be positive (incoming) and the other negative (outgoing). "
        "Select rows and choose which leg's sign to flip to correct the mismatch."
    )

    DETECTION_SQL = """
        SELECT
            t1.transactions_id  AS tx1_id,
            t2.transactions_id  AS tx2_id,
            t1.transfers_id,
            t1.date,
            a1.accounts_name    AS account1,
            a2.accounts_name    AS account2,
            t1.accounts_id      AS acc1_id,
            t2.accounts_id      AS acc2_id,
            t1.total_amount     AS amount1,
            t2.total_amount     AS amount2,
            COALESCE(p.payees_name, '') AS payee,
            t1.description,
            CASE
                WHEN t1.total_amount > 0 AND t2.total_amount > 0 THEN 'Both credit (+)'
                WHEN t1.total_amount < 0 AND t2.total_amount < 0 THEN 'Both debit (−)'
                ELSE 'Same sign'
            END AS mismatch_type
        FROM  Transactions t1
        JOIN  Transactions t2
               ON  t2.transfers_id    = t1.transfers_id
               AND t2.transactions_id > t1.transactions_id
        JOIN  Accounts a1 ON a1.accounts_id = t1.accounts_id
        JOIN  Accounts a2 ON a2.accounts_id = t2.accounts_id
        LEFT  JOIN Payees p ON p.payees_id = t1.payees_id
        WHERE t1.transfers_id IS NOT NULL
          AND t1.total_amount != 0
          AND t2.total_amount != 0
          AND SIGN(t1.total_amount) = SIGN(t2.total_amount)
        ORDER BY t1.date DESC, t1.transfers_id DESC
    """

    conn = get_connection()
    df   = pd.read_sql(DETECTION_SQL, conn)
    conn.close()

    if df.empty:
        st.success("✅ No transfer sign mismatches found.")
        return

    # ── Account filter — built from actual results ─────────────────────────────
    acc_pairs = pd.concat([
        df[["acc1_id", "account1"]].rename(columns={"acc1_id": "acc_id", "account1": "acc_name"}),
        df[["acc2_id", "account2"]].rename(columns={"acc2_id": "acc_id", "account2": "acc_name"}),
    ]).drop_duplicates("acc_id").sort_values("acc_name")
    acc_ids_involved  = acc_pairs["acc_id"].tolist()
    acc_name_lookup   = dict(zip(acc_pairs["acc_id"], acc_pairs["acc_name"]))

    filter_acc = st.selectbox(
        "Filter by account (show mismatches involving this account):",
        options=[None] + acc_ids_involved,
        format_func=lambda x: "All accounts" if x is None else acc_name_lookup.get(x, str(x)),
        key="fix_sign_acc_filter",
    )

    df_view = (
        df[(df["acc1_id"] == filter_acc) | (df["acc2_id"] == filter_acc)]
        if filter_acc is not None else df
    )

    st.warning(f"⚠️ {len(df_view):,} transfer pair(s) have mismatched signs.")

    df_display = df_view.copy()
    df_display["date"] = pd.to_datetime(df_display["date"]).dt.date
    df_display.insert(0, "Select", False)

    edited = st.data_editor(
        df_display[[
            "Select", "mismatch_type", "date", "transfers_id",
            "tx1_id", "account1", "amount1",
            "tx2_id", "account2", "amount2",
            "payee", "description",
        ]],
        column_config={
            "Select":       st.column_config.CheckboxColumn("Select", default=False, pinned=True),
            "mismatch_type":st.column_config.TextColumn("Mismatch"),
            "date":         st.column_config.DateColumn("Date"),
            "transfers_id": st.column_config.NumberColumn("Transfers_Id", format="%d"),
            "tx1_id":       st.column_config.NumberColumn("TX1 ID", format="%d"),
            "account1":     st.column_config.TextColumn("Account 1"),
            "amount1":      st.column_config.NumberColumn("Amount 1", format="%,.2f"),
            "tx2_id":       st.column_config.NumberColumn("TX2 ID", format="%d"),
            "account2":     st.column_config.TextColumn("Account 2"),
            "amount2":      st.column_config.NumberColumn("Amount 2", format="%,.2f"),
            "payee":        st.column_config.TextColumn("Payee"),
            "description":  st.column_config.TextColumn("Description"),
        },
        disabled=[
            "mismatch_type", "date", "transfers_id",
            "tx1_id", "account1", "amount1",
            "tx2_id", "account2", "amount2",
            "payee", "description",
        ],
        hide_index=True,
        width="stretch",
        key="fix_sign_editor",
    )

    selected  = edited[edited["Select"]]
    sel_count = len(selected)

    copy_df_button(df_display.drop(columns=["Select"]), key="dl_fix_sign")

    if sel_count == 0:
        st.caption("Select rows above, then choose which leg to flip.")
        return

    st.divider()
    st.markdown(f"**{sel_count:,} pair(s) selected.** Choose which transaction leg to flip:")

    flip_choice = st.radio(
        "Flip sign of:",
        options=["TX1 (Account 1)", "TX2 (Account 2)"],
        horizontal=True,
        key="fix_sign_flip_choice",
    )
    flip_tx_col    = "tx1_id"   if flip_choice == "TX1 (Account 1)" else "tx2_id"
    flip_acc_col   = "acc1_id"  if flip_choice == "TX1 (Account 1)" else "acc2_id"
    other_acc_col  = "acc2_id"  if flip_acc_col == "acc1_id"         else "acc1_id"

    if st.button(
        f"🔀 Flip Sign for {sel_count:,} Selected ({flip_choice})",
        type="primary",
        key="fix_sign_flip_btn",
    ):
        # Look up full rows in original df (data_editor only returns displayed columns)
        sel_tx1_ids = selected["tx1_id"].tolist()
        sel_rows    = df[df["tx1_id"].isin(sel_tx1_ids)]
        st.session_state["fix_sign_pending"] = {
            "tx_ids":       sel_rows[flip_tx_col].tolist(),
            "acc_ids":      sel_rows[flip_acc_col].tolist(),
            "other_acc_ids": sel_rows[other_acc_col].tolist(),
            "label":        f"flip the sign of **{sel_count:,}** transaction(s) ({flip_choice})",
        }
        st.rerun()

    pending = st.session_state.get("fix_sign_pending")
    if not pending:
        return

    st.warning(
        f"⚠️ Please confirm: {pending['label']}.\n\n"
        "This will negate **Total_Amount**, **Total_Amount_Target**, and all **Splits** amounts "
        "for the selected transactions, then recalculate balances. "
        "**This action cannot be undone automatically.**"
    )

    conf_yes, conf_no, _ = st.columns([1, 1, 4])
    with conf_yes:
        confirmed = st.button("✅ Yes, flip", type="primary", key="fix_sign_confirm_yes", width="stretch")
    with conf_no:
        if st.button("❌ Cancel", key="fix_sign_confirm_no", width="stretch"):
            del st.session_state["fix_sign_pending"]
            st.rerun()

    if not confirmed:
        return

    del st.session_state["fix_sign_pending"]

    tx_ids   = pending["tx_ids"]
    all_accs = set(pending["acc_ids"]) | set(pending["other_acc_ids"])
    flipped  = 0
    errors: list[str] = []

    conn = get_connection()
    cur  = conn.cursor()
    try:
        for tx_id in tx_ids:
            try:
                cur.execute(
                    """
                    UPDATE Transactions
                    SET    total_amount        = -total_amount,
                           total_amount_target = CASE
                               WHEN total_amount_target IS NOT NULL THEN -total_amount_target
                               ELSE NULL
                           END
                    WHERE  transactions_id = %s
                    """,
                    (tx_id,),
                )
                cur.execute(
                    "UPDATE Splits SET amount = -amount WHERE transactions_id = %s",
                    (tx_id,),
                )
                flipped += 1
            except Exception as row_err:
                errors.append(f"TX #{tx_id}: {row_err}")

        conn.commit()
    except Exception as outer_err:
        conn.rollback()
        st.error(f"❌ Commit failed: {outer_err}")
        return
    finally:
        cur.close()
        conn.close()

    for acc_id in all_accs:
        update_accounts_balances(int(acc_id))

    if errors:
        st.warning(f"⚠️ {len(errors)} row(s) could not be processed:")
        for err in errors:
            st.caption(err)

    if flipped:
        st.success(
            f"✅ Flipped sign for {flipped:,} transaction(s). "
            f"Balances recalculated for {len(all_accs)} account(s)."
        )
        st.rerun()


def _read_docker_container_logs(container_name: str, tail: int) -> "tuple[str, str] | None":
    """Read container stdout+stderr via the Docker Unix socket.

    Returns (plain_text, source_label) or None if the socket is unavailable.
    Uses the Docker HTTP API directly — no extra packages required.
    The Docker multiplexed stream format uses an 8-byte header per frame:
        [stream_type(1), 0, 0, 0, size_big_endian(4)]
    """
    import os, socket, struct

    sock_path = "/var/run/docker.sock"
    if not os.path.exists(sock_path):
        return None

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(10)
        sock.connect(sock_path)

        req = (
            f"GET /containers/{container_name}/logs"
            f"?stdout=1&stderr=1&tail={tail}&timestamps=0 HTTP/1.0\r\n"
            f"Host: localhost\r\n\r\n"
        )
        sock.sendall(req.encode())

        raw = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            raw += chunk
        sock.close()

        # Split HTTP header from body
        sep = raw.find(b"\r\n\r\n")
        if sep == -1:
            return None
        # Check for HTTP error
        status_line = raw[:raw.find(b"\r\n")].decode("utf-8", errors="replace")
        if " 404 " in status_line or " 500 " in status_line:
            return None

        body = raw[sep + 4:]

        # Parse Docker multiplexed stream
        lines: list[str] = []
        pos = 0
        while pos + 8 <= len(body):
            # stream_type: 1=stdout, 2=stderr (we show both)
            frame_size = struct.unpack(">I", body[pos + 4: pos + 8])[0]
            pos += 8
            if frame_size == 0:
                continue
            if pos + frame_size > len(body):
                break
            lines.append(body[pos: pos + frame_size].decode("utf-8", errors="replace"))
            pos += frame_size

        text = "".join(lines)
        return text, f"Docker container `{container_name}` (via socket)"
    except Exception:
        return None


def _docker_utc_to_local(utc_str: str) -> str:
    """Convert a Docker UTC timestamp string to local time (from DB_TIMEZONE env var).

    Docker `time` format: "2026-06-02T13:20:50.188349634Z"
    Returns a formatted local-time string like "2026-06-02 16:20:50".
    Falls back to the original string on any error.
    """
    try:
        import os
        from datetime import datetime, timezone
        import zoneinfo
        raw = utc_str.rstrip("Z")[:26]          # truncate to microseconds
        dt_utc = datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
        tz_name = os.getenv("DB_TIMEZONE", "Europe/Athens")
        dt_local = dt_utc.astimezone(zoneinfo.ZoneInfo(tz_name))
        return dt_local.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return utc_str


def _has_log_timestamp(text: str) -> bool:
    """Return True if *text* already starts with a Python logging timestamp
    (YYYY-MM-DD HH:MM:SS …).
    """
    import re
    return bool(re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", text.lstrip()))


def _read_docker_json_log(container_name: str, tail: int) -> "tuple[str, str] | None":
    """Read the Docker JSON log file for *container_name* from
    /var/lib/docker/containers/.  Returns (plain_text, label) or None.

    Deduplication
    -------------
    The scheduler (and Streamlit app) write to both print() → stdout AND
    logging → stderr, so Docker captures each message twice in the JSON log.
    We prefer the stderr lines (which carry the Python-formatted local timestamp)
    and suppress the matching stdout duplicate.

    Timestamp normalisation
    -----------------------
    Docker `time` fields are always UTC.  For stdout lines that have no Python
    timestamp prefix we prepend the converted local-time equivalent so every
    visible line shows the same timezone (EET/EEST).
    """
    import os, json, glob as _glob

    base = "/var/lib/docker/containers"
    if not os.path.isdir(base):
        return None
    try:
        # Find the log file by matching the container name in config.v2.json
        matches = []
        for config_path in _glob.glob(f"{base}/*/config.v2.json"):
            try:
                with open(config_path) as cf:
                    cfg = json.load(cf)
                if cfg.get("Name", "").lstrip("/") == container_name:
                    cid = cfg["ID"]
                    log_path = f"{base}/{cid}/{cid}-json.log"
                    if os.path.exists(log_path):
                        matches.append(log_path)
            except Exception:
                pass

        if not matches:
            return None

        log_path = matches[0]
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            all_lines = fh.readlines()

        tail_lines = all_lines[-tail:]

        # ── Parse JSON, build dedup set from stderr lines ─────────────────────
        parsed: list[tuple[str, str, str]] = []   # (stream, local_time, log_text)
        stderr_messages: set[str] = set()

        for raw in tail_lines:
            try:
                obj      = json.loads(raw)
                stream   = obj.get("stream", "stdout")
                log_text = obj.get("log", "")
                dt_local = _docker_utc_to_local(obj.get("time", ""))
                parsed.append((stream, dt_local, log_text))

                if stream == "stderr":
                    # Extract bare message text for dedup (strip Python log prefix)
                    msg = log_text.strip()
                    if _has_log_timestamp(msg):
                        # "2026-06-02 16:20:50,188 INFO ✔ Something…"
                        # strip up to and including the level keyword
                        import re as _re
                        m = _re.match(
                            r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[,\d]* "
                            r"(?:INFO|WARNING|ERROR|DEBUG|CRITICAL)\s+(.*)",
                            msg, _re.DOTALL,
                        )
                        bare = m.group(1) if m else msg
                    else:
                        bare = msg
                    stderr_messages.add(bare.rstrip())
            except Exception:
                parsed.append(("stdout", "", raw))

        # ── Build output — drop stdout duplicates, normalise timestamps ────────
        plain_lines: list[str] = []
        for stream, dt_local, log_text in parsed:
            stripped = log_text.strip()

            if stream == "stdout" and stripped:
                # Drop if the same message appeared on stderr
                if stripped in stderr_messages:
                    continue
                if any(stripped in m for m in stderr_messages):
                    continue
                # Prepend local time for timestampless stdout lines
                if not _has_log_timestamp(stripped):
                    log_text = f"{dt_local}  {log_text}"

            plain_lines.append(log_text)

        return "".join(plain_lines), f"Docker JSON log for `{container_name}`"
    except Exception:
        return None


def _render_log_viewer():
    import os

    st.subheader("📋 Log Viewer")
    st.caption(
        "Shows logs for the **Streamlit app** and **background scheduler**. "
        "Sources tried in order: ① log files in the shared `/app` volume "
        "(available after containers are rebuilt with the new code), "
        "② Docker socket at `/var/run/docker.sock` (requires the socket mount in docker-compose.yml), "
        "③ Docker JSON log files at `/var/lib/docker/containers/` (if host path is mounted)."
    )

    # ── Known log sources ──────────────────────────────────────────────────────
    # Each entry: (display_label, type, identifier)
    #   type "file"      → identifier is a filesystem path
    #   type "docker"    → identifier is the Docker container name
    SOURCES = [
        ("Streamlit app",  "file",   None),          # path resolved below
        ("Scheduler",      "file",   None),           # path resolved below
        ("Streamlit app",  "docker", "personal_finance"),
        ("Scheduler",      "docker", "personal_finance_scheduler"),
    ]

    # Resolve file paths
    app_data_dir = os.getenv("APP_DATA_DIR", ".")
    _file_candidates = {
        "Streamlit app": [
            os.path.join(app_data_dir, "app.log"),
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "app.log"),
            "app.log",
        ],
        "Scheduler": [
            os.path.join(app_data_dir, "scheduler.log"),
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "scheduler.log"),
            "scheduler.log",
        ],
    }

    available: list[tuple[str, str, str]] = []   # (dropdown_label, type, identifier)
    for label, src_type, identifier in SOURCES:
        if src_type == "file":
            for candidate in _file_candidates.get(label, []):
                if os.path.exists(candidate):
                    sz = os.path.getsize(candidate) / 1024
                    available.append(
                        (f"{label} — file ({os.path.basename(candidate)}, {sz:,.0f} KB)",
                         "file", candidate)
                    )
                    break
        else:
            available.append(
                (f"{label} — Docker container ({identifier})", "docker", identifier)
            )

    if not available:
        st.warning("No log sources configured. See caption above for setup instructions.")
        return

    # ── Controls ───────────────────────────────────────────────────────────────
    labels = [a[0] for a in available]
    sel_label = st.selectbox("Log source:", labels, key="log_source_sel")
    _, sel_type, sel_id = available[labels.index(sel_label)]

    ctrl1, ctrl2, ctrl3 = st.columns([1, 2, 2])
    with ctrl1:
        n_lines = int(st.number_input(
            "Last N lines:", min_value=50, max_value=50_000,
            value=500, step=100, key="log_n_lines",
        ))
    with ctrl2:
        level_filter = st.multiselect(
            "Level filter:",
            ["ERROR", "WARNING", "INFO", "DEBUG"],
            default=["ERROR", "WARNING"],
            key="log_level_filter",
            help="Leave empty to show all levels.",
        )
    with ctrl3:
        search_term = st.text_input(
            "Keyword search:", key="log_search",
            placeholder="e.g. TradingView  or  suspicious",
        )

    # ── Fetch log content ──────────────────────────────────────────────────────
    all_lines: list[str] = []
    source_desc = ""

    if sel_type == "file":
        try:
            with open(sel_id, encoding="utf-8", errors="replace") as fh:
                all_lines = fh.readlines()
            source_desc = f"file `{sel_id}`  •  {os.path.getsize(sel_id)/1024:,.1f} KB"
        except Exception as exc:
            st.error(f"Cannot read log file: {exc}")
            return
    else:
        # Docker container — try socket first, then JSON log file
        result = _read_docker_container_logs(sel_id, tail=n_lines)
        if result is None:
            result = _read_docker_json_log(sel_id, tail=n_lines)
        if result is None:
            st.error(
                f"Cannot read Docker logs for container **{sel_id}**.\n\n"
                "Make sure `/var/run/docker.sock` is mounted in `docker-compose.yml`:\n"
                "```yaml\n"
                "volumes:\n"
                "  - /var/run/docker.sock:/var/run/docker.sock:ro\n"
                "```\n"
                "Then recreate the container with `docker compose up -d --force-recreate personal_finance`."
            )
            return
        text, source_desc = result
        all_lines = text.splitlines(keepends=True)

    # ── Filter ─────────────────────────────────────────────────────────────────
    tail_lines = all_lines[-n_lines:]

    filtered = tail_lines
    if level_filter:
        filtered = [l for l in filtered if any(lvl in l for lvl in level_filter)]
    if search_term.strip():
        needle = search_term.strip().lower()
        filtered = [l for l in filtered if needle in l.lower()]

    # ── Display ────────────────────────────────────────────────────────────────
    st.caption(
        f"Source: {source_desc}  •  "
        f"{len(all_lines):,} total lines  •  showing **{len(filtered):,}** after filters"
    )

    log_text = "".join(filtered) if filtered else "(no lines match the current filters)"
    st.code(log_text, language=None)

    # ── Download ───────────────────────────────────────────────────────────────
    dl_col, _ = st.columns([1, 4])
    with dl_col:
        download_name = (
            os.path.basename(sel_id) if sel_type == "file"
            else f"{sel_id}.log"
        )
        st.download_button(
            label="⬇️ Download full log",
            data="".join(all_lines).encode("utf-8"),
            file_name=download_name,
            mime="text/plain",
            key="log_download_btn",
            width="stretch",
        )


def _render_data_export():
    import io
    st.subheader("📤 Full Data Export")
    st.caption(
        "Exports all major tables to a single Excel workbook (.xlsx). "
        "Historical prices and FX rates are limited to the last 2 years to keep the file size manageable. "
        "Transaction rows are capped at 100 000."
    )

    if st.button("⬇️ Generate & Download Export", type="primary", key="export_gen_btn"):
        with st.spinner("Fetching data from all tables…"):
            data = export_all_data()

        if not data:
            st.error("No data returned — check your database connection.")
            return

        with st.spinner("Building Excel workbook…"):
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as writer:
                for sheet_name, df in data.items():
                    # Excel sheet names max 31 chars
                    safe_name = sheet_name[:31]
                    df.to_excel(writer, sheet_name=safe_name, index=False)

        buf.seek(0)
        import datetime as _dt
        fname = f"personal_finance_export_{_dt.date.today()}.xlsx"
        st.download_button(
            label="📥 Download Excel File",
            data=buf,
            file_name=fname,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="export_download_btn",
        )

        # Summary
        st.success(f"Export ready — {len(data)} sheets.")
        summary_rows = [
            {"Sheet": k, "Rows": len(v), "Columns": len(v.columns)}
            for k, v in data.items()
        ]
        st.dataframe(pd.DataFrame(summary_rows), hide_index=True, width='stretch')


_CATEGORIES = {
    "💾 Database": [
        "💾 Backup & Restore",
        "🔧 DB Maintenance",
        "🛢 SQL Interface",
        "📤 Data Export",
        "🔄 Fix Missing Transfer Mirrors",
        "🔀 Fix Transfer Sign Mismatches",
        "🔗 Fix Missing Investment Cash Links",
    ],
    "📊 Market Data & Prices": [
        "📥 Fill Missing Prices",
        "🔍 Price Quality",
        "⚖ Normalize Investments",
        "🩺 Investment Data Quality",
    ],
    "📋 Logs": [
        "📋 Log Viewer",
    ],
}

_TOOL_RENDERERS = {
    "💾 Backup & Restore":              render_backup_restore,
    "🛢 SQL Interface":                 _render_sql_interface,
    "🔧 DB Maintenance":                _render_db_maintenance,
    "📤 Data Export":                   _render_data_export,
    "🔄 Fix Missing Transfer Mirrors":  _render_fix_missing_transfer_mirrors,
    "🔀 Fix Transfer Sign Mismatches":  _render_fix_transfer_sign_mismatches,
    "🔗 Fix Missing Investment Cash Links": _render_fix_missing_investment_cash_links,
    "📥 Fill Missing Prices":           _render_fill_missing_prices,
    "🔍 Price Quality":                 _render_price_quality,
    "⚖ Normalize Investments":         _render_normalize_investments,
    "🩺 Investment Data Quality":       _render_investment_data_quality,
    "📋 Log Viewer":                    _render_log_viewer,
}


def render_tools(conn):
    """Render the Tools page. Only the selected tool is rendered to keep it fast."""
    st.title("Tools")

    category = st.radio(
        "category",
        list(_CATEGORIES.keys()),
        horizontal=True,
        label_visibility="collapsed",
        key="tools_category",
    )
    st.divider()

    tools_in_category = _CATEGORIES[category]

    # Categories with few tools get tabs; larger categories use a selectbox.
    if len(tools_in_category) <= 3:
        tabs = st.tabs(tools_in_category)
        for tab, tool in zip(tabs, tools_in_category):
            with tab:
                _TOOL_RENDERERS[tool]()
    else:
        tool = st.selectbox(
            "Tool",
            tools_in_category,
            label_visibility="collapsed",
            key="tools_tool",
        )
        st.divider()
        _TOOL_RENDERERS[tool]()
