"""Unit tests for promoter-pledge extraction in the SHP XBRL parser. Synthetic XBRL
(no network) pinning: the pledge % is read at the promoter-aggregate context, encoded
DIRECTLY as a percentage (not the holding fraction/percent scale), and absent when the
filing reports no pledge.
"""
from __future__ import annotations

from nse_data.parsers.shp_xbrl import parse_shp

_NS = 'xmlns:shp="http://x" xmlns:xbrli="http://www.xbrl.org/2003/instance"'


def _ctx(cid, member):
    return (f'<xbrli:context id="{cid}"><xbrli:entity/><xbrli:scenario>'
            f'<xbrli:explicitMember dimension="shp:CategoryOfShareholdersAxis">'
            f'shp:{member}</xbrli:explicitMember></xbrli:scenario></xbrli:context>')


def _doc(prom, pub, pledge=None, *, fraction=False):
    k = 0.01 if fraction else 1.0
    pe = "ShareholdingAsAPercentageOfTotalNumberOfShares"
    body = [
        _ctx("PROM", "ShareholdingOfPromoterAndPromoterGroupMember"),
        _ctx("PUB", "PublicShareholdingMember"),
        f'<shp:{pe} contextRef="PROM">{prom * k}</shp:{pe}>',
        f'<shp:{pe} contextRef="PUB">{pub * k}</shp:{pe}>',
    ]
    if pledge is not None:
        body.append(f'<shp:EncumberedShareUnderPledgedAsPercentageOfTotalNumberOfShares '
                    f'contextRef="PROM">{pledge}</shp:EncumberedShareUnderPledgedAsPercentageOfTotalNumberOfShares>')
    return f'<xbrl {_NS}>' + "".join(body) + '</xbrl>'


def test_pledge_extracted_at_promoter_context():
    f = parse_shp(_doc(60.0, 40.0, pledge=55.0))
    assert f["promoter_pct"] == 60.0 and f["public_pct"] == 40.0
    assert f["promoter_pledge_pct"] == 55.0


def test_pledge_not_scaled_when_holdings_are_fractions():
    # holdings encoded as fractions (0.60/0.40 → scaled ×100) but pledge stays percent
    f = parse_shp(_doc(60.0, 40.0, pledge=1.0, fraction=True))
    assert f["promoter_pct"] == 60.0            # holding scaled up
    assert f["promoter_pledge_pct"] == 1.0       # pledge NOT scaled


def test_no_pledge_element_absent():
    f = parse_shp(_doc(50.0, 50.0))
    assert "promoter_pledge_pct" not in f


def test_pledge_clamped_0_100():
    f = parse_shp(_doc(60.0, 40.0, pledge=120.0))
    assert f["promoter_pledge_pct"] == 100.0
