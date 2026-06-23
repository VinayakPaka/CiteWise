# Individual Contribution Document — Member 1

**Course:** Multi-Agent Orchestration [AI/ML]
**Capstone Project:** CiteWise — A Multi-Agent Research Assistant with Fact-Checking
**Student (this document):** Vinayak Paka
**Student ID:** *[Your ID]*
**Team Member 2:** Vijay Gaurav
**GitHub Repository:** https://github.com/VinayakPaka/CiteWise
**Submission Date:** *[date]*
**My Role:** Research & Knowledge Pipeline (input side)

> Note: This document is submitted ahead of the build phase. It describes the shared CiteWise project and the specific components I am responsible for designing, implementing, and testing. My teammate's parallel document covers the verification, guardrails, and delivery side.

---

## 1. Project Overview (shared)

**Problem.** People researching a topic (students, journalists, analysts) face two costs at once: gathering enough sources, and trusting that the synthesized answer is actually correct. Single-prompt LLM tools hallucinate citations and present unverified claims as facts. A reliable research assistant needs *separation of duties* — the component that gathers information should not be the same one that judges whether it is true.

**Target user.** Students and knowledge workers who need a cited, fact-checked brief on a research question rather than an unverified paragraph.

**What the system does.** Given a research question, CiteWise plans sub-questions, retrieves evidence from the web and a local knowledge base, independently fact-checks every claim, loops back to gather more evidence when claims fail verification, and synthesizes a fully cited report — which a human must approve before it is saved/exported.

**Why multi-agent.** A single LLM cannot reliably self-verify; the adversarial split between the **Researcher** (find support) and the **Fact-Checker** (try to refute) is the core reason the system needs distinct agents.

---

## 2. Full Architecture (shared context)

**Agents (4 total):** Planner → Researcher → Fact-Checker → Synthesizer.

```
START → Planner → Researcher → Fact-Checker
   → [conditional] unsupported claims & retry_count < 2 ? → back to Researcher : → Synthesizer
   → Human Approval (interrupt) → [approved? Save/Export : revise] → END
```

**Shared state:**
```python
class ResearchState(TypedDict):
    question: str
    plan: ResearchPlan
    claims: list[Claim]
    verdicts: list[Verdict]
    verified_claims: list[Claim]
    retry_count: int
    report: FinalReport | None
    approved: bool
```

My components are the **Planner**, the **Researcher**, the **two tool integrations (web search + RAG)**, and the parts of the shared state they populate (`plan`, `claims`).

---

## 3. My Components & Responsibilities

### 3.1 Planner Agent
- Decomposes the research question into 3–6 structured sub-questions plus a research plan.
- **Structured output (Pydantic):**
```python
class ResearchPlan(BaseModel):
    sub_questions: list[str]
    rationale: str
```

### 3.2 Researcher Agent
- For each sub-question, runs web search + RAG retrieval and extracts candidate claims, each tied to a source.
- **Structured output:**
```python
class Claim(BaseModel):
    text: str
    source_url: str
    source_snippet: str
    sub_question: str
```
- Accumulates claims into shared state across loop iterations (supports the retry branch driven by my teammate's Fact-Checker).

### 3.3 Tool Integrations (my 2 tools)
1. **Web Search API** (e.g., Tavily / SerpAPI / DuckDuckGo) — live evidence gathering.
2. **RAG / Vector Store** (e.g., Chroma or FAISS + embeddings) — retrieval over a local document corpus for grounded, citable evidence.
   - **RAG justification:** the product's value is grounding claims in retrievable sources; without retrieval the Fact-Checker would have nothing concrete to verify against.

### 3.4 State & Context Design (my portion)
- Designed the `plan` and `claims` portions of `ResearchState` and the claim-accumulation pattern so evidence builds up cleanly across retry loops without duplication.

---

## 4. My Contribution Mapped to Rubric

| Rubric criterion | Weight | My contribution |
|------------------|--------|-----------------|
| Problem selection & clarity | 10% | Co-defined the problem, target user, and scope |
| Multi-agent architecture | 20% | Design Planner & Researcher agents, prompts, and their handoff schemas |
| LangGraph implementation | 15% | Build the Planner and Researcher nodes and their edges into the graph |
| Tool use & integrations | 10% | **Own both tool integrations: web search + RAG vector store** |
| State, memory, context | 10% | Design the plan/claims state and cross-loop claim accumulation |
| RAG / knowledge grounding | — | **Own the RAG layer and its justification** |
| Demo quality | 10% | Co-deliver the end-to-end runnable demo |
| Individual contribution clarity | 15% | This document + my commit history evidence the input-side work |

---

## 5. Design Decisions I Own (for the viva)

- **Why decompose first (Planner) instead of one search?** Complex questions need multiple sub-queries; decomposition improves retrieval coverage and gives the Fact-Checker discrete claims to judge.
- **Why RAG *and* web search?** Web gives freshness; the local store gives a controlled, citable ground truth used in evaluation.
- **Why attach a source to every claim at extraction time?** So verification and citation are possible downstream — a claim without a source is dropped.

---

## 6. Collaboration Boundary

I hand off `claims` (with sources) into shared state. **Member 2** consumes them in the Fact-Checker, drives the verdict-based routing loop back to my Researcher, and owns synthesis, guardrails, HITL, and evaluation. Integration points: the `Claim` schema and the `retry_count` loop contract, which we designed jointly.

---

## 7. Limitations & Future Work (my side)

- Retrieval quality bounds everything downstream; thin-evidence questions rely on the retry loop.
- Future: source-credibility scoring and caching of retrieved evidence.
