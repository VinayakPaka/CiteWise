"""FastAPI backend for CiteWise.

Exposes the LangGraph pipeline as a small web API and serves the single-page UI:

  GET  /                  -> the web UI (static/index.html)
  GET  /api/config        -> active provider + model
  GET  /api/research?question=...
                          -> Server-Sent Events: live node progress, then the
                             draft report (the graph pauses at the human-approval
                             interrupt) or a guardrail refusal
  POST /api/approve       -> resume the paused graph (approve -> export, or
                             reject -> revise and pause again)

The compiled graph is built once at startup; its in-memory checkpointer keeps
each request's thread state alive between the /research and /approve calls, which
is what makes the human-in-the-loop interrupt work across two HTTP requests.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from langgraph.types import Command
from pydantic import BaseModel

import config
from graph.graph import build_graph, initial_state
from schemas.models import FinalReport

app = FastAPI(title="CiteWise")

# Built once — the MemorySaver checkpointer inside persists thread state across
# the /research (pause at interrupt) and /approve (resume) requests.
GRAPH = build_graph()

_INDEX_HTML = (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")

# Friendly labels for the live progress display.
NODE_LABELS = {
    "guardrail": "Validating request",
    "planner": "Planning sub-questions",
    "researcher": "Researching (web + knowledge base)",
    "fact_checker": "Fact-checking claims",
    "increment_retry": "Re-researching for more evidence",
    "synthesizer": "Writing the report",
    "human_approval": "Awaiting approval",
    "export": "Exporting report",
}


class ApproveRequest(BaseModel):
    thread_id: str
    approved: bool


def _report_dict(report: FinalReport) -> dict:
    return {
        "summary": report.summary,
        "sections": [{"heading": s.heading, "content": s.content} for s in report.sections],
        "citations": report.citations,
    }


def _draft_payload(state: dict) -> dict:
    report = state.get("report")
    verdicts = state.get("verdicts", []) or []
    return {
        "report": _report_dict(report) if report else None,
        "verdicts": [
            {
                "claim": v.claim_text,
                "status": v.status,
                "confidence": round(v.confidence, 2),
                "reasoning": v.reasoning,
            }
            for v in verdicts
        ],
        "n_claims": len(state.get("claims", []) or []),
        "n_verified": len(state.get("verified_claims", []) or []),
    }


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse(_INDEX_HTML)


@app.get("/favicon.ico")
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/api/config")
def get_config() -> dict:
    return {"provider": config.CITEWISE_PROVIDER, "model": config.CITEWISE_MODEL}


@app.get("/api/research")
def research(question: str) -> StreamingResponse:
    thread_id = uuid.uuid4().hex
    cfg = {"configurable": {"thread_id": thread_id}}

    def gen():
        yield _sse({"type": "thread", "thread_id": thread_id})
        try:
            for chunk in GRAPH.stream(initial_state(question), cfg, stream_mode="updates"):
                if "__interrupt__" in chunk:
                    continue  # the human-approval pause; handled via state below
                for node in chunk:
                    yield _sse({"type": "node", "node": node, "label": NODE_LABELS.get(node, node)})

            state = GRAPH.get_state(cfg).values
            if state.get("refused"):
                yield _sse({"type": "refused", "reason": state.get("refusal_reason")})
            elif state.get("report"):
                yield _sse({"type": "draft", "thread_id": thread_id, **_draft_payload(state)})
            else:
                yield _sse({"type": "done"})
        except Exception as exc:  # surface errors to the UI instead of a dead stream
            yield _sse({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        yield _sse({"type": "end"})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/approve")
def approve(req: ApproveRequest) -> JSONResponse:
    cfg = {"configurable": {"thread_id": req.thread_id}}
    try:
        result = GRAPH.invoke(Command(resume={"approved": req.approved}), cfg)
        state = GRAPH.get_state(cfg).values
        if "__interrupt__" in result:
            # Rejected -> the Synthesizer revised the draft and paused again.
            return JSONResponse({"status": "draft", **_draft_payload(state)})
        status = "exported" if state.get("approved") else "done"
        report = state.get("report")
        return JSONResponse(
            {"status": status, "report": _report_dict(report) if report else None}
        )
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "message": f"{type(exc).__name__}: {exc}"}, status_code=500
        )
