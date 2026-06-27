"""Pydantic models — the structured contract for every agent handoff.

These schemas are the *joint* foundation of CiteWise: the Researcher produces
``Claim`` objects, the Fact-Checker produces ``Verdict`` objects, and the
Synthesizer produces a ``FinalReport``. Using schemas (not free text) on every
handoff is what makes the graph reliable and debuggable.
"""
from typing import Literal, Optional

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


class KeyFigure(BaseModel):
    """One headline statistic for the at-a-glance band, drawn from a verified claim."""

    value: str = Field(
        ..., description="Short headline number copied from a claim, e.g. '-90%', "
        "'$0.044/kWh', '5-7%', '92%'."
    )
    label: str = Field(..., description="2-5 word description of what the number measures.")
    source_index: int = Field(
        ..., description="1-based index into the report's citations list that backs this figure."
    )


class ReportChart(BaseModel):
    """An optional chart built from comparable numbers in the verified claims.

    Deliberately a single, flat series — ``categories`` and ``values`` are parallel
    arrays — so even a smaller model can emit it reliably as structured output.
    """

    kind: Literal["bar", "line"] = Field(
        ..., description="'bar' for a comparison across categories, 'line' for a trend over time."
    )
    title: str = Field(..., description="Short chart title.")
    y_label: str = Field(
        "", description="Y-axis label including units, e.g. 'USD/kWh' or '% reduction'."
    )
    categories: list[str] = Field(
        ..., description="2-8 x-axis labels, e.g. ['2010','2015','2020'] or "
        "['Utility','Commercial','Residential']."
    )
    values: list[float] = Field(
        ..., description="Numeric values parallel to categories; ONLY numbers that "
        "appear in the verified claims."
    )
    source_index: Optional[int] = Field(
        None, description="1-based citation index the chart data comes from."
    )


class FinalReport(BaseModel):
    """Output of the Synthesizer: the cited brief shown to the human for approval."""

    summary: str = Field(..., description="Executive summary of the findings.")
    sections: list[ReportSection] = Field(
        ..., description="Body of the report, grounded only in verified claims."
    )
    citations: list[str] = Field(
        ..., description="Deduplicated list of source URLs cited in the report."
    )
    key_figures: list[KeyFigure] = Field(
        default_factory=list,
        description="2-4 headline statistics for an at-a-glance band; empty if the "
        "report has no clear numbers.",
    )
    chart: Optional[ReportChart] = Field(
        default=None,
        description="Optional chart; null unless the verified claims contain a "
        "coherent trend or comparison.",
    )
