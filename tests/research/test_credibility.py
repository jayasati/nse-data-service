"""Unit tests for the Management Credibility engine (Engine 12). Deterministic
in-memory DB: pins delivery hit-rate, the erratic penalty, the bank NII fallback,
the over-promise penalty, and the insufficient-history guard.
"""
from __future__ import annotations

import datetime as _dt
import sqlite3

from nse_data.research import credibility_engine as ce

_FIN_COLS = ("symbol", "period_ending", "scope", "revenue_cr", "net_interest_income_cr",
             "interest_earned_cr", "pat_cr", "broadcast_dt")


def _db(fin_rows, ann_rows=()):
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE extracted_financials(symbol TEXT, period_ending TEXT, scope TEXT, "
              "revenue_cr REAL, net_interest_income_cr REAL, interest_earned_cr REAL, "
              "pat_cr REAL, broadcast_dt TEXT)")
    c.executemany(f"INSERT INTO extracted_financials({','.join(_FIN_COLS)}) "
                  f"VALUES ({','.join('?'*len(_FIN_COLS))})", fin_rows)
    c.execute("CREATE TABLE raw_announcements(symbol TEXT, subject TEXT, details TEXT, "
              "sentiment, broadcast_epoch INT)")
    c.executemany("INSERT INTO raw_announcements VALUES (?,?,?,?,?)", list(ann_rows))
    return c


def _quarters(sym, growth, base=100.0, n=10, bank=False):
    """n quarterly prints ending 2023-03..; each grows YoY by `growth` (fraction)."""
    pes = []
    y, m = 2023, 3
    for _ in range(n):
        pes.append(f"{y}-{m:02d}-{'31' if m in (3,12) else '30'}")
        m += 3
        if m > 12:
            m -= 12; y += 1
    rows = []
    for i, pe in enumerate(pes):
        top = base * ((1 + growth) ** (i / 4.0))
        bdt = (_dt.date.fromisoformat(pe) + _dt.timedelta(days=20)).isoformat()
        rev = None if bank else top
        nii = top if bank else None
        rows.append((sym, pe, "consolidated", rev, nii, None, top * 0.2, bdt))
    return rows, _dt.date.fromisoformat(pes[-1]) + _dt.timedelta(days=25)


def _ep(d):
    return int(_dt.datetime(d.year, d.month, d.day, tzinfo=_dt.timezone(_dt.timedelta(hours=5, minutes=30))).timestamp())


def test_steady_grower_high_credibility():
    rows, asof = _quarters("GROW", 0.15)
    r = ce.credibility_raw(_db(rows), "GROW", _ep(asof))
    assert r is not None and r["score"] >= 75
    assert r["components"]["rev_hit"] == 1.0 and r["components"]["erratic"] < 5


def test_declining_low_credibility():
    rows, asof = _quarters("FALL", -0.15)
    r = ce.credibility_raw(_db(rows), "FALL", _ep(asof))
    assert r is not None and r["score"] < 40
    assert r["components"]["rev_hit"] == 0.0


def test_bank_nii_fallback():
    rows, asof = _quarters("BANKX", 0.12, bank=True)
    r = ce.credibility_raw(_db(rows), "BANKX", _ep(asof))
    assert r is not None and r["score"] >= 75       # NII used as top line


def test_over_promise_penalty():
    # flat top line (no growth) + several order/expansion claims → penalised
    rows, asof = _quarters("LOUD", 0.0)
    asof_ep = _ep(asof)
    ann = [("LOUD", "Bagging/Receiving of orders/contracts", "won order", None, asof_ep - 10 * 86400),
           ("LOUD", "Capacity expansion", "new plant", None, asof_ep - 20 * 86400)]
    r = ce.credibility_raw(_db(rows, ann), "LOUD", asof_ep)
    assert r["components"]["claims"] >= 2 and r["components"]["conversion_adj"] == -12.0


def test_insufficient_history_returns_none():
    rows, asof = _quarters("NEW", 0.15, n=4)   # only ~2 YoY-comparable quarters
    assert ce.credibility_raw(_db(rows), "NEW", _ep(asof)) is None
