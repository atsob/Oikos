from langchain_ollama import ChatOllama
from curl_cffi import requests as cryq
from config.settings import ENV_CONFIG, OLLAMA_URL

def init_llm(num_predict: int = 400):
    """Initialize the Ollama LLM.

    ChatOllama has no `timeout` field of its own — passing one directly is silently
    accepted and ignored, leaving the underlying httpx client's default of no timeout
    at all, so a slow or stuck Ollama server can hang a request indefinitely (and, for
    callers that hold a DB connection open across the call, leak it as an
    idle-in-transaction connection that blocks other things). The real place to set it
    is `client_kwargs`, which flows through to httpx.Client(timeout=...). 600s comfortably
    covers this model's normal response time on modest hardware (observed ~9 minutes for
    a 400-token weekly summary) while still eventually failing a truly-stuck request
    instead of hanging forever.
    """
    return ChatOllama(
        model=ENV_CONFIG['ollama_model'],
        base_url=OLLAMA_URL,
        temperature=0,
        num_predict=num_predict,
        client_kwargs={'timeout': 600},
    )

def get_custom_session():
    """Get custom session for yfinance with impersonation."""
    return cryq.Session(verify=False, impersonate="chrome110")