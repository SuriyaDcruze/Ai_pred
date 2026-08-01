# Agent Engine — as built (Sprint 7, `v0.7.0`)

> A **deterministic planning + permissioned tool-execution** layer that orchestrates the existing
> read-only AEGIS engines **as tools** — with an explicit approval gate and a full immutable **audit
> trail**. It **never predicts or advises**, never bypasses the honesty/read-only guarantees of the
> engines it calls, and requires an explicit `PermissionRequest` for any state-changing tool. Frozen
> at `v0.7.0`, tag `v0.7.0-agent-engine`.

> ⚠️ **Disambiguation.** New package `app/agent/` (peer of `app/conversation/`,
> `app/decision_intelligence/`). Distinct from the legacy `app/chat/` assistant and from the
> Conversation Engine (Sprint 6): the Agent Engine *plans + executes tools under permission*, where
> the Conversation Engine *explains*. Read-only by default; the only writes are its own (future)
> audit/session storage, and any tool that mutates state requires explicit approval. (ADR 0035.)

## Data flow / component interaction
```
  AgentTask
     │
     ▼
  Planner (M3, plan-1) ──uses──► Tool Registry (M2, tool-1, metadata only)
     │ AgentPlan
     ▼
  Permission Engine (M4, perm-1) ──ALLOWED / APPROVAL_REQUIRED / DENIED──► AuthorizationResult
     │                                                     │ PermissionRequest(s), PENDING
     ▼                                                     ▼
  Executor (M5, exec-1) ──registry-gated ToolInvoker──►  (approvals granted by caller)
     │ ExecutionResult (+ immutable AuditEntry trail)
     ▼
  /agent/* REST API (M7, agent-api-1, thin transport)

  LLM Planning Adapter (M6, planllm-1) ── advisory suggestion ──► the Planner (authority) validates it
```
Every layer imports **neither** the Prediction (`app/ai/sklearn_model.py`) nor the Outcome
(`app/ai/outcome_model.py`) engine (AST-guarded), and none invokes an engine directly — tools are a
metadata catalog and the Executor calls them only through a replaceable invoker abstraction.

## Package structure & public interfaces
`app/agent/` (peer of `app/conversation/`):
- **`models.py` (M1, `agt-1`)** — the **Agent domain model**: `Agent`, `AgentSession` (frozen,
  functional-update, SHA-256 checksum, validated lifecycle state machine
  `CREATED`/`PLANNING`/`WAITING_FOR_APPROVAL`/`EXECUTING`/`COMPLETED`/`FAILED`/`CANCELLED`),
  `AgentTask`, `AgentPlan`, `ExecutionStep`, `ToolCall`, `ToolResult`, `PermissionRequest`, and the
  immutable auto-sequenced `AuditEntry`. Deterministic ids; serialization; versioning.
- **`tools.py` (M2, `tool-1`)** — the **Tool Registry** (metadata only): `ToolDefinition` (canonical
  immutable `<engine>.<action>` id + checksum), `ToolSchema`/`ToolParameter` (unique-name, typed),
  `ToolCategory`/`ToolCapability`/`ToolAvailability` enums (`FUTURE_EXTENSIONS` = the extensible
  bucket), immutable functional `ToolRegistry` (register / duplicate-reject / lookup by id + category
  + engine / deterministic order / discovery). Invariant: a WRITE tool must set
  `permission_required`. Read-only `default_registry()` catalog (7 tools over the existing engines).
- **`planner.py` (M3, `plan-1`)** — the **deterministic Planner**: turns an `AgentTask` into an
  `AgentPlan` from **registry metadata only**; rule-based selection (explicit `goal` /
  `requested_tools` / deterministic keyword scan; ambiguity → `UNSUPPORTED_TASK`); layered
  cycle-detecting topological ordering; error taxonomy
  `UNSUPPORTED_TASK`/`TOOL_NOT_FOUND`/`TOOL_UNAVAILABLE`/`INVALID_PLAN`/`DEPENDENCY_ERROR`;
  `PlanningResult` (`plan()` captures / `plan_or_raise()` raises); read-only `DEFAULT_PLANNING_RULES`.
- **`permissions.py` (M4, `perm-1`)** — the **Permission Engine**: policy-driven authorization of each
  step (`ALLOWED`/`APPROVAL_REQUIRED`/`DENIED`); ordered first-match `PermissionPolicy`/`PermissionRule`
  + `default_policy()`; a **metadata safety floor** — a WRITE / `permission_required` tool can never be
  relaxed below `APPROVAL_REQUIRED` (policy may only *tighten*, strictest-wins); per-step
  `StepAuthorization` + aggregate `AuthorizationResult`; a `PermissionRequest` per approval (left
  `PENDING`, never auto-approved); error taxonomy
  `POLICY_ERROR`/`INVALID_PERMISSION`/`APPROVAL_REQUIRED`/`PERMISSION_DENIED`.
- **`executor.py` (M5, `exec-1`)** — the **Executor**: runs only eligible steps in plan order
  (`ALLOWED`, or `APPROVAL_REQUIRED` with a **granted** `PermissionRequest`); DENIED / missing-approval
  / dependency-blocked steps stay unexecuted; `ExecutionOutcome` SUCCESS/SKIPPED/DENIED/FAILED
  (aggregate = worst); a replaceable `ToolInvoker` abstraction + offline deterministic
  `EchoToolInvoker` (**no engine**); `ExecutionContext` (functional output accumulation); an immutable
  `AuditEntry` per event in execution order; error taxonomy
  `EXECUTION_ERROR`/`TOOL_FAILURE`/`APPROVAL_MISSING`/`INVALID_EXECUTION`/`TOOL_UNAVAILABLE`.
- **`planning_llm.py` (M6, `planllm-1`)** — the **LLM Planning Adapter** (advisory only):
  provider-independent (`PlanningLLMProvider` ABC + `PlanningLLMAdapter`); registry +
  `create_planning_adapter()`; offline `EchoPlanningProvider` + duck-typed
  `OpenAIPlanningProvider`/`azure_openai` (**no SDK import**); `PlanningRequest`/`PlanningResponse`
  (`SuggestedStep` sequence + rationale + provider-only confidence); response validation → normalised
  `INVALID_RESPONSE`; error taxonomy
  `INVALID_REQUEST`/`INVALID_RESPONSE`/`PROVIDER_UNAVAILABLE`/`RATE_LIMITED`/`TIMEOUT`/`INTERNAL_ERROR`.
  `as_requested_tools()` feeds the **Planner** (the authority) — never an executable plan.
- **`app/api/agent.py` (M7)** — the thin `/agent/*` REST transport (9 endpoints).

## REST API (`/agent/*`, M7, ADR 0041)
| Method · Path | Purpose |
|---|---|
| `POST /agent/session` | create an in-memory agent session |
| `GET /agent/session/{id}` | the session state + task + plan + audit |
| `DELETE /agent/session/{id}` | cancel (terminal) + drop the session |
| `POST /agent/plan` | plan a task → `AgentPlan` (delegates to the Planner) |
| `POST /agent/authorize` | authorize a plan against a policy → `AuthorizationResult` |
| `POST /agent/execute` | execute an authorized plan (+ granted request ids) → `ExecutionResult` |
| `POST /agent/suggest` | advisory LLM planning suggestion (Planner remains authority) |
| `GET /agent/health` | aggregate readiness of the agent components |
| `GET /agent/version` | the agent-stack versions |

Thin transport: validate → delegate → serialise. Artifact-passing pipeline
(plan → authorize → execute). Every component fault is normalised to an HTTP status (planner
`404/409/422`, permission `400/422`, execution `422/409/502`, malformed payload `400`; provider faults
normalised in-body); no internal exception leaks. Imports no engine.

## Guarantees
- **Read-only by default + no advice/prediction:** imports neither engine (AST); tools are metadata;
  the Executor calls tools only through a replaceable invoker (the shipped one is an offline stub).
- **Permissioned:** no tool runs without an approved `PermissionRequest`; a state-changing tool can
  never be authorized below `APPROVAL_REQUIRED` (the metadata safety floor).
- **Deterministic + reproducible:** every module stamps a version and a SHA-256 checksum (volatile
  timestamps/latency excluded); identical inputs → identical plan / authorization / execution.
- **Auditable:** every execution action produces an immutable, ordered `AuditEntry`.
- **Advisory LLM:** the LLM only *suggests*; the deterministic Planner validates before any execution.

## Versions
`agt-1` (domain) · `tool-1` (registry) · `plan-1` (planner) · `perm-1` (permissions) · `exec-1`
(executor) · `planllm-1` (planning adapter) · `agent-api-1` (REST). Release `v0.7.0`, tag
`v0.7.0-agent-engine`.

## Storage
**None** — in-memory sessions only; the layer writes nothing and adds no migration.

## Honest note
Consistent with `docs/RESULTS.md`: the Agent Engine **manufactures no edge and gives no advice**. It
orchestrates the existing read-only engines under permission and audit; it invokes no engine directly
(the shipped `EchoToolInvoker`/`EchoPlanningProvider` are offline deterministic stubs), and the only
verified edge remains the Outcome Engine (backtest-only). Wiring real engine-backed tool invokers and
a live LLM provider is future work.
