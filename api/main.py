"""FastAPI backend for Oikos Personal Finance."""
import sys
import os
import warnings

# Suppress Streamlit cache warnings (crud.py / queries.py use @st.cache_data outside runtime)
warnings.filterwarnings("ignore", message="No runtime found", category=UserWarning)
# Suppress pandas "use SQLAlchemy" advisory — we intentionally use psycopg2 connections
warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy", category=UserWarning)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import dashboard, register, reports, static_data, market_data
from api.routers import investments, recurring, ai_router, tools_router, importers_router
from api.routers import securities, bank_router

app = FastAPI(title="Oikos API", version="2.0.0", docs_url="/api/docs", redoc_url="/api/redoc")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(dashboard.router,           prefix="/api/dashboard",    tags=["dashboard"])
app.include_router(register.router,            prefix="/api/register",     tags=["register"])
app.include_router(reports.router,             prefix="/api/reports",      tags=["reports"])
app.include_router(static_data.router,         prefix="/api/static-data",  tags=["static-data"])
app.include_router(market_data.router,         prefix="/api/market-data",  tags=["market-data"])
app.include_router(investments.router,         prefix="/api/investments",  tags=["investments"])
app.include_router(recurring.router,           prefix="/api/recurring",    tags=["recurring"])
app.include_router(ai_router.router,           prefix="/api/ai",           tags=["ai"])
app.include_router(tools_router.router,        prefix="/api/tools",        tags=["tools"])
app.include_router(importers_router.router,    prefix="/api/importers",    tags=["importers"])
app.include_router(securities.router,          prefix="/api/securities",   tags=["securities"])
app.include_router(bank_router.router,         prefix="/api/bank",         tags=["bank"])


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "2.0.0"}
