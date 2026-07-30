# ADR 0028 — Decision Intelligence versioning & compose-on-read (no persistence)

- **Status:** Accepted
- **Date:** 2026-07 (Sprint 5)
- **Deciders:** Architecture / CTO

## Context
A composed Decision Intelligence object is **derived** from four upstream engines and will change
when either the source data or the composition *method* changes. Consumers (the API, the future GPT
layer) need to know which method produced an object and to detect when a stored/snapshotted object is
**stale**. And, per ADR 0005/0018, all of this must live within the existing architecture without
altering any Sprint 1–4 table.

## Decision
Every object carries a single `decision_intelligence_version` (`di-1`) stamping the method, **and**
records the **upstream** versions it composed (`prediction_model_version`, `outcome_model_version`,
`feature_version`, `embedding_version`, `learning_version`, `dataset_version`) so a consumer can
detect staleness. The REST layer exposes an API `schema_version` (the payload shape) alongside the DI
version. Determinism is proved by **SHA-256 checksums** over the ordered object / evidence /
confidence (volatile fields excluded), so the same corpus + method always yields the same object and
the same checksums. A method change is a new `di-1`→`di-2`, never an edit. **Storage is
compose-on-read: the engine writes nothing and adds no migration** (Sprint 3 precedent). An optional
append-only `decision_intelligence_runs` audit/snapshot table (`0010`) was considered and
**deferred** — it is not needed for the read-only serving layer and can be added later without
changing the contract.

## Alternatives considered
- *Persist every composed object* — rejected (deferred): compose-on-read makes recompute == cache at
  current volumes and keeps the write-surface empty; snapshotting is a future auditability option.
- *One `version` field* — rejected: method, payload-shape, and the six upstream versions evolve
  independently; collapsing them breaks staleness/compatibility signalling.
- *A new database / modifying a prior table* — rejected: violates ADR 0005 + the read-only guarantee.

## Consequences
- **Positive:** every object is traceable to its method + upstream versions; staleness is detectable;
  objects are reproducible bit-for-bit; zero database impact this release.
- **Negative / accepted:** several version fields to reason about (justified by independent
  evolution); no persisted audit trail yet (deferred).
- **Enforced by:** determinism/checksum tests; deterministic-id tests; no-write tests; the unchanged
  migration set (no `0010` added); unchanged-Sprint-1–4 tests.
