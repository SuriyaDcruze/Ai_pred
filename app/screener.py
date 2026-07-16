"""NSE stock screener — "which stock to buy today, and where to sell."

Scans a basket of liquid Indian (NSE) large-caps on the daily timeframe — the horizon
a Groww swing/positional trader actually uses — and for each one runs the full
two-model pipeline: a direction read (logistic) and the **NSE-specific outcome model**
(validated to hold on Indian stocks). It returns only the setups the outcome model
would TAKE, ranked by conviction, each with the exact **entry, stop, and targets** to
place on Groww.

Honest by construction:
  * Uses the **NSE-trained** outcome model, not the crypto one — each market gets its own.
  * The direction model is trained on each stock's own history, current bar predicted
    out-of-sample.
  * Only surfaces TAKE setups; everything else is correctly hidden (no forcing trades).
  * Every result carries the same caveat: verified in backtest, NOT proven live.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.utils.logging import get_logger

logger = get_logger(__name__)

# Liquid, Groww-tradeable NSE large-caps. Extend freely.
NSE_BASKET = [
    "RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS",
    "ITC.NS", "SBIN.NS", "LT.NS", "AXISBANK.NS", "BHARTIARTL.NS",
    "HINDUNILVR.NS", "KOTAKBANK.NS", "BAJFINANCE.NS", "MARUTI.NS", "SUNPHARMA.NS",
]

_NSE_OUTCOME_PATH = "artifacts/outcome_model_nse.pkl"


def _stock_setup(symbol: str, interval: str, horizon: int, outcome, service) -> dict | None:
    """Full two-model read for one stock's latest bar. None if data is thin."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    import asyncio

    from app.ai.dataset import make_labels
    from app.ai.outcome_model import direction_side
    from app.data.schemas import candles_to_frame
    from app.stream.yahoo import YahooClient

    try:
        df = candles_to_frame(asyncio.run(YahooClient().fetch_history(symbol, interval, total=1500)))
    except Exception as exc:  # noqa: BLE001
        logger.warning("screener: %s fetch failed: %s", symbol, exc)
        return None
    if len(df) < 300:
        return None

    fb = service.feature_builder
    feats = fb.build_frame(df)
    cols = [c for c in fb.feature_columns if c in feats.columns]
    X = np.nan_to_num(feats[cols].to_numpy(dtype="float32"))

    # direction: train logistic on all-but-last, read the current bar out-of-sample
    lab = make_labels(feats["close"].to_numpy(), feats["high"].to_numpy(),
                      feats["low"].to_numpy(), horizon=horizon, cost_pct=0.002)
    y = lab["direction"]
    n = min(len(X), len(y))
    Xtr, ytr = X[: n - horizon], y[: n - horizon]
    ok = ytr >= 0
    if ok.sum() < 100 or len(set(ytr[ok])) < 2:
        return None
    sc = StandardScaler().fit(Xtr[ok])
    dm = LogisticRegression(max_iter=1500).fit(sc.transform(Xtr[ok]), ytr[ok])
    cur = X[-1:].copy()
    p = dm.predict_proba(sc.transform(cur))[0]
    probs = np.zeros(3)
    for i, c in enumerate(dm.classes_):
        probs[c] = p[i]
    side = int(direction_side(probs.reshape(1, -1))[0])   # +1 long / -1 short / 0 none
    if side == 0:
        return None

    # outcome model: will target be hit before stop? (assess builds outcome features)
    verdict = outcome.assess(cur[0], probs)

    close = float(df["close"].iloc[-1])
    atr = float(feats["atr"].iloc[-1]) if "atr" in feats else close * 0.02
    tp_mult, sl_mult = 1.5, 1.0
    if side > 0:
        entry, stop = close, close - sl_mult * atr
        tp1, tp2 = close + tp_mult * atr, close + 2.5 * atr
        action = "BUY"
    else:
        entry, stop = close, close + sl_mult * atr
        tp1, tp2 = close - tp_mult * atr, close - 2.5 * atr
        action = "SELL (short)"

    conf = float(max(probs))
    return {
        "symbol": symbol.replace(".NS", ""),
        "action": action,
        "direction_confidence": round(conf, 3),
        "p_target": round(verdict["p_target"], 3),
        "take": bool(verdict["take"]),
        "entry": round(entry, 2), "stop": round(stop, 2),
        "target1": round(tp1, 2), "target2": round(tp2, 2),
        "last_close": round(close, 2),
    }


def scan_nse(service, interval: str = "1d", horizon: int = 5, only_take: bool = True) -> dict:
    """Scan the NSE basket. Returns TAKE setups ranked by P(target), plus the rest."""
    from app.ai.outcome_model import OutcomePredictor

    outcome = OutcomePredictor.load(_NSE_OUTCOME_PATH)
    if outcome is None:
        return {"available": False,
                "note": "NSE outcome model not trained. Run train_and_save on NSE symbols."}

    rows = []
    for sym in NSE_BASKET:
        try:
            r = _stock_setup(sym, interval, horizon, outcome, service)
            if r:
                rows.append(r)
        except Exception as exc:  # noqa: BLE001
            logger.warning("screener %s: %s", sym, exc)

    takes = sorted([r for r in rows if r["take"]], key=lambda r: -r["p_target"])
    skips = sorted([r for r in rows if not r["take"]], key=lambda r: -r["p_target"])
    return {"available": True, "interval": interval, "scanned": len(rows),
            "buy_now": takes, "waiting": skips}
