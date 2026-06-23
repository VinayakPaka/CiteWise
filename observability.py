"""Observability for CiteWise — structured logs + LangSmith tracing.

Member 2 (output side). Every node, claim, verdict and routing decision is
emitted as a single structured (JSON) log line so a reviewer can replay exactly
what the graph did and why. If LangSmith env vars are present, LangChain/LangGraph
auto-trace each LLM call on top of these logs.

Import ``log_event`` in any node and call it with a short event name plus
keyword fields, e.g.::

    log_event("verdict", claim=claim.text, status=v.status, confidence=v.confidence)
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone

_LOGGER_NAME = "citewise"
_configured = False


def _configure() -> logging.Logger:
    global _configured
    logger = logging.getLogger(_LOGGER_NAME)
    if _configured:
        return logger

    level = os.getenv("CITEWISE_LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, level, logging.INFO))

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    _configured = True
    return logger


def langsmith_status() -> str:
    """Human-readable note on whether LangSmith tracing is active."""
    tracing = os.getenv("LANGSMITH_TRACING", "").lower() in {"1", "true", "yes"}
    has_key = bool(os.getenv("LANGSMITH_API_KEY"))
    if tracing and has_key:
        project = os.getenv("LANGSMITH_PROJECT", "default")
        return f"LangSmith tracing ON (project={project})"
    if tracing and not has_key:
        return "LangSmith tracing requested but LANGSMITH_API_KEY missing"
    return "LangSmith tracing OFF (set LANGSMITH_TRACING=true + LANGSMITH_API_KEY)"


def log_event(event: str, **fields) -> None:
    """Emit one structured log line: ``{"ts", "event", ...fields}``."""
    logger = _configure()
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **fields,
    }
    try:
        logger.info(json.dumps(record, default=str, ensure_ascii=False))
    except (TypeError, ValueError):
        # Fall back to repr if a field is not JSON-serialisable.
        logger.info("%s %r", event, fields)
