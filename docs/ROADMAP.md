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
- [x] Probability calibration (isotonic) on a holdout — `app/ai/calibration.py`.
- [x] Backtesting engine with realistic fees/slippage + significance test.
- [x] Baseline race + purged walk-forward challenger pipeline.
- [x] Switched champion to **calibrated logistic regression** (beat the deep net).
- [x] Cost-aware triple-barrier labels.
- [ ] **Target-before-stop outcome model** — the best remaining idea; attacks *profit*
      directly instead of accuracy (which is measured-stuck at ~61%). All tooling exists.
- [ ] Meta-model training once the Track Record has ~200 resolved calls (auto-log is on).
- [x] ~~Optuna over TCN/Transformer dims~~ — abandoned; the deep net lost to logistic.
- [ ] Per-symbol / asset-group models (only if they beat the global model — spec'd,
      not yet built).

## Phase 4 — serving & ops
- [ ] Kafka ingestion path (interface hook is isolated in `stream/`).
- [ ] Prometheus metrics: latency, GPU util, prediction throughput.
- [ ] Structured JSON logs + request tracing.
- [ ] Rate limiting + API auth (keys / JWT) before any public exposure.
- [ ] Load tests (Locust) and latency benchmarks.

## Phase 5 — dashboard
- [ ] Replace the static page with a React app (Vite) — the JSON API is stable.
- [ ] Live WS feed of signals, order book heatmap, open positions, P&L stats.

## The accuracy ceiling (measured, not assumed)
The honest headline finding of this project: **directional accuracy is stuck at
~59-61% and does not become profit after fees.** Across 4 improvement specs we tested
8 feature groups through purged walk-forward — every gain was inside the noise
(`reports/`). Feature engineering is exhausted as a lever. The remaining honest
options attack *trade selection*, not prediction: the outcome model and the
meta-model. See [`RESULTS.md`](RESULTS.md) for the full scoreboard.

## Known limitations (be honest with users)
- **It is break-even, not profitable.** Measured across 537 trades. It's a learning
  and practice tool, not a money-maker. Real-money trading is not recommended.
- **No broker execution** — no orders are ever placed, no account is ever touched.
- The heuristic fallback predictor is **indicative only** and only used if no trained
  model is present.
- SMC features are causal but swing confirmation lags by the fractal width.
- Stock data (Yahoo) is ~15 min delayed and market-hours only; the model was trained
  on crypto, so it's even less validated on stocks.