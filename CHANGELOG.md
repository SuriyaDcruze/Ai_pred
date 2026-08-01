# Changelog

All notable, milestone-level changes to AEGIS AI. This project ships **sprint by sprint**; each sprint
is frozen at a tagged release. Versions follow the `app/__init__.py` platform version. Every sprint is
**additive** — no sprint changes how predictions are made, resolved, or stored, and none touches the
immutable Prediction/Outcome engines (ADR 0002/0003).

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/).

## [0.7.0] — Agent Engine · tag `v0.7.0-agent-engine` (2026-08)
**Added** — the **Agent Engine** (`app/agent/`): a deterministic planning + permissioned
tool-execution layer over the existing read-only engines, with an approval gate and immutable audit
trail. Never predicts or advises; invokes no engine directly (offline stub invokers ship).
- Domain model (`agt-1`), Tool Registry (`tool-1`), deterministic Planner (`plan-1`), Permission
  Engine with a metadata safety floor (`perm-1`), Executor (registry-gated, audited, `exec-1`),
  advisory LLM Planning Adapter (`planllm-1`), and a thin `/agent/*` REST API (`agent-api-1`, 9
  endpoints).
- ADRs 0035–0041. As-built: `docs/architecture/agent-engine.md`. +104 tests → **998 passing**.
- **Breaking changes:** none. One additive router mount in `app/api/main.py`; version bump only.

## [0.6.0] — Conversation Intelligence Engine · tag `v0.6.0-conversation-intelligence` (2026-07)
**Added** — a read-only conversational **explanation** layer (`app/conversation/`) over Decision
Intelligence: domain model (`cnv-1`), intent detection (`int-1`), retrieval-through-DI (`ret-1`),
prompt builder (`prm-1`), conversation engine (`eng-1`), provider-independent LLM adapter (`llm-1`),
and a thin `/chat/*` REST API. The LLM explains only. ADRs 0029–0034. +117 tests → 894 passing.

## [0.5.0] — Decision Intelligence Engine · tag `v0.5.0-decision-intelligence` (2026-07)
**Added** — the synthesis + serving layer (`app/decision_intelligence/`, `di-1`) over the four prior
engines: compose → evidence/explanation → composite (evidence-quality) confidence → `/intelligence/*`.
Read-only, deterministic, reuse-not-recompute; no new edge, no advice. ADRs 0023–0028.

## [0.4.0] — Learning Engine · tag `v0.4.0-learning-engine` (2026-07)
**Added** — behavioural analytics over history (`app/learning/`, `lrn-1`/`lds-1`): dataset, patterns,
statistics, evidence-bound recommendations, `/learning/*`. Read-only; append-only satellite storage
(migrations 0006–0009). ADRs 0017–0022.

## [0.3.0] — Similarity Engine · tag `v0.3.0-similarity-engine` (2026-07)
**Added** — "I've seen this setup before" (`app/similarity/`): deterministic feature vectors,
embeddings (no training), filter-first brute-force cosine k-NN, `/memory/similar*`. ADRs 0011–0016.

## [0.2.0] — Historical Memory · tag `v0.2.0-historical-memory` (2026-07)
**Added** — store every prediction + outcome (`app/memory/`): satellite-table architecture,
composed-on-read memory record, retrieval via PredictionStore, `/memory/*` (migrations 0002–0005).
ADRs 0007–0010.

## [0.1.0] — Forward Testing · tag `v0.1.0-forward-testing` (2026-07)
**Added** — the live-proof engine (`app/forward_testing/`): engine, resolver, state machine, monitor,
`/forward/*` REST API, dashboard (migration 0001). Foundational ADRs 0001–0006. The one edge that
matters remains the Outcome Engine (backtest-only) — see `docs/RESULTS.md`.
