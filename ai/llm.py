from langchain_ollama import ChatOllama
from curl_cffi import requests as cryq
from config.settings import ENV_CONFIG, OLLAMA_URL

def init_llm(num_predict: int = 400):
    """Initialize the Ollama LLM."""
    return ChatOllama(
        model=ENV_CONFIG['ollama_model'],
        base_url=OLLAMA_URL,
        temperature=0,
        num_predict=num_predict,
        timeout=180,
    )

def get_custom_session():
    """Get custom session for yfinance with impersonation."""
    return cryq.Session(verify=False, impersonate="chrome110")