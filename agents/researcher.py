"""Researcher agent — gathers evidence and extracts sourced claims.

For each sub-question it runs web search (Tavily) and RAG retrieval (Chroma),
then asks the LLM to extract atomic, individually-sourced claims. Member 1
(Vinayak Paka). Output: a list of ``Claim`` objects appended to shared state.
"""
from __future__ import annotations

from pydantic import BaseModel

from config import CITEWISE_MODEL
from graph.state import ResearchState
from schemas.models import Claim
from tools.rag_store import retrieve, seed_sample_corpus
from tools.web_search import web_search


class _ExtractedClaims(BaseModel):
    """Wrapper so the LLM can return a list of claims via structured output."""

    claims: list[Claim]


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
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(model=CITEWISE_MODEL, max_tokens=4096)


def research_node(state: ResearchState) -> dict:
    """LangGraph node: ResearchPlan -> new list[Claim] (appended to state).

    Returns only the *newly found* claims; the shared state's ``operator.add``
    reducer appends them to any claims gathered in previous loop iterations.
    """
    plan = state["plan"]
    if plan is None:
        return {"claims": []}

    seed_sample_corpus()  # ensure the RAG store has reference content
    extractor = _llm().with_structured_output(_ExtractedClaims)

    new_claims: list[Claim] = []
    for sub_q in plan.sub_questions:
        sources = _format_sources(web_search(sub_q), retrieve(sub_q))
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
        new_claims.extend(result.claims)

    return {"claims": new_claims}
