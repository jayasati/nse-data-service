"""Collect the per-stock news memory into raw_news (plans/NEWS_MEMORY_PLAN.md).

  --headlines  Google News RSS per symbol (broad coverage; headline+url+date)
  --fulltext   ET Markets / Moneycontrol market RSS → direct URL → article body,
               entity-resolved to a symbol

    # focus test:
    PYTHONPATH=src .venv/bin/python -u scripts/collect_news.py --symbols MTARTECH --fulltext
    # nightly universe run:
    PYTHONPATH=src .venv/bin/python -u scripts/collect_news.py --headlines --fulltext --sleep 1
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

GRADES = ("A core", "B tradeable", "C volatile", "illiquid", "etf")

# Spend the limited body-fetch budget on the headlines most likely to move a stock.
_HI_RELEVANCE = ("result", "earnings", "profit", "quarter", "q1fy", "q2fy", "q3fy", "q4fy",
                 "rating", "upgrade", "downgrade", "order win", "bags order", "wins order",
                 "contract", "acquir", "acquisition", "merger", "stake", "block deal",
                 "bulk deal", "dividend", "buyback", "fund rais", "qip", "guidance", "fraud",
                 "sebi", "penalty", "resign", "default", "delisting", "open offer")


def _relevance(headline: str | None) -> int:
    h = (headline or "").lower()
    return sum(1 for k in _HI_RELEVANCE if k in h)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--symbols", help="comma list (default: graded universe)")
    ap.add_argument("--grades", default="A core,B tradeable,C volatile",
                    help="universe grades for the headline layer")
    ap.add_argument("--limit-symbols", type=int, default=0, help="cap symbols (0=all)")
    ap.add_argument("--headlines", action="store_true", help="run Google-News headline layer")
    ap.add_argument("--fulltext", action="store_true", help="run publisher full-text layer")
    ap.add_argument("--max-bodies", type=int, default=200,
                    help="cap full-text fetches per run (relevance-prioritised)")
    ap.add_argument("--sleep", type=float, default=1.0, help="politeness sleep between requests")
    args = ap.parse_args()
    if not args.headlines and not args.fulltext:
        args.headlines = args.fulltext = True   # default: both

    from nse_data.storage.db import open_db, apply_migrations
    from nse_data.research import news_store as ns

    conn = open_db(args.db)
    conn.execute("PRAGMA busy_timeout=60000")
    apply_migrations(conn)
    client = ns.make_client()

    # symbol → company name (for the Google News query)
    grades = tuple(g.strip() for g in args.grades.split(",") if g.strip())
    if args.symbols:
        syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        ph = ",".join("?" * len(grades))
        syms = [r[0] for r in conn.execute(
            f"SELECT symbol FROM tradeable_universe WHERE grade IN ({ph})", grades)]
    if args.limit_symbols:
        syms = syms[:args.limit_symbols]
    names = {}
    for s in syms:
        row = conn.execute(
            "SELECT company_name FROM raw_quote_metadata WHERE symbol=? AND company_name IS NOT NULL",
            (s,)).fetchone()
        names[s] = (row[0] if row else s)

    total_new = 0

    if args.headlines:
        print(f"== headline layer: {len(syms)} symbols ==", flush=True)
        new = 0
        for i, s in enumerate(syms, 1):
            arts = ns.fetch_google_news(client, f"{names[s]} stock")
            for a in arts:
                a["symbol"] = s
                a["source"] = "google_news"
                a["text_status"] = "headline_only"
            new += ns.store_news(conn, arts)
            time.sleep(args.sleep)
            if i % 50 == 0:
                print(f"  [{i}/{len(syms)}] +{new} headlines so far", flush=True)
        print(f"  headline layer: {new} new rows", flush=True)
        total_new += new

    if args.fulltext:
        print("== full-text layer: publisher feeds ==", flush=True)
        name_map = ns.build_name_map(conn)
        only = set(syms) if args.symbols else None   # focus filter when --symbols given
        cands = []
        for source, url in ns.PUBLISHER_FEEDS.items():
            try:
                feed = ns.parse_publisher_feed(client.get(url).text)
            except Exception as e:  # noqa: BLE001
                print(f"  {source}: feed error {e}"); continue
            for it in feed:
                sym = ns.resolve_symbol(it["headline"], name_map)
                if only is not None and sym not in only:
                    continue
                it["symbol"] = sym
                it["source"] = source
                cands.append(it)
            print(f"  {source}: {len(feed)} items scanned", flush=True)
        # spend the body budget on the highest-relevance headlines first (results/ratings/deals)
        cands.sort(key=lambda it: -_relevance(it["headline"]))
        bodies = new = 0
        for it in cands:
            if bodies < args.max_bodies:
                text, status = ns.fetch_article_text(client, it["url"])
                it["article_text"], it["text_status"] = (text or None), status
                bodies += 1
                time.sleep(args.sleep)
            else:
                it["text_status"] = "headline_only"
            new += ns.store_news(conn, [it])
        print(f"  full-text layer: {new} new rows, {bodies}/{len(cands)} bodies fetched "
              f"(relevance-prioritised)", flush=True)
        total_new += new

    tot = conn.execute("SELECT COUNT(*) FROM raw_news").fetchone()[0]
    okt = conn.execute("SELECT COUNT(*) FROM raw_news WHERE text_status='ok'").fetchone()[0]
    print(f"\nDONE: +{total_new} new · raw_news total={tot} (full-text ok={okt})")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
