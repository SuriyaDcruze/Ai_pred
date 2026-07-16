# Volume 27 — Observability & Monitoring

## Purpose
Know what the system and the *models* are doing in production — logs, metrics, and
crucially **drift detection**, so a silently-degrading model is caught, not trusted.

## Status: 🔴 Logs only — `app/utils/logging.py` (structured, TTY-aware Rich).

## Three pillars (target)
1. **Logs** (have): structured, per-module; RichHandler only on a TTY (fixed a non-tty
   crash). Add request ids + model/version stamps.
2. **Metrics** (target): request latency/throughput, engine timings, data-provider
   success/failure, screener duration, cache hit rate, prediction volume by market.
3. **Traces** (target): request tracing through service → engines for latency debugging.

## Model & research monitoring (the differentiator)
- **Drift detection** (`app/monitoring/drift_detection.py`, target): feature drift,
  prediction drift, probability/calibration drift, market-regime drift. On significant
  drift → **do not silently continue**: emit `reports/drift_report.md`, recommend a
  (validated) retrain.
- **Live-vs-backtest tracking** (ties to Vol 18): is the live edge consistent with
  backtest? Alert on divergence.
- **Model-version tracking:** every prediction stamped with model/feature/data version
  (Vol 21) so regressions are attributable.

## Notification Engine (target)
- Alerts to users ("a TAKE setup appeared on your watchlist") and to ops (drift, provider
  outage, error spikes). Channels: web push (PWA, Vol 23), email.

## Alerting principles
- Alert on **terminal/actionable** states, not noise. Silence ≠ health — cover failure
  signatures (provider down, resolver stuck, drift).

## Failure handling
- Monitoring failures never affect serving; degrade metrics before degrading the product.

## Testing
- Drift math on synthetic shifts; alert routing; metric emission.

## Future
- Prometheus/Grafana; a model-health dashboard; an ops "state of the edge" page (live
  win-rate, drift, provider health) — the honest internal scoreboard.
