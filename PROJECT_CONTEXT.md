# PROJECT_CONTEXT.md — Jobee

> **Operating Rule:** Read this file at the start of EVERY session or milestone before writing any code.
> After completing each milestone, update the Progress Log and Milestone Checklist below.

---

## Project Vision

**Jobee** is a 100% local-first, privacy-focused job discovery and application-tracking web application.
It runs entirely on `localhost` (Python 3.12 + FastAPI + SQLite). No paid APIs. No cloud processing.
Sensitive data never leaves the machine unless the user explicitly configures Google Sheets/Drive sync.

**Project location:** `d:\vishnu personal\jobee\`

---

## Version Roadmap

| Version | Status | Scope |
|---|---|---|
| **V1** | 🟢 **ACTIVE — COMPLETE** | 24h multi-source job scraper, fuzzy deduplication, SQLite WAL, Google Drive/Sheets sync, minimal B&W web dashboard, background scheduler, and CSV export. |
| **V2** | 🔒 Locked — Stub Only | Local ATS resume tailoring (RenderCV + Resume-Matcher + Ollama/BYOK) → tailored PDFs per job. Interfaces defined in `resumes_engine/base.py`. |
| **V3** | 🔒 Out of Scope | Human-in-the-loop browser auto-applier. |

**CRITICAL:** Do NOT build V2/V3 features until prompted. Only clean stub interfaces are maintained so they plug in without refactoring.

---

## Non-Negotiable Constraints

1. **100% local, zero paid APIs:** Runs on localhost. No OpenAI or paid scraping keys required.
2. **Strict <24h freshness:** Every saved job must have been posted within 24.0 hours (timezone-aware UTC). Anything older or unparseable is dropped.
3. **Plugin-Adapter Pattern:** Every job source inherits from `BaseAdapter` (`adapters/base.py`) → returns `List[JobPosting]`. Isolated execution with try/except so one failure never crashes others.
4. **Resume Mode from day 1:** Mode 1 (`single_base`) active in V1. Mode 2 (`ats_tailored`) visible in UI as locked "Coming in V2".
5. **Local-only fallback:** App runs 100% locally (SQLite + web dashboard + CSV export) even without Google `credentials.json`.
6. **Config-driven, zero hardcoded personal data:** All roles, locations, subreddits, slugs, filters live in `config.yaml` and are editable via the UI.
7. **UI:** Minimal black & white (#FFFFFF background, #000000 text/borders), Arial font only, light themed, no color accents.

---

## Tech Stack

| Layer | Choice |
|---|---|
| Language | Python 3.12 |
| Web framework | FastAPI + Jinja2 templates (vanilla HTML/CSS/JS, zero npm, zero external CDN) |
| DB | SQLite (WAL mode) via Python `sqlite3` (`data/jobs.db`) |
| Validation | Pydantic v2 |
| Scraping | python-jobspy (LinkedIn/Indeed/Glassdoor/Naukri), requests (Reddit .json, Greenhouse/Lever ATS APIs) |
| Fuzzy dedup | rapidfuzz |
| Scheduler | APScheduler 3.x (in-process background scheduler) |
| Google APIs | google-api-python-client + google-auth-oauthlib |
| Testing | pytest + pytest-asyncio + httpx (TestClient) |

---

## Open-Source Repository References

### Active in V1

| Repo | URL | Usage |
|---|---|---|
| JobSpy | https://github.com/speedyapply/JobSpy | `pip install python-jobspy` → wrapped in `adapters/jobspy_adapter.py` with jittered delays (2-5s) and per-site try/except. |
| YARS (Reddit) | https://github.com/datavorous/yars | Architectural reference for Reddit public `.json` scraping. Adapted User-Agent session and Retry backoff patterns into `adapters/reddit_adapter.py`. |
| job-scraper (ATS) | https://github.com/ghiarishi/job-scraper | Reference for Greenhouse (`boards-api.greenhouse.io`) and Lever (`api.lever.co`) public endpoints. Adapted into `adapters/ats_adapter.py`. |

### Standby for V2 (DO NOT INSTALL YET)

| Repo | URL | V2 Role |
|---|---|---|
| RenderCV | https://github.com/rendercv/rendercv | YAML-to-PDF ATS resume generator (local, no paid API) |
| Resume-Matcher | https://github.com/srbhr/resume-matcher | Local FastAPI + SQLite ATS keyword scorer + Ollama tailoring |

---

## Folder Structure

```
d:\vishnu personal\jobee\
├── PROJECT_CONTEXT.md           ← Living doc, read & updated every session
├── SETUP_GOOGLE.md              ← OAuth2 setup guide for Google Drive & Sheets
├── config.yaml                  ← All user settings (roles, filters, slugs, schedules)
├── .env.example                 ← Sample environment variables
├── requirements.txt             ← Pip dependencies
├── app.py                       ← FastAPI server + APScheduler + Jinja2 UI
├── run.bat                      ← One-click Windows launcher
├── core/
│   ├── models.py                ← Pydantic v2 schemas (JobPosting, ScrapeRun, AppConfig)
│   ├── config_loader.py         ← Load, validate, update, save config.yaml
│   ├── db.py                    ← SQLite WAL manager, CRUD, repost handling
│   ├── filter.py                ← 24h cutoff, keyword exclusion, experience regex, rapidfuzz
│   └── pipeline.py              ← Orchestrates adapters -> filtering -> db -> run logging
├── adapters/
│   ├── base.py                  ← Abstract BaseAdapter
│   ├── jobspy_adapter.py        ← python-jobspy wrapper (LinkedIn, Indeed, Glassdoor, Naukri)
│   ├── reddit_adapter.py        ← Public Reddit .json scraper (hiring post & contact parsing)
│   └── ats_adapter.py           ← Greenhouse & Lever public JSON poller
├── integrations/
│   ├── google_auth.py           ← Local OAuth2 flow with graceful local-only fallback
│   ├── drive_sync.py            ← Base resume upload & shareable link generation
│   └── sheets_sync.py           ← Google Sheets 13-column append & hash status sync
├── resumes_engine/
│   └── base.py                  ← V2 stub: ResumeEngineInterface
├── ui/
│   ├── templates/               ← Jinja2 HTML templates
│   │   ├── base.html            ← Layout with Arial font & minimal black/white header/tabs
│   │   ├── jobs.html            ← Jobs feed, inline status dropdown, stats, modals, search
│   │   ├── resume.html          ← Base resume upload, V2 toggle, Google auth status
│   │   ├── filters.html         ← Target roles, locations, max experience, ATS slugs
│   │   └── logs.html            ← Audit logs from scrape_runs table
│   └── static/
│       └── style.css            ← Pure black & white minimal stylesheet
├── resumes/                     ← User drops base_resume.pdf here
├── data/                        ← jobs.db (SQLite WAL) and exports
└── tests/
    ├── test_pipeline.py         ← Unit tests for 24h rule, dedupe, reposts, experience filters
    ├── test_integrations.py     ← Mock tests for Drive/Sheets sync & local-only fallbacks
    └── test_api.py              ← FastAPI endpoint & HTML template rendering tests
```

---

## SQLite Schema (`data/jobs.db`)

### Table: `jobs`
- `id` (INTEGER PK AUTOINCREMENT)
- `job_hash` (TEXT UNIQUE)
- `normalized_company` (TEXT)
- `normalized_title` (TEXT)
- `title` (TEXT)
- `company` (TEXT)
- `location` (TEXT)
- `source` (TEXT)
- `apply_type` (TEXT)
- `apply_url` (TEXT)
- `contact_info` (TEXT)
- `posted_at_utc` (TEXT ISO-8601 UTC)
- `first_seen_utc` (TEXT ISO-8601 UTC)
- `last_seen_utc` (TEXT ISO-8601 UTC)
- `seen_count` (INTEGER DEFAULT 1)
- `is_repost` (INTEGER DEFAULT 0)
- `jd_text` (TEXT)
- `status` (TEXT DEFAULT 'To Apply')
- `resume_mode_used` (TEXT DEFAULT 'single_base')
- `resume_drive_url` (TEXT)
- `synced_to_sheet` (INTEGER DEFAULT 0)

### Table: `scrape_runs`
- `id` (INTEGER PK AUTOINCREMENT)
- `started_at` (TEXT)
- `finished_at` (TEXT)
- `source` (TEXT)
- `status` (TEXT: success / partial / failed)
- `raw_count` (INTEGER)
- `dropped_old_count` (INTEGER)
- `filtered_out_count` (INTEGER)
- `duplicate_count` (INTEGER)
- `new_count` (INTEGER)
- `error_message` (TEXT)

### Table: `settings`
- `key` (TEXT PK)
- `value` (TEXT JSON-serialised)

---

## Google Sheets Column Spec (13 Columns)

Row 1: Frozen bold headers:
`Date Found | Hours Old | Source | Company | Role Title | Location | Apply Type | Apply Link | Contact Info | Status | Resume Mode | Resume Link | Job Hash`

Status column (J): Data validation dropdown: `To Apply, Applied, Interview, Rejected, Skipped`.
Status update: Finds row by `Job Hash` (column M) and updates cell `J<row>` without modifying other cells.

---

## Milestone Checklist

### ✅ Step 0: Context Initialization
- [x] `PROJECT_CONTEXT.md` created with full architecture, schema, rules, and open-source references.

### ✅ Milestone 1: Core Architecture, SQLite DB & Gatekeeper Engine
- [x] Complete directory structure scaffolded under `d:\vishnu personal\jobee\`.
- [x] `config.yaml` created with target roles, locations, exclude keywords, subreddits, and ATS slugs.
- [x] `core/models.py` (Pydantic v2 `JobPosting`, `ScrapeRun`, `AppConfig`).
- [x] `core/config_loader.py` for config validation and serialization.
- [x] `core/db.py` SQLite WAL mode manager, 3 tables, CRUD, repost handling.
- [x] `core/filter.py` 24h cutoff, title keywords, experience regex, rapidfuzz fuzzy deduplication.
- [x] `resumes_engine/base.py` V2 stub (`ResumeEngineInterface`).
- [x] `tests/test_pipeline.py`: 32 tests passing covering 24h cutoff, dedup, reposts, and experience filters.

### ✅ Milestone 2: Live Scraper Adapters (`JobSpy`, `Reddit`, `ATS`)
- [x] Vendor repos cloned into `_vendor/`, studied, and deleted.
- [x] `adapters/base.py` abstract `BaseAdapter`.
- [x] `adapters/jobspy_adapter.py` wrapping python-jobspy with jittered delays and error isolation.
- [x] `adapters/reddit_adapter.py` public `.json` scraping, retry logic, hiring regex, and contact extraction.
- [x] `adapters/ats_adapter.py` polling Greenhouse and Lever public JSON board endpoints.
- [x] `core/pipeline.py` orchestrating adapters with per-source logging to `scrape_runs`.

### ✅ Milestone 3: Google Drive & Google Sheets Sync
- [x] `integrations/google_auth.py` OAuth2 InstalledAppFlow with local-only fallback.
- [x] `integrations/drive_sync.py` base resume upload to Google Drive folder with link caching.
- [x] `integrations/sheets_sync.py` 13-column append, frozen headers, status validation, and hash-based updates.
- [x] `SETUP_GOOGLE.md` step-by-step setup documentation.
- [x] `tests/test_integrations.py`: 5 tests passing covering fallback behavior, row building, and hash status updates.

### ✅ Milestone 4: Local Web Dashboard, Scheduler & Launch Script
- [x] `ui/static/style.css` minimalist black & white (#FFFFFF / #000000) styling using Arial font only.
- [x] Jinja2 templates for all 4 tabs (`base.html`, `jobs.html`, `resume.html`, `filters.html`, `logs.html`).
- [x] `app.py` FastAPI server with APScheduler background worker, CSV export, and REST endpoints.
- [x] `run.bat` Windows launcher script.
- [x] `tests/test_api.py` testing routes, status update APIs, and CSV streaming.

---

## Living Progress & Architecture Decisions Log

### 2026-11-22 — Parallel Fast Scanning, Dedicated Reddit Tab & UI Rework
- **Fast Scanning Engine**: Upgraded `JobSpyAdapter` and `pipeline.py` with multi-threading (`ThreadPoolExecutor`), dropping scan times from 5+ minutes to <20 seconds. Automatically skips reCAPTCHA-blocked endpoints (Naukri 406) and Glassdoor remote errors.
- **Dedicated Reddit Jobs Tab**:
  - Implemented `adapters/reddit_search.py`: unauthenticated search combining keyword + location (defaults to `Hyderabad`) without requiring Reddit API keys.
  - Added `/reddit` template tab: real-time search, preview cards, direct links, and 1-click **"Import to Tracker"** into `jobs.db`.
  - Added REST APIs: `POST /api/reddit/search` and `POST /api/reddit/import`.
- **Unified Flexible Search**:
  - `core/db.py` enhanced to support choosing Role only, Location only (default: Hyderabad), both, or freeform keywords.
- **Polished B&W UI**:
  - High-contrast pure black & white design (#FFFFFF / #000000) using Arial font exclusively.
  - Modern pill tags, crisp borders, unified top search drawer, and real-time status feedback.
- **Verification**:
  - Added test suites: `tests/test_reddit_search.py`, `tests/test_db_search.py`, `tests/test_fast_scan.py`.
  - Total test suite expanded to **59 / 59 passing tests**.
  - Background server restarted with live access on `http://localhost:8000`.
