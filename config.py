"""Central configuration for CiteWise.

Loads environment variables from `.env` (if present) and exposes them as module
constants. Import these instead of calling os.getenv throughout the codebase.
"""
import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ModuleNotFoundError:
    # python-dotenv not installed yet (e.g. before `pip install -r requirements.txt`).
    # Fall back to whatever is already in the process environment.
    pass

# --- Observability guard ----------------------------------------------------
# LangChain POSTs traces to LangSmith whenever LANGSMITH_TRACING is truthy. With
# a missing/placeholder key that floods the console with 403 errors, so we keep
# tracing on only when the key actually looks like a LangSmith key (ls.../lsv2_).
_ls_key = (os.getenv("LANGSMITH_API_KEY") or "").strip()
if not _ls_key.startswith("ls"):
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"

# --- LLM provider -----------------------------------------------------------
# Which backend powers the agents: groq | google | ollama | anthropic.
# Switch providers by changing this one value in .env — no code changes needed.
CITEWISE_PROVIDER: str = os.getenv("CITEWISE_PROVIDER", "google").lower()

# A sensible default model per provider (override with CITEWISE_MODEL in .env).
_DEFAULT_MODELS = {
    "groq": "llama-3.3-70b-versatile",
    "google": "gemini-2.0-flash",
    "ollama": "llama3.1",
    "anthropic": "claude-opus-4-8",
}
CITEWISE_MODEL: str = os.getenv("CITEWISE_MODEL") or _DEFAULT_MODELS.get(
    CITEWISE_PROVIDER, "llama-3.3-70b-versatile"
)

# --- Provider API keys (only the active provider's key is required) ---------
ANTHROPIC_API_KEY: str | None = os.getenv("ANTHROPIC_API_KEY")
GROQ_API_KEY: str | None = os.getenv("GROQ_API_KEY")
GOOGLE_API_KEY: str | None = os.getenv("GOOGLE_API_KEY")

# --- Tools ------------------------------------------------------------------
TAVILY_API_KEY: str | None = os.getenv("TAVILY_API_KEY")

# --- Behaviour --------------------------------------------------------------
MAX_RESEARCH_RETRIES: int = int(os.getenv("MAX_RESEARCH_RETRIES", "2"))


def active_provider_key() -> str | None:
    """Return the credential the *current* provider needs (or "local" for Ollama).

    Used by the runners/UI to check the LLM is usable before invoking it.
    """
    return {
        "groq": GROQ_API_KEY,
        "google": GOOGLE_API_KEY,
        "anthropic": ANTHROPIC_API_KEY,
        "ollama": "local",  # Ollama runs locally and needs no API key
    }.get(CITEWISE_PROVIDER)
