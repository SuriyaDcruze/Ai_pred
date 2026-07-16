"""Stock Intelligence — the V3 explainable, transparent per-stock analysis.

This is the culmination the V3 spec asks for: not another accuracy experiment, but the
honest assembly of everything we *validated* into a single explainable recommendation.
For one stock it produces market state, relative strength (context), the direction
read, the **outcome-model decision** (the real edge), historical similarity, a trade
plan, and a plain-English "why" with positive/negative factors.

Honesty is structural:
  * The DECISION comes from the validated outcome model, not from context features.
  * Market state and relative strength are labelled **context**, never edge (we proved
    features don't add trade-selection edge).
  * WAIT is the default when the outcome model isn't confident — "fewer high-quality
    opportunities beats many weak signals" (V3).
  * Every number shown is real; nothing is inflated.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.utils.logging import get_logger

logger = get_logger(__name__)

_NSE_OUTCOME_PATH = "artifacts/outcome_model_nse.pkl"


def _market_state(row: pd.Series) -> dict:
    """Honest, rule-based market state (we proved learned regimes are noise)."""
    adx = float(row.get("adx", 0) or 0)
    ema9 = float(row.get("ema_9", 0) or 0)
    ema21 = float(row.get("ema_21", 0) or 0)
    ema50 = float(row.get("ema_50", 0) or 0)
    close = float(row.get("close", 0) or 0)
    atr = float(row.get("atr", 0) or 0)
    atr_pct = (atr / close * 100) if close else 0.0

    up = ema9 > ema21 > ema50
    down = ema9 < ema21 < ema50
    if adx >= 25 and up:
        trend = "Strong Uptrend"
    elif up:
        trend = "Uptrend"
    elif adx >= 25 and down:
        trend = "Strong Downtrend"
    elif down:
        trend = "Downtrend"
    else:
        trend = "Sideways / Range"

    vol = "High volatility" if atr_pct >= 3 else "Low volatility" if atr_pct <= 1 else "Normal volatility"
    return {"trend": trend, "volatility": vol, "adx": round(adx, 1), "atr_pct": round(atr_pct, 2)}


def _reasons(row: pd.Series, side: int, rs_nifty: float, state: dict, sim: dict) -> tuple[list, list]:
    """Plain-English positive / negative factors from the real feature values."""
    pos, neg = [], []
    rsi = float(row.get("rsi", 50) or 50)
    macd_h = float(row.get("macd_hist", 0) or 0)
    long = side > 0

    # trend
    if ("Uptrend" in state["trend"]) == long and state["trend"] != "Sideways / Range":
        pos.append(f"Trend agrees ({state['trend']})")
    elif state["trend"] == "Sideways / Range":
        neg.append("No clear trend (sideways) — setups are lower quality here")
    else:
        neg.append(f"Trend disagrees ({state['trend']})")

    # momentum
    if long and macd_h > 0:
        pos.append("Momentum rising (MACD histogram positive)")
    elif not long and macd_h < 0:
        pos.append("Momentum falling (MACD histogram negative)")
    else:
        neg.append("Momentum not confirming the direction")

    # RSI stretch
    if long and rsi >= 72:
        neg.append(f"RSI {rsi:.0f} — overbought, may be chasing")
    elif not long and rsi <= 28:
        neg.append(f"RSI {rsi:.0f} — oversold, may be catching a falling knife")
    else:
        pos.append(f"RSI {rsi:.0f} — not stretched")

    # relative strength vs Nifty (context)
    if rs_nifty > 1:
        (pos if long else neg).append(f"Outperforming Nifty by {rs_nifty:.1f}% (relative strength)")
    elif rs_nifty < -1:
        (neg if long else pos).append(f"Underperforming Nifty by {abs(rs_nifty):.1f}%")

    # volatility
    if state["volatility"] == "High volatility":
        neg.append("High volatility — stops get hit by noise")

    # historical similarity (context)
    if sim and not np.isnan(sim.get("win_rate", np.nan)):
        wr = sim["win_rate"] * 100
        (pos if wr >= 55 else neg).append(
            f"Similar past setups won {wr:.0f}% (avg {sim['avg_R']:+.2f}R) — historical context"
        )
    return pos, neg


def analyze_stock(service, symbol: str, interval: str = "1d", horizon: int = 5) -> dict:
    """Full explainable intelligence report for one stock. Never raises."""
    import asyncio

    from app.ai.dataset import make_labels
    from app.ai.outcome_model import (OutcomePredictor, build_outcome_features,
                                      direction_side)
    from app.ai.similarity_engine import SimilarityEngine
    from app.data.schemas import candles_to_frame
    from app.features.india import add_india_features
    from app.stream.yahoo import YahooClient
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    try:
        yc = YahooClient()
        df = candles_to_frame(asyncio.run(yc.fetch_history(symbol, interval, total=1500)))
        is_nse = symbol.upper().endswith(".NS")
        nifty = None
        if is_nse:
            try:
                nifty = candles_to_frame(asyncio.run(yc.fetch_history("^NSEI", interval, total=1500)))
            except Exception:  # noqa: BLE001
                nifty = None
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": f"data fetch failed: {exc}"}
    if len(df) < 300:
        return {"available": False, "error": "not enough history"}

    fb = service.feature_builder
    feats = fb.build_frame(df)
    feats = add_india_features(feats, nifty)
    cols = [c for c in fb.feature_columns if c in feats.columns]
    X = np.nan_to_num(feats[cols].to_numpy(dtype="float32"))
    row = feats.iloc[-1]

    # direction: train on history, read current bar out-of-sample
    lab = make_labels(feats["close"].to_numpy(), feats["high"].to_numpy(),
                      feats["low"].to_numpy(), horizon=horizon, cost_pct=0.002)
    y = lab["direction"]
    n = min(len(X), len(y))
    Xtr, ytr = X[: n - horizon], y[: n - horizon]
    ok = ytr >= 0
    if ok.sum() < 100 or len(set(ytr[ok])) < 2:
        return {"available": False, "error": "insufficient labelled history"}
    sc = StandardScaler().fit(Xtr[ok])
    dm = LogisticRegression(max_iter=1500).fit(sc.transform(Xtr[ok]), ytr[ok])
    probs = np.zeros(3)
    p = dm.predict_proba(sc.transform(X[-1:]))[0]
    for i, c in enumerate(dm.classes_):
        probs[c] = p[i]
    side = int(direction_side(probs.reshape(1, -1))[0])
    dir_conf = float(max(probs))

    # outcome model (NSE model for .NS, else the crypto/global one)
    outcome = OutcomePredictor.load(_NSE_OUTCOME_PATH if is_nse else None)
    verdict = outcome.assess(X[-1], probs) if outcome and side != 0 else None

    # historical similarity (context): how did trades in *this direction* fare in
    # similar past conditions? Fit on history, query the current bar.
    sim = {}
    try:
        from app.ai.outcome_model import TARGET_FIRST, outcome_labels

        side_arr = np.full(n, side, dtype=np.int64)          # assume the current side throughout
        oc, r_out = outcome_labels(
            feats["high"].to_numpy()[:n], feats["low"].to_numpy()[:n],
            feats["close"].to_numpy()[:n], side_arr, feats["atr"].to_numpy()[:n], horizon=horizon,
        )
        Xoc = build_outcome_features(X[:n], np.tile(probs, (n, 1)))
        m = (oc >= 0) & ~np.isnan(r_out)
        if m.sum() > 40:
            hist = np.where(m)[0][:-1]                        # history only, excl. current bar
            se = SimilarityEngine(k=20).fit(
                Xoc[hist], (oc[hist] == TARGET_FIRST).astype(int), r_out[hist]
            )
            sim = se.query(Xoc[-1])
    except Exception as exc:  # noqa: BLE001
        logger.debug("similarity skipped: %s", exc)

    state = _market_state(row)
    rs_nifty = float(row.get("in_rs_nifty_50", 0.0) or 0.0) * 100
    close = float(df["close"].iloc[-1])
    atr = float(row.get("atr", close * 0.02) or close * 0.02)

    take = bool(verdict and verdict["take"])
    if side == 0:
        rec, action = "WAIT", None
    elif not take:
        rec, action = "WAIT", ("BUY" if side > 0 else "SELL")
    else:
        rec = action = "BUY" if side > 0 else "SELL"

    plan = None
    if side != 0:
        if side > 0:
            plan = {"entry": round(close, 2), "stop": round(close - atr, 2),
                    "target1": round(close + 1.5 * atr, 2), "target2": round(close + 2.5 * atr, 2)}
        else:
            plan = {"entry": round(close, 2), "stop": round(close + atr, 2),
                    "target1": round(close - 1.5 * atr, 2), "target2": round(close - 2.5 * atr, 2)}
        plan["risk_reward"] = "1 : 1.5 (→ 2.5)"
        plan["holding"] = f"{horizon}–{horizon * 2} trading days"

    pos, neg = _reasons(row, side if side else 1, rs_nifty, state, sim)

    return {
        "available": True, "symbol": symbol.replace(".NS", ""), "last_price": round(close, 2),
        "recommendation": rec,
        "leaning": ("BUY" if side > 0 else "SELL" if side < 0 else "—"),
        "direction_confidence": round(dir_conf, 3),
        "outcome_probability": round(verdict["p_target"], 3) if verdict else None,
        "decision": "TAKE" if take else "VETO/WAIT",
        "market_state": state,
        "relative_strength_vs_nifty_pct": round(rs_nifty, 2) if nifty is not None else None,
        "historical_similarity": (
            {"win_rate": round(sim["win_rate"], 3), "avg_R": round(sim["avg_R"], 2),
             "n": sim["n"]} if sim and not np.isnan(sim.get("win_rate", np.nan)) else None
        ),
        "plan": plan, "positive_factors": pos, "negative_factors": neg,
        "explanation": _summary(rec, action, state, rs_nifty, verdict, sim, nifty is not None),
        "disclaimer": "Backtest-verified, NOT proven live. Paper-trade first. Not SEBI-registered advice.",
    }


def _summary(rec, action, state, rs, verdict, sim, has_nifty) -> str:
    if rec == "WAIT" and action is None:
        return f"No directional edge right now ({state['trend']}). The honest call is WAIT."
    op = f"{round(verdict['p_target'] * 100)}%" if verdict else "n/a"
    if rec == "WAIT":
        return (f"Direction leans {action}, but the outcome model VETOes it "
                f"(only {op} chance of hitting target before stop). Fewer, higher-quality "
                f"trades beat many weak ones — WAIT.")
    bits = [f"{action}: {state['trend'].lower()}", f"outcome probability {op}"]
    if has_nifty and abs(rs) > 1:
        bits.append(f"{'outperforming' if rs > 0 else 'underperforming'} Nifty by {abs(rs):.1f}%")
    if sim and not np.isnan(sim.get("win_rate", np.nan)):
        bits.append(f"similar setups won {round(sim['win_rate'] * 100)}%")
    return "Both models agree. " + ", ".join(bits) + ". Use the stop, risk ≤1%, paper-trade first."
