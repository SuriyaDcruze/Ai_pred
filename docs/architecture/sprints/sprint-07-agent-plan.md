# Sprint 7 — Agent Engine · Architecture & Milestone Plan

> **Process (identical to Sprints 1–6):** Architecture → Milestones → Review → Approval →
> Implementation, one milestone at a time with a review gate after each.
>
> **Status:** 🔨 **In progress — implementing milestone by milestone.** **M1 (Agent Domain Model)
> ✅ · M2 (Tool Registry) ✅ — awaiting M2 review.** Later milestones defined per their milestone specs.
>
> **Sprint sequence:** … Sprint 5 (Decision Intelligence `v0.5.0`) → Sprint 6 (Conversation
> Intelligence `v0.6.0`) → **Sprint 7 (Agent Engine `v0.7.0`, proposed)**.

**Related:** the [Decision Intelligence Engine](../decision-intelligence-engine.md) (Sprint 5), the
[Conversation Intelligence Engine](../conversation-intelligence-engine.md) (Sprint 6), Vol 03/24
(SEBI posture / compliance, [ADRs](../adr/) 0002/0003/0004/0018), and `docs/RESULTS.md` (the honest
scoreboard this sprint must not contradict).

---

## 0. Ground truth & disambiguation
The AEGIS stack now exposes six read-only engines (`/forward/*`, `/memory/*`, `/memory/similar*`,
`/learning/*`, `/intelligence/*`, `/chat/*`). The **Agent Engine** adds a **deterministic planning +
permissioned tool-execution** layer that orchestrates those existing capabilities as **tools** — with
an explicit approval gate and a full immutable **audit trail**. It **never predicts or advises** and
never bypasses the honesty/read-only guarantees of the engines it calls; any state-changing tool must
pass an explicit `PermissionRequest`.

⚠️ **Disambiguation.** New package `app/agent/` (peer of `app/conversation/`, `app/decision_intelligence/`).
Distinct from the legacy `app/chat/` assistant and from the Conversation Engine (Sprint 6) — the Agent
Engine *plans + executes tools under permission*, where the Conversation Engine *explains*. Read-only
by default; the only writes are its own (future) audit/session storage, and any tool that mutates
state requires explicit approval.

## 1. Core principles (each enforced by tests)
Deterministic (pure over inputs; deterministic ids + checksums); **permissioned** (no tool runs
without an approved `PermissionRequest`); **auditable** (every action → an immutable `AuditEntry`);
read-only over the existing engines unless a tool is explicitly approved to write; imports **neither**
the Prediction nor the Outcome engine (AST-guarded); never predicts or advises.

## 2. Milestone breakdown (plan-gated)
| M | Title | Scope | Status |
|---|---|---|---|
| **M1** ✅ | Agent Domain Model | `Agent`, `AgentSession` (+ lifecycle state machine), `AgentTask`, `AgentPlan`, `ExecutionStep`, `ToolCall`, `ToolResult`, `PermissionRequest`, `AuditEntry`; deterministic ids/checksums; serialization; versioning. **No execution/tools/routing/LLM/permissions/planning.** | **done:** `app/agent/{models,__init__}.py`; 17 tests. `agt-1`; lifecycle `CREATED`/`PLANNING`/`WAITING_FOR_APPROVAL`/`EXECUTING`/`COMPLETED`/`FAILED`/`CANCELLED` (validated transitions); immutable auto-sequenced audit; deterministic ids + SHA-256 checksums; frozen functional-update session; serialization round-trip. Imports no engine (AST). No Sprint 1–6 file touched. |
| **M2** ✅ | Tool Registry | tool definition + schema, categories, discovery, validation, serialization, versioning (`tool-1`). **Metadata only — no execution/planning/permissions/engine calls/LLM/REST.** | **done:** `app/agent/tools.py`; 15 tests. `tool-1`; `ToolDefinition` (canonical immutable `<engine>.<action>` id + SHA-256 checksum), `ToolSchema`/`ToolParameter` (unique-name + typed integrity), `ToolCategory`/`ToolCapability`/`ToolAvailability` enums (FUTURE_EXTENSIONS bucket = extensible), immutable functional `ToolRegistry` (register / duplicate-reject / lookup by id + category + engine / deterministic order by id / discovery / round-trip). WRITE ⇒ permission_required invariant. Read-only `default_registry()` catalog (7 tools over the existing engines). Imports no engine (AST) — only the M1 primitives. No Sprint 1–6 / M1 file touched. |
| **M3+** | *(per forthcoming milestone specs)* | Anticipated concerns: **Planner** (deterministic plan from a task), **Permission Engine** (approval gate), **Executor** (permissioned, audited tool execution), **Audit store**, **LLM planning adapter**, **REST API**, **docs & freeze**. Each defined + gated at its milestone. | pending |

Each milestone: implement only that milestone → full suite green → prove Sprints 1–6 + engines
untouched → docs-before-push → commit + push → **STOP for review**.

## 3. Constraints (M1–M2)
Domain models (M1) and tool **metadata** (M2) only — **no** tool execution, routes, LLM calls,
permission logic, planning, or engine invocation. Frozen, deterministic, serializable. Sprint 1–6 +
earlier M provably unchanged each milestone.

**Out of scope (this sprint, unless a milestone spec says otherwise):** any prediction/inference/
training; buy/sell advice; auto-executing state-changing actions without approval; a UI; Postgres.
