"""
Registry of every indicator the nightly compute job runs.

Explicit list, not dynamic discovery — we want every new indicator to be a
visible one-line addition here so reviewers can see what's in the pipeline.

To add an indicator: write its file (a subclass of Indicator), import it
below, and append an instance to INDICATORS.
"""

from __future__ import annotations

from .base import Indicator
from .eod_full import EodFullSet
from .momentum.macd import MovingAverageConvergenceDivergence
from .momentum.macd_intraday import MacdIntraday
from .momentum.rsi import RelativeStrengthIndex
from .momentum.rsi_intraday import RsiIntraday
from .relative_strength import RelativeStrengthLine
from .trend.cpr import CentralPivotRange
from .trend.ema import EodEma
from .trend.ema_intraday import EmaIntraday
from .trend.market_structure import MarketStructure, MarketStructureIntraday
from .trend.sma import SimpleMovingAverage
from .trend.supertrend_intraday import SupertrendIntraday
from .volatility.atr_series import AtrIntraday, AtrSeries
from .volatility.bollinger_intraday import BollingerIntraday
from .volume.open_interest import OpenInterestEod
from .volume.relative_volume_intraday import RelativeVolumeIntraday
from .volume.volume_delta import VolumeDelta
from .volume.vwap_intraday import VwapIntraday

INDICATORS: tuple[Indicator, ...] = (
    # EOD (daily, off raw_bhavcopy_cm) — see indicators/compute.py
    SimpleMovingAverage(),
    EodEma(),
    RelativeStrengthIndex(),
    MovingAverageConvergenceDivergence(),
    EodFullSet(),
    AtrSeries(),
    CentralPivotRange(),
    MarketStructure(),
    RelativeStrengthLine(),
    OpenInterestEod(),
    # Intraday (5-min, off raw_intraday_candles + live feed) — recomputed
    # every minute during market hours by the live scheduler.
    RsiIntraday(),
    MacdIntraday(),
    VwapIntraday(),
    SupertrendIntraday(),
    VolumeDelta(),
    EmaIntraday(),
    AtrIntraday(),
    BollingerIntraday(),
    RelativeVolumeIntraday(),
    MarketStructureIntraday(),
)
