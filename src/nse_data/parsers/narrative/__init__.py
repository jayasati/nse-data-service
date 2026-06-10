"""Press-release / investor-deck text ingestion (SECTOR_RESULT_PLAYBOOK.md §4 P7).

The P&L-only extractor cannot see the signals that decide several sectors:
guidance (IT), volumes (FMCG/Auto), order book (Capital Goods), FDA status
(Pharma), dividend. Those live in the result *narrative*, not the financial
tables. This package will extract them as structured fields that the sector
specs (``fundamentals.sectors``) consume alongside the P&L growth dict.

Two layers, reconciled (see ``llm_narrative``):

    from nse_data.parsers.narrative import extract_narrative, NarrativeFields
    from nse_data.parsers.narrative import narrative_fields          # LLM-first

``extract_narrative`` is the pure-regex read (conservative — None unless
plainly stated; offline). ``narrative_fields`` layers gpt-4o on top: LLM wins
categoricals, the regex wins numeric unit disputes, regex fills LLM gaps, and
the whole thing degrades to regex when no LLM is configured.
"""
from .llm_narrative import extract_narrative_vision, narrative_fields
from .narrative_extractor import NarrativeFields, extract_narrative

__all__ = [
    "NarrativeFields",
    "extract_narrative",
    "extract_narrative_vision",
    "narrative_fields",
]
