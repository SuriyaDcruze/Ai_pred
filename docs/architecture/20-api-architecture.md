# Volume 20 — API Architecture

## Purpose
Define the API surface, contracts, versioning, and the gateway concerns (auth, rate
limiting) needed before any public exposure.

## Status: 🟡 Built, unversioned, no auth — `app/api/main.py`

## Current endpoints (REST + WS)
| Endpoint | Purpose |
|---|---|
| `GET /health`, `/market`, `/history` | status, price, candles |
| `POST /analyze` | signal + rules + outcome for a symbol |
| `GET /outcome` | outcome-model TAKE/VETO |
| `GET /intelligence` | full explainable stock report (Vol 08) |
| `GET /sectors` | NSE sector ranking (Vol 09) |
| `GET /screener/nse` | today's NSE TAKE setups |
| `GET/POST /rules`, `/rules/check` | My Rules (Vol 12) |
| `GET /news`, `/risk-notice` | news sentiment, learning-mode banner |
| `POST /chat` | conversation (Vol 07) |
| `/calls*`, `/round`, `/autolog` | paper trading (Vol 17) |
| `/forward/*` | Forward Testing — record & score live recommendations (Vol 18, Sprint 1 M4) |
| `/memory/*` | Historical Memory — composed records, search, stats, timeline, GPT context (Vol 13, Sprint 2 M5) |
| `/memory/similar*` | Similarity Engine — cosine k-NN neighbours + honest stats (Vol 14, Sprint 3 M5) |
| `WS /ws/signals` | live chart + signal stream |

### `/forward/*` (Forward Testing, `app/api/forward.py`)
| Endpoint | Purpose |
|---|---|
| `POST /forward/prediction` | record a BUY/SELL recommendation (`201`; `409` duplicate; `422` invalid) |
| `GET /forward/prediction/{id}` | one record (`404` if unknown) |
| `GET /forward/active?symbol=` | open predictions |
| `GET /forward/completed?limit=&symbol=` | resolved predictions |
| `GET /forward/stats?symbol=` | R-based aggregate stats |
| `GET /forward/summary?symbol=` | stats + expectancy + backtest baseline + live-vs-backtest + honest confidence |
| `GET /forward/breakdown?by=&symbol=` | grouped aggregates by market/sector/timeframe/confidence/regime (422 on unknown dimension) |

The **Forward Testing dashboard** (`/dashboard/forward.html`, Sprint 1 M5) is a
presentation-only page over these endpoints — six sections (overview, live-vs-backtest,
breakdown, active, completed, timeline). All aggregation is server-side
(`app/api/forward_analytics.py`); the page never touches the DB.

Thin adapters over the M2 store / M3 engine — **no model logic, no engine imports** (the
LLM/models are never invoked here; the API only persists and reports recommendations the
engines already produced).

### `/memory/*` (Historical Memory, `app/api/memory.py`)
| Endpoint | Purpose |
|---|---|
| `GET /memory/record/{prediction_id}` | one composed Memory Record (404 if unknown) |
| `GET /memory/search` | filtered + keyset-paginated search |
| `GET /memory/statistics` | aggregate rollups + sample size (never computed in the API) |
| `GET /memory/timeline` | chronological records for a symbol / date window |
| `GET /memory/context` | bounded, deterministic GPT grounding bundle |
| `POST /memory/build/{prediction_id}` | enrich one prediction (idempotent) |
| `POST /memory/backfill` | enrich all resolved-but-unbuilt (idempotent); returns counts |
| `POST /memory/rebuild-aggregates` | recompute rollups from source |

Thin transport over the Retrieval Engine + Memory Builder (thin-controller pattern) — **no
business logic, no direct DB access, no engine imports**. Errors map to `404`/`422`/`500`
with no stack traces.

### `/memory/similar*` (Similarity Engine, `app/api/similarity.py`, Sprint 3 M5)
| Endpoint | Purpose |
|---|---|
| `GET /memory/similar/{prediction_id}` | k-NN neighbours of a prediction + honest summary + versions |
| `GET /memory/similar?prediction_id=` | same, target as a query parameter (+ candidate filters) |
| `POST /memory/similar/search` | same, target + filters in the request body |
| `GET /memory/similar/health` | engine enabled? + embedding/feature/search version + dimension |

Thin transport over the Similarity Search Engine (M3) — **no search algorithm in the API**.
The engine is injected into `RetrievalEngine` in the app lifespan (M4 setter). Never exposes
raw embeddings/feature vectors. Errors: `400` validation · `404` prediction/embedding ·
`409` version mismatch · `503` engine unavailable.

## Contracts
- Pydantic request/response models (`app/api/schemas.py`) — typed, validated.
- **Invariant:** every response that contains a prediction originates from an engine;
  there is no endpoint where an LLM produces one.

## Target hardening (before public/multi-user)
- **Versioning:** `/api/v1/...` prefix; deprecation policy.
- **Auth:** API keys / JWT (Vol 24) on all user/state-changing routes.
- **Rate limiting:** per-key limits (protect Yahoo/Binance quotas & CPU).
- **Gateway concerns:** CORS (already), request tracing, structured error envelope.
- **Idempotency** on write routes (paper trade logging).
- **OpenAPI** as the published contract (FastAPI auto-docs).

## Failure handling
- Consistent error envelope (`{ error, detail }`); 422 for bad input; graceful 5xx with
  request id. Never leak internals or fabricate data on error.

## Testing
- Endpoint smoke + schema validation; target: contract tests + auth tests.

## LLM integration
- The Conversation engine calls these endpoints as **read-only tools**; the tool schema
  excludes any prediction-producing operation (Vol 07).

## Future
- gRPC/internal contracts if engines are extracted; webhook/notification API (Vol 27);
  public read API for the honest track record (once it exists, Vol 18).
