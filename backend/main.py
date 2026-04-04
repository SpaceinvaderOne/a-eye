from __future__ import annotations

import logging
import secrets
from base64 import b64decode
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse, Response

from backend.auth import COOKIE_NAME, SESSION_TTL, create_session, verify_session

import pillow_heif
pillow_heif.register_heif_opener()

from backend.config import get_settings
from backend.database import init_db, get_image, get_outcome_stats, get_stats, list_images, get_rename_history, count_images, count_rename_history
from backend.ollama_client import OllamaClient
from backend.prompts import ensure_defaults, get_active_prompt, STAGE_VISION, STAGE_CONTEXT
from backend.routes import create_api_router
from backend.scheduler import Scheduler
from backend.watcher import FileWatcher
from backend.worker import WorkerQueue
from backend.workspace import Workspace

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Resolve paths relative to the project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATES_DIR = _PROJECT_ROOT / "frontend" / "templates"
_STATIC_DIR = _PROJECT_ROOT / "frontend" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle for the app."""
    settings = get_settings()
    app.state.settings = settings

    # Ensure data directories exist
    data_dir = Path(settings.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "thumbnails").mkdir(exist_ok=True)

    # Database
    db_path = str(data_dir / "photo_renamer.db")
    db = await init_db(db_path)
    app.state.db = db

    # Prompt library — ensure defaults exist, load active templates
    await ensure_defaults(db)
    vision_prompt = await get_active_prompt(db, STAGE_VISION)
    context_prompt = await get_active_prompt(db, STAGE_CONTEXT)

    # Ollama client
    ollama = OllamaClient(
        host=settings.ollama_host,
        vision_model=settings.vision_model,
        llm_model=settings.llm_model,
    )
    ollama.set_templates(
        vision_prompt["content"] if vision_prompt else "",
        context_prompt["content"] if context_prompt else "",
    )
    app.state.ollama = ollama

    # Worker queue
    worker = WorkerQueue(db=db, settings=settings, ollama=ollama)
    app.state.worker = worker
    await worker.start()

    # File watcher
    watcher = FileWatcher(db=db, settings=settings, worker=worker)
    app.state.watcher = watcher

    # Scheduler (controls worker pause/resume based on time window)
    scheduler = Scheduler(settings=settings, worker=worker, watcher=watcher)
    app.state.scheduler = scheduler
    await scheduler.start()

    # Start watch mode if configured
    if settings.watch_mode:
        await watcher.start()

    # Workspace (temporary processing area)
    workspace_dir = Path(settings.workspace_dir) if settings.workspace_dir else data_dir / "workspace"
    workspace = Workspace(workspace_dir=workspace_dir, settings=settings, ollama=ollama)
    await workspace.init()
    app.state.workspace = workspace

    # Detect read-only photos mount
    photos_path = Path(settings.photos_dir)
    photos_readonly = False
    if photos_path.exists():
        test_file = photos_path / ".a-eye-write-test"
        try:
            test_file.touch()
            test_file.unlink()
        except (OSError, PermissionError):
            photos_readonly = True
            logger.info("Photos directory is read-only — catalogue-only mode")
    app.state.photos_readonly = photos_readonly
    worker._photos_readonly = photos_readonly

    logger.info(
        "A-Eye started — mode=%s, photos=%s (%s), ollama=%s",
        settings.rename_mode, settings.photos_dir,
        "read-only" if photos_readonly else "read-write",
        settings.ollama_host,
    )

    yield

    # Shutdown
    await workspace.close()
    await scheduler.stop()
    await watcher.stop()
    await worker.stop()
    await ollama.close()
    await db.close()
    logger.info("A-Eye shut down")


class AuthMiddleware(BaseHTTPMiddleware):
    """Cookie + Basic Auth. Never sends WWW-Authenticate (no browser dialog)."""

    async def dispatch(self, request: Request, call_next):
        settings = getattr(request.app.state, "settings", None)
        if not settings or not settings.basic_auth_user or not settings.basic_auth_pass:
            return await call_next(request)

        # Allow wizard, login page, and essential API endpoints without auth
        skip_exact = ("/api/health", "/api/models", "/api/onboard/settings", "/login", "/api/login")
        skip_prefix = ("/onboard", "/static/")
        path = request.url.path
        if path in skip_exact or any(path.startswith(p) for p in skip_prefix):
            return await call_next(request)

        # 1. Check session cookie
        cookie = request.cookies.get(COOKIE_NAME)
        if cookie and verify_session(cookie, settings.basic_auth_user, settings.basic_auth_pass):
            return await call_next(request)

        # 2. Check Basic Auth header (for API clients like curl)
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Basic "):
            try:
                decoded = b64decode(auth_header[6:]).decode("utf-8")
                user, password = decoded.split(":", 1)
            except (ValueError, UnicodeDecodeError):
                return JSONResponse({"detail": "Invalid credentials"}, status_code=401)

            user_ok = secrets.compare_digest(user, settings.basic_auth_user)
            pass_ok = secrets.compare_digest(password, settings.basic_auth_pass)
            if user_ok and pass_ok:
                return await call_next(request)

        # 3. Reject — redirect browsers to /login, return 401 JSON for API
        accept = request.headers.get("Accept", "")
        if "text/html" in accept:
            return RedirectResponse("/login", status_code=302)
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)


class SetupRedirectMiddleware(BaseHTTPMiddleware):
    """Redirects all page routes to /onboard when setup_complete is False."""

    async def dispatch(self, request: Request, call_next):
        settings = getattr(request.app.state, "settings", None)
        if settings and not settings.setup_complete:
            path = request.url.path
            # Don't redirect API calls, static files, or the onboard page itself
            if not path.startswith(("/api/", "/static/", "/onboard")):
                return RedirectResponse(url="/onboard", status_code=303)
        return await call_next(request)


app = FastAPI(title="A-Eye", lifespan=lifespan)

# Middleware stack (Starlette runs LIFO — last registered runs first)
# 1. Auth checks cookie/Basic credentials
# 2. SetupRedirect sends to /onboard if not set up yet (runs first due to LIFO)
app.add_middleware(AuthMiddleware)
app.add_middleware(SetupRedirectMiddleware)

# Mount static files and templates
if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR)) if _TEMPLATES_DIR.exists() else None

# API routes
app.include_router(create_api_router())


def _page_context(request: Request) -> dict:
    """Common template context for all page routes."""
    settings = request.app.state.settings
    photos_readonly = getattr(request.app.state, "photos_readonly", False)
    catalogue_mode = photos_readonly or settings.catalogue_mode
    auth_enabled = bool(settings.basic_auth_user and settings.basic_auth_pass)
    return {
        "settings": settings,
        "photos_readonly": photos_readonly,
        "catalogue_mode": catalogue_mode,
        "auth_enabled": auth_enabled,
    }


# -- Page routes (server-rendered HTMX) -------------------------------------

@app.get("/", response_class=HTMLResponse)
async def page_dashboard(request: Request):
    if not templates:
        return HTMLResponse("<h1>A-Eye</h1><p>Frontend templates not found. API is available at /api/</p>")
    db = request.app.state.db
    stats = await get_stats(db)
    outcomes = await get_outcome_stats(db)
    ctx = _page_context(request)
    ctx.update({"stats": stats, "outcomes": outcomes})
    return templates.TemplateResponse(request, name="dashboard.html", context=ctx)


@app.get("/onboard", response_class=HTMLResponse)
async def page_onboard(request: Request):
    settings = request.app.state.settings
    if settings.setup_complete:
        return RedirectResponse(url="/", status_code=303)
    if not templates:
        return HTMLResponse("<p>Templates not found</p>")
    photos_readonly = getattr(request.app.state, "photos_readonly", False)
    return templates.TemplateResponse(request, name="onboard.html", context={
        "settings": settings,
        "photos_readonly": photos_readonly,
    })


@app.get("/login", response_class=HTMLResponse)
async def page_login(request: Request):
    settings = request.app.state.settings
    # If already authenticated via cookie, redirect to dashboard
    if settings.basic_auth_user and settings.basic_auth_pass:
        cookie = request.cookies.get(COOKIE_NAME)
        if cookie and verify_session(cookie, settings.basic_auth_user, settings.basic_auth_pass):
            return RedirectResponse("/", status_code=302)
    # If auth not enabled, no need for login page
    if not settings.basic_auth_user or not settings.basic_auth_pass:
        return RedirectResponse("/", status_code=302)
    if not templates:
        return HTMLResponse("<p>Templates not found</p>")
    return templates.TemplateResponse(request, name="login.html", context={
        "settings": settings,
    })


class _LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/api/login")
async def api_login(request: Request, body: _LoginRequest):
    settings = request.app.state.settings
    if not settings.basic_auth_user or not settings.basic_auth_pass:
        return JSONResponse({"detail": "Auth not configured"}, status_code=400)

    user_ok = secrets.compare_digest(body.username, settings.basic_auth_user)
    pass_ok = secrets.compare_digest(body.password, settings.basic_auth_pass)
    if not (user_ok and pass_ok):
        return JSONResponse({"detail": "Invalid credentials"}, status_code=401)

    cookie_value = create_session(settings.basic_auth_user, settings.basic_auth_pass)
    response = JSONResponse({"success": True})
    response.set_cookie(
        key=COOKIE_NAME,
        value=cookie_value,
        max_age=SESSION_TTL,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response


@app.post("/api/logout")
async def api_logout():
    response = JSONResponse({"success": True})
    response.delete_cookie(key=COOKIE_NAME, path="/")
    return response


@app.get("/queue", response_class=HTMLResponse)
async def page_queue(request: Request, status: str | None = None, page: int = 1, source: str = "library", sort: str = "created_at", sort_dir: str = "desc"):
    if not templates:
        return HTMLResponse("<p>Templates not found</p>")

    workspace_mode = source == "workspace"
    if workspace_mode:
        ws = getattr(request.app.state, "workspace", None)
        if not ws or not ws.db:
            return HTMLResponse("<p>Workspace not available</p>")
        db = ws.db
        api_prefix = "/api/workspace"
    else:
        db = request.app.state.db
        api_prefix = "/api"

    limit = 50
    offset = (page - 1) * limit
    images = await list_images(db, status=status, offset=offset, limit=limit, sort=sort, sort_dir=sort_dir)
    total = await count_images(db, status=status)
    total_pages = max(1, (total + limit - 1) // limit)
    stats = await get_stats(db)
    ctx = _page_context(request)
    ctx.update({
        "images": images, "status_filter": status, "page": page,
        "total_pages": total_pages, "total": total, "stats": stats,
        "api_prefix": api_prefix, "workspace_mode": workspace_mode, "source": source,
        "sort": sort, "sort_dir": sort_dir,
    })
    return templates.TemplateResponse(request, name="queue.html", context=ctx)


@app.get("/review", response_class=HTMLResponse)
async def page_review(request: Request, page: int = 1, source: str = "library"):
    if not templates:
        return HTMLResponse("<p>Templates not found</p>")

    workspace_mode = source == "workspace"
    if workspace_mode:
        ws = getattr(request.app.state, "workspace", None)
        if not ws or not ws.db:
            return HTMLResponse("<p>Workspace not available</p>")
        db = ws.db
        api_prefix = "/api/workspace"
    else:
        db = request.app.state.db
        api_prefix = "/api"

    limit = 20
    offset = (page - 1) * limit
    images = await list_images(db, status="proposed", offset=offset, limit=limit)
    total = await count_images(db, status="proposed")
    total_pages = max(1, (total + limit - 1) // limit)
    ctx = _page_context(request)
    ctx.update({
        "images": images, "page": page, "total_pages": total_pages,
        "total": total, "api_prefix": api_prefix,
        "workspace_mode": workspace_mode, "source": source,
    })
    return templates.TemplateResponse(request, name="review.html", context=ctx)


@app.get("/settings", response_class=HTMLResponse)
async def page_settings(request: Request):
    if not templates:
        return HTMLResponse("<p>Templates not found</p>")
    ctx = _page_context(request)
    return templates.TemplateResponse(request, name="settings.html", context=ctx)


@app.get("/search", response_class=HTMLResponse)
async def page_search(request: Request):
    if not templates:
        return HTMLResponse("<p>Templates not found</p>")
    ctx = _page_context(request)
    return templates.TemplateResponse(request, name="search.html", context=ctx)


@app.get("/mosaic", response_class=HTMLResponse)
async def page_mosaic(request: Request):
    if not templates:
        return HTMLResponse("<p>Templates not found</p>")
    ctx = _page_context(request)
    return templates.TemplateResponse(request, name="mosaic.html", context=ctx)


@app.get("/history", response_class=HTMLResponse)
async def page_history(request: Request, page: int = 1):
    if not templates:
        return HTMLResponse("<p>Templates not found</p>")
    db = request.app.state.db
    limit = 50
    offset = (page - 1) * limit
    history = await get_rename_history(db, offset=offset, limit=limit)
    total = await count_rename_history(db)
    total_pages = max(1, (total + limit - 1) // limit)
    for entry in history:
        entry["image"] = await get_image(db, entry["image_id"])
    ctx = _page_context(request)
    ctx.update({"history": history, "page": page, "total_pages": total_pages})
    return templates.TemplateResponse(request, name="history.html", context=ctx)


# Code to recover stuck images when the app restarts

class WorkerQueue:
    def __init__(self):
        # Current initialization code
        self.reset_stuck_images()

    def reset_stuck_images(self):
        """Reset images stuck in processing state to pending."""
        stuck_images = self.get_images_with_status('processing')
        for image in stuck_images:
            image.status = 'pending'
            image.save()

    def get_images_with_status(self, status):
        # Dummy implementation, this should interact with your image storage mechanism
        return []  # Return images based on status

# The WorkerQueue class should be instantiated during the app's startup process
