"""
Transfer Issues Review UI
Displays flagged transfer issues for manual review and resolution.
"""

import streamlit as st
import pandas as pd
from database.connection import get_connection


def _get_conn():
    conn = get_connection()
    cur = conn.cursor()
    return conn, cur


def _load_issues(status_filter="Open"):
    conn, cur = _get_conn()
    try:
        cur.execute("""
            SELECT
                i.Issue_Id,
                i.Issue_Type,
                i.Status,
                i.Date_A,
                i.Date_B,
                i.Amount_A,
                i.Amount_B,
                a1.Accounts_Name  AS Account_A,
                a2.Accounts_Name  AS Account_B,
                i.Description_A,
                i.Description_B,
                i.Notes,
                i.Transactions_Id_A,
                i.Transactions_Id_B,
                i.Created_At
            FROM Transfer_Issues i
            LEFT JOIN Accounts a1 ON a1.Accounts_Id = i.Accounts_Id_A
            LEFT JOIN Accounts a2 ON a2.Accounts_Id = i.Accounts_Id_B
            WHERE i.Status = %s
            ORDER BY i.Created_At DESC
        """, (status_filter,))
        rows = cur.fetchall()
        cols = [d[0].lower() for d in cur.description]
        return pd.DataFrame(rows, columns=cols)
    finally:
        cur.close()
        conn.close()


def _confirm_link(issue_id, tx_id_a, tx_id_b):
    """Link the two transactions as a transfer pair and close the issue."""
    conn, cur = _get_conn()
    try:
        # Fetch both transactions
        cur.execute("""
            SELECT Transactions_Id, Accounts_Id, Accounts_Id_Target,
                   Total_Amount, Transfers_Id
            FROM Transactions WHERE Transactions_Id IN (%s, %s)
        """, (tx_id_a, tx_id_b))
        txs = {r[0]: r for r in cur.fetchall()}

        if tx_id_a not in txs or tx_id_b not in txs:
            st.error("One or both transactions no longer exist.")
            return False

        # Generate a new shared Transfers_Id
        cur.execute("SELECT nextval('transfers_id_seq')")
        new_tid = cur.fetchone()[0]

        # Update both rows to share the new Transfers_Id and cross-link targets
        tx_a = txs[tx_id_a]
        tx_b = txs[tx_id_b]

        cur.execute("""
            UPDATE Transactions
            SET Transfers_Id = %s,
                Accounts_Id_Target   = %s,
                Total_Amount_Target  = %s
            WHERE Transactions_Id = %s
        """, (new_tid, tx_b[1], tx_b[3], tx_id_a))

        cur.execute("""
            UPDATE Transactions
            SET Transfers_Id = %s,
                Accounts_Id_Target   = %s,
                Total_Amount_Target  = %s
            WHERE Transactions_Id = %s
        """, (new_tid, tx_a[1], tx_a[3], tx_id_b))

        # Close the issue
        cur.execute("""
            UPDATE Transfer_Issues
            SET Status = 'Confirmed', Resolved_At = NOW()
            WHERE Issue_Id = %s
        """, (issue_id,))

        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        st.error(f"Error confirming link: {e}")
        return False
    finally:
        cur.close()
        conn.close()


def _dismiss_issue(issue_id):
    """Mark the issue as dismissed — keep both transactions as separate."""
    conn, cur = _get_conn()
    try:
        cur.execute("""
            UPDATE Transfer_Issues
            SET Status = 'Dismissed', Resolved_At = NOW()
            WHERE Issue_Id = %s
        """, (issue_id,))
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        st.error(f"Error dismissing issue: {e}")
        return False
    finally:
        cur.close()
        conn.close()


def render_transfer_issues():
    """Render the Transfer Issues review page."""
    st.subheader("⚠️ Transfer Issues — Manual Review")
    st.markdown(
        "These transfers were flagged during import because the two sides "
        "have slightly different dates. Review each pair and either **Confirm** "
        "(link them as one transfer) or **Dismiss** (keep them as separate transactions)."
    )

    tab_open, tab_confirmed, tab_dismissed = st.tabs(
        ["🔴 Open", "✅ Confirmed", "🔕 Dismissed"]
    )

    for tab, status in [(tab_open, "Open"),
                        (tab_confirmed, "Confirmed"),
                        (tab_dismissed, "Dismissed")]:
        with tab:
            df = _load_issues(status)
            if df.empty:
                st.info(f"No {status.lower()} issues.")
                continue

            st.markdown(f"**{len(df)} issue(s)**")

            for _, row in df.iterrows():
                with st.expander(
                    f"Issue #{row.issue_id} — {row.issue_type} — "
                    f"{row.account_a} ↔ {row.account_b}",
                    expanded=(status == "Open")
                ):
                    col1, col2 = st.columns(2)

                    with col1:
                        st.markdown("**Side A**")
                        st.write(f"📅 Date: `{row.date_a}`")
                        st.write(f"🏦 Account: `{row.account_a}`")
                        st.write(f"💰 Amount: `{float(row.amount_a):,.2f}`")
                        st.write(f"📝 Description: `{row.description_a}`")
                        st.write(f"🔑 Transaction ID: `{row.transactions_id_a}`")

                    with col2:
                        st.markdown("**Side B**")
                        st.write(f"📅 Date: `{row.date_b}`")
                        st.write(f"🏦 Account: `{row.account_b}`")
                        st.write(f"💰 Amount: `{float(row.amount_b):,.2f}`")
                        st.write(f"📝 Description: `{row.description_b}`")
                        st.write(f"🔑 Transaction ID: `{row.transactions_id_b}`")

                    if row.notes:
                        st.caption(f"ℹ️ {row.notes}")

                    if status == "Open":
                        st.markdown("---")
                        btn_col1, btn_col2, _ = st.columns([1, 1, 3])
                        with btn_col1:
                            if st.button(
                                "✅ Confirm Link",
                                key=f"confirm_{row.issue_id}",
                                type="primary"
                            ):
                                if _confirm_link(
                                    row.issue_id,
                                    row.transactions_id_a,
                                    row.transactions_id_b
                                ):
                                    st.success("Linked! Refreshing...")
                                    st.rerun()
                        with btn_col2:
                            if st.button(
                                "🔕 Dismiss",
                                key=f"dismiss_{row.issue_id}"
                            ):
                                if _dismiss_issue(row.issue_id):
                                    st.success("Dismissed. Refreshing...")
                                    st.rerun()
                    else:
                        st.caption(f"Resolved at: {row.get('resolved_at') or row.get('Resolved_At', 'N/A')}")
