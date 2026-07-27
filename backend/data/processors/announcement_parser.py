import json
from pathlib import Path
from datetime import datetime
from config import EXISTING_DATA_DIR, IMPORTANT_CATEGORIES, EXCLUDED_CATEGORIES


def load_announcements_from_cache(date_str):
    date_dir = EXISTING_DATA_DIR / date_str
    if not date_dir.exists():
        return []

    announcements = []

    for json_file in date_dir.glob("*.json"):
        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        exchange = "BSE" if "Bse_" in json_file.name else "NSE"
        for raw in data:
            parsed = _parse_raw(raw, exchange)
            if parsed:
                announcements.append(parsed)

    return announcements


def _parse_raw(raw, exchange):
    if exchange == "BSE":
        return {
            "exchange": "BSE",
            "bse_code": str(raw.get("SCRIP_CD", "")),
            "company_name": raw.get("SLONGNAME", ""),
            "category": raw.get("SUBCATNAME", ""),
            "headline": raw.get("NEWSSUB", ""),
            "description": raw.get("HEADLINE", ""),
            "announcement_date": raw.get("DT_TM", "")[:10],
            "announcement_time": raw.get("DT_TM", "")[11:19] if len(raw.get("DT_TM", "")) > 11 else "",
            "is_critical": raw.get("CRITICALNEWS", 0),
            "attachment_url": f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{raw['ATTACHMENTNAME']}" if raw.get("ATTACHMENTNAME") else None,
        }
    else:
        return {
            "exchange": "NSE",
            "nse_symbol": raw.get("symbol", ""),
            "company_name": raw.get("sm_name", ""),
            "category": raw.get("desc", ""),
            "headline": raw.get("attchmntText", raw.get("desc", "")),
            "description": raw.get("attchmntText", ""),
            "announcement_date": raw.get("sort_date", "")[:10] if raw.get("sort_date") else "",
            "announcement_time": raw.get("sort_date", "")[11:19] if len(raw.get("sort_date", "")) > 11 else "",
            "is_critical": 0,
            "attachment_url": raw.get("attchmntFile", None),
            "isin": raw.get("sm_isin", ""),
            "industry": raw.get("smIndustry", ""),
        }


def filter_important_announcements(announcements):
    return [a for a in announcements if a["category"] in IMPORTANT_CATEGORIES]


def filter_by_companies(announcements, bse_codes=None, nse_symbols=None):
    filtered = []
    for a in announcements:
        if a["exchange"] == "BSE" and bse_codes and a.get("bse_code") in bse_codes:
            filtered.append(a)
        elif a["exchange"] == "NSE" and nse_symbols and a.get("nse_symbol") in nse_symbols:
            filtered.append(a)
    return filtered


def group_by_company(announcements):
    grouped = {}
    for a in announcements:
        key = a.get("nse_symbol") or a.get("bse_code") or a.get("company_name")
        if key not in grouped:
            grouped[key] = {
                "company_name": a.get("company_name"),
                "bse_code": a.get("bse_code"),
                "nse_symbol": a.get("nse_symbol"),
                "announcements": [],
            }
        grouped[key]["announcements"].append(a)
    return grouped


def get_announcement_stats(announcements):
    stats = {
        "total": len(announcements),
        "by_exchange": {},
        "by_category": {},
        "by_date": {},
        "critical_count": sum(1 for a in announcements if a.get("is_critical")),
    }
    for a in announcements:
        exchange = a["exchange"]
        stats["by_exchange"][exchange] = stats["by_exchange"].get(exchange, 0) + 1
        category = a["category"]
        stats["by_category"][category] = stats["by_category"].get(category, 0) + 1
        date = a["announcement_date"]
        stats["by_date"][date] = stats["by_date"].get(date, 0) + 1
    return stats


def load_all_cached_dates():
    if not EXISTING_DATA_DIR.exists():
        return []
    dates = []
    for d in EXISTING_DATA_DIR.iterdir():
        if d.is_dir() and d.name.startswith("20"):
            dates.append(d.name)
    return sorted(dates)
