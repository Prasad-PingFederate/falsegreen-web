"""falsegreen web service.

Free public-repo scanning as the front door; the paid tier is private repos and
history via a GitHub App. The free scan is the marketing - it has to be instant,
shareable, and uncomfortable enough to be memorable.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from falsegreen.models import Severity

from . import badge as badge_mod
from . import store
from .scanner import ScanError, ScanReport, parse_repo, scan_repository

BASE_DIR = Path(__file__).resolve().parent
PUBLIC_URL = os.getenv("PUBLIC_URL", "http://localhost:8000").rstrip("/")

SCAN_RATE_LIMIT = int(os.getenv("SCAN_RATE_LIMIT", "10"))
SCAN_RATE_WINDOW = int(os.getenv("SCAN_RATE_WINDOW", "3600"))

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]{2,}$")

app = FastAPI(title="falsegreen", docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

static_dir = BASE_DIR.parent / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.on_event("startup")
def _startup() -> None:
    store.init()


def _client_ip(request: Request) -> str:
    # Trust the proxy header only for its first hop; behind Fly/Railway that is
    # the real client. Falls back to the socket peer locally.
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _payload_from(report: ScanReport) -> dict:
    result = report.result
    findings = result.sorted_findings()

    by_rule: dict = {}
    for f in findings:
        entry = by_rule.setdefault(
            f.rule.value,
            {"rule": f.rule.value, "title": f.title, "severity": f.severity.value, "count": 0},
        )
        entry["count"] += 1

    return {
        "score": report.score.value,
        "grade": report.score.grade,
        "headline": report.score.headline,
        "detail": report.score.detail,
        "tests_found": result.total_tests,
        "tests_can_fail": report.score.trustworthy,
        "tests_cannot_fail": result.untrustworthy_tests,
        "files_scanned": result.files_scanned,
        # What the scan could not account for. A Trust Score that quietly
        # covered fewer files than it found would be the same false green this
        # product exists to expose, so the shortfall travels with the score.
        "files_found": report.files_found,
        "files_skipped": report.files_skipped,
        "errors": list(result.errors),
        "counts": {s.value: len(result.by_severity(s)) for s in Severity},
        "by_rule": sorted(by_rule.values(), key=lambda r: -r["count"]),
        "findings": [
            {
                "rule": f.rule.value,
                "severity": f.severity.value,
                "title": f.title,
                "detail": f.detail,
                "file": f.location(result.root),
                "line": f.line,
                "test": f.test_name,
                "snippet": f.snippet,
                "explanation": f.explanation,
            }
            for f in findings
            if f.severity != Severity.LOW
        ][:120],
    }


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "recent": store.recent_scans(8),
            "stats": store.stats(),
            "public_url": PUBLIC_URL,
        },
    )


@app.post("/scan")
def scan(request: Request, repo: str = Form(...)):
    try:
        owner, name = parse_repo(repo)
    except ScanError as err:
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "error": str(err),
                "recent": store.recent_scans(8),
                "stats": store.stats(),
                "public_url": PUBLIC_URL,
            },
            status_code=400,
        )
    return RedirectResponse(url=f"/r/{owner}/{name}", status_code=303)


@app.get("/r/{owner}/{name}", response_class=HTMLResponse)
def report(request: Request, owner: str, name: str, refresh: int = 0):
    slug = f"{owner}/{name}"

    cached = None if refresh else store.get_scan(slug)

    if cached is None:
        ip = _client_ip(request)
        if store.rate_limited(ip, SCAN_RATE_LIMIT, SCAN_RATE_WINDOW):
            return templates.TemplateResponse(
                "index.html",
                {
                    "request": request,
                    "error": (
                        f"Rate limit reached ({SCAN_RATE_LIMIT} scans per hour). "
                        f"Run it locally with no limits: pip install falsegreen"
                    ),
                    "recent": store.recent_scans(8),
                    "stats": store.stats(),
                    "public_url": PUBLIC_URL,
                },
                status_code=429,
            )

        started = time.time()
        try:
            result = scan_repository(slug)
        except ScanError as err:
            return templates.TemplateResponse(
                "index.html",
                {
                    "request": request,
                    "error": str(err),
                    "recent": store.recent_scans(8),
                    "stats": store.stats(),
                    "public_url": PUBLIC_URL,
                },
                status_code=400,
            )

        payload = _payload_from(result)
        payload["duration"] = round(time.time() - started, 1)
        store.save_scan(slug, owner, name, result.score.value, result.score.grade, payload)
        cached = store.get_scan(slug, max_age=0)

    store.touch_view(slug)

    return templates.TemplateResponse(
        "report.html",
        {
            "request": request,
            "slug": slug,
            "owner": owner,
            "name": name,
            "data": cached["payload"],
            "scanned_at": cached["scanned_at"],
            "public_url": PUBLIC_URL,
        },
    )


@app.get("/badge/{owner}/{name}.svg")
def badge(owner: str, name: str):
    record = store.get_scan(f"{owner}/{name}", max_age=0)
    svg = (
        badge_mod.render(record["score"])
        if record
        else badge_mod.render_unknown()
    )
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={
            # Short cache so a fixed suite shows its new score quickly, and
            # GitHub's image proxy does not pin a stale badge for a day.
            "Cache-Control": "public, max-age=1800",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
        },
    )


@app.get("/api/scan/{owner}/{name}")
def api_scan(owner: str, name: str):
    record = store.get_scan(f"{owner}/{name}")
    if not record:
        return JSONResponse({"error": "not scanned yet"}, status_code=404)
    return JSONResponse(
        {
            "repo": f"{owner}/{name}",
            "score": record["score"],
            "grade": record["grade"],
            "scanned_at": record["scanned_at"],
            **{
                k: record["payload"][k]
                for k in ("tests_found", "tests_can_fail", "tests_cannot_fail", "counts")
            },
        }
    )


@app.post("/waitlist")
def waitlist(request: Request, email: str = Form(...), repo_slug: str = Form("")):
    address = (email or "").strip().lower()
    if not EMAIL_RE.match(address) or len(address) > 254:
        return JSONResponse({"ok": False, "error": "Enter a valid email address."}, status_code=400)

    is_new = store.add_to_waitlist(address, repo_slug)
    return JSONResponse(
        {
            "ok": True,
            "new": is_new,
            "message": "You're on the list." if is_new else "You're already on the list.",
        }
    )


@app.get("/health")
def health():
    return {"status": "ok", "waitlist": store.waitlist_size(), **store.stats()}
