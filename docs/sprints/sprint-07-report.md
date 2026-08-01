# Sprint 7 Report — Agent Engine

- **Sprint:** 7 · Agent Engine
- **Status:** ✅ **COMPLETE**
- **Recommended release tag:** `v0.7.0-agent-engine`
- **Version:** `app/__init__.py` → `0.7.0`
- **Repo:** `SuriyaDcruze/Ai_pred` · branch `main`

> Plan & per-milestone status: [../architecture/sprints/sprint-07-agent-plan.md](../architecture/sprints/sprint-07-agent-plan.md).
> As-built volume: [../architecture/agent-engine.md](../architecture/agent-engine.md).
> Decisions: [../architecture/adr/](../architecture/adr/) (0035–0041). Release notes:
> [../releases/v0.7.0-agent-engine.md](../releases/v0.7.0-agent-engine.md).

---

## 1. Objectives
Add a **deterministic planning + permissioned tool-execution** layer — the **Agent Engine** — that
orchestrates the existing read-only AEGIS engines **as tools**, with an explicit approval gate and a
full immutable audit trail. It **never predicts or advises**, never bypasses the honesty/read-only
guarantees of the engines it calls, and requires an explicit `PermissionRequest` for any
state-changing tool. Built as a new package `app/agent/` distinct from the legacy `app/chat/` and the
Conversation Engine (ADR 0035), fully deterministic, and **without** touching Sprint 1–6 or the
Prediction/Outcome engines.

## 2. Completed milestones
| M | Scope | Tests | Commit |
|---|---|---|---|
| M1 | Agent Domain Model — agent/session/task/plan/tool-call/result/step/permission/audit (`agt-1`) | 17 | `7c22c28` |
| M2 | Tool Registry — metadata catalog, categories, discovery, validation (`tool-1`) | 15 | `c65db6d` |
| M3 | Planner — deterministic task → plan from registry metadata only (`plan-1`) | 16 | `d3e797b` |
| M4 | Permission Engine — policy-driven authorization + safety floor (`perm-1`) | 14 | `f602171` |
| M5 | Executor — policy-enforced execution, registry-gated, audited (`exec-1`) | 16 | `36411b2` |
| M6 | LLM Planning Adapter — provider-independent, advisory-only (`planllm-1`) | 11 | `1c851d9` |
| M7 | REST API — thin `/agent/*` transport (9 endpoints, `agent-api-1`) | 15 | `49c3de2` |
| M8 | Documentation & freeze | — | *(this milestone)* |

**Agent Engine tests: 104.** Every milestone was plan-gated (plan → approve → implement → review →
next), each proving Sprint 1–6 + the engines untouched.

## 3. Architecture summary
```
  AgentTask → Planner (M3) ─uses→ Tool Registry (M2) → AgentPlan
            → Permission Engine (M4) → AuthorizationResult (+ PermissionRequests, PENDING)
            → Executor (M5, registry-gated ToolInvoker) → ExecutionResult (+ immutable AuditEntry trail)
            → /agent/* REST API (M7)
  LLM Planning Adapter (M6) — advisory suggestion → the Planner (authority) validates it
```
Package: `app/agent/{models,tools,planner,permissions,executor,planning_llm}.py` + `app/api/agent.py`.
Read-only by default; imports neither the Prediction nor the Outcome engine; invokes no engine directly.

## 4. Implementation summary
- **Domain model** (`models.py`): frozen, deterministic dataclasses (sha1 ids, SHA-256 checksums
  excluding volatile timestamps); `AgentSession` functional-update aggregate with a validated
  lifecycle state machine and an immutable auto-sequenced audit log.
- **Tool Registry** (`tools.py`): immutable functional catalog of `ToolDefinition` metadata; canonical
  `<engine>.<action>` ids; typed schemas; extensible categories; WRITE ⇒ permission-required invariant;
  a read-only default catalog of 7 tools over the existing engines.
- **Planner** (`planner.py`): pure `(task, registry, rules)` → `AgentPlan`; rule-based selection;
  layered cycle-detecting topological ordering; full error taxonomy; captures-or-raises API.
- **Permission Engine** (`permissions.py`): ordered first-match policy; a metadata **safety floor** so
  policy can only tighten (a state-changing tool is never below `APPROVAL_REQUIRED`); per-step +
  aggregate results; deterministic `PermissionRequest` per approval (never auto-approved).
- **Executor** (`executor.py`): runs only authorized/approved steps in plan order through a replaceable
  registry-gated `ToolInvoker` (offline `EchoToolInvoker`, no engine); dependency-aware skipping;
  immutable ordered audit; worst-outcome aggregation.
- **LLM Planning Adapter** (`planning_llm.py`): provider-independent, advisory-only structured
  suggestions; offline `EchoPlanningProvider` + duck-typed OpenAI/Azure (no SDK); response validation;
  normalised error taxonomy; the Planner remains the authority.
- **REST API** (`app/api/agent.py`): thin `/agent/*` transport; 9 endpoints; artifact-passing pipeline;
  every component fault normalised to HTTP; imports no engine.

## 5. Testing summary
- **Sprint 7 tests: 104** (M1 17 · M2 15 · M3 16 · M4 14 · M5 16 · M6 11 · M7 15).
- **Total project tests: 998 passed, 0 failed** (100% pass rate; 894 → 998, net +104).
- **Isolation verification:** AST guards prove every `app/agent/*` module + `app/api/agent.py` import
  **neither** engine (nor, for the planning adapter, the Planner); the Executor touches no engine and
  calls tools only through the invoker abstraction; the planning adapter imports **no LLM SDK**. All
  tests use temporary databases where a DB is involved.
- **Deterministic verification:** identical inputs → identical plan / authorization / execution /
  suggestion (checksums); the `EchoToolInvoker` and `EchoPlanningProvider` are deterministic offline;
  volatile values (timestamps, latency) excluded from checksums.

## 6. Design decisions (ADRs 0035–0041)
- **0035** Agent Engine (deterministic planning + permissioned execution layer).
- **0036** Tool Registry: a metadata catalog, never an executor.
- **0037** Deterministic Planner (registry-metadata-only, no LLM in the plan path).
- **0038** Permission model: metadata safety floor, policy tightens-only.
- **0039** Executor: registry-gated invoker abstraction, immutable audit, no engine access.
- **0040** Advisory LLM planning: the deterministic Planner is the authority.
- **0041** Agent REST API: thin transport, single route owner (`/agent/*`).

## 7. Verification checklist
- ✅ **Sprint 1–6 unchanged** — no file touched; suites green (894 prior tests all pass).
- ✅ **Prediction / Outcome engines unchanged** — never imported (AST); never invoked.
- ✅ **Read-only by default** — tools are metadata; the shipped invoker is an offline stub; no write,
  no migration.
- ✅ **Permissioned + auditable** — no tool runs without an approved `PermissionRequest`; the safety
  floor is enforced in code + tests; every execution action is an immutable ordered `AuditEntry`.
- ✅ **Advisory-only LLM** — the planning adapter never emits an executable plan; the Planner validates.
- ✅ **Deterministic** — see §5; checksums stable; offline stubs.
- ✅ **OpenAPI synchronized** — all `/agent/*` routes present.

## 8. Known limitations
- **Offline invokers by default** — the shipped `EchoToolInvoker` (execution) and
  `EchoPlanningProvider` (suggestions) are deterministic stubs; real engine-backed tool invokers and a
  live LLM provider need thin translators + credentials. No SDK ships.
- **In-memory sessions** — the REST layer's agent sessions are per-process (no persistence).
- **No new edge, no advice** — the Agent Engine orchestrates existing read-only capabilities; it
  manufactures no predictive edge. Consistent with `docs/RESULTS.md`.

## 9. Deployment readiness & future work
- **Deployment readiness:** `/agent/*` is mounted (one `include_router` line); the pipeline self-builds
  from the default registry; read-only, no new dependency, no migration. Ships with offline stubs.
- **Future work:** wire real engine-backed `ToolInvoker`s (each calling an existing read-only engine
  under permission); a real LLM planning provider translator; optional session/audit persistence; and
  richer planning rules as the catalog grows.

---

## Sprint 7 freeze summary
- **Milestones completed:** 8 / 8 (M1–M8).
- **Modules added:** 7 — `app/agent/{models,tools,planner,permissions,executor,planning_llm}.py` +
  `app/api/agent.py` (plus package `__init__`).
- **Existing files touched:** `app/api/main.py` (mount the router — one import + one `include_router`)
  + the `app/__init__.py` version bump. No Sprint 1–6 module changed.
- **Migrations:** **0** (in-memory; no persistence; no prior table changed).
- **API endpoints added:** 9 (`/agent/*`).
- **Tests added:** 104 → full suite **998 passed, 0 failed**.
- **Sprint 1–6 & engines:** provably untouched (import-guard + no-write + unchanged-tests).
- **Version:** `0.6.0` → `0.7.0`.

**Definition of Done — met:** the Agent Engine can plan a task into a deterministic plan, authorize it
under a policy with a safety floor, execute only approved steps through a registry-gated invoker while
recording an immutable audit trail, and take advisory LLM suggestions that the deterministic Planner
validates — all served over a thin `/agent/*` REST API. Every result is deterministic and checksummed;
the layer predicts and advises nothing and invokes no engine directly; Sprint 1–6, the engines, and
the legacy chat are provably unchanged; the as-built Agent Engine volume documents the engine; Sprint 7
is frozen at `v0.7.0` with tag `v0.7.0-agent-engine`.
