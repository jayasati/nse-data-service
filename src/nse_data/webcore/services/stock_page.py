"""Per-stock page service — shapes the /stocks symbol tabs from the repository.

The interesting work is the **results** section: it re-runs the exact sector
verdict the live detector fired (sector router + growth_json + narrative_json,
all already stored), so the page shows the same read the Telegram alert did —
one source of truth, recomputed not duplicated. Everything else is bounded
pass-through with light derived fields; every section renders as empty (never
an error) when its tables have no rows for the symbol.
"""
from __future__ import annotations

import json

from ..repositories.stock_page import StockPageRepository


def _parse(s: str | None) -> dict | None:
    if not s:
        return None
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else None
    except (ValueError, TypeError):
        return None


def _num(v):
    return v if isinstance(v, (int, float)) else None


def _nz(v):
    """0/None → None. For bank ratios (GNPA/NNPA/CET1/ROA) a 0 means 'not in
    this filing' (consolidated banks omit them), never a real zero."""
    return v if isinstance(v, (int, float)) and v != 0 else None


def _ebitda(row) -> float | None:
    """True operating EBITDA = PBT(before exceptional) + finance cost +
    depreciation − other income (migration 072 inputs). Uses PBT-before-
    exceptional so a one-off doesn't inflate the operating line; falls back to
    plain PBT when no exceptional split. None unless the core inputs are present."""
    pbt = _num(row["pbt_before_exceptional_cr"])
    if pbt is None:
        pbt = _num(row["pbt_cr"])
    fin, dep, oi = _num(row["finance_cost_cr"]), _num(row["depreciation_cr"]), _num(row["other_income_cr"])
    if pbt is None or fin is None or dep is None or oi is None:
        return None
    return round(pbt + fin + dep - oi, 2)


def _gross_profit(row) -> float | None:
    """Revenue − cost of goods (materials + purchases + inventory change). None
    unless revenue and the materials line are present (non-manufacturers omit)."""
    rev, mat = _num(row["revenue_cr"]), _num(row["cost_of_materials_cr"])
    if rev is None or mat is None:
        return None
    cogs = mat + (_num(row["purchases_of_stock_cr"]) or 0.0) \
        + (_num(row["change_in_inventory_cr"]) or 0.0)
    return round(rev - cogs, 2)


def _margin(numer, denom) -> float | None:
    if not isinstance(numer, (int, float)) or not isinstance(denom, (int, float)) or not denom:
        return None
    return round(numer / denom * 100.0, 2)


class StockPageService:
    def __init__(self, repo: StockPageRepository):
        self.repo = repo

    # ---- shared: the sector-engine read of the latest result ---------------
    def _verdict_for(self, fin_row: dict, symbol: str) -> dict | None:
        """Re-run the live sector verdict from a stored financials row."""
        growth = _parse(fin_row.get("growth_json"))
        narrative = _parse(fin_row.get("narrative_json"))
        if not growth:
            return None
        try:
            from nse_data.fundamentals.sectors import classify_result
            v = classify_result(
                symbol, growth,
                {"profit_on_sale_of_investments_cr": fin_row.get("profit_on_sale_of_investments_cr")},
                narrative=narrative,
            )
        except Exception:  # noqa: BLE001 — a verdict is enrichment, not a 500
            return None
        return {
            "period_ending": fin_row.get("period_ending"),
            "label": v.label, "direction": v.direction,
            "flags": list(v.flags), "summary": v.summary,
            "narrative": narrative,
        }

    def _latest_verdict(self, symbol: str) -> dict | None:
        for row in self.repo.financials(symbol, limit=4):
            v = self._verdict_for(row, symbol)
            if v:
                return v
        return None

    def _sector(self, symbol: str) -> dict:
        out: dict = {"index": None, "sector_class": None, "rs_rank": None, "rs_trend": None}
        try:
            from nse_data.fundamentals.sectors import sector_class_for
            from nse_data.market.sector_map import sector_for
            index = sector_for(symbol)
            out["index"] = index
            out["sector_class"] = sector_class_for(symbol).value
            if index:
                st = self.repo.sector_state_latest(index.upper())
                if st:
                    out["rs_rank"] = st["rs_rank"]
                    out["rs_trend"] = st["rs_trend"]
        except Exception:  # noqa: BLE001 — config files may be absent in tests
            pass
        return out

    # ---- the six sections ---------------------------------------------------
    def overview(self, symbol: str) -> dict:
        symbol = symbol.upper()
        profile = self.repo.profile_latest(symbol)
        return {
            "symbol": symbol,
            "quality": profile,
            "sector": self._sector(symbol),
            "surveillance": self.repo.surveillance(symbol),
            "price_band": self.repo.price_band(symbol),
            "next_event": self.repo.next_pending_event(symbol),
            "result_verdict": self._latest_verdict(symbol),
            "consensus_sources": sorted({e["source"] for e in self.repo.estimates(symbol)}),
        }

    def results(self, symbol: str) -> dict:
        symbol = symbol.upper()
        quarters = []
        for row in self.repo.financials(symbol):
            g = _parse(row.get("growth_json")) or {}
            quarters.append({
                "period_ending": row["period_ending"], "scope": row["scope"],
                "revenue_cr": row["revenue_cr"], "pat_cr": row["pat_cr"],
                "pbt_cr": row["pbt_cr"], "other_income_cr": row["other_income_cr"],
                "nii_cr": row["net_interest_income_cr"],
                "ppop_cr": row["operating_profit_cr"],
                "provisions_cr": row["provisions_cr"],
                # 0 here = not disclosed in this (usually consolidated) filing —
                # a bank is never truly 0% GNPA/CET1/ROA — so show blank, not "0%".
                "gnpa_pct": _nz(row["gross_npa_pct"]), "nnpa_pct": _nz(row["net_npa_pct"]),
                "eps": row["eps_basic"],
                "yoy_revenue_pct": g.get("yoy_revenue_pct"),
                "yoy_pat_pct": g.get("yoy_pat_pct"),
                "yoy_ebitda_pct": g.get("yoy_ebitda_pct"),
                "yoy_ppop_pct": g.get("yoy_ppop_pct"),
                "qoq_revenue_pct": g.get("qoq_revenue_pct"),
                "qoq_pat_pct": g.get("qoq_pat_pct"),
                "qoq_ebitda_pct": g.get("qoq_ebitda_pct"),
                "qoq_ppop_pct": g.get("qoq_ppop_pct"),
                "confidence": row["extract_confidence"], "strategy": row["strategy"],
                # --- analysis fields (migration 072) + derived margins ---
                "ebitda_cr": _ebitda(row),
                "ebitda_margin_pct": _margin(_ebitda(row), row["revenue_cr"]),
                "gross_margin_pct": _margin(_gross_profit(row), row["revenue_cr"]),
                "net_margin_pct": _margin(row["pat_cr"], row["revenue_cr"]),
                "employee_cost_cr": row["employee_cost_cr"],
                "other_expenses_cr": row["other_expenses_cr"],
                "exceptional_items_cr": row["exceptional_items_cr"],
                "current_tax_cr": row["current_tax_cr"],
                "deferred_tax_cr": row["deferred_tax_cr"],
                "other_comprehensive_income_cr": row["other_comprehensive_income_cr"],
                # bank health
                "operating_expenses_cr": row["operating_expenses_cr"],
                "gross_npa_cr": _nz(row["gross_npa_cr"]), "net_npa_cr": _nz(row["net_npa_cr"]),
                "cet1_ratio": _nz(row["cet1_ratio"]), "roa_pct": _nz(row["return_on_assets"]),
            })
        by_period: dict[str, list[dict]] = {}
        for e in self.repo.estimates(symbol):
            by_period.setdefault(e["period_ending"], []).append(
                {k: e[k] for k in ("source", "rev_est_cr", "pat_est_cr", "eps_est",
                                   "nii_est_cr", "nim_est_pct")})
        return {
            "symbol": symbol,
            "quarters": quarters,
            "verdict": self._latest_verdict(symbol),
            "estimates": [{"period_ending": p, "rows": rows}
                          for p, rows in sorted(by_period.items(), reverse=True)],
            "ratings": self.repo.ratings(symbol),
        }

    def events(self, symbol: str) -> dict:
        symbol = symbol.upper()
        return {
            "symbol": symbol,
            "pending": self.repo.pending_events(symbol),
            "board_meetings": self.repo.board_meetings(symbol),
            "corporate_actions": self.repo.corporate_actions(symbol),
            "earnings_setup": self.repo.earnings_setup_latest(symbol),
        }

    def filings(self, symbol: str) -> dict:
        return {"symbol": symbol.upper(),
                "announcements": self.repo.announcements(symbol.upper())}

    def activity(self, symbol: str) -> dict:
        symbol = symbol.upper()
        bt = self.repo.backtest_summary(symbol)
        if bt and bt.get("trades"):
            bt["win_rate"] = round(100.0 * (bt.get("wins") or 0) / bt["trades"], 1)
        else:
            bt = None
        return {
            "symbol": symbol,
            "signal_backtest": self.repo.signal_backtest(symbol),
            "signals": self.repo.signals(symbol),
            "paper_trades": self.repo.paper_trades(symbol),
            "backtest": bt,
        }

    def moves(self, symbol: str) -> dict:
        """Significant intraday moves from the open (gap excluded), constant
        ones first. Carries the symbol's tradeable-universe grade so the UI can
        flag liquid (A/B) names and de-emphasise illiquid noise."""
        symbol = symbol.upper()
        moves = self.repo.intraday_moves(symbol)
        by_date: dict = {}
        for c in self.repo.intraday_move_candidates(symbol):
            by_date.setdefault(c["date"], []).append(c)
        for m in moves:
            m["candidates"] = by_date.get(m["date"], [])
        return {
            "symbol": symbol,
            "grade": self.repo.universe_grade(symbol),
            "moves": moves,
        }

    def flow(self, symbol: str) -> dict:
        symbol = symbol.upper()
        return {
            "symbol": symbol,
            "large_deals": self.repo.large_deals(symbol),
            "insider": self.repo.insider_trades(symbol),
            "shareholding": self.repo.shareholding(symbol),
            "delivery": self.repo.delivery_trend(symbol),
            "oi": self.repo.oi_latest(symbol),
            "volatility": self.repo.volatility_latest(symbol),
        }
