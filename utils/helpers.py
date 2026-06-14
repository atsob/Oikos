import warnings
import urllib3

def configure_warnings_and_ssl():
    """Configure warnings and SSL settings."""
    warnings.filterwarnings('ignore', category=UserWarning)
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # Streamlit's data_editor triggers a pandas FutureWarning about DataFrame
    # concatenation with empty/all-NA columns when adding new rows (num_rows="dynamic").
    # This originates inside Streamlit's own data_editor.py and cannot be fixed here —
    # suppress it until Streamlit resolves it upstream.
    warnings.filterwarnings(
        'ignore',
        message="The behavior of DataFrame concatenation with empty or all-NA entries",
        category=FutureWarning,
        module=r"streamlit\.elements\.widgets\.data_editor",
    )