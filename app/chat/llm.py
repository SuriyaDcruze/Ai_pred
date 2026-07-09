"""GPT-style LLM trading assistant backed by the Claude API (Claude Opus 4.8).

The deterministic :class:`TradingAssistant` computes the *exact* numbers — entry,
stop, targets, position size, support/resistance, the trigger condition — and
this class hands those to Claude as ground truth. Claude only phrases the answer
and handles free-form follow-ups; it is explicitly forbidden from inventing
levels. So conversation is natural, but every price the model says matches a line
drawn on the chart.

If the ``anthropic`` package or an API key/profile is unavailable, this falls
back to the deterministic assistant's own text — the app never breaks.
"""

from __future__ import annotations

import json

import pandas as pd

from app.chat.assistant import ChatReply, TradingAssistant
from app.config import settings
from app.data.schemas import Signal
from app.utils.logging import get_logger

logger = get_logger(__name__)

# Load .env so ANTHROPIC_API_KEY placed there is picked up by the SDK.
try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass

SYSTEM_PROMPT = """You are Aegis, an institutional-grade trading co-pilot embedded in a live charting \
app. You are talking to a trader who can tap the chart to test any price.

Hard rules — non-negotiable:
- Use ONLY the numbers in the CONTEXT block. Never invent or estimate prices, levels, \
sizes, or indicator values. If a number isn't in the context, say you don't have it.
- Answer the exact question. Be concrete: give the exact entry price, exact stop, exact \
targets, the risk:reward, position size, and — crucially — the TRIGGER (the "when": what \
must happen before entering). Traders want "buy at X when Y", not "watch support".
- Colors: green = entry / upside targets / support; red = stop / resistance / against the \
trade. Refer to them so they match the lines drawn on the chart.
- Never guarantee profit or certainty. Always respect the stop and the confidence level.
- If the system says WAIT, don't manufacture a trade — but DO give the exact plan being \
stalked and its trigger, so the trader knows precisely where and when to act.
- Be concise and direct, like a desk trader. No filler, no disclaimers beyond the one-line \
risk note. Plain text, short lines or bullets. No markdown headers."""


class LLMAssistant:
    """Claude-backed conversational layer over the deterministic engine."""

    def __init__(self, risk_manager=None, model: str | None = None):
        self._det = TradingAssistant(risk_manager=risk_manager)
        self._model = model or settings.chat_model
        self._client = None
        self._client_ready: bool | None = None

    # ------------------------------------------------------------------ #
    @property
    def available(self) -> bool:
        """True if the Anthropic SDK imports and credentials resolve."""
        if self._client_ready is not None:
            return self._client_ready
        try:
            import anthropic

            self._client = anthropic.Anthropic()  # resolves key/profile from env
            self._client_ready = True
            logger.info("LLM assistant ready (model=%s).", self._model)
        except Exception as exc:  # noqa: BLE001 - any failure -> heuristic fallback
            logger.warning("LLM assistant unavailable (%s); using deterministic replies.", exc)
            self._client_ready = False
        return self._client_ready

    def respond(
        self,
        signal: Signal,
        ohlcv: pd.DataFrame,
        features: pd.DataFrame,
        message: str,
        hypo_price: float | None = None,
    ) -> ChatReply:
        # Always compute the deterministic answer first: it yields the exact
        # numbers + the chart markers, and is our fallback.
        grounded = self._det.respond(signal, ohlcv, features, message, hypo_price)
        if not self.available:
            return grounded

        context = self._build_context(signal, features, ohlcv, grounded, message, hypo_price)
        try:
            text = self._call_claude(context, message)
        except Exception as exc:  # noqa: BLE001 - network/API error -> fallback
            logger.warning("Claude call failed (%s); serving deterministic reply.", exc)
            return grounded

        # Keep the deterministic markers so the chart lines match the numbers.
        return ChatReply(
            reply=text,
            markers=grounded.markers,
            decision=grounded.decision,
            confidence=grounded.confidence,
        )

    # ------------------------------------------------------------------ #
    def _call_claude(self, context: str, message: str) -> str:
        # Omit `thinking` for a snappy chat reply; low effort keeps it fast.
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=700,
            system=SYSTEM_PROMPT,
            output_config={"effort": "low"},
            messages=[
                {
                    "role": "user",
                    "content": f"CONTEXT (ground truth — use these numbers only):\n{context}\n\n"
                    f"Trader asks: {message}",
                }
            ],
        )
        parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
        return "\n".join(parts).strip() or "I couldn't form a response — try rephrasing."

    @staticmethod
    def _build_context(
        signal: Signal,
        features: pd.DataFrame,
        ohlcv: pd.DataFrame,
        grounded: ChatReply,
        message: str,
        hypo_price: float | None,
    ) -> str:
        row = features.iloc[-1]
        price = float(ohlcv["close"].iloc[-1])

        def g(name, default=None):
            v = row.get(name, default)
            try:
                return round(float(v), 4)
            except (TypeError, ValueError):
                return default

        ctx = {
            "symbol": signal.symbol,
            "timeframe": signal.timeframe,
            "current_price": round(price, 4),
            "market_status": signal.market_status.value,
            "decision": signal.decision.value,
            "confidence_pct": round(signal.confidence * 100, 1),
            "trend_strength": signal.trend_strength,
            "tapped_price": round(hypo_price, 4) if hypo_price else None,
            "indicators": {
                "rsi": g("rsi"), "macd_hist": g("macd_hist"), "adx": g("adx"),
                "atr": g("atr"), "vwap": g("vwap"), "supertrend_dir": g("supertrend_dir"),
                "ema9": g("ema_9"), "ema21": g("ema_21"), "ema50": g("ema_50"),
                "structure_trend": g("structure_trend"), "bos": g("bos"), "choch": g("choch"),
            },
            "support": g("donchian_lower"),
            "resistance": g("donchian_upper"),
            "pivot": g("pivot"),
            "computed_answer": grounded.reply,  # exact numbers + trigger already worked out
            "computed_levels": [
                {"price": round(m.price, 4), "color": m.color, "label": m.label}
                for m in grounded.markers
            ],
        }
        return json.dumps(ctx, indent=2, default=str)
