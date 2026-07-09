# How the model works — and the honest truth about accuracy

## 1. What the model sees
Every candle is turned into **45 numbers** (features): returns, RSI, MACD, ATR,
ADX, VWAP, Bollinger width, SuperTrend, SMC structure (BOS/CHoCH/FVG/order
blocks), and **candlestick patterns** (Hammer, Engulfing, Doji, Stars, Pin Bars…).
The model reads the **last 128 candles** — a 128 × 45 grid — as one input.

## 2. What it predicts
It's **multi-task**. For the next few candles it outputs:
- **P(up)**, **P(down)**, **P(sideways)** — the direction probabilities
- predicted **high / low / close** (relative to the current price)
- expected **volatility**
- a **confidence** score

The "prediction" you see is the highest of P(up)/P(down)/P(sideways).

## 3. How it decides to trade
A prediction is **not** a trade. A trade only fires when **all** of these agree:
trend ✔ · volume ✔ · momentum ✔ · market structure ✔ · candlestick ✔ ·
**confidence ≥ 80%** · **risk:reward ≥ 2**. Otherwise it says **WAIT**. This is why
it says WAIT most of the time — it refuses low-quality setups on purpose.

## 4. How "accuracy" is measured
We measure **directional accuracy** out-of-sample: on data the model never trained
on, how often did "up" actually go up (and "down" go down)?
- **50%** = a coin flip (no skill)
- **>55%** = a possible real edge
- **>58%** = notable (rare)

## 5. The honest result (this is "what's wrong")
On the current model, measured on a large out-of-sample sample:

> **~51.6% directional accuracy.**

That is **barely above a coin flip.** Translation:
- The engineering works — data, features, training, backtest, forward-test all run.
- But the model has **no reliable edge**. It cannot predict 1-hour BTC direction
  well enough to make money after fees. This is normal — short-term price
  prediction is one of the hardest problems in finance, and most models fail here.

**So nothing is "broken."** The code is correct. What's "wrong" is the *signal*:
this feature set on this timeframe doesn't contain enough predictive information.
A 51.6% model with 0.15% fees is a slow loser, not a winner.

## 6. What would actually raise the accuracy
In rough order of impact:
1. **Find a predictable regime** — run the **signal sweep** (`app/backtest/sweep.py`)
   across 15m / 1h / 4h / 1d × horizons; if *any* clears ~55%, focus there.
2. **Better features / targets** — order-flow, funding, cross-asset, longer horizons,
   or predicting "does a setup hit target before stop" instead of raw direction.
3. **More & cleaner data** — years across many symbols, on a GPU (Colab).
4. **A win/loss filter** — train a second model on the forward-tested track record
   to keep only the setups that actually win.
5. **Accept the base rate** — if nothing beats ~52%, the honest conclusion is that
   this approach doesn't predict this market, and the design must change.

## 7. The one number to trust
Not the training loss, not a single backtest — the **forward-tested win rate** in
the dashboard's 📓 Track Record, over **many** calls. That's the truth about
whether it works, on live data you can't overfit to.
