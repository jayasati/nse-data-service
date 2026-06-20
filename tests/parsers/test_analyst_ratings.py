"""Weeks 18.7/18.8: analyst ratings scraper + tier-1 upgrade/downgrade signals."""
from __future__ import annotations

import datetime as dt

import pytest

from nse_data.parsers import analyst_ratings as ar
from nse_data.scheduler.market_hours import IST
from nse_data.storage import db as dbmod

NOW = dt.datetime(2025, 6, 2, 10, 0, tzinfo=IST)
TIERS = {"icici securities": 1, "goldman sachs": 1, "dolat capital": 2}


# ---------------------------------------------------------------- pure parse

def test_parse_reco_title_with_target():
    reco = ar.parse_reco_title(
        "Buy UltraTech Cement; target of Rs 13,000: ICICI Securities")
    assert reco == {"call": "buy", "company": "UltraTech Cement",
                    "target": 13000.0, "brokerage": "ICICI Securities"}


def test_parse_reco_title_without_target():
    reco = ar.parse_reco_title("Reduce Acme Industries: Goldman Sachs")
    assert reco is not None
    assert reco["call"] == "reduce"
    assert reco["target"] is None
    assert reco["brokerage"] == "Goldman Sachs"


def test_parse_reco_title_rejects_non_recos():
    assert ar.parse_reco_title("Top 10 stock picks for June") is None
    assert ar.parse_reco_title("") is None
    assert ar.parse_reco_title(None) is None


def test_parse_rss():
    xml = """<rss><channel>
      <item><title>Buy X; target of Rs 100: Broker</title>
        <link>http://e/1</link><pubDate>Mon, 02 Jun 2025 09:30:00 +0530</pubDate></item>
      <item><title></title></item>
    </channel></rss>"""
    items = ar.parse_rss(xml)
    assert len(items) == 1
    assert items[0]["title"].startswith("Buy X")
    assert ar.parse_rss("<broken") == []


def test_match_brokerage_tier():
    assert ar.match_brokerage_tier("ICICI Securities Ltd", TIERS) == 1
    assert ar.match_brokerage_tier("Dolat Capital", TIERS) == 2
    assert ar.match_brokerage_tier("Unknown Desk", TIERS) is None


def test_analyst_signal_type():
    assert ar.analyst_signal_type("hold", "buy") == ar.ANALYST_UPGRADE_T1
    assert ar.analyst_signal_type("buy", "sell") == ar.ANALYST_DOWNGRADE_T1
    assert ar.analyst_signal_type("buy", "accumulate") is None   # same rank
    assert ar.analyst_signal_type(None, "buy") is None           # first sighting
    assert ar.analyst_signal_type("buy", None) is None


# -------------------------------------------------------------------- pass

@pytest.fixture()
def conn(tmp_path):
    c = dbmod.open_db(str(tmp_path / "t.db"))
    dbmod.apply_migrations(c, migrations_dir="migrations")
    c.execute(
        "INSERT INTO raw_announcements "
        "(fingerprint, segment, symbol, company_name, subject, broadcast_dt, created_at) "
        "VALUES ('f1', 'EQ', 'ULTRACEMCO', 'UltraTech Cement Limited', 's', "
        " '01-Jun-2025 10:00:00', 0)",
    )
    c.commit()
    yield c
    c.close()


def _item(title, pub="Mon, 02 Jun 2025 09:30:00 +0530"):
    return {"title": title, "link": "http://e/x", "published_at": pub}


def test_pass_ingests_and_resolves_symbol(conn):
    report = ar.run_analyst_ratings_pass(
        conn, fetcher=lambda: [_item(
            "Buy UltraTech Cement; target of Rs 13,000: ICICI Securities")],
        tiers=TIERS, now=NOW, sender=None,
    )
    assert report["inserted"] == 1
    row = conn.execute(
        "SELECT symbol, brokerage, tier, old_call, new_call, new_target "
        "FROM raw_analyst_ratings",
    ).fetchone()
    assert row == ("ULTRACEMCO", "ICICI Securities", 1, None, "buy", 13000.0)
    # first sighting → no call change → no signal
    assert conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 0


def test_pass_idempotent_on_refetch(conn):
    items = [_item("Buy UltraTech Cement; target of Rs 13,000: ICICI Securities")]
    ar.run_analyst_ratings_pass(conn, fetcher=lambda: items, tiers=TIERS, now=NOW)
    report = ar.run_analyst_ratings_pass(conn, fetcher=lambda: items, tiers=TIERS, now=NOW)
    assert report["inserted"] == 0
    assert conn.execute("SELECT COUNT(*) FROM raw_analyst_ratings").fetchone()[0] == 1


def test_tier1_downgrade_fires_signal_and_alert(conn):
    sent = []

    def sender(token, chat_id, text, thread_id=None, **_kw):
        sent.append(text)
        return True

    ar.run_analyst_ratings_pass(
        conn, fetcher=lambda: [_item(
            "Buy UltraTech Cement; target of Rs 13,000: ICICI Securities")],
        tiers=TIERS, now=NOW, sender=sender,
    )
    report = ar.run_analyst_ratings_pass(
        conn, fetcher=lambda: [_item(
            "Sell UltraTech Cement; target of Rs 9,000: ICICI Securities",
            pub="Mon, 02 Jun 2025 11:00:00 +0530")],
        tiers=TIERS, now=NOW, sender=sender,
    )
    assert report["signaled"] == 1
    row = conn.execute(
        "SELECT old_call, new_call, old_target, new_target FROM raw_analyst_ratings "
        "ORDER BY id DESC LIMIT 1",
    ).fetchone()
    assert row == ("buy", "sell", 13000.0, 9000.0)
    sig = conn.execute(
        "SELECT signal_type, direction, dispatched FROM signals",
    ).fetchone()
    assert sig == (ar.ANALYST_DOWNGRADE_T1, "short", 1)
    assert len(sent) == 1
    assert "Tier-1 Analyst Downgrade" in sent[0]
    assert "Buy → Sell" in sent[0]
    assert "₹13,000 → ₹9,000" in sent[0]


def test_tier2_change_stored_but_silent(conn):
    sent = []

    def sender(token, chat_id, text, thread_id=None, **_kw):
        sent.append(text)
        return True

    ar.run_analyst_ratings_pass(
        conn, fetcher=lambda: [_item("Buy UltraTech Cement: Dolat Capital")],
        tiers=TIERS, now=NOW, sender=sender,
    )
    report = ar.run_analyst_ratings_pass(
        conn, fetcher=lambda: [_item("Sell UltraTech Cement: Dolat Capital",
                                     pub="Mon, 02 Jun 2025 11:00:00 +0530")],
        tiers=TIERS, now=NOW, sender=sender,
    )
    assert report["signaled"] == 0 and sent == []
    assert conn.execute("SELECT COUNT(*) FROM raw_analyst_ratings").fetchone()[0] == 2


def test_stale_item_stored_but_silent(conn):
    report = ar.run_analyst_ratings_pass(
        conn,
        fetcher=lambda: [_item("Buy UltraTech Cement: ICICI Securities",
                               pub="Thu, 01 May 2025 09:30:00 +0530")],
        tiers=TIERS, now=NOW,
    )
    assert report["inserted"] == 1 and report["signaled"] == 0


def test_polling_window():
    assert ar._in_polling_window(dt.datetime(2025, 6, 2, 9, 0, tzinfo=IST)) is True
    assert ar._in_polling_window(dt.datetime(2025, 6, 2, 7, 0, tzinfo=IST)) is False
    assert ar._in_polling_window(dt.datetime(2025, 6, 2, 16, 0, tzinfo=IST)) is False
    assert ar._in_polling_window(dt.datetime(2025, 6, 1, 10, 0, tzinfo=IST)) is False  # Sunday


def test_load_brokerage_tiers_real_config():
    tiers = ar.load_brokerage_tiers()
    assert tiers.get("goldman sachs") == 1
    assert ar.match_brokerage_tier("Motilal Oswal Financial Services", tiers) == 1
