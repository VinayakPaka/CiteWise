# Individual Contribution Document

**Course:** Multi-Agent Orchestration [AI/ML]
**Capstone Project:** CiteWise — A Multi-Agent Research Assistant with Fact-Checking
**Student:** *[Your Name]*
**Student ID:** *[Your ID]*
**GitHub Repository:** *[link]*
**Submission Date:** *[date]*

> Note: I am completing this capstone individually. This document is submitted ahead of the build phase and describes the project I have designed and the contributions I will be responsible for delivering. Every component listed — architecture, agents, tools, evaluation, guardrails, and the human-in-the-loop layer — has been designed by me and will be implemented and tested by me. The *project scope* and my *individual contribution* are therefore the same.

---

## 1. Project Overview

**Problem.** People researching a topic (students, journalists, analysts) face two costs at once: gathering enough sources, and trusting that the synthesized answer is actually correct. Single-prompt LLM tools hallucinate citations and present unverified claims as facts. A reliable research assistant needs *separation of duties* — the component that gathers information should not be the same one that judges whether it is true.

**Target user.** Students and knowledge workers who need a cited, fact-checked brief on a research question rather than an unverified paragraph.

**Why it matters.** Unverified AI output is the single biggest blocker to using LLMs for real research. This product makes verification a first-class, observable step.

**What it does.** Given a research question, the system plans sub-questions, retrieves evidence from the web and a local knowledge base, independently fact-checks every claim, loops back to gather more evidence when claims fail verification, and synthesizes a fully cited report — which a human must approve before it is saved/exported.

---

## 2. Why This Problem Requires a Multi-Agent System

A single LLM call cannot reliably *self-verify* — the same model that wrote a claim is biased toward defending it. The problem decomposes into genuinely distinct responsibilities that benefit from separate agents, prompts, and (optionally) models:

1. **Decomposition** is a planning task (break a vague question into answerable sub-questions).
2. **Retrieval** is an information-gathering task (search, rank, ground).
3. **Verification** is an adversarial task — its job is to *refute*, not to help. It must be a separate agent with an opposing objective.
4. **Synthesis** is a writing/citation task.

The adversarial split between **Researcher** (find support) and **Fact-Checker** (try to refute) is the core reason this needs a multi-agent design, and it directly creates the conditional loop in the graph.

---

## 3. System Architecture

### 3.1 Agents (4 meaningful agents with distinct roles)

| Agent | Responsibility | Input → Output |
|-------|---------------|----------------|
| **Planner** | Decomposes the research question into 3–6 structured sub-questions and a research plan | question → `ResearchPlan` |
| **Researcher** | For each sub-question, runs web search + RAG retrieval, extracts candidate claims with source citations | sub-question → `list[Claim]` |
| **Fact-Checker (Critic)** | Adversarially verifies each claim against its sources; assigns a verdict (supported / unsupported / needs-more-evidence) with reasoning | `Claim` → `Verdict` |
| **Synthesizer** | Writes the final cited report using only verified claims | verified claims → `FinalReport` |

### 3.2 LangGraph State

A shared `TypedDict` state object flows through every node:

```python
class ResearchState(TypedDict):
    question: str
    plan: ResearchPlan                 # from Planner
    claims: list[Claim]                # accumulated by Researcher
    verdicts: list[Verdict]            # from Fact-Checker
    verified_claims: list[Claim]
    retry_count: int                   # guards the loop
    report: FinalReport | None
    approved: bool                     # set by human-in-the-loop
```

### 3.3 Graph Flow, Routing & Branching

```
START
  → Planner
  → Researcher
  → Fact-Checker
  → [conditional edge: evaluate verdicts]
        ├── if unsupported/low-confidence claims remain AND retry_count < 2
        │        → back to Researcher   (loop: gather more evidence)
        └── else → Synthesizer
  → Human Approval (interrupt)
  → [conditional edge]
        ├── approved   → Save/Export node → END
        └── rejected   → back to Synthesizer (revise) → END
```

- **Conditional decision #1:** verdict-based loop back to the Researcher (max 2 retries to prevent infinite loops).
- **Conditional decision #2:** human approval gate routes to export or revision.

### 3.4 Structured Outputs (Pydantic)

Every agent handoff uses a schema, never free text:

```python
class Claim(BaseModel):
    text: str
    source_url: str
    source_snippet: str
    sub_question: str

class Verdict(BaseModel):
    claim_text: str
    status: Literal["supported", "unsupported", "needs_more_evidence"]
    confidence: float = Field(ge=0, le=1)
    reasoning: str

class FinalReport(BaseModel):
    summary: str
    sections: list[ReportSection]
    citations: list[str]
```

---

## 4. Tools and Integrations (2+)

1. **Web Search API** (e.g., Tavily / SerpAPI / DuckDuckGo) — live evidence gathering by the Researcher.
2. **RAG / Vector Store** (e.g., Chroma or FAISS + embeddings) — retrieval over a local document corpus for grounded, citable evidence. **RAG is justified here** because the product's entire value is grounding claims in retrievable sources; without retrieval the Fact-Checker would have nothing concrete to verify against.

*(Optional 3rd tool: a citation/URL validator that confirms a source URL resolves and contains the cited snippet.)*

---

## 5. Evaluation (5+ test cases)

I will build an evaluation harness with a labeled question set. Each case is designed to check specific behaviors:

| # | Test case | What it validates |
|---|-----------|-------------------|
| 1 | Well-known factual question (verifiable answer) | End-to-end correctness + correct citations |
| 2 | Question with a common misconception | Fact-Checker rejects the false claim |
| 3 | Question needing multiple sub-questions | Planner decomposition + state accumulation |
| 4 | Question with thin evidence | Loop-back to Researcher triggers (retry path) |
| 5 | Out-of-scope / unsafe question | Guardrail refusal fires |
| 6 | Ambiguous question | Graceful handling / clarification |

Metrics to be tracked: claim verification accuracy, citation validity rate, retry-loop frequency, and refusal correctness. Failures will be logged, analyzed, and fed back into prompt revisions (documented in the repo's `eval/` folder).

---

## 6. Debugging & Observability

- **LangSmith tracing** will be enabled for every run — full visibility into each node's inputs/outputs, token usage, and the routing decisions taken.
- Structured intermediate logs will be printed for the plan, each claim, each verdict, and each routing branch, so the loop behavior is inspectable without LangSmith.

---

## 7. Guardrails & Human-in-the-Loop

**Guardrails:**
- **Input validation** — reject empty/oversized/non-research inputs.
- **Policy/refusal check** — a guardrail node refuses unsafe or out-of-scope requests before any tool call.
- **Citation enforcement** — the Synthesizer may only use claims marked `supported`; unsupported claims are excluded by construction.
- **Loop guard** — `retry_count` cap prevents infinite research loops.

**Human-in-the-loop (required for high-impact action):**
- The **final report export/save** is the high-impact action. The graph **interrupts** before saving and presents the draft for human approval. The human can **approve** (→ export) or **reject** (→ Synthesizer revises). Nothing is persisted/exported without explicit approval.

---

## 8. My Individual Contributions (mapped to rubric)

| Rubric criterion | Weight | My contribution (planned & owned end-to-end) |
|------------------|--------|-----------------|
| Problem selection & clarity | 10% | Chose and scoped the verified-research problem; defined target user and value |
| Multi-agent architecture | 20% | Design all 4 agents, their roles, prompts, and the adversarial Researcher/Fact-Checker split |
| LangGraph implementation | 15% | Build the full graph: state schema, nodes, edges, both conditional routers, and the loop guard |
| Tool use & integrations | 10% | Integrate web search + RAG vector store (+ optional citation validator) |
| State, memory, context | 10% | Design the `ResearchState` and claim/verdict accumulation across the loop |
| Evaluation & debugging | 10% | Build the 6-case eval harness, LangSmith tracing, failure analysis, prompt improvements |
| Guardrails & HITL | 10% | Implement validation, refusal node, citation enforcement, and the approval interrupt |
| Demo quality | 10% | Deliver an end-to-end runnable system with sample questions and clear cited output |
| Individual contribution clarity | 15% | Entire project designed and built solo; this document + repo history will evidence it |

---

## 9. Design Decisions & Trade-offs (for the viva)

- **Why a separate Fact-Checker agent instead of self-check?** To create a genuine adversarial objective — the verifier is prompted to refute, removing the author's bias.
- **Why cap retries at 2?** Cost/latency vs. completeness; prevents infinite loops on genuinely unanswerable questions. Logged so reviewers can see when it fires.
- **Why RAG *and* web search?** Web gives freshness; the local store gives a controlled, citable ground truth for evaluation.
- **Why HITL on export only?** That is the single high-impact, hard-to-reverse action; gating earlier steps would add friction without reducing risk.

---

## 10. Limitations & Future Work

- Verification quality is bounded by source quality; the Fact-Checker can be misled by a confident but wrong source.
- No long-term memory across sessions (each research run is independent).
- Future: multi-verifier voting, source-credibility scoring, and caching of verified claims.

---

## 11. Repository Map

```
/agents        planner.py, researcher.py, fact_checker.py, synthesizer.py
/graph         state.py, graph.py (nodes, edges, routers)
/tools         web_search.py, rag_store.py, citation_validator.py
/schemas       models.py (Pydantic)
/eval          test_cases.py, run_eval.py, results/
/guardrails    validation.py, policy.py
main.py        end-to-end demo entry point
README.md
```
