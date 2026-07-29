# ADR 0022 — Learning versioning & append-only satellite storage

- **Status:** Accepted
- **Date:** 2026-07 (Sprint 4)
- **Deciders:** Architecture / CTO

## Context
Learning artifacts (datasets, patterns, statistics, recommendations) are **derived** from
completed history and will change when either the source data or the *method* changes. Consumers
(the API, Decision Intelligence, the GPT layer) need to know which method produced a number and to
detect when a stored artifact is stale. And, per ADR 0005/0018, all of this must live in the one
`prediction_history.db` without ever altering a Sprint 1–3 table.

## Decision
Three independent version stamps travel on every artifact:
- **`learning_version`** (`lrn-1`) — the analysis *method* (how a dataset/pattern/stat/rec is
  derived). A method change is a new version, not an edit.
- **`dataset_version`** (`lds-1`) — the *shape* of a learning record. Exposed by the REST API as
  `schema_version`.
- **API `schema_version`** (`1`) — the shape of the HTTP payloads (forward-compatible evolution).
Determinism is proved by **SHA-256 checksums** over the ordered artifacts (volatile fields
excluded), so the same corpus + method + params always produce the same content and the same
`run_id`. Storage is **append-only satellite tables** in the existing database — `learning_runs`
(`0006`), `learning_patterns` (`0007`), `learning_pattern_stats` (`0008`),
`learning_recommendations` (`0009`) — each its **own** table, all **derived + rebuildable**, added
by append-only migrations that change **no** prior table. The overall release is versioned
`v0.4.0` (`app/__init__.py`), tag `v0.4.0-learning-engine`.

## Alternatives considered
- *One `version` field* — rejected: method, record-shape, and API-payload shape evolve
  independently; collapsing them forces a false lock-step and breaks compatibility signalling.
- *A new database / modify Sprint 1–3 tables* — rejected: violates ADR 0005 and the read-only
  guarantee (ADR 0018); satellite tables keep every change additive and rollback a clean drop.
- *Random UUID artifact ids* — rejected: ids are deterministic functions of identity + version, so
  the same logical artifact always keys the same (reproducibility + dedup).

## Consequences
- **Positive:** every number is traceable to its method; staleness is detectable; the schema grows
  without breaking compatibility; artifacts are reproducible bit-for-bit and rebuildable.
- **Negative / accepted:** three version fields are more to reason about than one — justified by
  their independent evolution; a method change requires a deliberate version bump.
- **Enforced by:** determinism/checksum tests; deterministic-id tests; append-only migration tests
  (fresh DB + populated-DB upgrade leave every prior table unchanged); row round-trip tests.
