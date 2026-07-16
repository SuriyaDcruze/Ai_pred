# Volume 28 — Future Roadmap

## The honest sequencing principle
> Prove the edge before scaling the platform. Build for trust, not hype. Every promotion
> passes purged walk-forward + the untouched final test, or it does not ship.

## Phase 1 — Prove the edge (the only thing that matters now)
- ⭐ **Forward Testing engine** (Vol 18): log every AI TAKE call, resolve it live, report
  live-vs-backtest honestly. **Priority #1.**
- **Historical Memory → Postgres** (Vol 13/21): the durable prediction store it needs.
- Let it run **weeks-to-months** → accumulate 50–100+ live TAKE trades → an honest verdict.

## Phase 2 — Deepen decision value (parallel-safe, no edge claims)
- **Portfolio Intelligence** (Vol 11): "₹X → allocation" with risk-based sizing.
- **Conversation orchestrator** (Vol 07): LLM as read-only tool-router; compare flows,
  "what changed since yesterday", teaching.
- **News Intelligence** (Vol 10): Indian sources + event calendar (context, event-risk).

## Phase 3 — Productise (only once Phase 1 shows a real live edge)
- **User Profile + Auth + Security** (Vol 16/24): multi-user, watchlists, roles.
- **API v1 + gateway** (Vol 20): versioning, auth, rate limits.
- **Frontend componentisation + PWA** (Vol 22/23): installable mobile.
- **Observability + drift** (Vol 27): production model monitoring.

## Phase 4 — Scale (only if demand & scale justify)
- Extract heavy engines to services (Vol 04 §7); Redis cache; task queue.
- Native mobile (only if PWA limits bite).
- B2B API for the honest track record.

## What we will NOT do (documented non-goals)
- ❌ Chase directional accuracy past ~61% (10 experiments proved it's noise).
- ❌ Build microservices, native apps, or admin/analytics before there's proof & demand.
- ❌ Let an LLM predict the market.
- ❌ Execute orders / automate real money.
- ❌ Monetise "profits" before a live track record exists.

## Success, restated
Aegis succeeds if it becomes the **most trusted, transparent, honestly-validated NSE
market-intelligence platform** — measured by decision quality, trade selection,
explainability, reproducibility, and user trust. Not by a percentage on a leaderboard.

## The living document
This book is versioned with the repo. Every implemented volume updates its status; every
architecture change updates its volume — **in the same commit as the code** (the
docs-before-push rule). The Architecture Book is the permanent technical foundation of
Aegis AI.
