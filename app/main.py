from pathlib import Path
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
import os

from app.config import settings
from app.state import get_bundle
from app.routers import query as query_router
from app.routers import feedback as feedback_router
from app.routers import dashboard as dashboard_router

app = FastAPI(title="RAG Hybrid API", version="1.0.0")

origins = [o.strip() for o in settings.allow_origins.split(",")] if settings.allow_origins else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = PROJECT_ROOT / "static"

# --------------------------
#  API ROUTING FIX (CORE FIX)
# --------------------------

# Global default client API
#app.include_router(query_router.router, prefix="/api")

# Dynamic tenant API:
# /maran/api/ask
# /rsms/api/history
app.include_router(query_router.router, prefix="/{client_id}/api")

# --------------------------
#  FEEDBACK & DASHBOARD APIs
# --------------------------

# Feedback endpoints: /api/feedback/submit, /api/feedback/dashboard, etc.
# Mounted at /api/feedback (nginx proxies /api/* to backend)
# The frontend feedback-integration.js uses window.API_BASE + '/feedback/submit'
# which resolves to /{client_id}/api/feedback/submit -> nginx proxies to backend
app.include_router(feedback_router.router, prefix="/api/feedback", tags=["feedback"])

# Dashboard endpoints: /api/dashboard/overview, /api/dashboard/queries, etc.
app.include_router(dashboard_router.router, prefix="/api/dashboard", tags=["dashboard"])


# --------------------------
#  DOCUMENT SERVING
# --------------------------

DOCS_BASE_PATH = Path(os.getenv("BASE_DIR", "/opt/sms-rag/data"))

@app.get("/{client_id}/docs/{filename:path}")
def serve_document(client_id: str, filename: str):
    base = (DOCS_BASE_PATH / client_id / "documents").resolve()
    full = (base / filename).resolve()

    if not str(full).startswith(str(base)) or not full.is_file():
        logger.error(f"Document not found: {full}")
        raise HTTPException(status_code=404, detail="Document not found")

    logger.info(f"Serving: {full}")
    return FileResponse(str(full))


# --------------------------
#  STATIC HTML PAGES
# --------------------------

@app.get("/login.html", response_class=HTMLResponse)
def serve_login():
    return FileResponse(STATIC_DIR / "login.html", media_type="text/html")

@app.get("/dashboard.html", response_class=HTMLResponse)
def serve_dashboard():
    return FileResponse(STATIC_DIR / "dashboard.html", media_type="text/html")

@app.get("/feedback-dashboard.html", response_class=HTMLResponse)
def serve_feedback_dashboard():
    return FileResponse(STATIC_DIR / "feedback-dashboard.html", media_type="text/html")

@app.get("/feedback-integration.js")
def serve_feedback_js():
    return FileResponse(STATIC_DIR / "feedback-integration.js", media_type="application/javascript")


# --------------------------
#  SPA ROUTER (MUST BE LAST)
# --------------------------

def _index_file() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")

@app.get("/", response_class=HTMLResponse)
def serve_root():
    return _index_file()

# IMPORTANT: ALWAYS KEEP THIS LAST
@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    """Serve index.html for any unknown route (after API routes)."""
    # Serve static files directly if they exist
    static_file = STATIC_DIR / full_path
    if static_file.is_file():
        return FileResponse(str(static_file))
    return _index_file()
