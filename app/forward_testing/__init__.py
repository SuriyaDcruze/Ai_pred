"""Forward Testing — continuous forward validation of the platform's recommendations.

Forward Testing records every recommendation the platform makes and later scores it
against real future price. It is neither a backtest nor live trading: it is the evidence
layer that shows whether the Prediction and Outcome engines keep their edge on data they
have never seen.

This package is **strictly independent** of those engines — it consumes their outputs and
never imports, modifies, or refactors them.

Milestones 1–3 provide the domain models, the persistence layer, and the engine
(resolver + monitor); the REST API and dashboard arrive in later milestones.
"""

from __future__ import annotations

from app.forward_testing.engine import ForwardTestingEngine
from app.forward_testing.models import PredictionRecord, PredictionStatus
from app.forward_testing.monitor import ForwardTestingMonitor
from app.forward_testing.resolver import ResolutionOutcome, resolve_prediction
from app.forward_testing.store import PredictionStore

__all__ = [
    "PredictionRecord",
    "PredictionStatus",
    "PredictionStore",
    "ForwardTestingEngine",
    "ForwardTestingMonitor",
    "ResolutionOutcome",
    "resolve_prediction",
]
