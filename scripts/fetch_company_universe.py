#!/usr/bin/env python3
"""
Step 1: Fetch company universe from BSE, cross-reference NSE symbols from announcement cache.
Filter: market_cap < 2000 Cr, Active Equity stocks.
"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
sys.path.insert(0, str(Path("/home/ubuntu/FinEng")))

from data.storage.db import get_db
from config import EXISTING_DATA_DIR

BSE_GROUPS = ['A', 'B', 'M', 'MT', 'T', 'P', 'Z', 'X', 'XT', 'IP', 'MS']
MAX_MCAP_CR = 2000


def fetch_bse_stocks():
    """Fetch all BSE stocks via BseIndiaApi and filter by market cap."""
    from BseIndiaApi.src.bse import BSE

    bse = BSE(Path("/tmp"))
    all_stocks = []

    for group in BSE_GROUPS:
        try:
            data = bse.listSecurities(group=group, segment="Equity", status="Active")
            for d in data:
                mcap_str = d.get("Mktcap", "0")
                mcap = float(mcap_str) if mcap_str else 0
                all_stocks.append({
                    "bse_code": d["SCRIP_CD"],
                    "company_name": d["Scrip_Name"],
                    "isin": d.get("ISIN_NUMBER"),
                    "industry": d.get("INDUSTRY"),
                    "group": group,
                    "market_cap_cr": mcap,
                    "scrip_id": d.get("scrip_id"),
                })
            print(f"  BSE Group {group}: {len(data)} stocks")
        except Exception as e:
            print(f"  BSE Group {group}: ERROR - {str(e)[:60]}")

    bse.exit()
    return all_stocks


def extract_nse_symbols_from_cache():
    """Extract NSE symbol -> company name mapping from announcement cache."""
    nse_map = {}

    if not EXISTING_DATA_DIR.exists():
        return nse_map

    for date_dir in EXISTING_DATA_DIR.iterdir():
        if not date_dir.is_dir():
            continue
        for f in date_dir.glob("nse_*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8", errors="replace"))
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    sym = (item.get("symbol") or "").strip()
                    name = (item.get("sm_name") or "").strip()
                    isin = (item.get("sm_isin") or "").strip()
                    industry = (item.get("smIndustry") or "").strip()
                    if sym and name:
                        if sym not in nse_map:
                            nse_map[sym] = {
                                "nse_symbol": sym,
                                "company_name": name,
                                "isin": isin,
                                "industry": industry,
                            }
            except Exception:
                continue

    return nse_map


def deduplicate_by_isin(bse_stocks, nse_map):
    """
    Merge BSE and NSE stocks, deduplicate by ISIN.
    Returns combined list with both bse_code and nse_symbol where available.
    """
    # Index NSE by ISIN
    nse_by_isin = {}
    for sym, info in nse_map.items():
        if info["isin"]:
            nse_by_isin[info["isin"]] = info

    # Index BSE by ISIN
    merged = {}
    for stock in bse_stocks:
        isin = stock["isin"]
        if isin and isin in merged:
            # Duplicate ISIN from BSE — keep the one with higher mcap
            if stock["market_cap_cr"] > merged[isin]["market_cap_cr"]:
                merged[isin] = stock
        elif isin:
            merged[isin] = stock

    # Add NSE stocks not in BSE
    for sym, info in nse_map.items():
        isin = info["isin"]
        if isin and isin not in merged:
            merged[isin] = {
                "bse_code": None,
                "company_name": info["company_name"],
                "isin": isin,
                "industry": info["industry"],
                "group": None,
                "market_cap_cr": 0,
                "nse_symbol": sym,
                "scrip_id": None,
            }

    # Cross-reference: add NSE symbols to BSE stocks
    for isin, stock in merged.items():
        if isin in nse_by_isin:
            stock["nse_symbol"] = nse_by_isin[isin]["nse_symbol"]
        elif "nse_symbol" not in stock:
            stock["nse_symbol"] = None

    return list(merged.values())


def filter_small_cap(stocks, max_mcap=MAX_MCAP_CR):
    """Filter stocks with market cap below max_mcap Cr."""
    filtered = []
    for s in stocks:
        mcap = s.get("market_cap_cr", 0)
        if 0 < mcap <= max_mcap:
            filtered.append(s)
        elif mcap == 0:
            # Keep stocks with unknown mcap (might be small)
            filtered.append(s)
    return filtered


def classify_sector(industry):
    """Map BSE industry to our sector categories."""
    if not industry:
        return "other"
    ind = industry.lower()
    if any(x in ind for x in ["bank", "finance", "nbfc", "housing", "insurance"]):
        return "banking_finance"
    if any(x in ind for x in ["software", "it ", "computer", "technology", "telecom"]):
        return "it_telecom"
    if any(x in ind for x in ["pharma", "drug", "medical", "health", "hospital"]):
        return "pharma_healthcare"
    if any(x in ind for x in ["chemical", "fertilizer", "pesticide"]):
        return "chemicals"
    if any(x in ind for x in ["auto", "tyre", "rubber", "parts"]):
        return "auto_ancillary"
    if any(x in ind for x in ["textile", "cotton", "jute", "silk", "wool"]):
        return "textiles"
    if any(x in ind for x in ["power", "energy", "oil", "gas", "coal", "uranium"]):
        return "energy"
    if any(x in ind for x in ["metal", "steel", "iron", "aluminium", "copper", "zinc"]):
        return "metals"
    if any(x in ind for x in ["cement", "brick", "glass", "ceramic"]):
        return "cement_building"
    if any(x in ind for x in ["real estate", "realty", "construction", "infra"]):
        return "realty_infra"
    if any(x in ind for x in ["food", "beverage", "tobacco", "consumer"]):
        return "consumer"
    if any(x in ind for x in ["paper", "packaging", "printing"]):
        return "paper_packaging"
    if any(x in ind for x in ["jewel", "diamond", "gold", "silver"]):
        return "jewellery"
    if any(x in ind for x in ["media", "entertainment", "publishing"]):
        return "media"
    if any(x in ind for x in ["agri", "farm", "fishery", "dairy"]):
        return "agriculture"
    return "other"


def main():
    print("=" * 60)
    print("STEP 1: Fetching Company Universe")
    print("=" * 60)

    # 1. Fetch BSE stocks
    print("\n[1/4] Fetching BSE stocks...")
    bse_stocks = fetch_bse_stocks()
    print(f"  Total BSE stocks: {len(bse_stocks)}")

    # 2. Extract NSE symbols from announcement cache
    print("\n[2/4] Extracting NSE symbols from announcement cache...")
    nse_map = extract_nse_symbols_from_cache()
    print(f"  NSE symbols found: {len(nse_map)}")

    # 3. Merge and deduplicate
    print("\n[3/4] Merging BSE + NSE, deduplicating by ISIN...")
    all_stocks = deduplicate_by_isin(bse_stocks, nse_map)
    print(f"  Unique companies: {len(all_stocks)}")

    # 4. Filter by market cap
    print(f"\n[4/4] Filtering market cap < {MAX_MCAP_CR} Cr...")
    filtered = filter_small_cap(all_stocks)
    print(f"  Companies below {MAX_MCAP_CR} Cr: {len(filtered)}")
    print(f"  With unknown MCap: {len([s for s in filtered if s['market_cap_cr'] == 0])}")

    # Save to DB
    print("\nSaving to database...")
    conn = get_db()
    cursor = conn.cursor()
    added = 0
    for stock in filtered:
        sector = classify_sector(stock.get("industry"))
        try:
            cursor.execute("""
                INSERT OR REPLACE INTO companies
                (bse_code, nse_symbol, company_name, sector, industry,
                 market_cap, group_name, isin, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            """, (
                stock.get("bse_code"),
                stock.get("nse_symbol"),
                stock["company_name"],
                sector,
                stock.get("industry"),
                stock["market_cap_cr"] * 1e7 if stock["market_cap_cr"] else None,  # Convert Cr to INR
                stock.get("group"),
                stock.get("isin"),
            ))
            added += 1
        except Exception as e:
            print(f"  DB error: {str(e)[:60]}")

    conn.commit()
    conn.close()

    # Stats
    print(f"\n{'=' * 60}")
    print(f"RESULTS:")
    print(f"  Companies added to DB: {added}")
    print(f"  BSE stocks fetched: {len(bse_stocks)}")
    print(f"  NSE symbols from cache: {len(nse_map)}")

    # Sector breakdown
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT sector, COUNT(*) as cnt FROM companies GROUP BY sector ORDER BY cnt DESC")
    print(f"\n  Sector breakdown:")
    for r in cursor.fetchall():
        print(f"    {r['sector']}: {r['cnt']}")
    conn.close()


if __name__ == "__main__":
    main()
