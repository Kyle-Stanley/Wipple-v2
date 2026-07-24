"""Narrow text evidence for schema choices that math cannot identify.

Accounting identities remain authoritative whenever they distinguish a
schedule.  Text is consulted only for the sparse revenue/cost/profit triangle,
which is algebraically identical to contract value/estimated cost/profit.

The extractor copies printed titles and headers; this module interprets those
strings deterministically.  It never maps arbitrary columns and never asks a
model to invent an accounting meaning.
"""

from __future__ import annotations

import re


def _norm(value: str) -> str:
    value = re.sub(r"[^a-z0-9% ]+", " ", str(value).lower())
    return re.sub(r"\s+", " ", value).strip()


_CC_TITLE_PHRASES = (
    "schedule of completed contracts",
    "completed contracts schedule",
    "contracts completed",
    "completed contracts",
)

_WIP_TITLE_PHRASES = (
    "work in progress",
    "work in process",
    "schedule of contracts in progress",
    "contracts in progress",
    "contracts in process",
    "uncompleted contracts",
)

# Only schema-specific header language belongs here.  Shared language such as
# "gross profit", "contract price", and "billed to date" intentionally casts
# no vote.
_CC_HEADERS = {
    "total contract revenue",
    "total revenues earned",
    "total revenue earned",
    "revenues earned prior years",
    "revenue earned prior years",
    "prior years revenue",
    "revenues earned current year",
    "revenue earned current year",
    "current year revenue",
    "costs incurred prior years",
    "cost incurred prior years",
    "prior years cost",
    "costs incurred current year",
    "cost incurred current year",
    "current year cost",
    "gross profit prior years",
    "prior years gross profit",
    "gross profit current year",
    "current year gross profit",
}

_WIP_HEADERS = {
    "original contract",
    "original contract amount",
    "change orders",
    "t m change orders",
    "tm change orders",
    "total contract",
    "contract value",
    "projected costs",
    "projected cost",
    "estimated total cost",
    "total estimated cost",
    "cost to complete",
    "costs to complete",
    "% complete",
    "percent complete",
    "under billings",
    "underbillings",
    "over billings",
    "overbillings",
    "costs in excess of billings",
    "billings in excess of costs",
}


def resolve_schema_text(headers=None, title_texts=None) -> dict:
    """Return deterministic WIP/CC evidence from exact printed text.

    Titles are the strongest evidence.  Headers are used when no recognized
    title is present.  Opposing votes remain unresolved instead of being
    converted into a confidence score.
    """
    titles = [_norm(value) for value in (title_texts or []) if _norm(value)]
    normalized_headers = [
        _norm(value) for value in (headers or []) if _norm(value)
    ]

    cc_titles = [
        title for title in titles
        if any(phrase in title for phrase in _CC_TITLE_PHRASES)
    ]
    wip_titles = [
        title for title in titles
        if any(phrase in title for phrase in _WIP_TITLE_PHRASES)
    ]
    cc_headers = [
        header for header in normalized_headers if header in _CC_HEADERS
    ]
    wip_headers = [
        header for header in normalized_headers if header in _WIP_HEADERS
    ]

    if cc_titles and not wip_titles:
        chosen, source = "cc", "title"
    elif wip_titles and not cc_titles:
        chosen, source = "wip", "title"
    elif cc_titles or wip_titles:
        chosen, source = None, "conflicting_titles"
    elif cc_headers and not wip_headers:
        chosen, source = "cc", "headers"
    elif wip_headers and not cc_headers:
        chosen, source = "wip", "headers"
    elif cc_headers or wip_headers:
        chosen, source = None, "conflicting_headers"
    else:
        chosen, source = None, "none"

    return {
        "chosen": chosen,
        "source": source,
        "cc_titles": cc_titles,
        "wip_titles": wip_titles,
        "cc_headers": cc_headers,
        "wip_headers": wip_headers,
    }
