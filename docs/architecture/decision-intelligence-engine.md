# Decision Intelligence Engine — as built (Sprint 5, Vol 07/08 sense, `v0.5.0`)

> The **synthesis + serving** layer after the four sprint engines. It **composes** the stored
> Prediction/Outcome/Risk verdict, Historical Memory context, Similarity neighbours, and the Learning
> Engine's validated observations into **one explainable, evidence-bound Decision Intelligence
> object**, then explains it, rates the *quality of its evidence*, and serves it at `/intelligence/*`.
> Deterministic, read-only, honesty-gated. Frozen at `v0.5.0`, tag `v0.5.0-decision-intelligence`.

> ⚠️ **Disambiguation.** This is **distinct** from the legacy `app/intelligence.py` (V3),
> `app/sector.py`, and Vol 08 market intelligence, which compute a *fresh* per-symbol analysis from
> live market data + the models directly. The Decision Intelligence Engine (`app/decision_intelligence/`)
> **composes what the four engines already produced** over the *historical* picture — it re-runs no
> model and reads no market data. (ADR 0023.)

## Overall architecture / data flow (component boundaries)
```
  FORWARD TESTING (Sprint 1) → PREDICTION/OUTCOME/RISK (immutable, stored verbatim)
        │
        ▼
  HISTORICAL MEMORY (Sprint 2) → SIMILARITY (Sprint 3) → LEARNING (Sprint 4)
        │   (four read-only surfaces; none imports Prediction/Outcome)
        ▼
  ┌──────────────────  DECISION INTELLIGENCE ENGINE (app/decision_intelligence/)  ───────────────┐
  │  Composition (M2)  →  Evidence & Explanation (M3)  →  Composite Confidence & Prioritisation (M4)│
  │     reads each engine's stored/derived output verbatim; recomputes nothing                     │
  └───────────────────────────────────────────┬──────────────────────────────────────────────────┘
                                               ▼
                     /intelligence/* REST API (M5, thin transport, read-only)
                                               ▼
        Dashboard  +  Decision Intelligence UI  +  GPT / Conversation assistant (Vol 07, FUTURE)
```

## Package structure & module responsibilities
`app/decision_intelligence/` (peer of `app/memory/`, `app/similarity/`, `app/learning/`):
- **`models.py` (M1)** — the domain model & composition contract: the canonical `DecisionIntelligence`
  object; canonical states `EMPTY`/`INSUFFICIENT_DATA`/`PARTIAL`/`COMPLETE`/`STALE`/`ERROR`; `di-1`
  version + `UpstreamVersions`; `Provenance`, `EvidenceRef`, `DecisionComponent` (each contributor
  owns exactly one section — **no subsystem populates another's**); deterministic immutable
  `decision_id` + SHA-256 checksum; serialization foundation.
- **`compose.py` + `providers.py` (M2)** — the **Composition Engine**: `SourceAdapter` + four
  duck-typed adapters (Prediction/Memory/Similarity/Learning) reading their subsystem verbatim; a
  fixed pipeline order; graceful degradation (thin → `INSUFFICIENT_DATA`, errored → `ERROR`, the whole
  object is never failed); required-prediction anchor; version tracking; `LearningPipelineProvider`
  reuses the Learning Engine's own pipeline.
- **`evidence.py` (M3)** — the **Evidence & Explanation Engine**: a deterministic evidence graph
  (root → subsystem → facet), a provenance resolver (no orphans), a For/Against breakdown (+ factual
  stored-figure conflict signals), a missing-evidence list (`INSUFFICIENT_DATA`/`NOT_AVAILABLE`/
  `NOT_SUPPORTED`), and a descriptive explanation + disclaimer.
- **`confidence.py` (M4)** — the **Composite Confidence & Prioritisation Engine**: an
  **evidence-quality** indicator (score/level/factors/penalties/strengths/warnings/explanation) — **not**
  a probability of success or a trading signal — plus conflict detection and a deterministic
  `prioritise()` (evidence strength only).
- **`app/api/intelligence.py` (M5)** — the thin `/intelligence/*` REST transport.

## Execution flow
For one prediction: **`compose`** (read the four engines → assemble the object) → **`explain`** (graph
+ provenance + For/Against + missing + explanation) → **`assess`** (composite evidence-quality
confidence + conflicts + prioritisation) → **serialise** (stable content + checksums). Each step is a
pure function of its input; the API just wires them.

## Read-only & deterministic guarantees
- **Read-only (ADR 0024):** imports **neither** the Prediction nor the Outcome engine (AST-guarded);
  reads the stored verdict via `PredictionStore`, memory via `RetrievalEngine`, neighbours via the
  Similarity read path, and Learning via its own pipeline; **writes nothing** (compose-on-read, no
  migration); changes no Sprint 1–4 table.
- **Reuse, never recompute (ADR 0024):** every composed figure equals its source engine's figure — no
  statistic is re-implemented (a reuse-regression test asserts this).
- **Deterministic (ADR 0028):** `decision_id` is a pure function of `(version, prediction_id)`;
  SHA-256 checksums over the ordered object / evidence / confidence (volatile fields excluded) prove
  identical inputs → identical output; the API response excludes wall-clock, so it is byte-identical
  across calls.
- **Honesty-gated:** thin/absent inputs → `INSUFFICIENT_DATA` per facet; graceful degradation to
  prediction-only; no new edge, no advice; conflicts recorded, never hidden.

## Evidence flow (M3)
Every composed element becomes a graph node with a **provenance** entry and **evidence references**
back to its source (`prediction_id` / neighbour id / `recommendation_id`). Validation guarantees **no
explanation without evidence, no evidence without provenance** (orphans/duplicates rejected). The
For/Against conflict signals are factual observations of stored figures (e.g. the outcome model's own
veto probability), never opinions.

## Confidence flow (M4)
Composite Confidence answers only *"how trustworthy is the assembled evidence?"* — derived
deterministically from completeness, consistency, provenance, sample breadth, and **conflicts**
(outcome/similarity disagreement, incomplete provenance, version mismatch). It is **not** a prediction:
a 0.95-confidence prediction with no support still scores **LOW** (thin evidence). Prioritisation
organises objects by evidence strength only — never outcome/future info, never an action. (ADR 0026.)

## API overview (`/intelligence/*`, M5, ADR 0027)
| Method · Path | Purpose |
|---|---|
| `GET /intelligence/{prediction_id}` | the complete composed object + evidence + explanation + confidence + prioritisation |
| `GET /intelligence/symbol/{symbol}` | Decision Intelligence for a symbol's latest stored prediction |
| `GET /intelligence/health` | infrastructure readiness + versions (no business logic) |
| `GET /intelligence/version` | API / Decision Intelligence / schema versions |

Thin transport: validates → invokes compose→explain→assess → serialises deterministically. Error
taxonomy `400/404/409/422/503`; single owner of the `/intelligence/*` sub-namespace (static routes
before the `{prediction_id}` catch-all); the legacy exact `GET /intelligence` is untouched.

## Versioning
`decision_intelligence_version = di-1` (method) · six recorded **upstream** versions (for staleness)
· API `schema_version` (payload shape). A method change is a new `di-1`→`di-2`, never an edit. (ADR
0028.)

## Storage
**None** — compose-on-read; the engine writes nothing and adds **no migration**. An optional
append-only `decision_intelligence_runs` audit/snapshot table was **deferred** (can be added later
without changing the contract).

## Honest note
Consistent with `docs/RESULTS.md`: the Decision Intelligence Engine **manufactures no edge**. It
composes and explains existing evidence; on today's near-empty corpus it degrades honestly to
"prediction + outcome verdict, with `INSUFFICIENT_DATA` for historical context." The only verified
edge remains the Outcome Engine (backtest-only).
