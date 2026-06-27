"""Synthesizer agent — writes the final cited report from verified claims only.

Member 2 (output side). The Synthesizer receives the claims the Fact-Checker
marked ``supported`` and writes a structured ``FinalReport``. Citation
enforcement is applied *after* generation so the report can only cite sources
backed by a supported claim — the enforcement is by construction, not by trust.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from graph.state import ResearchState
from llm import get_structured_llm
from observability import log_event
from schemas.models import Claim, FinalReport, KeyFigure, ReportChart, ReportSection
from tools.citation_validator import allowed_sources, enforce_citations

SYNTHESIZER_SYSTEM = (
    "You are the Synthesizer in a multi-agent research system. Write a concise, "
    "well-structured research brief that answers the user's question using ONLY "
    "the verified claims provided. Every statement in your report must be "
    "traceable to one of these claims and their sources. Do NOT introduce facts "
    "that are not in the verified claims. If the verified evidence is thin, say so "
    "honestly rather than padding the report.\n\n"
    "CITATIONS — this is mandatory and non-negotiable:\n"
    "Each source has a fixed number (e.g. [1], [2]) given to you in the prompt. "
    "Every factual statement in the summary AND in the body sections must end with "
    "the inline citation marker(s) of the source(s) that back it, e.g. "
    "“Module prices fell roughly 90% over the decade [1].” When a "
    "statement draws on two sources, cite both: “... [1][3]”. Use the "
    "EXACT source numbers given to you — never renumber them, never invent a "
    "number, and never cite a source as a bare URL inside the prose. A sentence "
    "of fact without an inline [n] marker is not acceptable.\n\n"
    "CALIBRATED LANGUAGE — match your wording to the strength of the evidence:\n"
    "- Projections are not certainties. When a claim describes a projection, "
    "scenario, model or forecast (signalled by words like 'projected', "
    "'scenarios show', 'by 2035', 'by mid-century'), report it AS a projection — "
    "e.g. 'several scenarios project that solar PV could become one of the "
    "dominant sources of electricity', NOT 'solar PV will dominate'. Prefer "
    "'could', 'is projected to', 'scenarios suggest' over 'will'.\n"
    "- Be precise about scope. If a claim is specifically about utility-scale "
    "solar, write 'utility-scale solar', not 'solar' in general (rooftop solar "
    "costs more), and keep any figure attached to the segment it describes.\n"
    "- Do not assert a cause or mechanism (e.g. 'economies of scale', "
    "'technological improvements') unless a verified claim states it; if the "
    "evidence only reports an outcome, describe the outcome, not an unstated cause."
)

# Distinct report structures cycled when the reviewer rejects WITHOUT giving
# feedback, so each revision is a visibly different take on the same evidence —
# not a paraphrase. The verified claims (the facts) stay the same; only the
# framing, ordering and headings change.
_REVISION_STYLES = [
    "Use a 'bottom line up front' structure: open the summary with a one-sentence "
    "verdict, then sections titled 'What the evidence supports', 'Where it's "
    "uncertain', and 'Cautions'. Lead with the most concrete, specific findings.",
    "Use a benefits-vs-risks structure: a brief intro, then a 'Benefits' section, "
    "a 'Risks & limitations' section, and a closing 'Practical takeaways' section "
    "with actionable points drawn only from the verified claims.",
    "Use a tight executive-summary structure: 3–4 short sections, each at most two "
    "sentences, no padding, foregrounding numbers and specifics where available.",
]


def _format_claims(verified: list[Claim], source_num: dict[str, int]) -> str:
    """Render claims, tagging each with the inline number of its source."""
    lines = []
    for c in verified:
        n = source_num.get(c.source_url, 0)
        lines.append(
            f"- {c.text}\n    cite this as: [{n}]  (source_url: {c.source_url})\n"
            f"    snippet: {c.source_snippet}"
        )
    return "\n".join(lines)


def _format_sources(sources: list[str]) -> str:
    """Render the fixed source numbering the model must cite by."""
    return "\n".join(f"[{i}] {url}" for i, url in enumerate(sources, 1))


INFOGRAPHIC_SYSTEM = (
    "You turn verified research claims into a small at-a-glance infographic. You "
    "are given the verified claims and their fixed source numbers. Extract two "
    "things:\n\n"
    "1) key_figures: 2 to 4 of the most striking headline NUMBERS that appear in "
    "the claims (a percentage, cost, count, change, etc.). For each, copy the value "
    "VERBATIM from a claim, give a 2-5 word label, and set source_index to the "
    "source number it came from. Return an empty list only if there are fewer than "
    "2 clear numbers.\n\n"
    "2) chart: WHENEVER the claims contain two or more numbers that can be sensibly "
    "compared, BUILD A CHART from them — strongly prefer to include one. Two cases:\n"
    "   - a COMPARISON of one metric across categories/segments -> kind='bar' "
    "(e.g. cost by sector, share by segment, an effect across groups);\n"
    "   - one metric at several points in TIME -> kind='line' (e.g. cost by year).\n"
    "Fill the parallel arrays 'categories' (the labels) and 'values' (the numbers) "
    "— same length, 2 to 8 entries, in a sensible order. Use ONLY numbers that "
    "actually appear in the claims; never invent, estimate or interpolate. Put the "
    "unit in y_label and set source_index. All values in one chart MUST share the "
    "same unit/metric so the comparison is meaningful. Return chart = null ONLY if "
    "there is genuinely no set of two or more same-unit comparable numbers (e.g. "
    "every finding is qualitative).\n\n"
    "Use ONLY values present in the claims. Never fabricate data."
)


class _Infographic(BaseModel):
    """Wrapper for the infographic extraction call's structured output."""

    key_figures: list[KeyFigure] = []
    chart: Optional[ReportChart] = None


def _extract_infographic(verified, sources, source_num):
    """Best-effort: derive key figures + an optional chart from verified claims.

    Grounded in the claims only. Returns ``([], None)`` if extraction is not
    possible or the model errors, so the report still renders without visuals.
    """
    n = len(sources)
    if n == 0 or not verified:
        return [], None
    extractor = get_structured_llm(_Infographic, max_tokens=1536, temperature=0.0)
    human = (
        f"Sources (cite by these numbers):\n{_format_sources(sources)}\n\n"
        f"Verified claims:\n{_format_claims(verified, source_num)}\n\n"
        "Produce the infographic (key_figures and an optional chart) using only "
        "numbers that appear in the claims."
    )
    try:
        info: _Infographic = extractor.invoke(
            [("system", INFOGRAPHIC_SYSTEM), ("human", human)]
        )
    except Exception as exc:  # rate limit / malformed tool call — degrade gracefully
        log_event("infographic_error", error=str(exc)[:200])
        return [], None

    # Keep only figures whose source_index points at a real source.
    figures = [f for f in (info.key_figures or []) if 1 <= f.source_index <= n][:4]

    chart = info.chart
    if chart is not None:
        cats = list(chart.categories or [])
        vals = list(chart.values or [])
        k = min(len(cats), len(vals), 8)
        if k >= 2:
            src = chart.source_index if (chart.source_index and 1 <= chart.source_index <= n) else None
            chart = chart.model_copy(update={"categories": cats[:k], "values": vals[:k], "source_index": src})
        else:
            chart = None  # not enough comparable points
    return figures, chart


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
                    "narrowing its scope, or trying again so the web search can "
                    "surface more verifiable sources."
                ),
            )
        ],
        citations=[],
    )


def synthesize_node(state: ResearchState) -> dict:
    """LangGraph node: verified_claims -> FinalReport (citations enforced)."""
    question = state["question"]
    verified = state.get("verified_claims", []) or []
    feedback = (state.get("revision_feedback") or "").strip()
    revision_count = state.get("revision_count", 0)

    if not verified:
        report = _insufficient_report(question)
        log_event("synthesize", n_verified=0, note="insufficient evidence")
        return {"report": report}

    # Decide how to steer this synthesis:
    #   - explicit human feedback   -> follow it closely
    #   - rejected without feedback -> rotate to a visibly different structure
    #   - first pass                -> standard structure, deterministic
    instruction = ""
    temperature = 0.0
    if feedback:
        instruction = (
            "IMPORTANT — this is a REVISION. A human reviewer rejected the previous "
            "draft and asked for these changes:\n"
            f"“{feedback}”\n"
            "Produce a genuinely revised brief that addresses this feedback "
            "(restructure, re-emphasise, expand or trim as requested) while still "
            "grounding every statement in ONLY the verified claims above.\n\n"
        )
        temperature = 0.5
    elif revision_count > 0:
        style = _REVISION_STYLES[(revision_count - 1) % len(_REVISION_STYLES)]
        instruction = (
            "IMPORTANT — this is a REVISION and the reviewer wants a DIFFERENT take "
            "on the SAME evidence. Do NOT reuse the previous draft's structure, "
            "section headings, or wording.\n"
            f"{style}\n\n"
        )
        temperature = 0.6

    writer = get_structured_llm(FinalReport, max_tokens=4096, temperature=temperature)

    # Fixed, deterministic source numbering — the same order enforce_citations
    # emits, so the inline [n] markers the model writes line up with the numbered
    # Citations list rendered to the reader.
    sources = allowed_sources(verified)
    source_num = {url: i for i, url in enumerate(sources, 1)}

    human = (
        f"Research question: {question}\n\n"
        f"Sources — cite ONLY by these fixed numbers (inline, e.g. [1]):\n"
        f"{_format_sources(sources)}\n\n"
        f"Verified claims (the ONLY facts you may use; each is tagged with the "
        f"source number to cite it by):\n"
        f"{_format_claims(verified, source_num)}\n\n"
        f"{instruction}"
        "Write the final cited research brief. Put an inline [n] marker after "
        "every factual statement, using the source numbers above. Do NOT write a "
        "Citations section yourself — only fill the structured citations field "
        "with the source URLs. Leave key_figures empty and chart null — a separate "
        "step fills those in."
    )

    report: FinalReport = writer.invoke(
        [("system", SYNTHESIZER_SYSTEM), ("human", human)]
    )

    # Enforce: drop any citation not backed by a supported claim.
    report = enforce_citations(report, verified)

    # Derive an at-a-glance infographic (key figures + optional chart) from the
    # verified claims. Best-effort: a failure just leaves the report text-only.
    figures, chart = _extract_infographic(verified, sources, source_num)
    report = report.model_copy(update={"key_figures": figures, "chart": chart})

    log_event(
        "synthesize",
        n_verified=len(verified),
        n_sections=len(report.sections),
        n_citations=len(report.citations),
        n_figures=len(figures),
        has_chart=chart is not None,
        revised=bool(feedback or revision_count),
        revision_count=revision_count,
    )
    return {"report": report}
