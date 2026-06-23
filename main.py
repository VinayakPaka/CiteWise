"""CiteWise — end-to-end demo entry point.

Runs the full multi-agent graph (Planner → Researcher → Fact-Checker →
Synthesizer) with the retry loop and the human-in-the-loop approval interrupt,
then exports the approved report. Member 2 owns this end-to-end wiring.

Usage:
    python main.py                       # uses the built-in sample question
    python main.py "Your question here"  # research your own question
"""
from __future__ import annotations

import sys

import config
from graph.graph import build_graph, run_research
from observability import langsmith_status
from schemas.models import FinalReport

SAMPLE_QUESTION = "How has the cost of solar power changed, and why does it matter?"


def _print_report(report: FinalReport) -> None:
    print("\n" + "=" * 70)
    print("DRAFT REPORT (pending your approval)")
    print("=" * 70)
    print(f"\n{report.summary}\n")
    for section in report.sections:
        print(f"## {section.heading}\n{section.content}\n")
    if report.citations:
        print("Citations:")
        for url in report.citations:
            print(f"  - {url}")
    print("=" * 70)


def _interactive_approval(payload: dict) -> bool:
    """Human-in-the-loop callback: show the draft and ask to approve."""
    print(f"\nQuestion: {payload.get('question')}")
    print(f"\nSummary: {payload.get('summary')}")
    print(f"Sections: {', '.join(payload.get('sections', [])) or '(none)'}")
    citations = payload.get("citations", [])
    print("Citations:")
    for url in citations:
        print(f"  - {url}")
    if not citations:
        print("  (none)")

    answer = input("\nApprove and export this report? [y/N]: ").strip().lower()
    approved = answer in {"y", "yes"}
    print("Approved — exporting." if approved else "Rejected — sending back for revision.")
    return approved


def main() -> None:
    question = sys.argv[1] if len(sys.argv) > 1 else SAMPLE_QUESTION

    print("CiteWise — Multi-Agent Research Assistant")
    print(langsmith_status())
    print(f"Model: {config.CITEWISE_MODEL}")
    print(f"\nResearch question: {question}\n")

    if not config.active_provider_key():
        print(f"No API key for provider '{config.CITEWISE_PROVIDER}' — add it to .env to run the agents.")
        print("(Guardrails still run; try `python -m eval.run_eval --offline`.)")
        return
    if not config.TAVILY_API_KEY:
        print("TAVILY_API_KEY is not set — add it to .env (web search needs it).")
        return

    app = build_graph()
    state = run_research(app, question, on_approval=_interactive_approval)

    if state.get("refused"):
        print(f"\nRequest refused by guardrails: {state.get('refusal_reason')}")
        return

    report = state.get("report")
    if report is not None:
        _print_report(report)
    if state.get("approved"):
        print("\nReport approved and exported to the ./output directory.")
    else:
        print("\nReport was not approved; nothing was exported.")


if __name__ == "__main__":
    main()
