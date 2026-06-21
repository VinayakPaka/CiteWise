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

# --- LLM ---
ANTHROPIC_API_KEY: str | None = os.getenv("ANTHROPIC_API_KEY")
# Default to the most capable Claude model; override via .env for cheaper dev runs.
CITEWISE_MODEL: str = os.getenv("CITEWISE_MODEL", "claude-opus-4-8")

# --- Tools ---
TAVILY_API_KEY: str | None = os.getenv("TAVILY_API_KEY")

# --- Behaviour ---
MAX_RESEARCH_RETRIES: int = int(os.getenv("MAX_RESEARCH_RETRIES", "2"))
