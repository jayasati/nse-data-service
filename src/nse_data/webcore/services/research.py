"""Stock-research service — assembles the interactive page payload for one symbol + date range.

Daily candles with gap/day% + delivery, intraday >1.5% legs (minute zigzag), news (chart markers
+ timeline), promoter/FII holding trend, and block/bulk deals. No web imports.
"""
from __future__ import annotations

import datetime as dt

from ..errors import BadRequest, Unavailable
from ..repositories.research import ResearchRepository

_IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _ep(date_str: str, end: bool = False) -> int:
    d = dt.date.fromisoformat(date_str)
    t = dt.time(23, 59) if end else dt.time(0, 0)
    return int(dt.datetime.combine(d, t, _IST).timestamp())


def _ist_date(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts, _IST).date().isoformat()


def _hm(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts, _IST).strftime("%H:%M")


def _zigzag(bars, thr: float = 1.5):
    """Intraday directional legs ≥ thr%. bars = [(ts, close), …]. Returns [(i_from, i_to, dir)]."""
    if len(bars) < 5:
        return []
    legs, piv, ext, d = [], 0, 0, 0
    for i in range(1, len(bars)):
        p = bars[i][1]
        if d >= 0 and p >= bars[ext][1]:
            ext = i; continue
        if d <= 0 and p <= bars[ext][1]:
            ext = i; continue
        if d >= 0 and (bars[ext][1] - p) / bars[ext][1] * 100 >= thr:
            if (bars[ext][1] - bars[piv][1]) / bars[piv][1] * 100 >= thr:
                legs.append((piv, ext, "UP"))
            piv, ext, d = ext, i, -1
        elif d <= 0 and (p - bars[ext][1]) / bars[ext][1] * 100 >= thr:
            if (bars[piv][1] - bars[ext][1]) / bars[piv][1] * 100 >= thr:
                legs.append((piv, ext, "DN"))
            piv, ext, d = ext, i, 1
    pct = (bars[ext][1] - bars[piv][1]) / bars[piv][1] * 100
    if abs(pct) >= thr:
        legs.append((piv, ext, "UP" if pct > 0 else "DN"))
    return legs


class ResearchService:
    def __init__(self, repo: ResearchRepository):
        self.repo = repo

    def study(self, symbol: str, start: str, end: str) -> dict:
        if not self.repo.tables_ready():
            raise Unavailable("candle table not present")
        symbol = symbol.upper().strip()
        try:
            s_ep, e_ep = _ep(start), _ep(end, end=True)
        except ValueError:
            raise BadRequest("dates must be YYYY-MM-DD")
        rows = self.repo.daily_bars(symbol, s_ep, e_ep)
        deliv = self.repo.delivery(symbol, start, end)
        bars, prev = [], None
        for ts, o, h, lo, c, v in rows:
            d = _ist_date(ts)
            bars.append({"time": d, "open": round(o, 2), "high": round(h, 2),
                         "low": round(lo, 2), "close": round(c, 2),
                         "gap_pct": round((o - prev) / prev * 100, 2) if prev else None,
                         "day_pct": round((c - o) / o * 100, 2),
                         "range_pct": round((h - lo) / o * 100, 2),
                         "deliv_pct": deliv.get(d)})
            prev = c
        return {"symbol": symbol, "start": start, "end": end, "bars": bars,
                "intraday": self._intraday(symbol, rows), "news": self._news(symbol, s_ep, e_ep),
                "holdings": self._holdings(symbol), "deals": self._deals(symbol)}

    def _intraday(self, symbol: str, rows) -> list[dict]:
        out = []
        for ts, *_ in rows:
            d = _ist_date(ts)
            day0 = int(dt.datetime.combine(dt.date.fromisoformat(d), dt.time(0, 0), _IST).timestamp())
            mins = self.repo.minute_closes(symbol, day0, day0 + 86400)
            legs = _zigzag(mins)
            if legs:
                out.append({"date": d, "legs": [
                    {"from": _hm(mins[a][0]), "to": _hm(mins[b][0]), "dir": dr,
                     "pct": round((mins[b][1] - mins[a][1]) / mins[a][1] * 100, 1)}
                    for a, b, dr in legs]})
        return out

    def _news(self, symbol: str, s_ep: int, e_ep: int) -> dict:
        rows = self.repo.news(symbol, s_ep, e_ep)
        items = [{"date": _ist_date(ep), "headline": hd, "source": src, "url": url}
                 for ep, hd, src, url in rows]
        markers: dict = {}
        for it in items:
            markers.setdefault(it["date"], []).append(it["headline"])
        return {"items": items, "markers": [
            {"time": d, "count": len(hs), "text": (hs[0][:70] + ("…" if len(hs) > 1 else ""))}
            for d, hs in markers.items()]}

    def _holdings(self, symbol: str) -> list[dict]:
        return [{"qe_date": q, "promoter": p, "fii": f, "dii": di, "pledge": pl}
                for q, p, f, di, pl in self.repo.holdings(symbol)]

    def _deals(self, symbol: str) -> list[dict]:
        return [{"date": d, "type": t, "side": bs, "qty": q, "price": px, "client": cn}
                for d, t, cn, bs, q, px in self.repo.deals(symbol)]
