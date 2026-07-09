"""Beginner-friendly trading assistant.

Answers plain-language questions and, when you tap a price on the chart, gives a
simple verdict — *is this a good place to trade or not?* — with the plan spelled
out for someone who has never traded before. Every price the model states maps
to a coloured line drawn on the chart (green = get in / take profit, red = safety
exit). Deterministic and explainable — no external LLM needed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

from app.data.schemas import Side, Signal
from app.risk.manager import RiskManager

GREEN = "green"
RED = "red"
YELLOW = "yellow"


@dataclass
class ChatMarker:
    price: float
    color: str
    label: str
    style: str = "line"  # "line" | "marker"


@dataclass
class ChatReply:
    reply: str
    markers: list[ChatMarker] = field(default_factory=list)
    decision: str | None = None
    confidence: float | None = None
    side: str | None = None          # BUY / SELL suggested for the tapped point
    entry: float | None = None       # the exact price the answer is about
    stop: float | None = None
    tp1: float | None = None
    tp2: float | None = None


class TradingAssistant:
    def __init__(self, risk_manager: RiskManager | None = None):
        self.risk = risk_manager or RiskManager()

    # ------------------------------------------------------------------ #
    def respond(
        self,
        signal: Signal,
        ohlcv: pd.DataFrame,
        features: pd.DataFrame,
        message: str,
        hypo_price: float | None = None,
    ) -> ChatReply:
        text = (message or "").lower().strip()
        row = features.iloc[-1]
        price = float(ohlcv["close"].iloc[-1])
        atr = float(row.get("atr") or price * 0.01)

        # A chart tap (has a price) is always a "trade at this price" question.
        if hypo_price is not None:
            return self._what_if(signal, row, price, atr, hypo_price, text)

        # Someone asking about a pattern by name -> teach it + say if it's on the chart now.
        from app.features.candlesticks import lookup_pattern

        lesson = lookup_pattern(text)
        if lesson:
            return self._pattern_lookup(lesson, row)

        wants_what_if = (
            bool(re.search(r"\bhere\b", text))
            or "this level" in text
            or "this price" in text
            or "this spot" in text
            or text.startswith("if i")
            or "what if" in text
        )
        if wants_what_if:
            return self._what_if(signal, row, price, atr, price, text)

        if "safe" in text or "risky" in text or "should i risk" in text:
            return self._safety(signal, price, atr)
        if any(w in text for w in ("why", "wait", "no trade")) and signal.decision is Side.WAIT:
            return self._why_wait(signal)
        if any(w in text for w in ("stop", "stoploss", "sl")):
            return self._stop(signal, price, atr)
        if any(w in text for w in ("risk", "size", "position", "how much")):
            return self._risk(signal)
        if any(w in text for w in ("target", "profit", "tp", "take profit")):
            return self._targets(signal)
        if any(w in text for w in ("where", "enter", "entry", "trade")):
            return self._where(signal, row, price, atr)
        if any(w in text for w in ("buy", "long", "sell", "short", "should i", "direction", "bias")):
            return self._direction(signal, row, text)
        if any(w in text for w in ("hi", "hello", "help", "hey")):
            return self._help()
        return self._summary(signal, row, price)

    # ------------------------------------------------------------------ #
    # The big one: tap a price -> "is this a good place to trade?"
    # ------------------------------------------------------------------ #

    def _what_if(self, signal, row, price, atr, entry, text) -> ChatReply:
        if any(w in text for w in ("sell", "short")):
            side = Side.SELL
        elif any(w in text for w in ("buy", "long")):
            side = Side.BUY
        else:
            # No explicit side -> pick from WHERE they tapped: buy low, sell high.
            side = self._location_side(entry, row)

        plan = self.risk.build_plan(side, entry, atr)
        if plan is None:
            return ChatReply("Hmm, I couldn't work out a trade at that price. Try tapping somewhere else.", [])

        long = side is Side.BUY
        arrow = "UP ⬆" if long else "DOWN ⬇"
        action = "BUY" if long else "SELL"
        icon, verdict, quality = self._place_verdict(side, entry, row)
        aligned = (side.value == signal.decision.value) or (
            signal.decision is Side.WAIT and side is self._bias(row)
        )

        risk_amt = round(abs(entry - plan.stop_loss) * plan.position_size)
        reward_amt = round(risk_amt * plan.risk_reward)

        # Headline verdict first — the thing a beginner needs.
        if quality == "good":
            headline = f"✅ GOOD SPOT to {action} here."
        elif quality == "bad":
            headline = f"❌ NOT a good spot to {action} here — I'd wait for a better one."
        else:
            headline = f"🟡 OK-ish spot to {action}, but nothing special."

        lines = [
            f"{headline}",
            "",
            f"You'd be betting price goes {arrow}.",
            "",
            f"📍 Why: {verdict}",
            "",
            "📋 THE PLAN (simple):",
            f"• Get in at: {self._fmt(entry)}",
            f"• 🛑 Safety exit (stop): {self._fmt(plan.stop_loss)} — if price hits this, the idea was wrong. "
            f"Get out. You lose about ${risk_amt} (that's 1% of your money).",
            f"• 🎯 Take profit at: {self._fmt(plan.take_profit_1)} (first) or {self._fmt(plan.take_profit_2)} (bigger).",
            f"• ⚖️ You risk ${risk_amt} to make about ${reward_amt} — a {plan.risk_reward}-to-1 trade"
            + (" 👍 (good ratio)." if plan.risk_reward >= 2 else " (a bit low)."),
            "",
            "🛡️ Is it safe? " + self._safety_line(risk_amt),
        ]
        if aligned:
            lines += ["", "✅ Bonus: this lines up with what the market is doing right now."]
        else:
            lines += ["", "⚠️ Heads up: this goes AGAINST the current market direction — that's riskier."]

        markers = [
            ChatMarker(entry, GREEN if long else RED, f"{action} @ {self._fmt(entry)}", "marker"),
            ChatMarker(plan.stop_loss, RED, f"Stop {self._fmt(plan.stop_loss)}"),
            ChatMarker(plan.take_profit_1, GREEN, f"TP1 {self._fmt(plan.take_profit_1)}"),
            ChatMarker(plan.take_profit_2, GREEN, f"TP2 {self._fmt(plan.take_profit_2)}"),
        ]
        return ChatReply(
            "\n".join(lines), markers, signal.decision.value, signal.confidence,
            side=side.value, entry=round(entry, 2),
            stop=plan.stop_loss, tp1=plan.take_profit_1, tp2=plan.take_profit_2,
        )

    # ------------------------------------------------------------------ #

    def _where(self, signal, row, price, atr) -> ChatReply:
        if signal.decision in (Side.BUY, Side.SELL) and signal.risk:
            r = signal.risk
            action = "BUY ⬆" if signal.decision is Side.BUY else "SELL ⬇"
            lines = [
                f"✅ There's a trade right now: {action} ({int(signal.confidence*100)}% confident).",
                "",
                "📋 THE PLAN:",
                f"• Get in between: {self._fmt(r.entry_low)} and {self._fmt(r.entry_high)} (green zone)",
                f"• 🛑 Safety exit (stop): {self._fmt(r.stop_loss)} (red line)",
                f"• 🎯 Take profit: {self._fmt(r.take_profit_1)} then {self._fmt(r.take_profit_2)}",
                f"• ⚖️ Risk-reward: {r.risk_reward}-to-1 · size {r.position_size} units (risks 1% of your money)",
                "",
                "💡 Only get in once price is actually in the green zone. Don't chase it.",
            ]
            return ChatReply("\n".join(lines), self._plan_markers(signal), signal.decision.value, signal.confidence)
        return self._exact_plan_while_waiting(signal, row, price, atr)

    def _exact_plan_while_waiting(self, signal, row, price, atr) -> ChatReply:
        bias = self._bias(row)
        long = bias is Side.BUY
        sup = float(row.get("donchian_lower") or price)
        res = float(row.get("donchian_upper") or price)

        if long:
            entry = round(max(sup, price - 0.5 * atr), 2)
            when = (f"wait for price to dip to about {self._fmt(entry)} and then a candle to close back "
                    f"up above it (a bounce off the {self._fmt(sup)} floor).")
        else:
            entry = round(min(res, price + 0.5 * atr), 2)
            when = (f"wait for price to rise to about {self._fmt(entry)} and then a candle to close back "
                    f"down below it (a rejection at the {self._fmt(res)} ceiling).")

        plan = self.risk.build_plan(bias, entry, atr)
        markers = []
        plan_line = ""
        if plan is not None:
            markers = [
                ChatMarker(entry, GREEN if long else RED, f"Watch {self._fmt(entry)}", "marker"),
                ChatMarker(plan.stop_loss, RED, f"Stop {self._fmt(plan.stop_loss)}"),
                ChatMarker(plan.take_profit_1, GREEN, f"TP1 {self._fmt(plan.take_profit_1)}"),
                ChatMarker(plan.take_profit_2, GREEN, f"TP2 {self._fmt(plan.take_profit_2)}"),
            ]
            plan_line = (f"• If it happens: stop {self._fmt(plan.stop_loss)}, "
                         f"targets {self._fmt(plan.take_profit_1)}/{self._fmt(plan.take_profit_2)}, "
                         f"{plan.risk_reward}-to-1.")

        lines = [
            f"🔴 No good trade right now — better to WAIT. (I'm only {int(signal.confidence*100)}% sure.)",
            "",
            "My #1 rule: don't trade unless it's clear. No trade = no risk. 🛡️",
            "",
            f"👀 WHAT I'M WATCHING (leaning {'BUY ⬆' if long else 'SELL ⬇'}):",
            f"• Best spot: {self._fmt(entry)} (the marked line on the chart)",
            f"• When to jump in: {when}",
            plan_line,
            "",
            "💡 Tap any price on the chart and I'll tell you if it's a good spot or not.",
        ]
        return ChatReply("\n".join(lines), markers, signal.decision.value, signal.confidence)

    def _direction(self, signal, row, text) -> ChatReply:
        if signal.decision in (Side.BUY, Side.SELL):
            action = "BUY ⬆ (bet it goes up)" if signal.decision is Side.BUY else "SELL ⬇ (bet it goes down)"
            lines = [
                f"📢 My call: {action}",
                f"How sure am I? {int(signal.confidence*100)}%.",
                "",
                "Why: " + "; ".join(signal.reasons[:3]),
            ]
            return ChatReply("\n".join(lines), self._plan_markers(signal), signal.decision.value, signal.confidence)
        lean = "up ⬆" if self._bias(row) is Side.BUY else "down ⬇"
        lines = [
            "🟡 Right now: WAIT — neither a clear buy nor sell.",
            f"The market's leaning slightly {lean}, but it's not strong enough to trade "
            f"(only {int(signal.confidence*100)}% sure).",
            "",
            "I'd rather skip it than take a weak trade. Ask me 'why wait?' for details.",
        ]
        return ChatReply("\n".join(lines), [], signal.decision.value, signal.confidence)

    def _why_wait(self, signal) -> ChatReply:
        blockers = [r for r in signal.risks if "Unconfirmed" in r or "below" in r or "Heuristic" in r]
        pretty = self._plainify_blockers(blockers)
        lines = [
            f"🔴 I'm saying WAIT (only {int(signal.confidence*100)}% sure). Here's why, simply:",
            "",
            *[f"• {b}" for b in pretty],
            "",
            "Basically: not enough signs agree yet. I only trade when the odds are clearly on our side. "
            "Missing a trade is fine — losing money on a bad one isn't. 🛡️",
        ]
        return ChatReply("\n".join(lines), [], signal.decision.value, signal.confidence)

    def _safety(self, signal, price, atr) -> ChatReply:
        if signal.risk:
            r = signal.risk
            risk_amt = round(abs(r.entry_high - r.stop_loss) * r.position_size) or round(price * 0.01 * r.position_size)
            lines = [
                "🛡️ How safe is this?",
                "",
                f"This trade only risks about 1% of your money (${risk_amt}) if it goes wrong — that's a "
                f"controlled, sensible risk. The safety-exit (stop) at {self._fmt(r.stop_loss)} caps your loss.",
                "",
                "BUT — no trade is ever 100% safe. You can still lose. Only take it if you're okay losing "
                f"${risk_amt}. Always keep the stop in place. 📏",
            ]
        else:
            lines = [
                "🛡️ Safest move right now: DON'T trade.",
                "",
                f"I'm at WAIT ({int(signal.confidence*100)}% sure) because the setup isn't strong. "
                "No trade means no risk. Wait for a clear signal.",
                "",
                "Golden rule: never risk money you can't afford to lose, and always use a stop loss. 📏",
            ]
        return ChatReply("\n".join(lines), self._plan_markers(signal), signal.decision.value, signal.confidence)

    def _stop(self, signal, price, atr) -> ChatReply:
        if signal.risk:
            r = signal.risk
            return ChatReply(
                f"🛑 Your safety exit (stop) is {self._fmt(r.stop_loss)} (red line).\n\n"
                f"What it means: if price hits that, the trade idea was wrong — get out immediately. "
                f"It caps your loss at about 1% of your money. Never remove it.",
                [ChatMarker(r.stop_loss, RED, f"Stop {self._fmt(r.stop_loss)}")],
                signal.decision.value, signal.confidence,
            )
        est = round(price - 1.5 * atr, 2)
        return ChatReply(
            f"There's no live trade yet, so no stop is set. If you did buy here, I'd put the safety exit "
            f"around {self._fmt(est)}. Tap a price and I'll size it exactly.",
            [ChatMarker(est, RED, f"~Stop {self._fmt(est)}")],
        )

    def _targets(self, signal) -> ChatReply:
        if signal.risk:
            r = signal.risk
            return ChatReply(
                f"🎯 Where to take profit:\n"
                f"• First target: {self._fmt(r.take_profit_1)} — take some money off here.\n"
                f"• Bigger target: {self._fmt(r.take_profit_2)} — let the rest run to here.\n\n"
                f"Tip: taking half at the first target and moving your stop to break-even is a safe habit.",
                [ChatMarker(r.take_profit_1, GREEN, f"TP1 {self._fmt(r.take_profit_1)}"),
                 ChatMarker(r.take_profit_2, GREEN, f"TP2 {self._fmt(r.take_profit_2)}")],
                signal.decision.value, signal.confidence,
            )
        return ChatReply("No live trade yet, so no targets. Ask 'where do I trade?' and I'll show the plan.")

    def _risk(self, signal) -> ChatReply:
        if signal.risk:
            r = signal.risk
            return ChatReply(
                f"💰 Your money at risk:\n"
                f"• Trade size: {r.position_size} units\n"
                f"• Most you can lose: about 1% of your account (the stop at {self._fmt(r.stop_loss)} protects you)\n"
                f"• Reward-to-risk: {r.risk_reward}-to-1 — you aim to make {r.risk_reward}× what you risk.\n\n"
                f"Never risk more than a small slice (1–2%) of your account on one trade.",
                self._plan_markers(signal), signal.decision.value, signal.confidence,
            )
        return ChatReply(
            "My rule: never risk more than 1% of your money on a single trade. There's no live trade "
            "right now, so nothing is at risk. 🛡️"
        )

    def _pattern_lookup(self, lesson: dict, row: pd.Series) -> ChatReply:
        """Explain a candlestick pattern and say whether it's on the chart right now."""
        from app.features.candlesticks import PATTERN_INFO

        present = [c for c in lesson["cols"] if float(row.get(c, 0) or 0) > 0]
        lines = [f"🕯️ {lesson['title']}", "", lesson["body"], ""]
        if present:
            name = PATTERN_INFO.get(present[0], (lesson["title"], "", ""))[0]
            lines.append(f"👀 And RIGHT NOW — yes! I can see a **{name}** forming on the current "
                         f"candle. Worth watching, but always wait for confirmation before trading.")
        else:
            lines.append(f"👀 Right now there's **no {lesson['keys'][0]}** on the chart. I'll point it "
                         f"out the moment one appears.")
        return ChatReply("\n".join(lines))

    def _help(self) -> ChatReply:
        return ChatReply(
            "👋 Hi! I'm your trading helper. I keep it simple. Try:\n\n"
            "• 'Where do I trade?' — I'll show the best spot\n"
            "• 'Should I buy or sell?' — my call, plain and clear\n"
            "• 'Is this safe?' — honest risk check\n"
            "• 'Why wait?' — if there's no trade, why not\n\n"
            "👉 Easiest of all: just TAP anywhere on the chart. I'll tell you if it's a good place to "
            "trade or not, which way to bet, and exactly where to get out. Green = buy, red = sell."
        )

    def _summary(self, signal, row, price) -> ChatReply:
        mood = {"Strong Bullish": "strongly up ⬆", "Bullish": "up ⬆", "Neutral": "unclear ↔",
                "Bearish": "down ⬇", "Strong Bearish": "strongly down ⬇"}.get(signal.market_status.value, "unclear")
        call = "take a trade" if signal.decision in (Side.BUY, Side.SELL) else "WAIT (no trade)"
        return ChatReply(
            f"📊 Quick read: market looks {mood}. Price {self._fmt(price)}. My call: {call} "
            f"({int(signal.confidence*100)}% sure).\n\n"
            f"Ask me 'where do I trade?', or just tap the chart to test any price.",
            self._plan_markers(signal), signal.decision.value, signal.confidence,
        )

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #

    def _plan_markers(self, signal: Signal) -> list[ChatMarker]:
        if not signal.risk:
            return []
        r = signal.risk
        return [
            ChatMarker((r.entry_low + r.entry_high) / 2, GREEN, f"Entry {self._fmt((r.entry_low+r.entry_high)/2)}"),
            ChatMarker(r.stop_loss, RED, f"Stop {self._fmt(r.stop_loss)}"),
            ChatMarker(r.take_profit_1, GREEN, f"TP1 {self._fmt(r.take_profit_1)}"),
            ChatMarker(r.take_profit_2, GREEN, f"TP2 {self._fmt(r.take_profit_2)}"),
        ]

    @staticmethod
    def _fmt(x) -> str:
        try:
            x = float(x)
        except (TypeError, ValueError):
            return str(x)
        if abs(x) >= 100:
            return f"${x:,.2f}"
        if abs(x) >= 1:
            return f"${x:,.3f}"
        return f"${x:,.6f}"

    @staticmethod
    def _safety_line(risk_amt: int) -> str:
        return (f"No trade is ever 100% safe — you can still lose. But this one only risks ${risk_amt} "
                f"(1% of your money) and has a clear exit, so the danger is controlled. Only trade if "
                f"you're fine losing ${risk_amt}.")

    @staticmethod
    def _location_side(entry: float, row: pd.Series) -> Side:
        """Buy low / sell high: pick direction from where the tap sits in the range."""
        res = float(row.get("donchian_upper") or entry)
        sup = float(row.get("donchian_lower") or entry)
        rng = max(res - sup, 1e-9)
        pos = (entry - sup) / rng
        if pos >= 0.6:                       # high in the range -> sell the ceiling
            return Side.SELL
        if pos <= 0.4:                       # low in the range -> buy the floor
            return Side.BUY
        return TradingAssistant._bias(row)   # middle -> follow the trend

    @staticmethod
    def _place_verdict(side: Side, entry: float, row: pd.Series) -> tuple[str, str, str]:
        """Return (icon, plain verdict, quality in {'good','ok','bad'})."""
        res = float(row.get("donchian_upper") or entry)
        sup = float(row.get("donchian_lower") or entry)
        rng = max(res - sup, 1e-9)
        pos = (entry - sup) / rng  # 0 = at floor(support), 1 = at ceiling(resistance)
        long = side is Side.BUY
        if long:
            if pos <= 0.35:
                return "✅", "you're near a support floor — a price where it often bounces back UP. Good place to buy.", "good"
            if pos >= 0.75:
                return "❌", "you'd be buying right under a ceiling (resistance) — that's buying high / chasing. Risky.", "bad"
            return "🟡", "you're in the middle of the range — no clear floor or ceiling nearby, so no real edge.", "ok"
        else:
            if pos >= 0.65:
                return "✅", "you're near a resistance ceiling — a price where it often turns back DOWN. Good place to sell.", "good"
            if pos <= 0.25:
                return "❌", "you'd be selling right on a support floor — that's selling low / chasing. Risky.", "bad"
            return "🟡", "you're in the middle of the range — no clear floor or ceiling nearby, so no real edge.", "ok"

    @staticmethod
    def _plainify_blockers(blockers: list[str]) -> list[str]:
        out = []
        for b in blockers:
            if "trend" in b.lower():
                out.append("The trend isn't clearly in our favour.")
            elif "volume" in b.lower():
                out.append("Not enough buying/selling pressure to back the move.")
            elif "momentum" in b.lower():
                out.append("Momentum (the market's push) isn't strong enough.")
            elif "structure" in b.lower():
                out.append("The chart pattern doesn't confirm the move yet.")
            elif "candle" in b.lower():
                out.append("No strong candlestick signal at a key level.")
            elif "confidence" in b.lower() or "below" in b.lower():
                out.append("The odds just aren't high enough to risk money.")
            elif "Heuristic" in b:
                out.append("(Model is in demo mode — treat signals as practice only.)")
        return out or ["The market's just too unclear right now."]

    @staticmethod
    def _bias(row: pd.Series) -> Side:
        score = 0
        if float(row.get("ema_9", 0) or 0) > float(row.get("ema_21", 0) or 0):
            score += 1
        if float(row.get("macd_hist", 0) or 0) > 0:
            score += 1
        if float(row.get("structure_trend", 0) or 0) > 0:
            score += 1
        if float(row.get("supertrend_dir", 0) or 0) > 0:
            score += 1
        return Side.BUY if score >= 2 else Side.SELL
