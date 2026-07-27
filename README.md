# AI Equity Research - Small Cap / Micro Cap

An AI-powered equity research platform focused on Indian small cap and micro cap stocks (market cap up to ₹10,000 Cr).

## Features

- **Company Profiles**: Track 47+ small cap companies across 12 sectors
- **Announcements**: BSE/NSE corporate announcements with filtering
- **Technical Analysis**: RSI, MACD, trend analysis, valuation metrics
- **Fundamental Data**: P/E, P/B, ROE, market cap from Yahoo Finance
- **Sector Analysis**: IT, Pharma, Chemicals, Textiles, Auto, Realty, Banking, Metals, Defence

## Quick Start

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/AI_Equity_Research_Analyst_Aug_2026.git
cd AI_Equity_Research_Analyst_Aug_2026

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run the application
./run.sh
```

## Project Structure

```
AI_Equity_Research_Analyst_Aug_2026/
├── backend/
│   ├── app.py              # FastAPI server
│   ├── config.py           # Configuration
│   ├── data/
│   │   ├── collectors/     # BSE, NSE, yfinance data
│   │   ├── processors/     # Announcement parser
│   │   └── storage/        # SQLite database
│   └── analysis/           # Technical analysis
├── frontend/
│   └── index.html          # Web dashboard
├── scripts/
│   └── daily_pipeline.py   # Data collection
├── requirements.txt
└── run.sh
```

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/sectors` | List all sectors |
| `GET /api/companies` | List companies (filter by sector) |
| `GET /api/companies/{id}` | Company profile |
| `GET /api/companies/{id}/analysis` | Technical analysis |
| `GET /api/dashboard` | Dashboard stats |
| `GET /api/cache/{date}/announcements` | Daily announcements |

## Sectors Tracked

- IT Software (10 companies)
- Chemicals (8 companies)
- Pharma (6 companies)
- Textiles (5 companies)
- Auto Ancillary (4 companies)
- Consumer (4 companies)
- Banking/Finance (3 companies)
- Realty (3 companies)
- Metals/Mining (2 companies)
- Defence/Aerospace (2 companies)

## Tech Stack

- **Backend**: FastAPI, SQLite, Python
- **Frontend**: Vanilla JS, Tailwind CSS
- **Data Sources**: BSE India API, NSE India API, Yahoo Finance
