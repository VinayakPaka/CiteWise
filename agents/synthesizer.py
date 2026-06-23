"""Synthesizer agent — writes the final cited report from verified claims only.

Member 2 (output side). The Synthesizer receives the claims the Fact-Checker
marked ``supported`` and writes a structured ``FinalReport``. Citation
enforcement is applied *after* generation so the report can only cite sources
backed by a supported claim — the enforcement is by construction, not by trust.
"""
from __future__ import annotations

from config import CITEWISE_MODEL
from graph.state import ResearchState
from observability import log_event
from schemas.models import Claim, FinalReport, ReportSection
from tools.citation_validator import allowed_sources, enforce_citations

SYNTHESIZER_SYSTEM = (
    "You are the Synthesizer in a multi-agent research system. Write a concise, "
    "well-structured research brief that answers the user's question using ONLY "
    "the verified claims provided. Every statement in your report must be "
    "traceable to one of these claims and their sources. Do NOT introduce facts "
    "that are not in the verified claims. Cite sources by their URL. If the "
    "verified evidence is thin, say so honestly rather than padding the report."
)


def _format_claims(verified: list[Claim]) -> str:
    lines = []
    for i, c in enumerate(verified, 1):
        lines.append(
            f"[{i}] {c.text}\n    source_url: {c.source_url}\n"
            f"    snippet: {c.source_snippet}"
        )
    return "\n".join(lines)


def _llm():
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(model=CITEWISE_MODEL, max_tokens=4096)


def _insufficient_report(question: str) -> FinalReport:
    """Honest fallback when no claim survived verification."""
    return FinalReport(
        summary=(
            "No claims passed independent fact-checking for this question, so a "
            "reliable cited brief cannot be produced from the available evidence."
        ),
        sections=[
            ReportSection(
                heading="Insufficient verified evidence",
                content=(
                    f"CiteWise could not verify enough evidence to answer "
                    f"\u201c{question}\u201d. Try rephrasing the question, "
                    "narrowing its scope, or adding sources to the knowledge base."
                ),
            )
        ],
        citations=[],
    )


def synthesize_node(state: ResearchState) -> dict:
    """LangGraph node: verified_claims -> FinalReport (citations enforced)."""
    question = state["question"]
    verified = state.get("verified_claims", []) or []

    if not verified:
        report = _insufficient_report(question)
        log_event("synthesize", n_verified=0, note="insufficient evidence")
        return {"report": report}

    writer = _llm().with_structured_output(FinalReport)
    report: FinalReport = writer.invoke(
        [
            ("system", SYNTHESIZER_SYSTEM),
            (
                "human",
                f"Research question: {question}\n\n"
                f"Verified claims (the ONLY facts you may use):\n"
                f"{_format_claims(verified)}\n\n"
                f"Available source URLs you may cite:\n"
                f"{chr(10).join(allowed_sources(verified))}\n\n"
                "Write the final cited research brief.",
            ),
        ]
    )

    # Enforce: drop any citation not backed by a supported claim.
    report = enforce_citations(report, verified)
    log_event(
        "synthesize",
        n_verified=len(verified),
        n_sections=len(report.sections),
        n_citations=len(report.citations),
    )
    return {"report": report}
