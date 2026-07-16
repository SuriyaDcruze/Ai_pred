# What We Built & What We Actually Got — The Honest Results

This is the plain-language scoreboard for the Aegis project: everything we tried,
and the **real, measured numbers** — not marketing, not hopes. Every figure here
came from running the code on live market data, out-of-sample, with fees.

> **One-line summary:** Direction prediction is stuck at ~61% and is **break-even**
> (no feature idea moved it — measured). But a separate **outcome model** (trade
> selection) turns break-even into **positive expectancy in backtest**, verified on
> **both crypto and Indian NSE stocks** and on an untouched final test. That edge is
> the real result — **but it has zero live track record and is not proven with real
> money.**

> **⚠️ For anyone reviewing this for real money or as a product:** the positive
> numbers below are **backtest only**. Winning samples are modest (97 crypto / 41 NSE
> untouched trades). There are **no live trades**. The honest next step is weeks-to-
> months of paper forward-testing before trusting it. Selling trading advice in India
> requires SEBI compliance — this repo is decision-support/education, not advice.

---

## 📊 The headline numbers

| What | Number | Plain meaning |
|------|--------|---------------|
| **Directional accuracy** | **~59%** | When it says up/down, it's right ~59% of the time (50% = coin flip) |
| **Trading result after fees** | **~0% (break-even)** | 537 trades, avg **−0.005R**, t = −0.09 — not different from zero |
| **High-confidence signals** | **~90% "accurate", but 0 profit** | Looked amazing; didn't survive real stops + fees |
| **After trying to improve it** | **still ~61%, still break-even** | 4 specs, 8 feature groups tried; every gain was inside the noise |
| **🟢 Outcome model (trade selection)** | **break-even → +0.22R to +0.48R** | Filtering trades by "target before stop?" — verified on untouched final test (PF 2.31). First real edge. |
| **🇮🇳 Outcome edge on Indian NSE stocks** | **+0.49R WF, +0.84R untouched** | Same edge holds on NSE daily (PF 2.7 / 6.9). Generalises across markets — strong evidence it's real. |
| Similarity engine (kNN explainability) | no predictive edge | Confirmed: outcome model already captures it. Kept for "similar setups won X%" explanations. |
| Parameter sensitivity (overfit check) | edge robust | Filtered edge stays positive across horizons 8–24 — not curve-fit to one lucky setting. |
| **Best possible with this approach** | **~59-61% direction, no reliable profit** | Not 70%. Not 90%. Those are impossible here. |

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

### 7. New features — regime, price-action, session (the accuracy push)
A detailed research spec asked us to lift directional accuracy from ~59% toward
60-63% by adding three new feature groups. We built them as **isolated challengers**
(production model never touched) and judged them with **purged 5-fold walk-forward
across BTC/ETH/SOL**, full metrics, 8 leakage tests.

| Feature set | Walk-forward acc | vs base | Decision |
|---|---|---|---|
| Champion (base, 45 feats) | 61.01% ± 2.59pp | — | — |
| + market regime | 61.37% | +0.37pp | ❌ REJECT |
| + price action | 60.03% | −0.98pp | ❌ REJECT |
| + session | 61.47% | +0.47pp | ❌ REJECT |
| + combinations | all worse | — | ❌ REJECT |

- **Why rejected:** the +0.37/+0.47pp "gains" are **~7× smaller than the fold-to-fold
  noise (±2.59pp)** — statistically zero. The session gain also came from **class
  imbalance** (just predicting DOWN more). And combining the "winners" made it
  *worse* — the fingerprint of noise, not signal.
- **Verdict:** ❌ No robust improvement. We hardened the accept rule with uncertainty
  + class-balance gates so noise can't sneak through, and left production unchanged.
  (Full report: `reports/accuracy_improvement_summary.md`.)

### 8. Phase-2 features — multi-timeframe + interactions (the accuracy push, again)
A fourth spec asked for higher-value features. We built its top two ideas and ran
them through the same purged 5-fold walk-forward gauntlet.

| Feature set | Walk-forward acc | vs base | Decision |
|---|---|---|---|
| Champion (base) | 61.18% ± 3.35pp | — | — |
| + multi-timeframe (4h+1d) | 61.55% | +0.37pp | ❌ REJECT |
| + feature interactions | 61.32% | +0.14pp | ❌ REJECT |
| + market regime (retest) | 61.50% | +0.32pp | ❌ REJECT |
| + combinations | ≤ 61.69% | ≤ +0.51pp | ❌ REJECT |

- **Multi-timeframe** gave the 1h model real 4h/1d context (leakage-safe — only closed
  higher-TF bars). **Interactions** handed the *linear* model products like ADX×Volume
  it can't learn itself. Both reasonable; both landed inside the ±3.35pp noise.
- **Verdict:** ❌ No gain clears the noise floor (1.68pp). Full report:
  `reports/final_accuracy_summary.md`.

> **After three rounds and eight feature groups: feature engineering is exhausted as
> an accuracy lever.** The measured ceiling for this approach is ~61%. More features
> just fit more noise. This is a real finding, arrived at honestly.

### 9. 🟢 The Outcome Model — the FIRST verified edge (target-before-stop)
Instead of predicting direction better (stuck), we built a **second, independent
model** that predicts *"will this trade hit its target before its stop?"* and vetoes
the likely losers. This attacks the real gap — accuracy ≠ profit — head on.

| Test | Take every signal | **Filtered by outcome model** |
|---|---|---|
| Non-overlapping walk-forward | −0.011R (PF 0.98) | **+0.225R (PF 1.47)** |
| **Untouched final test** (never in any fold) | +0.035R (PF 1.06) | **+0.482R (PF 2.31)** |

- **Why this one is real** (unlike the confidence bucket that died): the threshold
  sweep is **monotonic** (0.40→0.70 ⇒ +0.08R→+0.33R — the fingerprint of true signal),
  it held across BTC/ETH/SOL, and it **survived both** non-overlapping sampling *and*
  the untouched final test — the exact tests that killed earlier leads.
- **Honest caveats:** the final-test filtered sample is 97 non-overlapping trades
  (modest); it's R-expectancy, not a full compounding backtest; crypto/1h only; needs
  **live forward-testing before real money.** But it is a genuine, verified edge — the
  first in the project. Report: `reports/outcome_model_summary.md`.
- **Verdict:** 🟢 ACCEPT (verified). The lesson that took the whole project: don't
  predict direction better — **select trades better.**

---

## 🔧 How we've tried to improve it (the complete list)

Everything attempted to push accuracy or profit, and where each landed. This is the
answer to *"how do we improve it?"* — we already tried the serious ideas:

| Attempt | Outcome |
|---|---|
| Better model (deep net → logistic) | ✅ 48% → 59% direction |
| Cost-aware labels | ✅ cleaner target, kept |
| Probability calibration | ✅ honest confidence (ECE 0.14 → 0.05) |
| High-confidence selectivity | ❌ looked 90%, no real profit |
| Market-regime features | ❌ within noise (tested twice) |
| Price-action features | ❌ hurt accuracy |
| Session/time features | ❌ noise + class imbalance |
| **Multi-timeframe fusion** (4h/1d context) | ❌ +0.37pp, within noise |
| **Feature interactions** (ADX×Volume etc.) | ❌ +0.14pp, within noise |
| Feature combinations | ❌ all degraded or noise |
| Meta-model (learn which signals win) | ⏳ built, needs ~200 logged calls |
| **🟢 Target-before-stop outcome model** | ✅ **BUILT & VERIFIED — turns break-even into +0.22R to +0.48R** |

**The pattern — and the breakthrough:** across **4 specs and 8 feature groups**,
accuracy would not move past ~61% (all noise). Feature engineering is **exhausted**.
But the honest conclusion from that — *stop chasing accuracy, chase trade selection* —
led to the **outcome model**, which **worked**: filtering direction signals by
"will this hit target before stop?" turns break-even into positive expectancy, and it
**survived the untouched final test** (PF 2.31). That's the first verified edge here.

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
- Honest tooling: baseline race, calibration report, confidence analyzer, backtester,
  **purged walk-forward harness + feature-challenger pipeline** (new)
- Nightly retrain (champion/challenger) and meta-model, both built
- **~215 automated tests passing**, incl. 8 leakage / future-invariance tests; all
  pushed to `SuriyaDcruze/Ai_pred`
- A red **learning-mode banner** on the dashboard, on by default, telling the truth

### ⏳ In progress / waiting on time
- **Meta-model needs data.** It learns which signals win from your live Track Record,
  but needs ~200 resolved calls. **Auto-log is ON**, filling the record server-side
  (~24 calls/day on 1h) — so it's accumulating. Check with
  `python -m app.training.meta --status`.

### 🔬 Tried, and honestly hit a wall
- **Accuracy.** Better model, cost-aware labels, calibration, and now **8 feature
  groups across 4 specs** (regime, price-action, session, multi-timeframe,
  interactions, combinations) — accuracy sits at **~61% and won't budge robustly.**
  Every feature gain was inside the fold-to-fold noise. Feature engineering is
  **exhausted** as a lever. (Fully measured in `reports/`.)
- **Profitability.** Every angle lands at **break-even after fees.** This is the
  measured ceiling of the approach, not a to-do item.

### 🛣️ Options from here (in honest order of value)
1. **Use it as a learning tool** — the highest-value path *today*. Paper-trade,
   read patterns, build discipline. This works right now, risk-free.
2. **Build the target-before-stop outcome model** — the **best remaining idea.** It
   predicts "will the target be hit before the stop?" — attacking *profit* directly
   instead of accuracy (which we've now shown is stuck). All the tooling for it
   exists. It may still fail, but it's the honest next experiment.
3. **Let the meta-model mature** — leave auto-log running a few weeks, then train it.
   Also attacks trade *selection* rather than raw accuracy.
4. **More/different data or horizons** — try 6h/24h horizons, more history, more
   assets. Low odds of a breakthrough, but cheap to test.
5. **Not recommended:** connecting real-money trading. The model is break-even —
   automating it would just pay fees to break even, and risks real loss on variance.

### 💬 "People are suggesting ways to improve it" — how to judge those suggestions
Anyone can suggest an idea. The honest test any suggestion must pass here:
- Does it improve **out-of-sample** accuracy (not training accuracy)?
- Across **multiple folds** and **multiple assets** (not one lucky run)?
- On **non-overlapping**, **leakage-free** samples?
- **Balanced** across UP and DOWN (not just predicting one direction more)?
- Does the gain **exceed the fold-to-fold noise** (~2.6pp here)?
- And ultimately — does it survive the **real backtester with fees** (accuracy ≠ profit)?

Every suggestion we've run through this — including the latest feature ideas — has
failed at least one of these gates. That's not pessimism; it's the bar real quant
work has to clear. Bring any suggestion and we'll run it through the same honest
pipeline (`python -m app.training.challenger_compare`) rather than trusting a claim.

### 🧭 The one-sentence status
> **A finished, honest, well-tested platform whose direction model is stuck at ~61% /
> break-even — but whose new *outcome model* (trade selection, not direction) turns
> that into a verified +0.22R to +0.48R edge that survived the untouched final test.
> First real result. Next: wire it into the live decision engine + forward-test it
> before any real money.**

### 🚀 The breakthrough, and where it stands now
The whole project's lesson in one line: **we couldn't predict direction better, but we
learned to *select trades* better.** The outcome model is the first verified edge, and
it is now **wired into the live dashboard** (a TAKE/VETO layer) and powers the
**NSE/Groww screener**. Since the breakthrough we also:
- ✅ Ran a **full compounding backtest** — confirmed filtered ≫ take-all on every
  asset, but the headline % is a fantasy (idealised fills); trust the per-trade R.
- ✅ **Parameter-sensitivity check** — the edge holds across horizons 8–24 (not
  curve-fit).
- ✅ **Validated on Indian NSE stocks** — the edge generalises (crypto 1h + NSE daily).
- ✅ Built the **NSE screener** ("which stock to buy on Groww, where to sell").

**The one thing still missing — and it's the big one: a live track record.** Every
positive number is backtest. Immediate next step: **weeks-to-months of paper
forward-testing** to see if the edge survives real slippage, gaps, and live data.
Only after that: tiny real money, then (honestly marketed) a product.

### ❓ Open questions for a mentor / reviewer
1. Is a **41-trade** (NSE) / **97-trade** (crypto) untouched-test edge enough to
   justify live paper-testing, or should we gather more first?
2. The outcome model is meta-labeling (López de Prado). Is our leakage control on the
   out-of-fold direction probabilities rigorous enough?
3. For an Indian product: what's the compliant path — SEBI RA registration, or keep it
   strictly as education/decision-support with no "advice"?
4. Cost modelling: we use flat R-based costs. How much would realistic NSE
   slippage/impact erode the +0.49R edge?
