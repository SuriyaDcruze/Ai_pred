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
| `WS /ws/signals` | live chart + signal stream |

### `/forward/*` (Forward Testing, `app/api/forward.py`)
| Endpoint | Purpose |
|---|---|
| `POST /forward/prediction` | record a BUY/SELL recommendation (`201`; `409` duplicate; `422` invalid) |
| `GET /forward/prediction/{id}` | one record (`404` if unknown) |
| `GET /forward/active?symbol=` | open predictions |
| `GET /forward/completed?limit=&symbol=` | resolved predictions |
| `GET /forward/stats?symbol=` | R-based aggregate stats |
| `GET /forward/summary?symbol=` | stats + honest confidence read + sample-size disclaimer |

Thin adapters over the M2 store / M3 engine — **no model logic, no engine imports** (the
LLM/models are never invoked here; the API only persists and reports recommendations the
engines already produced).

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
