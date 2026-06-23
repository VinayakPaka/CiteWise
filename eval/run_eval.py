"""Evaluation harness runner.

Member 2. Runs the 6 evaluation cases through the compiled CiteWise graph and
checks each one against its expectations, printing a pass/fail report. LLM-backed
cases auto-approve the human-in-the-loop gate so the harness is non-interactive.

Usage (from the repo root):

    python -m eval.run_eval            # run all cases (LLM cases need API keys)
    python -m eval.run_eval --offline  # only deterministic guardrail cases
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

import config
from eval.test_cases import CASES, EvalCase
from graph.graph import build_graph, run_research
from observability import langsmith_status, log_event


@dataclass
class CaseResult:
    case: EvalCase
    status: str  # "PASS", "FAIL", "SKIP", "ERROR"
    detail: str


def _check_expectations(case: EvalCase, state: dict) -> tuple[bool, str]:
    failures: list[str] = []

    if case.expect_refused is not None:
        if bool(state.get("refused")) != case.expect_refused:
            failures.append(
                f"refused={state.get('refused')} (expected {case.expect_refused})"
            )

    if case.expect_report:
        if state.get("report") is None:
            failures.append("expected a report, got none")

    if case.expect_min_sub_questions is not None:
        plan = state.get("plan")
        n = len(plan.sub_questions) if plan else 0
        if n < case.expect_min_sub_questions:
            failures.append(
                f"sub_questions={n} (expected >= {case.expect_min_sub_questions})"
            )

    verified = state.get("verified_claims") or []
    if case.expect_min_verified is not None and len(verified) < case.expect_min_verified:
        failures.append(
            f"verified={len(verified)} (expected >= {case.expect_min_verified})"
        )
    if case.expect_max_verified is not None and len(verified) > case.expect_max_verified:
        failures.append(
            f"verified={len(verified)} (expected <= {case.expect_max_verified})"
        )

    if failures:
        return False, "; ".join(failures)
    return True, "ok"


def run_case(app, case: EvalCase, offline: bool) -> CaseResult:
    if case.requires_llm and (offline or not config.active_provider_key()):
        return CaseResult(case, "SKIP", f"needs {config.CITEWISE_PROVIDER} key (and Tavily)")

    log_event("eval_case_start", id=case.id, category=case.category)
    try:
        state = run_research(
            app,
            case.question,
            on_approval=lambda payload: True,  # auto-approve for non-interactive eval
            thread_id=f"eval-{case.id}",
        )
    except Exception as exc:  # surface infra/LLM errors without aborting the suite
        return CaseResult(case, "ERROR", f"{type(exc).__name__}: {exc}")

    ok, detail = _check_expectations(case, state)
    return CaseResult(case, "PASS" if ok else "FAIL", detail)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the CiteWise eval harness.")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Only run deterministic (no-API) guardrail cases.",
    )
    args = parser.parse_args()

    print("CiteWise — Evaluation Harness")
    print(langsmith_status())
    if not config.active_provider_key() and not args.offline:
        print(f"Note: no {config.CITEWISE_PROVIDER} API key set — LLM cases will be skipped.\n")

    app = build_graph()
    results = [run_case(app, case, args.offline) for case in CASES]

    print("\n" + "=" * 78)
    icon = {"PASS": "PASS ", "FAIL": "FAIL ", "SKIP": "SKIP ", "ERROR": "ERROR"}
    for r in results:
        print(f"[{icon[r.status]}] {r.case.category:24} {r.detail}")
    print("=" * 78)

    passed = sum(r.status == "PASS" for r in results)
    failed = sum(r.status == "FAIL" for r in results)
    skipped = sum(r.status == "SKIP" for r in results)
    errored = sum(r.status == "ERROR" for r in results)
    print(
        f"Summary: {passed} passed, {failed} failed, "
        f"{skipped} skipped, {errored} errored (of {len(results)})."
    )

    # Non-zero exit if anything actually failed or errored (skips are fine).
    return 1 if (failed or errored) else 0


if __name__ == "__main__":
    raise SystemExit(main())
