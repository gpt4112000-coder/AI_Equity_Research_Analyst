import sys
import json
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path("/home/ubuntu/FinEng/BseIndiaApi/src")))

from nse.NSE import NSE


def fetch_nse_announcements(index="equities", date_str=None):
    india_tz = ZoneInfo("Asia/Kolkata")
    if date_str:
        now = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=india_tz)
    else:
        now = datetime.now(india_tz)

    from_date = datetime(now.year, now.month, now.day, 0, 0, 0, tzinfo=india_tz)
    to_date = now

    nse = NSE(download_folder="./nse_data", server=False)
    announcements = nse.announcements(
        index=index,
        from_date=from_date,
        to_date=to_date,
    )
    return announcements


def parse_nse_announcement(raw):
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
