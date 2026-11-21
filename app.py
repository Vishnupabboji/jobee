"""
app.py — Main entry point for Jobee.

Runs local FastAPI server + Jinja2 UI + APScheduler background scanner.
Launch with:
    python app.py
or double-click run.bat
"""

from __future__ import annotations

import csv
import io
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import uvicorn
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from adapters.reddit_search import import_reddit_post_to_db, search_reddit_jobs
from core import db as jobee_db
from core.config_loader import load_config, save_config, update_config
from core.jd_scraper import fetch_live_jd
from core.models import AppConfig
from core.pipeline import run_scan
from integrations import drive_sync, google_auth, sheets_sync

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("jobee")

ROOT = Path(__file__).resolve().parent
TEMPLATES_DIR = ROOT / "ui" / "templates"
STATIC_DIR = ROOT / "ui" / "static"
RESUMES_DIR = ROOT / "resumes"
BASE_RESUME_FILE = RESUMES_DIR / "base_resume.pdf"

# Initialize APScheduler
scheduler = BackgroundScheduler()


def _scheduled_scan_job():
    log.info("APScheduler: running scheduled scan...")
    cfg = load_config()
    resume_url = drive_sync.get_cached_resume_url()
    run_scan(cfg, resume_drive_url=resume_url)


def _reconfigure_scheduler(hours: int):
    scheduler.remove_all_jobs()
    if hours > 0:
        scheduler.add_job(
            _scheduled_scan_job,
            "interval",
            hours=hours,
            id="jobee_scan_job",
            replace_existing=True,
        )
        log.info("Scheduler configured: every %d hours.", hours)
    else:
        log.info("Scheduler turned off.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    jobee_db.init_db()
    RESUMES_DIR.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    if not getattr(app.state, "testing", False):
        try:
            scheduler.start()
            _reconfigure_scheduler(cfg.schedule_hours)
        except Exception as exc:
            log.warning("Could not start scheduler: %s", exc)
        log.info("Jobee server running at http://localhost:8000")
    yield
    # Shutdown
    if scheduler.running:
        scheduler.shutdown(wait=False)
        log.info("Jobee scheduler shut down.")


app = FastAPI(title="Jobee", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ---------------------------------------------------------------------------
# HTML Tab Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def tab_jobs(
    request: Request,
    source: Optional[str] = None,
    status: Optional[str] = None,
    role: Optional[str] = None,
    location: Optional[str] = None,
    q: Optional[str] = None,
):
    cfg = load_config()
    all_jobs = jobee_db.get_all_jobs(status=status, source=source, role=role, location=location, keyword=q)
    now = datetime.now(timezone.utc)

    # Compute hours_old and format
    formatted_jobs = []
    for j in all_jobs:
        posted_str = j.get("posted_at_utc") or j.get("first_seen_utc")
        hours_old = 0.0
        if posted_str:
            try:
                p_dt = datetime.fromisoformat(posted_str)
                if p_dt.tzinfo is None:
                    p_dt = p_dt.replace(tzinfo=timezone.utc)
                hours_old = round((now - p_dt).total_seconds() / 3600, 1)
            except Exception:
                pass
        j_copy = dict(j)
        j_copy["hours_old"] = hours_old
        formatted_jobs.append(j_copy)

    # Stats
    raw_all = jobee_db.get_all_jobs()
    stats = {
        "total": len(raw_all),
        "to_apply": sum(1 for x in raw_all if x.get("status") == "To Apply"),
        "applied": sum(1 for x in raw_all if x.get("status") == "Applied"),
        "synced": sum(1 for x in raw_all if x.get("synced_to_sheet") == 1),
    }

    return templates.TemplateResponse(
        request=request,
        name="jobs.html",
        context={
            "active_tab": "jobs",
            "jobs": formatted_jobs,
            "config": cfg,
            "stats": stats,
            "request_params": {
                "source": source or "",
                "status": status or "",
                "role": role or "",
                "location": location or "",
                "q": q or "",
            },
        },
    )


@app.get("/reddit", response_class=HTMLResponse)
async def tab_reddit(request: Request):
    cfg = load_config()
    return templates.TemplateResponse(
        request=request,
        name="reddit.html",
        context={
            "active_tab": "reddit",
            "config": cfg,
        },
    )


@app.get("/resume", response_class=HTMLResponse)
async def tab_resume(request: Request):
    cfg = load_config()
    resume_exists = BASE_RESUME_FILE.exists()
    resume_drive_url = drive_sync.get_cached_resume_url()
    is_authed = google_auth.is_authenticated()

    return templates.TemplateResponse(
        request=request,
        name="resume.html",
        context={
            "active_tab": "resume",
            "config": cfg,
            "resume_exists": resume_exists,
            "resume_drive_url": resume_drive_url,
            "google_auth": is_authed,
        },
    )


@app.get("/filters", response_class=HTMLResponse)
async def tab_filters(request: Request):
    cfg = load_config()
    return templates.TemplateResponse(
        request=request,
        name="filters.html",
        context={
            "active_tab": "filters",
            "config": cfg,
        },
    )


@app.get("/logs", response_class=HTMLResponse)
async def tab_logs(request: Request):
    cfg = load_config()
    runs = jobee_db.get_scrape_runs(limit=100)
    return templates.TemplateResponse(
        request=request,
        name="logs.html",
        context={
            "active_tab": "logs",
            "config": cfg,
            "runs": runs,
        },
    )


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/scan")
async def api_trigger_scan():
    cfg = load_config()
    resume_url = drive_sync.get_cached_resume_url()
    summary = run_scan(cfg, resume_drive_url=resume_url)
    return {"success": True, "summary": summary}


@app.post("/api/sync/sheets")
async def api_trigger_sheets_sync():
    cfg = load_config()
    if not cfg.google_sheet_id:
        return {"success": False, "error": "Google Sheet ID is not set in config."}
    
    count, err = sheets_sync.sync_jobs_to_sheet(cfg.google_sheet_id)
    if err:
        return {"success": False, "error": err}
    return {"success": True, "count": count}


class StatusUpdateReq(BaseModel):
    status: str


@app.post("/api/jobs/{job_hash}/status")
async def api_update_status(job_hash: str, req: StatusUpdateReq):
    ok = jobee_db.update_job_status(job_hash, req.status)
    if not ok:
        raise HTTPException(status_code=404, detail="Job not found")

    cfg = load_config()
    if cfg.google_sheet_id and google_auth.is_authenticated():
        # Update row in Google Sheet asynchronously or synchronously
        sheets_sync.update_status_in_sheet(cfg.google_sheet_id, job_hash, req.status)

    return {"success": True, "status": req.status}


@app.post("/api/jobs/{job_hash}/fetch-jd")
async def api_fetch_live_jd(job_hash: str):
    job = jobee_db.get_job_by_hash(job_hash)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    apply_url = job.get("apply_url")
    if not apply_url:
        return {"success": False, "error": "No application URL available to scrape."}

    # Scrape live from web
    cleaned_jd = fetch_live_jd(apply_url)
    # Save to SQLite
    jobee_db.update_job_jd(job_hash, cleaned_jd)

    return {"success": True, "jd_text": cleaned_jd}


class CustomScanReq(BaseModel):
    role: Optional[str] = ""
    location: Optional[str] = "Hyderabad"


@app.post("/api/scan/custom")
async def api_scan_custom(req: CustomScanReq):
    base_cfg = load_config()

    role_val = (req.role or "").strip()
    loc_val = (req.location or "Hyderabad").strip() or "Hyderabad"

    target_roles = [role_val] if role_val else base_cfg.roles

    # Create targeted config for this custom scan
    custom_cfg = AppConfig(
        roles=target_roles,
        locations=[loc_val],
        max_years_experience=base_cfg.max_years_experience,
        exclude_title_keywords=base_cfg.exclude_title_keywords,
        enable_jobspy=True,
        jobspy_sites=base_cfg.jobspy_sites,
        enable_reddit=False,
        enable_ats=base_cfg.enable_ats,
        ats_slugs=base_cfg.ats_slugs,
        fuzzy_threshold=base_cfg.fuzzy_threshold,
    )

    resume_url = drive_sync.get_cached_resume_url()
    summary = run_scan(custom_cfg, resume_drive_url=resume_url)
    total_new = sum(stats.get("new", 0) for stats in summary.values())

    return {"success": True, "total_new": total_new, "summary": summary}


class RedditSearchReq(BaseModel):
    query: Optional[str] = ""
    location: Optional[str] = "Hyderabad"


@app.post("/api/reddit/search")
async def api_reddit_search(req: RedditSearchReq):
    q = (req.query or "").strip()
    loc = (req.location or "Hyderabad").strip()
    posts = search_reddit_jobs(query=q, location=loc, limit=30)
    return {"success": True, "posts": posts}


class RedditImportReq(BaseModel):
    post: dict
    location: Optional[str] = "Hyderabad"


@app.post("/api/reddit/import")
async def api_reddit_import(req: RedditImportReq):
    ok, msg = import_reddit_post_to_db(req.post, default_location=req.location or "Hyderabad")
    return {"success": ok, "message": msg}


class ScheduleReq(BaseModel):
    hours: int


@app.post("/api/settings/schedule")
async def api_update_schedule(req: ScheduleReq):
    cfg = update_config({"schedule_hours": req.hours})
    _reconfigure_scheduler(cfg.schedule_hours)
    return {"success": True, "schedule_hours": cfg.schedule_hours}


@app.post("/api/settings/filters")
async def api_save_filters(
    roles: str = Form(...),
    locations: str = Form(...),
    max_years_experience: int = Form(...),
    exclude_title_keywords: str = Form(...),
    fuzzy_threshold: int = Form(...),
    enable_jobspy: Optional[str] = Form(None),
    enable_reddit: Optional[str] = Form(None),
    subreddits: Optional[str] = Form(""),
    enable_ats: Optional[str] = Form(None),
    greenhouse_slugs: Optional[str] = Form(""),
    lever_slugs: Optional[str] = Form(""),
):
    parsed_roles = [r.strip() for r in roles.replace("\r", "").split("\n") if r.strip()]
    parsed_locations = [l.strip() for l in locations.replace("\r", "").split("\n") if l.strip()]
    parsed_excludes = [e.strip() for e in exclude_title_keywords.replace("\r", "").split("\n") if e.strip()]
    parsed_subs = [s.strip() for s in (subreddits or "").split(",") if s.strip()]
    parsed_gh = [s.strip() for s in (greenhouse_slugs or "").split(",") if s.strip()]
    parsed_lv = [s.strip() for s in (lever_slugs or "").split(",") if s.strip()]

    update_config({
        "roles": parsed_roles,
        "locations": parsed_locations,
        "max_years_experience": max_years_experience,
        "exclude_title_keywords": parsed_excludes,
        "fuzzy_threshold": fuzzy_threshold,
        "enable_jobspy": bool(enable_jobspy),
        "enable_reddit": bool(enable_reddit),
        "subreddits": parsed_subs,
        "enable_ats": bool(enable_ats),
        "ats_slugs": {
            "greenhouse": parsed_gh,
            "lever": parsed_lv,
        },
    })
    return RedirectResponse(url="/filters", status_code=303)


@app.post("/api/settings/google")
async def api_save_google_settings(
    google_sheet_id: str = Form(""),
    google_drive_folder_id: str = Form(""),
):
    update_config({
        "google_sheet_id": google_sheet_id.strip(),
        "google_drive_folder_id": google_drive_folder_id.strip(),
    })
    return RedirectResponse(url="/resume", status_code=303)


@app.post("/api/resume/upload")
async def api_upload_resume(file: UploadFile = File(...)):
    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    content = await file.read()
    with BASE_RESUME_FILE.open("wb") as f:
        f.write(content)

    cfg = load_config()
    # Try uploading to Drive if authed
    if google_auth.is_authenticated():
        drive_sync.upload_base_resume(drive_folder_id=cfg.google_drive_folder_id)

    return RedirectResponse(url="/resume", status_code=303)


@app.get("/api/export/csv")
async def api_export_csv():
    jobs = jobee_db.get_all_jobs()
    output = io.StringIO()
    writer = csv.writer(output)

    headers = [
        "Date Found", "Source", "Company", "Title", "Location",
        "Apply Type", "Apply Link", "Contact Info", "Status",
        "Resume Mode", "Resume Link", "Job Hash"
    ]
    writer.writerow(headers)

    for j in jobs:
        writer.writerow([
            (j.get("first_seen_utc") or "")[:10],
            j.get("source", ""),
            j.get("company", ""),
            j.get("title", ""),
            j.get("location", ""),
            j.get("apply_type", ""),
            j.get("apply_url", ""),
            j.get("contact_info", ""),
            j.get("status", ""),
            j.get("resume_mode_used", ""),
            j.get("resume_drive_url", ""),
            j.get("job_hash", ""),
        ])

    output.seek(0)
    filename = f"jobee_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False)
