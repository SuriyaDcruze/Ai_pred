# ADR 0012 — Deterministic, versioned feature vectors

- **Status:** Accepted
- **Date:** 2026-07 (Sprint 3)
- **Deciders:** Architecture / CTO

## Context
The Similarity Engine needs a numerical representation of a historical decision to compare by
distance. That representation must be **reproducible** (the same Memory Record must always
produce the same vector, across processes and runs) and **evolvable** (the encoding will
change over time). A learned/opaque encoder would break reproducibility and drag in training.

## Decision
Encode a Memory Record into a **fixed-length, deterministic feature vector** (`sim-fv-1`,
dimension **100**) with an immutable, ordered layout:
- **Enums → fixed one-hot vocabularies** (unknown → all-zeros).
- **Open categoricals (sector, model versions) → stable SHA-1 hashing** into fixed buckets —
  never Python's salted `hash()`.
- **Numerics → clamped min-max scaling** with documented bands; explicit **present flags** so
  a missing value is distinct from a real zero.
Any change to feature order, vocabulary, or normalisation requires a **new `feature_version`**
— never an in-place edit.

## Alternatives considered
- *Learned embeddings from the model* — rejected: non-deterministic, couples to the engines,
  and would need training (ADR 0002/0003 forbid touching the models here).
- *Python `hash()` for categoricals* — rejected: salted per process → non-reproducible buckets.
- *Raw feature dict passed downstream* — rejected: no fixed dimension; no distance metric.

## Consequences
- **Positive:** bit-for-bit reproducible; testable exactly; forward-compatible via
  `feature_version`; no model artifact, no training.
- **Negative / accepted:** hand-designed features (not learned) — fine for an explainability
  representation; fixed one-hot vocabularies must be extended via a version bump.
- **Enforced by:** determinism + encoding tests; the layout is a frozen `_SPECS` tuple.
