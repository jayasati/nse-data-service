"""Tests for the participant-OI parser (live NSE format)."""
from __future__ import annotations

from nse_data.collectors.base import Request
from nse_data.collectors.participant_oi import ParticipantOi

_SAMPLE = (
    '""Participant wise Open Interest (no. of contracts) ... as on Jun 19, 2026"",,,,,,,,,,,,,,\n'
    "Client Type,Future Index Long,Future Index Short,Future Stock Long,Future Stock Short ,"
    "Option Index Call Long,Option Index Put Long,Option Index Call Short,Option Index Put Short,"
    "Option Stock Call Long,Option Stock Put Long,Option Stock Call Short,Option Stock Put Short,"
    "Total Long Contracts ,Total Short Contracts\n"
    "Client,238409,76124,3299193,282940,3119888,2416378,2974782,3193601,2663304,863992,1441716,1227636,12601164,9196799\n"
    "DII,84100,11589,459287,4594205,12434,22666,430,135,816,36008,421905,17617,615311,5045881\n"
    "FII,39587,266010,4108754,3474848,640164,1157965,940484,528275,302448,430391,476136,294863,6679309,5980616\n"
    "Pro,46731,55104,1027166,542407,1216287,1043549,1073078,918547,1273667,1216947,1900478,1007222,5824347,5496836\n"
    "TOTAL,408827,408827,8894400,8894400,4988774,4640558,4988774,4640558,4240235,2547338,4240235,2547338,25720132,25720132\n"
)


def test_parses_four_participants_skips_header_and_total():
    req = Request(path_or_url="x", response_type="text", meta={"date": "2026-06-19"})
    rows = ParticipantOi().normalize(_SAMPLE, req)
    assert {r["client_type"] for r in rows} == {"Client", "DII", "FII", "Pro"}   # no header/TOTAL
    fii = next(r for r in rows if r["client_type"] == "FII")
    assert fii["fut_idx_long"] == 39587 and fii["fut_idx_short"] == 266010
    assert fii["opt_idx_call_long"] == 640164 and fii["total_long"] == 6679309
    assert all(r["report_date"] == "2026-06-19" for r in rows)
