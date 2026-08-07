# AI Equity Research Analyst - Progress Log

**Last Updated:** 2026-08-07 18:20 IST
**Current Version:** v4.5.0 (Per-Company Concall Transcript Summaries)
**GitHub:** https://github.com/gpt4112000-coder/AI_Equity_Research_Analyst
**Latest Commit:** d0b53fb (uncommitted concall work on top)

---

## Project Overview

An announcement-centric AI equity research platform for Indian small-cap stocks (BSE + NSE). Fetches company universe, imports announcements, extracts insights (rule-based + Ollama), generates per-announcement AI summaries on-demand, and synthesizes company-level research reports.

---

## Architecture

```
Frontend (index.html, fetch.html, plain JS) → FastAPI (app.py) → SQLite DB
                                                                    ↑
                                                            Ollama (qwen2.5:3b, ~6s/request on CPU)
```

- Single-port serving: FastAPI serves both API and frontend from port 8001
- SSH access: `ssh -L 8001:localhost:8001 ubuntu@223.196.192.172` → `http://localhost:8001`
- No external frontend dependencies (no Tailwind, no CDN)
- tmux session `backend` for persistent server

---

## Database Stats (as of 2026-08-02)

| Metric | Value |
|--------|-------|
| Companies | 3,394 |
| Sectors | 19 |
| Announcements | 239,356 |
| Rule-based Insights | 170,573 |
| Announcements with AI Summary | ~143+ |
| DB Size | 514+ MB |

### By Exchange
- BSE: 152,562
- NSE: 86,794

### By Insight Type
- General: 105,517
- Financial: 26,538
- Management: 23,885
- Acquisition: 8,794
- Guidance: 7,657
- Capex: 2,660
- Orders: 2,551
- Dividend: 2,370
- Credit Rating: 1,093

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
- **Backend:** `backend/app.py` (FastAPI, ~676 lines)
- **Frontend:** `frontend/index.html` (plain JS, ~640 lines)
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
  - `GET /api/companies/{id}/ai_summary` - company-level synthesized report
  - `POST /api/ai/batch/start|stop`, `GET /api/ai/batch/status` - background announcement analysis
  - `POST /api/documents/batch/start|stop`, `GET /api/documents/batch/status` - background document analysis
  - `GET /api/companies/{id}/insights?source=all|announcement|document&metric=` - unified insights timeline

### Step 7: Fetch Stock Feature (NEW - 2026-07-29)
- **Script:** `scripts/fetch_stock_announcements.py`
- **Frontend:** `frontend/fetch.html`
- **Features:**
  - Search stocks by company name or symbol
  - Fetch announcements from NSE (fallback to BSE)
  - Timeline selection: 1y, 3y, 5y, 10y
  - Incremental analysis (skips already-analyzed)
  - History of previously fetched stocks
- **API Endpoints:**
  - `GET /api/fetch?symbol=X&years=N` - Fetch from exchange
  - `GET /api/fetched-stocks` - List fetched stocks
  - `GET /api/search-stock?q=X` - Search companies
  - `GET /api/stock-info/{symbol}` - Live market data
  - `GET /api/sentiment-timeline/{company_id}` - Monthly sentiment

### Step 8: Advanced Filters (NEW - 2026-07-29)
- **Backend:** Extended `/api/companies` with 5 new parameters
- **Frontend:** Added filter panel with 6 filter types
- **Features:**
  1. **Insight Type Filter** - guidance, order, capex, financial, dividend, credit_rating, management, acquisition
  2. **Sort By** - market_cap, announcements, company, latest, sentiment, guidance_count, order_count, capex_count
  3. **Sort Order** - Ascending/Descending toggle
  4. **Date Range** - Filter by announcement date (from/to)
  5. **Sentiment Filter** - Positive/Negative/Neutral dominant sentiment
  6. **Amount Range** - Filter by monetary amount in insights (Cr)
- **API Parameters:**
  - `insight_type=guidance`
  - `sort_by=sentiment&sort_order=desc`
  - `from_date=2026-01-01&to_date=2026-07-29`
  - `sentiment=positive`
  - `min_amount=100&max_amount=10000`
- **Test Results:**
  - `insight_type=guidance` → 675 companies
  - `sort_by=guidance_count&sort_order=desc` → Sorted correctly
  - `from_date=2026-01-01&to_date=2026-07-29` → 3369 companies
  - `sentiment=positive` → 21 companies
  - `min_amount=1000` → 29 companies
  - Combined filters working correctly

### Step 9: Corporate Event Enrichment (NEW - 2026-08-07)
- **Repos evaluated:** `nse-bse-mcp` (TypeScript MCP passthrough to npm `nse-bse-api`, JS port of our Python libs, no new data source) — skipped, reused `BseIndiaApi`/`NseIndiaApi` directly.
- **Script:** `scripts/backfill_corporate.py` (A2) — runs in tmux `enrich`, logs to `logs/enrich_corporate.log`
- **Data added per company:**
  - Corporate actions (dividend/bonus/split/buyback/delisting) → mirrored into `announcements` with `subcategory` (purpose) + `event_key`, `announcement_date` = ex-date, so the existing AI pipeline analyzes each event **exactly once** (dedup gate: same `event_key`, or an existing announcement same-date/same-family).
  - Board meetings (NSE) → `board_meetings` table
  - Result calendar (BSE) → `result_calendar` table
  - Annual reports (NSE) → `company_files` (kind=`annual_report`), links recorded; PDF downloads optional (`--no-download` off)
- **Tracking:** `enrich_status` table (per company+exchange), resumable with `--resume`.
- **Schema (A1):** `announcements.subcategory`/`event_key` columns; new `board_meetings`, `result_calendar`, `enrich_status` tables.
- **Parse upgrade (A3):** `parse_bse`/`parse_nse` set `category`/`subcategory` from `CATEGORYNAME`/`SUBCATNAME`.
- **Documents classifier (A4):** `_classify_announcement` in `backend/app.py` now prefers structured `category`/`subcategory`/`event_key` (regex fallback only for legacy rows) for annual-report / credit-rating / concall buckets.
- **Corporate Events endpoint + UI (A5):**
  - `GET /api/companies/{id}/corporate-events` — corporate actions (event_key mirrors), board meetings, result calendar, enrichment status.
  - Frontend "Corporate Events" card in `renderCompany` (badges for each exchange + event rows).
- **MCP server (A6):** `backend/mcp_nse_bse_server.py` (official `mcp` SDK 1.14, FastMCP) — 10 tools: `search_companies`, `db_corporate_events`, `db_insights`, `nse_announcements`, `bse_announcements`, `nse_corporate_actions`, `bse_corporate_actions`, `nse_board_meetings`, `nse_annual_reports`, `bse_result_calendar`. Registered in `opencode.json` as local stdio server (conda python). Verified: handshake + tools/list + live tool call.
- **Usage:** `python scripts/backfill_corporate.py [--company N] [--exchanges both|bse|nse] [--resume] [--no-download] [--limit N]`

### Step 10: Frontend Event Visibility + Refresh Persistence (NEW - 2026-08-07)
- **Backend additions:**
  - `/api/companies`: added `ca_count` + `latest_ca_date` per company (LEFT JOIN on `event_key` announcements), `corporate_actions=1` filter, `ca_latest` sort.
  - `/api/companies/{id}/documents`: added `corporate_actions` (full list, not just top-200) + `type_counts` (category-family breakdown).
  - `/api/companies/{id}/corporate-events`: added `type_counts`.
- **Frontend additions (`frontend/index.html`):**
  - **Refresh persistence:** `syncUrl()` writes current view/filters to the URL via `history.pushState` (guard prevents duplicate entries on `popstate`); `restoreState()` reads **all** params on load (was only `?company=`); `popstate` listener re-renders on Back/Forward. Refreshing now stays on the current page.
  - **Documents card:** new "Corporate Actions" tab with family color badges (Dividend/Bonus/Split/Buyback/Delisting); "Announcement Types" chip bar (full-DB counts) with click-to-filter of the Announcements tab.
  - **Companies list:** "N CA" badge on rows, "Corporate Actions: Has Actions" filter chip, "Latest Corp. Action" sort option.
- **Verified:** backend compile + endpoint checks, `node --check`, headless Chrome render of company page (enrichment badges, CA rows, board meetings, type chips) and filtered companies list.
- **AI Insights Timeline redundancy removal (2026-08-07):**
  - Consolidated all batch-analysis controls (Generate Summary / Analyze Announcements / Analyze All Companies / Analyze Documents + per-job Stop + progress) into the **AI Research Summary** card only; the **AI Insights Timeline** card is now a pure display (source toggle + metric chips + insight rows).
  - `/api/companies/{id}/insights`: dedup by `(date, metric, headline)` preferring announcement source (announcement rows deduped first, before sort). Verified company 516 `source=all`: 33 → 30 rows, zero duplicates; the two `2026-08-02` financials rows are distinct filings (results + board-meeting outcome) and are correctly kept; `date=None` document pile reduced 22 → 19.

---

## Key Fixes Applied

1. **Route decorators** - Fixed mismatched route for announcement summary
2. **Empty ai_summary** - Fixed empty string passing truthiness check
3. **Missing uvicorn.run()** - Added entry point for direct execution
4. **Frontend api() function** - Added POST method support
5. **NSE date parsing** - Fixed `%Y-%m-%d` format
6. **Sector filter** - Fixed not passing param to API
7. **Frontend rewrite** - Removed Tailwind CDN dependency, plain JS + inline CSS
8. **market_cap None crash** - Fixed `(dict(company)['market_cap'] or 0)` in company summary
9. **BSE path import** - Fixed `sys.path.insert(0, str(Path("/home/ubuntu/FinEng")))` for BseIndiaApi
10. **Count query** - Fixed regex-based extraction for robust count queries with insight sorts

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/stats` | Dashboard statistics |
| GET | `/api/sectors` | List of sectors with counts |
| GET | `/api/companies` | Company list with filters (now supports 14 parameters) |
| GET | `/api/companies/{id}` | Company detail + stored AI summary + analysis status |
| GET | `/api/companies/{id}/ai_summary` | Generate & store company-level AI summary |
| POST | `/api/announcements/{id}/ai_summary` | Generate per-announcement AI summary |
| POST | `/api/ai/batch/start?company_id=` / `/stop` | Background announcement AI analysis |
| GET | `/api/ai/batch/status` | Announcement batch progress |
| POST | `/api/documents/batch/start?company_id=` / `/stop` | Background document AI analysis |
| GET | `/api/documents/batch/status` | Document batch progress |
| GET | `/api/companies/{id}/insights` | Unified insights timeline (`source`, `metric` params) |
| GET | `/api/companies/{id}/documents/status` | Downloaded/analyzed document counts |
| GET | `/api/fetch?symbol=X&years=N` | Fetch announcements from exchange |
| GET | `/api/fetched-stocks` | List previously fetched stocks |
| GET | `/api/search-stock?q=X` | Search companies by name/symbol |
| GET | `/api/stock-info/{symbol}` | Live market data (yfinance) |
| GET | `/api/sentiment-timeline/{company_id}` | Monthly sentiment bar chart |
| GET | `/` | Frontend (index.html) |
| GET | `/fetch` | Fetch stock page (fetch.html) |

---

## DB Schema

### Tables
- `companies` - Company master (3,394 rows)
- `announcements` - All announcements (181,065 rows, with `ai_summary` JSON column)
- `announcement_insights` - Rule-based insights (181,065 rows)
- `company_summary` - Aggregated summary per company (with `has_*` flags, sentiment counts, `key_themes`)
- `company_ai_summary` - Generated company-level AI summary (stored with timestamp)
- `fetched_stock` - Tracks stocks fetched via new feature (NEW)
- `price_history` - Historical prices
- `technical_indicators` - Technical analysis data

---

## Environment

- **Conda Env:** `BSE_NSE_Announcement`
- **Python:** 3.10
- **Key Packages:** fastapi, uvicorn, httpx, bse, nse, nsedt, yfinance
- **Ollama:** Running with qwen2.5:3b + nomic-embed-text
- **Port:** 8001 (avoid 8000, used by another app)
- **tmux:** Backend runs in tmux session `backend`

---

## How to Restart Backend

```bash
# Kill existing tmux session
tmux kill-session -t backend 2>/dev/null

# Start new session
cd /home/ubuntu/FinEng/AI_Equity_Research_Analyst_Aug_2026/backend
source /home/ubuntu/anaconda3/etc/profile.d/conda.sh
conda activate BSE_NSE_Announcement
tmux new-session -d -s backend "python app.py"

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

## Frontend Pages

### 1. Dashboard (`/`)
- Stats grid (companies, announcements, insights)
- By-exchange bar chart
- By-insight-type bar chart (clickable to filter)
- "Browse Companies" button

### 2. Company List (`/companies`)
- Search bar
- Sector filter chips
- **NEW:** Insight type filter chips (9 types)
- **NEW:** Sort dropdown + ASC/DESC toggle
- **NEW:** Date range inputs
- **NEW:** Sentiment filter chips
- **NEW:** Amount range inputs
- **NEW:** Clear Filters button
- Paginated table with company data

### 3. Company Detail (`/?company=ID`)
- Company header (name, symbols, market cap)
- Live stock info card (yfinance data)
- Key insight cards (Guidance, Orders, Capex)
- Sentiment overview + key themes
- Sentiment timeline chart (monthly)
- AI Research Summary section
- Recent Announcements list

### 4. Fetch Stock (`/fetch`)
- Search input with company name/symbol
- Timeline selector (1y, 3y, 5y, 10y)
- Fetch button
- Status display
- History grid of previously fetched stocks

---

## Git History (Recent)

```
06e5c30 - feat: add fetch stock feature and advanced filters
a428e32 - feat: store company AI summary in DB, show immediately on load with colored sections
271cb1d - fix: api() function now supports POST method for AI summary endpoints
265fab8 - fix: correct route decorators, add per-announcement AI analysis UI
f8ca013 - Batch AI summary generation for all announcements via Ollama
0fae2b4 - Add AI Research Summary per company using Ollama qwen2.5:3b
```

---

## Next Steps (Potential)

Based on research of popular equity research platforms (Finapolis, Seeking Alpha, Kabra, Stoxly, Screener.in, StockEdge, Investorstack):

### High Priority
1. **Stock Screener with Histograms** - Add distribution histograms to filter controls
2. **Peer Comparison Table** - Side-by-side comparison of selected companies
3. **Stage/Classification System** - Classify companies into stages (Emerging, Stage 1, Stage 2, Breakout)
4. **Industry/Sector Directory** - Browse companies by industry, compare players
5. **Growth Guidance Tracker** - Track company guidance history

### Medium Priority
6. **Forward Estimates** - Analyst consensus on future revenue/EPS
7. **Insider Activity** - Track insider buying/selling (if available for Indian markets)
8. **Sentiment Timeline Enhancement** - Score announcements for sentiment
9. **Valuation Model** - Simple DCF model, P/E, P/B, EV/EBITDA comparisons

### Lower Priority
10. **Earnings Calendar** - Upcoming earnings dates
11. **Dividend Analysis** - Yield history, payout ratio, growth
12. **Technical Indicators Dashboard** - RSI, MACD, Bollinger Bands visualization
13. **Export Reports as PDF**
14. **Multi-user Authentication**

---

## Rollback Instructions

To restore this exact version:
```bash
cd /home/ubuntu/FinEng/AI_Equity_Research_Analyst_Aug_2026
git log --oneline -10  # Find commit hash
git checkout <commit-hash>
# or
git reset --hard <commit-hash>
```

---

## Session Summary (2026-07-29)

### Work Completed
1. **Fetch Stock Feature**
   - Created `scripts/fetch_stock_announcements.py` - fetches from NSE/BSE
   - Created `frontend/fetch.html` - search + timeline + history
   - Added 5 new API endpoints
   - Added `fetched_stock` table to DB

2. **Advanced Filters**
   - Extended `/api/companies` with 5 new parameters
   - Added filter UI with 6 filter types
   - Fixed broken `filterByInsight` function
   - Added CSS styles for new filter controls
   - Optimized sort queries for performance

3. **Bug Fixes**
   - Fixed BSE path import for BseIndiaApi
   - Fixed count query for insight sorts (regex-based)
   - Fixed `market_cap` None crash

### Files Modified
- `backend/app.py` - Extended with new endpoints and filters
- `backend/data/storage/db.py` - Added `fetched_stock` table
- `frontend/index.html` - Added filter UI, live stock info, sentiment timeline
- `frontend/fetch.html` - NEW - Fetch stock page
- `scripts/fetch_stock_announcements.py` - NEW - Fetch logic

### Test Results
- All 6 filter types working correctly
- Combined filters working
- Fetch feature working (tested with ETERNAL, WIPRO)
- Live stock info working
- Sentiment timeline working

### Ready for Next Session
- All changes committed and pushed to GitHub
- Backend running in tmux session `backend`
- Ready to implement next features (peer comparison, industry directory, etc.)

---

## Session Summary (2026-08-02)

### Work Completed
1. **Right-click / New-tab links**
   - Company list name cells now render as real `<a href="/?company=ID">` links (Ctrl/Cmd+click or middle-click opens new tab; plain click still navigates in-app)
   - `fetch.html` history cards converted to real anchor links

2. **Peer Comparison — Screener-style**
   - New endpoint `GET /api/compare/sector-peers?company_id=X&limit=5` — auto-selects same-sector companies (NSE symbols prioritized, sorted by market cap)
   - Peer table transposed: **stocks as rows, metrics as columns**
   - Auto-populates base company + up to 5 sector peers on first open; saved selections respected
   - Existing `/api/compare/metrics` + `/api/compare/peers` untouched

3. **Documents Section (Documents tab in company page)**
   - Replaced the "Recent Announcements" card with a **Documents** card with 4 tabs: Announcements / Annual reports / Credit ratings / Concalls
   - Announcements tab has filters: Recent / Important / Search / All
   - Annual reports from NSE API (cached) merged with classified announcement attachments
   - Credit ratings classified from announcements DB (CRISIL, CARE, ICRA, BRICKWORK, INDIA RATINGS, FITCH)
   - Concalls grouped by quarter with transcript/PPT/recording classified from attachment URLs
   - New endpoint `GET /api/companies/{company_id}/documents` with 600s cache

4. **Bug Fix — "loadDocuments is not defined"**
   - Docs JS had been accidentally inserted inside the `navigate()` function scope; relocated to script end so all docs functions are global

5. **Data Freshness Fix (critical)**
   - Root cause: all import scripts referenced an `is_critical` column that **did not exist** in the `announcements` table → every `INSERT` raised `OperationalError` silently swallowed by `except: continue` → imports reported "0 new" since 2026-07-29
   - Fixes in `backend/data/storage/db.py`:
     - Added `is_critical INTEGER DEFAULT 0` to schema + auto-migration (`ALTER TABLE` if column missing)
     - Deduplicated 10,492 duplicate announcement rows (and their orphan insights)
     - Added UNIQUE index on `(company_id, announcement_date, headline)` so `INSERT OR IGNORE` stays dedup-safe
   - Re-ran full import: **+34,324 announcements** → 239,356 total, DB now current through **2026-08-02**
   - New `scripts/import_recent_announcements.py` — lightweight incremental importer (last 3 days, runs in <0.1s)
   - `run_announcements.sh` cron now calls the incremental importer after downloads, so new data flows into the DB every minute automatically

### Files Modified
- `backend/app.py` — sector-peers + documents endpoints (~1,100 lines)
- `backend/data/storage/db.py` — schema migration (is_critical + unique index + dedup)
- `frontend/index.html` — new-tab links, peer comparison UI, Documents section (~1,300 lines)
- `frontend/fetch.html` — history cards as real links
- `scripts/import_recent_announcements.py` — NEW incremental importer
- `BseIndiaApi/src/examples/run_announcements.sh` — cron now imports into DB

### Test Results
- Documents endpoint: RELIANCE (162 anns / 18 ARs / 7 ratings / 5 concalls), KPITTECH (97 anns / 7 ARs / 5 concalls), non-NSE companies return empty gracefully
- Recent announcements visible: Birla Cable (516) shows 2026-08-02 financial results
- Full import dedup-safe: 239,356 total == 239,356 distinct
- `/api/stats` reports 239,356 announcements; backend serving on port 8001

### Ready for Next Session
- Changes uncommitted — commit + push to GitHub
- AI summaries for the ~238k new announcements not yet generated (use the new "Analyze All Companies" batch button; takes many hours at ~6s/announcement)

---

## Session Summary (2026-08-02, part 2) — AI Batch Analysis + AI Insights Timeline

### Problem
- Running Ollama on the whole DB took hours and could only be triggered from the terminal (`scripts/batch_ai_summaries.py`), with no progress feedback and no guarantee of skipping already-read documents.

### Work Completed
1. **Structured AI insights storage (metric-wise, datewise)**
   - New `ai_insights` table: one row per announcement × category (capex, guidance, orders, dividend, financials, regulatory, etc.) with `announcement_date`, `headline`, `summary`, parsed `amount`/`amount_text`, `sentiment`, `importance`
   - Indexes: `(company_id, metric, announcement_date)`, `(company_id, announcement_date)`, unique `(company_id, announcement_id, metric)`
   - Partial index `idx_ann_pending_ai` on announcements for fast "pending" lookups
   - Refactored `generate_announcement_summary` → `_generate_announcement_summary_core` which also writes `ai_insights` rows (amount parsed from AI `key_numbers` into crore scale)

2. **Background batch job system (webpage-triggerable)**
   - `POST /api/ai/batch/start?company_id=X` — starts a daemon thread; scope = one company or all companies
   - `GET /api/ai/batch/status` — progress polling (total, processed, generated, skipped, failed, current, message)
   - `POST /api/ai/batch/stop` — clean stop between announcements
   - **Incremental by design**: worker snapshots only `ai_summary IS NULL OR ai_summary = ''` rows; already-read docs are never re-processed (verified: re-running a completed company batch shows 0 pending)
   - Snapshot-up-front pagination avoids skipping rows as the result set shrinks
   - Fixed a `threading.Lock` reentrancy deadlock that froze the API during stop

3. **AI Insights timeline endpoint + frontend**
   - `GET /api/companies/{id}/ai-insights?metric=capex` — metric-filtered, datewise timeline
   - New "AI Batch Analysis" card on company page: **Analyze This Company** / **Analyze All Companies** / **Stop** with live progress bar (polls every 2s)
   - New "AI Insights Timeline" card: metric filter chips + datewise grouped insights with importance/sentiment/amount badges

### Files Modified
- `backend/data/storage/db.py` — `ai_insights` table + indexes + pending-AI partial index
- `backend/app.py` — core refactor, batch job endpoints, ai-insights endpoint (~1,310 lines)
- `frontend/index.html` — AI Batch + AI Insights cards and JS (~1,460 lines)

### Test Results
- Single-announcement generation stores structured `ai_insights` row (verified: Birla Cable financials, 2026-08-02)
- Company batch (5 pending): 5 new, 0 failed; insights grouped by date + metric
- Global batch: 238,521 pending detected; progress updates correctly; stop works cleanly
- Re-run on completed company → 0 pending (incremental verified)
- Documents endpoint + all existing endpoints still work; frontend JS passes `node --check`

### Ready for Next Session
- Changes uncommitted — commit + push to GitHub
- Optionally trigger the full "Analyze All Companies" batch from the webpage (many hours)

---

## Session Summary (2026-08-07) — Per-Company Concall Transcript Summaries

### Problem
- Earnings-call transcripts (~5,800 PDFs across 513 companies) were downloaded as plain documents but never summarized; no web-triggered way to AI-analyze a company's concalls and view the results.

### Work Completed
1. **Shared concall pipeline** (`backend/concall_pipeline.py`)
   - `concalls` table (`company_id`, `announcement_id` UNIQUE, `call_date`, `quarter`, `title`, `transcript_path`, summary fields, `status` pending→downloaded→done|error, `analyzed_at`)
   - `TRANSCRIPT_LIKE` SQL predicate (concall/transcript/earnings-call categories) + `recent_cutoff(years=2)` scope
   - `ensure_queued` (INSERT OR IGNORE), `download_pending` (BSE AttachLive/AttachHis alt URL fallback), `analyze_downloaded` — shared by CLI scripts and the backend batch job
   - AI prompts: `SINGLE_PROMPT` / `DIGEST_PROMPT` (chunk ~45k, cap 50k chars, 80 pages) + `MERGE_PROMPT` → JSON `{summary, guidance, management_views, qna_summary, key_topics, key_numbers, sentiment, importance}`
   - **Resume-by-design**: transient LLM failures return status `retry` → row stays `downloaded` and is re-tried next run; only real errors (missing file, no extractable text) → `error`
   - Downloads verified: `_upsert_company_file` (7-placeholder INSERT) and the "file already on disk → mark downloaded" branch both fixed during smoke testing

2. **LLM backend abstraction** (`backend/llm.py`)
   - `generate(system, user, max_tokens, retries)` picks backend by `CONCALL_LLM_BACKEND` (default `opencode` → OpenAI-compatible Zen API `https://opencode.ai/zen/v1/chat/completions`, model `deepseek-v4-flash-free`; `ollama` fallback for local qwen2.5:3b)
   - Zen key loaded from `OPENCODE_API_KEY` env or `~/.local/share/opencode/auth.json`; **never logged**
   - Verified live: 200 OK, returns JSON as requested

3. **CLI + daily hooks**
   - `scripts/fetch_concall_transcripts.py` (`--company/--years/--companies/--limit/--dry-run`), `scripts/analyze_concalls.py` (`--company/--companies/--limit`)
   - `scripts/daily_pipeline.py` `_schedule_concall_processing(date_str)` background thread: queue → download → analyze transcripts announced on the run date (2y cutoff)

4. **Backend batch job + API**
   - `POST /api/concalls/batch/start?company_id=` (daemon thread), `GET /api/concalls/batch/status`, `POST /api/concalls/batch/stop`
   - Live progress: `on_progress` callback updates `done`/`processed` per analyzed row (not just per-company)
   - `GET /api/companies/{id}/concalls` — stored summaries
   - `company_documents` concall section joins per-announcement summaries + per-quarter `analyzed` counts
   - Removed a leaked `conn.close()` in `company_documents` that caused a 500 (`sqlite3.ProgrammingError`)

5. **Frontend** (`frontend/index.html`)
   - Concalls tab: toolbar with **Analyze Concalls** / Stop + live progress, per-quarter "N/M summarized", per-asset ✓ when done
   - Collapsible **AI Summary** `<details>` per transcript: summary, guidance, management views, Q&A highlights, key-topic chips, sentiment badge
   - "X concalls summarized with AI" total line; `startConcallBatch`/`stopConcallBatch`/`pollConcallBatchStatus` with throttled live reload of `loadDocuments` while running

### Files Modified / Added
- `backend/concall_pipeline.py` (new), `backend/llm.py` (new), `backend/data/storage/db.py` (concalls table), `backend/app.py` (batch job + endpoints), `scripts/fetch_concall_transcripts.py` (new), `scripts/analyze_concalls.py` (new), `scripts/daily_pipeline.py`, `frontend/index.html`

### Test Results
- Pilot company 681 (Shemaroo): 73 transcript announcements in window; 4 analyzed via CLI with parsed JSON stored (summary/guidance/management_views/qna_summary/key_topics/sentiment/importance)
- Full batch for 681 running end-to-end: queued all, downloaded 65, analyzing all; DB checkpoints each row so it's resumable
- Frontend verified via headless Chrome CDP: Concalls tab renders Analyze/Stop buttons, "23 concalls summarized with AI", per-quarter "7/16 summarized", AI Summary details with sentiment/Guidance/Q&A
- Endpoints: `/api/concalls/batch/status` (idle + running), `/api/companies/681/concalls`, `/api/companies/681/documents` all 200

### Ready for Next Session
- Changes uncommitted — commit + push to GitHub
- Let the 681 batch finish, then optionally kick off the recent backfill (5,160 PDFs last-2y / 437 companies) as a resumable tmux job
- Update PROGRESS.md Step numbering if needed


