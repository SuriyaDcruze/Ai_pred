"""Full compounding backtest of the outcome-model-filtered strategy.

The R-expectancy checks proved the outcome model turns break-even into a positive
edge. This is the final, most honest layer: turn those R-multiples into an actual
**equity curve** and read off the numbers that decide whether an edge is *tradeable* —
compounded return, **max drawdown**, and **Sharpe** — not just average R.

Realism rules, chosen to avoid flattering ourselves:
  * **One position at a time.** No overlapping trades — you can't risk 1% on 100
    simultaneous positions. A new trade is only entered when the previous has closed.
    (This is also why the earlier overlapping trade counts were not independent.)
  * **Fixed-fractional risk.** Each trade risks `risk_pct` of *current* equity, so wins
    and losses compound — the real account experience.
  * **Every trade is out-of-sample.** Outcome probabilities come from walk-forward
    out-of-fold prediction on the dev set, then a model trained on all dev data for the
    untouched final slice. No look-ahead.
  * **Costs already in the R.** The realised R has round-trip cost subtracted.

    python -m app.backtest.outcome_backtest --assets BTCUSDT ETHUSDT SOLUSDT
"""

from __future__ import annotations

import argparse
import math

import numpy as np
import pandas as pd

from app.ai.outcome_model import (
    TARGET_FIRST,
    build_outcome_features,
    direction_side,
    oof_direction_probs,
    outcome_labels,
)
from app.training.outcome_training import _prep
from app.utils.logging import get_logger

logger = get_logger(__name__)


def _oof_outcome_probs(Xoc, oc_lab, mask, horizon, n_folds, seed):
    """Out-of-fold P(target first) for the dev set + a model trained on all dev to
    score the untouched final slice. Every value is out-of-sample."""
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.preprocessing import StandardScaler

    n = len(Xoc)
    probs = np.full(n, np.nan)
    dev_end = int(n * 0.85)
    fold = dev_end // (n_folds + 1)

    def fit(tr_idx):
        ytr = (oc_lab[tr_idx] == TARGET_FIRST).astype(int)
        if len(set(ytr)) < 2:
            return None
        sc = StandardScaler().fit(Xoc[tr_idx])
        clf = HistGradientBoostingClassifier(max_depth=4, max_iter=200, learning_rate=0.05,
                                             random_state=seed)
        clf.fit(sc.transform(Xoc[tr_idx]), ytr)
        return sc, clf

    for k in range(1, n_folds + 1):
        tr_end = fold * k
        vs = tr_end + horizon
        ve = min(vs + fold, dev_end)
        if vs >= ve:
            continue
        tr = np.arange(0, tr_end)[mask[:tr_end]]
        if len(tr) < 100:
            continue
        m = fit(tr)
        if m is None:
            continue
        sc, clf = m
        va = np.arange(vs, ve)
        probs[va] = clf.predict_proba(sc.transform(Xoc[va]))[:, 1]

    # untouched final slice: train on all dev
    trd = np.arange(0, dev_end - horizon)[mask[:dev_end - horizon]]
    if len(trd) >= 100:
        m = fit(trd)
        if m is not None:
            sc, clf = m
            fin = np.arange(dev_end, n)
            probs[fin] = clf.predict_proba(sc.transform(Xoc[fin]))[:, 1]
    return probs, dev_end


def _simulate(entries: list[tuple[int, float]], horizon: int, start_equity: float,
              risk_pct: float, cost_r: float) -> dict:
    """Sequential one-position-at-a-time compounding sim.

    entries = sorted list of (bar_index, realised_R). Returns equity curve + stats.
    """
    equity = start_equity
    curve = [equity]
    peak = equity
    max_dd = 0.0
    trade_returns = []
    open_until = -1
    n_taken = 0

    for bar, r in entries:
        if bar <= open_until:
            continue                                  # a position is still open
        net_r = r - cost_r
        risk_amt = equity * risk_pct
        pnl = net_r * risk_amt
        equity += pnl
        trade_returns.append(pnl / (equity - pnl))    # return on equity for this trade
        curve.append(equity)
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)
        open_until = bar + horizon
        n_taken += 1

    tr = np.array(trade_returns)
    wins = tr[tr > 0]
    losses = tr[tr < 0]
    sharpe = float(tr.mean() / tr.std() * math.sqrt(len(tr))) if len(tr) > 1 and tr.std() > 0 else 0.0
    pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else float("inf")
    return {
        "trades": n_taken, "final_equity": equity,
        "total_return_pct": (equity / start_equity - 1) * 100,
        "win_rate": float((tr > 0).mean()) if len(tr) else 0.0,
        "max_drawdown_pct": max_dd * 100, "profit_factor": pf,
        "sharpe": sharpe, "curve": curve,
    }


def run(assets, interval, bars, horizon, n_folds, cost_pct, threshold, risk_pct, seed):
    per_asset = []
    for asset in assets:
        X, y_dir, frame = _prep(asset, interval, bars, horizon, cost_pct)
        atr = frame["atr"].to_numpy(); close = frame["close"].to_numpy()
        high = frame["high"].to_numpy(); low = frame["low"].to_numpy()
        dp = oof_direction_probs(X, y_dir, horizon=horizon, n_folds=n_folds, seed=seed)
        side = direction_side(dp)
        oc, r_out = outcome_labels(high, low, close, side, atr, horizon=horizon)
        Xoc = build_outcome_features(X, dp)
        mask = (side != 0) & (oc >= 0) & ~np.isnan(r_out)
        oc_prob, dev_end = _oof_outcome_probs(Xoc, oc, mask, horizon, n_folds, seed)

        # cost in R units (fee% of price / stop distance), nominal fallback 0.20
        with np.errstate(divide="ignore", invalid="ignore"):
            cr = (cost_pct * close) / (1.0 * atr)
        cost_r = float(np.nanmedian(np.where(np.isfinite(cr), cr, 0.20)))

        # candidate trades: every valid directional bar with an out-of-sample outcome prob
        valid = mask & ~np.isnan(oc_prob)
        idxs = np.where(valid)[0]
        take_all = [(int(i), float(r_out[i])) for i in idxs]
        filtered = [(int(i), float(r_out[i])) for i in idxs if oc_prob[i] >= threshold]

        sim_all = _simulate(take_all, horizon, 10_000, risk_pct, cost_r)
        sim_flt = _simulate(filtered, horizon, 10_000, risk_pct, cost_r)
        per_asset.append({"asset": asset, "take_all": sim_all, "filtered": sim_flt})
        logger.info("%s: take-all %.0f%% (DD %.0f%%) | filtered %.0f%% (DD %.0f%%, Sharpe %.2f)",
                    asset, sim_all["total_return_pct"], sim_all["max_drawdown_pct"],
                    sim_flt["total_return_pct"], sim_flt["max_drawdown_pct"], sim_flt["sharpe"])
    return per_asset


def main() -> None:
    p = argparse.ArgumentParser(description="Compounding backtest of the outcome-filtered strategy")
    p.add_argument("--assets", nargs="+", default=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    p.add_argument("--interval", default="1h")
    p.add_argument("--bars", type=int, default=20_000)
    p.add_argument("--horizon", type=int, default=12)
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--cost-pct", dest="cost_pct", type=float, default=0.0012)
    p.add_argument("--threshold", type=float, default=0.60)
    p.add_argument("--risk-pct", dest="risk_pct", type=float, default=0.01)
    p.add_argument("--seed", type=int, default=7)
    args = p.parse_args()

    res = run(args.assets, args.interval, args.bars, args.horizon, args.folds,
              args.cost_pct, args.threshold, args.risk_pct, args.seed)

    print(f"\n{'=' * 76}")
    print("COMPOUNDING BACKTEST — outcome-filtered vs take-all (one position at a time)")
    print(f"Risk {args.risk_pct:.0%}/trade · threshold P(target)≥{args.threshold} · "
          f"start $10,000 · costs in R")
    print("=" * 76)
    print(f"{'ASSET':9s} {'STRATEGY':12s} {'TRADES':>7s} {'RETURN':>9s} {'MAX DD':>8s} "
          f"{'WIN%':>6s} {'PF':>6s} {'SHARPE':>7s}")
    print("-" * 76)
    agg = {"ta_ret": [], "fl_ret": [], "fl_dd": [], "fl_sh": []}
    for a in res:
        for key, label in [("take_all", "take-all"), ("filtered", "filtered")]:
            s = a[key]
            print(f"{a['asset']:9s} {label:12s} {s['trades']:7d} {s['total_return_pct']:+8.1f}% "
                  f"{s['max_drawdown_pct']:7.1f}% {s['win_rate'] * 100:5.1f}% "
                  f"{s['profit_factor']:6.2f} {s['sharpe']:7.2f}")
        agg["ta_ret"].append(a["take_all"]["total_return_pct"])
        agg["fl_ret"].append(a["filtered"]["total_return_pct"])
        agg["fl_dd"].append(a["filtered"]["max_drawdown_pct"])
        agg["fl_sh"].append(a["filtered"]["sharpe"])
        print("-" * 76)
    print(f"\nAVERAGE filtered: {np.mean(agg['fl_ret']):+.1f}% return · "
          f"{np.mean(agg['fl_dd']):.1f}% max DD · Sharpe {np.mean(agg['fl_sh']):.2f}")
    print(f"AVERAGE take-all: {np.mean(agg['ta_ret']):+.1f}% return")
    print("=" * 76)
    print("Note: crypto/1h, ~2 years/asset. R-expectancy realised as compounding equity.")
    print("Still needs LIVE forward-testing before real money — a backtest is not a track record.")


if __name__ == "__main__":
    main()
