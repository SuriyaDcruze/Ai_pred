---
title: Aegis Trading AI
emoji: ⚡
colorFrom: green
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
---

# Aegis — Real-Time Trading AI

A modular AI trading assistant. It streams live market data, engineers technical +
Smart-Money-Concept (SMC) + **candlestick-pattern** features, runs a hybrid
TCN→Transformer model to read the market, explains every call in plain English,
and lets you **forward-test the AI against yourself** on live data.

> **Decision-support software, not financial advice.** It never guarantees profit.
> Every output carries a confidence score, a stop loss, and risk sizing. Trade at
> your own risk.

---

## What it does

| Area | Status | Where |
|------|--------|-------|
| 18+ technical indicators | ✅ | `app/indicators/technical.py` |
| SMC features (FVG, OB, BOS, CHoCH, swings, liquidity) | ✅ | `app/features/smc.py` |
| **Candlestick patterns** (Hammer, Engulfing, Doji, Stars, Pin Bars…) | ✅ | `app/features/candlesticks.py` |
| Hybrid TCN→Transformer multi-task model (45 features) | ✅ | `app/ai/model.py` |
| Training (walk-forward, AMP, early-stop, TensorBoard) | ✅ | `app/training/train.py` |
| **Backtester** (no-lookahead, fees + slippage) | ✅ | `app/backtest/engine.py` |
| **Signal sweep** (hunt for a predictable timeframe/horizon) | ✅ | `app/backtest/sweep.py` |
| Decision engine (multi-confirmation gate) + risk manager | ✅ | `app/decision/`, `app/risk/` |
| Beginner-friendly chat assistant (+ optional Claude LLM) | ✅ | `app/chat/` |
| **Forward-testing tracker** (You vs AI, live win rate) | ✅ | `app/tracking/tracker.py` |
| Live Binance stream (geo-block fallback built in) | ✅ | `app/stream/binance.py` |
| **Stocks** — Apple, Tesla, ITC, Reliance… via Yahoo (no API key) | ✅ | `app/stream/yahoo.py` |
| **News + sentiment** (free RSS, finance lexicon) | ✅ | `app/features/sentiment.py` |
| **My Rules** — your checklist, enforced before every trade | ✅ | `app/decision/rules.py` |
| Live dashboard (chart, chat, tap-to-trade, scoreboard, news) | ✅ | `app/dashboard/` |
| REST + WebSocket API | ✅ | `app/api/main.py` |
| Colab GPU training notebook | ✅ | `colab/` |
| Render deployment | ✅ | `render.yaml`, `docs/DEPLOY_RENDER.md` |

---

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

pytest -q                                  # run the tests
python -m app.scripts.demo                 # signal on synthetic data (offline)
uvicorn app.api.main:app --port 8010       # serve → open /dashboard/
```

Open **http://localhost:8010/dashboard/**.

### Train a better model (GPU, on Colab)
1. `python -m app.scripts.package_colab` → makes `aegis_project.zip`
2. Open `colab/Aegis_Train_Colab.ipynb` on Google Colab (GPU runtime)
3. Run the **signal sweep** to find a predictable setup, then train + download
   `model_best.pt` into your local `artifacts/` folder.

### Deploy
See [`docs/DEPLOY_RENDER.md`](docs/DEPLOY_RENDER.md).

---

## The dashboard, in a nutshell

- **Live candlestick chart** with a pulsing LIVE indicator, filter toggles for
  levels / your calls / candlestick patterns.
- **Chat co-pilot** — ask "where do I trade?", "is this safe?", or type a pattern
  name ("hammer", "engulfing") to learn it *and* see if it's on the chart now.
- **Tap the chart** → a BUY/SELL prompt pops up right there → you pick a direction,
  the AI independently picks its own → both are pinned at the exact point.
- **📓 Track Record + 🏆 scoreboard** — every call is scored WIN/LOSS against real
  future price (no lookahead), and a live head-to-head shows **You vs the AI**.
- **🤖 Auto-log** — let the AI log its own picks hands-free to build its record.
- **📰 News & sentiment** — free headlines, scored bullish/bearish. This is the only
  input on the platform that is *not* derived from price, so it can move before the
  chart does.
- **✅ My Rules** — your own checklist (min confidence, never fight the trend, min
  R:R, don't buy the top, daily trade cap…), scored against the live setup and
  persisted in SQLite. Read the note below — it matters.

### A word on "My Rules"

It is a **discipline** feature, not an accuracy feature, and the difference is the
whole point. It will not make the model smarter. What it does is stop you taking
the trades you already know you shouldn't: the 30%-confidence ones, the ones
against the trend, the fifth revenge trade of a losing day. When an edge is thin —
and ours is (see below) — being undisciplined is the fastest way to give it away.
That is worth more than a percentage point of model accuracy.

---

## How the model works (short version)

The model reads the **last 128 candles** as 45 numbers each (indicators + SMC +
candlestick patterns) and outputs, for the next few candles: **P(up) / P(down) /
P(sideways)**, predicted high/low/close, expected volatility, and a confidence
score. A trade only fires when trend + volume + momentum + structure + candlestick
all agree **and** confidence ≥ 80% **and** risk:reward ≥ 2 — otherwise **WAIT**.
See [`docs/HOW_IT_WORKS.md`](docs/HOW_IT_WORKS.md) for the full explanation and the
honest truth about accuracy.

## Architecture

```
live data → stream/ → indicators/ + features/ (incl. candlesticks) → FeatureBuilder
                                                    │
                                                    ▼
                                          ai/model.py (multi-task)
                                                    │
                    ┌───────────────────────────────┤
                    ▼                                ▼
             decision/engine.py  ◀────────────  risk/manager.py
                    │
                    ▼
        Signal (BUY / SELL / WAIT) → decision/rules.py (your checklist)
                                            │
                                            ▼
                              api/ → dashboard/ → tracking/ (forward-test)
```

Two inputs sit outside that price-derived pipeline: **`stream/yahoo.py`** (stocks —
same model, same dashboard, no API key) and **`features/sentiment.py`** (news).

## Safety rails (hard-coded)
- No signal unless confidence ≥ 80%, R:R ≥ 2, and all confirmations agree → else WAIT.
- Position size never exceeds the configured max account risk (default 1%).
- The word "guaranteed" appears nowhere in a signal.
