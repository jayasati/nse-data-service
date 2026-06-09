"""Unit tests for the XBRL financial parser (deterministic, inline fixture)."""
from __future__ import annotations

from nse_data.parsers.xbrl_financials import parse_xbrl

# Minimal INDAS-shaped XBRL: current quarter (Q_CUR), year-ago quarter (Q_OLD),
# and a dimensioned context (Q_DIM) that must be ignored. Values in rupees.
_XBRL = """<?xml version="1.0"?>
<xbrl xmlns:xbrli="urn:xbrli" xmlns:f="urn:in-bse-fin" xmlns:xbrldi="urn:xbrldi">
  <xbrli:context id="Q_CUR">
    <xbrli:period><xbrli:startDate>2026-01-01</xbrli:startDate>
      <xbrli:endDate>2026-03-31</xbrli:endDate></xbrli:period>
  </xbrli:context>
  <xbrli:context id="Q_OLD">
    <xbrli:period><xbrli:startDate>2025-01-01</xbrli:startDate>
      <xbrli:endDate>2025-03-31</xbrli:endDate></xbrli:period>
  </xbrli:context>
  <xbrli:context id="Q_DIM">
    <xbrli:entity><xbrli:segment>
      <xbrldi:explicitMember dimension="f:SegAxis">f:SomeMember</xbrldi:explicitMember>
    </xbrli:segment></xbrli:entity>
    <xbrli:period><xbrli:startDate>2026-01-01</xbrli:startDate>
      <xbrli:endDate>2026-03-31</xbrli:endDate></xbrli:period>
  </xbrli:context>
  <xbrli:context id="YTD">
    <xbrli:period><xbrli:startDate>2025-04-01</xbrli:startDate>
      <xbrli:endDate>2026-03-31</xbrli:endDate></xbrli:period>
  </xbrli:context>

  <f:NatureOfReportStandaloneConsolidated contextRef="Q_CUR">Standalone</f:NatureOfReportStandaloneConsolidated>

  <f:RevenueFromOperations contextRef="Q_CUR">2549720000</f:RevenueFromOperations>
  <f:RevenueFromOperations contextRef="Q_OLD">2000000000</f:RevenueFromOperations>
  <f:RevenueFromOperations contextRef="Q_DIM">9999</f:RevenueFromOperations>
  <f:OtherIncome contextRef="Q_CUR">338500000</f:OtherIncome>
  <f:Income contextRef="Q_CUR">2888220000</f:Income>
  <f:Expenses contextRef="Q_CUR">2210270000</f:Expenses>
  <f:ProfitBeforeTax contextRef="Q_CUR">373300000</f:ProfitBeforeTax>
  <f:TaxExpense contextRef="Q_CUR">99630000</f:TaxExpense>
  <f:ProfitLossForPeriod contextRef="Q_CUR">273670000</f:ProfitLossForPeriod>
  <f:ComprehensiveIncomeForThePeriod contextRef="Q_CUR">275960000</f:ComprehensiveIncomeForThePeriod>
  <f:BasicEarningsLossPerShareFromContinuingOperations contextRef="Q_CUR">5.0</f:BasicEarningsLossPerShareFromContinuingOperations>
  <f:BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations contextRef="Q_CUR">4.2</f:BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations>
  <f:DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations contextRef="Q_CUR">4.1</f:DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations>
</xbrl>"""


def test_parse_xbrl_current_quarter_and_scaling():
    r = parse_xbrl(_XBRL)
    assert r is not None
    assert r["scope"] == "standalone"
    assert r["period_ending"] == "2026-03-31"
    f = r["fields"]
    # rupees -> crore (÷1e7)
    assert f["revenue_cr"] == 254.97
    assert f["other_income_cr"] == 33.85
    assert f["total_income_cr"] == 288.82
    assert f["pat_cr"] == 27.37
    # current quarter chosen, NOT the year-ago (200.0) or dimensioned (tiny)
    assert f["revenue_cr"] != 200.0


def test_eps_prefers_continuing_and_discontinued():
    f = parse_xbrl(_XBRL)["fields"]
    assert f["eps_basic"] == 4.2          # C&D preferred over continuing-only 5.0
    assert f["eps_diluted"] == 4.1


def test_consolidated_scope_detected():
    xml = _XBRL.replace(">Standalone<", ">Consolidated<")
    assert parse_xbrl(xml)["scope"] == "consolidated"


def test_garbage_returns_none():
    assert parse_xbrl(b"not xml at all <<<") is None
    assert parse_xbrl("<xbrl xmlns:x='u'></xbrl>") is None   # no quarter context
