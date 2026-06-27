"""FastAPI backend for CiteWise.

Exposes the LangGraph pipeline as a small web API, serves the single-page UI, and
adds accounts + per-user research history on top:

  GET  /                    -> the web UI (static/index.html)
  GET  /api/config          -> active provider + model + which logins are enabled
  GET  /api/me              -> the signed-in user (or null)
  POST /auth/signup         -> create an email + password account
  POST /auth/login          -> sign in with email + password
  POST /auth/demo           -> guest login (no password)
  POST /auth/logout         -> end the session
  GET  /api/research?question=...
                            -> Server-Sent Events: live node progress, then the
                               draft report (the graph pauses at the human-approval
                               interrupt) or a guardrail refusal
  POST /api/approve         -> resume the paused graph (approve -> export, or
                               reject -> revise and pause again)
  GET    /api/history       -> the signed-in user's past research runs
  GET    /api/history/{id}  -> one past run (full report + verdicts)
  DELETE /api/history/{id}  -> delete one past run

The compiled graph is built once at startup; its in-memory checkpointer keeps
each request's thread state alive between the /research and /approve calls, which
is what makes the human-in-the-loop interrupt work across two HTTP requests.
Completed runs are persisted to SQLite (see ``webapp/db.py``) so they survive in
each user's history sidebar.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from langgraph.types import Command
from pydantic import BaseModel

import config
from graph.graph import build_graph, initial_state
from schemas.models import FinalReport
from webapp import auth, db, pdf_export

app = FastAPI(title="CiteWise")

# Built once — the MemorySaver checkpointer inside persists thread state across
# the /research (pause at interrupt) and /approve (resume) requests.
GRAPH = build_graph()

_STATIC_DIR = Path(__file__).parent / "static"
_INDEX_HTML = (_STATIC_DIR / "index.html").read_text(encoding="utf-8")

app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


# Friendly labels for the live progress display.
NODE_LABELS = {
    "guardrail": "Validating request",
    "planner": "Planning sub-questions",
    "researcher": "Researching the web",
    "fact_checker": "Fact-checking claims",
    "increment_retry": "Re-researching for more evidence",
    "synthesizer": "Writing the report",
    "human_approval": "Awaiting approval",
    "export": "Exporting report",
}


class ApproveRequest(BaseModel):
    thread_id: str
    approved: bool
    feedback: str | None = None


class DemoLoginRequest(BaseModel):
    name: str = "Guest"


class SignupRequest(BaseModel):
    email: str
    password: str
    name: str | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


def _report_dict(report: FinalReport) -> dict:
    return {
        "summary": report.summary,
        "sections": [{"heading": s.heading, "content": s.content} for s in report.sections],
        "citations": report.citations,
        "key_figures": [f.model_dump() for f in report.key_figures],
        "chart": report.chart.model_dump() if report.chart else None,
    }


def _verdicts_list(verdicts) -> list[dict]:
    return [
        {
            "claim": v.claim_text,
            "status": v.status,
            "confidence": round(v.confidence, 2),
            "reasoning": v.reasoning,
        }
        for v in (verdicts or [])
    ]


def _draft_payload(state: dict) -> dict:
    report = state.get("report")
    return {
        "report": _report_dict(report) if report else None,
        "verdicts": _verdicts_list(state.get("verdicts", [])),
        "n_claims": len(state.get("claims", []) or []),
        "n_verified": len(state.get("verified_claims", []) or []),
    }


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"


def _unauthorized() -> JSONResponse:
    return JSONResponse({"error": "unauthorized"}, status_code=401)


# --------------------------------------------------------------------------- #
# Pages & config
# --------------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse(_INDEX_HTML)


@app.get("/favicon.ico")
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/api/config")
def get_config() -> dict:
    return {
        "provider": config.CITEWISE_PROVIDER,
        "model": config.CITEWISE_MODEL,
        "chain": config.llm_chain(),  # primary + reachable fallbacks, in order
        "allow_guest": config.ALLOW_GUEST,
    }


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
@app.get("/api/me")
def me(request: Request) -> JSONResponse:
    user = auth.current_user(request)
    return JSONResponse({"user": auth.public_user(user) if user else None})


@app.post("/auth/signup")
def auth_signup(req: SignupRequest) -> JSONResponse:
    user, error = auth.signup(req.email, req.password, req.name or "")
    if error:
        return JSONResponse({"error": error}, status_code=400)
    resp = JSONResponse({"ok": True, "user": auth.public_user(user)})
    auth.login_user(resp, user)
    return resp


@app.post("/auth/login")
def auth_login(req: LoginRequest) -> JSONResponse:
    user, error = auth.login(req.email, req.password)
    if error:
        return JSONResponse({"error": error}, status_code=401)
    resp = JSONResponse({"ok": True, "user": auth.public_user(user)})
    auth.login_user(resp, user)
    return resp


@app.post("/auth/demo")
def auth_demo(req: DemoLoginRequest) -> JSONResponse:
    if not config.ALLOW_GUEST:
        return JSONResponse({"error": "guest_disabled"}, status_code=403)
    user = auth.guest_user(req.name)
    resp = JSONResponse({"ok": True, "user": auth.public_user(user)})
    auth.login_user(resp, user)
    return resp


@app.post("/auth/logout")
def auth_logout() -> JSONResponse:
    resp = JSONResponse({"ok": True})
    auth.logout(resp)
    return resp


# --------------------------------------------------------------------------- #
# Research (auth required)
# --------------------------------------------------------------------------- #
@app.get("/api/research")
def research(request: Request, question: str) -> Response:
    user = auth.current_user(request)
    if not user:
        return _unauthorized()
    user_id = user["id"]
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
                db.save_research(
                    user_id, thread_id, question, "refused",
                    provider=config.CITEWISE_PROVIDER, model=config.CITEWISE_MODEL,
                )
                yield _sse({"type": "refused", "reason": state.get("refusal_reason")})
            elif state.get("report"):
                payload = _draft_payload(state)
                rid = db.save_research(
                    user_id, thread_id, question, "draft",
                    report=payload["report"], verdicts=payload["verdicts"],
                    n_claims=payload["n_claims"], n_verified=payload["n_verified"],
                    provider=config.CITEWISE_PROVIDER, model=config.CITEWISE_MODEL,
                )
                yield _sse({"type": "draft", "thread_id": thread_id, "research_id": rid, **payload})
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
def approve(request: Request, req: ApproveRequest) -> JSONResponse:
    user = auth.current_user(request)
    if not user:
        return _unauthorized()
    user_id = user["id"]
    cfg = {"configurable": {"thread_id": req.thread_id}}
    try:
        result = GRAPH.invoke(
            Command(resume={"approved": req.approved, "feedback": req.feedback}), cfg
        )
        state = GRAPH.get_state(cfg).values
        question = state.get("question", "")
        if "__interrupt__" in result:
            # Rejected -> the Synthesizer revised the draft and paused again.
            payload = _draft_payload(state)
            db.save_research(
                user_id, req.thread_id, question, "draft",
                report=payload["report"], verdicts=payload["verdicts"],
                n_claims=payload["n_claims"], n_verified=payload["n_verified"],
                provider=config.CITEWISE_PROVIDER, model=config.CITEWISE_MODEL,
            )
            return JSONResponse({"status": "draft", **payload})

        approved = bool(state.get("approved"))
        report = state.get("report")
        report_dict = _report_dict(report) if report else None
        db.save_research(
            user_id, req.thread_id, question,
            "exported" if approved else "draft",
            report=report_dict, verdicts=_verdicts_list(state.get("verdicts", [])),
            provider=config.CITEWISE_PROVIDER, model=config.CITEWISE_MODEL,
        )
        return JSONResponse(
            {"status": "exported" if approved else "done", "report": report_dict}
        )
    except Exception as exc:
        return JSONResponse(
            {"status": "error", "message": f"{type(exc).__name__}: {exc}"}, status_code=500
        )


# --------------------------------------------------------------------------- #
# History (auth required)
# --------------------------------------------------------------------------- #
@app.get("/api/history")
def history(request: Request) -> JSONResponse:
    user = auth.current_user(request)
    if not user:
        return _unauthorized()
    return JSONResponse({"items": db.list_researches(user["id"])})


@app.get("/api/history/{research_id}")
def history_item(request: Request, research_id: int) -> JSONResponse:
    user = auth.current_user(request)
    if not user:
        return _unauthorized()
    rec = db.get_research(user["id"], research_id)
    if not rec:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse(rec)


@app.get("/api/history/{research_id}/pdf")
def history_pdf(request: Request, research_id: int) -> Response:
    """Stream the report for one run as a polished, on-brand PDF download."""
    user = auth.current_user(request)
    if not user:
        return _unauthorized()
    rec = db.get_research(user["id"], research_id)
    if not rec or not rec.get("report"):
        return JSONResponse({"error": "not_found"}, status_code=404)
    pdf = pdf_export.build_report_pdf(
        rec["question"],
        rec["report"],
        meta={
            "created_at": rec.get("created_at"),
            "provider": rec.get("provider"),
            "model": rec.get("model"),
            "n_verified": rec.get("n_verified"),
        },
    )
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{pdf_export.filename_for(rec["question"])}"'
        },
    )


@app.delete("/api/history/{research_id}")
def history_delete(request: Request, research_id: int) -> JSONResponse:
    user = auth.current_user(request)
    if not user:
        return _unauthorized()
    ok = db.delete_research(user["id"], research_id)
    return JSONResponse({"ok": ok})
