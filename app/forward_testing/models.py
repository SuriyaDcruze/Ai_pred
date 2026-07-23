"""Domain models for Forward Testing — the prediction record and its lifecycle.

A **prediction record** is the permanent, auditable trace of one recommendation the
platform made: what the models said, the plan that followed, the market context at the
time, which model versions produced it, and — once the market has spoken — what actually
happened.

Two design rules from the Architecture Book:

* **Immutable creation fields** (Vol 34 — Audit). Everything captured at creation is
  written once. Only the lifecycle columns (``status``, ``resolved_*``, ``realised_r``,
  ``holding_bars``, ``updated_at``) change afterwards.
* **Version everything independently** — the prediction model, the outcome model, and the
  feature set each carry their own version, so a future model swap never invalidates or
  breaks older records.

This module is pure domain + persistence mapping. It imports **nothing** from the
Prediction or Outcome engines: those are read-only production components, and Forward
Testing only ever consumes their outputs.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping


class PredictionStatus(str, Enum):
    """Lifecycle of a forward-tested prediction.

    ``PENDING → (ENTRY_TRIGGERED) → ACTIVE → TARGET_HIT | STOP_HIT | EXPIRED → COMPLETED``
    with ``CANCELLED`` as an early exit. A record is *open* while the market can still
    change its fate, and *terminal* once it cannot.
    """

    PENDING = "PENDING"                   # recorded, entry not yet triggered
    ENTRY_TRIGGERED = "ENTRY_TRIGGERED"   # entry price touched (limit-style entries)
    ACTIVE = "ACTIVE"                     # in the trade, awaiting target/stop/expiry
    TARGET_HIT = "TARGET_HIT"             # terminal: target reached first
    STOP_HIT = "STOP_HIT"                 # terminal: stop reached first
    EXPIRED = "EXPIRED"                   # terminal: max holding elapsed, neither hit
    CANCELLED = "CANCELLED"               # terminal: invalidated before entry
    COMPLETED = "COMPLETED"               # terminal: finalised & written to memory

    @classmethod
    def open_states(cls) -> frozenset["PredictionStatus"]:
        """States where the market can still resolve the prediction."""
        return frozenset({cls.PENDING, cls.ENTRY_TRIGGERED, cls.ACTIVE})

    @classmethod
    def terminal_states(cls) -> frozenset["PredictionStatus"]:
        """States that are final — the monitor must never re-resolve these."""
        return frozenset(
            {cls.TARGET_HIT, cls.STOP_HIT, cls.EXPIRED, cls.CANCELLED, cls.COMPLETED}
        )

    def is_open(self) -> bool:
        """True when the monitor should keep watching this prediction."""
        return self in self.open_states()

    def is_terminal(self) -> bool:
        """True when the prediction's outcome is settled."""
        return self in self.terminal_states()


def _utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string (the storage format for timestamps)."""
    return datetime.now(tz=timezone.utc).isoformat()


@dataclass
class PredictionRecord:
    """One recommendation, captured permanently for forward testing and learning.

    Attributes are grouped as: identity/instrument, model outputs, trade plan, market
    context, version stamps, and lifecycle. Only the lifecycle fields mutate after
    creation.
    """

    # --- instrument -------------------------------------------------------- #
    symbol: str
    exchange: str
    timeframe: str
    current_price: float
    direction: str            # BUY | SELL | WAIT (the model's directional read)
    recommendation: str       # BUY | SELL | WAIT (the final decision after the gates)
    created_candle_ts: int    # epoch seconds of the bar this was based on

    # --- identity / timestamps -------------------------------------------- #
    prediction_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str = field(default_factory=_utc_now_iso)
    source: str = "forward"   # forward | manual | screener

    # --- model outputs (verbatim from the engines) ------------------------- #
    direction_prob: float | None = None
    outcome_prob: float | None = None
    decision_score: float | None = None

    # --- trade plan (Risk Engine) ------------------------------------------ #
    entry: float | None = None
    stop: float | None = None
    target1: float | None = None
    target2: float | None = None

    # --- market context (explainability + future learning) ----------------- #
    market_regime: str | None = None
    market_phase: str | None = None
    sector: str | None = None
    session: str | None = None
    volatility_bucket: str | None = None
    similarity_score: float | None = None
    context: dict[str, Any] = field(default_factory=dict)

    # --- version stamps (independent, forward compatible) ------------------ #
    prediction_model_version: str | None = None
    outcome_model_version: str | None = None
    feature_version: str | None = None

    # --- lifecycle (the only mutable part) --------------------------------- #
    status: PredictionStatus = PredictionStatus.PENDING
    resolved_at: str | None = None
    resolved_price: float | None = None
    resolution_reason: str | None = None
    realised_r: float | None = None
    holding_bars: int | None = None

    # ---------------------------------------------------------------- helpers
    def is_open(self) -> bool:
        """True while the monitor should keep watching this prediction."""
        return self.status.is_open()

    def is_terminal(self) -> bool:
        """True once the outcome is settled."""
        return self.status.is_terminal()

    def touch(self) -> None:
        """Stamp ``updated_at`` — call whenever a lifecycle field changes."""
        self.updated_at = _utc_now_iso()

    # ------------------------------------------------------------ persistence
    def to_row(self) -> dict[str, Any]:
        """Flatten to a database row (column name → value).

        ``context`` is serialised to ``context_json`` so arbitrary future context can be
        stored without a schema change.
        """
        return {
            "prediction_id": self.prediction_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "created_candle_ts": self.created_candle_ts,
            "symbol": self.symbol,
            "exchange": self.exchange,
            "timeframe": self.timeframe,
            "source": self.source,
            "current_price": self.current_price,
            "direction": self.direction,
            "direction_prob": self.direction_prob,
            "outcome_prob": self.outcome_prob,
            "decision_score": self.decision_score,
            "recommendation": self.recommendation,
            "entry": self.entry,
            "stop": self.stop,
            "target1": self.target1,
            "target2": self.target2,
            "market_regime": self.market_regime,
            "market_phase": self.market_phase,
            "sector": self.sector,
            "session": self.session,
            "volatility_bucket": self.volatility_bucket,
            "similarity_score": self.similarity_score,
            "context_json": json.dumps(self.context) if self.context else None,
            "prediction_model_version": self.prediction_model_version,
            "outcome_model_version": self.outcome_model_version,
            "feature_version": self.feature_version,
            "status": self.status.value,
            "resolved_at": self.resolved_at,
            "resolved_price": self.resolved_price,
            "resolution_reason": self.resolution_reason,
            "realised_r": self.realised_r,
            "holding_bars": self.holding_bars,
        }

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "PredictionRecord":
        """Rebuild a record from a database row.

        Reads **by column name** and tolerates missing keys, so columns added by future
        migrations never break older readers (forward compatibility).
        """
        def get(key: str, default: Any = None) -> Any:
            try:
                value = row[key]
            except (KeyError, IndexError):
                return default
            return default if value is None else value

        raw_context = get("context_json")
        try:
            context = json.loads(raw_context) if raw_context else {}
        except (TypeError, ValueError):
            context = {}

        return cls(
            prediction_id=get("prediction_id"),
            created_at=get("created_at"),
            updated_at=get("updated_at"),
            created_candle_ts=int(get("created_candle_ts", 0)),
            symbol=get("symbol"),
            exchange=get("exchange"),
            timeframe=get("timeframe"),
            source=get("source", "forward"),
            current_price=float(get("current_price", 0.0)),
            direction=get("direction"),
            direction_prob=get("direction_prob"),
            outcome_prob=get("outcome_prob"),
            decision_score=get("decision_score"),
            recommendation=get("recommendation"),
            entry=get("entry"),
            stop=get("stop"),
            target1=get("target1"),
            target2=get("target2"),
            market_regime=get("market_regime"),
            market_phase=get("market_phase"),
            sector=get("sector"),
            session=get("session"),
            volatility_bucket=get("volatility_bucket"),
            similarity_score=get("similarity_score"),
            context=context,
            prediction_model_version=get("prediction_model_version"),
            outcome_model_version=get("outcome_model_version"),
            feature_version=get("feature_version"),
            status=PredictionStatus(get("status", PredictionStatus.PENDING.value)),
            resolved_at=get("resolved_at"),
            resolved_price=get("resolved_price"),
            resolution_reason=get("resolution_reason"),
            realised_r=get("realised_r"),
            holding_bars=get("holding_bars"),
        )
