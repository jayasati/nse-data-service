"""RSI 14 indicator — sanity-check Wilder math + writer round-trip."""

from __future__ import annotations

from nse_data.indicators.compute import compute_for_symbol
from nse_data.indicators.momentum.rsi import RelativeStrengthIndex

from .conftest import insert_bhavcopy


def test_all_up_moves_pushes_rsi_to_top(indicators_db):
    """Strictly rising closes → RSI saturates near 100 (no down moves)."""
    closes = [100.0 + i for i in range(80)]
    insert_bhavcopy(indicators_db, "TEST", closes)

    compute_for_symbol(indicators_db, RelativeStrengthIndex(), "TEST")

    last = indicators_db.execute(
        "SELECT rsi_14 FROM indicator_rsi WHERE symbol=? ORDER BY date DESC LIMIT 1",
        ("TEST",),
    ).fetchone()
    assert last and last[0] is not None
    assert last[0] > 99.0


def test_all_down_moves_pushes_rsi_to_zero(indicators_db):
    closes = [200.0 - i for i in range(80)]
    insert_bhavcopy(indicators_db, "TEST", closes)

    compute_for_symbol(indicators_db, RelativeStrengthIndex(), "TEST")

    last = indicators_db.execute(
        "SELECT rsi_14 FROM indicator_rsi WHERE symbol=? ORDER BY date DESC LIMIT 1",
        ("TEST",),
    ).fetchone()
    assert last and last[0] is not None
    assert last[0] < 1.0


def test_flat_then_mixed_centers_near_50(indicators_db):
    """Alternating up/down of equal size → RSI hovers near 50."""
    closes = []
    p = 100.0
    for i in range(80):
        p += 1.0 if i % 2 == 0 else -1.0
        closes.append(p)
    insert_bhavcopy(indicators_db, "TEST", closes)

    compute_for_symbol(indicators_db, RelativeStrengthIndex(), "TEST")

    last = indicators_db.execute(
        "SELECT rsi_14 FROM indicator_rsi WHERE symbol=? ORDER BY date DESC LIMIT 1",
        ("TEST",),
    ).fetchone()
    assert last and last[0] is not None
    assert 40.0 < last[0] < 60.0
