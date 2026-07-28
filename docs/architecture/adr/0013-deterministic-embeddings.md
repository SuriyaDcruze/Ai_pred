# ADR 0013 — Deterministic embeddings (no training)

- **Status:** Accepted
- **Date:** 2026-07 (Sprint 3)
- **Deciders:** Architecture / CTO

## Context
The `memory_embeddings` satellite (Sprint 2, ADR 0007) stores a vector per historical decision
for similarity. Sprint 3 must fill it. The vector must be **deterministic** (no stochastic
behaviour) and require **no training** — consistent with "Similarity performs no inference and
no training" and the frozen engines (ADR 0002/0003).

## Decision
Embedding scheme **`sim-emb-1`** = the **L2-normalised** `sim-fv-1` feature vector (dim 100).
- Deterministic: identical feature vector → identical embedding, bit-for-bit.
- Unit-length: every embedding on the unit sphere → cosine similarity is well-behaved and
  reduces to a dot product. A zero vector stays zero.
- Stored via `MemoryStore` only; idempotent by `(prediction_id, embedding_kind)`; filling the
  `context_v1` placeholder the Memory Builder created.

## Alternatives considered
- *Fixed-seed random projection* for dimensionality reduction — viable and reserved for a
  future `sim-emb-2` if 100-dim proves too large; deferred (no need yet, and it adds a seed to
  manage).
- *A trained autoencoder / learned embedding* — rejected: training + non-determinism + model
  artifact; out of scope and against the honesty anchors.
- *Store the raw feature vector unnormalised* — rejected: makes cosine sensitive to magnitude;
  normalising once at store time is cleaner.

## Consequences
- **Positive:** reproducible, cheap, no artifact; unit-sphere geometry simplifies search;
  version-stamped (`embedding_version`) so a scheme change coexists with old vectors.
- **Negative / accepted:** the embedding carries no more information than the feature vector
  (it *is* the normalised vector) — acceptable for explainability; a learned embedding is a
  future option behind a new `embedding_version`.
- **Storage note:** the frozen table has no version columns, so
  `embedding_version`/`feature_version` are packed into `model_name` (`"sim-emb-1/sim-fv-1"`).
