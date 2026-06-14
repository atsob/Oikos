import time as _time
import pandas as pd
import streamlit as st
import streamlit.components.v1 as _st_components

# ── Plotly config applied to every chart for better mobile UX ─────────────────
_PLOTLY_CONFIG = {
    "scrollZoom": True,          # pinch-to-zoom and scroll-wheel zoom
    "displayModeBar": "hover",   # toolbar appears only on hover, less clutter on mobile
    "responsive": True,          # reflows when container resizes (orientation change, etc.)
    "modeBarButtonsToRemove": ["select2d", "lasso2d"],  # keep only useful tools
}


def pf_plotly_chart(fig, key=None):
    """Drop-in replacement for st.plotly_chart with mobile-friendly defaults.

    Uses container width, enables scroll/pinch zoom, and hides the toolbar until
    the user hovers — which keeps the mobile view clean.
    """
    st.plotly_chart(fig, width="stretch", config=_PLOTLY_CONFIG, key=key)


def scroll_table_to_bottom():
    """Scroll the data-editor/dataframe immediately above this call to its last row.

    Works correctly even when multiple data editors are on the page (e.g. inside
    different Streamlit tabs that all render simultaneously).  Instead of using a
    fixed index like s[0], the script locates its own <iframe> inside the parent
    document and then finds the last .dvn-scroller that precedes it in the DOM —
    which is always the grid directly above this component.

    A millisecond timestamp is embedded to make the HTML unique every rerun so
    Streamlit never serves a cached (and therefore non-executing) copy.
    """
    ts = int(_time.time() * 1000)
    _st_components.html(
        f"<script>var _ts={ts};"
        "(function(){"
        "function _scroll(){"
        "var doc=window.parent.document;"
        "var scrollers=doc.querySelectorAll('.dvn-scroller');"
        "var frames=doc.querySelectorAll('iframe');"
        "var myFrame=null;"
        "for(var i=0;i<frames.length;i++){try{if(frames[i].contentWindow===window){myFrame=frames[i];break;}}catch(e){}}"
        "if(!myFrame||!scrollers.length)return;"
        "var target=null;"
        "for(var j=0;j<scrollers.length;j++){"
        "if(myFrame.compareDocumentPosition(scrollers[j])&2)target=scrollers[j];}"  # 2 = PRECEDING
        "if(target)target.scrollTop=target.scrollHeight;}"
        "setTimeout(_scroll,400);setTimeout(_scroll,900);setTimeout(_scroll,1600);"
        "})();</script>",
        height=1,
    )


# Βοηθητική συνάρτηση για το χρώμα (πράσινο αν > 0, κόκκινο αν < 0)
def get_color(val):
    return "#28a745" if val >= 0 else "#dc3545"

def color_negative_red(val):
    """Return CSS color for negative values (red), positive (green), zero (blue)."""
    if val < 0:
        return 'color: red'
    elif val > 0:
        return 'color: green'
    else:
        return 'color: blue'

def color_quantity(val):
    """Return CSS color for quantity values."""
    if val < 0:
        return 'color: red'
    elif val > 0:
        return 'color: green'
    else:
        return 'color: blue'

def color_value(val):
    """Return CSS color for value (€) values."""
    if val < 0:
        return 'color: red'
    elif val > 0:
        return 'color: green'
    else:
        return 'color: blue'

def style_qty_display(series_or_df):
    # Δημιουργούμε μια λίστα από κενά styles με το ίδιο μέγεθος
    styles = ['' for _ in range(len(series_or_df))]
    
    for i in range(len(series_or_df)):
        val = series_or_df['qty'].iloc[i] # Κοιτάμε την αριθμητική τιμή της qty
        
        if val < 0:
            styles[i] = 'color: red'
        elif val == 0:
            styles[i] = 'color: blue'
        else:
            styles[i] = 'color: green'
            
    return styles

def format_qty_display(row):
    """Format quantity for display based on type."""
    if row['type'] in ['Cash', 'Assets', 'Pension']:
        val = f"{row['qty']:,.2f}"
        symbols = {'EUR': '€', 'USD': '$', 'GBP': '£'}
        sym = symbols.get(row['curr'], row['curr'])
        return f"{sym} {val}"
    else:
        val = f"{row['qty']:,.8f}"
        if '.' in val:
            val = val.rstrip('0').rstrip('.')
        if val in ["0", "-0"]:
            val = "0"
        return val
    
def copy_df_button(df, key: str, label: str = "📋 Copy") -> None:
    """Render a button that copies the dataframe as TSV to the clipboard (paste directly into Excel).

    Uses execCommand('copy') as the primary path because navigator.clipboard is blocked inside
    Streamlit's sandboxed iframes (requires HTTPS + allow="clipboard-write" which Streamlit
    does not set). execCommand works in all browsers without special permissions.
    """
    raw = df.data if hasattr(df, "data") else df
    if not isinstance(raw, pd.DataFrame):
        raw = pd.DataFrame(raw)
    # If the index carries meaningful labels (named or MultiIndex with names),
    # promote it to columns so it appears in the copy.
    idx = raw.index
    has_named_index = bool(
        getattr(idx, 'name', None) or
        any(n for n in getattr(idx, 'names', []))
    )
    if has_named_index:
        raw = raw.reset_index()
    tsv = raw.to_csv(sep="\t", index=False)
    # Escape characters that would break the JS template literal
    tsv_js = tsv.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")
    html = f"""
    <button id="cpbtn" onclick="
      (function(btn){{
        var ta = document.createElement('textarea');
        ta.value = `{tsv_js}`;
        ta.style.position = 'fixed';
        ta.style.left = '-9999px';
        ta.style.top  = '0';
        document.body.appendChild(ta);
        ta.focus();
        ta.select();
        var ok = false;
        try {{ ok = document.execCommand('copy'); }} catch(e) {{}}
        document.body.removeChild(ta);
        if (ok) {{
          btn.innerText = '✅ Copied!';
          setTimeout(function(){{ btn.innerText = '{label}'; }}, 2000);
        }} else {{
          btn.innerText = '❌ Failed';
          setTimeout(function(){{ btn.innerText = '{label}'; }}, 2000);
        }}
      }})(this);"
      style="background:#262730;color:white;border:1px solid #555;padding:4px 12px;
             border-radius:4px;cursor:pointer;font-size:13px;font-family:sans-serif">
      {label}
    </button>
    """
    _st_components.html(html, height=38)


def insight_card(
    icon: str,
    title: str,
    message: str,
    level: str = "info",   # "info" | "warning" | "danger" | "success"
) -> None:
    """Render a styled actionable insight card on the dashboard."""
    _PALETTE = {
        "info":    ("#3498DB", "rgba(52,152,219,0.12)", "rgba(52,152,219,0.35)"),
        "warning": ("#F39C12", "rgba(243,156,18,0.12)",  "rgba(243,156,18,0.35)"),
        "danger":  ("#E74C3C", "rgba(231,76,60,0.12)",   "rgba(231,76,60,0.35)"),
        "success": ("#2ECC71", "rgba(46,204,113,0.12)",  "rgba(46,204,113,0.35)"),
    }
    fg, bg, border = _PALETTE.get(level, _PALETTE["info"])
    st.markdown(
        f"""
        <div style="
            background:{bg};
            border:1px solid {border};
            border-left:4px solid {fg};
            border-radius:10px;
            padding:12px 16px;
            margin:4px 0;
            display:flex;
            align-items:flex-start;
            gap:12px;
            ">
            <span style="font-size:1.6em;line-height:1.2">{icon}</span>
            <div>
                <div style="font-weight:700;color:{fg};font-size:0.88em;
                            letter-spacing:.02em;margin-bottom:3px">{title}</div>
                <div style="font-size:0.85em;line-height:1.5;opacity:.9">{message}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def dark_mode_css() -> str:
    """Return CSS that forces dark theme colours regardless of Streamlit config."""
    return """
<style>
/* ── Dark Mode overrides ────────────────────────────────────────────── */
.stApp, [data-testid="stAppViewContainer"] {
    background-color: #0e1117 !important;
    color: #fafafa !important;
}
[data-testid="stSidebar"] {
    background-color: #1a1c24 !important;
}
[data-testid="stHeader"] {
    background-color: #0e1117 !important;
}
.stMarkdown, .stText, p, span, label, h1, h2, h3, h4 {
    color: #fafafa !important;
}
[data-testid="stMetricValue"], [data-testid="stMetricLabel"] {
    color: #fafafa !important;
}
.stDataFrame, [data-testid="stDataFrame"] {
    background-color: #1e2030 !important;
}
div[data-baseweb="select"] > div,
div[data-baseweb="input"] > div,
textarea {
    background-color: #262730 !important;
    color: #fafafa !important;
    border-color: #444 !important;
}
[data-testid="stExpander"] {
    background-color: #1e2030 !important;
    border-color: #333 !important;
}
</style>
"""


def custom_metric(label, value, pnl_value):
    # Color definition: Green (>0), Red (<0), Blue (==0)
    if pnl_value > 0:
        color = "#28a745"  # Green
    elif pnl_value < 0:
        color = "#dc3545"  # Red
    else:
        color = "#007bff"  # Blue (Standard Blue)

    st.markdown(
        f"""
        <div style="
            background-color: rgba(0,0,0,0.05); 
            padding: 15px; 
            border-radius: 10px; 
            border-left: 5px solid {color};">
            <p style="color: grey; font-size: 14px; margin: 0; font-family: sans-serif;">{label}</p>
            <p style="color: {color}; font-size: 24px; font-weight: bold; margin: 0; font-family: sans-serif;">
                {value}
            </p>
        </div>
        """, 
        unsafe_allow_html=True
    )
