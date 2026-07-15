"""Meta-labelling — a second model that learns **which of our signals actually win**.

This is the strongest honest move available to us, and it is worth understanding
why, because it is not obvious.

The price model is stuck near 51.6% directional accuracy. Squeezing that to 54%
is hard and may be impossible. But *we do not have to*. The meta-model asks a
different, much easier question:

    Not "which way will price go?"   (nearly unlearnable)
    But "given that our model just said BUY, is THIS one of the setups where it
    tends to be right?"   (genuinely learnable)

It never predicts price. It sits on top of the price model and **vetoes** the
setups that historically lose. It converts a mediocre predictor into a selective
one — which is exactly how López de Prado frames it in *Advances in Financial
Machine Learning*, and why the technique is respected rather than hyped.

The training data is your own forward-tested Track Record: every AI pick, the
market conditions when it was made, and whether it actually won. No lookahead is
possible, because the outcome was scored against real future candles that had not
happened when the call was logged.

**It needs data before it can help.** Below ``MIN_CALLS`` resolved calls, this
module refuses to train rather than produce a confident-looking model fitted to
thirty examples. That refusal is the feature.

    python -m app.training.meta            # train (if enough data)
    python -m app.training.meta --status   # how close are we?
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.utils.logging import get_logger

logger = get_logger(__name__)

META_PATH = os.path.join("artifacts", "meta_filter.pkl")

# Below this many RESOLVED calls, a meta-model is fitting noise and nothing else.
# 200 is already optimistic; 500+ is where it starts to mean something.
MIN_CALLS = 200

# The market conditions we record at the moment of each call. Deliberately a small,
# non-redundant set — with a few hundred samples, twenty features would overfit
# instantly.
META_FEATURES = [
    "confidence",        # how sure the price model was
    "rsi",
    "adx",               # is there a trend at all?
    "atr_pct",           # volatility as % of price
    "ema_gap_pct",       # ema_50 vs ema_200 — trend direction & strength
    "macd_hist",
    "volume_delta",
    "cdl_signal",        # net candlestick pressure
    "with_trend",        # 1 if the call agrees with the EMA trend, else 0
    "hour",              # time of day — liquidity regimes are real
]


@dataclass
class MetaStatus:
    resolved: int
    needed: int
    wins: int
    losses: int

    @property
    def ready(self) -> bool:
        return self.resolved >= self.needed

    @property
    def win_rate(self) -> float:
        return self.wins / self.resolved if self.resolved else 0.0

    def report(self) -> str:
        pct = min(100, int(100 * self.resolved / self.needed))
        bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
        lines = [
            f"  Resolved calls : {self.resolved} / {self.needed}   [{bar}] {pct}%",
            f"  Won / lost     : {self.wins} / {self.losses}  ({self.win_rate:.1%} win rate)",
        ]
        if self.ready:
            lines.append("  Status         : READY — enough data to train the meta-model.")
        else:
            short = self.needed - self.resolved
            lines.append(f"  Status         : NOT READY — {short} more resolved calls needed.")
            lines.append("                   Leave auto-log ON and let the record build.")
        return "\n".join(lines)


def status(db_path: str | None = None) -> MetaStatus:
    from app.tracking.tracker import CallStore

    store = CallStore(db_path) if db_path else CallStore()
    calls = [c for c in store.all() if c.source == "ai" and c.status in ("WIN", "LOSS")]
    wins = sum(1 for c in calls if c.status == "WIN")
    return MetaStatus(resolved=len(calls), needed=MIN_CALLS, wins=wins, losses=len(calls) - wins)


def _conditions_at(features: pd.DataFrame, side: str, confidence: float) -> dict | None:
    """Snapshot the market conditions on the bar a call was made."""
    if features.empty:
        return None
    row = features.iloc[-1]

    def f(name: str, default: float = 0.0) -> float:
        v = row.get(name, default)
        try:
            v = float(v)
        except (TypeError, ValueError):
            return default
        return default if np.isnan(v) else v

    close = f("close", 1.0) or 1.0
    ema50, ema200 = f("ema_50", close), f("ema_200", close)
    up = ema50 > ema200
    return {
        "confidence": confidence,
        "rsi": f("rsi", 50.0),
        "adx": f("adx"),
        "atr_pct": f("atr") / close * 100,
        "ema_gap_pct": (ema50 - ema200) / close * 100,
        "macd_hist": f("macd_hist"),
        "volume_delta": f("volume_delta"),
        "cdl_signal": f("cdl_signal"),
        "with_trend": float((side == "BUY") == up),
        "hour": float(features.index[-1].hour),
    }


def build_dataset(db_path: str | None = None) -> pd.DataFrame:
    """Reconstruct the market conditions for every resolved AI call.

    Refetches the candles around each call and rebuilds the features as they were
    at that moment. Slow, but honest — and it means we don't have to have stored
    the features up front.
    """
    import asyncio

    from app.data.schemas import candles_to_frame
    from app.features.engineering import FeatureBuilder
    from app.stream.binance import BinanceClient
    from app.stream.yahoo import YahooClient
    from app.tracking.tracker import CallStore

    store = CallStore(db_path) if db_path else CallStore()
    calls = [c for c in store.all() if c.source == "ai" and c.status in ("WIN", "LOSS")]
    if not calls:
        return pd.DataFrame()

    fb = FeatureBuilder()
    rows: list[dict] = []

    async def gather() -> None:
        cache: dict[tuple[str, str], pd.DataFrame] = {}
        for call in calls:
            key = (call.symbol, call.timeframe)
            if key not in cache:
                client = YahooClient() if YahooClient.is_stock(call.symbol) else BinanceClient()
                try:
                    candles = await client.fetch_history(call.symbol, call.timeframe, total=1000)
                    cache[key] = candles_to_frame(candles)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("could not refetch %s %s: %s", *key, exc)
                    cache[key] = pd.DataFrame()
            df = cache[key]
            if df.empty:
                continue
            # candles strictly up to (and including) the bar the call was pinned to
            upto = df[df.index <= pd.Timestamp(call.clicked_time, unit="s", tz="UTC")]
            if len(upto) < 250:
                continue
            feats = fb.build_frame(upto)
            cond = _conditions_at(feats, call.side, confidence=0.0)
            if cond is None:
                continue
            cond["won"] = 1 if call.status == "WIN" else 0
            cond["r_multiple"] = call.r_multiple
            rows.append(cond)

    asyncio.run(gather())
    return pd.DataFrame(rows)


def train(db_path: str | None = None, out: str = META_PATH) -> dict:
    """Fit the veto model. Refuses on thin data rather than pretending."""
    st = status(db_path)
    if not st.ready:
        logger.error("Not enough resolved calls to train a meta-model.\n%s", st.report())
        raise SystemExit(
            f"\nNeed {MIN_CALLS} resolved AI calls, have {st.resolved}.\n"
            "A model fitted to this little data would look confident and be worthless.\n"
            "Leave auto-log ON and come back when the Track Record has filled up.\n"
        )

    import joblib
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import TimeSeriesSplit

    logger.info("Rebuilding market conditions for %d resolved calls…", st.resolved)
    df = build_dataset(db_path)
    if len(df) < MIN_CALLS:
        raise SystemExit(f"Only reconstructed {len(df)} usable rows. Aborting.")

    X, y = df[META_FEATURES].to_numpy(), df["won"].to_numpy()

    # Time-ordered CV. A random split would let the model peek at the future,
    # which is the single most common way meta-models fool their authors.
    aucs = []
    for tr, te in TimeSeriesSplit(n_splits=4).split(X):
        m = GradientBoostingClassifier(n_estimators=120, max_depth=3, learning_rate=0.05)
        m.fit(X[tr], y[tr])
        if len(set(y[te])) > 1:
            aucs.append(roc_auc_score(y[te], m.predict_proba(X[te])[:, 1]))

    auc = float(np.mean(aucs)) if aucs else 0.5
    logger.info("Out-of-sample AUC: %.3f  (0.50 = worthless, >0.55 = a real filter)", auc)

    model = GradientBoostingClassifier(n_estimators=120, max_depth=3, learning_rate=0.05)
    model.fit(X, y)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    joblib.dump({"model": model, "features": META_FEATURES, "auc": auc, "n": len(df)}, out)
    logger.info("Saved meta-filter to %s", out)

    if auc < 0.55:
        logger.warning(
            "AUC %.3f is not meaningfully better than a coin flip. The honest reading is "
            "that the winning setups are NOT distinguishable from the losing ones with these "
            "features. Do not deploy this as a filter yet.", auc
        )

    return {"auc": auc, "n": len(df), "baseline_win_rate": float(y.mean())}


def main() -> None:
    p = argparse.ArgumentParser(description="Meta-model: learn which signals actually win")
    p.add_argument("--status", action="store_true", help="Show progress toward having enough data")
    p.add_argument("--db", default=None)
    args = p.parse_args()

    st = status(args.db)
    print("\n📓 Meta-model readiness\n")
    print(st.report())
    print()
    if args.status:
        return
    if st.ready:
        result = train(args.db)
        print(f"\nTrained on {result['n']} calls. Out-of-sample AUC: {result['auc']:.3f}")
        print(f"Baseline win rate: {result['baseline_win_rate']:.1%}")


if __name__ == "__main__":
    main()
