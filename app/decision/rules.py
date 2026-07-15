"""**My Rules** — your personal trading checklist, enforced by the machine.

Every trader has rules. Almost nobody follows them, because in the moment the
chart is exciting and the rules are boring. So we take the decision away from
your mood: before any trade, the AI scores the setup against *your* checklist and
tells you plainly whether it passes.

This is a **discipline** feature, not an accuracy feature — and the difference
matters. It will not make the model smarter. What it does is stop you taking the
trades you already know you shouldn't: the 30%-confidence ones, the ones against
the trend, the fifth trade of a losing day. In practice that is worth more than a
percentage point of model accuracy, because the model's edge is thin and being
undisciplined is the fastest way to give it away.

Rules are stored in the same SQLite DB as the track record, so they survive
restarts and apply even when the browser is closed (the auto-logger uses them).
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

import pandas as pd

from app.data.schemas import Side, Signal
from app.utils.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_PATH = os.path.join("data", "calls.db")

PASS, FAIL, SKIP = "pass", "fail", "n/a"


@dataclass
class RuleResult:
    id: str
    label: str
    why: str
    status: str          # pass | fail | n/a
    detail: str          # plain-English explanation of *this* evaluation
    enabled: bool = True


@dataclass
class RuleVerdict:
    """The checklist's answer: does this setup respect your own rules?"""

    passed: int
    failed: int
    has_trade: bool = True          # False when the AI is saying WAIT
    results: list[RuleResult] = field(default_factory=list)

    @property
    def obeys_rules(self) -> bool:
        return self.failed == 0

    @property
    def verdict(self) -> str:
        if not any(r.enabled for r in self.results):
            return "No rules switched on"
        if not self.has_trade:
            return "No trade to check right now"
        if self.failed == 0:
            return "This trade OBEYS your rules"
        if self.failed == 1:
            return "This trade BREAKS 1 of your rules"
        return f"This trade BREAKS {self.failed} of your rules"

    @property
    def advice(self) -> str:
        if not any(r.enabled for r in self.results):
            return "Turn on a few rules below — they are your seatbelt."
        if not self.has_trade:
            return "The AI is saying WAIT, so there's nothing to score yet. Sitting out IS a position."
        if self.failed == 0:
            return "Your checklist is happy. Size it small and follow the plan."
        return "Your own rules say SKIP this one. Wait for a setup that ticks every box."


# --------------------------------------------------------------------------- #
# The rule catalogue. Each rule is a small, auditable function — you can read
# exactly why it passed or failed. `value` is the number the user can tweak.
# --------------------------------------------------------------------------- #

@dataclass
class Rule:
    id: str
    label: str                       # what the user sees
    why: str                         # why this rule protects them
    default_on: bool
    default_value: float | None      # None = no tweakable number
    unit: str
    check: Callable[[dict[str, Any], float | None], tuple[str, str]]


def _r_confidence(ctx: dict, value: float | None) -> tuple[str, str]:
    conf = ctx["confidence"] * 100
    need = value or 60.0
    if ctx["side"] == Side.WAIT:
        return SKIP, "No trade on the table."
    if conf >= need:
        return PASS, f"AI is {conf:.0f}% sure (you need {need:.0f}%)."
    return FAIL, f"AI is only {conf:.0f}% sure — below your {need:.0f}% minimum."


def _r_with_trend(ctx: dict, value: float | None) -> tuple[str, str]:
    row, side = ctx["row"], ctx["side"]
    if side == Side.WAIT:
        return SKIP, "No trade on the table."
    fast, slow = row.get("ema_50"), row.get("ema_200")
    if fast is None or slow is None or pd.isna(fast) or pd.isna(slow):
        return SKIP, "Not enough history to know the trend."
    up = fast > slow
    if (side == Side.BUY and up) or (side == Side.SELL and not up):
        return PASS, f"Trend is {'UP' if up else 'DOWN'} and you're going {side.value}. Same direction."
    return FAIL, f"Trend is {'UP' if up else 'DOWN'} but you'd be going {side.value}. That's swimming upstream."


def _r_risk_reward(ctx: dict, value: float | None) -> tuple[str, str]:
    risk = ctx.get("risk")
    need = value or 1.5
    if ctx["side"] == Side.WAIT or risk is None:
        return SKIP, "No trade plan to measure."
    rr = risk.risk_reward
    if rr >= need:
        return PASS, f"You'd win {rr:.1f}x what you risk (you need {need:.1f}x)."
    return FAIL, f"You'd only win {rr:.1f}x what you risk — under your {need:.1f}x floor."


def _r_not_overbought(ctx: dict, value: float | None) -> tuple[str, str]:
    row, side = ctx["row"], ctx["side"]
    rsi = row.get("rsi")
    hi = value or 70.0
    lo = 100 - hi
    if side == Side.WAIT or rsi is None or pd.isna(rsi):
        return SKIP, "No trade on the table."
    if side == Side.BUY and rsi >= hi:
        return FAIL, f"RSI is {rsi:.0f} — already overbought. You'd be buying the top."
    if side == Side.SELL and rsi <= lo:
        return FAIL, f"RSI is {rsi:.0f} — already oversold. You'd be selling the bottom."
    return PASS, f"RSI is {rsi:.0f} — not stretched. Room to move."


def _r_candle_confirm(ctx: dict, value: float | None) -> tuple[str, str]:
    if ctx["side"] == Side.WAIT:
        return SKIP, "No trade on the table."
    pats = ctx.get("patterns") or []
    if pats:
        return PASS, f"Candle confirms it: {', '.join(pats[:2])}."
    return FAIL, "No candlestick pattern backing this up. The candles aren't agreeing yet."


def _r_news_agrees(ctx: dict, value: float | None) -> tuple[str, str]:
    side, news = ctx["side"], ctx.get("news_score")
    if side == Side.WAIT:
        return SKIP, "No trade on the table."
    if news is None:
        return SKIP, "No news available for this market."
    strong = value if value is not None else 0.35
    if side == Side.BUY and news <= -strong:
        return FAIL, f"Headlines are clearly bearish ({news:+.2f}) but you'd be buying."
    if side == Side.SELL and news >= strong:
        return FAIL, f"Headlines are clearly bullish ({news:+.2f}) but you'd be selling."
    return PASS, f"News sentiment ({news:+.2f}) isn't fighting your {side.value}."


def _r_max_trades(ctx: dict, value: float | None) -> tuple[str, str]:
    cap = int(value or 3)
    taken = ctx.get("trades_today", 0)
    if ctx["side"] == Side.WAIT:
        return SKIP, "No trade on the table."
    if taken < cap:
        return PASS, f"{taken} of {cap} trades used today. You have room."
    return FAIL, f"You've already taken {taken} trades today (your cap is {cap}). Stop. Walk away."


def _r_calm_market(ctx: dict, value: float | None) -> tuple[str, str]:
    row = ctx["row"]
    atr, close = row.get("atr"), row.get("close")
    cap = value or 3.0        # ATR as % of price
    if ctx["side"] == Side.WAIT or atr is None or close in (None, 0) or pd.isna(atr):
        return SKIP, "No trade on the table."
    pct = (atr / close) * 100
    if pct <= cap:
        return PASS, f"Market is calm enough (candles swing ~{pct:.1f}%, your cap is {cap:.1f}%)."
    return FAIL, f"Market is wild right now (candles swing ~{pct:.1f}%). Stops get hit by noise."


RULES: list[Rule] = [
    Rule("min_confidence", "Only trade when the AI is confident",
         "A coin-flip signal is not a signal. This is your single strongest filter.",
         True, 60.0, "%", _r_confidence),
    Rule("with_trend", "Never trade against the trend",
         "The trend is the one edge that's free. Fighting it is the most common way beginners lose.",
         True, None, "", _r_with_trend),
    Rule("min_rr", "Only take trades that pay more than they risk",
         "You can be right less than half the time and still make money — if your winners are bigger.",
         True, 1.5, "x", _r_risk_reward),
    Rule("not_stretched", "Don't buy the top / sell the bottom",
         "Chasing a move that already happened is how you end up as someone else's exit.",
         True, 70.0, "RSI", _r_not_overbought),
    Rule("candle_confirm", "Wait for the candles to agree",
         "Price is the final word. Make it confirm the idea before you commit money.",
         False, None, "", _r_candle_confirm),
    Rule("news_agrees", "Don't trade into bad news",
         "News is the one thing that moves price before the chart shows it.",
         True, 0.35, "score", _r_news_agrees),
    Rule("max_trades", "Cap how many trades you take per day",
         "Overtrading — not bad picks — is what empties most accounts. Revenge trading is real.",
         True, 3.0, "trades", _r_max_trades),
    Rule("calm_market", "Skip wildly volatile markets",
         "In chaos your stop gets hit by random noise, even when your direction was right.",
         False, 3.0, "% ATR", _r_calm_market),
]

_BY_ID = {r.id: r for r in RULES}


# --------------------------------------------------------------------------- #
# Storage — same SQLite file as the track record.
# --------------------------------------------------------------------------- #

class RuleStore:
    """Persists which rules are on and their thresholds."""

    def __init__(self, path: str = _DEFAULT_PATH):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with self._conn() as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS rules (
                       id      TEXT PRIMARY KEY,
                       enabled INTEGER NOT NULL,
                       value   REAL
                   )"""
            )
            for r in RULES:                      # seed defaults, never overwrite
                c.execute(
                    "INSERT OR IGNORE INTO rules(id, enabled, value) VALUES (?,?,?)",
                    (r.id, int(r.default_on), r.default_value),
                )

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def settings(self) -> dict[str, tuple[bool, float | None]]:
        with self._conn() as c:
            rows = c.execute("SELECT id, enabled, value FROM rules").fetchall()
        return {r["id"]: (bool(r["enabled"]), r["value"]) for r in rows}

    def set(self, rule_id: str, enabled: bool | None = None, value: float | None = None) -> None:
        if rule_id not in _BY_ID:
            raise KeyError(f"unknown rule {rule_id!r}")
        cur = self.settings().get(rule_id, (True, _BY_ID[rule_id].default_value))
        on = cur[0] if enabled is None else enabled
        val = cur[1] if value is None else value
        with self._conn() as c:
            c.execute("UPDATE rules SET enabled=?, value=? WHERE id=?", (int(on), val, rule_id))

    def reset(self) -> None:
        with self._conn() as c:
            for r in RULES:
                c.execute(
                    "UPDATE rules SET enabled=?, value=? WHERE id=?",
                    (int(r.default_on), r.default_value, r.id),
                )

    def catalogue(self) -> list[dict[str, Any]]:
        """Every rule + its current state, for the settings UI."""
        cur = self.settings()
        out = []
        for r in RULES:
            on, val = cur.get(r.id, (r.default_on, r.default_value))
            out.append(
                {
                    "id": r.id, "label": r.label, "why": r.why,
                    "enabled": on, "value": val, "unit": r.unit,
                    "tweakable": r.default_value is not None,
                }
            )
        return out


def trades_today(calls: list) -> int:
    """How many trades *you* logged today (AI auto-picks don't count against you)."""
    today = datetime.now(timezone.utc).date()
    n = 0
    for c in calls:
        if getattr(c, "source", "") == "ai":
            continue
        ts = getattr(c, "created_at", None)
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts)
            except ValueError:
                continue
        if ts and ts.date() == today:
            n += 1
    return n


def check_rules(
    signal: Signal,
    features: pd.DataFrame,
    store: RuleStore,
    news_score: float | None = None,
    trades_today_count: int = 0,
) -> RuleVerdict:
    """Score a signal against the user's checklist. Never raises."""
    row = features.iloc[-1]
    ctx: dict[str, Any] = {
        "row": row,
        "side": signal.decision,
        "confidence": signal.confidence,
        "risk": signal.risk,
        "patterns": signal.patterns,
        "news_score": news_score,
        "trades_today": trades_today_count,
    }
    cur = store.settings()
    results: list[RuleResult] = []
    passed = failed = 0

    for r in RULES:
        on, val = cur.get(r.id, (r.default_on, r.default_value))
        if not on:
            results.append(RuleResult(r.id, r.label, r.why, SKIP, "Rule is switched off.", False))
            continue
        try:
            status, detail = r.check(ctx, val)
        except Exception as exc:  # noqa: BLE001 - a broken rule must never break a signal
            logger.warning("rule %s failed to evaluate: %s", r.id, exc)
            status, detail = SKIP, "Could not check this rule right now."
        if status == PASS:
            passed += 1
        elif status == FAIL:
            failed += 1
        results.append(RuleResult(r.id, r.label, r.why, status, detail, True))

    return RuleVerdict(
        passed=passed,
        failed=failed,
        has_trade=signal.decision != Side.WAIT,
        results=results,
    )
