# Roadmap — what's built vs. what's next

The v0.1 core is genuinely runnable end-to-end. The items below are the honest
remaining surface area from the original spec, in recommended build order. None
are faked in the current tree — where an interface exists, it is marked.

## Phase 1 — data & persistence (next)
- [ ] Wire `app/database/models.py` into the API via an async session dependency.
- [ ] Persist every incoming candle + emitted signal (tables already in `schema.sql`).
- [ ] Redis cache for the latest candle buffer per symbol (dep already declared).
- [ ] Backfill job: page full history into Postgres for offline training.

## Phase 2 — more exchanges (adapters)
Each implements `MarketDataProvider` + `ExchangeStream` in `app/stream/`:
- [ ] Bybit (v5 public WS + REST)
- [ ] Coinbase Advanced Trade (WS + REST)
- [ ] Forex / Stocks via a broker (e.g. OANDA, Alpaca)
- [ ] NSE / MCX via a data vendor (most have no free WS — poll REST)
> The AI, decision, and risk layers require **zero changes** to add a venue.

## Phase 3 — model & training hardening
- [ ] Probability calibration (temperature scaling / isotonic) on a holdout set.
- [ ] Backtesting engine with realistic fees/slippage + walk-forward equity curve.
- [ ] Hyperparameter search (Optuna) over TCN/Transformer dims.
- [ ] Per-symbol fine-tuning + a symbol-embedding input.
- [ ] ONNX / TorchScript export for low-latency inference.

## Phase 4 — serving & ops
- [ ] Kafka ingestion path (interface hook is isolated in `stream/`).
- [ ] Prometheus metrics: latency, GPU util, prediction throughput.
- [ ] Structured JSON logs + request tracing.
- [ ] Rate limiting + API auth (keys / JWT) before any public exposure.
- [ ] Load tests (Locust) and latency benchmarks.

## Phase 5 — dashboard
- [ ] Replace the static page with a React app (Vite) — the JSON API is stable.
- [ ] Live WS feed of signals, order book heatmap, open positions, P&L stats.

## Known limitations (be honest with users)
- The heuristic fallback predictor is **indicative only** and never fires a live
  trade (confidence is capped below the 0.80 gate) until a real model is trained.
- SMC features are causal but swing confirmation lags by the fractal width.
- No broker execution is included — this is decision-support, not an auto-trader.