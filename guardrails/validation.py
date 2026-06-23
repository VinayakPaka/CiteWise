"""Input validation guardrail.

Member 2. Rejects inputs that are not usable research questions *before* any
LLM/tool call is made: empty, too short to be meaningful, or oversized (a likely
prompt-stuffing / abuse attempt). Deterministic and key-free so it is cheap and
unit-testable.
"""
from __future__ import annotations

from dataclasses import dataclass

MIN_LENGTH = 8          # shorter than this can't be a real research question
MAX_LENGTH = 2000       # guard against oversized / prompt-stuffing inputs
MIN_WORDS = 2


@dataclass
class ValidationResult:
    ok: bool
    reason: str | None = None


def validate_question(question: object) -> ValidationResult:
    """Validate a raw research question. Returns ``ValidationResult``."""
    if not isinstance(question, str):
        return ValidationResult(False, "Question must be text.")

    q = question.strip()
    if not q:
        return ValidationResult(False, "Question is empty.")
    if len(q) < MIN_LENGTH:
        return ValidationResult(
            False, f"Question is too short (min {MIN_LENGTH} characters)."
        )
    if len(q) > MAX_LENGTH:
        return ValidationResult(
            False, f"Question is too long (max {MAX_LENGTH} characters)."
        )
    if len(q.split()) < MIN_WORDS:
        return ValidationResult(
            False, "Question is too vague — please ask a fuller question."
        )
    return ValidationResult(True)
