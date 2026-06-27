"""Citation enforcement.

Member 2. The Synthesizer is instructed to cite only verified sources, but
instructions are not a guarantee. This module *enforces* that guarantee by
construction: it rebuilds the report's citation list from the set of source URLs
that belong to ``supported`` claims and strips any URL the model may have
invented or pulled from an unsupported claim.

Excluding unsupported citations at the data level is stronger than asking the LLM
nicely — it is the project's "citation enforcement" guardrail.
"""
from __future__ import annotations

from schemas.models import Claim, FinalReport


def allowed_sources(verified_claims: list[Claim]) -> list[str]:
    """Deduplicated, order-preserving list of source URLs from verified claims."""
    seen: set[str] = set()
    ordered: list[str] = []
    for c in verified_claims:
        if c.source_url and c.source_url not in seen:
            seen.add(c.source_url)
            ordered.append(c.source_url)
    return ordered


def enforce_citations(report: FinalReport, verified_claims: list[Claim]) -> FinalReport:
    """Return a copy of ``report`` whose citations == verified source URLs.

    The citation list is normalised to exactly the allowed sources, in the fixed
    ``allowed_sources`` order. Any citation the model invented or pulled from an
    unsupported claim is dropped by construction. Using this canonical order (the
    same order the Synthesizer is given for its inline [n] markers) keeps the
    rendered, position-numbered Citations list aligned with the inline markers in
    the prose.
    """
    return report.model_copy(
        update={"citations": allowed_sources(verified_claims)}
    )
