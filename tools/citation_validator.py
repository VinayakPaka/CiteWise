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
    """Return a copy of ``report`` whose citations ⊆ verified source URLs.

    Any citation not backed by a supported claim is dropped. The citation list is
    then normalised to exactly the allowed sources that the report draws on.
    """
    allowed = set(allowed_sources(verified_claims))

    # Keep only model-provided citations that are actually backed by evidence,
    # preserving the model's ordering, then append any allowed source it missed.
    kept = [c for c in report.citations if c in allowed]
    for url in allowed_sources(verified_claims):
        if url not in kept:
            kept.append(url)

    return report.model_copy(update={"citations": kept})
