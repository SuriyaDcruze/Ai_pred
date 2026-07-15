# What We Built & What We Actually Got — The Honest Results

This is the plain-language scoreboard for the Aegis project: everything we tried,
and the **real, measured numbers** — not marketing, not hopes. Every figure here
came from running the code on live market data, out-of-sample, with fees.

> **One-line summary:** We built a genuinely good market-*reading* tool
> (~59% directional accuracy, honest calibrated confidence). We did **not** build a
> money-maker — measured across ~540 trades it is **break-even after fees**. That is
> the truth, and no amount of extra engineering changed it.

---

## 📊 The headline numbers

| What | Number | Plain meaning |
|------|--------|---------------|
| **Directional accuracy** | **~59%** | When it says up/down, it's right ~59% of the time (50% = coin flip) |
| **Trading result after fees** | **~0% (break-even)** | 537 trades, avg **−0.005R**, t = −0.09 — not different from zero |
| **High-confidence signals** | **~90% "accurate", but 0 profit** | Looked amazing; didn't survive real stops + fees |
| **Best possible with this approach** | **52–59% direction, no reliable profit** | Not 70%. Not 90%. Those are impossible here. |

---

## 🧭 The journey — what we tried, in order, and what each gave us

### 1. The original deep neural network (TCN + Transformer)
- **What:** a 580,000-parameter deep-learning model.
- **Result:** measured honestly on non-overlapping data → **48% directional accuracy.**
  Worse than guessing "always up." It was also **stuck 79% bearish**, so it only
  looked good when the market happened to fall.
- **Verdict:** ❌ Replaced.

### 2. The baseline race (the turning point)
We did what the research spec demanded: raced the deep net against simple models
on honest, non-overlapping, cost-aware labels.

| Model | Directional accuracy |
|-------|---------------------|
| **Logistic Regression** 🏆 | **59.2%** |
| XGBoost | 58.2% |
| Random Forest | 57.7% |
| Always-UP (no model) | 51.5% |
| **Deep net (580K params)** | **48.0%** |

- **Result:** a plain **logistic regression beat the deep net by ~11 points.**
- **Verdict:** ✅ Switched the whole platform to logistic regression. Simpler, trains
  in **2 seconds on a laptop** (no GPU), and better.

### 3. Cost-aware labels
- **What:** stopped teaching the model to chase price moves too small to beat fees.
- **Result:** cleaner training target; part of why logistic reached 59%.
- **Verdict:** ✅ Kept.

### 4. Probability calibration (making confidence honest)
- **What:** the raw model's "confidence" was a lie — when it said "54% sure" it was
  actually right 76% of the time.
- **Result:** calibration error (ECE) dropped from **0.138 → 0.05.** Now "60%" really
  means right ~60% of the time.
- **Verdict:** ✅ Kept. This fixed the broken trade gate that used to say WAIT 100%
  of the time.

### 5. Does it make money? (the real backtest)
Traded through the full engine — real stops, real fees, no look-ahead — across
BTC / ETH / SOL, two timeframes each.

- **Result:** **537 trades, average −0.005R, t = −0.09.** Break-even. The 95%
  confidence range (−0.13R to +0.12R) includes zero — no edge.
- **Reality check:** we also tested a brainless "always sell" strategy → +0.022R.
  Our model beat it slightly (+0.11R), proving it isn't *just* riding trends — but
  the edge is not statistically real.
- **Verdict:** ⚠️ Honest break-even. Not tradeable for profit.

### 6. The high-confidence hunt (the most promising lead — and how it died)
The research spec's best idea: maybe only the model's *most confident* signals are
profitable. We checked.

- **The exciting part:** the 80%+ confidence signals were **~90% directionally
  accurate**, and it held across **all 4 coins** (BTC 94%, ETH 93%, SOL 89%, BNB 86%).
  That is not luck.
- **The reality:** gated to 80% confidence, the **real backtester** (546 trades)
  returned **−0.089R, t = −1.51 — slightly losing, not significant.**
- **Why:** those confident signals are tiny low-volatility drifts. The model calls
  the *direction* right, but the move is too small to hit a real profit target
  before timing out or hitting the stop. **Directional accuracy ≠ profit.**
- **Verdict:** ❌ No tradeable edge. The spec predicted this exact outcome and said:
  *"stay in learning mode."* We did.

---

## 🐛 The bugs & problems we hit (and fixed)

Real engineering is mostly finding your own mistakes. These are the ones that
mattered — several of them were quietly faking good results before we caught them.

| # | The problem | Why it was dangerous | Fixed by |
|---|-------------|---------------------|----------|
| 1 | **Overlapping labels inflated accuracy** | With a 12-candle horizon, neighbouring bars share 11/12 of their future — so a "repeat last answer" rule scored a fake **63%** that dropped to **48%** once fixed. Every earlier accuracy number was inflated. | Sampling test bars every 12 candles (non-overlapping) + a purge gap |
| 2 | **The confidence gate was jammed shut** | Uncalibrated confidence never reached 80%, so the trade gate fired **0 signals in 500 candles** — "My Rules" always said "no trade." Looked like caution; was a bug. | Calibration + lowering/rethinking the gate |
| 3 | **Early-stopping watched the wrong number** | Training saved the "best" model on *total* loss, which kept improving from the price heads while the *direction* head (the only part we trade on) got **worse**. We were saving models as they degraded. | Select on **direction loss**, not total loss |
| 4 | **A log line printed a loss as if it were accuracy** | "dir 0.99" looked like 99% accuracy; it was actually a loss value (random = 1.099). Nearly made us celebrate a random model. | Relabelled the log + added a random-baseline warning |
| 5 | **Deep net "riding the bear market"** | Early +5.7% looked like an edge, but the model was 79% bearish in a falling market — it wasn't predicting, just leaning. | Tested vs a brainless "always sell" null; balanced logistic model |
| 6 | **High-confidence bucket looked 90% / profitable** | A simple expectancy proxy credited every correct *direction* as a full win, hiding that the moves were too small to trade. Almost became a false "edge." | Confirmed with the **real path-dependent backtester** — it vanished |
| 7 | **`app/data` package silently un-tracked** | An over-broad `.gitignore` rule dropped the package from git — the exact bug that had broken the Render deploy before. | Anchored the ignore rule + re-added the files (caught before commit) |
| 8 | **Binance geo-blocked (HTTP 451)** | On Indian IPs / cloud hosts, Binance data + WebSocket are blocked, breaking the live chart. | Mirror fallback (`data-api.binance.vision`) + REST polling when WS is blocked |
| 9 | **New model needed more warm-up than the old one** | The logistic model asked for 210 candles vs the deep net's 128, crashing API calls that used to work. | Lowered the minimum + graceful `nan_to_num` degradation |
| 10 | **Model switch broke torch-only code paths** | `ai_direction` assumed a torch model (`.cfg.seq_len`), which the logistic model doesn't have — a latent crash in auto-log. | Made it model-agnostic, trust calibrated probs directly |

**The pattern:** almost every bug made results look *better* than reality
(fake 63%, fake 99%, fake +5.7%, fake 90%). Honest measurement kept catching them.
That is the single most important thing this project did right.

---

## 🎯 Why "59% accurate" is not "59% profit" (the one idea to remember)

This trips up every beginner, so it's worth stating plainly:

- **Accuracy** = did price go the way we predicted, eventually?
- **Profit** = did our *target* get hit before our *stop*, after paying fees?

These are different. The model can correctly say "up over 12 candles" and you still
**lose the trade** if price dips and hits your stop on candle 3 first — and every
trade pays ~0.12% in fees. That gap is why a 59%-accurate model is break-even. It's
also why anyone selling "90% accurate AI trading" is selling a number that doesn't
pay the bills.

---

## ✅ What we DID successfully build

Not everything is about profit. These all work and have real value:

| Feature | What it does |
|---------|-------------|
| **Calibrated logistic model** | Reads the market at ~59% directional, honest confidence |
| **61 candlestick patterns** | Detects + teaches every classic pattern on your chart |
| **My Rules** | Enforces your personal risk checklist before every trade |
| **News + sentiment** | Free headlines scored bullish/bearish (crypto + stocks) |
| **Stocks + crypto** | Binance (BTC/ETH…) and Yahoo (AAPL/ITC.NS/TSLA…), no API keys |
| **Forward-test tracker** | You vs AI scoreboard, scored on real future price |
| **Backtester** | No-lookahead, fees + slippage, statistical significance tests |
| **Baseline race** | Proves which model actually wins, honestly |
| **Confidence analyzer** | Tests whether high-confidence signals are trustworthy |
| **Nightly retrain** | New model must *beat* the old one before replacing it |
| **Meta-model** | Learns from your live track record (needs ~200 calls) |
| **~199 automated tests** | The whole thing is verified, not vibes |

---

## 🚦 The final, honest verdict

**Short-horizon crypto/stock direction, with these tools, does not contain a
profitable edge.** We attacked it from every serious angle — better model, cost-aware
labels, calibration, confidence selectivity, meta-labeling — and each honest test
landed in the same place: **decent at reading direction, break-even at making money.**

That is not a failure of engineering. The engineering is sound and thoroughly
tested. It is the honest state of the art: markets are close to unpredictable at
this timescale, and **anyone claiming otherwise for a fee is lying.**

**Use this platform to:** learn how trading works, read charts, practise discipline
with My Rules, and forward-test yourself against the AI — all with **zero real money
and zero orders ever placed.**

**Do not use it to:** trade real money expecting profit. It can't reliably deliver
that. Nothing at this state of the art can.

*Everything above is reproducible: `python -m app.training.baselines`,
`python -m app.evaluation.confidence_analysis`, `python -m app.ai.calibration`.*

---

## 📍 Where we stand right now

**The platform is complete, tested, and running.** What's done, what's open, and what's next:

### ✅ Done and working
- Calibrated logistic model live in the app (crypto + stocks)
- 61 candlestick patterns, My Rules, news sentiment, forward-test tracker
- Honest tooling: baseline race, calibration report, confidence analyzer, backtester
- Nightly retrain (champion/challenger) and meta-model, both built
- ~199 automated tests passing; all pushed to `SuriyaDcruze/Ai_pred`
- A red **learning-mode banner** on the dashboard, on by default, telling the truth

### ⏳ In progress / waiting on time
- **Meta-model needs data.** It learns which signals win from your live Track Record,
  but needs ~200 resolved calls. **Auto-log is ON**, filling the record server-side
  (~24 calls/day on 1h) — so it's accumulating. Check with
  `python -m app.training.meta --status`.

### 🔬 Tried, and honestly hit a wall
- Profitability. Every angle (better model, cost-aware labels, calibration,
  high-confidence selectivity) lands at **break-even**. This is the measured ceiling
  of the approach, not a to-do item.

### 🛣️ Options from here (in honest order of value)
1. **Use it as a learning tool** — the highest-value path *today*. Paper-trade,
   read patterns, build discipline. This works right now, risk-free.
2. **Let the meta-model mature** — leave auto-log running a few weeks, then train it.
   The one remaining idea that could find a *selective* edge (may still fail).
3. **Market-regime detection** (from the spec) — worth building for rigor; likely
   confirms the wall rather than breaks it.
4. **Not recommended:** connecting real-money trading. The model is break-even —
   automating it would just pay fees to break even, and risks real loss on variance.

### 🧭 The one-sentence status
> **A finished, honest, well-tested trading *practice* platform — great for learning,
> break-even for profit — with auto-log quietly gathering data for the last
> experiment (the meta-model) that might, or might not, find a small selective edge.**
