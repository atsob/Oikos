"""FastAPI backend for Oikos Personal Finance."""
import sys
import os
import warnings

# On Windows, stdout/stderr default to the console's codepage (e.g. cp1252),
# which can't encode the ✔/⚠️/❌ characters the downloader modules print for
# progress feedback — that raised UnicodeEncodeError mid-function and aborted
# the download before it reached its DB commit, silently discarding otherwise
# fully-fetched data. Reconfigure to UTF-8 so those prints can't crash a run.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="backslashreplace")

# Suppress pandas "use SQLAlchemy" advisory — we intentionally use psycopg2 connections
warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy", category=UserWarning)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contextlib import asynccontextmanager

import bcrypt
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from api.deps import require_auth
from api.routers import auth as auth_router
from api.routers import dashboard, register, reports, static_data, market_data
from api.routers import investments, recurring, ai_router, tools_router, importers_router
from api.routers import securities, bank_router, preferences, news
from config.settings import ENV_CONFIG
from database.queries import bootstrap_admin_user


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Creates the first login account if the Users table is empty and both env
    # vars are set (see .env.example) — idempotent, safe to leave set forever.
    username, password = ENV_CONFIG.get("admin_username"), ENV_CONFIG.get("admin_password")
    if username and password:
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        bootstrap_admin_user(username, password_hash)
    yield


app = FastAPI(title="Oikos API", version="2.0.0", docs_url="/api/docs", redoc_url="/api/redoc", lifespan=lifespan)

app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Not gated — you can't require a session to create one.
app.include_router(auth_router.router,         prefix="/api/auth",         tags=["auth"])

_auth = [Depends(require_auth)]
app.include_router(dashboard.router,           prefix="/api/dashboard",    tags=["dashboard"],    dependencies=_auth)
app.include_router(register.router,            prefix="/api/register",     tags=["register"],     dependencies=_auth)
app.include_router(reports.router,             prefix="/api/reports",      tags=["reports"],      dependencies=_auth)
app.include_router(static_data.router,         prefix="/api/static-data",  tags=["static-data"],  dependencies=_auth)
app.include_router(market_data.router,         prefix="/api/market-data",  tags=["market-data"],  dependencies=_auth)
app.include_router(investments.router,         prefix="/api/investments",  tags=["investments"],  dependencies=_auth)
app.include_router(recurring.router,           prefix="/api/recurring",    tags=["recurring"],    dependencies=_auth)
app.include_router(ai_router.router,           prefix="/api/ai",           tags=["ai"],            dependencies=_auth)
app.include_router(tools_router.router,        prefix="/api/tools",        tags=["tools"],        dependencies=_auth)
app.include_router(importers_router.router,    prefix="/api/importers",    tags=["importers"],    dependencies=_auth)
app.include_router(securities.router,          prefix="/api/securities",   tags=["securities"],   dependencies=_auth)
app.include_router(bank_router.router,         prefix="/api/bank",         tags=["bank"],          dependencies=_auth)
app.include_router(preferences.router,         prefix="/api/preferences",  tags=["preferences"],  dependencies=_auth)
app.include_router(news.router,                prefix="/api/news",        tags=["news"],          dependencies=_auth)


@app.middleware("http")
async def no_cache_api(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "2.0.0"}


@app.get("/api/changelog", dependencies=_auth)
def changelog():
    """Raw CHANGELOG.md content, rendered by the in-app Release Notes page.

    Single source of truth: the same file is what's browsed on GitHub, so the two
    never drift apart the way a duplicated in-app copy would.
    """
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    path = os.path.join(repo_root, "CHANGELOG.md")
    try:
        with open(path, encoding="utf-8") as fh:
            return {"content": fh.read()}
    except FileNotFoundError:
        return {"content": "# Changelog\n\nNo changelog found."}


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
    @app.get('/favicon.png',          include_in_schema=False)
    def _f1(): return FileResponse(os.path.join(_dist, 'favicon.png'), media_type='image/png')

    @app.get('/logo.png',             include_in_schema=False)
    def _f1b(): return FileResponse(os.path.join(_dist, 'logo.png'), media_type='image/png')

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
