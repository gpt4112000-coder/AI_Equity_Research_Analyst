# AI Equity Research Analyst - Progress Log

**Last Updated:** 2026-07-28 23:40 IST
**Current Version:** v4.0.0 (FastAPI)
**GitHub:** https://github.com/gpt4112000-coder/AI_Equity_Research_Analyst
**Latest Commit:** a428e32

---

## Project Overview

An announcement-centric AI equity research platform for Indian small-cap stocks (BSE + NSE). Fetches company universe, imports announcements, extracts insights (rule-based + Ollama), generates per-announcement AI summaries on-demand, and synthesizes company-level research reports.

---

## Architecture

```
Frontend (index.html, plain JS) → FastAPI (app.py) → SQLite DB
                                                      ↑
                                              Ollama (qwen2.5:3b, ~6s/request on CPU)
```

- Single-port serving: FastAPI serves both API and frontend from port 8001
- SSH access: `ssh -L 8001:localhost:8001 ubuntu@223.196.192.172` → `http://localhost:8001`
- No external frontend dependencies (no Tailwind, no CDN)

---

## Database Stats (as of 2026-07-28)

| Metric | Value |
|--------|-------|
| Companies | 3,387 |
| Sectors | 19 |
| Announcements | 179,626 |
| Rule-based Insights | 179,626 |
| Announcements with AI Summary | ~143+ |
| DB Size | 514 MB |

### By Exchange
- BSE: 135,387
- NSE: 44,239

### By Insight Type
- General: 104,701
- Financial: 26,444
- Management: 23,827
- Acquisition: 8,733
- Guidance: 7,362
- Capex: 2,632
- Orders: 2,511
- Dividend: 2,347
- Credit Rating: 1,069

---

## Completed Steps

### Step 1: Fetch Company Universe
- **Script:** `scripts/fetch_company_universe.py`
- **Source:** BSE `listSecurities()` all groups + NSE from announcement cache
- **Filter:** Market cap < ₹2,000 Cr (micro + small cap)
- **Result:** 3,387 companies (removed 1,113 with 0 MCap)
- **Deduplication:** By ISIN across BSE and NSE

### Step 2: Import Announcements
- **Script:** `scripts/import_announcements.py`
- **Source:** 363 date folders at `/home/ubuntu/FinEng/BseIndiaApi/src/examples/Bse_Nse_announcement_downloads/`
- **Result:** 179,626 announcements (135K BSE + 44K NSE) across 4,458 companies
- **Fix:** NSE date parsing (`%Y-%m-%d` format, backfilled 44,239 empty dates)

### Step 3: Rule-based Insight Extraction
- **Script:** `scripts/extract_insights_fast.py`
- **Result:** 179,626 insights across 9 types
- **Types:** general, financial, management, acquisition, guidance, capex, order, dividend, credit_rating

### Step 4: Company Summary Aggregation
- **Script:** `scripts/build_company_summaries.py`
- **Result:** Summary built for all 3,387 companies

### Step 5: Web Interface
- **Backend:** `backend/app.py` (FastAPI, 495 lines)
- **Frontend:** `frontend/index.html` (plain JS, 489 lines)
- **Features:**
  - Dashboard with stats
  - Company list with sector filters + search + pagination
  - Company detail page with sentiment/themes/quick facts
  - Per-announcement AI summaries (on-demand)
  - Company-level AI Research Summary (stored in DB)

### Step 6: AI Integration (Ollama)
- **Model:** qwen2.5:3b (1.9GB)
- **Speed:** ~6s per request on CPU
- **Endpoints:**
  - `POST /api/announcements/{id}/ai_summary` - per-announcement structured summary
  - `POST /api/companies/{id}/generate_summaries` - batch process up to 20 announcements
  - `GET /api/companies/{id}/ai_summary` - company-level synthesized report

---

## Key Fixes Applied

1. **Route decorators** - Fixed mismatched route for announcement summary (was GET on wrong path)
2. **Empty ai_summary** - Fixed empty string passing truthiness check causing JSON parse error
3. **Missing uvicorn.run()** - Added entry point for direct execution
4. **Frontend api() function** - Added POST method support (was hardcoded to GET)
5. **NSE date parsing** - Fixed `%Y-%m-%d` format (was using wrong parser)
6. **Sector filter** - Fixed not passing param to API
7. **Frontend rewrite** - Removed Tailwind CDN dependency, plain JS + inline CSS

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/stats` | Dashboard statistics |
| GET | `/api/sectors` | List of sectors with counts |
| GET | `/api/companies` | Company list with filters |
| GET | `/api/companies/{id}` | Company detail + stored AI summary |
| GET | `/api/companies/{id}/ai_summary` | Generate & store company-level AI summary |
| POST | `/api/companies/{id}/generate_summaries` | Batch generate per-announcement summaries |
| POST | `/api/announcements/{id}/ai_summary` | Generate per-announcement AI summary |
| GET | `/` | Frontend |

---

## DB Schema

### Tables
- `companies` - Company master (3,387 rows)
- `announcements` - All announcements (179,626 rows, with `ai_summary` JSON column)
- `announcement_insights` - Rule-based insights (179,626 rows)
- `company_summary` - Aggregated summary per company
- `company_ai_summary` - Generated company-level AI summary (stored with timestamp)
- `price_history` - Historical prices
- `technical_indicators` - Technical analysis data

---

## Environment

- **Conda Env:** `BSE_NSE_Announcement`
- **Python:** 3.10
- **Key Packages:** fastapi, uvicorn, httpx, bse, nse, nsedt, yfinance
- **Ollama:** Running with qwen2.5:3b + nomic-embed-text
- **Backend PID:** Check with `ps aux | grep app.py`
- **Port:** 8001 (avoid 8000, used by another app)
- **tmux:** Backend runs in tmux session `backend` (or via nohup)

---

## How to Restart Backend

```bash
# Kill existing
pkill -f "python app.py"

# Start new
nohup bash -c 'source /home/ubuntu/anaconda3/etc/profile.d/conda.sh && conda activate BSE_NSE_Announcement && cd /home/ubuntu/FinEng/AI_Equity_Research_Analyst_Aug_2026/backend && python app.py' > /tmp/backend.log 2>&1 &

# Verify
sleep 8 && curl -s http://localhost:8001/api/stats
```

---

## How to Access

```bash
# From local machine
ssh -L 8001:localhost:8001 ubuntu@223.196.192.172

# Then open in browser
http://localhost:8001
```

---

## Git History (Recent)

```
a428e32 feat: store company AI summary in DB, show immediately on load with colored sections
271cb1d fix: api() function now supports POST method for AI summary endpoints
265fab8 fix: correct route decorators, add per-announcement AI analysis UI
f8ca013 Batch AI summary generation for all announcements via Ollama
0fae2b4 Add AI Research Summary per company using Ollama qwen2.5:3b
```

---

## Rollback Instructions

To restore this exact version:
```bash
cd /home/ubuntu/FinEng/AI_Equity_Research_Analyst_Aug_2026
git checkout a428e32
# or
git reset --hard a428e32
```

---

## Next Steps (Potential)

1. Category filtering from `ai_summary.categories` JSON field
2. Bulk pre-generate summaries for all companies (batch processor)
3. Real-time announcement monitoring
4. Price correlation with announcement sentiment
5. Export reports as PDF
6. Multi-user authentication
