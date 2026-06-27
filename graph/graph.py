"""LangGraph assembly — nodes, conditional edges, loop guard, HITL interrupt.

Member 2 (output side). This module owns the orchestration:

    START → guardrail → Planner → Researcher → Fact-Checker
        → [conditional #1] unsupported claims & retry_count < MAX ? → Researcher
                                                                    : → Synthesizer
        → Human Approval (interrupt)
        → [conditional #2] approved ? → Export : → Synthesizer (revise)
        → END

Two conditional edges, a retry loop guard (``retry_count`` cap), and a
human-in-the-loop interrupt before the high-impact Export step are all defined
here. The Planner/Researcher nodes are Member 1's; everything else is Member 2's.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from agents.fact_checker import fact_check_node
from agents.planner import plan_node
from agents.researcher import research_node
from agents.synthesizer import synthesize_node
from config import MAX_RESEARCH_RETRIES, evidence_is_thin
from graph.state import ResearchState
from guardrails.policy import check_policy
from guardrails.validation import validate_question
from observability import log_event
from schemas.models import FinalReport

OUTPUT_DIR = Path(os.getenv("CITEWISE_OUTPUT_DIR", "output"))


# --------------------------------------------------------------------------- #
# Nodes (Member 2)
# --------------------------------------------------------------------------- #
def guardrail_node(state: ResearchState) -> dict:
    """Input validation + policy/refusal, before any LLM or tool call."""
    question = state["question"]

    valid = validate_question(question)
    if not valid.ok:
        log_event("guardrail_reject", stage="validation", reason=valid.reason)
        return {"refused": True, "refusal_reason": valid.reason}

    policy = check_policy(question)
    if not policy.allowed:
        log_event("guardrail_reject", stage="policy", reason=policy.reason)
        return {"refused": True, "refusal_reason": policy.reason}

    log_event("guardrail_pass", question=question)
    return {"refused": False, "refusal_reason": None}


def increment_retry_node(state: ResearchState) -> dict:
    """Loop-guard bookkeeping: bump retry_count before re-running the Researcher."""
    new_count = state.get("retry_count", 0) + 1
    log_event("retry", retry_count=new_count, max_retries=MAX_RESEARCH_RETRIES)
    return {"retry_count": new_count}


def human_approval_node(state: ResearchState) -> dict:
    """Human-in-the-loop gate. Interrupts the graph to collect an approve/reject.

    Resume by invoking the graph with ``Command(resume={"approved": bool})``.
    Nothing is persisted until this returns ``approved=True``.
    """
    report: FinalReport = state["report"]
    decision = interrupt(
        {
            "action": "approve_report",
            "question": state["question"],
            "summary": report.summary,
            "sections": [s.heading for s in report.sections],
            "citations": report.citations,
        }
    )

    if isinstance(decision, dict):
        approved = bool(decision.get("approved"))
        feedback = (decision.get("feedback") or "").strip()
    else:
        approved = bool(decision)
        feedback = ""

    # On a rejection we steer the Synthesizer: explicit feedback if the reviewer
    # gave any, and a bumped revision_count so a feedback-less reject still rotates
    # to a visibly different structure (handled in the Synthesizer).
    updates: dict = {"approved": approved}
    if approved:
        updates["revision_feedback"] = None
    else:
        updates["revision_feedback"] = feedback or None
        updates["revision_count"] = state.get("revision_count", 0) + 1

    log_event(
        "human_decision",
        approved=approved,
        has_feedback=bool(feedback),
        revision_count=updates.get("revision_count", state.get("revision_count", 0)),
    )
    return updates


def export_node(state: ResearchState) -> dict:
    """High-impact action: persist the approved report to disk (post-approval)."""
    report: FinalReport = state["report"]
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = OUTPUT_DIR / f"report_{stamp}.md"

    lines = [f"# CiteWise Report\n", f"**Question:** {state['question']}\n",
             "## Summary\n", report.summary, ""]
    for section in report.sections:
        lines += [f"## {section.heading}\n", section.content, ""]
    if report.citations:
        lines += ["## Citations\n"]
        # Number them so the inline [n] markers in the prose resolve here.
        lines += [f"[{i}] {url}" for i, url in enumerate(report.citations, 1)]
    path.write_text("\n".join(lines), encoding="utf-8")

    log_event("export", path=str(path), n_citations=len(report.citations))
    return {}


# --------------------------------------------------------------------------- #
# Conditional edge routers (Member 2)
# --------------------------------------------------------------------------- #
def route_after_guardrail(state: ResearchState) -> str:
    return "refused" if state.get("refused") else "ok"


def route_after_factcheck(state: ResearchState) -> str:
    """Conditional #1: adversarial retry loop with a hard loop guard.

    Loop back to the Researcher while the retry budget is not exhausted AND either
    (a) some claims remain unverified, or (b) the verified evidence is still too
    thin (too few verified claims / distinct sources). Case (b) is what keeps a
    sparse topic — one whose first search returns a single secondary source and a
    handful of claims that all happen to verify — from being written up as a
    complete report. Otherwise proceed to synthesis.
    """
    verdicts = state.get("verdicts", []) or []
    verified = state.get("verified_claims", []) or []
    retry_count = state.get("retry_count", 0)

    unresolved = [v for v in verdicts if v.status != "supported"]
    thin = evidence_is_thin(verified)
    needs_more = bool(unresolved) or thin

    if needs_more and retry_count < MAX_RESEARCH_RETRIES:
        log_event(
            "route_factcheck",
            decision="retry",
            unresolved=len(unresolved),
            verified=len(verified),
            thin=thin,
            retry_count=retry_count,
        )
        return "retry"

    log_event(
        "route_factcheck",
        decision="synthesize",
        verified=len(verified),
        thin=thin,
        retry_count=retry_count,
        loop_guard_hit=needs_more and retry_count >= MAX_RESEARCH_RETRIES,
    )
    return "synthesize"


def route_after_approval(state: ResearchState) -> str:
    """Conditional #2: human-approval gate → export or revise."""
    decision = "export" if state.get("approved") else "revise"
    log_event("route_approval", decision=decision)
    return decision


# --------------------------------------------------------------------------- #
# Graph assembly
# --------------------------------------------------------------------------- #
def build_graph(checkpointer=None):
    """Build and compile the CiteWise graph.

    A checkpointer is required for the human-in-the-loop interrupt to work; a
    ``MemorySaver`` is used by default for the demo.
    """
    g = StateGraph(ResearchState)

    g.add_node("guardrail", guardrail_node)
    g.add_node("planner", plan_node)
    g.add_node("researcher", research_node)
    g.add_node("fact_checker", fact_check_node)
    g.add_node("increment_retry", increment_retry_node)
    g.add_node("synthesizer", synthesize_node)
    g.add_node("human_approval", human_approval_node)
    g.add_node("export", export_node)

    g.add_edge(START, "guardrail")
    g.add_conditional_edges(
        "guardrail", route_after_guardrail, {"refused": END, "ok": "planner"}
    )
    g.add_edge("planner", "researcher")
    g.add_edge("researcher", "fact_checker")
    g.add_conditional_edges(
        "fact_checker",
        route_after_factcheck,
        {"retry": "increment_retry", "synthesize": "synthesizer"},
    )
    g.add_edge("increment_retry", "researcher")
    g.add_edge("synthesizer", "human_approval")
    g.add_conditional_edges(
        "human_approval",
        route_after_approval,
        {"export": "export", "revise": "synthesizer"},
    )
    g.add_edge("export", END)

    return g.compile(checkpointer=checkpointer or MemorySaver())


def run_research(app, question: str, on_approval, thread_id: str = "demo") -> ResearchState:
    """Drive the graph end-to-end, handling the human-approval interrupt loop.

    ``on_approval`` is called with the interrupt payload (the draft report) and
    must return ``True`` to export or ``False`` to send the draft back for
    revision. This keeps the interrupt mechanics in one place so both the demo
    (interactive) and the eval harness (auto-approve) reuse the same driver.
    """
    from langgraph.types import Command

    config = {"configurable": {"thread_id": thread_id}}
    result = app.invoke(initial_state(question), config)

    # Loop while the graph is paused at the human-approval interrupt.
    while "__interrupt__" in result:
        payload = result["__interrupt__"][0].value
        approved = bool(on_approval(payload))
        result = app.invoke(Command(resume={"approved": approved}), config)

    return app.get_state(config).values


def initial_state(question: str) -> ResearchState:
    """Build a fresh ResearchState for a question."""
    return {
        "question": question,
        "plan": None,
        "claims": [],
        "verdicts": [],
        "verified_claims": [],
        "retry_count": 0,
        "report": None,
        "refused": False,
        "refusal_reason": None,
        "approved": False,
        "revision_feedback": None,
        "revision_count": 0,
    }
