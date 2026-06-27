"""Researcher agent — gathers evidence and extracts sourced claims.

For each sub-question it runs web search (Tavily), then asks the LLM to extract
atomic, individually-sourced claims tied to the source they came from. Member 1
(Vinayak Paka). Output: a list of ``Claim`` objects appended to shared state.
"""
from __future__ import annotations

import os

from pydantic import BaseModel

from config import evidence_is_thin
from graph.state import ResearchState
from llm import get_structured_llm
from observability import log_event
from schemas.models import Claim
from tools.source_quality import is_citable
from tools.web_search import web_search


class _ExtractedClaims(BaseModel):
    """Wrapper so the LLM can return a list of claims via structured output."""

    claims: list[Claim]


# Claim budget. The total is sized so every sub-question gets its per-sub-question
# share instead of the first few starving the rest (which hurts completeness).
# Fact-checking these runs in parallel, so a larger budget stays fast. Tune in .env.
MAX_CLAIMS_PER_SUBQ = int(os.getenv("CITEWISE_MAX_CLAIMS_PER_SUBQ", "5"))
MAX_TOTAL_NEW_CLAIMS = int(os.getenv("CITEWISE_MAX_CLAIMS", "24"))

# How many web results to keep per sub-question (after source-quality filtering and
# authority ranking). Higher = broader, more comprehensive evidence per topic, at
# the cost of more fact-check calls. Topics with sparse first searches benefit most.
WEB_RESULTS = int(os.getenv("CITEWISE_WEB_RESULTS", "6"))


RESEARCHER_SYSTEM = (
    "You are the Researcher in a multi-agent research system. You are given a "
    "sub-question and a set of web search results. "
    "Extract only atomic factual claims that are directly supported by the "
    "provided sources. For each claim, copy the exact supporting snippet and "
    "the source URL it came from. Never invent sources, and never state a claim "
    "the sources do not support. Tie every claim to the given sub-question.\n\n"
    "RELEVANCE: If a provided source is not actually about the sub-question's "
    "topic, ignore it completely — do not extract any claim from an off-topic "
    "source, even if it states checkable facts. Only draw claims from sources "
    "whose content genuinely addresses the sub-question."
)


# Trim each source to keep prompt (input-token) cost down — the key facts are
# almost always near the start of a result, and the URL is kept for citation.
MAX_SOURCE_CHARS = int(os.getenv("CITEWISE_MAX_SOURCE_CHARS", "600"))


def _trim(text: str) -> str:
    text = (text or "").strip()
    return text[:MAX_SOURCE_CHARS] + ("…" if len(text) > MAX_SOURCE_CHARS else "")


def _format_sources(web_results: list[dict]) -> str:
    """Render only citable web sources for the LLM.

    ``web_search`` already drops non-citable domains and ranks by authority, but
    we re-check each result with the same gate as a guard. A claim the LLM could
    only draw from a dropped source has no citable home and would be stripped
    downstream anyway — better to never offer it.
    """
    lines: list[str] = []
    for r in web_results:
        if not is_citable(r["url"]):
            continue
        lines.append(f"[web] {r['url']}\n{_trim(r['content'])}")
    return "\n\n".join(lines) if lines else "(no sources found)"


def _claim_key(claim: Claim) -> tuple[str, str]:
    """De-duplication identity: normalised claim text + source URL."""
    return (claim.text.strip().lower(), claim.source_url.strip().lower())


def research_node(state: ResearchState) -> dict:
    """LangGraph node: ResearchPlan -> new, de-duplicated list[Claim].

    First pass: research every sub-question. On a retry the Fact-Checker has
    looped back, and how we search depends on *why*:
      - evidence is thin overall (too few verified claims/sources) -> re-research
        EVERY sub-question at depth to broaden the evidence base, not just the
        uncovered ones (this is what rescues sparse topics like a health question
        whose first search returned a single secondary source);
      - otherwise -> only fill the sub-questions that still lack a verified claim.
    Newly found claims are de-duplicated against everything gathered so far before
    being appended via the shared state's ``operator.add`` reducer.
    """
    plan = state["plan"]
    if plan is None:
        return {"claims": []}

    retry_count = state.get("retry_count", 0)
    existing_claims = state.get("claims", []) or []
    verified = state.get("verified_claims", []) or []

    # A sub-question is "covered" once it has at least one verified claim.
    covered = {c.sub_question for c in verified}
    if retry_count == 0:
        targets = list(plan.sub_questions)
        search_depth = "basic"
    else:
        search_depth = "advanced"  # dig deeper on retries
        if evidence_is_thin(verified):
            # Broaden everywhere to gather more distinct sources; dedup stops us
            # from re-adding claims we already have.
            targets = list(plan.sub_questions)
        else:
            targets = [sq for sq in plan.sub_questions if sq not in covered]

    if not targets:  # every sub-question already has verified evidence
        return {"claims": []}

    seen = {_claim_key(c) for c in existing_claims}
    # Modest token cap: a claim list never needs much, and a smaller budget limits
    # the blast radius if a weak model starts repeating before the penalty kicks in.
    extractor = get_structured_llm(_ExtractedClaims, max_tokens=2048)

    new_claims: list[Claim] = []
    for sub_q in targets:
        if len(new_claims) >= MAX_TOTAL_NEW_CLAIMS:
            break
        sources = _format_sources(
            web_search(sub_q, max_results=WEB_RESULTS, search_depth=search_depth)
        )
        try:
            result = extractor.invoke(
                [
                    ("system", RESEARCHER_SYSTEM),
                    (
                        "human",
                        f"Sub-question: {sub_q}\n\nSources:\n{sources}\n\n"
                        f"Extract up to {MAX_CLAIMS_PER_SUBQ} DISTINCT supported "
                        "claims, each with its source_url and source_snippet. Every "
                        "claim must be different — never repeat a claim.",
                    ),
                ]
            )
        except Exception as exc:
            # A weak model can still emit malformed/looping tool-call JSON
            # ("tool_use_failed"), or hit a rate limit. Skip this sub-question's
            # claims and keep the run alive rather than failing the whole research.
            log_event("researcher_extract_error", sub_question=sub_q, error=str(exc)[:200])
            continue

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
