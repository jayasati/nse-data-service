"""Week 18.6: the result beat/miss alert card + its dispatch path."""
from __future__ import annotations

import datetime as dt
import json

import pytest

from nse_data.bot import dispatcher as d
from nse_data.bot.result_alert_message import format_result_alert
from nse_data.scheduler.market_hours import IST
from nse_data.storage import db as dbmod

NOW = dt.datetime(2025, 6, 2, 10, 0, tzinfo=IST)


@pytest.fixture()
def conn(tmp_path):
    c = dbmod.open_db(str(tmp_path / "t.db"))
    dbmod.apply_migrations(c, migrations_dir="migrations")
    yield c
    c.close()


def _seed_financials(conn, symbol="ACME"):
    conn.execute(
        "INSERT INTO extracted_financials "
        "(symbol, period_ending, scope, revenue_cr, pat_cr, eps_basic, "
        " growth_json, narrative_json, broadcast_dt, extracted_at) "
        "VALUES (?, '2025-03-31', 'standalone', 1234.5, 150.2, 12.5, ?, ?, "
        " '02-Jun-2025 09:45:00', ?)",
        (symbol, json.dumps({"yoy_revenue_pct": 22.3}),
         json.dumps({"mgmt_tone": "positive"}), int(NOW.timestamp())),
    )
    conn.commit()


def test_card_format_beat(conn):
    _seed_financials(conn)
    text = format_result_alert(
        conn, symbol="ACME", signal_type="result_beat",
        context={"rsi_5m": 61.2, "trend_regime": "uptrend"}, confidence=0.74,
    )
    assert "🟢 ACME — Result Beat" in text
    assert "Revenue: ₹1,234.5Cr (+22.3% YoY)" in text
    assert "PAT: ₹150.2Cr | EPS: ₹12.5" in text
    assert "Earnings Quality: HIGH" in text
    assert "RSI(5m): 61.2 | Regime: uptrend" in text
    assert "Confidence: Medium (0.74)" in text


def test_card_format_miss(conn):
    _seed_financials(conn)
    text = format_result_alert(
        conn, symbol="ACME", signal_type="result_miss",
        context={}, confidence=0.70,
    )
    assert "🔴 ACME — Result Miss" in text


def test_card_empty_without_financials(conn):
    assert format_result_alert(
        conn, symbol="GHOST", signal_type="result_beat", context={}, confidence=0.7,
    ) == ""


# ------------------------------------------------------------- dispatch path

def _seed_listing(conn, symbol):
    for i in range(35):
        conn.execute(
            "INSERT INTO raw_bhavcopy_cm (date, symbol, series, close, volume) "
            "VALUES (?, ?, 'EQ', 100, 1000)",
            (f"2025-04-{i + 1:02d}" if i < 30 else f"2025-05-{i - 29:02d}", symbol),
        )
    conn.commit()


class FakeRedis:
    def __init__(self):
        self.sets, self.strings, self.hashes = {}, {}, {}

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    def get(self, key):
        return self.strings.get(key)

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))


def test_result_beat_dispatches_card(conn):
    _seed_financials(conn)
    _seed_listing(conn, "ACME")
    conn.execute(
        "INSERT INTO signals (symbol, signal_type, detected_at, direction) "
        "VALUES ('ACME', 'result_beat', ?, 'long')",
        (NOW.isoformat(),),
    )
    conn.commit()
    r = FakeRedis()
    r.hashes["ind:ACME"] = {
        "price_vs_vwap": "above", "vwap_slope": "0.5",
        "rsi_5m": "60.0", "trend_regime": "strong_uptrend",
    }
    sent = []

    def sender(token, chat_id, text, thread_id=None):
        sent.append(text)
        return True

    report = d.dispatch_pass(conn, token="t", chat_id="c",
                             redis_client=r, now=NOW, sender=sender)
    assert report["sent"] == 1
    assert "Result Beat" in sent[0]
    assert "Earnings Quality" in sent[0]
