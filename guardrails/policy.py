"""Policy / refusal guardrail.

Member 2. Refuses unsafe or out-of-scope requests *before* any tool call. This
is the second line of defence after input validation. The check is intentionally
deterministic (keyword/pattern based) so it is reliable, fast, and testable in
the eval harness without burning API calls.

Scope: CiteWise is a research-brief assistant. It refuses requests that seek
operational help with weapons, explosives, illicit drugs/cyber-attacks, or other
clearly harmful "how to cause harm" instructions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Patterns that signal a request for operational harm. Kept narrow on purpose:
# we want to refuse "how to build a bomb", not "history of nuclear weapons".
_UNSAFE_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bhow to (make|build|create|synthesi[sz]e)\b.*\b(bomb|explosive|weapon|gun|poison|nerve agent)\b", re.I),
    re.compile(r"\b(build|make|construct|assemble)\b.*\b(bomb|explosive device|ied|pipe bomb)\b", re.I),
    re.compile(r"\b(synthesi[sz]e|cook|manufacture)\b.*\b(meth|methamphetamine|fentanyl|heroin|cocaine)\b", re.I),
    re.compile(r"\bhow to\b.*\b(hack|ddos|sql inject|ransomware|keylogger)\b.*\b(into|attack|someone|target)?\b", re.I),
    re.compile(r"\b(kill|poison|harm|hurt)\b.*\b(someone|a person|people|my)\b", re.I),
    re.compile(r"\bbioweapon|chemical weapon|dirty bomb\b.*\b(make|build|deploy|create)\b", re.I),
]


@dataclass
class PolicyResult:
    allowed: bool
    reason: str | None = None


def check_policy(question: str) -> PolicyResult:
    """Return whether ``question`` is in-scope and safe to research."""
    q = question.strip()
    for pattern in _UNSAFE_PATTERNS:
        if pattern.search(q):
            return PolicyResult(
                False,
                "This request asks for operational instructions to cause harm, "
                "which is outside CiteWise's scope. I can research the topic at a "
                "factual/historical level instead.",
            )
    return PolicyResult(True)
