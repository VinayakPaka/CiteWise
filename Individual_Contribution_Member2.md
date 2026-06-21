# Individual Contribution Document — Member 2

**Course:** Multi-Agent Orchestration [AI/ML]
**Capstone Project:** CiteWise — A Multi-Agent Research Assistant with Fact-Checking
**Student (this document):** *[Member 2 Name]*
**Student ID:** *[Your ID]*
**Team Member 1:** *[Member 1 Name]*
**GitHub Repository:** *[link]*
**Submission Date:** *[date]*
**My Role:** Verification, Guardrails & Delivery (output side)

> Note: This document is submitted ahead of the build phase. It describes the shared CiteWise project and the specific components I am responsible for designing, implementing, and testing. My teammate's parallel document covers the planning and research/retrieval side.

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

My components are the **Fact-Checker**, the **Synthesizer**, the **graph routing/branching** (both conditional decisions), the **guardrails**, the **human-in-the-loop** approval, and the **evaluation + observability** layer.

---

## 3. My Components & Responsibilities

### 3.1 Fact-Checker (Critic) Agent
- Adversarially verifies each claim against its source; its objective is to *refute*, not to assist — removing the author's bias.
- **Structured output (Pydantic):**
```python
class Verdict(BaseModel):
    claim_text: str
    status: Literal["supported", "unsupported", "needs_more_evidence"]
    confidence: float = Field(ge=0, le=1)
    reasoning: str
```

### 3.2 Synthesizer Agent
- Writes the final cited report using **only** claims marked `supported`.
```python
class FinalReport(BaseModel):
    summary: str
    sections: list[ReportSection]
    citations: list[str]
```

### 3.3 Graph Routing & Branching (I own the conditional logic)
- **Conditional #1:** verdict-based loop back to the Researcher when unsupported/low-confidence claims remain **and** `retry_count < 2` (loop guard prevents infinite loops).
- **Conditional #2:** human-approval gate routes to export or back to the Synthesizer for revision.

### 3.4 Guardrails
- **Input validation** — reject empty/oversized/non-research inputs.
- **Policy/refusal node** — refuses unsafe or out-of-scope requests before any tool call.
- **Citation enforcement** — Synthesizer may only use `supported` claims by construction.
- **Loop guard** — `retry_count` cap.

### 3.5 Human-in-the-Loop (required high-impact control)
- The final report export/save is the high-impact action. The graph **interrupts** before saving and presents the draft for human approval (approve → export, reject → revise). Nothing is persisted without explicit approval.

### 3.6 Evaluation & Observability
- A 6-case evaluation harness (verifiable fact, misconception, multi-sub-question, thin-evidence retry, unsafe-refusal, ambiguous).
- **LangSmith tracing** + structured logs for every node, claim, verdict, and routing decision.

---

## 4. My Contribution Mapped to Rubric

| Rubric criterion | Weight | My contribution |
|------------------|--------|-----------------|
| Problem selection & clarity | 10% | Co-defined the problem, target user, and scope |
| Multi-agent architecture | 20% | Design Fact-Checker & Synthesizer agents and the adversarial split |
| LangGraph implementation | 15% | **Own the graph routing: both conditional edges, the loop guard, graph assembly** |
| State, memory, context | 10% | Design the verdicts/verified-claims/report/approval state |
| Evaluation & debugging | 10% | **Own the 6-case eval harness, LangSmith tracing, failure analysis** |
| Guardrails & HITL | 10% | **Own validation, refusal, citation enforcement, and the approval interrupt** |
| Demo quality | 10% | Co-deliver the end-to-end runnable demo |
| Individual contribution clarity | 15% | This document + my commit history evidence the output-side work |

---

## 5. Design Decisions I Own (for the viva)

- **Why a separate Fact-Checker instead of self-check?** A separate adversarial objective removes the author's bias toward defending its own claims.
- **Why cap retries at 2?** Cost/latency vs. completeness; prevents infinite loops on unanswerable questions. The cap is logged so reviewers can see when it fires.
- **Why HITL on export only?** That is the single high-impact, hard-to-reverse action; gating earlier steps would add friction without reducing risk.
- **Why citation enforcement by construction?** Excluding unsupported claims at the data level is stronger than asking the LLM not to use them.

---

## 6. Collaboration Boundary

I consume `claims` (with sources) produced by **Member 1's** Researcher, judge them, and drive the routing loop back to their Researcher when evidence is thin. I then synthesize, gate with HITL, and deliver output. Integration points: the `Claim` schema and the `retry_count` loop contract, which we designed jointly.

---

## 7. Limitations & Future Work (my side)

- Verification quality is bounded by source quality; a confident-but-wrong source can mislead the Fact-Checker.
- Future: multi-verifier voting and source-credibility scoring before accepting a verdict.
