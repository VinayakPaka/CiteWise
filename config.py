"""Central configuration for CiteWise.

Loads environment variables from `.env` (if present) and exposes them as module
constants. Import these instead of calling os.getenv throughout the codebase.
"""
import os
import secrets
import sys

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

# --- LLM provider(s) --------------------------------------------------------
# Primary backend for the agents: groq | google | ollama | anthropic | cerebras
# | mistral. Optionally list fallback providers (comma-separated) that are tried,
# in order, when the primary errors or rate-limits — e.g. cerebras -> mistral.
CITEWISE_PROVIDER: str = os.getenv("CITEWISE_PROVIDER", "cerebras").lower()
CITEWISE_FALLBACK_PROVIDERS: list[str] = [
    p.strip().lower()
    for p in os.getenv("CITEWISE_FALLBACK_PROVIDERS", "mistral").split(",")
    if p.strip() and p.strip().lower() != CITEWISE_PROVIDER
]

# A sensible default model per provider. Override the primary with CITEWISE_MODEL,
# or any provider with CITEWISE_<PROVIDER>_MODEL (e.g. CITEWISE_MISTRAL_MODEL).
_DEFAULT_MODELS = {
    "groq": "llama-3.3-70b-versatile",
    "google": "gemini-2.0-flash",
    "ollama": "llama3.1",
    "anthropic": "claude-opus-4-8",
    "cerebras": "gpt-oss-120b",          # free ~1M tokens/day, native tool calling
    "mistral": "mistral-small-latest",   # free tier, supports tool calling
}


def model_for(provider: str) -> str:
    """Resolve the model id for a provider.

    Precedence: CITEWISE_<PROVIDER>_MODEL > CITEWISE_MODEL (primary only) > default.
    """
    provider = provider.lower()
    specific = os.getenv(f"CITEWISE_{provider.upper()}_MODEL")
    if specific:
        return specific
    if provider == CITEWISE_PROVIDER and os.getenv("CITEWISE_MODEL"):
        return os.getenv("CITEWISE_MODEL")  # type: ignore[return-value]
    return _DEFAULT_MODELS.get(provider, "llama-3.3-70b-versatile")


# Model on the primary provider (shown in the UI; used by default).
CITEWISE_MODEL: str = model_for(CITEWISE_PROVIDER)

# --- Provider API keys (only the providers you actually use need a key) ------
ANTHROPIC_API_KEY: str | None = os.getenv("ANTHROPIC_API_KEY")
GROQ_API_KEY: str | None = os.getenv("GROQ_API_KEY")
GOOGLE_API_KEY: str | None = os.getenv("GOOGLE_API_KEY")
CEREBRAS_API_KEY: str | None = os.getenv("CEREBRAS_API_KEY")
MISTRAL_API_KEY: str | None = os.getenv("MISTRAL_API_KEY")

# --- Tools ------------------------------------------------------------------
TAVILY_API_KEY: str | None = os.getenv("TAVILY_API_KEY")

# --- Web app: accounts, sessions & history ----------------------------------
# Login is email + password. A successful login mints a JWT (HS256) that is
# stored in an httponly cookie; CITEWISE_JWT_SECRET signs it. When it is not set
# we generate a RANDOM per-process secret instead of shipping a fixed default —
# a hardcoded key in the source would let anyone who reads it forge a login. The
# trade-off is that logins don't survive a server restart; set a fixed secret in
# .env to persist sessions in production:
#   python -c "import secrets; print(secrets.token_urlsafe(48))"
JWT_SECRET: str = os.getenv("CITEWISE_JWT_SECRET") or secrets.token_urlsafe(48)
JWT_ALG: str = "HS256"
if not os.getenv("CITEWISE_JWT_SECRET"):
    print(
        "[citewise] CITEWISE_JWT_SECRET not set — using a random per-process secret; "
        "logins won't survive a restart. Set it in .env to persist sessions.",
        file=sys.stderr,
    )

# Send the session cookie only over HTTPS. Defaults to False so the local
# http://127.0.0.1 demo works; set CITEWISE_COOKIE_SECURE=true behind HTTPS.
COOKIE_SECURE: bool = os.getenv("CITEWISE_COOKIE_SECURE", "false").lower() in {"1", "true", "yes"}

# Guest login (type a name, no password). Kept on by default so flaky demo Wi-Fi
# or a forgotten password never leaves you with an unusable app on stage.
ALLOW_GUEST: bool = os.getenv("CITEWISE_ALLOW_GUEST", "true").lower() in {"1", "true", "yes"}

# Where the local accounts + research history live (SQLite file).
DB_PATH: str = os.getenv("CITEWISE_DB", "citewise.db")

# How long a login stays valid (JWT lifetime + cookie max-age), in days.
SESSION_DAYS: int = int(os.getenv("CITEWISE_SESSION_DAYS", "30"))


# --- Behaviour --------------------------------------------------------------
MAX_RESEARCH_RETRIES: int = int(os.getenv("MAX_RESEARCH_RETRIES", "2"))

# Evidence sufficiency. The Researcher↔Fact-Checker loop keeps going until the
# report has at least MIN_VERIFIED_CLAIMS verified claims drawn from at least
# MIN_DISTINCT_SOURCES distinct sources — so a one-source, few-claim draft (which
# is what a sparse first search produces on some topics) is treated as "thin" and
# triggers more research instead of being written up as if it were complete.
MIN_VERIFIED_CLAIMS: int = int(os.getenv("CITEWISE_MIN_VERIFIED_CLAIMS", "6"))
MIN_DISTINCT_SOURCES: int = int(os.getenv("CITEWISE_MIN_DISTINCT_SOURCES", "4"))


def evidence_is_thin(verified_claims) -> bool:
    """True when the verified evidence is too sparse to write a complete report.

    Shared by the Researcher (decide whether to broaden the next pass) and the
    graph router (decide whether to loop back at all), so both use one definition.
    """
    verified = verified_claims or []
    distinct_sources = {c.source_url for c in verified if c.source_url}
    return len(verified) < MIN_VERIFIED_CLAIMS or len(distinct_sources) < MIN_DISTINCT_SOURCES


def provider_key(provider: str) -> str | None:
    """Return the credential a provider needs ("local" for Ollama, which needs none)."""
    return {
        "groq": GROQ_API_KEY,
        "google": GOOGLE_API_KEY,
        "anthropic": ANTHROPIC_API_KEY,
        "cerebras": CEREBRAS_API_KEY,
        "mistral": MISTRAL_API_KEY,
        "ollama": "local",  # Ollama runs locally and needs no API key
    }.get(provider.lower())


def active_provider_key() -> str | None:
    """Credential the *primary* provider needs. Used to pre-flight before a run."""
    return provider_key(CITEWISE_PROVIDER)


def llm_chain() -> list[str]:
    """Primary + reachable fallback providers, in order, that have a key configured."""
    seen: set[str] = set()
    chain: list[str] = []
    for provider in [CITEWISE_PROVIDER, *CITEWISE_FALLBACK_PROVIDERS]:
        if provider not in seen and provider_key(provider):
            seen.add(provider)
            chain.append(provider)
    return chain
