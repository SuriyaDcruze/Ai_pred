# Sprint 8 — Workflow Engine · Architecture & Milestone Plan

> **Process (identical to Sprints 1–7):** Architecture → Milestones → Review → Approval →
> Implementation, one milestone at a time with a review gate after each.
>
> **Status:** 🔨 **In progress — implementing milestone by milestone.** Architecture approved.
> **M1 (Workflow Domain Model) ✅ · M2 (Definition & Validation) ✅ — awaiting M2 review.** M3–M8
> defined below, each plan-gated.
>
> **Sprint sequence:** Sprint 5 (Decision Intelligence `v0.5.0`) → Sprint 6 (Conversation
> Intelligence `v0.6.0`) → Sprint 7 (Agent Engine `v0.7.0`) → **Sprint 8 (Workflow Engine `v0.8.0`,
> proposed)**.

**Related:** the [Agent Engine](../agent-engine.md) (Sprint 7 — the layer directly below this one),
the [Conversation Intelligence Engine](../conversation-intelligence-engine.md) (Sprint 6), Vol 03/24
(SEBI posture / compliance, [ADRs](../adr/) 0002/0003/0004/0018/0035–0041), and `docs/RESULTS.md`
(the honest scoreboard this sprint must not contradict).

---

## 1. Architecture
The Workflow Engine is a **deterministic orchestration layer for long-running, multi-step workflows**.
It sits **above the Agent Engine** and composes **multiple agent executions** into a durable,
resumable, auditable process — sequential steps, conditional branches, parallel branches, retries,
rollback hooks, waiting/approval checkpoints, scheduling, timeouts, cancellation, and resume-after-
interruption. It is pure **coordination**: it decides *what runs next and when*, then delegates the
*doing* to the Agent Engine (which in turn plans, authorizes, and executes tools over the read-only
engines). It performs no business logic of its own.

```
  User
    ↓
  Conversation Engine (Sprint 6, explain)
    ↓
  Agent Engine (Sprint 7, plan → authorize → execute one task under permission + audit)
    ↓
  Workflow Engine (Sprint 8, THIS) — orchestrates MANY agent executions:
      WorkflowDefinition ─► Transition/Branch Engine ─► Workflow Runtime ─► (invokes the Agent Engine per step)
                                    │                          │
                              WorkflowEvent(s)          WorkflowCheckpoint(s) ─► resume / idempotency / rollback
    ↓
  Existing AEGIS Engines (Forward Testing · Memory · Similarity · Learning · Decision Intelligence)
     — reached ONLY through the Agent Engine, never directly.
```
Every layer imports **neither** the Prediction (`app/ai/sklearn_model.py`) nor the Outcome
(`app/ai/outcome_model.py`) engine (AST-guarded), and the Workflow Engine reaches the AEGIS engines
**only through the Agent Engine** — never directly and never bypassing its permission gate.

## 2. Ground truth & disambiguation
Sprint 7 delivered the **Agent Engine**: it takes *one* task, plans it into an `AgentPlan`, authorizes
each step (with a metadata safety floor), executes only approved steps through a registry-gated
invoker, and records an immutable audit trail — all deterministic, all read-only by default. What it
does **not** do is coordinate *many* such executions over time with branching, waiting, scheduling,
and recovery. That is the Workflow Engine's job.

⚠️ **Disambiguation.** New package `app/workflow/` (peer of `app/agent/`, `app/conversation/`,
`app/decision_intelligence/`). Distinct from:
- the **Agent Engine** (`app/agent/`) — the Workflow Engine *orchestrates* agent executions; it does
  **not** replace the planner/permission/executor, and it invokes the Agent Engine through an
  abstraction (never bypassing its permission gate);
- the existing **Forward Testing** monitor/state-machine (`app/forward_testing/`) — that resolves a
  *single prediction's* outcome over time; the Workflow Engine coordinates *arbitrary multi-agent
  processes* and does not touch prediction resolution;
- the developer-facing "workflow" tooling in the harness — unrelated; this is a product engine.

The Workflow Engine **never** predicts, trains, bypasses permissions, replaces the Agent Engine, or
executes business logic itself. Read-only over the AEGIS engines (via the Agent Engine); its only
writes are its own workflow state/checkpoints/audit (in-memory by default — see Constraints §6 and
Risk R1).

## 3. Design principles (each to be enforced by tests)
- **Deterministic** — a workflow's advance is a pure function of `(definition, current state, events,
  injected clock)`; identical inputs → identical next-state and SHA-256 checksum. Never uses wall-clock
  `Date`/random directly; time comes from an injected clock, randomness (if any) is seeded/derived.
- **Auditable** — every transition, agent invocation, checkpoint, retry, rollback, wait, approval,
  timeout, and cancellation emits an immutable, ordered `WorkflowEvent`/audit entry.
- **Resumable** — a workflow can be reconstructed from its last `WorkflowCheckpoint` and replayed
  forward; interruption (process restart) loses no committed progress.
- **Idempotent** — each step carries an idempotency key; re-applying an already-applied step or event
  is a no-op, so retries and resume never double-execute.
- **Versioned** — every module stamps a version (`wf-1`, `wfdef-1`, …); a definition pins the engine
  version it was authored against; a shape/method change is a new version, never an edit.
- **Event-driven** — the runtime advances by consuming `WorkflowEvent`s (step-completed, approval-
  granted, timer-fired, cancelled, …); external inputs (approvals, schedules) arrive as events.
- **Permission-aware** — state-changing work happens **only** via the Agent Engine's permission gate;
  the Workflow Engine adds approval **checkpoints** but never grants or bypasses a `PermissionRequest`.
- **Enterprise-modular** — clean layers (domain → definition → transitions → runtime → durability →
  scheduling → REST), each independently testable; transport-independent agent invocation via an
  abstraction (duck-typed), so the Agent Engine can be swapped/stubbed in tests.

## 4. Engine position
`app/workflow/` is a **peer package one level above** `app/agent/`. It depends on the Agent Engine's
**public interface only** (a `WorkflowAgentRunner` abstraction wrapping `Planner`/`PermissionEngine`/
`Executor`, or a stub in tests) and on its own domain model. It imports **no** AEGIS engine and reaches
them only transitively through the Agent Engine. Served at `/workflow/*` (thin transport). It reuses
`data/prediction_history.db` only if a durable checkpoint store is later approved (Risk R1); the
default is in-memory + caller-serializable checkpoints.

## 5. Milestone breakdown (M1–M8, plan-gated)
Each milestone: implement only that milestone → full suite green → prove Sprints 1–7 + engines
untouched → docs-before-push → commit + push → **STOP for review**.

| M | Title | Scope | Proposed version |
|---|---|---|---|
| **M1** ✅ | Workflow Domain Model | `Workflow`, `WorkflowSession`, `WorkflowDefinition`, `WorkflowStep`, `WorkflowTransition`, `WorkflowExecution`, `WorkflowState`, `WorkflowCheckpoint`, `WorkflowEvent`, `WorkflowResult` — frozen deterministic dataclasses; validated lifecycle state machine; deterministic ids + SHA-256 checksums (volatile timestamps excluded); immutable ordered event log; serialization round-trip; versioning. **No runtime / transitions / scheduling / agent calls / REST.** → **done:** `app/workflow/{models,__init__}.py`; 19 tests. `wf-1`; lifecycle `CREATED`/`RUNNING`/`WAITING`/`PAUSED`/`COMPLETED`/`FAILED`/`CANCELLED` (validated transitions); `StepState`/`StepKind`/`TransitionKind`/`WorkflowEventType`/`WorkflowOutcome` enums; declarative definition + structural validation (unique step ids, known initial step, transitions reference known steps — full DAG/reachability is M2); immutable auto-sequenced event history; deterministic ids + SHA-256 checksums; frozen functional-update session/execution; serialization round-trip. Imports **no** engine and **not the Agent Engine** (AST) — self-contained helpers. No Sprint 1–7 file touched. |
| **M2** ✅ | Definition & Validation | declarative `WorkflowDefinition` (steps + typed transitions + retry/timeout/rollback policies carried in `config`/`metadata`) + immutable `WorkflowRegistry`; **static validation** — DAG/graph integrity, reachability, reachable terminal, no dangling/duplicate transitions, policy + agent-task structure; serialization; versioning. **Metadata only — no execution.** → **done:** `app/workflow/definition.py`; 16 tests. `wfdef-1`; `DefinitionValidator` (pure static; collects all issues), `ValidationResult`/`ValidationIssue` (checksum + round-trip), `ValidationError` + `ValidationErrorCode` taxonomy (`INVALID_WORKFLOW`/`DUPLICATE_WORKFLOW`/`DUPLICATE_STEP`/`UNKNOWN_STEP`/`INVALID_INITIAL_STEP`/`UNREACHABLE_STEP`/`NO_TERMINAL_STEP`/`INVALID_TRANSITION`/`DUPLICATE_TRANSITION`/`CYCLIC_GRAPH`/`INVALID_POLICY`/`INVALID_AGENT_TASK`); DFS cycle detection + reachability + reachable-terminal; retry/timeout/rollback **structure-only** checks (no predicate/policy evaluation); immutable functional `WorkflowRegistry` (register / duplicate-reject / order by id / lookup / discovery / round-trip). Extends definitions via `config`/`metadata` only — **M1 model unchanged**. Imports no engine and **not the Agent Engine** (AST) — only M1. No Sprint 1–7 / M1 file touched. |
| **M3** | Transition & Branching Engine | deterministic, **pure** control-flow: given `(definition, state, events)` compute the next step(s) — **sequential**, **conditional branching** (deterministic predicate evaluation over state), and **parallel branches** (fan-out/fan-in join semantics). Cycle/guard validation; error taxonomy. **No agent execution, no side effects.** | `wftx-1` |
| **M4** | Workflow Runtime (Executor) | drives a definition step-by-step by invoking the **Agent Engine** per step through a `WorkflowAgentRunner` abstraction (never bypassing its permission gate); applies **retries** (bounded, deterministic backoff via injected clock) and **timeouts**; emits `WorkflowEvent`s; records the audit trail; aggregates a `WorkflowResult`. **Delegates all doing to the Agent Engine; executes no business logic.** | `wfx-1` |
| **M5** | Checkpointing, Resume, Idempotency & Rollback | deterministic `WorkflowCheckpoint` capture after each committed step; **resume-after-interruption** by rebuilding state from the last checkpoint + replaying pending events; **idempotency keys** so a replayed/retried step is a no-op; **rollback hooks** invoked (best-effort, audited) on failure; pluggable `CheckpointStore` (in-memory default). | `wfck-1` |
| **M6** | Scheduling, Waiting, Approval Checkpoints, Timeout & Cancellation | **waiting states** (pause until an event); **approval checkpoints** (pause until an approval event — delegated to the Agent Engine's permission model, never bypassed); **scheduled execution** (run-at/after evaluated against an **injected clock**, deterministic); **timeout** + **cancellation** handling; all event-driven. | `wfsch-1` |
| **M7** | REST API | thin `/workflow/*` transport orchestrating M2–M6: register/define, start session, advance, checkpoint/resume, approve, cancel, status, health, version. Request validation, response serialization, error normalization (component faults → HTTP), API versioning. **No orchestration logic in the routes.** | `workflow-api-1` |
| **M8** | Documentation & release | as-built Workflow Engine volume, ADRs, Sprint 8 report, release notes `v0.8.0`, compatibility matrix (Sprint 1–8), CHANGELOG + sprint-index update, version bump `app/__init__.py` → `0.8.0`, tag `v0.8.0-workflow-engine`. Freeze. | — |

**Feature → milestone coverage:** sequential/conditional/parallel → **M3**; retries/timeout → **M4**;
rollback hooks → **M5**; waiting states/approval checkpoints/scheduled execution/timeout/cancellation →
**M6**; resume-after-interruption/idempotency/checkpoints → **M5**; event-driven → M1 events emitted by
M4/M6; permission-aware → M4/M6 delegate to the Agent Engine.

## 6. Constraints
- **Does NOT:** make predictions · train models · bypass permissions · replace the Agent Engine ·
  execute business logic itself. It orchestrates **agent executions**.
- **Read-only over the AEGIS engines** — reached **only** through the Agent Engine; imports neither the
  Prediction nor the Outcome engine (AST-guarded); no direct engine import.
- **Deterministic** — no wall-clock/random in logic or checksums; time via an injected clock.
- **No new database by default** — workflow state/checkpoints are in-memory + serialisable; a durable
  checkpoint store is a **pluggable adapter** and any persistence (append-only satellite table on the
  existing `prediction_history.db`, never modifying existing tables) is a **separate decision requiring
  explicit approval** (ADR candidate / Risk R1). Tests use temporary databases only.
- **Sprints 1–7 frozen** — no Sprint 1–7 file modified except the additive `api/main.py` router mount
  (M7) and the `app/__init__.py` version bump (M8). Proven each milestone.
- **Out of scope (this sprint):** any prediction/inference/training; buy/sell advice; auto-executing
  state-changing actions without approval; a UI; Postgres migration; distributed/multi-node execution;
  a message broker.

## 7. Definition of Done (whole sprint)
The Workflow Engine can take a validated `WorkflowDefinition` and deterministically orchestrate a
multi-step process — sequential, conditional, and parallel — over **multiple Agent Engine executions**,
with retries, timeouts, rollback hooks, waiting/approval checkpoints, scheduling, cancellation, and
**resume-after-interruption** from checkpoints, all **idempotent** and **auditable**; every advance is a
pure function of `(definition, state, events, clock)` with a stable checksum; it **never** predicts,
advises, trains, bypasses the Agent Engine's permission gate, or executes business logic itself; it
reaches the AEGIS engines only through the Agent Engine; served over a thin `/workflow/*` REST API;
Sprints 1–7, the engines, and the legacy chat are provably unchanged; the as-built volume documents the
engine; frozen at `v0.8.0` with tag `v0.8.0-workflow-engine`. Full suite green (≈ 998 + N), 0 failed.

## 8. ADR candidates
- **A** — Workflow Engine: deterministic orchestration above the Agent Engine (sits above, orchestrates
  many agent executions, never replaces it).
- **B** — Workflow definitions are declarative + statically validated (DAG/reachability), not code.
- **C** — Deterministic transition/branching engine (pure control flow; injected clock; no side effects).
- **D** — Workflow Runtime invokes the Agent Engine through an abstraction and **never bypasses its
  permission gate** (approval checkpoints delegate, they don't grant).
- **E** — Checkpoint/resume/idempotency model (deterministic checkpoints; replay-safe; pluggable store).
- **F** — Rollback hooks: best-effort, audited compensation on failure (not distributed transactions).
- **G** — Scheduling & time via an injected clock (deterministic; no wall-clock in checksums).
- **H** — Workflow REST API: thin transport, single route owner (`/workflow/*`).
- **(conditional) I** — Durable checkpoint persistence (append-only satellite table) — only if Risk R1
  is approved; otherwise in-memory default stands.

## 9. Risks
- **R1 — Resumability vs. "no new persistence."** True resume-after-restart needs durable checkpoints,
  which tensions with the Sprint 5–7 in-memory discipline. *Mitigation:* pluggable `CheckpointStore`
  with an in-memory default; resume is proven deterministically by serialise→restore in tests; durable
  (append-only satellite) persistence is a separate, explicitly-approved decision (ADR I). **Decision
  needed at review.**
- **R2 — Determinism vs. time/scheduling/timeouts.** Wall-clock would break reproducibility.
  *Mitigation:* injected clock everywhere; time excluded from checksums (Forward-Testing monitor
  precedent).
- **R3 — Parallel branches vs. deterministic ordering.** Concurrency can reorder results. *Mitigation:*
  parallel branches are modelled as a deterministic fan-out/fan-in with a stable join order; the engine
  computes readiness deterministically (it schedules, it does not thread).
- **R4 — Permission bypass.** An orchestrator could accidentally run un-approved work. *Mitigation:* all
  doing goes through the Agent Engine; approval checkpoints delegate to its permission model; an AST/no-
  bypass guard + tests (ADR D).
- **R5 — Long-running / unbounded workflows.** Loops or waits could hang. *Mitigation:* bounded retries,
  explicit timeouts, cancellation, and step/iteration caps; a definition must have a reachable terminal
  state (M2 validation).
- **R6 — Scope creep toward a distributed engine.** *Mitigation:* explicitly single-process, in-memory,
  no broker/multi-node this sprint (Constraints §6); extraction is a future concern.
- **R7 — Rollback expectations.** Rollback hooks are best-effort compensation, **not** ACID
  transactions across engines. *Mitigation:* documented as such (ADR F); hooks are audited and idempotent.

## 10. Review checklist (before approving M1)
- ☐ Engine position agreed: Workflow **above** Agent; orchestrates agent executions; never replaces it.
- ☐ Boundaries agreed: no prediction/training, no business logic, no permission bypass, read-only via
  the Agent Engine only.
- ☐ Milestone breakdown (M1–M8) and per-milestone version stamps agreed.
- ☐ **R1 decision:** in-memory-only checkpoints for this sprint, or approve a durable append-only
  satellite store (ADR I)?
- ☐ Determinism approach agreed: injected clock, no wall-clock/random in logic or checksums.
- ☐ Parallel-branch semantics agreed: deterministic fan-out/fan-in, single-process (no threads/broker).
- ☐ Rollback semantics agreed: best-effort audited compensation, not distributed transactions.
- ☐ Constraints §6 + out-of-scope list agreed.
- ☐ ADR candidates (A–H, conditional I) agreed as the Sprint 8 decision set.
- ☐ Confirm the same gated cadence: implement **M1 only**, then STOP for review.

---

*Awaiting approval on this architecture + milestone plan. Per the mandated process, **nothing is
implemented until M1 is reviewed and approved** — then M1 only, then STOP for review.*
