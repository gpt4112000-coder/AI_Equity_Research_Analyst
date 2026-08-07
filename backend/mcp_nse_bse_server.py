#!/usr/bin/env python3
"""MCP server exposing NSE/BSE exchange data + the local research DB as
interactive research tools for opencode.

Registered as a local stdio MCP server in opencode.json so the agent can query
live announcements, corporate actions, board meetings, annual reports and the
results of our backfilled analysis pipeline.

Run standalone for a quick smoke test:
  python mcp_nse_bse_server.py --self-test

Launched by opencode via:
  python backend/mcp_nse_bse_server.py
"""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "backend"))
sys.path.insert(0, "/home/ubuntu/FinEng/BseIndiaApi/src")
sys.path.insert(0, "/home/ubuntu/FinEng/NseIndiaApi/src")

from mcp.server.fastmcp import FastMCP  # noqa: E402

from config import DB_PATH  # noqa: E402

mcp = FastMCP("nse-bse-research")


def _db():
    import sqlite3
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _parse_date(value):
    if not value:
        return None
    v = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y%m%d"):
        try:
            return dt.datetime.strptime(v, fmt)
        except ValueError:
            continue
    return None


def _search_companies(query, limit=10):
    conn = _db()
    cur = conn.cursor()
    q = f"%{query}%"
    cur.execute("""
        SELECT id, company_name, nse_symbol, bse_code, sector, industry, market_cap
        FROM companies
        WHERE company_name LIKE ? OR nse_symbol LIKE ? OR bse_code LIKE ?
        ORDER BY market_cap DESC LIMIT ?
    """, (q, q, q, limit))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


@mcp.tool()
def search_companies(query: str, limit: int = 10) -> str:
    """Search the local company universe by name, NSE symbol, or BSE code.

    Returns id, company_name, nse_symbol, bse_code, sector, market_cap.
    Use the returned id with db_corporate_events / db_insights.
    """
    return json.dumps(_search_companies(query, limit), default=str)


@mcp.tool()
def db_corporate_events(company_id: int) -> str:
    """Read structured corporate events already stored in the research DB for a
    company: corporate actions, board meetings, and result calendar (from the
    enrichment pipeline). Use after search_companies to resolve company_id.
    """
    conn = _db()
    cur = conn.cursor()
    out = {}
    cur.execute("SELECT company_name, nse_symbol, bse_code FROM companies WHERE id = ?", (company_id,))
    out["company"] = dict(cur.fetchone()) if cur.fetchone() else None
    cur.execute("""
        SELECT exchange, category, subcategory, headline, announcement_date
        FROM announcements WHERE company_id = ? AND event_key IS NOT NULL
        ORDER BY announcement_date DESC LIMIT 50
    """, (company_id,))
    out["corporate_actions"] = [dict(r) for r in cur.fetchall()]
    cur.execute("""
        SELECT exchange, meeting_date, purpose FROM board_meetings
        WHERE company_id = ? ORDER BY meeting_date DESC LIMIT 50
    """, (company_id,))
    out["board_meetings"] = [dict(r) for r in cur.fetchall()]
    cur.execute("""
        SELECT exchange, result_date, event FROM result_calendar
        WHERE company_id = ? ORDER BY result_date DESC LIMIT 50
    """, (company_id,))
    out["result_calendar"] = [dict(r) for r in cur.fetchall()]
    conn.close()
    return json.dumps(out, default=str)


@mcp.tool()
def db_insights(company_id: int, metric: str = "", limit: int = 50) -> str:
    """Read AI-extracted metric insights for a company from the research DB
    (unified: announcement + document insights). metric is optional, e.g.
    'dividend', 'guidance', 'orders', 'capex', 'financials', 'management',
    'acquisition', 'regulatory', 'credit_rating'.
    """
    conn = _db()
    cur = conn.cursor()
    out = {"company_id": company_id}
    out["metrics"] = [r[0] for r in cur.execute(
        "SELECT DISTINCT metric FROM ai_insights WHERE company_id = ? ORDER BY metric",
        (company_id,))]
    if metric:
        cur.execute("""
            SELECT metric, announcement_date, headline, summary, amount, sentiment, importance
            FROM ai_insights WHERE company_id = ? AND metric = ?
            ORDER BY announcement_date DESC LIMIT ?
        """, (company_id, metric, limit))
        out["announcement_insights"] = [dict(r) for r in cur.fetchall()]
    else:
        cur.execute("""
            SELECT metric, announcement_date, headline, summary, amount, sentiment, importance
            FROM ai_insights WHERE company_id = ? ORDER BY announcement_date DESC LIMIT ?
        """, (company_id, limit))
        out["announcement_insights"] = [dict(r) for r in cur.fetchall()]
    conn.close()
    return json.dumps(out, default=str)


def _nse_announcements(symbol, from_date, to_date):
    from nse.NSE import NSE
    out = []
    with NSE("/tmp/opencode/mcp_nse", server=False) as nse:
        for fd, td in _chunks(from_date, to_date):
            try:
                resp = nse.announcements(symbol=symbol, from_date=fd, to_date=td)
                rows = resp if isinstance(resp, list) else resp.get("data", [])
                out.extend(rows)
            except Exception as e:
                out.append({"error": str(e)})
    return out


def _bse_announcements(scripcode, category, subcategory, from_date, to_date):
    from bse import BSE
    out = []
    with BSE("/tmp/opencode/mcp_bse") as bse:
        for fd, td in _chunks(from_date, to_date):
            try:
                resp = bse.announcements(
                    from_date=fd, to_date=td, scripcode=str(scripcode),
                    category=category, subcategory=subcategory)
                table = resp.get("Table") or []
                out.extend(table)
            except Exception as e:
                out.append({"error": str(e)})
    return out


def _chunks(from_date, to_date, max_days=366):
    cur = from_date
    while cur < to_date:
        end = min(cur + dt.timedelta(days=max_days - 1), to_date)
        yield cur, end
        cur = end + dt.timedelta(days=1)


@mcp.tool()
def nse_announcements(symbol: str, from_date: str = "", to_date: str = "",
                      years: int = 1) -> str:
    """Live NSE corporate announcements for a symbol (e.g. 'RELIANCE').
    Use from_date/to_date (YYYY-MM-DD) or just years back from today.
    """
    to = _parse_date(to_date) or dt.datetime.now()
    frm = _parse_date(from_date) or (to - dt.timedelta(days=365 * years))
    return json.dumps(_nse_announcements(symbol.upper(), frm, to), default=str)


@mcp.tool()
def bse_announcements(scripcode: str, category: str = "-1", subcategory: str = "-1",
                      from_date: str = "", to_date: str = "", years: int = 1) -> str:
    """Live BSE corporate announcements for a scrip code (e.g. '500325').
    Optionally filter by category/subcategory, e.g. category='Corp. Action',
    subcategory='Dividend'. Use from_date/to_date (YYYY-MM-DD) or years back.
    """
    to = _parse_date(to_date) or dt.datetime.now()
    frm = _parse_date(from_date) or (to - dt.timedelta(days=365 * years))
    return json.dumps(_bse_announcements(scripcode, category, subcategory, frm, to), default=str)


@mcp.tool()
def nse_corporate_actions(symbol: str, years: int = 3) -> str:
    """Live NSE corporate actions (dividends, bonuses, splits) for a symbol."""
    from nse.NSE import NSE
    out = []
    to = dt.datetime.now()
    frm = to - dt.timedelta(days=365 * years)
    with NSE("/tmp/opencode/mcp_nse", server=False) as nse:
        for fd, td in _chunks(frm, to):
            try:
                out.extend(nse.actions(symbol=symbol.upper(), from_date=fd, to_date=td))
            except Exception as e:
                out.append({"error": str(e)})
    return json.dumps(out, default=str)


@mcp.tool()
def bse_corporate_actions(scripcode: str, years: int = 3) -> str:
    """Live BSE corporate actions (dividends, bonuses, splits) for a scrip code."""
    from bse import BSE
    out = []
    to = dt.datetime.now()
    frm = to - dt.timedelta(days=365 * years)
    with BSE("/tmp/opencode/mcp_bse") as bse:
        for fd, td in _chunks(frm, to):
            try:
                out.extend(bse.actions(scripcode=str(scripcode), from_date=fd, to_date=td))
            except Exception as e:
                out.append({"error": str(e)})
    return json.dumps(out, default=str)


@mcp.tool()
def nse_board_meetings(symbol: str, years: int = 1) -> str:
    """Live NSE board meeting intimation dates for a symbol."""
    from nse.NSE import NSE
    out = []
    to = dt.datetime.now()
    frm = to - dt.timedelta(days=365 * years)
    with NSE("/tmp/opencode/mcp_nse", server=False) as nse:
        for fd, td in _chunks(frm, to):
            try:
                out.extend(nse.boardMeetings(symbol=symbol.upper(), from_date=fd, to_date=td))
            except Exception as e:
                out.append({"error": str(e)})
    return json.dumps(out, default=str)


@mcp.tool()
def nse_annual_reports(symbol: str) -> str:
    """Live list of NSE annual report filings (FY, file URLs) for a symbol."""
    from nse.NSE import NSE
    with NSE("/tmp/opencode/mcp_nse", server=False) as nse:
        try:
            resp = nse.annual_reports(symbol=symbol.upper())
            return json.dumps(resp.get("data") or [], default=str)
        except Exception as e:
            return json.dumps({"error": str(e)})


@mcp.tool()
def bse_result_calendar(scripcode: str, years: int = 1) -> str:
    """Live BSE corporate results calendar for a scrip code (upcoming + recent)."""
    from bse import BSE
    out = []
    to = dt.datetime.now()
    frm = to - dt.timedelta(days=365 * years)
    with BSE("/tmp/opencode/mcp_bse") as bse:
        for fd, td in _chunks(frm, to):
            try:
                out.extend(bse.resultCalendar(scripcode=str(scripcode), from_date=fd, to_date=td))
            except Exception as e:
                out.append({"error": str(e)})
    return json.dumps(out, default=str)


def self_test():
    """Non-MCP smoke test of a couple of tools."""
    print(json.dumps(_search_companies("birla", 3), indent=1, default=str))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    else:
        mcp.run()
