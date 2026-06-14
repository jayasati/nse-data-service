# Sector Engine Build Roadmap

> Sequencing for the analysis layer. Ranks unbuilt engines by **coverage × edge × build-cost**.
> Pairs with `SECTOR_RESULT_PLAYBOOK.md` (logic) and the `metals.py` reference (pattern).

## Scoring the priorities

Three factors per candidate engine:

- **Coverage** — how many of the 666 classified names it serves (incl. sectors that share its pattern).
- **Edge** — how badly the generic engine mis-reads this sector (i.e. how much a dedicated rule adds). High when the sector's true signal lives *outside* the income statement or below-the-line noise routinely creates false signals.
- **Build cost** — data availability + logic complexity. Low when the metrics are in the standard XBRL extract; high when you need investor-presentation parsing.

## Current state

Validated and live: `bfsi` (118), `it` (34), `energy` (17 + Power/Utilities spillover). That's ~170 names on real rules. Everything else is on `generic`.

## Recommended sequence

**1. metals — BUILD FIRST (reference now written).**
Coverage 23 + Construction Materials 12 (cement shares the EBITDA/tonne shape) = ~35.
Edge HIGH: inventory-timing gains and below-the-line noise routinely fake a beat;
generic can't see EBITDA/tonne. Build cost LOW: volume/EBITDA are in the standard extract.
The `metals.py` reference covers both metals and cement — just point cement names at it
via `sector_mapping.yaml` and tune `realization` to "per bag".

**2. auto — coverage 44, edge MEDIUM-HIGH, cost LOW.**
Volume × ASP × margin are all standard lines. The `commodity_tailwind` guard is the key
edge (generic reads a steel-driven margin pop as quality; it isn't). Highest single-sector
coverage among unbuilt engines. Same dataclass shape as metals.

**3. fmcg — coverage 39, edge HIGH, cost MEDIUM.**
UVG is the whole signal and generic completely misses it (reads value growth as good).
Cost is MEDIUM only because UVG isn't always cleanly disclosed — needs a parse of
management commentary / investor PDF for some names. Big edge where data is present.

**4. capgoods — coverage 100 + Construction 19 = ~119, edge MEDIUM, cost MEDIUM.**
Largest coverage of any unbuilt engine. Edge is the order-book/book-to-bill forward read
and the `working_capital_balloon` guard. Cost is MEDIUM: order inflow/book often sit in
the investor presentation, not the financial statement. Build the income-statement guards
first (works on the standard extract), layer order-book parsing second.

**5. pharma — coverage 63, edge MEDIUM, cost MEDIUM-HIGH.**
US-sales breakout and USFDA events are the edge but both need narrative/segment parsing
beyond the core financials. Worth it for coverage, but sequence after the cheaper wins.
USFDA status is better handled as a separate event-flag feed than inside the result engine.

**6. realty — coverage 13, edge VERY HIGH, cost HIGH.**
Edge is the highest of any sector — the P&L is genuinely misleading (POCS-lumpy) and
generic will actively mislead you here. But cost is HIGH: pre-sales/bookings live only in
the investor presentation, not the filed result, so it needs presentation parsing the
other engines don't. Build last among dedicated engines; until then, route realty to
generic with a hard LOW-confidence cap so it never generates a tradable signal on P&L alone.

**Keep on generic (don't over-build early):**
Chemicals (38), Consumer Services (37), Consumer Durables (31), Services (23),
Telecom (10), Textiles (12), Media (7), Diversified (1), Unclassified (280).
Reason: either idiosyncratic (chemicals specialty-vs-commodity split needs sub-sector
data you may not have) or low coverage. Harden the generic engine's universal guards
(`other_income_prop`, `tax_rate_swing`, `exceptional_item`, `margin_vs_revenue_divergence`)
instead — that lifts quality across all 439 generic names at once, which beats a niche
engine for 7 media stocks.

## Cross-cutting work (do alongside engines 1–2)

- **Shared helpers module.** `_surprise`, `_trend_expectation`, `_pct_change`, the
  below-the-line strip guards — factor these out of `metals.py` into a common module so
  every engine imports them. The universal guards should be a mixin all engines call.
- **Config-drive the tunables.** Move every weight/threshold/dampen factor into
  `sector_mapping.yaml` per sector. You'll retune constantly during backtest; don't
  hardcode.
- **Backtest harness.** Before trusting any engine live: replay 8 quarters of results,
  score each, and measure next-day + 10-day return vs. signal. The `metrics` dict on
  every `SectorSignal` exists for exactly this — it's your audit trail. Validate that
  guards actually improve hit-rate (a guard that doesn't lift backtest accuracy should
  be cut).
- **Confidence gating.** Wire `confidence=LOW` to "no trade" globally. Better to skip a
  result than trade a half-extracted one — especially intraday.

## Suggested milestones

| Milestone | Engines | Cumulative coverage on real rules |
|---|---|---:|
| Now | bfsi, it, energy | ~170 |
| M1 | + metals (+cement) | ~205 |
| M2 | + auto | ~249 |
| M3 | + fmcg | ~288 |
| M4 | + capgoods (+construction) | ~407 |
| M5 | + pharma | ~470 |
| M6 | + realty, hardened generic | ~483 dedicated + strong generic tail |
