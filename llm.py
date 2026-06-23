"""LLM provider factory.

CiteWise is provider-agnostic: every agent gets its chat model from ``get_llm``,
and the provider is chosen in ``.env`` via ``CITEWISE_PROVIDER``. This lets the
project run on a free model (Groq, Gemini, or local Ollama) today and switch to
Claude by changing one line once Anthropic credits are available — no code edits.

Each provider's package is imported lazily so only the one you actually use needs
to be installed.
"""
from __future__ import annotations

from config import CITEWISE_MODEL, CITEWISE_PROVIDER


def get_llm(max_tokens: int = 2048, temperature: float = 0.0):
    """Return a LangChain chat model for the configured provider.

    All agents call this and then ``.with_structured_output(...)``, so switching
    providers is a one-line ``.env`` change.
    """
    provider = CITEWISE_PROVIDER

    if provider == "groq":
        from langchain_groq import ChatGroq

        return ChatGroq(
            model=CITEWISE_MODEL, max_tokens=max_tokens, temperature=temperature
        )

    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=CITEWISE_MODEL,
            max_output_tokens=max_tokens,
            temperature=temperature,
        )

    if provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(model=CITEWISE_MODEL, temperature=temperature)

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        # Opus 4.x rejects temperature/top_p, so we deliberately don't pass them.
        return ChatAnthropic(model=CITEWISE_MODEL, max_tokens=max_tokens)

    raise ValueError(
        f"Unknown CITEWISE_PROVIDER={provider!r}. "
        "Use one of: groq, google, ollama, anthropic."
    )
