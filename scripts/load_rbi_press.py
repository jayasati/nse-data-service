"""Scrape RBI press releases (rbi.org.in, India-hosted static HTML) into raw_rbi_press.
Central-bank policy/regulatory events move banks & rate-sensitive sectors but are NOT
per-stock NSE filings, so the News engine can't see them — this feed fills that gap and
is surfaced as a sector catalyst on the Buy card.

    PYTHONPATH=src .venv/bin/python scripts/load_rbi_press.py     # daily refresh
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

URL = "https://www.rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx"
_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def classify(title: str) -> str:
    t = title.lower()
    if any(w in t for w in ("monetary policy", "mpc", "policy rate", "repo rate", "bi-monthly")):
        return "monetary_policy"
    if any(w in t for w in ("treasury bill", "open market", "omo", "crr", "auction",
                            "government stock", "liquidity adjustment", "g-sec", "sdf", "vrr", "vrrr")):
        return "liquidity"
    # NOTE: avoid bare "bank" — it matches "Reserve Bank" in every title.
    if any(w in t for w in ("penalty", "directions", "norms", "guidelines", "framework",
                            "risk weight", "prudential", "master direction", "master circular",
                            "lending", "kyc", "npa", "provisioning", "basel", "licen",
                            "co-operative bank", "banking regulation", "nbfc", "deposit")):
        return "banking_regulatory"
    return "other"


def parse(html: str):
    """[(prid, title, date 'YYYY-MM-DD')] newest first. Date = nearest preceding
    'Mon DD, YYYY' on the page (RBI lists each release under its date heading)."""
    dates = [(m.start(), m.group(1)) for m in re.finditer(r"([A-Z][a-z]{2} \d{1,2}, \d{4})", html)]
    out = []
    for m in re.finditer(r"prid=(\d+)[^>]*>\s*([^<]{8,140})</a>", html):
        prid, title = int(m.group(1)), re.sub(r"\s+", " ", m.group(2)).strip()
        d = None
        for pos, ds in dates:                       # nearest date before this link
            if pos < m.start():
                d = ds
            else:
                break
        iso = None
        dm = re.match(r"([A-Z][a-z]{2}) (\d{1,2}), (\d{4})", d) if d else None
        if dm:
            mo, day, yr = dm.groups()
            if mo in _MONTHS:
                iso = f"{int(yr):04d}-{_MONTHS[mo]:02d}-{int(day):02d}"
        out.append((prid, title, iso))
    return out


def main() -> int:
    import requests
    import urllib3
    urllib3.disable_warnings()
    from nse_data.storage.db import open_db, apply_migrations

    html = requests.get(URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=30, verify=False).text
    items = parse(html)
    conn = open_db("data/nse.db")
    conn.execute("PRAGMA busy_timeout=60000")
    apply_migrations(conn)
    now = int(time.time())
    n = 0
    for prid, title, iso in items:
        conn.execute(
            "INSERT INTO raw_rbi_press (prid, pub_date, title, kind, url, first_seen) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(prid) DO UPDATE SET "
            "pub_date=COALESCE(excluded.pub_date, pub_date), title=excluded.title, kind=excluded.kind",
            (prid, iso, title, classify(title), f"{URL}?prid={prid}", now))
        n += 1
    conn.commit()
    recent = conn.execute(
        "SELECT pub_date, kind, title FROM raw_rbi_press WHERE kind IN "
        "('monetary_policy','banking_regulatory') ORDER BY prid DESC LIMIT 5").fetchall()
    print(f"raw_rbi_press: upserted {n} releases. recent policy/regulatory:")
    for d, k, t in recent:
        print(f"  {d}  [{k}]  {t[:70]}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
