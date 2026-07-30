# Sprint 6 — GPT / Conversation Intelligence Engine (Volume 07) · Architecture & Milestone Plan

> **Process (identical to Sprints 1–5):** Architecture → Milestones → Review → Approval →
> Implementation, one milestone at a time with a review gate after each.
>
> **Status:** 🔨 **Approved — implementing milestone by milestone.** **M1 ✅ · M2 ✅ · M3 (Retrieval
> Orchestrator): ✅ done — awaiting review.** M4–M8 pending.
>
> **Sprint sequence:** Sprint 1 (Forward Testing `v0.1.0`) → Sprint 2 (Historical Memory `v0.2.0`)
> → Sprint 3 (Similarity `v0.3.0`) → Sprint 4 (Learning `v0.4.0`) → Sprint 5 (Decision Intelligence
> `v0.5.0`) → **Sprint 6 (Conversation Intelligence `v0.6.0`, proposed)**.

**Related:** Vol 07 (GPT/Conversation assistant), the [Decision Intelligence Engine](../decision-intelligence-engine.md)
(Sprint 5), Vol 03/24 (SEBI posture / compliance), [ADRs](../adr/) 0002/0003/0006/0018/0023–0028,
and `docs/RESULTS.md` (the honest scoreboard this sprint must not contradict).

---

## 0. Ground truth & disambiguation
Sprint 5 delivered the **Decision Intelligence Engine** — one explainable, evidence-bound object per
prediction (compose → explain → confidence → `/intelligence/*`). Sprint 6 adds a **read-only
conversational explanation layer** on top of it: a user asks in natural language to *explain* an
existing prediction / its evidence / historical behaviour / similarity / learning / system status,
and the LLM **explains only**.

⚠️ **Scope collision — a chat layer already exists.** `app/chat/` (`TradingAssistant`,
`LLMAssistant`) is a **pre-Decision-Intelligence** rule-based + optional-LLM assistant. The Sprint 6
engine is a **new, separate** package `app/conversation/` that consumes **only** the completed
Decision Intelligence Engine (never the models directly). The legacy `app/chat/` stays a separate
concern (a later sprint may supersede it — never a destructive edit).

## 1. Core principles (each enforced by tests)
The GPT / Conversation layer **never** predicts, retrains, recalculates confidence, generates market
signals, gives buy/sell advice, modifies data, bypasses retrieval, or hallucinates. Every answer
originates from **existing deterministic system outputs**; where information does not exist it
reports `INSUFFICIENT_DATA` / `NOT_AVAILABLE` / `NOT_SUPPORTED`. Guarantees: deterministic retrieval,
read-only, explainable, traceable (**citations**), reproducible, enterprise-modular, conversation
memory, auditable, graceful degradation. Imports **neither** the Prediction nor the Outcome engine
(AST-guarded); the LLM performs **explanation only**.

## 2. Architecture position (the requested chain)
```
  User → Conversation Engine → Intent Detection → Retrieval Orchestrator
       → Decision Intelligence API (/intelligence/*)  [→ Prediction · Memory · Similarity · Learning]
       → Prompt Builder → LLM Adapter (explanation only) → User Response
```
The GPT layer talks **only** to the completed Decision Intelligence Engine — never to prediction
models. Package: `app/conversation/` (peer of `app/decision_intelligence/`) + `app/api/chat.py` (a
thin router at M7). Read-only; no writes to any prior table.

## 3. Milestone breakdown (plan-gated)
| M | Title | Scope | Status |
|---|---|---|---|
| **M1** ✅ | Conversation Domain Model | messages, sessions, conversation context, citations, metadata, serialization, versioning. **No GPT/retrieval/API/prompts.** | **done:** `app/conversation/{models,__init__}.py`; 19 tests. `cnv-1`; `Role`/`ConversationStatus`/`Availability` enums; `Citation`; deterministic message ids + SHA-256 session checksum; frozen functional-update sessions; serialization round-trip. Imports no engine (AST). No Sprint 1–5 file touched. |
| **M2** ✅ | Intent Detection | deterministic intent classification (explain prediction / show evidence / why confidence / historical / similar / learning summary / decision summary / health / version / help / unknown). No LLM reasoning. | **done:** `app/conversation/intent.py`; 16 tests. `int-1`; rule-based classifier (phrase/keyword/synonym tiers, configurable priority); extensible `INTENT_REGISTRY`; deterministic entity extraction (prediction_id/symbol) + validation (required subject); classification confidence (rule-match strength, **not** DI confidence); serialization. No LLM/embeddings/retrieval; imports no engine (AST). No Sprint 1–5 / M1 file touched. |
| **M3** ✅ | Retrieval Orchestrator | invoke the Decision Intelligence API; retrieve evidence/explanation/confidence; merge context; build the conversation payload. No generation, no prompts. | **done:** `app/conversation/{retrieval,sources}.py`; 17 tests. `ret-1`; transport-independent `DecisionIntelligenceSource` (+ real `InProcessSource` going **through** the DI engine); intent→target routing; deterministic pipeline (validate → select → fetch once → slice → merge); availability (`AVAILABLE`/`INSUFFICIENT_DATA`/`NOT_AVAILABLE`/`NOT_SUPPORTED`/`ERROR`); context merger (ordering + provenance + citations, content verbatim); serialization (retrieved_at excluded from checksum). Core imports no engine (AST); accesses **only** Decision Intelligence. No Sprint 1–5 / M1 / M2 file touched. |
| **M4** | Prompt Builder | deterministic system/instruction prompt + retrieved context + citation formatting + token budgeting + context ordering. Never invents information. | pending |
| **M5** | Conversation Engine | session handling, memory, follow-ups, multi-turn, context persistence, lifecycle. | pending |
| **M6** | LLM Adapter | provider-independent abstraction (OpenAI / Azure OpenAI / local future). | pending |
| **M7** | REST API | thin `/chat`-style transport: chat, session, health, version. | pending |
| **M8** | Documentation & release | as-built volume, ADRs, Sprint 6 report, release notes `v0.6.0`, compatibility matrix, version bump, tag `v0.6.0-conversation-intelligence`. Freeze. | pending |

Each milestone: implement only that milestone → full suite green → prove Sprints 1–5 + engines
untouched → docs-before-push → commit + push → **STOP for review**.

## 4. Constraints & Definition of Done
Read-only consumer of the existing AEGIS architecture; deterministic + explainable + auditable;
never predicts/advises; honest `INSUFFICIENT_DATA`/`NOT_AVAILABLE`/`NOT_SUPPORTED`; Sprint 1–5
provably unchanged each milestone; frozen at `v0.6.0` with tag `v0.6.0-conversation-intelligence`.

**Out of scope:** any prediction/inference/training; new market analysis; buy/sell advice; a chat
UI; superseding the legacy `app/chat/`; Postgres migration.
