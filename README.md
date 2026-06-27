# CiteWise — A Multi-Agent Research Assistant with Fact-Checking

> Capstone Project · Multi-Agent Orchestration [AI/ML] · Built with **LangGraph + Claude**

CiteWise turns a research question into a **cited, fact-checked brief**. It separates
*finding* information from *verifying* it: a **Researcher** agent gathers evidence, and an
independent **Fact-Checker** agent adversarially verifies every claim before it reaches the
final report. A human approves the report before it is exported.

## Why a multi-agent system?

A single LLM cannot reliably self-verify — the model that wrote a claim is biased toward
defending it. CiteWise splits the work across four specialised agents with distinct roles
and an **adversarial Researcher ↔ Fact-Checker loop** that re-gathers evidence whenever a
claim fails verification.

## Architecture

```
START → Planner → Researcher → Fact-Checker
   → [conditional] unsupported claims AND retry_count < MAX ? → back to Researcher
                                                              : → Synthesizer
   → Human Approval (interrupt) → [approved? Export : revise] → END
```

| Agent | Responsibility |
|-------|----------------|
| **Planner** | Decomposes the question into 3–6 answerable sub-questions |
| **Researcher** | Live web search with source-quality filtering; extracts claims with their sources |
| **Fact-Checker** | Adversarially verifies each claim against its source |
| **Synthesizer** | Writes the cited report from verified claims only |

## Tech stack

- **Orchestration:** LangGraph — shared state, nodes, conditional edges, retry loop
- **LLM:** pluggable provider (`llm.py`) with an **automatic fallback chain** — default **Cerebras** (`gpt-oss-120b`, free ~1M tokens/day) failing over to **Mistral**; also supports Groq, Gemini, Ollama, Claude. Chosen via `CITEWISE_PROVIDER` / `CITEWISE_FALLBACK_PROVIDERS` in `.env`
- **Tools:** Tavily web search behind an authority-ranked **source-quality gate** (`tools/source_quality.py`) that blocks social media / video / forums / wikis and prefers primary sources (gov, edu, IGOs, peer-reviewed)
- **Structured outputs:** Pydantic schemas on every agent handoff
- **Observability:** LangSmith tracing
- **Guardrails:** input validation, refusal node, source-quality filtering, citation enforcement, evidence-sufficiency + retry-loop caps
- **Human-in-the-loop:** approval interrupt before the final report is exported
- **Web app:** Aurora single-page UI, email + password sign-in (JWT) or guest login, and a
  per-user research **history** stored in local SQLite (`webapp/db.py`)

## Repository layout

```
agents/       planner.py, researcher.py, fact_checker.py, synthesizer.py
graph/        state.py (shared state), graph.py (nodes, edges, routers)
tools/        web_search.py, source_quality.py, citation_validator.py
schemas/      models.py (Pydantic contract shared by all agents)
guardrails/   validation.py, policy.py
eval/         test_cases.py, run_eval.py
webapp/       server.py (FastAPI API), auth.py (email + password, JWT cookie),
              db.py (SQLite accounts + history), static/ (Aurora UI: index.html, app.js)
llm.py        LLM provider factory (Groq / Gemini / Ollama / Claude)
config.py     environment / model / provider configuration
main.py       command-line entry point
run_web.py    web app launcher
```

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows (use: source .venv/bin/activate on macOS/Linux)
pip install -r requirements.txt
copy .env.example .env           # then fill in your API keys
```

You need a free **LLM API key** and a free **Tavily API key** (web search —
https://app.tavily.com). The default LLM is **Cerebras** (free ~1M tokens/day, no
card — https://cloud.cerebras.ai) with automatic failover to **Mistral**
(https://console.mistral.ai); add either/both key in `.env`. To use a different
backend, set `CITEWISE_PROVIDER` / `CITEWISE_FALLBACK_PROVIDERS` (also supports
Groq, Gemini, local Ollama, or Claude). LangSmith is optional for tracing.

## Running

### Web UI (recommended)

```bash
python run_web.py        # then open http://127.0.0.1:8000
```

A polished single-page app: **sign in** (email or guest), type a question, watch
the agents work live, review the fact-checked draft with colour-coded verdicts, and
**approve or reject** before the report is exported. Every run is saved to your
**history** sidebar so you can re-open past research anytime.

**Login:** Sign up with an **email + password** — the server hashes the password
(PBKDF2-HMAC-SHA256) and stores a signed **JWT** in an httponly cookie. A no-password
**guest login** is also kept (set `CITEWISE_ALLOW_GUEST=false` to require an account)
so the app always works — handy for a live demo. Set `CITEWISE_JWT_SECRET` to a long
random string in production. Accounts and history live in a local `citewise.db`
SQLite file (git-ignored).

### Command line

```bash
python main.py                       # run the sample question end-to-end
python main.py "Your question here"  # research your own question
```

The graph runs Planner → Researcher → Fact-Checker → Synthesizer, loops back to
the Researcher when claims fail verification (capped by `MAX_RESEARCH_RETRIES`),
then **pauses for your approval** before exporting the report to `./output`.

### Evaluation

```bash
python -m eval.run_eval            # 6-case harness (LLM cases need API keys)
python -m eval.run_eval --offline  # deterministic guardrail cases only (no keys)
```

Every node, claim, verdict and routing decision is emitted as a structured JSON
log line; set `LANGSMITH_TRACING=true` + `LANGSMITH_API_KEY` for full tracing.

> **Status:** end-to-end pipeline implemented. Work happens on feature branches
> off `main`. See `Individual_Contribution_*.md` for the per-member breakdown.

## Team

| Member | Area |
|--------|------|
| **Vinayak Paka** | Research Pipeline — Planner, Researcher, web search, source-quality filtering, input-side state |
| **Vijay Gaurav** | Verification, Guardrails & Delivery — Fact-Checker, Synthesizer, routing, guardrails, evaluation |
