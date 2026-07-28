# ADR 0015 — Retrieval integration via optional dependency injection

- **Status:** Accepted
- **Date:** 2026-07 (Sprint 3)
- **Deciders:** Architecture / CTO

## Context
The `/memory/similar` contract lives on Sprint 2's `RetrievalEngine` and returned *unavailable*.
Sprint 3 must **activate** it with the Similarity Search Engine — but the search engine already
**depends on** `RetrievalEngine` (for candidate enumeration), so a naive back-reference would
create both a construction cycle and an **import cycle** (`retrieval.py` ↔ `search.py`).

## Decision
Wire the engine into retrieval by **optional dependency injection via a setter**:
- `RetrievalEngine` gains an optional `similarity_engine` (default `None`) + `set_similarity_
  engine()`. The engine is created **second** and injected — breaking the construction cycle
  (dependency inversion).
- `retrieval.py` imports **nothing** from `app.similarity` at module load: the injected engine
  is **duck-typed** and the single type reference (`SimilarityError`) is a **lazy import inside
  the method** — so there is no import cycle.
- `similar()`: no engine → the documented *unavailable* (unchanged); engine present → delegate
  and map to the result. An **unexpected** engine failure degrades gracefully to *unavailable*;
  typed errors surface.

## Alternatives considered
- *Constructor injection only* — insufficient alone: the engine doesn't exist when retrieval is
  built. (A constructor kwarg is offered too, but the setter is the primary path.)
- *Move candidate enumeration off `RetrievalEngine`* — larger change; the existing read API is
  exactly what search needs.
- *Import the engine at module top in `retrieval.py`* — rejected: import cycle.

## Consequences
- **Positive:** the one Sprint 2 file touched (`retrieval.py`) changes **additively**; disabled
  behaviour is byte-identical (Sprint 2's 47 retrieval/API tests pass unchanged); no import
  cycle; the engine is swappable/testable.
- **Negative / accepted:** duck-typing loses static type-checking at the seam — mitigated by
  tests and a stable, documented interface (`search_by_prediction` / `search`).
- **Enforced by:** a test asserting no top-level `app.similarity` import in `retrieval.py`.
