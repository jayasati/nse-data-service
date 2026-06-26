"""NSDL fortnightly Sector-wise FPI Investment collector + sector-rotation read.

This is the TRUE per-sector FPI rotation the daily feed can't give. NSDL publishes a fortnightly
static HTML report per sector (net investment + AUC). We read the selection page to find the latest
report's static-file URL, fetch + parse it (34 sectors), and store the latest fortnight's net
equity flow per sector in raw_fpi_sector. rotation() ranks sectors into/out-of → surfaced in the
brief + desk note. Not auto-scored into conviction (validation discipline).

Markup-drift guard: the wide table has a fixed 98-col shape (2 + 4 periods x 2 currencies x 12
asset cols); the latest-fortnight net-equity is col 50. If the shape changes we raise rather than
silently mis-parse.
"""
from __future__ import annotations

import datetime as _dt
import re
import sqlite3

import httpx
import structlog

log = structlog.get_logger(__name__)

_BASE = "https://www.fpi.nsdl.co.in/web/"
_SELECTION = _BASE + "Reports/FPI_Fortnightly_Selection.aspx"
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
_TR = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
_OPT = re.compile(r"<option[^>]*value=['\"]([^'\"]+FIIInvestSector[^'\"]+)['\"][^>]*>([^<]+)</option>", re.I)

# column indices in a sector data row (validated against len==98)
_C_SECTOR, _C_NET_EQ, _C_NET_TOT, _C_AUC_EQ, _ROW_LEN = 1, 50, 61, 74, 98


class FpiSectorParseError(Exception):
    """NSDL fortnightly sector report markup didn't match the expected shape."""


def _cells(tr: str) -> list[str]:
    import html as _html
    return [_html.unescape(re.sub(r"<[^>]+>", "", x)).replace("\xa0", " ").strip()
            for x in _CELL.findall(tr)]


def _num(s: str) -> float | None:
    s = (s or "").replace(",", "").strip()
    if s in ("", "-", "NA"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


_FN_DATE = re.compile(r"FIIInvestSector_([A-Za-z]+)(\d{2})(\d{4})\.html", re.I)


def _parse_filename_date(rel: str) -> str | None:
    """'~/.../FIIInvestSector_June152026.html' -> '2026-06-15'. The filename month is consistent;
    the dropdown LABELS mix abbreviations (JUN/MAR vs JUNE/MAY), so the filename is the reliable
    source. Tries abbreviated (%b) then full (%B) month names."""
    m = _FN_DATE.search(rel or "")
    if not m:
        return None
    mon, dd, yyyy = m.groups()
    for fmt in ("%b", "%B"):
        try:
            month = _dt.datetime.strptime(mon.title(), fmt).month
            return _dt.date(int(yyyy), month, int(dd)).isoformat()
        except ValueError:
            continue
    return None


def _parse_label_date(label: str) -> str | None:
    """Fallback: 'JUNE 15, 2026' / 'JUN 15, 2026' -> '2026-06-15' (tries full + abbrev month)."""
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return _dt.datetime.strptime(label.strip().title(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def latest_report(client: httpx.Client) -> tuple[str, str, str]:
    """(static_url, as_of_date_iso, period_label) for the most recent fortnight."""
    h = client.get(_SELECTION, headers=_UA).text
    opts = _OPT.findall(h)
    if not opts:
        raise FpiSectorParseError("no fortnightly report options on selection page")
    rel, label = opts[0]                       # newest first
    url = _BASE + rel.lstrip("~/").removeprefix("web/")
    return url, (_parse_filename_date(rel) or _parse_label_date(label) or label), label.strip()


def parse_sector_table(html: str) -> list[dict]:
    # The page has several tables; the sector table's data rows have the fixed 98-col shape with a
    # numeric Sr.No. Match only those (other tables / header rows are skipped). If NSDL shifts the
    # shape, we parse <10 sectors and raise — the drift guard.
    out = []
    for r in (_cells(tr) for tr in _TR.findall(html)):
        if len(r) == _ROW_LEN and r[0].strip().isdigit() and r[_C_SECTOR].strip():
            out.append({"sector": r[_C_SECTOR].strip(), "net_equity_cr": _num(r[_C_NET_EQ]),
                        "net_total_cr": _num(r[_C_NET_TOT]), "auc_equity_cr": _num(r[_C_AUC_EQ])})
    if len(out) < 10:
        raise FpiSectorParseError(f"parsed only {len(out)} sectors (expected ~34) — NSDL drift")
    return out


def fetch_and_store(conn: sqlite3.Connection) -> dict:
    with httpx.Client(timeout=40, follow_redirects=True) as client:
        url, as_of, label = latest_report(client)
        if conn.execute("SELECT 1 FROM raw_fpi_sector WHERE as_of_date=? LIMIT 1", (as_of,)).fetchone():
            return {"as_of_date": as_of, "skipped": "already collected"}
        sectors = parse_sector_table(client.get(url, headers=_UA).text)
    for s in sectors:
        conn.execute(
            "INSERT OR REPLACE INTO raw_fpi_sector (as_of_date, period_label, sector, net_equity_cr, "
            "net_total_cr, auc_equity_cr, captured_at) VALUES (?,?,?,?,?,?,datetime('now'))",
            (as_of, label, s["sector"], s["net_equity_cr"], s["net_total_cr"], s["auc_equity_cr"]))
    conn.commit()
    rep = {"as_of_date": as_of, "label": label, "sectors": len(sectors)}
    log.info("fpi_sector", **rep)
    return rep


def backfill(conn: sqlite3.Connection, since: str = "2025-05-01", throttle: float = 3.0) -> dict:
    """Fetch + store historical fortnightly sector reports with as_of_date >= `since` (for the
    backtest). Throttled — NSDL rate-limits the heavy static-file fetches."""
    import time
    fetched = skipped = failed = 0
    with httpx.Client(timeout=40, follow_redirects=True) as client:
        opts = _OPT.findall(client.get(_SELECTION, headers=_UA).text)
        for rel, label in opts:
            as_of = _parse_filename_date(rel) or _parse_label_date(label.strip())
            if not as_of or as_of < since:
                continue
            if conn.execute("SELECT 1 FROM raw_fpi_sector WHERE as_of_date=? LIMIT 1",
                            (as_of,)).fetchone():
                skipped += 1
                continue
            url = _BASE + rel.lstrip("~/").removeprefix("web/")
            try:
                sectors = parse_sector_table(client.get(url, headers=_UA).text)
            except Exception as e:  # noqa: BLE001
                log.warning("fpi_backfill_failed", as_of=as_of, err=str(e))
                failed += 1
                continue
            for s in sectors:
                conn.execute(
                    "INSERT OR REPLACE INTO raw_fpi_sector (as_of_date, period_label, sector, "
                    "net_equity_cr, net_total_cr, auc_equity_cr, captured_at) "
                    "VALUES (?,?,?,?,?,?,datetime('now'))",
                    (as_of, label.strip(), s["sector"], s["net_equity_cr"], s["net_total_cr"],
                     s["auc_equity_cr"]))
            conn.commit()
            fetched += 1
            time.sleep(throttle)
    rep = {"fetched": fetched, "skipped": skipped, "failed": failed}
    log.info("fpi_sector_backfill", **rep)
    return rep


def rotation(conn: sqlite3.Connection, n: int = 4) -> dict:
    """Top sectors FPI rotated INTO / OUT OF in the latest fortnight (by net equity ₹cr)."""
    d = conn.execute("SELECT MAX(as_of_date) FROM raw_fpi_sector").fetchone()
    if not d or not d[0]:
        return {}
    rows = [(s, v) for s, v in conn.execute(
        "SELECT sector, net_equity_cr FROM raw_fpi_sector WHERE as_of_date=? "
        "AND net_equity_cr IS NOT NULL ORDER BY net_equity_cr DESC", (d[0],))]
    return {"as_of_date": d[0],
            "into": [(s, v) for s, v in rows[:n] if v > 0],
            "out_of": [(s, v) for s, v in rows[-n:] if v < 0][::-1]}


def _load_sector_map() -> dict:
    """{NSDL sector name -> [NSE sectoral index names]} from config/fpi_sector_map.yaml."""
    import pathlib
    import yaml
    p = pathlib.Path(__file__).resolve().parents[3] / "config" / "fpi_sector_map.yaml"
    if not p.exists():
        return {}
    return (yaml.safe_load(p.read_text()) or {}).get("sector_to_indices", {})


def tag_member_stocks(conn: sqlite3.Connection, min_flow_cr: float = 3000.0) -> dict:
    """Map the latest fortnight's strong-flow sectors down to member stocks (via NSE sectoral-index
    membership) and tag each TAILWIND/HEADWIND. Only sectors with |net equity| >= min_flow_cr and a
    mapped index are tagged. Writes fpi_sector_stock."""
    d = conn.execute("SELECT MAX(as_of_date) FROM raw_fpi_sector").fetchone()
    if not d or not d[0]:
        return {"tagged": 0}
    as_of = d[0]
    smap = _load_sector_map()
    conn.execute("DELETE FROM fpi_sector_stock WHERE as_of_date=?", (as_of,))
    tagged = 0
    for sector, net in conn.execute(
            "SELECT sector, net_equity_cr FROM raw_fpi_sector WHERE as_of_date=? "
            "AND net_equity_cr IS NOT NULL", (as_of,)):
        if abs(net) < min_flow_cr or sector not in smap:
            continue
        sig = "FPI_SECTOR_TAILWIND" if net > 0 else "FPI_SECTOR_HEADWIND"
        members: set[str] = set()
        for idx in smap[sector]:
            members.update(r[0] for r in conn.execute(
                "SELECT symbol FROM raw_index_members WHERE index_name=?", (idx,)))
        for sym in members:
            conn.execute(
                "INSERT OR REPLACE INTO fpi_sector_stock (as_of_date, symbol, sector, "
                "net_equity_cr, signal, created_at) VALUES (?,?,?,?,?,datetime('now'))",
                (as_of, sym, sector, round(net, 1), sig))
            tagged += 1
    conn.commit()
    log.info("fpi_sector_stock", as_of_date=as_of, tagged=tagged)
    return {"as_of_date": as_of, "tagged": tagged}


def register_fpi_sector_job(scheduler, db_path: str) -> str:
    """Mon & Thu 18:45 IST — fortnightly data updates ~twice monthly; idempotent on as_of_date."""
    from apscheduler.triggers.cron import CronTrigger

    from ..events.calendar import _feature_enabled
    from ..scheduler import market_hours
    from ..storage.db import open_db
    job_id = "fpi_sector"

    def _tick():
        if not _feature_enabled("fpi_sector", True):
            return
        conn = open_db(db_path)
        try:
            fetch_and_store(conn)
            tag_member_stocks(conn)
        except Exception:
            log.exception("fpi_sector_failed")
        finally:
            conn.close()

    scheduler.add_job(
        _tick, trigger=CronTrigger(day_of_week="mon,thu", hour=18, minute=45,
                                   timezone=market_hours.IST),
        id=job_id, max_instances=1, coalesce=True, replace_existing=True)
    return job_id
