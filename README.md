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
| **Researcher** | Web search + RAG retrieval; extracts claims with their sources |
| **Fact-Checker** | Adversarially verifies each claim against its source |
| **Synthesizer** | Writes the cited report from verified claims only |

## Tech stack

- **Orchestration:** LangGraph — shared state, nodes, conditional edges, retry loop
- **LLM:** Claude (`claude-opus-4-8`) via `langchain-anthropic`
- **Tools:** Tavily web search + Chroma vector store (RAG)
- **Structured outputs:** Pydantic schemas on every agent handoff
- **Observability:** LangSmith tracing
- **Guardrails:** input validation, refusal node, citation enforcement, loop cap
- **Human-in-the-loop:** approval interrupt before the final report is exported

## Repository layout

```
agents/       planner.py, researcher.py, fact_checker.py, synthesizer.py
graph/        state.py (shared state), graph.py (nodes, edges, routers)
tools/        web_search.py, rag_store.py, citation_validator.py
schemas/      models.py (Pydantic contract shared by all agents)
guardrails/   validation.py, policy.py
eval/         test_cases.py, run_eval.py
config.py     environment / model configuration
main.py       end-to-end demo entry point
```

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows (use: source .venv/bin/activate on macOS/Linux)
pip install -r requirements.txt
copy .env.example .env           # then fill in your API keys
```

You need an **Anthropic API key** (LLM) and a **Tavily API key** (web search).
LangSmith is optional but recommended for tracing/observability.

## Running

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
| **Vinayak Paka** | Research & Knowledge Pipeline — Planner, Researcher, web search, RAG, input-side state |
| *[Member 2]* | Verification, Guardrails & Delivery — Fact-Checker, Synthesizer, routing, guardrails, evaluation |
