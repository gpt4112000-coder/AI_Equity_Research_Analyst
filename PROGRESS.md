# AI Equity Research Analyst - Progress Log

**Last Updated:** 2026-07-29 18:30 IST
**Current Version:** v4.1.0 (FastAPI + Filters)
**GitHub:** https://github.com/gpt4112000-coder/AI_Equity_Research_Analyst
**Latest Commit:** 06e5c30

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

## Database Stats (as of 2026-07-29)

| Metric | Value |
|--------|-------|
| Companies | 3,394 |
| Sectors | 19 |
| Announcements | 181,065 |
| Rule-based Insights | 181,065 |
| Announcements with AI Summary | ~143+ |
| DB Size | 514+ MB |

### By Exchange
- BSE: 135,396
- NSE: 45,669

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
  - `POST /api/companies/{id}/generate_summaries` - batch process all announcements
  - `GET /api/companies/{id}/ai_summary` - company-level synthesized report

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
| GET | `/api/companies/{id}` | Company detail + stored AI summary |
| GET | `/api/companies/{id}/ai_summary` | Generate & store company-level AI summary |
| POST | `/api/companies/{id}/generate_summaries` | Batch generate per-announcement summaries |
| POST | `/api/announcements/{id}/ai_summary` | Generate per-announcement AI summary |
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
