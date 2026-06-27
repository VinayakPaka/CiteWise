"""LLM provider factory with an automatic fallback chain.

CiteWise is provider-agnostic: every agent gets its chat model from this module,
and the provider(s) are chosen in ``.env``. ``CITEWISE_PROVIDER`` is the primary;
``CITEWISE_FALLBACK_PROVIDERS`` is an optional ordered list tried when the primary
errors or rate-limits (e.g. ``cerebras`` -> ``mistral``).

Agents call ``get_structured_llm(Schema, ...)`` then ``.invoke(...)``; the returned
object runs the primary's structured-output model and, on any failure, falls
through to the next provider in the chain. Each provider's package is imported
lazily so only the ones you actually use need to be installed.
"""
from __future__ import annotations

import os

import config
from observability import log_event

# Free tiers rate-limit hard. The provider SDKs retry HTTP 429s with backoff that
# honours the server's Retry-After. The LAST provider in the chain gets this full
# budget; earlier providers fail over fast (see get_structured_llm) so a throttled
# primary spills to the next provider instead of stalling on retries.
LLM_MAX_RETRIES = int(os.getenv("CITEWISE_LLM_MAX_RETRIES", "6"))

# Penalise token repetition. Smaller free models (notably llama-3.1-8b-instant)
# otherwise loop the same item forever on list/structured output until the
# tool-call JSON overruns and the provider rejects it. 0 disables it.
LLM_FREQUENCY_PENALTY = float(os.getenv("CITEWISE_FREQUENCY_PENALTY", "0.8"))
# Discourage re-using any token already seen — also helps break repetition loops.
LLM_PRESENCE_PENALTY = float(os.getenv("CITEWISE_PRESENCE_PENALTY", "0.3"))


def _build_chat(provider: str, max_tokens: int, temperature: float, max_retries: int):
    """Construct a single LangChain chat model for one provider."""
    provider = provider.lower()
    model = config.model_for(provider)

    if provider == "groq":
        from langchain_groq import ChatGroq

        kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "max_retries": max_retries,
        }
        penalties = {}
        if LLM_FREQUENCY_PENALTY:
            penalties["frequency_penalty"] = LLM_FREQUENCY_PENALTY
        if LLM_PRESENCE_PENALTY:
            penalties["presence_penalty"] = LLM_PRESENCE_PENALTY
        if penalties:
            kwargs["model_kwargs"] = penalties
        return ChatGroq(**kwargs)

    if provider == "cerebras":
        # Cerebras is OpenAI-compatible — point ChatOpenAI at its endpoint.
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=model,
            api_key=config.CEREBRAS_API_KEY,
            base_url="https://api.cerebras.ai/v1",
            max_tokens=max_tokens,
            temperature=temperature,
            max_retries=max_retries,
        )

    if provider == "mistral":
        from langchain_mistralai import ChatMistralAI

        return ChatMistralAI(
            model=model,
            api_key=config.MISTRAL_API_KEY,
            max_tokens=max_tokens,
            temperature=temperature,
            max_retries=max_retries,
        )

    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=model, max_output_tokens=max_tokens, temperature=temperature
        )

    if provider == "ollama":
        from langchain_ollama import ChatOllama

        # Ollama's max-output-tokens knob is ``num_predict`` — pass the caller's
        # cap through so Ollama-backed agents honour it (otherwise reports can be
        # truncated at the model default).
        return ChatOllama(model=model, temperature=temperature, num_predict=max_tokens)

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        # Pass temperature like every other provider so the same get_llm(temperature=…)
        # call behaves consistently across the abstraction (Claude accepts temperature
        # for standard, non-thinking requests).
        return ChatAnthropic(model=model, max_tokens=max_tokens, temperature=temperature)

    raise ValueError(
        f"Unknown provider {provider!r}. Use one of: "
        "groq, google, ollama, anthropic, cerebras, mistral."
    )


def _structured(chat, provider: str, schema):
    """Apply structured output, forcing tool calling where that's most reliable."""
    if provider == "cerebras":
        # OpenAI-compatible custom endpoint: tool calling is more reliable here
        # than OpenAI's strict json_schema mode.
        return chat.with_structured_output(schema, method="function_calling")
    return chat.with_structured_output(schema)


class _FallbackRunnable:
    """Run the primary structured LLM; on any error, fall through to the next.

    Agents only call ``.invoke()``, so this thin shim is enough — and it logs
    which provider served the call and when it failed over.
    """

    def __init__(self, options: list[tuple[str, object]]):
        self.options = options

    def invoke(self, *args, **kwargs):
        last_exc = None
        for i, (name, runnable) in enumerate(self.options):
            try:
                result = runnable.invoke(*args, **kwargs)
                if i > 0:
                    log_event("llm_fallback_used", provider=name)
                return result
            except Exception as exc:  # noqa: BLE001 — fail over on any provider error
                last_exc = exc
                log_event("llm_provider_failed", provider=name, error=str(exc)[:200])
        raise last_exc  # type: ignore[misc]


def get_llm(max_tokens: int = 2048, temperature: float = 0.0):
    """Return the primary provider's chat model (no fallback, no schema)."""
    return _build_chat(config.CITEWISE_PROVIDER, max_tokens, temperature, LLM_MAX_RETRIES)


def get_structured_llm(schema, max_tokens: int = 2048, temperature: float = 0.0):
    """Structured-output caller across the primary + fallback providers.

    Returns an object with ``.invoke(messages)`` that yields a validated ``schema``
    instance, transparently failing over when the primary errors or rate-limits.
    """
    chain = config.llm_chain()
    options: list[tuple[str, object]] = []
    for i, provider in enumerate(chain):
        # Only the LAST provider gets the full retry budget; earlier ones DON'T
        # retry at all, so a rate-limited primary fails over instantly instead of
        # waiting out its Retry-After before spilling to the next provider.
        retries = LLM_MAX_RETRIES if i == len(chain) - 1 else 0
        try:
            chat = _build_chat(provider, max_tokens, temperature, retries)
            options.append((provider, _structured(chat, provider, schema)))
        except Exception as exc:  # noqa: BLE001
            # A provider whose package isn't installed (ImportError) or that can't
            # be constructed shouldn't sink the whole chain — skip it and let the
            # remaining providers serve. Only fatal if NOTHING is usable (below).
            log_event("llm_provider_unavailable", provider=provider, error=str(exc)[:200])

    if not options:
        raise ValueError(
            f"No usable LLM provider in the chain {chain!r}. Set an API key and "
            "install the package for at least one of them (e.g. "
            "`pip install langchain-mistralai` for the Mistral fallback)."
        )
    if len(options) == 1:
        return options[0][1]
    return _FallbackRunnable(options)
