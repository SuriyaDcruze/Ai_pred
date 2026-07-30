"""Composite Confidence & Prioritisation Engine for Decision Intelligence (Sprint 5 · Milestone 4).

Evaluates **how strong, complete, and trustworthy the assembled evidence is** for a composed
:class:`DecisionIntelligence` object — and produces a deterministic **prioritisation score** used
only to organise objects by evidence strength.

**Core principle — read this before anything else.** Composite Confidence answers *"how trustworthy
is the assembled evidence?"* It is an **evidence-quality indicator**. It is **NOT** a probability of
success, a prediction confidence, a market/trading confidence, or an AI confidence, and it is
**never** a buy/sell/hold signal. A high-confidence *prediction* with no historical/similar/learning
support scores **low composite confidence** — because the assembled evidence is thin, even though the
model's own stored confidence is high. Prioritisation likewise organises objects by *evidence
strength only* and never implies an action.

It is a **pure, read-only, deterministic** transform over the already-composed object (M2) + its
evidence graph (M3): it re-runs no engine, computes no new statistic, and modifies nothing. Every
factor, penalty, and strength traces back to an existing subsystem output (an evidence node).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence

from app.decision_intelligence.evidence import EvidenceEngine, ExplainedDecision
from app.decision_intelligence.models import (
    CONTRIBUTORS,
    DecisionIntelligence,
    DecisionIntelligenceError,
    DecisionStatus,
    Subsystem,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)

#: Fixed factor weights (sum to 1.0) — the deterministic aggregation contract.
_WEIGHTS: dict[str, float] = {
    "prediction": 0.20, "historical_memory": 0.15, "similarity": 0.20, "learning": 0.20,
    "evidence_quality": 0.25,
}
_CONFLICT_PENALTY = 0.10          # each recorded conflict subtracts this from the score
_SIM_TARGET = 8                   # neighbour count at which similarity evidence is "full"
_SAMPLE_TARGET = 10               # total sample at which prioritisation sample-breadth saturates
_LABELS = {Subsystem.PREDICTION: "Prediction", Subsystem.HISTORICAL_MEMORY: "Historical Memory",
           Subsystem.SIMILARITY: "Similarity", Subsystem.LEARNING: "Learning"}
#: subsystem → the upstream-version key used to detect version mismatches
_VERSION_KEY = {Subsystem.PREDICTION: "prediction_model_version",
                Subsystem.SIMILARITY: "embedding_version", Subsystem.LEARNING: "learning_version"}


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _round(x: float | None, n: int = 4) -> float | None:
    return None if x is None else round(float(x), n)


# --------------------------------------------------------------------------- enums / errors
class ConfidenceLevel(str, Enum):
    """The evidence-quality band (NOT a trading-confidence band)."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INSUFFICIENT = "INSUFFICIENT"


class ConflictKind(str, Enum):
    """Kinds of evidence conflict (recorded, never hidden; each reduces composite confidence)."""

    OUTCOME_DISAGREEMENT = "OUTCOME_DISAGREEMENT"        # outcome model contradicts the direction
    SIMILARITY_DISAGREEMENT = "SIMILARITY_DISAGREEMENT"  # similar cases contradict the direction
    INCOMPLETE_PROVENANCE = "INCOMPLETE_PROVENANCE"      # a complete section lacks source/version
    VERSION_MISMATCH = "VERSION_MISMATCH"               # section version ≠ recorded upstream version


class ConfidenceError(DecisionIntelligenceError):
    """The input is not a well-formed decision/evidence pair."""


class InvalidConfidenceError(DecisionIntelligenceError):
    """A composite-confidence object failed validation (bad score/factor/reference)."""


# --------------------------------------------------------------------------- value objects
@dataclass(frozen=True)
class ConfidenceFactor:
    """One evidence-quality contributor — its normalised value, weight, and its source node."""

    name: str
    subsystem: Subsystem
    value: float                    # 0..1 (quality of *this* evidence, not a probability)
    weight: float
    node_ref: str                   # the evidence node this factor is traceable to
    detail: str | None = None

    def stable_dict(self) -> dict[str, Any]:
        return {"name": self.name, "subsystem": self.subsystem.value, "value": _round(self.value),
                "weight": _round(self.weight), "node_ref": self.node_ref, "detail": self.detail}


@dataclass(frozen=True)
class Penalty:
    """A recorded reduction from a perfect (1.0) evidence picture — always explainable."""

    reason: str
    amount: float
    subsystem: Subsystem | None = None
    detail: str | None = None

    def stable_dict(self) -> dict[str, Any]:
        return {"reason": self.reason, "amount": _round(self.amount),
                "subsystem": self.subsystem.value if self.subsystem else None, "detail": self.detail}


@dataclass(frozen=True)
class Strength:
    """A recorded evidence strength — always explainable + traceable."""

    reason: str
    subsystem: Subsystem
    detail: str | None = None

    def stable_dict(self) -> dict[str, Any]:
        return {"reason": self.reason, "subsystem": self.subsystem.value, "detail": self.detail}


@dataclass(frozen=True)
class Conflict:
    """A detected evidence conflict (recorded, never hidden)."""

    kind: ConflictKind
    subsystem: Subsystem
    detail: str

    def stable_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "subsystem": self.subsystem.value, "detail": self.detail}


@dataclass(frozen=True)
class EvidenceQuality:
    """A summary of the assembled evidence's completeness/attribution (not an outcome measure)."""

    completeness: float             # fraction of contributor sections COMPLETE
    provenance_completeness: float  # fraction of sections with a source identifier
    complete_sections: int
    missing_count: int
    conflict_count: int
    total_sample: int

    def stable_dict(self) -> dict[str, Any]:
        return {"completeness": _round(self.completeness),
                "provenance_completeness": _round(self.provenance_completeness),
                "complete_sections": self.complete_sections, "missing_count": self.missing_count,
                "conflict_count": self.conflict_count, "total_sample": self.total_sample}


@dataclass(frozen=True)
class CompositeConfidence:
    """The evidence-quality assessment of a Decision Intelligence object (never a trading signal)."""

    decision_id: str
    prediction_id: str
    decision_intelligence_version: str
    source_checksum: str
    score: float                    # 0..1 — evidence trustworthiness (NOT probability of success)
    level: ConfidenceLevel
    prioritisation_score: float     # 0..1 — organises objects by evidence strength only
    factors: tuple[ConfidenceFactor, ...]
    penalties: tuple[Penalty, ...]
    strengths: tuple[Strength, ...]
    conflicts: tuple[Conflict, ...]
    warnings: tuple[str, ...]
    evidence_quality: EvidenceQuality
    explanation: str
    checksum: str = ""

    def stable_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id, "prediction_id": self.prediction_id,
            "decision_intelligence_version": self.decision_intelligence_version,
            "source_checksum": self.source_checksum, "score": _round(self.score),
            "level": self.level.value, "prioritisation_score": _round(self.prioritisation_score),
            "factors": [f.stable_dict() for f in self.factors],
            "penalties": [p.stable_dict() for p in self.penalties],
            "strengths": [s.stable_dict() for s in self.strengths],
            "conflicts": [c.stable_dict() for c in self.conflicts], "warnings": list(self.warnings),
            "evidence_quality": self.evidence_quality.stable_dict(), "explanation": self.explanation,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.stable_dict(), "checksum": self.checksum}

    def serialize(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))


def _sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# --------------------------------------------------------------------------- the engine
class ConfidenceEngine:
    """Assesses composite (evidence-quality) confidence + prioritisation for a composed object.

    Stateless, deterministic, thread-safe, re-entrant. Reads only; never re-runs a subsystem, never
    computes a new statistic, never emits a trading signal."""

    def assess(
        self, decision: DecisionIntelligence, explained: ExplainedDecision | None = None
    ) -> CompositeConfidence:
        """Compute the composite confidence + prioritisation for a decision (+ its evidence graph).

        Raises:
            ConfidenceError: the input is not a DecisionIntelligence / matching ExplainedDecision.
            InvalidConfidenceError: the produced object failed validation.
        """
        if not isinstance(decision, DecisionIntelligence):
            raise ConfidenceError("expected a DecisionIntelligence object")
        if explained is None:
            explained = EvidenceEngine().explain(decision)
        if not isinstance(explained, ExplainedDecision) or explained.decision_id != decision.decision_id:
            raise ConfidenceError("evidence graph does not match the decision")

        node_of = {c.subsystem: c.node_id for c in explained.evidence_graph.root.children}
        factors = self._factors(decision, node_of, explained.evidence_graph.root.node_id)
        conflicts = self._conflicts(decision)
        missing_count = len(explained.explanation.missing)

        penalties: list[Penalty] = []
        for factor in factors:                                  # weak/absent evidence lowers the score
            gap = factor.weight * (1.0 - factor.value)
            if gap > 1e-9:
                penalties.append(Penalty(
                    reason=f"{factor.name} evidence weak or absent", amount=round(gap, 4),
                    subsystem=factor.subsystem, detail=factor.detail,
                ))
        for conflict in conflicts:
            penalties.append(Penalty(reason=f"conflict: {conflict.kind.value}",
                                     amount=_CONFLICT_PENALTY, subsystem=conflict.subsystem,
                                     detail=conflict.detail))

        score = _clamp(1.0 - sum(p.amount for p in penalties))
        level = self._level(score)
        detail_of = {f.subsystem: f.detail for f in factors}
        strengths = tuple(
            Strength(reason=f"{_LABELS[sub]} evidence present", subsystem=sub, detail=detail_of.get(sub))
            for sub in CONTRIBUTORS if decision.component(sub).status is DecisionStatus.COMPLETE
        )
        eq = self._evidence_quality(decision, conflicts, missing_count)
        prioritisation = self._prioritisation(eq)
        warnings = self._warnings(decision, conflicts)
        explanation = self._explanation(score, level, eq, conflicts)

        result = CompositeConfidence(
            decision_id=decision.decision_id, prediction_id=decision.prediction_id,
            decision_intelligence_version=decision.decision_intelligence_version,
            source_checksum=decision.checksum, score=round(score, 4), level=level,
            prioritisation_score=round(prioritisation, 4), factors=tuple(factors),
            penalties=tuple(penalties), strengths=strengths, conflicts=tuple(conflicts),
            warnings=tuple(warnings), evidence_quality=eq, explanation=explanation,
        )
        result = _with_checksum(result)
        self._validate(result, explained)
        logger.info("confidence: %s score=%.3f level=%s conflicts=%d priority=%.3f",
                    decision.decision_id, score, level.value, len(conflicts), prioritisation)
        return result

    # ---------------------------------------------------------------- factors
    def _factors(
        self, decision: DecisionIntelligence, node_of: dict, root_id: str
    ) -> list[ConfidenceFactor]:
        def payload(sub: Subsystem) -> dict:
            comp = decision.component(sub)
            return comp.payload if isinstance(comp.payload, dict) else {}

        def complete(sub: Subsystem) -> bool:
            return decision.component(sub).status is DecisionStatus.COMPLETE

        pred = payload(Subsystem.PREDICTION)
        pred_value = 0.0
        if complete(Subsystem.PREDICTION):
            pred_value = 1.0 if pred.get("direction_prob") is not None else 0.6
        sim = payload(Subsystem.SIMILARITY)
        sim_value = _clamp((sim.get("neighbour_count") or 0) / _SIM_TARGET, 0.3, 1.0) \
            if complete(Subsystem.SIMILARITY) else 0.0
        completeness = sum(complete(s) for s in CONTRIBUTORS) / len(CONTRIBUTORS)

        return [
            ConfidenceFactor("prediction", Subsystem.PREDICTION, pred_value, _WEIGHTS["prediction"],
                             node_of.get(Subsystem.PREDICTION, root_id),
                             "stored prediction evidence present" if pred_value else "no prediction evidence"),
            ConfidenceFactor("historical_memory", Subsystem.HISTORICAL_MEMORY,
                             1.0 if complete(Subsystem.HISTORICAL_MEMORY) else 0.0,
                             _WEIGHTS["historical_memory"], node_of.get(Subsystem.HISTORICAL_MEMORY, root_id),
                             "resolved historical context present"),
            ConfidenceFactor("similarity", Subsystem.SIMILARITY, sim_value, _WEIGHTS["similarity"],
                             node_of.get(Subsystem.SIMILARITY, root_id),
                             f"{sim.get('neighbour_count') or 0} comparable case(s)"),
            ConfidenceFactor("learning", Subsystem.LEARNING,
                             1.0 if complete(Subsystem.LEARNING) else 0.0, _WEIGHTS["learning"],
                             node_of.get(Subsystem.LEARNING, root_id),
                             "validated learning observation present"),
            ConfidenceFactor("evidence_quality", Subsystem.DECISION_INTELLIGENCE, completeness,
                             _WEIGHTS["evidence_quality"], root_id,
                             f"{int(completeness * len(CONTRIBUTORS))}/{len(CONTRIBUTORS)} sections complete"),
        ]

    # ---------------------------------------------------------------- conflicts
    def _conflicts(self, decision: DecisionIntelligence) -> list[Conflict]:
        conflicts: list[Conflict] = []
        pred = decision.component(Subsystem.PREDICTION)
        p = pred.payload if isinstance(pred.payload, dict) else {}
        if pred.status is DecisionStatus.COMPLETE and p.get("direction") in ("BUY", "SELL") \
                and p.get("outcome_prob") is not None and float(p["outcome_prob"]) < 0.5:
            conflicts.append(Conflict(ConflictKind.OUTCOME_DISAGREEMENT, Subsystem.PREDICTION,
                                      f"outcome-model probability {float(p['outcome_prob']):.2f} < 0.5 "
                                      f"contradicts the {p['direction']} direction"))
        sim = decision.component(Subsystem.SIMILARITY)
        sp = sim.payload if isinstance(sim.payload, dict) else {}
        if sim.status is DecisionStatus.COMPLETE and sp.get("win_rate") is not None \
                and float(sp["win_rate"]) < 0.5:
            conflicts.append(Conflict(ConflictKind.SIMILARITY_DISAGREEMENT, Subsystem.SIMILARITY,
                                      f"similar cases win rate {float(sp['win_rate']):.2f} < 0.5"))
        # provenance / version conflicts on COMPLETE sections
        for sub in CONTRIBUTORS:
            comp = decision.component(sub)
            if comp.status is not DecisionStatus.COMPLETE:
                continue
            if not comp.provenance.source:
                conflicts.append(Conflict(ConflictKind.INCOMPLETE_PROVENANCE, sub,
                                          f"{_LABELS[sub]} section has no source identifier"))
            key = _VERSION_KEY.get(sub)
            if key:
                recorded = getattr(decision.upstream_versions, key, None)
                sect = comp.provenance.subsystem_version
                if sect and recorded and sect != recorded:
                    conflicts.append(Conflict(ConflictKind.VERSION_MISMATCH, sub,
                                              f"{_LABELS[sub]} version {sect!r} != recorded {recorded!r}"))
        return conflicts

    # ---------------------------------------------------------------- summaries
    @staticmethod
    def _evidence_quality(
        decision: DecisionIntelligence, conflicts: list[Conflict], missing_count: int
    ) -> EvidenceQuality:
        complete = sum(decision.component(s).status is DecisionStatus.COMPLETE for s in CONTRIBUTORS)
        with_source = sum(bool(decision.component(s).provenance.source) for s in CONTRIBUTORS)
        sim = decision.component(Subsystem.SIMILARITY).payload or {}
        learn = decision.component(Subsystem.LEARNING).payload or {}
        total_sample = int((sim.get("sample_size") or 0) + (learn.get("pattern_count") or 0)) \
            if isinstance(sim, dict) and isinstance(learn, dict) else 0
        return EvidenceQuality(
            completeness=complete / len(CONTRIBUTORS),
            provenance_completeness=with_source / len(CONTRIBUTORS), complete_sections=complete,
            missing_count=missing_count, conflict_count=len(conflicts), total_sample=total_sample,
        )

    @staticmethod
    def _prioritisation(eq: EvidenceQuality) -> float:
        """Organise objects by **evidence strength only** — never prediction outcome / future info."""
        sample_breadth = _clamp(eq.total_sample / _SAMPLE_TARGET)
        conflict_ratio = _clamp(eq.conflict_count / len(CONTRIBUTORS))
        return _clamp(0.5 * eq.completeness + 0.2 * eq.provenance_completeness
                      + 0.2 * sample_breadth + 0.1 * (1.0 - conflict_ratio))

    @staticmethod
    def _level(score: float) -> ConfidenceLevel:
        if score >= 0.75:
            return ConfidenceLevel.HIGH
        if score >= 0.50:
            return ConfidenceLevel.MEDIUM
        if score >= 0.20:
            return ConfidenceLevel.LOW
        return ConfidenceLevel.INSUFFICIENT

    @staticmethod
    def _warnings(decision: DecisionIntelligence, conflicts: list[Conflict]) -> list[str]:
        warnings = [f"{c.kind.value}: {c.detail}" for c in conflicts]
        for sub in CONTRIBUTORS:
            if decision.component(sub).status is not DecisionStatus.COMPLETE:
                warnings.append(f"{_LABELS[sub]} evidence is {decision.component(sub).status.value}.")
        return warnings

    @staticmethod
    def _explanation(score: float, level: ConfidenceLevel, eq: EvidenceQuality,
                     conflicts: list[Conflict]) -> str:
        return (
            f"Composite evidence confidence {score:.2f} ({level.value}) — a measure of how complete, "
            f"consistent, and well-attributed the ASSEMBLED EVIDENCE is; NOT a probability of success, "
            f"a prediction confidence, or a trading signal. Sections complete: "
            f"{eq.complete_sections}/{len(CONTRIBUTORS)}; conflicts: {len(conflicts)}; "
            f"missing facets: {eq.missing_count}."
        )

    # ---------------------------------------------------------------- validation
    @staticmethod
    def _validate(result: CompositeConfidence, explained: ExplainedDecision) -> None:
        if not 0.0 <= result.score <= 1.0 or not 0.0 <= result.prioritisation_score <= 1.0:
            raise InvalidConfidenceError("score out of [0, 1]")
        if not isinstance(result.level, ConfidenceLevel):
            raise InvalidConfidenceError("invalid confidence level")
        if not result.factors:
            raise InvalidConfidenceError("no confidence factors")
        node_ids = {n.node_id for n in explained.evidence_graph.nodes()}
        for factor in result.factors:
            if not 0.0 <= factor.value <= 1.0 or not 0.0 <= factor.weight <= 1.0:
                raise InvalidConfidenceError(f"factor {factor.name} out of range")
            if factor.node_ref not in node_ids:                 # every factor traces to a real node
                raise InvalidConfidenceError(f"factor {factor.name} references unknown node")
        if not result.explanation:
            raise InvalidConfidenceError("empty explanation")


def _with_checksum(result: CompositeConfidence) -> CompositeConfidence:
    from dataclasses import replace
    return replace(result, checksum=_sha256(result.stable_dict()))


def prioritise(confidences: Sequence[CompositeConfidence]) -> list[CompositeConfidence]:
    """Deterministically order Decision Intelligence objects by **evidence strength** (descending),
    tie-broken by ``decision_id``. Organisational only — never implies buy/sell/hold or any action."""
    return sorted(confidences, key=lambda c: (-c.prioritisation_score, c.decision_id))
