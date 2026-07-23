# Forward Testing API (`/forward/*`) — Reference

Complete reference for the Forward Testing REST API shipped in **Sprint 1** (Milestones
4–5). Implemented in `app/api/forward.py` (an `APIRouter` mounted in `app/api/main.py`),
backed by the `PredictionStore` and `ForwardTestingEngine` over
`data/prediction_history.db`.

**Design contract:** every endpoint is a thin adapter over the store/engine — no model
logic, no imports from the Prediction/Outcome engines (ADR 0006). All aggregation is
server-side (`app/api/forward_analytics.py`). Responses are structured JSON. All values that
depend on a resolved sample are reported **with sample size**; the API never presents a
handful of trades as proof.

Base path: `/forward`. All responses are `application/json`.

---

## Data types

### `PredictionRecord` (response object)
Returned (nested under `prediction`) by create and get, and in the `predictions` arrays of
active/completed. Creation fields are immutable; only the lifecycle fields change on resolve.

| Field | Type | Notes |
|---|---|---|
| `prediction_id` | string | server-generated hex id |
| `created_at`, `updated_at` | string (ISO-8601 UTC) | |
| `created_candle_ts` | int | epoch seconds of the origin bar |
| `symbol`, `exchange`, `timeframe`, `source` | string | |
| `current_price` | number | price at the call |
| `direction` | string | `BUY` / `SELL` / `WAIT` (model read) |
| `recommendation` | string | `BUY` / `SELL` (final decision) |
| `direction_prob`, `outcome_prob`, `decision_score` | number \| null | model outputs, verbatim |
| `entry`, `stop`, `target1`, `target2` | number \| null | risk plan |
| `market_regime`, `market_phase`, `sector`, `session`, `volatility_bucket` | string \| null | context |
| `similarity_score` | number \| null | |
| `context` | object | free-form context (JSON) |
| `prediction_model_version`, `outcome_model_version`, `feature_version` | string \| null | independent stamps |
| `status` | string | one of the 8 lifecycle states (below) |
| `resolved_at`, `resolved_price`, `resolution_reason` | string/number \| null | set on resolution |
| `realised_r` | number \| null | realised R-multiple (win = +R, stop = −1.0) |
| `holding_bars` | int \| null | bars held |
| `is_open`, `is_terminal` | bool | derived convenience flags |

**Lifecycle states:** `PENDING`, `ENTRY_TRIGGERED`, `ACTIVE` (open) → `TARGET_HIT`,
`STOP_HIT`, `EXPIRED`, `CANCELLED`, `COMPLETED` (terminal). A market-entry recommendation is
created **ACTIVE**.

### `Stats` object (used by `/stats`, `/summary`, and each breakdown group)
| Field | Type | Meaning |
|---|---|---|
| `total` | int | total predictions (store-wide; `/stats` only) |
| `open` | int | currently open (`/stats` only) |
| `resolved` | int | resolved trades in scope |
| `wins`, `losses` | int | |
| `win_rate` | number \| null | wins / resolved |
| `avg_r` | number \| null | mean realised R (== expectancy) |
| `total_r` | number | sum of realised R |
| `profit_factor` | number \| null | gross win R ÷ gross loss R (`∞` when no losses) |
| `max_drawdown_r` | number | peak-to-trough of the cumulative-R curve |
| `avg_holding_bars` | number \| null | |
| `open_risk_r` | number | R at risk across open trades (`/stats` only) |

---

## Endpoints

### `POST /forward/prediction`
Record a recommendation for forward testing. Only actionable **BUY/SELL** calls can be
recorded (a WAIT is not a trade).

**Request body** (`ForwardPredictionRequest`):

| Field | Type | Required | Default | Validation |
|---|---|---|---|---|
| `symbol` | string | ✅ | — | non-empty |
| `current_price` | number | ✅ | — | > 0 |
| `direction` | string | ✅ | — | `BUY`/`SELL`/`WAIT` (upper-cased) |
| `recommendation` | string | ✅ | — | `BUY`/`SELL` (upper-cased) |
| `created_candle_ts` | int | ✅ | — | > 0 |
| `exchange` | string | | `"NSE"` | |
| `timeframe` | string | | `"1d"` | |
| `entry`, `stop`, `target1`, `target2` | number | | null | > 0 if present |
| `direction_prob`, `outcome_prob` | number | | null | in `[0, 1]` |
| `decision_score`, `similarity_score` | number | | null | |
| `market_regime`, `market_phase`, `sector`, `session`, `volatility_bucket` | string | | null | |
| `context` | object | | null | |
| `prediction_model_version`, `outcome_model_version`, `feature_version` | string | | null | |
| `source` | string | | `"manual"` | |

**Responses**
- `201 Created` → `{ "prediction": PredictionRecord }` (status `ACTIVE`)
- `409 Conflict` → duplicate for the same `(symbol, timeframe, created_candle_ts, source)`
- `422 Unprocessable Entity` → validation failure (e.g. `recommendation="WAIT"`,
  `current_price<=0`, prob out of range)

**Example**
```bash
curl -X POST http://localhost:8000/forward/prediction \
  -H 'Content-Type: application/json' \
  -d '{
    "symbol": "RELIANCE.NS", "exchange": "NSE", "timeframe": "1d",
    "current_price": 2900.0, "direction": "BUY", "recommendation": "BUY",
    "created_candle_ts": 1721600000,
    "entry": 2900.0, "stop": 2850.0, "target1": 3000.0,
    "outcome_prob": 0.62, "sector": "Energy", "market_regime": "BULL",
    "prediction_model_version": "pred-2025-11", "feature_version": "feat-v3"
  }'
```
```json
{ "prediction": { "prediction_id": "a1b2…", "status": "ACTIVE", "symbol": "RELIANCE.NS",
  "recommendation": "BUY", "entry": 2900.0, "stop": 2850.0, "target1": 3000.0,
  "is_open": true, "is_terminal": false, "realised_r": null /* … */ } }
```

---

### `GET /forward/prediction/{prediction_id}`
Fetch one record.
- `200 OK` → `{ "prediction": PredictionRecord }`
- `404 Not Found` → unknown id

```bash
curl http://localhost:8000/forward/prediction/a1b2c3
```

---

### `GET /forward/active`
Open predictions (PENDING / ENTRY_TRIGGERED / ACTIVE), oldest first.

**Query:** `symbol` (optional).
**Response:** `200 OK` → `{ "count": int, "predictions": PredictionRecord[] }`

```bash
curl 'http://localhost:8000/forward/active?symbol=RELIANCE.NS'
```

---

### `GET /forward/completed`
Resolved predictions (terminal states), newest first.

**Query:** `limit` (optional, 1–1000), `symbol` (optional).
**Response:** `200 OK` → `{ "count": int, "predictions": PredictionRecord[] }`

```bash
curl 'http://localhost:8000/forward/completed?limit=100'
```

---

### `GET /forward/stats`
Aggregate performance of resolved predictions (all figures in R-multiples).

**Query:** `symbol` (optional).
**Response:** `200 OK` → `Stats` object (see above).

```bash
curl http://localhost:8000/forward/stats
```
```json
{ "total": 12, "open": 4, "resolved": 8, "wins": 5, "losses": 3,
  "win_rate": 0.625, "avg_r": 0.44, "total_r": 3.5, "profit_factor": 1.9,
  "max_drawdown_r": 1.2, "avg_holding_bars": 6.0, "open_risk_r": 4.0 }
```

---

### `GET /forward/summary`
Stats **plus** an honest read on what the live sample proves, and a live-vs-backtest
comparison.

**Query:** `symbol` (optional).
**Response:** `200 OK`

| Field | Type | Notes |
|---|---|---|
| `stats` | Stats | as `/stats` |
| `expectancy` | number \| null | per-trade expected R (== `stats.avg_r`) |
| `confidence` | string | `no_data` / `insufficient_sample` (<50) / `building` (≥50) |
| `note` | string | plain-language sample caveat |
| `min_meaningful_sample` | int | 50 |
| `backtest` | object | `{ configured, win_rate, avg_r, profit_factor, label }` — the configured baseline; `configured=false` when disabled |
| `live_vs_backtest` | object | `{ live_win_rate, backtest_win_rate, difference, sample_size, ci_low, ci_high, status, baseline_configured, backtest_label }` |
| `disclaimer` | string | forward-testing ≠ live trading ≠ backtest |

`live_vs_backtest.status` ∈ `no_data` / `building_sample` (<30) / `inconclusive` /
`statistically_significant` (95% CI on the live win rate excludes 0.5). The backtest
baseline defaults to the documented outcome-model walk-forward result (59.6% win, +0.285
avg R, PF 1.63) and is overridable via `AEGIS_FORWARD_BACKTEST_*`.

```bash
curl http://localhost:8000/forward/summary
```

---

### `GET /forward/breakdown`
Performance grouped by a context dimension (aggregated server-side).

**Query:**
- `by` — one of `market`, `sector`, `timeframe`, `confidence`, `regime` (default `sector`).
- `symbol` (optional).

**Responses**
- `200 OK` → `{ "dimension": string, "groups": [{ "bucket": string, "stats": Stats }],
  "resolved_total": int, "min_meaningful_sample": 50 }` (groups sorted by resolved count,
  desc; records missing the dimension are grouped under `"unknown"`).
- `422 Unprocessable Entity` → unknown dimension.

```bash
curl 'http://localhost:8000/forward/breakdown?by=sector'
```
```json
{ "dimension": "sector", "resolved_total": 8, "min_meaningful_sample": 50,
  "groups": [ { "bucket": "Energy", "stats": { "resolved": 5, "win_rate": 0.6, "avg_r": 0.5 /* … */ } },
              { "bucket": "IT", "stats": { "resolved": 3, "win_rate": 0.33 /* … */ } } ] }
```

---

## Error model
Errors use FastAPI's standard shape: `{ "detail": <string | validation-error-list> }`.

| Status | When |
|---|---|
| `404` | unknown `prediction_id` |
| `409` | duplicate prediction (idempotent create) |
| `422` | request validation failure, or unknown breakdown dimension |
| `503` | store/engine not initialised (misconfiguration, not a user error) |

## Notes
- **Idempotency:** creating the same `(symbol, timeframe, created_candle_ts, source)` twice
  yields `409` — safe to retry.
- **Read-only over the engines:** these endpoints record and report; they never run a model
  or write a prediction's outputs after creation (only lifecycle columns change on resolve).
- **Dashboard:** `/dashboard/forward.html` is the presentation layer over this API.
