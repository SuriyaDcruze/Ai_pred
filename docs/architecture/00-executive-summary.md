# Volume 00 — Executive Summary

> One-page truth about what Aegis is, what it has proven, and how it is built.
> If you read only one volume, read this.

## What Aegis is

Aegis is an **AI-powered Market Intelligence Platform for the Indian NSE** (crypto and
US equities secondary). It helps investors make better-informed decisions through
transparent, explainable, scientifically-validated AI analysis. It is **not** a trading
bot, **not** a signal-selling service, and **not** an accuracy-maximiser. It never
places orders.

## The core technology (our IP)

Two proprietary models, kept strictly independent of any LLM:

1. **Prediction Engine** — a *calibrated logistic-regression* direction model. Produces
   Direction, Probability, Confidence. Measured out-of-sample directional accuracy
   **~59–61%** (honest; wrong ~40% of the time by nature).
2. **Outcome Engine** — a *target-before-stop* meta-labelling model. Predicts whether a
   trade will hit its target before its stop, and vetoes the rest. **This is the one
   verified edge**: it turns a break-even direction model into positive expectancy in
   backtest, and the edge **generalises across crypto (1h) and Indian NSE stocks
   (daily)** — strong evidence it is real, not curve-fit.

Everything else (Market/Sector/News Intelligence, Risk, Portfolio, Conversation)
**consumes** these outputs. No LLM ever generates a prediction.

## What is proven — and what is not

| Claim | Status |
|---|---|
| Direction ~59–61% out-of-sample | ✅ Measured, reproducible |
| Feature engineering can't push past ~61% | ✅ 10 experiments across 6 specs, all noise |
| Outcome model turns break-even → +0.22R to +0.48R (crypto), +0.49R (NSE) | ✅ Backtest, incl. untouched final test |
| Edge is robust to parameters, generalises across markets | ✅ Sensitivity + NSE validation |
| **It makes money with real money, live** | ❌ **UNPROVEN — zero live trades** |

The **single most important architectural fact**: every positive number is backtest.
Winning samples are modest (97 crypto / 41 NSE untouched trades). The platform's
credibility — and any path to real money or revenue — depends on building a **live
forward-testing track record** (Volume 18).

## Architecture in one paragraph

A **FastAPI modular monolith** (~10k LOC, ~230 tests) with clean engine boundaries:
data (`stream/`) → features (`features/`, `indicators/`) → Prediction (`ai/`) → Outcome
(`ai/outcome_model.py`) → Intelligence (`intelligence.py`, `sector.py`) → Risk (`risk/`)
→ Decision (`decision/`) → API (`api/main.py`) → Dashboard (`dashboard/`). Research
tooling (`training/`, `evaluation/`, `backtest/`) enforces honest evaluation. The
monolith is designed with **extractable boundaries** so any engine can become a service
later — but microservices are deliberately *not* built now (over-engineering for the
stage).

## Strategic priorities (CTO view)

1. **Forward Testing (Vol 18) is priority #1.** Prove the edge live. Nothing else
   matters until there's a real track record.
2. **Do not over-engineer.** Modular monolith now; extract services only when scale
   demands. No premature microservices, no premature mobile-native.
3. **Honesty is the moat and the compliance strategy.** SEBI: decision-support, not
   advice. Persistent disclaimers + an audit trail are architectural requirements.
4. **Protect the Prediction Model's independence** — enforced by API contract, not
   convention. The LLM reads structured outputs; it cannot predict.

## The one-sentence summary

> A scientifically-honest, explainable NSE market-intelligence platform with one
> verified-in-backtest edge (the Outcome Engine), built as a clean modular monolith,
> whose next and most important step is proving that edge live.
