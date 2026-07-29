"""Recommendation Engine — evidence-bound descriptive observations (Sprint 4 · Vol 15 · M4).

Turns the **VALIDATED** statistical patterns from the Statistical Validation Engine (M3) into
plain-language, **auditable** recommendation objects. It performs **no statistical calculations**
(it restates M3's already-computed figures), **never** predicts, trains, or gives trading advice,
and it modifies nothing upstream. It is a **pure, read-only, deterministic** transform: identical
inputs always yield identical recommendations; imports neither the Prediction nor the Outcome
engine.

Evidence & honesty:
- **Only VALIDATED patterns** become recommendations; a run with none → ``INSUFFICIENT_DATA``.
- Every recommendation carries the supporting ``prediction_id``s (sourced from the M2 candidate
  patterns, so M3's output is left untouched), the verbatim statistical basis, and a **non-empty**
  ``limitations`` list.
- **Descriptive framing only** — *"historically … over N trades (95% CI …)"*, tagged as a
  hypothesis to validate live. Never *"buy / this will win / take this trade."*
- **Recommendation confidence is NOT statistical significance** — it is confidence in
  *communicating* the observation, derived deterministically from sample size, CI width,
  consistency, and evidence quality.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from typing import Any, Sequence

from app.learning.models import (
    DATASET_VERSION,
    LEARNING_VERSION,
    CandidatePattern,
    ConfidenceInterval,
    InvalidValidationError,
    LearningStatus,
    MissingEvidenceError,
    Recommendation,
    RecommendationConfidence,
    RecommendationResult,
    RecommendationType,
    UnsupportedVersionError,
    ValidatedPattern,
    ValidationResult,
    _recommendation_key,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)

#: A pattern whose win-rate stability across sub-periods falls below this is described as
#: UNSTABLE_BEHAVIOUR (mirrors the platform's aversion to curve-fit-to-one-window results).
UNSTABLE_THRESHOLD: float = 0.60
#: Sample-size tiers used by the (deterministic) communication-confidence rubric.
STRONG_SAMPLE: int = 100
MODERATE_SAMPLE: int = 50

#: Dimension (grouping key) → the descriptive observation category. Extensible registry.
CATEGORY_BY_DIMENSION: dict[str, str] = {
    "sector": "Sector Observation",
    "symbol": "Symbol Observation",
    "market_regime": "Regime Observation",
    "market_phase": "Regime Observation",
    "timeframe": "Timeframe Observation",
    "confidence_bucket": "Confidence Observation",
    "holding_period_bucket": "Risk Observation",
    "outcome_category": "Risk Observation",
    "prediction_model_version": "Model Observation",
    "feature_version": "Model Observation",
}
_DEFAULT_CATEGORY = "Historical Observation"


def recommendation_category(grouping_key: str) -> str:
    """The descriptive observation category for a validated pattern's dimension (never advice)."""
    return CATEGORY_BY_DIMENSION.get(grouping_key, _DEFAULT_CATEGORY)


def recommendation_type_of(pattern: ValidatedPattern) -> RecommendationType:
    """The performance/stability character of a validated pattern (deterministic).

    A VALIDATED pattern's interval excludes the baseline, so it is unambiguously a strength or a
    weakness; an unstable win rate across sub-periods overrides to UNSTABLE_BEHAVIOUR."""
    if pattern.consistency_score is not None and pattern.consistency_score < UNSTABLE_THRESHOLD:
        return RecommendationType.UNSTABLE_BEHAVIOUR
    if pattern.confidence_interval.low > pattern.significance.baseline:
        return RecommendationType.HISTORICAL_STRENGTH
    return RecommendationType.HISTORICAL_WEAKNESS


def recommendation_confidence(pattern: ValidatedPattern) -> RecommendationConfidence:
    """Confidence in **communicating** the observation (NOT significance).

    A deterministic, non-arbitrary rubric over four evidence-quality factors — sample size, CI
    width (quality), sub-period consistency, and evidence traceability — scored 0–7 and banded."""
    score = 0
    # sample size
    if pattern.sample_size >= STRONG_SAMPLE:
        score += 2
    elif pattern.sample_size >= MODERATE_SAMPLE:
        score += 1
    # confidence-interval width (narrower ⇒ more communicable)
    quality = pattern.confidence_interval.quality
    score += {"HIGH": 2, "MODERATE": 1}.get(quality, 0)
    # sub-period consistency (stability)
    if pattern.consistency_score is not None:
        if pattern.consistency_score >= 0.80:
            score += 2
        elif pattern.consistency_score >= UNSTABLE_THRESHOLD:
            score += 1
    # evidence traceability (every trade accounted for)
    if pattern.evidence_count == pattern.sample_size and pattern.evidence_count > 0:
        score += 1
    if score >= 5:
        return RecommendationConfidence.HIGH
    if score >= 3:
        return RecommendationConfidence.MEDIUM
    return RecommendationConfidence.LOW


# ------------------------------------------------------------------ language (descriptive only)
def _pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def _r(value: float | None) -> str:
    if value is None:
        return "n/a"
    if math.isinf(value):
        return "∞"
    return f"{value:+.2f}R"


def _pf(value: float | None) -> str:
    if value is None:
        return "n/a"
    return "∞" if math.isinf(value) else f"{value:.2f}"


def _title(pattern: ValidatedPattern, category: str, rec_type: RecommendationType) -> str:
    label = rec_type.value.replace("_", " ").title()
    return f"{category}: {pattern.grouping_key}={pattern.grouping_value} — {label}"


def _summary(pattern: ValidatedPattern) -> str:
    ci = pattern.confidence_interval
    return (
        f"Historically, {pattern.grouping_key} '{pattern.grouping_value}' resolved with a "
        f"{_pct(pattern.win_rate)} win rate across {pattern.sample_size} completed trades "
        f"(95% CI {_pct(ci.low)}–{_pct(ci.high)})."
    )


def _detailed_explanation(pattern: ValidatedPattern, correction_method: str) -> str:
    ci = pattern.confidence_interval
    consistency = ("not assessed (too few sub-periods)" if pattern.consistency_score is None
                   else f"{pattern.consistency_score:.2f} (1.0 = fully stable)")
    return (
        f"Across {pattern.sample_size} completed trades grouped by "
        f"{pattern.grouping_key}={pattern.grouping_value}, the historical win rate was "
        f"{_pct(pattern.win_rate)} (95% Wilson CI {_pct(ci.low)}–{_pct(ci.high)}), averaging "
        f"{_r(pattern.average_r)} per trade (expectancy {_r(pattern.expectancy)}), profit factor "
        f"{_pf(pattern.profit_factor)}, maximum drawdown {_r(pattern.max_drawdown_r)}. Win-rate "
        f"stability across sub-periods: {consistency}. This is a descriptive historical "
        f"observation, statistically significant after {correction_method} correction — a "
        f"hypothesis to validate in Forward Testing, not a prediction or trading advice."
    )


def _statistical_basis(pattern: ValidatedPattern, correction_method: str) -> str:
    ci, sig = pattern.confidence_interval, pattern.significance
    return (
        f"n={pattern.sample_size}, wins={pattern.wins}, losses={pattern.losses}, "
        f"win_rate={pattern.win_rate:.4f}, avg_r={pattern.average_r:.4f}, "
        f"expectancy={pattern.expectancy:.4f}, profit_factor={_pf(pattern.profit_factor)}, "
        f"max_drawdown_r={pattern.max_drawdown_r}, ci=[{ci.low:.4f},{ci.high:.4f}] "
        f"(width={ci.width:.4f}, {ci.quality}), p_value={sig.p_value:.4g}, z={sig.z_score:.4g}, "
        f"baseline={sig.baseline}, correction={correction_method}, corrected_significant=True."
    )


def _limitations(pattern: ValidatedPattern, rec_type: RecommendationType) -> tuple[str, ...]:
    """Always a non-empty list of honest caveats bound to this specific observation."""
    ci = pattern.confidence_interval
    items = [
        f"Based on a historical sample of {pattern.sample_size} completed trades — a past "
        f"observation, not a prediction of future results.",
    ]
    if ci.width >= 0.20:
        items.append(
            f"Confidence interval {_pct(ci.low)}–{_pct(ci.high)} (width {ci.width:.2f}); the true "
            f"long-run rate remains uncertain within this range."
        )
    if pattern.grouping_key in ("market_regime", "market_phase"):
        items.append("Observed within a specific market regime/phase; may not generalise to others.")
    if pattern.grouping_key == "timeframe":
        items.append(f"Specific to the '{pattern.grouping_value}' timeframe.")
    if pattern.grouping_key in ("symbol",):
        items.append("Concentrated in a single instrument; sensitive to that instrument's regime.")
    if rec_type is RecommendationType.UNSTABLE_BEHAVIOUR:
        items.append("Win rate was not stable across sub-periods — possible curve-fit; treat with caution.")
    if pattern.consistency_score is None:
        items.append("Too few sub-periods to assess stability across time.")
    items.append("A hypothesis to validate in Forward Testing — not trading advice.")
    return tuple(items)


def _checksum(recommendations: Sequence[Recommendation]) -> str:
    payload = json.dumps([r.stable_dict() for r in recommendations], sort_keys=True,
                         separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class RecommendationEngine:
    """Generates evidence-bound descriptive recommendations from validated patterns (read-only).

    Deterministic, thread-safe (pure over its inputs; no shared mutable state), and idempotent:
    the same validation result + candidate patterns always produce the same
    :class:`RecommendationResult`.
    """

    def __init__(self, *, unstable_threshold: float = UNSTABLE_THRESHOLD) -> None:
        """Configure the engine.

        Args:
            unstable_threshold: consistency below which a pattern is described as unstable.
        """
        self.unstable_threshold = unstable_threshold

    def generate(
        self, validation: ValidationResult, patterns: Sequence[CandidatePattern]
    ) -> RecommendationResult:
        """Generate recommendations from a validation result + its source candidate patterns.

        Args:
            validation: the M3 :class:`ValidationResult` (only its VALIDATED patterns are used).
            patterns: the M2 candidate patterns — the source of each pattern's supporting
                ``prediction_id``s (so the M3 output is not modified to carry evidence).

        Raises:
            InvalidValidationError: the validation input is malformed.
            UnsupportedVersionError: a validation/pattern version is unsupported.
            MissingEvidenceError: a validated pattern has no matching candidate evidence.
        """
        started = time.perf_counter()
        self._validate_inputs(validation, patterns)
        evidence = {p.pattern_id: p for p in patterns}

        validated = [v for v in validation.validated_patterns
                     if v.status is LearningStatus.VALIDATED]
        # No VALIDATED patterns ⇒ the honest answer is "insufficient data" — fabricate nothing.
        if not validated:
            return self._result((), validation, processed=0, started=started,
                                status=LearningStatus.INSUFFICIENT_DATA)

        recommendations: list[Recommendation] = []
        for pattern in validated:
            candidate = evidence.get(pattern.pattern_key)
            if candidate is None or not candidate.prediction_ids:
                raise MissingEvidenceError(
                    f"{pattern.pattern_key}: no supporting evidence for a validated pattern"
                )
            recommendations.append(self._build(pattern, candidate, validation.correction_method))

        # Deterministic order + a duplicate-identity guard.
        recommendations.sort(key=lambda r: r.recommendation_key)
        keys = [r.recommendation_key for r in recommendations]
        if len(keys) != len(set(keys)):
            # Collapse exact duplicates (same validated pattern seen twice) deterministically.
            seen: dict[str, Recommendation] = {}
            for rec in recommendations:
                seen.setdefault(rec.recommendation_key, rec)
            recommendations = sorted(seen.values(), key=lambda r: r.recommendation_key)

        return self._result(tuple(recommendations), validation, processed=len(validated),
                            started=started, status=LearningStatus.VALIDATED)

    # ---------------------------------------------------------------- internals
    def _build(
        self, pattern: ValidatedPattern, candidate: CandidatePattern, correction_method: str
    ) -> Recommendation:
        rec_type = recommendation_type_of(pattern)
        category = recommendation_category(pattern.grouping_key)
        key = _recommendation_key(pattern.learning_version, pattern.pattern_key, rec_type.value)
        rec_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
        pattern_hash = hashlib.sha256(pattern.pattern_key.encode("utf-8")).hexdigest()
        return Recommendation(
            recommendation_id=rec_hash[:16], recommendation_key=key, recommendation_hash=rec_hash,
            learning_version=pattern.learning_version, dataset_version=pattern.dataset_version,
            pattern_key=pattern.pattern_key, pattern_hash=pattern_hash, recommendation_type=rec_type,
            recommendation_category=category, title=_title(pattern, category, rec_type),
            summary=_summary(pattern),
            detailed_explanation=_detailed_explanation(pattern, correction_method),
            statistical_basis=_statistical_basis(pattern, correction_method),
            evidence_count=pattern.evidence_count, sample_size=pattern.sample_size,
            confidence_interval=pattern.confidence_interval, significance=pattern.significance,
            consistency_score=pattern.consistency_score,
            recommendation_confidence=recommendation_confidence(pattern),
            supporting_prediction_ids=tuple(candidate.prediction_ids),
            limitations=_limitations(pattern, rec_type),
        )

    def _validate_inputs(
        self, validation: Any, patterns: Sequence[CandidatePattern]
    ) -> None:
        if not isinstance(validation, ValidationResult):
            raise InvalidValidationError("expected a ValidationResult")
        if (validation.learning_version != LEARNING_VERSION
                or validation.dataset_version != DATASET_VERSION):
            raise UnsupportedVersionError(
                f"unsupported validation versions "
                f"{validation.learning_version}/{validation.dataset_version}"
            )
        for pattern in validation.validated_patterns:
            if not isinstance(pattern, ValidatedPattern):
                raise InvalidValidationError("validation contains a non-ValidatedPattern")
            if not isinstance(pattern.confidence_interval, ConfidenceInterval):
                raise InvalidValidationError(f"{pattern.pattern_key}: malformed confidence interval")
        for candidate in patterns:
            if not isinstance(candidate, CandidatePattern):
                raise InvalidValidationError("expected CandidatePattern evidence")

    def _result(
        self, recommendations: tuple[Recommendation, ...], validation: ValidationResult, *,
        processed: int, started: float, status: LearningStatus,
    ) -> RecommendationResult:
        distribution = {c.value: 0 for c in RecommendationConfidence}
        for rec in recommendations:
            distribution[rec.recommendation_confidence.value] += 1
        duration_ms = (time.perf_counter() - started) * 1000
        result = RecommendationResult(
            recommendations=recommendations, status=status,
            validated_patterns_processed=processed, recommendations_created=len(recommendations),
            rejected=processed - len(recommendations), confidence_distribution=distribution,
            learning_version=validation.learning_version, dataset_version=validation.dataset_version,
            checksum=_checksum(recommendations), generation_duration_ms=duration_ms,
        )
        logger.info(
            "recommendation generation: validated=%d created=%d rejected=%d confidence=%s "
            "status=%s in %.1fms",
            processed, len(recommendations), result.rejected, distribution, status.value, duration_ms,
        )
        return result
