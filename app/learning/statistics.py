"""Statistical Validation Engine — turn candidate patterns into validated learning artifacts
(Sprint 4 · Volume 15 · Milestone 3).

This is the **first** Learning milestone that performs statistics. It takes the deterministic
candidate patterns (M2) and the Learning Dataset (M1) and, for each pattern, computes descriptive
statistics, a 95% confidence interval, a significance test, and — across the whole family — a
**multiple-comparison correction**, then classifies each pattern as ``VALIDATED`` /
``HYPOTHESIS`` / ``INSUFFICIENT_DATA``. It generates **no recommendations**, exposes **no HTTP**,
and does **no** GPT integration (those are later milestones).

It is a **pure, read-only transform**: it reads the (frozen) dataset + patterns, writes nothing,
and imports neither the Prediction nor the Outcome engine.

**Reuse, don't reinvent (§4.4 of the plan).** The base rollups (win rate, avg R, expectancy,
profit factor, max drawdown, holding) are computed by the **same Sprint 2 aggregate math**
(`app.memory.aggregates._metrics`) that populates ``memory_aggregates`` — so a validated pattern's
numbers agree with the stored aggregates for the same records (asserted by a regression test).
This milestone **adds** only what the aggregates lack: confidence intervals, significance,
multiple-comparison correction, and a consistency (stability) check.

Honesty gates (the whole point of the milestone):
- **Nothing below the sample floor.** ``sample_size < min_sample`` ⇒ ``INSUFFICIENT_DATA``.
- **Intervals, not point estimates.** Every rate carries a 95% Wilson interval.
- **Significance + correction.** A pattern is ``VALIDATED`` only if it is significant *after*
  multiple-comparison correction **and** its interval excludes the baseline (a CI straddling a
  coin flip is not actionable). Weak evidence is never promoted.
- **Deterministic + thread-safe + idempotent.** Identical inputs ⇒ identical results.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from types import SimpleNamespace
from typing import Any, Callable, Sequence

# Reuse the Sprint 2 aggregate math (single source of truth for the base rollups). We import the
# private `_metrics` deliberately: it is the exact per-group computation behind `memory_aggregates`,
# so validated stats cannot drift from the stored aggregates. Read-only; nothing here mutates it.
from app.memory.aggregates import _metrics as _memory_metrics
from app.learning.models import (
    DATASET_VERSION,
    LEARNING_VERSION,
    CandidatePattern,
    ConfidenceInterval,
    InconsistentEvidenceError,
    InvalidDatasetError,
    LearningDataset,
    LearningRecord,
    LearningStatus,
    MalformedPatternError,
    Significance,
    StatisticsError,
    UnknownCorrectionError,
    UnsupportedVersionError,
    ValidatedPattern,
    ValidationResult,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)

#: Minimum resolved trades before a pattern is eligible for a VALIDATED verdict. Below it the
#: honest output is INSUFFICIENT_DATA (aligned with the platform's small-sample aversion).
DEFAULT_MIN_SAMPLE: int = 30
#: Significance threshold (two-sided) for the raw test and the multiple-comparison correction.
DEFAULT_ALPHA: float = 0.05
#: Null win-rate a pattern is tested against (a coin flip by default).
DEFAULT_BASELINE: float = 0.5
#: Default multiple-comparison correction strategy.
DEFAULT_CORRECTION: str = "benjamini_hochberg"
#: Chronological sub-periods used for the consistency (stability) check.
DEFAULT_PERIODS: int = 2
#: z for a two-sided 95% interval.
_Z_95: float = 1.959963984540054


# ------------------------------------------------------------------ statistical primitives
def _norm_cdf(x: float) -> float:
    """Standard-normal CDF via the error function (deterministic, no external deps)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def wilson_interval(wins: int, n: int, *, z: float = _Z_95) -> tuple[float, float]:
    """The Wilson score interval for a binomial proportion — well-behaved for small ``n`` and
    proportions near 0/1 (unlike the normal approximation). Returns ``(low, high)`` clamped to
    ``[0, 1]``."""
    if n <= 0:
        return (0.0, 1.0)
    phat = wins / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (phat + z2 / (2.0 * n)) / denom
    margin = (z * math.sqrt((phat * (1.0 - phat) + z2 / (4.0 * n)) / n)) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def ci_quality(width: float) -> str:
    """A coarse label of a confidence interval's width (narrower ⇒ more actionable)."""
    if width < 0.20:
        return "HIGH"
    if width < 0.40:
        return "MODERATE"
    return "LOW"


def proportion_ztest(wins: int, n: int, baseline: float) -> tuple[float, float]:
    """Two-sided one-sample proportion z-test of ``wins/n`` against ``baseline``.

    Returns ``(z_score, p_value)``. A degenerate baseline (0 or 1) has zero null variance; then
    a matching rate is not significant (z=0, p=1) and any difference is fully significant
    (z=±inf, p=0)."""
    if n <= 0:
        return (0.0, 1.0)
    phat = wins / n
    var = baseline * (1.0 - baseline)
    if var <= 0.0:
        if phat == baseline:
            return (0.0, 1.0)
        return (math.inf if phat > baseline else -math.inf, 0.0)
    z = (phat - baseline) / math.sqrt(var / n)
    p = 2.0 * (1.0 - _norm_cdf(abs(z)))
    return (z, max(0.0, min(1.0, p)))


def consistency_score(records: Sequence[LearningRecord], n_periods: int) -> float | None:
    """Stability of the win rate across chronological sub-periods (1.0 = identical across
    periods, lower = curve-fit to one window). ``None`` when there is too little data to split.

    Splits the records into up to ``n_periods`` contiguous, time-ordered chunks and returns
    ``1 - (max sub-period win rate - min sub-period win rate)``."""
    if n_periods < 2 or len(records) < 2:
        return None
    ordered = sorted(records, key=lambda r: (r.prediction_timestamp or "", r.prediction_id))
    n = len(ordered)
    k = min(n_periods, n)
    rates: list[float] = []
    for idx in range(k):
        chunk = ordered[idx * n // k:(idx + 1) * n // k]
        if not chunk:
            continue
        wins = sum(1 for r in chunk if float(r.realised_r) > 0)
        rates.append(wins / len(chunk))
    if len(rates) < 2:
        return None
    return 1.0 - (max(rates) - min(rates))


# ------------------------------------------------------------------ multiple-comparison correction
def _bonferroni(pvals: Sequence[float], alpha: float) -> list[bool]:
    """Bonferroni: control the family-wise error rate (conservative)."""
    m = len(pvals)
    threshold = alpha / m if m else alpha
    return [p <= threshold for p in pvals]


def _benjamini_hochberg(pvals: Sequence[float], alpha: float) -> list[bool]:
    """Benjamini–Hochberg: control the false-discovery rate (less conservative than Bonferroni)."""
    m = len(pvals)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: pvals[i])
    k_max = 0
    for rank, i in enumerate(order, start=1):
        if pvals[i] <= (rank / m) * alpha:
            k_max = rank
    rejected = [False] * m
    for rank, i in enumerate(order, start=1):
        if rank <= k_max:
            rejected[i] = True
    return rejected


def _no_correction(pvals: Sequence[float], alpha: float) -> list[bool]:
    """No correction — raw per-test threshold (use only when a single hypothesis is tested)."""
    return [p <= alpha for p in pvals]


#: Extensible registry of correction strategies: name → (p-values, alpha) → per-pattern verdicts.
#: Add a strategy by registering a callable here — existing interfaces are unchanged.
CORRECTION_STRATEGIES: dict[str, Callable[[Sequence[float], float], list[bool]]] = {
    "benjamini_hochberg": _benjamini_hochberg,
    "bonferroni": _bonferroni,
    "none": _no_correction,
}


def available_corrections() -> list[str]:
    """The correction strategies the validator understands (stable order)."""
    return list(CORRECTION_STRATEGIES)


def _adapt(record: LearningRecord) -> SimpleNamespace:
    """Project a LearningRecord onto the attribute names the Sprint 2 `_metrics` reads
    (``realised_r`` / ``holding_bars`` / ``created_at`` / ``prediction_id``) — so the base
    rollups are computed by the *same* code that fills ``memory_aggregates``."""
    return SimpleNamespace(
        realised_r=record.realised_r,
        holding_bars=record.holding_period,
        created_at=record.prediction_timestamp,
        prediction_id=record.prediction_id,
    )


def _checksum(patterns: Sequence[ValidatedPattern]) -> str:
    payload = json.dumps([p.stable_dict() for p in patterns], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class StatisticalValidator:
    """Validates candidate patterns into statistically classified learning artifacts (read-only).

    Deterministic, thread-safe (no shared mutable state; pure over its inputs), and idempotent:
    the same dataset + patterns + configuration always produce the same :class:`ValidationResult`.
    """

    def __init__(
        self,
        *,
        min_sample: int = DEFAULT_MIN_SAMPLE,
        alpha: float = DEFAULT_ALPHA,
        baseline: float = DEFAULT_BASELINE,
        correction: str = DEFAULT_CORRECTION,
        n_periods: int = DEFAULT_PERIODS,
    ) -> None:
        """Configure the validator.

        Args:
            min_sample: minimum resolved trades for a VALIDATED verdict (else INSUFFICIENT_DATA).
            alpha: two-sided significance threshold (and correction level).
            baseline: null win rate a pattern is tested against (default: 0.5, a coin flip).
            correction: multiple-comparison strategy (see :data:`CORRECTION_STRATEGIES`).
            n_periods: chronological sub-periods for the consistency check.

        Raises:
            UnknownCorrectionError: an unregistered correction strategy was requested.
        """
        if correction not in CORRECTION_STRATEGIES:
            raise UnknownCorrectionError(
                f"unknown correction {correction!r}; known: {', '.join(CORRECTION_STRATEGIES)}"
            )
        if not 0.0 < alpha < 1.0:
            raise ValueError("alpha must be in (0, 1)")
        if not 0.0 <= baseline <= 1.0:
            raise ValueError("baseline must be in [0, 1]")
        self.min_sample = min_sample
        self.alpha = alpha
        self.baseline = baseline
        self.correction = correction
        self.n_periods = n_periods

    def validate(
        self, dataset: LearningDataset, patterns: Sequence[CandidatePattern]
    ) -> ValidationResult:
        """Validate candidate patterns against the Learning Dataset.

        Raises:
            InvalidDatasetError: the dataset is malformed.
            MalformedPatternError: a pattern is not a well-formed CandidatePattern.
            UnsupportedVersionError: a dataset/pattern version is unsupported.
            InconsistentEvidenceError: a pattern references evidence absent from the dataset.
            StatisticsError: a statistic could not be computed (non-finite evidence).
        """
        started = time.perf_counter()
        self._validate_inputs(dataset, patterns)

        # A thin/empty corpus (or no patterns) is not an error — it is the honest young-system
        # answer: fabricate no statistical conclusions.
        if dataset.status is LearningStatus.INSUFFICIENT_DATA or not patterns:
            return self._result((), dataset, hypotheses_tested=len(patterns),
                                 status=LearningStatus.INSUFFICIENT_DATA, started=started)

        index = {r.prediction_id: r for r in dataset.records}
        computed: list[dict[str, Any]] = []
        for pattern in patterns:
            records = self._records_for(pattern, index)
            base = _memory_metrics([_adapt(r) for r in records])
            n = int(base["n_resolved"])
            if n <= 0 or base["win_rate"] != base["win_rate"]:      # n==0 or NaN win rate
                raise StatisticsError(f"{pattern.pattern_id}: corrupted statistics (n={n})")
            low, high = wilson_interval(int(base["wins"]), n)
            z, p_value = proportion_ztest(int(base["wins"]), n, self.baseline)
            computed.append({
                "pattern": pattern, "records": records, "base": base, "n": n,
                "ci_low": low, "ci_high": high, "z": z, "p_value": p_value,
                "consistency": consistency_score(records, self.n_periods),
            })

        # Multiple-comparison correction is applied across the WHOLE family of tested patterns.
        p_values = [c["p_value"] for c in computed]
        corrected = CORRECTION_STRATEGIES[self.correction](p_values, self.alpha)

        validated: list[ValidatedPattern] = [
            self._build(item, corrected_significant) for item, corrected_significant in zip(computed, corrected)
        ]
        validated.sort(key=lambda v: v.pattern_key)

        v_count = sum(1 for v in validated if v.status is LearningStatus.VALIDATED)
        h_count = sum(1 for v in validated if v.status is LearningStatus.HYPOTHESIS)
        i_count = sum(1 for v in validated if v.status is LearningStatus.INSUFFICIENT_DATA)
        if v_count:
            run_status = LearningStatus.VALIDATED
        elif h_count:
            run_status = LearningStatus.HYPOTHESIS
        else:
            run_status = LearningStatus.INSUFFICIENT_DATA

        return self._result(tuple(validated), dataset, hypotheses_tested=len(patterns),
                            status=run_status, started=started,
                            counts=(v_count, h_count, i_count))

    # ---------------------------------------------------------------- internals
    def _build(self, item: dict[str, Any], corrected_significant: bool) -> ValidatedPattern:
        pattern: CandidatePattern = item["pattern"]
        base = item["base"]
        n = item["n"]
        width = item["ci_high"] - item["ci_low"]
        ci = ConfidenceInterval(low=item["ci_low"], high=item["ci_high"], width=width,
                                quality=ci_quality(width))
        sig = Significance(p_value=item["p_value"], z_score=item["z"], baseline=self.baseline,
                           significant=item["p_value"] <= self.alpha)
        status = self._classify(n, ci, corrected_significant)
        return ValidatedPattern(
            pattern_key=pattern.pattern_id, learning_version=pattern.learning_version,
            dataset_version=pattern.dataset_version, pattern_type=pattern.pattern_type,
            grouping_key=pattern.grouping_key, grouping_value=pattern.grouping_value,
            sample_size=n, wins=int(base["wins"]), losses=int(base["losses"]),
            win_rate=base["win_rate"], loss_rate=int(base["losses"]) / n,
            average_r=base["avg_r"], expectancy=base["expectancy"],
            profit_factor=base["profit_factor"], max_drawdown_r=base["max_drawdown_r"],
            avg_holding_bars=base["avg_holding_bars"], confidence_interval=ci, significance=sig,
            correction_method=self.correction, correction_significant=corrected_significant,
            consistency_score=item["consistency"], status=status,
            evidence_count=pattern.evidence_count,
        )

    def _classify(self, n: int, ci: ConfidenceInterval, corrected_significant: bool) -> LearningStatus:
        """Promote to VALIDATED only when ALL honesty gates pass; never promote weak evidence."""
        if n < self.min_sample:
            return LearningStatus.INSUFFICIENT_DATA
        excludes_baseline = ci.low > self.baseline or ci.high < self.baseline
        if corrected_significant and excludes_baseline:
            return LearningStatus.VALIDATED
        return LearningStatus.HYPOTHESIS

    def _records_for(
        self, pattern: CandidatePattern, index: dict[str, LearningRecord]
    ) -> list[LearningRecord]:
        records: list[LearningRecord] = []
        for pid in pattern.prediction_ids:
            record = index.get(pid)
            if record is None:
                raise InconsistentEvidenceError(
                    f"{pattern.pattern_id}: evidence {pid!r} not present in the dataset"
                )
            realised = float(record.realised_r)
            if realised != realised or realised in (math.inf, -math.inf):
                raise StatisticsError(f"{pattern.pattern_id}: non-finite realised R for {pid!r}")
            records.append(record)
        if len(records) != pattern.evidence_count:
            raise InconsistentEvidenceError(
                f"{pattern.pattern_id}: evidence_count {pattern.evidence_count} != {len(records)} records"
            )
        return records

    def _validate_inputs(
        self, dataset: Any, patterns: Sequence[CandidatePattern]
    ) -> None:
        if not isinstance(dataset, LearningDataset):
            raise InvalidDatasetError("expected a LearningDataset")
        if dataset.learning_version != LEARNING_VERSION or dataset.dataset_version != DATASET_VERSION:
            raise UnsupportedVersionError(
                f"unsupported dataset versions {dataset.learning_version}/{dataset.dataset_version}"
            )
        for record in dataset.records:
            if not isinstance(record, LearningRecord):
                raise InvalidDatasetError("dataset contains a non-LearningRecord row")
        for pattern in patterns:
            if not isinstance(pattern, CandidatePattern):
                raise MalformedPatternError("expected a CandidatePattern")
            if (pattern.learning_version != LEARNING_VERSION
                    or pattern.dataset_version != DATASET_VERSION):
                raise UnsupportedVersionError(
                    f"{pattern.pattern_id}: unsupported pattern versions "
                    f"{pattern.learning_version}/{pattern.dataset_version}"
                )

    def _result(
        self, validated: tuple[ValidatedPattern, ...], dataset: LearningDataset, *,
        hypotheses_tested: int, status: LearningStatus, started: float,
        counts: tuple[int, int, int] | None = None,
    ) -> ValidationResult:
        v_count, h_count, i_count = counts if counts is not None else (0, 0, 0)
        duration_ms = (time.perf_counter() - started) * 1000
        result = ValidationResult(
            validated_patterns=validated, status=status, corpus_size=dataset.corpus_size,
            validated_count=v_count, hypothesis_count=h_count, insufficient_count=i_count,
            hypotheses_tested=hypotheses_tested, correction_method=self.correction,
            min_sample=self.min_sample, alpha=self.alpha, baseline=self.baseline,
            learning_version=dataset.learning_version, dataset_version=dataset.dataset_version,
            checksum=_checksum(validated), validation_duration_ms=duration_ms,
        )
        logger.info(
            "statistical validation: corpus=%d tested=%d validated=%d rejected=%d correction=%s "
            "alpha=%.3f min_sample=%d status=%s in %.1fms",
            dataset.corpus_size, hypotheses_tested, v_count, h_count + i_count, self.correction,
            self.alpha, self.min_sample, status.value, duration_ms,
        )
        return result
