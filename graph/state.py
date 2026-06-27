"""The shared LangGraph state for CiteWise.

Every node receives and returns (a partial of) this ``ResearchState``. This is
the jointly-owned contract between Member 1 (input side: question/plan/claims)
and Member 2 (output side: verdicts/report/approval), so it lives on ``main``.

Note on ``claims``: it uses ``operator.add`` as a reducer, so each Researcher
pass *appends* its claims to the accumulated list rather than replacing it.
This is what lets the Fact-Checker → Researcher retry loop build up evidence
across iterations. All other fields use the default "replace" semantics.
"""
import operator
from typing import Annotated, Optional, TypedDict

from schemas.models import Claim, FinalReport, ResearchPlan, Verdict


class ResearchState(TypedDict):
    # --- input ---
    question: str

    # --- Planner ---
    plan: Optional[ResearchPlan]

    # --- Researcher (accumulated across retry loops) ---
    claims: Annotated[list[Claim], operator.add]

    # --- Fact-Checker ---
    verdicts: list[Verdict]
    verified_claims: list[Claim]
    retry_count: int

    # --- Synthesizer ---
    report: Optional[FinalReport]

    # --- Guardrails / human-in-the-loop ---
    refused: bool
    refusal_reason: Optional[str]
    approved: bool
    # Human steering captured when a draft is rejected; consumed by the
    # Synthesizer so the revision actually differs from the rejected draft.
    revision_feedback: Optional[str]
    # How many times the human has rejected a draft. Lets the Synthesizer rotate
    # to a visibly different report structure on each feedback-less rejection.
    revision_count: int
