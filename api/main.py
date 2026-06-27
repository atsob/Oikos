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
from fastapi.middleware.gzip import GZipMiddleware

from api.routers import dashboard, register, reports, static_data, market_data
from api.routers import investments, recurring, ai_router, tools_router, importers_router
from api.routers import securities, bank_router

app = FastAPI(title="Oikos API", version="2.0.0", docs_url="/api/docs", redoc_url="/api/redoc")

app.add_middleware(GZipMiddleware, minimum_size=1000)
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


@app.middleware("http")
async def no_cache_api(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "2.0.0"}


# ── Serve React build (production) ────────────────────────────────────────────
# Only active when frontend/dist exists (i.e. inside Docker). In dev the Vite
# dev server runs separately and proxies /api to this process.
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

_dist = os.path.join(os.path.dirname(__file__), '..', 'frontend', 'dist')
if os.path.isdir(_dist):
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request as StarletteRequest
    from starlette.responses import Response as StarletteResponse

    app.mount('/assets', StaticFiles(directory=os.path.join(_dist, 'assets')), name='assets')

    # PWA and root static files — each needs an explicit route because only /assets is mounted
    @app.get('/favicon.svg',          include_in_schema=False)
    def _f1(): return FileResponse(os.path.join(_dist, 'favicon.svg'), media_type='image/svg+xml')

    @app.get('/icon-192.png',         include_in_schema=False)
    def _f2(): return FileResponse(os.path.join(_dist, 'icon-192.png'), media_type='image/png')

    @app.get('/icon-512.png',         include_in_schema=False)
    def _f3(): return FileResponse(os.path.join(_dist, 'icon-512.png'), media_type='image/png')

    @app.get('/icons.svg',            include_in_schema=False)
    def _f4(): return FileResponse(os.path.join(_dist, 'icons.svg'), media_type='image/svg+xml')

    @app.get('/manifest.webmanifest', include_in_schema=False)
    def _f5(): return FileResponse(os.path.join(_dist, 'manifest.webmanifest'), media_type='application/manifest+json')

    @app.get('/sw.js',                include_in_schema=False)
    def _f6():
        return FileResponse(
            os.path.join(_dist, 'sw.js'),
            media_type='application/javascript',
            headers={'Cache-Control': 'no-cache, no-store, must-revalidate'},
        )

    @app.get('/registerSW.js',        include_in_schema=False)
    def _f7():
        return FileResponse(
            os.path.join(_dist, 'registerSW.js'),
            media_type='application/javascript',
            headers={'Cache-Control': 'no-cache, no-store, must-revalidate'},
        )

    class SPAMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: StarletteRequest, call_next):
            response = await call_next(request)
            path = request.url.path
            if response.status_code == 404 and not path.startswith('/api/') and path != '/api':
                return FileResponse(os.path.join(_dist, 'index.html'))
            return response

    app.add_middleware(SPAMiddleware)
