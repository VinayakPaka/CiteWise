"""Demo runner for Member 1's slice: Planner -> Researcher.

Runs the two agents in sequence on a sample question and prints the plan and the
extracted, sourced claims. This exercises the input-side pipeline in isolation;
the full LangGraph (with Fact-Checker, Synthesizer, routing and human approval)
is assembled by Member 2 in graph/graph.py.

Usage (from the repo root, after `pip install -r requirements.txt` and filling
in .env):

    python -m scripts.demo_research
"""
from __future__ import annotations

import config
from agents.planner import plan_node
from agents.researcher import research_node

SAMPLE_QUESTION = "How has the cost of solar power changed, and why does it matter?"


def main() -> None:
    if not config.ANTHROPIC_API_KEY:
        print("ANTHROPIC_API_KEY is not set — add it to .env first.")
        return
    if not config.TAVILY_API_KEY:
        print("TAVILY_API_KEY is not set — add it to .env first.")
        return

    state = {
        "question": SAMPLE_QUESTION,
        "plan": None,
        "claims": [],
        "verdicts": [],
        "verified_claims": [],
        "retry_count": 0,
        "report": None,
        "refused": False,
        "refusal_reason": None,
        "approved": False,
    }

    print(f"Question: {SAMPLE_QUESTION}\n")

    # 1. Planner
    state.update(plan_node(state))
    plan = state["plan"]
    print("Plan / sub-questions:")
    for i, sq in enumerate(plan.sub_questions, 1):
        print(f"  {i}. {sq}")
    print(f"  rationale: {plan.rationale}\n")

    # 2. Researcher
    update = research_node(state)
    claims = update["claims"]
    print(f"Extracted {len(claims)} sourced claim(s):")
    for c in claims:
        print(f"  - {c.text}\n    source: {c.source_url}")


if __name__ == "__main__":
    main()
