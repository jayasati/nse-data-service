"""
Registry of every indicator the nightly compute job runs.

Explicit list, not dynamic discovery — we want every new indicator to be a
visible one-line addition here so reviewers can see what's in the pipeline.

To add an indicator: write its file (a subclass of Indicator), import it
below, and append an instance to INDICATORS.
"""

from __future__ import annotations

from .base import Indicator
from .momentum.macd import MovingAverageConvergenceDivergence
from .momentum.rsi import RelativeStrengthIndex
from .trend.sma import SimpleMovingAverage

INDICATORS: tuple[Indicator, ...] = (
    SimpleMovingAverage(),
    RelativeStrengthIndex(),
    MovingAverageConvergenceDivergence(),
)
