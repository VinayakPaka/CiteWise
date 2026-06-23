"""Researcher agent — gathers evidence and extracts sourced claims.

For each sub-question it runs web search (Tavily) and RAG retrieval (Chroma),
then asks the LLM to extract atomic, individually-sourced claims. Member 1
(Vinayak Paka). Output: a list of ``Claim`` objects appended to shared state.
"""
from __future__ import annotations

import os

from pydantic import BaseModel

from graph.state import ResearchState
from llm import get_llm
from schemas.models import Claim
from tools.rag_store import retrieve, seed_sample_corpus
from tools.web_search import web_search


class _ExtractedClaims(BaseModel):
    """Wrapper so the LLM can return a list of claims via structured output."""

    claims: list[Claim]


# Claim budget. The total is sized so every sub-question gets its per-sub-question
# share instead of the first few starving the rest (which hurts completeness).
# Fact-checking these runs in parallel, so a larger budget stays fast. Tune in .env.
MAX_CLAIMS_PER_SUBQ = int(os.getenv("CITEWISE_MAX_CLAIMS_PER_SUBQ", "5"))
MAX_TOTAL_NEW_CLAIMS = int(os.getenv("CITEWISE_MAX_CLAIMS", "24"))


RESEARCHER_SYSTEM = (
    "You are the Researcher in a multi-agent research system. You are given a "
    "sub-question and a set of sources (web results and reference documents). "
    "Extract only atomic factual claims that are directly supported by the "
    "provided sources. For each claim, copy the exact supporting snippet and "
    "the source URL it came from. Never invent sources, and never state a claim "
    "the sources do not support. Tie every claim to the given sub-question."
)


def _format_sources(web_results: list[dict], docs) -> str:
    lines: list[str] = []
    for r in web_results:
        lines.append(f"[web] {r['url']}\n{r['content']}")
    for d in docs:
        src = d.metadata.get("source", "unknown")
        lines.append(f"[kb] {src}\n{d.page_content}")
    return "\n\n".join(lines) if lines else "(no sources found)"


def _llm():
    return get_llm(max_tokens=4096)


def _claim_key(claim: Claim) -> tuple[str, str]:
    """De-duplication identity: normalised claim text + source URL."""
    return (claim.text.strip().lower(), claim.source_url.strip().lower())


def research_node(state: ResearchState) -> dict:
    """LangGraph node: ResearchPlan -> new, de-duplicated list[Claim].

    First pass: research every sub-question. On a retry (the Fact-Checker loops
    back when claims fail verification), research only the sub-questions that
    still lack a verified claim and search more deeply, so the loop gathers
    genuinely new evidence instead of re-finding the same claims. Newly found
    claims are de-duplicated against everything gathered so far before being
    appended via the shared state's ``operator.add`` reducer.
    """
    plan = state["plan"]
    if plan is None:
        return {"claims": []}

    seed_sample_corpus()  # ensure the RAG store has reference content

    retry_count = state.get("retry_count", 0)
    existing_claims = state.get("claims", []) or []
    verified = state.get("verified_claims", []) or []

    # A sub-question is "covered" once it has at least one verified claim.
    covered = {c.sub_question for c in verified}
    if retry_count > 0:
        targets = [sq for sq in plan.sub_questions if sq not in covered]
        search_depth = "advanced"  # dig deeper on retries
    else:
        targets = list(plan.sub_questions)
        search_depth = "basic"

    if not targets:  # every sub-question already has verified evidence
        return {"claims": []}

    seen = {_claim_key(c) for c in existing_claims}
    extractor = _llm().with_structured_output(_ExtractedClaims)

    new_claims: list[Claim] = []
    for sub_q in targets:
        if len(new_claims) >= MAX_TOTAL_NEW_CLAIMS:
            break
        sources = _format_sources(
            web_search(sub_q, search_depth=search_depth), retrieve(sub_q)
        )
        result = extractor.invoke(
            [
                ("system", RESEARCHER_SYSTEM),
                (
                    "human",
                    f"Sub-question: {sub_q}\n\nSources:\n{sources}\n\n"
                    "Extract the supported claims with their source_url and "
                    "source_snippet.",
                ),
            ]
        )
        per_subq = 0
        for claim in result.claims:
            if per_subq >= MAX_CLAIMS_PER_SUBQ or len(new_claims) >= MAX_TOTAL_NEW_CLAIMS:
                break
            # Pin the sub-question so "covered" tracking is reliable downstream.
            claim = claim.model_copy(update={"sub_question": sub_q})
            key = _claim_key(claim)
            if key not in seen:
                seen.add(key)
                new_claims.append(claim)
                per_subq += 1

    return {"claims": new_claims}
