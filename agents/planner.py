"""Planner agent — decomposes the research question into sub-questions.

Member 1 (Vinayak Paka). Output: a structured ``ResearchPlan``.
"""
from __future__ import annotations

from graph.state import ResearchState
from llm import get_llm
from schemas.models import ResearchPlan

PLANNER_SYSTEM = (
    "You are the Planner in a multi-agent research system. Given a research "
    "question, break it into 3 to 6 specific, independently answerable "
    "sub-questions that together fully cover the original question. Avoid "
    "overlap between sub-questions. Also give a one or two sentence rationale "
    "for the decomposition."
)


def _llm():
    return get_llm(max_tokens=1024)


def plan_node(state: ResearchState) -> dict:
    """LangGraph node: question -> ResearchPlan."""
    llm = _llm().with_structured_output(ResearchPlan)
    plan = llm.invoke(
        [
            ("system", PLANNER_SYSTEM),
            ("human", f"Research question: {state['question']}"),
        ]
    )
    return {"plan": plan}
