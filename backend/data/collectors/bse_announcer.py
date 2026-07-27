import sys
import json
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path("/home/ubuntu/FinEng/BseIndiaApi/src")))

from bse import BSE


def fetch_bse_announcements(date_str=None):
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")

    ann = []
    page_count = 1
    total_count = 1000

    with BSE('./') as bse:
        while True:
            res = bse.announcements(page_no=page_count)
            if page_count == 1:
                total_count = res['Table1'][0]['ROWCNT']
            page_count += 1
            ann.extend(res['Table'])
            if len(ann) >= total_count:
                break

    return ann


def parse_bse_announcement(raw):
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
