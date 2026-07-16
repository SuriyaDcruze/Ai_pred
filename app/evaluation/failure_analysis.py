"""Failure / self-awareness analysis — WHEN is the edge real, and when does it vanish?

Phase 4's genuinely new idea, done honestly. We do NOT build another opaque "decision
quality" model — that would just duplicate the Outcome Model we already shipped.
Instead we analyse the outcome model's real trades, segmented by market condition, to
answer the question the AI should be able to answer about itself:

    In which conditions does filtering actually pay — and in which is it a coin flip?

The output is explainable self-knowledge: "the edge concentrates in trending,
higher-volume regimes; it's weak in sideways chop" — the kind of statement that lets
the system (and the user) know *when not to trade*. If the edge turns out uniform,
that's an honest finding too: the outcome model already captured it.

    python -m app.evaluation.failure_analysis --assets BTCUSDT ETHUSDT SOLUSDT
"""

from __future__ import annotations

import argparse
import os

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
REPORTS = "reports"


def _segment_stats(r: np.ndarray, cost: float = 0.20) -> dict:
    if len(r) == 0:
        return {"n": 0, "win_rate": float("nan"), "avg_R": float("nan"), "pf": float("nan")}
    net = r - cost
    wins, losses = net[net > 0], net[net < 0]
    pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else float("inf")
    return {"n": int(len(net)), "win_rate": float((net > 0).mean()),
            "avg_R": float(net.mean()), "pf": float(pf)}


def _collect(assets, interval, bars, horizon, n_folds, cost_pct, threshold, seed):
    """Gather per-trade records (condition + filtered flag + realised R) across assets,
    using non-overlapping walk-forward so the numbers are honest."""
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.preprocessing import StandardScaler

    rows = []
    for asset in assets:
        X, y_dir, frame = _prep(asset, interval, bars, horizon, cost_pct)
        atr = frame["atr"].to_numpy(); close = frame["close"].to_numpy()
        high = frame["high"].to_numpy(); low = frame["low"].to_numpy()
        adx = frame["adx"].to_numpy() if "adx" in frame else np.zeros(len(close))
        dp = oof_direction_probs(X, y_dir, horizon=horizon, n_folds=n_folds, seed=seed)
        side = direction_side(dp)
        oc, r_out = outcome_labels(high, low, close, side, atr, horizon=horizon)
        Xoc = build_outcome_features(X, dp)
        mask = (side != 0) & (oc >= 0) & ~np.isnan(r_out)

        atr_pct = (atr / np.where(close == 0, np.nan, close))
        hour = frame.index.hour.to_numpy() if isinstance(frame.index, pd.DatetimeIndex) else np.zeros(len(close))
        conf = dp.max(axis=1)

        n = len(Xoc); dev_end = int(n * 0.85); fold = dev_end // (n_folds + 1)
        for k in range(1, n_folds + 1):
            tr_end = fold * k; vs = tr_end + horizon; ve = min(vs + fold, dev_end)
            if vs >= ve:
                continue
            tr = np.arange(0, tr_end)[mask[:tr_end]]
            va = np.arange(vs, ve, horizon)          # non-overlapping
            va = va[mask[va]]
            if len(tr) < 100 or len(va) < 10:
                continue
            ytr = (oc[tr] == TARGET_FIRST).astype(int)
            if len(set(ytr)) < 2:
                continue
            sc = StandardScaler().fit(Xoc[tr])
            clf = HistGradientBoostingClassifier(max_depth=4, max_iter=200,
                                                 learning_rate=0.05, random_state=seed)
            clf.fit(sc.transform(Xoc[tr]), ytr)
            pt = clf.predict_proba(sc.transform(Xoc[va]))[:, 1]
            for i, idx in enumerate(va):
                rows.append({
                    "asset": asset, "r": r_out[idx], "kept": pt[i] >= threshold,
                    "adx": adx[idx], "atr_pct": atr_pct[idx] * 100,
                    "conf": conf[idx], "hour": hour[idx],
                })
    return pd.DataFrame(rows)


def analyse(df: pd.DataFrame) -> dict:
    """Segment the KEPT (filtered) trades by condition; find strength vs weakness."""
    kept = df[df.kept]
    segments = {}

    def by(name, series, bins, labels):
        buckets = pd.cut(series, bins=bins, labels=labels)
        return {str(lab): _segment_stats(kept.r[buckets == lab].to_numpy())
                for lab in labels}

    segments["trend (ADX)"] = by("adx", kept.adx, [-1, 18, 25, 1e9],
                                 ["weak <18", "moderate 18-25", "strong >25"])
    segments["volatility (ATR%)"] = by("atr_pct", kept.atr_pct, [-1, 1.0, 2.5, 1e9],
                                       ["low <1%", "normal 1-2.5%", "high >2.5%"])
    segments["direction confidence"] = by("conf", kept.conf, [0, 0.45, 0.55, 1.01],
                                          ["low <45%", "mid 45-55%", "high >55%"])
    hour = kept.hour
    sess = pd.Series(np.where((hour >= 13) & (hour < 21), "US 13-21",
                     np.where((hour >= 7) & (hour < 13), "EU 7-13", "Asia 21-7")), index=kept.index)
    segments["session (UTC)"] = {s: _segment_stats(kept.r[sess == s].to_numpy())
                                 for s in ["Asia 21-7", "EU 7-13", "US 13-21"]}
    return {"overall_kept": _segment_stats(kept.r.to_numpy()),
            "overall_all": _segment_stats(df.r.to_numpy()), "segments": segments}


def write_reports(res: dict, meta: dict) -> None:
    os.makedirs(REPORTS, exist_ok=True)
    ok, al = res["overall_kept"], res["overall_all"]
    lines = [
        "# Self-Awareness — where the outcome-model edge is real (and where it isn't)",
        "",
        f"Assets {meta['assets']} · non-overlapping walk-forward · threshold {meta['threshold']}",
        "",
        f"**Overall:** take-all {al['avg_R']:+.3f}R (n={al['n']}) → "
        f"filtered {ok['avg_R']:+.3f}R, {ok['win_rate']:.0%} win, PF {ok['pf']:.2f} (n={ok['n']}).",
        "",
        "Filtered trades broken down by condition. A segment where avg R stays clearly "
        "positive is where the edge is real; near-zero means the model should be more "
        "cautious there — *this is the AI's self-knowledge of when not to trade.*",
        "",
    ]
    weak, strong = [], []
    for seg, buckets in res["segments"].items():
        lines += [f"## By {seg}", "", "| Bucket | Trades | Win rate | Avg R |", "|---|---|---|---|"]
        for b, s in buckets.items():
            if s["n"] == 0:
                lines.append(f"| {b} | 0 | — | — |"); continue
            lines.append(f"| {b} | {s['n']} | {s['win_rate']:.0%} | {s['avg_R']:+.3f} |")
            if s["n"] >= 20:
                (strong if s["avg_R"] > 0.10 else weak if s["avg_R"] < 0.03 else []).append(
                    f"{seg}: {b} ({s['avg_R']:+.3f}R, n={s['n']})")
        lines.append("")
    lines += ["## Self-knowledge summary", ""]
    lines += [f"- 🟢 **Edge strong:** {s}" for s in strong] or ["- (no clearly-strong segment above threshold sample)"]
    lines += [f"- 🔴 **Edge weak / avoid:** {w}" for w in weak] or ["- (no clearly-weak segment)"]
    with open(os.path.join(REPORTS, "self_awareness.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    logger.info("Wrote reports/self_awareness.md")
    return strong, weak


def main() -> None:
    p = argparse.ArgumentParser(description="Failure / self-awareness analysis")
    p.add_argument("--assets", nargs="+", default=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    p.add_argument("--interval", default="1h")
    p.add_argument("--bars", type=int, default=20_000)
    p.add_argument("--horizon", type=int, default=12)
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--cost-pct", dest="cost_pct", type=float, default=0.0012)
    p.add_argument("--threshold", type=float, default=0.60)
    p.add_argument("--seed", type=int, default=7)
    args = p.parse_args()

    df = _collect(args.assets, args.interval, args.bars, args.horizon, args.folds,
                  args.cost_pct, args.threshold, args.seed)
    res = analyse(df)
    strong, weak = write_reports(res, vars(args))

    ok, al = res["overall_kept"], res["overall_all"]
    print(f"\n{'=' * 64}")
    print("SELF-AWARENESS — where does the outcome-model edge live?")
    print("=" * 64)
    print(f"Overall: take-all {al['avg_R']:+.3f}R  →  filtered {ok['avg_R']:+.3f}R "
          f"({ok['win_rate']:.0%} win, PF {ok['pf']:.2f}, n={ok['n']})")
    for seg, buckets in res["segments"].items():
        print(f"\nBy {seg}:")
        for b, s in buckets.items():
            if s["n"] == 0:
                continue
            flag = "  🟢" if s["avg_R"] > 0.10 and s["n"] >= 20 else ("  🔴" if s["avg_R"] < 0.03 and s["n"] >= 20 else "")
            print(f"  {b:18s} n={s['n']:4d}  win {s['win_rate']:.0%}  avg {s['avg_R']:+.3f}R{flag}")
    print(f"\n{'-' * 64}")
    print("🟢 = edge strong here · 🔴 = weak, the AI should be cautious")
    print("Report: reports/self_awareness.md")
    print("=" * 64)


if __name__ == "__main__":
    main()
