"""
Vercel entrypoint (root-level `main.py` is one of Vercel's recognized Python
entrypoint filenames). The top-level `app` variable here is an ASGI app
(FastAPI), which Vercel's Python runtime loads directly - no extra config
needed for the framework itself.

This single app serves two things:
  1. The static frontend (static/index.html, styles.css, app.js) - a plain
     HTML/CSS/JS page, no build step, no framework, so there is nothing to
     compile and nothing that can go stale relative to the API.
  2. The JSON scan API under /api/* that the frontend calls with fetch().

Keeping both in one ASGI app (rather than a separate static deploy + a
/api function) means there's exactly one thing to deploy and one place
request routing can go wrong.
"""
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from qa_lite.scanner import analyze, crawl

app = FastAPI(title="QA Lite Agent")

STATIC_DIR = Path(__file__).parent / "static"

# Scan sizes are kept small on purpose so a full scan finishes well inside a
# serverless function's execution time limit (see vercel.json maxDuration
# and the wall-clock budget inside qa_lite/scanner.py's crawl()).
DEFAULT_MAX_PAGES = 8
DEFAULT_MAX_DEPTH = 1
HARD_MAX_PAGES = 15
HARD_MAX_DEPTH = 2


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/scan")
def scan(
    url: str = Query(..., description="Target URL to scan"),
    max_pages: int = Query(DEFAULT_MAX_PAGES, ge=1, le=HARD_MAX_PAGES),
    max_depth: int = Query(DEFAULT_MAX_DEPTH, ge=0, le=HARD_MAX_DEPTH),
):
    """Browser-free crawl + heuristic QA analysis, returned as JSON for the frontend to render."""
    if not (url.startswith("http://") or url.startswith("https://")):
        url = f"https://{url}"
    site_map = crawl(url, max_pages=max_pages, max_depth=max_depth)
    result = analyze(site_map)
    return {"site_map": site_map, "bugs": result["bugs"], "checks": result["checks"]}


# Static assets (CSS/JS) under /static/*
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def home():
    return FileResponse(STATIC_DIR / "index.html")
