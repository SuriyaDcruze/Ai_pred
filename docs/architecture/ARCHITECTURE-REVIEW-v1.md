# Aegis Architecture Review v1.0

> Enterprise architecture review of the 28-volume Architecture Book. Deliverable:
> findings, prioritized improvements, and an updated implementation roadmap. **No code.**
> Reviewer stance: think like the CTO maintaining this for ten years — bias toward
> maintainability and restraint, not the biggest architecture.

---

## 1. Executive assessment

**Overall grade: strong foundation, thin on formalism and AI-platform operations.**

The book is unusually honest and correctly scoped (modular monolith, edge-proof-first,
LLM-never-predicts). Its **research-integrity spine is a genuine differentiator** most
enterprise books lack. Where it is weak is not *vision* — it is *engineering formalism*
and *AI-specific operations*:

| Dimension | Grade | Note |
|---|---|---|
| Vision / product / business | **A** | Honest, scoped, differentiated |
| Domain modelling (DDD) | **C** | Engines are clear; the *domain model & ubiquitous language* are implicit |
| Diagrams (sequence / state) | **C−** | One data-flow diagram; **no sequence or state diagrams** |
| API formalism | **C** | Endpoints listed; **no formal request/response contracts** |
| AI-platform ops | **C−** | **No model registry**, no formal background-job/scheduler architecture |
| Data / persistence | **B−** | Good target schema; migration path light on jobs |
| Security / compliance | **B** | Strong invariants; **audit logging** not its own concern |
| Testing | **A−** | Research-integrity tests are excellent |
| Restraint / anti-over-engineering | **A** | Correctly defers microservices/native/mobile |

**Bottom line:** the book is a good *narrative* architecture. To be a *production*
architecture it needs (a) the **decisions captured as records**, (b) an explicit
**domain model + contracts**, (c) the **AI-ops layer** (model registry, jobs) that the
priority work (Forward Testing) actually depends on, and (d) **diagrams**.

## 2. Invariants — confirmed preserved (non-negotiable)

All six architectural invariants are intact and reinforced by this review; nothing
proposed here touches them:
1. Prediction Engine = proprietary core. 2. LLM never predicts. 3. LLM consumes structured
outputs only. 4. Honest validation mandatory. 5. Modular monolith until scale forces
extraction. 6. Forward testing mandatory before live-performance claims.

---

## 3. Gap analysis (by review category)

| Category | Gap found | Severity |
|---|---|---|
| Clean architecture / DDD | No explicit **domain model / ubiquitous language** — entities (Prediction, Recommendation, Setup, Trade, TrackRecord) are implied, not defined | High |
| Decision governance | Decisions are *stated in prose* but not captured as immutable, dated **ADRs** — the #1 ten-year-maintainability gap | Critical |
| AI architecture | **No Model Registry** — artifacts are pkl-in-git with ad-hoc meta; lineage/metrics/promotion/rollback not formalised. Blocks safe multi-model growth (crypto, NSE, per-sector) | High |
| Workflows | **No background-job / scheduler architecture** — yet Forward Testing (Vol 18) and nightly retrain (Vol 15) *require* it | High |
| API design | **No formal API contracts** (schemas per endpoint / OpenAPI discipline) | High |
| Security / compliance | **Audit logging** is folded into Historical Memory; SEBI/enterprise needs it as an explicit, tamper-evident concern | High |
| Diagrams | **No sequence diagrams, no state machines** (recommendation lifecycle, resolution, retrain-promotion, conversation orchestration) | Medium |
| Product / UX | **No user-journey documentation** (onboarding, daily loop, paper-trade→track) | Medium |
| Performance / caching | Caching is ad-hoc (sector 15-min); no formal caching/perf budget doc | Medium |
| Notifications | Mentioned in Vol 27 but not designed | Medium |
| Resilience | **No disaster-recovery / backup-restore** (RTO/RPO, artifact recovery) | Medium (Future) |
| Config / flags | Config exists (pydantic-settings); **feature flags** absent (safe engine rollout) | Low |
| Eventing | No event architecture — *correctly absent for now*, but worth an explicit "in-process events, defer the bus" position | Low |

---

## 4. Recommended improvements (new volumes)

Each: **why · problem solved · integrates where · volumes affected · now/later ·
benefits · trade-offs.** Grouped by priority. New volume numbers continue from 28.

### 🔴 CRITICAL

**Vol 29 — Architecture Decision Records (ADR)**
- *Why:* a ten-year platform must remember *why* it chose what it chose. The book states
  decisions; it doesn't preserve their context/consequences immutably.
- *Problem:* future maintainers re-litigate settled decisions (deep-net vs logistic,
  monolith vs services, no-LLM-prediction) without the original reasoning.
- *Integrates:* `docs/architecture/adr/NNNN-*.md`, one file per decision.
- *Affects:* references Vol 04, 05, 07 (records their decisions retroactively).
- *When:* **now** (cheap; back-fill the ~8 decisions already made).
- *Benefits:* institutional memory; faster onboarding; prevents drift.
- *Trade-offs:* light discipline overhead per decision. Minimal.

### 🟠 HIGH

**Vol 30 — Domain Model & Ubiquitous Language**
- *Why:* clean architecture needs shared, precise terms. "Signal", "prediction",
  "recommendation", "setup", "trade", "track record" are used loosely.
- *Problem:* ambiguity leaks into code, API, and conversations; DDD boundaries blur.
- *Integrates:* a single reference; entities + value objects + their lifecycles.
- *Affects:* Vol 02, 04, 13, 18, 21 (aligns their nouns).
- *When:* **now** (precedes DB/API formalisation).
- *Benefits:* one language across model, API, UI, docs; cleaner boundaries.
- *Trade-offs:* must be kept in sync (cheap).

**Vol 31 — API Contracts (formal)**
- *Why:* Vol 20 lists endpoints but not schemas. A platform others build on needs
  contract-level precision.
- *Problem:* frontend/mobile/LLM-tools code against undocumented shapes; breaking changes
  go unnoticed.
- *Integrates:* per-endpoint request/response schemas; publish OpenAPI; versioning rules.
- *Affects:* Vol 20 (extends it), Vol 07 (LLM tool schema), Vol 22/23 (clients).
- *When:* **now-ish** (before multi-client / public API).
- *Benefits:* stable contracts; safe evolution; the LLM tool schema (no-predict) is
  enforceable.
- *Trade-offs:* contract maintenance; mitigated by FastAPI-generated OpenAPI.

**Vol 32 — Model Registry**
- *Why:* Aegis is an *AI* platform with **multiplying models** (direction, outcome, crypto,
  NSE, future per-sector/meta). Pkl-in-git with ad-hoc meta doesn't scale or govern.
- *Problem:* no single source of truth for which model/version is live, its metrics, its
  lineage, or how to roll back; promotion is manual.
- *Integrates:* a registry (metadata store + artifact store) recording version, training
  data range, metrics (dir-acc, Brier, ECE, backtest R), promotion state, lineage.
- *Affects:* Vol 05, 06, 15 (promotion), 21 (metadata), 25 (deploy/rollback), 27 (monitor).
- *When:* **before** we have >2 live models (soon — NSE already added a second).
- *Benefits:* safe multi-model growth; instant rollback; reproducibility; audits.
- *Trade-offs:* a store to run; start as a JSON/DB table + artifact dir, not MLflow yet.

**Vol 33 — Background Jobs & Scheduling**
- *Why:* the **priority work — Forward Testing (Vol 18)** and nightly retrain (Vol 15) —
  are *jobs*. There is no job architecture (scheduler, idempotency, retries, monitoring).
- *Problem:* without it, forward-testing/retrain are fragile scripts with no observability.
- *Integrates:* a scheduler + job runner (start: APScheduler/cron; later: RQ/Celery);
  idempotent jobs; run journal.
- *Affects:* Vol 15, 18, 25, 27 (job metrics/alerts).
- *When:* **now** — it is a dependency of Vol 18.
- *Benefits:* reliable forward-testing/retrain; retries; visibility.
- *Trade-offs:* a runtime component; keep it minimal in-process first.

**Vol 34 — Audit Logging & Compliance**
- *Why:* SEBI posture (Vol 03/24) needs a **tamper-evident record of every recommendation
  shown and every user action** — beyond the prediction store.
- *Problem:* compliance defensibility and incident forensics are under-specified.
- *Integrates:* append-only audit log (recommendation-shown, disclaimer-shown, user-action,
  model-version); retention.
- *Affects:* Vol 13, 24, 21.
- *When:* **before** any monetisation / public users; design now.
- *Benefits:* regulatory defensibility; trust; forensics.
- *Trade-offs:* write volume; mitigated by append-only + retention policy.

### 🟡 MEDIUM

**Vol 35 — Sequence Diagrams & State Machines** (cross-cutting)
- *Why:* the mandate + clarity. Missing: recommendation lifecycle state machine
  (DRAFT→SHOWN→OPEN→WIN/LOSS/EXPIRED), forward-test resolution sequence, retrain-promotion
  sequence, conversation orchestration sequence.
- *When:* alongside the volumes they clarify (18, 15, 07, 13). *Benefits:* onboarding,
  correctness. *Trade-offs:* keep diagrams in-repo (mermaid) so they don't rot.

**Vol 36 — User Journeys** — onboarding, the daily NSE loop, paper-trade→track, "why WAIT",
portfolio. *Why:* aligns UX/product with the engines; *When:* before frontend
componentisation (Vol 22). *Trade-offs:* minimal.

**Vol 37 — Caching & Performance** — formalise what's cached (sector, indices, features),
TTLs, invalidation, and latency budgets (single analysis ≤10s, screener ≤40s). *When:*
when the screener/portfolio load grows. *Affects:* Vol 19, 09, 11.

**Vol 38 — Notifications** — watchlist "TAKE appeared", ops alerts (drift, outage). *Why:*
core to the mobile/product loop; *When:* Phase 3 (with User Profile + PWA). *Affects:*
Vol 16, 23, 27.

### 🔵 FUTURE

**Vol 39 — Disaster Recovery** — backup/restore, RTO/RPO, model-artifact recovery. *When:*
at productisation. **Config Management & Feature Flags** — fold into Vol 19/25 initially;
a flag system when engine rollouts need it. **Aegis Orchestrator** — the explicit
composition layer; *today `AnalysisService` suffices* — promote to a documented
Orchestrator volume only if engine count/composition complexity grows.

---

## 5. What we deliberately DECLINE (restraint = architecture too)

A CTO's job includes saying no. These are **not** added now, with reasons:

- **Event bus / message-queue architecture (Kafka/etc.):** premature for a modular
  monolith. Use **in-process events/hooks** if decoupling is needed; revisit a bus only at
  Phase 4 scale. Adding it now buys complexity, not value.
- **Plugin architecture:** the `stream/` provider interface is already plugin-like; a
  formal plugin framework is speculative generality. Defer until a third-party actually
  needs to extend Aegis.
- **Microservices, native mobile, admin/analytics suites:** unchanged from the book —
  deferred until proof + demand. Restraint preserved.

---

## 6. Prioritized summary

| Priority | Volumes | Justification |
|---|---|---|
| 🔴 Critical | 29 ADR | Ten-year memory; near-zero cost; enables everything else's governance |
| 🟠 High | 30 Domain Model · 31 API Contracts · 32 Model Registry · 33 Background Jobs · 34 Audit Logging | These are *dependencies of the priority work* (Forward Testing needs jobs; multi-model needs a registry) and of productisation (contracts, audit) |
| 🟡 Medium | 35 Diagrams/State · 36 User Journeys · 37 Caching/Perf · 38 Notifications | Clarity & product-loop value; do alongside the features they serve |
| 🔵 Future | 39 DR · Config/Flags · Orchestrator | Real at scale; premature now |

---

## 7. Updated implementation roadmap (architecture + build, integrated)

Dependencies in **(parens)**. Nothing implemented without approval per the mandated process.

### Phase 0 — Governance & contracts (docs, days, no runtime)
- Vol 29 ADR (back-fill 8 decisions) · Vol 30 Domain Model · Vol 31 API Contracts.
- *Goal:* lock the language and decisions before building the priority engine.

### Phase 1 — Prove the edge (the point of the whole project)
- **Vol 33 Background Jobs** → **Vol 18 Forward Testing** → **Vol 13 Historical Memory →
  Postgres (Vol 21)** → **Vol 32 Model Registry** → **Vol 34 Audit Logging.**
- (Vol 18 needs 33; the registry + audit make it trustworthy & governed.)
- *Goal:* a real, logged, governed **live track record**. This is the gate to everything.

### Phase 2 — Decision value (parallel-safe, no edge claims)
- Vol 11 Portfolio · Vol 07 Conversation orchestrator · Vol 10 News · Vol 35 Diagrams ·
  Vol 36 User Journeys.
- *Goal:* richer, explainable, analyst-grade decisions on top of the proven pipeline.

### Phase 3 — Productise (only if Phase 1 shows a real live edge)
- Vol 16 User Profile · Vol 24 Auth/Security · Vol 20/31 API v1 · Vol 22 frontend
  componentise · Vol 23 PWA · Vol 27 Observability/drift · Vol 37 Caching · Vol 38
  Notifications.
- *Goal:* multi-user, mobile, monitored product — on proof.

### Phase 4 — Scale (only if demand + scale justify)
- Vol 39 DR · service extraction · event bus · Redis · native mobile · B2B API.

---

## 8. Success-criteria verification

- ✅ Research integrity preserved — reinforced (Vol 32 registry + Vol 34 audit make it
  *governed*, not just tested).
- ✅ Prediction Engine protected as core IP — untouched; registry formalises its lifecycle.
- ✅ LLM stays orchestration/explanation — Vol 31 makes the "no-predict" tool schema
  *enforceable by contract*.
- ✅ Maintainable by a small team — additions are docs + light runtime; we *declined* the
  heavy items (bus, plugins, microservices).
- ✅ Scales cleanly as engines are added — Model Registry + Domain Model + Contracts are
  exactly the seams that make adding engines safe.
- ✅ Supports future brokers/mobile/enterprise — Contracts + Auth + PWA + DR path defined,
  deferred to when justified.

**Reviewer's one-line verdict:** *Add the governance and AI-ops layer the book is missing
(ADR, Domain Model, Contracts, Model Registry, Background Jobs, Audit) — because the
priority work depends on it — and decline the enterprise complexity that does not yet earn
its keep. Then build Forward Testing.*
