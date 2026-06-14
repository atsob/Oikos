import streamlit as st
import pandas as pd
import datetime as dt_lib

def init_session_state():
    """Initialize all session state variables."""
    defaults = {
        'current_tx_id': None,
        'selected_acc_index': 0,
        'account_id_internal': None,
        'show_splits_pane': False,
        'inv_date_val': pd.Timestamp(dt_lib.date.today().year - 1, 12, 31),
        'nwr_date_val': pd.Timestamp(2000, 1, 1),
    }
    
    for key, default_value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = default_value