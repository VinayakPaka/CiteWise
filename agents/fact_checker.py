"""Fact-Checker (Critic) agent — adversarially verifies each claim.

Member 2 (output side). For every ``Claim`` produced by the Researcher, the
Fact-Checker judges whether the claim's own ``source_snippet`` actually supports
``text``. Its objective is to *refute*, not to assist: this adversarial split is
the core reason CiteWise needs a separate agent from the Researcher (which is
biased toward defending what it found).

Output per claim: a ``Verdict`` with status ∈ {supported, unsupported,
needs_more_evidence}, a 0–1 confidence, and reasoning. A claim is added to
``verified_claims`` only when status == "supported" and confidence meets the
acceptance threshold.
"""
from __future__ import annotations

import os

from graph.state import ResearchState
from llm import get_llm
from observability import log_event
from schemas.models import Claim, Verdict

# Minimum confidence for a "supported" verdict to count as verified. Tunable via
# env so reviewers can tighten/loosen acceptance without code changes.
ACCEPT_CONFIDENCE = float(os.getenv("CITEWISE_ACCEPT_CONFIDENCE", "0.6"))

FACT_CHECKER_SYSTEM = (
    "You are the Fact-Checker in a multi-agent research system. You are an "
    "adversarial critic: your job is to TRY TO REFUTE each claim, not to defend "
    "it. You are given a claim and the exact source snippet it was drawn from.\n\n"
    "Judge ONLY whether the provided snippet logically supports the claim:\n"
    "  - 'supported': the snippet directly and unambiguously supports the claim.\n"
    "  - 'unsupported': the snippet contradicts the claim or does not support it.\n"
    "  - 'needs_more_evidence': the snippet is related but insufficient to "
    "confirm the claim on its own.\n\n"
    "Do not use outside knowledge to fill gaps the snippet leaves open — that is "
    "exactly the kind of unsupported leap you must catch. Give a calibrated "
    "confidence (0-1) and concise reasoning."
)


def _llm():
    return get_llm(max_tokens=1024)


def _verify_one(checker, claim: Claim) -> Verdict:
    try:
        verdict: Verdict = checker.invoke(
            [
                ("system", FACT_CHECKER_SYSTEM),
                (
                    "human",
                    f"Claim: {claim.text}\n\n"
                    f"Source URL: {claim.source_url}\n"
                    f"Source snippet:\n{claim.source_snippet}\n\n"
                    "Return your verdict. Set claim_text to the claim verbatim.",
                ),
            ]
        )
        # Guarantee claim_text matches the claim verbatim for downstream matching.
        if verdict.claim_text != claim.text:
            verdict = verdict.model_copy(update={"claim_text": claim.text})
        return verdict
    except Exception as exc:  # be robust: a failed check is treated as unverified
        log_event("fact_checker_error", claim=claim.text, error=str(exc))
        return Verdict(
            claim_text=claim.text,
            status="needs_more_evidence",
            confidence=0.0,
            reasoning=f"Verification failed to run: {exc}",
        )


def fact_check_node(state: ResearchState) -> dict:
    """LangGraph node: claims -> verdicts + verified_claims.

    Re-judges the *entire* accumulated claim list each pass (claims accumulate
    across retry loops via the state reducer), so verdicts always reflect all
    evidence gathered so far.
    """
    claims = state.get("claims", []) or []
    if not claims:
        log_event("fact_check", n_claims=0, note="no claims to verify")
        return {"verdicts": [], "verified_claims": []}

    checker = _llm().with_structured_output(Verdict)

    verdicts: list[Verdict] = []
    verified: list[Claim] = []
    for claim in claims:
        verdict = _verify_one(checker, claim)
        verdicts.append(verdict)
        accepted = (
            verdict.status == "supported" and verdict.confidence >= ACCEPT_CONFIDENCE
        )
        if accepted:
            verified.append(claim)
        log_event(
            "verdict",
            claim=claim.text,
            status=verdict.status,
            confidence=round(verdict.confidence, 3),
            accepted=accepted,
        )

    log_event(
        "fact_check_summary",
        n_claims=len(claims),
        n_supported=len(verified),
        n_rejected=len(claims) - len(verified),
        retry_count=state.get("retry_count", 0),
    )
    return {"verdicts": verdicts, "verified_claims": verified}
