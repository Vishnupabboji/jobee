# Jobee 🐝

A 100% local-first, privacy-focused Job Discovery, Filtering, and Application Tracking platform built with FastAPI, SQLite WAL, and a clean minimalist monochrome interface.

## Highlights

- **Multi-Source Scraping (<20s):** Parallel multi-threaded discovery across LinkedIn, Indeed, Glassdoor, Greenhouse, and Lever boards.
- **Dedicated Reddit Jobs Search:** Search hiring threads and job discussions across Indian tech subreddits (default: Hyderabad) with 1-click import into your SQLite tracker.
- **On-Demand Live JD Web Scraper:** Click "JD" on any posting to read or dynamically fetch & clean live full-text descriptions from the web.
- **Flexible Multi-Clause Search:** Filter postings by Role alone, Location alone (default: Hyderabad), both together, or freeform keywords.
- **Strictly Local-First:** Runs 100% locally with zero paid API keys required. Optional Google Drive & Google Sheets sync with seamless local fallback.
- **Minimalist Aesthetic:** High-contrast pure black & white design (#FFFFFF / #000000), Arial typography, and zero visual clutter.

## Project Structure

```
jobee/
├── adapters/            # JobSpy, ATS (Greenhouse/Lever), and Reddit adapters
├── core/                # DB manager (SQLite WAL), deduplication, filters, pipeline, JD scraper
├── integrations/        # Google Drive & Sheets sync with local fallback
├── resumes_engine/      # Resume interface & V2 tailoring stub
├── ui/                  # Jinja2 templates & monochrome CSS
├── tests/               # 59 comprehensive unit and integration tests
├── config.yaml          # Target roles, locations, subreddits, ATS slugs
├── app.py               # FastAPI web server and scheduler
├── run.bat              # 1-click Windows launcher
└── requirements.txt     # Python dependencies
```

## Quick Start

1. **Install Dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Launch Application:**
   ```bash
   python app.py
   # or run on Windows:
   run.bat
   ```

3. **Open in Browser:**
   Visit `http://localhost:8000`

## Running Tests

```bash
pytest tests/
```
