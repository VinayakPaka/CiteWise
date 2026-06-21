"""Pydantic models — the structured contract for every agent handoff.

These schemas are the *joint* foundation of CiteWise: the Researcher produces
``Claim`` objects, the Fact-Checker produces ``Verdict`` objects, and the
Synthesizer produces a ``FinalReport``. Using schemas (not free text) on every
handoff is what makes the graph reliable and debuggable.
"""
from typing import Literal

from pydantic import BaseModel, Field


class ResearchPlan(BaseModel):
    """Output of the Planner: how the question is broken down."""

    sub_questions: list[str] = Field(
        ..., description="3–6 specific, independently answerable sub-questions."
    )
    rationale: str = Field(
        ..., description="Why the question was decomposed this way."
    )


class Claim(BaseModel):
    """A single factual claim with the source that supports it.

    Produced by the Researcher; consumed by the Fact-Checker and Synthesizer.
    A claim without a resolvable source must be dropped.
    """

    text: str = Field(..., description="A single, atomic factual claim.")
    source_url: str = Field(..., description="URL of the supporting source.")
    source_snippet: str = Field(
        ..., description="Exact snippet from the source that supports the claim."
    )
    sub_question: str = Field(
        ..., description="Which sub-question this claim helps answer."
    )


class Verdict(BaseModel):
    """Output of the Fact-Checker for one claim."""

    claim_text: str = Field(..., description="The claim being judged (verbatim).")
    status: Literal["supported", "unsupported", "needs_more_evidence"] = Field(
        ..., description="Whether the source actually supports the claim."
    )
    confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Confidence in the verdict, 0–1."
    )
    reasoning: str = Field(
        ..., description="Why this verdict was reached (adversarial check)."
    )


class ReportSection(BaseModel):
    """One section of the final report."""

    heading: str
    content: str


class FinalReport(BaseModel):
    """Output of the Synthesizer: the cited brief shown to the human for approval."""

    summary: str = Field(..., description="Executive summary of the findings.")
    sections: list[ReportSection] = Field(
        ..., description="Body of the report, grounded only in verified claims."
    )
    citations: list[str] = Field(
        ..., description="Deduplicated list of source URLs cited in the report."
    )
