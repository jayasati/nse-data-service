"""
F&O EOD bhavcopy in udiff format.

URL: https://nsearchives.nseindia.com/content/fo/
     BhavCopy_NSE_FO_0_0_0_<YYYYMMDD>_F_0000.csv.zip

NSE migrated F&O bhavcopy in 2023 from the legacy fo<DDMMMYYYY>bhav.csv.zip
(architecture §5.8 #55) to this new format. ~45,000 rows per trading day.

Lands as ZIP → unzip in memory → parse CSV → persist.
Includes run_for_date(d) for backfill via scripts/backfill.py.
"""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from .base import CsvCollector, Request, Row


log = logging.getLogger(__name__)


def _url_for(d: date) -> str:
    yyyymmdd = d.strftime("%Y%m%d")
    return (
        f"https://nsearchives.nseindia.com/content/fo/"
        f"BhavCopy_NSE_FO_0_0_0_{yyyymmdd}_F_0000.csv.zip"
    )


class BhavcopyFO(CsvCollector):
    name = "bhavcopy_fo"
    table = "raw_bhavcopy_fo"
    pk_cols = ("trade_date", "ticker", "expiry", "strike", "option_type")

    archive_root = Path("data/archive/bhavcopy_fo")
    response_type = "bytes"   # ZIP, not text

    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        d = (context or {}).get("for_date") or date.today()
        return [Request(
            path_or_url=_url_for(d),
            referer="https://www.nseindia.com/all-reports-derivatives",
            response_type="bytes",
            meta={"for_date": d.isoformat()},
        )]

    def normalize(self, data: bytes, request: Request) -> list[Row]:
        if not isinstance(data, bytes) or len(data) < 100:
            return []
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            log.warning("bhavcopy_fo: response not a valid ZIP, %d bytes", len(data))
            return []

        names = zf.namelist()
        if not names:
            return []
        with zf.open(names[0]) as f:
            content = f.read().decode("utf-8")

        # Archive the unzipped CSV
        for_date = (request.meta or {}).get("for_date")
        if for_date:
            try:
                d = datetime.fromisoformat(for_date).date()
                year_dir = self.archive_root / str(d.year)
                year_dir.mkdir(parents=True, exist_ok=True)
                (year_dir / names[0]).write_text(content)
            except Exception as e:
                log.warning("bhavcopy_fo archive failed: %s", e)

        reader = csv.DictReader(io.StringIO(content))
        rows: list[Row] = []
        for r in reader:
            ticker = (r.get("TckrSymb") or "").strip()
            if not ticker:
                continue
            expiry = (r.get("XpryDt") or "").strip()
            strike_raw = (r.get("StrkPric") or "0").strip()
            optn = (r.get("OptnTp") or "").strip()
            if not expiry:
                continue
            # NSE writes "" for futures' option_type; substitute 'XX' so the
            # composite PK is NOT NULL across the board.
            option_type = optn if optn in ("CE", "PE") else "XX"

            rows.append({
                "trade_date":      _none_if_dash(r.get("TradDt")),
                "business_date":   _none_if_dash(r.get("BizDt")),
                "segment":         _none_if_dash(r.get("Sgmt")),
                "source":          _none_if_dash(r.get("Src")),
                "instrument_type": _none_if_dash(r.get("FinInstrmTp")),
                "instrument_id":   _none_if_dash(r.get("FinInstrmId")),
                "isin":            _none_if_dash(r.get("ISIN")),
                "ticker":          ticker,
                "series":          _none_if_dash(r.get("SctySrs")),
                "expiry":          expiry,
                "actual_expiry":   _none_if_dash(r.get("FininstrmActlXpryDt")),
                "strike":          _f(strike_raw),
                "option_type":     option_type,
                "instrument_name": _none_if_dash(r.get("FinInstrmNm")),
                "open":            _f(r.get("OpnPric")),
                "high":            _f(r.get("HghPric")),
                "low":             _f(r.get("LwPric")),
                "close":           _f(r.get("ClsPric")),
                "last":            _f(r.get("LastPric")),
                "prev_close":      _f(r.get("PrvsClsgPric")),
                "underlying_price": _f(r.get("UndrlygPric")),
                "settle":          _f(r.get("SttlmPric")),
                "open_interest":   _i(r.get("OpnIntrst")),
                "change_in_oi":    _i(r.get("ChngInOpnIntrst")),
                "total_volume":    _i(r.get("TtlTradgVol")),
                "total_value":     _f(r.get("TtlTrfVal")),
                "total_trades":    _i(r.get("TtlNbOfTxsExctd")),
                "session_id":      _none_if_dash(r.get("SsnId")),
                "new_lot_size":    _i(r.get("NewBrdLotQty")),
            })
        return rows

    def run_for_date(self, session, db, d: date):
        """Backfill hook — called by scripts/backfill.py."""
        return self.run(session, db, context={"for_date": d})


def _f(v):
    if v is None or v == "" or v == "-":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v):
    if v is None or v == "" or v == "-":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _none_if_dash(v):
    if v is None:
        return None
    s = str(v).strip()
    return None if s in ("", "-") else s