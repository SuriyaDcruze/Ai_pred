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

A beginner-friendly AI trading **practice** platform for **crypto and Indian (NSE)
stocks**. It reads the market with a two-model pipeline — a direction model *and* a
trade-selection ("outcome") model — explains every call in plain English, enforces
your personal risk rules, includes a **Groww/NSE screener** ("which stock to buy,
where to sell"), and lets you **forward-test the AI** on live data — with **no real
money and no orders ever placed.**

> **Decision-support and learning software, not financial advice, and not a
> money-maker.** The direction model is **break-even** on its own. A separate
> **outcome model** turns that into a positive edge *in backtest* — verified on both
> crypto and Indian stocks — but it has **zero live track record.** Do not trade real
> money on it until it is proven live. See [Honest status](#-honest-status-read-this-first).

---

## 📌 For a reviewer / mentor — the 60-second summary

- **What it is:** an honest AI trading *decision-support & practice* platform. Two
  models: a **direction** model (~60% accurate) and an **outcome / trade-selection**
  model (meta-labeling — "will this trade hit target before stop?").
- **The one real finding:** direction prediction is stuck at ~61% and is break-even.
  But **filtering trades by the outcome model turns break-even into positive
  expectancy** — verified with purged walk-forward + an untouched final test, on
  **crypto (1h)** *and* **Indian NSE stocks (daily)**. The edge generalises across
  markets and timeframes, which is strong evidence it is real, not curve-fit.
- **Backtest numbers (untouched test, filtered):** crypto ~+0.48R (PF 2.3);
  NSE ~+0.84R (PF 6.9, small sample). **Not yet proven live.**
- **Open question for a mentor:** the winning samples are modest (97 crypto / 41 NSE
  untouched trades) and there is **no live track record**. Is the edge real, or
  will it fade live? The honest next step is weeks-to-months of paper forward-testing.
- **Regulatory note (India):** this is decision-support/education, not registered
  investment advice. Selling signals/advice in India needs SEBI compliance.

---

## 🎯 Honest status (read this first)

Most trading products hide these numbers. We lead with them.

| Question | Honest answer |
|----------|---------------|
| **How often does it guess direction right?** | **~59–61%** out-of-sample (50% = a coin flip). Wrong ~40% of the time, by design. |
| **Does raw direction make money after fees?** | **No — break-even.** 537 trades, ~0.00 avg, not different from zero. |
| **Did better features help?** | **No — measured across 6 specs, 10 feature groups** (regime, price-action, session, multi-timeframe, interactions, and India-specific: relative-strength-vs-Nifty, gap, VWAP, 52-week). Every gain inside the noise. Feature engineering is **exhausted**. |
| **Does the outcome model (trade selection) help?** | **Yes, in backtest.** Filtering → positive expectancy on crypto AND NSE, survived the untouched final test. |
| **Is it proven with real money?** | **No — 0 live trades.** Backtest-verified ≠ live-proven. Needs weeks-to-months of forward-testing. |
| **Can it reach 90% / guaranteed profit?** | **No. Impossible.** Anyone claiming it is lying. |

**Why ~60% right ≠ profitable:** being right about *direction* isn't the same as
being right about *timing*. The model can correctly say "up over the next 12
candles" and still lose the trade if price dips and hits your stop first — and every
trade pays ~0.12% in fees that eat a thin edge. So it reads the market decently but
does not have a reliable money-making edge. That is the truth, measured, in your own
repo (`python -m app.training.baselines`).

**What it's genuinely good for:** learning how trading works, reading charts (61
candlestick patterns), practising risk discipline (My Rules), screening NSE stocks
for setups, and forward-testing the AI — all risk-free.

---

## 🇮🇳 Indian NSE stocks (Groww focus)

The platform has a first-class focus on **Indian large-cap stocks** you can trade on
Groww (Reliance, TCS, Infosys, HDFC Bank, ITC, SBI, Bajaj Finance, …).

- **The screener** (`GET /screener/nse`, dashboard "🇮🇳 Today's NSE setups") scans 15
  liquid NSE stocks on the **daily** timeframe (what swing/positional traders use),
  runs both models on each, and returns only the **TAKE** setups — ranked, with exact
  **entry / stop / target** to place on Groww. When nothing clears the bar (most
  days), it says so and shows the closest candidates. **An honest screener that tells
  you to sit out.**
- **Why this is honest to ship:** the outcome-model edge was **validated on NSE data
  first** (daily, 8 large-caps): filtered **+0.49R** (PF 2.69) walk-forward, **+0.84R**
  (PF 6.9) on the untouched final test, vs ~+0.05R take-all. Each market gets its own
  model (`artifacts/outcome_model_nse.pkl`); the crypto model does not serve stocks.
- **Honest limits:** Yahoo data is ~15 min delayed and market-hours only (fine for
  daily decisions, not intraday timing). The NSE winning sample is **41 untouched
  trades** — promising, not proven. **Paper-trade on Groww first**, risk ≤1%, always
  use the stop. This is **not registered investment advice** (SEBI).

### India-specific features — tested, no predictive edge (kept for explainability)
The V2 "India First" spec proposed NSE-specific signals — **relative strength vs
Nifty 50**, gap behaviour, VWAP distance, 52-week position, weekly trend
(`app/features/india.py`). We tested them honestly on the NSE outcome model
(untouched final test): base +0.85R (PF 7.4) → **+ India features +0.62R (PF 3.9),
slightly worse.** They add **no trade-selection edge** — consistent with every prior
feature test. They are kept **for explainability only**: a Groww card can honestly
say "outperforming Nifty, near 52-week high" as *context*, not as a model input.
**The product's moat is transparency + the validated outcome model, not more
features.**

### 🔍 Explainable stock intelligence (V3 "Groww Assistant")

`GET /intelligence?symbol=RELIANCE.NS` and the dashboard **🔍 Deep Analysis** card give
a full, transparent read of any stock — the thing the V3 vision actually asks for
(*"the AI should never behave like a black box"*):

- **Recommendation:** BUY / SELL / **WAIT** — the *decision* from the **validated
  outcome model** (the real edge), never from context features.
- **Market state** (rule-based trend + volatility), **relative strength vs Nifty**,
  **historical similarity** ("similar setups won X%"), a **trade plan**
  (entry/stop/target/R:R/holding), and **plain-English For / Against factors**.
- **WAIT is the default** when the outcome model isn't confident — *"fewer
  high-quality opportunities beat many weak signals."*

Every number is real; context is labelled context. This transparency — showing its
work and telling you when to sit out — is the honest, SEBI-defensible differentiator.

### 🏭 Sector Intelligence — understand the sector before the stock

`GET /sectors` ranks the 10 major NSE sectors (Banking, IT, Auto, Pharma, FMCG,
Energy, Metal, Realty, Infra, PSU Bank) by **relative strength vs the Nifty 50** and
momentum, using NSE sector indices (`app/sector.py`). Indian equities move by **sector
rotation** — a bank-stock long is lower quality when Banking is weak, whatever the
stock's own chart says. Each stock's sector strength is folded into the Deep Analysis
card's For/Against factors (e.g. *"IT sector Strong, rank 1/10"*). Like relative
strength, it is honest **context for decision quality, not a model feature** (we proved
features don't add edge). It is the "analyse the sector before the stock" layer the
India-first mandate asks for.

---

## 🧠 How the model works, in plain English

1. **It looks at the last candles** and turns them into **45 numbers** — things like
   RSI, MACD, trend direction, volatility, and candlestick shapes. (Think of these
   as 45 different "readings" of the market's mood.)
2. **It predicts direction** for the next **12 candles**: probabilities for **UP /
   DOWN / NEUTRAL**. A move too small to beat trading fees is deliberately called
   NEUTRAL — we never train it to chase moves you can't profit from.
3. **The number it gives is calibrated** — when it says "60% sure," it really is
   right about 60% of the time. (Most models lie about this; ours is corrected.)
4. **A prediction is not a trade.** It only becomes a BUY/SELL signal if a majority
   of 5 confirmations agree (trend, volume, momentum, structure, candlestick) **and**
   confidence clears the gate **and** the reward is at least 2× the risk. Otherwise
   it says **WAIT** — which is a real answer, meaning "no good setup right now."

**The model itself is a calibrated logistic regression.** We originally built a
580,000-parameter deep neural network (TCN + Transformer), then tested it honestly
against simple baselines — and a plain logistic regression **beat it** (59% vs 48%).
So we switched. Simpler, faster (trains in 2 seconds), honest, and better. The full
story is in [`docs/HOW_IT_WORKS.md`](docs/HOW_IT_WORKS.md).

---

## What it does

| Area | Status | Where |
|------|--------|-------|
| 18+ technical indicators | ✅ | `app/indicators/technical.py` |
| SMC features (FVG, OB, BOS, CHoCH, swings, liquidity) | ✅ | `app/features/smc.py` |
| **Candlestick patterns** — all **61**, chart-labelled + teachable in chat | ✅ | `app/features/candlesticks.py`, `patterns_extra.py` |
| **Calibrated logistic model** (the one that ships — beat the deep net) | ✅ | `app/ai/sklearn_model.py` |
| **Probability calibration** (isotonic — honest confidence) | ✅ | `app/ai/calibration.py` |
| **Baseline race** (proves which model actually wins) | ✅ | `app/training/baselines.py` |
| Deep TCN→Transformer (kept as a comparison baseline — it lost) | ✅ | `app/ai/model.py` |
| **Backtester** (no-lookahead, fees + slippage, significance test) | ✅ | `app/backtest/engine.py` |
| **Purged walk-forward + challenger pipeline** (fair, leakage-proof feature tests) | ✅ | `app/training/walk_forward.py`, `challenger_compare.py` |
| **Confidence-bucket analyzer** (is high confidence trustworthy?) | ✅ | `app/evaluation/confidence_analysis.py` |
| Candidate features (regime, price-action, session, multi-TF, interactions) | ⚗️ tested, all noise | `app/features/` |
| **Nightly retrain** (champion/challenger — new model must beat old) | ✅ | `app/scripts/nightly_retrain.py` |
| **Meta-model** (learns which signals win from your Track Record) | ✅ | `app/training/meta.py` |
| Decision engine (multi-confirmation gate) + risk manager | ✅ | `app/decision/`, `app/risk/` |
| Beginner-friendly chat assistant (+ optional Claude LLM) | ✅ | `app/chat/` |
| **Forward-testing tracker** (You vs AI, live win rate) | ✅ | `app/tracking/tracker.py` |
| Live Binance stream (geo-block fallback built in) | ✅ | `app/stream/binance.py` |
| **Outcome model** — trade selection ("target before stop?"), the real edge | ✅ | `app/ai/outcome_model.py` |
| **🇮🇳 NSE / Groww screener** — which Indian stock to buy, where to sell | ✅ | `app/screener.py` |
| **🔍 Explainable stock intelligence** (V3 "Groww Assistant") | ✅ | `app/intelligence.py` |
| **🏭 Sector Intelligence** — NSE sector rotation (Banking/IT/Auto…) as context | ✅ | `app/sector.py` |
| **Similarity engine** — "similar past setups won X%" (explainability) | ✅ | `app/ai/similarity_engine.py` |
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

### Reproduce the honesty (no GPU needed)
```bash
python -m app.training.baselines          --symbol BTCUSDT --bars 20000   # race the models
python -m app.ai.calibration              --symbol BTCUSDT                # confidence honesty report
python -m app.ai.sklearn_model            --symbol BTCUSDT --bars 20000   # train the shipping model (2s, CPU)
python -m app.evaluation.confidence_analysis --symbol BTCUSDT             # is high confidence tradeable?
python -m app.training.challenger_compare --assets BTCUSDT ETHUSDT SOLUSDT  # test new features honestly
```
The winner saves to `artifacts/sklearn_model.pkl` and is picked up automatically.

**Got an "add these features to hit 65%" idea (from anyone)?** Run it through
`challenger_compare` — it applies purged walk-forward, an uncertainty gate (a gain
must beat the ~2.6pp fold noise), a class-balance gate, and leakage tests, then says
ACCEPT or REJECT. So far **4 improvement specs and 8 feature groups → all REJECT**
(every gain was noise). See [`docs/RESULTS.md`](docs/RESULTS.md) and [`reports/`](reports/).
You can test any claim instead of trusting it.

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

## Architecture

```
live data → stream/ → indicators/ + features/ (incl. candlesticks) → FeatureBuilder
                                                    │
                                                    ▼
                                  ai/sklearn_model.py (calibrated logistic)
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
- A prediction is not a trade: needs a majority of 5 confirmations **and** the
  confidence gate **and** R:R ≥ 2 — else **WAIT**.
- The confidence number is **calibrated**, so the gate actually means something.
- Position size never exceeds the configured max account risk (default 1%).
- **No orders are ever placed. No exchange account is ever touched.** It is a
  practice platform. Nothing here can spend your money.
- A red **learning-mode banner** stays on screen while the model is unproven.
- The word "guaranteed" appears nowhere in a signal.
