# Volume 04 — System Architecture

## 1. Architectural style: modular monolith (deliberate)

Aegis is a **single FastAPI application** with strong internal module boundaries — a
*modular monolith*, not microservices. This is a deliberate CTO decision for the stage
(pre-live-proof, single developer, single deployable):

- **Why not microservices now:** 20 independently-deployable services would multiply
  ops cost (20 deploys, networking, observability, data consistency) for **zero benefit**
  at current scale. It is textbook over-engineering.
- **Why a *modular* monolith:** clean boundaries + explicit contracts mean any engine can
  be **extracted into a service later, cheaply**, *if and when scale demands it*. We pay
  that cost only when it's justified.

> Design for extraction; defer the extraction.

## 2. The module map (current → target)

```
                          ┌──────────────────────────────┐
   Market Data            │        API Gateway           │   ← app/api/main.py
   stream/ (Yahoo,        │   REST + WebSocket + (auth)   │
   Binance)               └──────────────┬───────────────┘
        │                                │
        ▼                                ▼
   ┌─────────────┐              ┌──────────────────┐
   │  Features   │──────────────▶  Service Layer   │   ← app/service.py (orchestrator)
   │ features/   │              └───┬───────┬──────┘
   │ indicators/ │                  │       │
   └─────────────┘                  ▼       ▼
                          ┌───────────────┐ ┌───────────────┐
                          │ PREDICTION    │ │  OUTCOME       │   ← the core IP
                          │ ENGINE (dir)  │ │  ENGINE        │
                          │ ai/sklearn... │ │ ai/outcome...  │
                          └───────┬───────┘ └───────┬───────┘
                                  └────────┬────────┘
                                           ▼
   ┌──────────────────────────────────────────────────────────────┐
   │ INTELLIGENCE LAYER (consumes model outputs, never predicts)   │
   │  Market state · Sector (sector.py) · Similarity · News        │
   │  Risk (risk/) · Decision+Rules (decision/) · Screener         │
   │  → intelligence.py assembles the explainable recommendation   │
   └───────────────────────────────┬──────────────────────────────┘
                                    ▼
   ┌───────────────┐   ┌────────────────────┐   ┌───────────────────┐
   │ Conversation  │   │  Historical Memory │   │ Forward Testing   │
   │ (LLM, Vol 07) │   │  + Paper Trading   │   │ (live proof, V18) │
   │ reads outputs │   │  tracking/ (SQLite)│   │  🔴 to build      │
   └───────┬───────┘   └────────────────────┘   └───────────────────┘
           ▼
   ┌───────────────┐
   │  Frontend     │   ← app/dashboard/ (glacier UI, mobile-first)
   └───────────────┘
```

## 3. The non-negotiable invariants

1. **Prediction-Model independence.** Only the Prediction & Outcome engines emit
   Direction/Probability/Confidence/Plan/Recommendation. The Conversation (LLM) layer and
   every other module **consume** these structured outputs. Enforced by contract: the LLM
   is wired to *read* engine responses, never to compute predictions. Any code path where
   an LLM produces a market call is an architecture violation.
2. **No order execution.** No module places orders or touches a brokerage account. This
   is a documented invariant, not an accident.
3. **Honest evaluation is in the pipeline, not the pull request.** `training/` +
   `evaluation/` + `backtest/` enforce purged walk-forward, leakage tests, and
   champion/challenger. A model artifact only ships if it passes.
4. **Shared feature pipeline.** Training and inference both go through
   `features/engineering.py::FeatureBuilder` so they can never drift.

## 4. Primary data flow (a recommendation)

```
User picks symbol
  → Service fetches candles (stream/, with fallback)
  → FeatureBuilder → 45 features (+ context features for explainability)
  → Prediction Engine → direction probs (calibrated)
  → Outcome Engine → P(target-before-stop) → TAKE / VETO
  → Risk Engine → entry / stop / targets / size
  → Intelligence → market state + sector + similarity + reasons
  → Decision: BUY / SELL / WAIT (WAIT unless direction≠WAIT AND outcome=TAKE)
  → API response (structured) → Frontend card / Conversation explanation
  → (future) Forward-Testing logs it + scores it live
```

## 5. Technology choices & trade-offs

| Concern | Choice | Why / trade-off |
|---|---|---|
| Language | Python 3.12 | ML ecosystem; single language across ML + API |
| API | FastAPI (async) | Modern, typed, WS support; monolith is simplest deployable |
| Models | scikit-learn (logistic, HistGB) | **Beat the deep net**; trains in seconds on CPU; interpretable |
| Deep net | PyTorch (kept as baseline) | Documented loser; retained only for comparison |
| Data | Yahoo (NSE/stocks), Binance (crypto) | Free, no keys; Yahoo delayed ~15min (accepted for daily) |
| Storage | SQLite (tracker) → Postgres (target) | SQLite fine now; Postgres for memory/multi-user (Vol 21) |
| Frontend | Single-file HTML + lightweight-charts | Zero build; needs componentising to scale (Vol 22) |
| Deploy | Render / HF / Docker | Simple; CI/CD + artifact mgmt to harden (Vol 25) |

## 6. Cross-cutting concerns (where each lives in the book)

- **Security & auth** → Vol 24 (currently minimal; single-user).
- **Persistence & schema** → Vol 21.
- **Observability** → Vol 27 (logs today; metrics + drift later).
- **API versioning & contracts** → Vol 20.
- **Testing** → Vol 26 (~230 tests, incl. leakage/future-invariance).

## 7. Scalability & the extraction path

The monolith scales vertically far beyond current needs. When (if) a specific engine
becomes a bottleneck or needs independent scaling/ownership, extract it in this order of
ease (already loosely coupled): **Screener/Intelligence** (stateless, CPU-heavy) →
**Conversation** (LLM I/O bound) → **Forward Testing** (write-heavy) → **Prediction/
Outcome** (model serving). Each already communicates via structured
function/HTTP-shaped contracts, so extraction is a transport change, not a rewrite.

## 8. Failure handling (principles)

- Data-source down → mirror/poll fallback (Binance 451 → `data-api.binance.vision`;
  Yahoo → cached).
- An intelligence sub-engine throwing → it degrades to "unavailable", the core
  recommendation still renders (the veto layer and signal never break on context).
- Model artifact missing → graceful fallback chain (sklearn → deep net → heuristic),
  each logged.
- Every failure is logged; none is silent.
