"""Importers top-level router.

Exposes a Bank / Brokerage / QIF radio selector and delegates to the
appropriate section renderer.

  🏦 Bank section:
    - CSV / XLSX bank statement import & reconciliation
    - Revolut Personal account CSV

  📊 Brokerage section:
    - Interactive Brokers (Flex Web Service)
    - Revolut Trading (CSV export)
    - Capital.com (CSV)
    - FxPro (PDF statement)

  📁 QIF section:
    - QIF Importer
    - QIF Transfer Issues
    (Legacy format — not tied to a specific bank or broker)
"""

from __future__ import annotations

import streamlit as st


def render_importers() -> None:
    """Top-level Importers page — Bank / Brokerage / QIF sub-navigation."""
    st.title("📥 Importers")

    section = st.radio(
        "section",
        ["🏦 Bank", "📊 Brokerage", "📁 QIF"],
        horizontal=True,
        label_visibility="collapsed",
        key="importers_section",
    )
    st.divider()

    if section == "🏦 Bank":
        from ui.bank_import import render_bank_section
        render_bank_section()

    elif section == "📊 Brokerage":
        from ui.broker_import import render_brokerage_section
        render_brokerage_section()

    else:  # QIF
        tab_qif, tab_qif_tx = st.tabs([
            "📁 QIF Importer",
            "📝 Transfer Issues",
        ])
        with tab_qif:
            from data.qif_importer import render_qif_importer
            render_qif_importer()
        with tab_qif_tx:
            from data.transfer_issues import render_transfer_issues
            render_transfer_issues()
