# The Aegis Architecture Book

> The permanent technical foundation of Aegis AI. Every future implementation follows
> what is written here. This book is **design**, not code. It is versioned with the
> repo and updated whenever the architecture evolves.

**Status:** v0.1 — Table of Contents + current-state assessment (this document).
The individual volumes are authored on approval, in the priority agreed below.

---

## How to read this book

- **Volumes 00–03** are the *why* (executive summary, vision, requirements, business).
- **Volumes 04–18** are the *engines* — one per bounded capability, each with the same
  template (Purpose · Responsibilities · Inputs/Outputs · Architecture · Data · API ·
  Failure/Logging · Security · Testing · Prediction-Model integration · LLM integration ·
  Future).
- **Volumes 19–27** are the *platform* (backend, API, DB, frontend, mobile, security,
  deploy, testing, observability).
- **Volume 28** is the *roadmap*.

**Two invariants every volume must respect** (non-negotiable, from the mandate):
1. **The Prediction Model is the core IP.** Only it produces Direction / Probability /
   Confidence / Entry / Stop / Targets / Holding / Recommendation. No LLM ever predicts.
2. **Honest evaluation is architectural, not optional.** Purged walk-forward, untouched
   final test, leakage prevention, and "backtest ≠ live" labelling are first-class
   requirements, not afterthoughts.

---

## Table of Contents

| # | Volume | Scope (one line) | Current state |
|---|--------|------------------|---------------|
| 00 | [**Executive Summary**](00-executive-summary.md) | What Aegis is, the one verified edge, the honest limits | ✅ written |
| 01 | [**Vision**](01-vision.md) | India-first market-intelligence platform; not a bot | ✅ written |
| 02 | [**Product Requirements**](02-product-requirements.md) | User stories, functional + non-functional reqs | ✅ written |
| 03 | [**Business Goals**](03-business-goals.md) | Who it's for, honesty-as-moat, SEBI posture, monetisation | ✅ written |
| 04 | [**System Architecture**](04-system-architecture.md) | Modular-monolith now → modular services later; data flow | ✅ written |
| 05 | [**Prediction Engine**](05-prediction-engine.md) | Calibrated logistic direction model (the core IP) | 🟢 built |
| 06 | [**Outcome Engine**](06-outcome-engine.md) | Target-before-stop meta-labeling (the verified edge) | 🟢 built |
| 07 | [**GPT / Conversation Assistant**](07-gpt-assistant.md) | LLM as orchestrator over Aegis services; never predicts | 🟡 basic |
| 08 | [**Market Intelligence**](08-market-intelligence.md) | Market state, trend/vol context, explainable report | 🟢 built |
| 09 | [**Sector Intelligence**](09-sector-intelligence.md) | NSE sector rotation ranking; context not edge | 🟢 built |
| 10 | [**News Intelligence**](10-news-intelligence.md) | Indian sources, event classification, impact | 🟡 basic sentiment |
| 11 | [**Portfolio Intelligence**](11-portfolio-intelligence.md) | "₹X → allocation" — sizing, diversification, correlation | 🔴 not built |
| 12 | [**Risk Engine**](12-risk-engine.md) | ATR stops, R-multiples, position sizing, portfolio risk | 🟢 built |
| 13 | [**Historical Memory**](13-historical-memory.md) | Store every prediction+outcome; foundation for learning | 🟡 SQLite tracker |
| 14 | [**Similarity Engine**](14-similarity-engine.md) | "I've seen this setup before" (explainability) | 🟢 built |
| 15 | [**Learning Engine**](15-learning-engine.md) | Meta-model + nightly champion/challenger retrain | 🟡 built, needs data |
| 16 | [**User Profile**](16-user-profile.md) | Preferences, watchlists, risk appetite, auth identity | 🔴 not built |
| 17 | [**Paper Trading**](17-paper-trading.md) | Log recommendations, score vs real future price | 🟡 tracker exists |
| 18 | [**Forward Testing**](18-forward-testing.md) ⭐ | The live-proof engine — turn backtest edge into a record | 🔴 the key gap |
| 19 | [**Backend Architecture**](19-backend-architecture.md) | FastAPI app, service layer, async, background tasks | 🟡 monolith |
| 20 | [**API Architecture**](20-api-architecture.md) | REST/WS contracts, versioning, gateway, rate limits, auth | 🟡 unversioned |
| 21 | [**Database Design**](21-database-design.md) | From SQLite → Postgres; schema for predictions/users/trades | 🟡 minimal |
| 22 | [**Frontend Architecture**](22-frontend-architecture.md) | Single-file dashboard → componentised app; state, theming | 🟡 one file |
| 23 | [**Mobile Architecture**](23-mobile-architecture.md) | Responsive web now; PWA / native path | 🔴 responsive only |
| 24 | [**Security Architecture**](24-security-architecture.md) | Auth, secrets, no-order guarantee, SEBI disclaimers, PII | 🔴 minimal |
| 25 | [**Deployment**](25-deployment.md) | Render/HF/Docker; envs; CI/CD; model artifact management | 🟡 basic |
| 26 | [**Testing Strategy**](26-testing-strategy.md) | Unit, leakage/future-invariance, walk-forward, e2e | 🟢 ~230 tests |
| 27 | [**Observability & Monitoring**](27-observability-monitoring.md) | Logs, metrics, drift detection, model/version tracking | 🔴 logs only |
| 28 | [**Future Roadmap**](28-future-roadmap.md) | Sequenced, honest, edge-proof-first | ✅ written |

Legend: 🟢 built & tested · 🟡 partial / needs hardening · 🔴 not built.
(Status reflects the *implementation*, not the *volume* — all 28 volumes are now written.)

---

## ✅ Book status: all 28 volumes authored (v1.0)

The Architecture Book is complete and is the permanent technical foundation. Per the
mandated process, **no module is implemented until its volume is reviewed and the build is
approved.** The recommended first implementation is **Volume 18 — Forward Testing** (the
live-proof engine); everything else waits on it.

---

## High-level assessment of the CURRENT architecture

### What it actually is today
A **well-structured Python FastAPI modular monolith** (~10k LOC, ~230 tests) with a
clean separation into `ai/`, `features/`, `training/`, `decision/`, `risk/`,
`stream/`, `evaluation/`, `intelligence`, `sector`, `screener`, and a single-file
dashboard. The service layer (`app/service.py`) orchestrates; the API (`app/api/main.py`)
exposes REST + WebSocket. Data comes from Yahoo (NSE/stocks) and Binance (crypto).

### Strengths (genuinely good, keep them)
- **The two-model core is real and validated in backtest** — direction (Prediction
  Engine) + outcome (Outcome Engine). This is the IP and it's sound.
- **Research integrity is baked in** — purged walk-forward, leakage/future-invariance
  tests, champion/challenger, calibration. Very few retail projects have this.
- **Clean module boundaries already** — most of the mandate's "engines" map to existing
  files with single responsibilities. The refactor to formal modules is small.
- **Honest evaluation everywhere** — backtest-vs-live is labelled; no inflated claims.

### Gaps vs. the enterprise vision (the real work)
- **No auth, no user profiles, no multi-tenant** — it's single-user today. (Vol 16, 24)
- **Persistence is a single SQLite tracker** — no Postgres, no prediction store, no
  audit trail. Historical Memory (Vol 13) and Forward Testing (Vol 18) need this.
- **No Portfolio Engine** (Vol 11) and **no live Forward-Testing engine** (Vol 18) —
  the two most valuable missing capabilities.
- **Conversation layer is rule-based + optional LLM** — not yet the intent-routing
  orchestrator the mandate describes (Vol 07).
- **Frontend is one 400-line HTML file** — fine for now, needs componentising to scale
  (Vol 22); mobile is responsive-web only (Vol 23).
- **Observability is logs only** — no metrics, no drift detection in production (Vol 27).
- **API is unversioned, no gateway/rate-limit/auth** (Vol 20).

### The CTO's honest architectural opinion (read this first)
1. **Do NOT build 20 independently-deployable microservices now.** For a pre-revenue,
   pre-live-proof, single-developer product, microservices is textbook over-engineering
   — it multiplies ops cost and slows iteration for zero benefit at this scale. The
   correct architecture is a **modular monolith with clean engine boundaries** (which we
   nearly have), designed so any engine *can* be extracted into a service later *if and
   when scale demands it*. The Architecture Book will define the boundaries and contracts
   so that future extraction is cheap — without paying the cost today.
2. **The #1 architectural priority is Forward Testing (Vol 18), not enterprise scaffold.**
   Every positive number is backtest-only. No amount of architecture changes the fact
   that the edge is unproven live. The single highest-leverage build is the engine that
   turns the validated backtest edge into a **real, logged, live track record**. Auth,
   portfolios, mobile — all matter *after* there's proof worth scaling.
3. **Regulatory posture is an architecture concern, not a footnote.** SEBI: Aegis is
   decision-support/education, not registered advice. The architecture must enforce this
   — persistent disclaimers, an audit trail of every recommendation shown, and a hard
   no-order-execution guarantee (already true; make it a documented invariant).
4. **Preserve the Prediction Model's independence in the design.** The LLM/Conversation
   layer calls services and explains; it must be *architecturally incapable* of emitting
   a prediction. This is enforced by the API contract (the LLM only reads structured
   engine outputs) — Vol 07 will make this explicit.

### Recommended authoring order for the book (priority)
1. **Vol 00–04** (exec summary, vision, requirements, business, system architecture) —
   the foundation everything references.
2. **Vol 18 + 13 + 21** (Forward Testing, Historical Memory, Database) — the proof path.
3. **Vol 05–06 + 12** (Prediction, Outcome, Risk) — document the core IP precisely.
4. **Vol 07 + 11** (Conversation orchestrator, Portfolio) — the next user-facing value.
5. **Vol 20 + 24 + 16** (API, Security, User Profile) — productisation.
6. The remainder as the platform matures.

---

*Awaiting approval on scope and priority before authoring the full volumes. Nothing in
this book is implemented until its volume is reviewed and approved — per the mandated
development process.*
