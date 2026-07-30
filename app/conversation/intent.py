"""Intent Detection Engine for the Conversation Intelligence Engine (Sprint 6 · Milestone 2).

Deterministic, **rule-based** routing of a user request into a well-defined conversation intent. It
determines **what the user wants**, not how to answer: it performs **classification only** — it does
**not** call an LLM, use embeddings or semantic search, retrieve any data, call the Decision
Intelligence API, build prompts, or generate responses.

Everything here is pure and reproducible: the same input always produces the same intent, the same
matched rules, the same extracted entities, and the same classification confidence (a rule-match
strength — **never** to be confused with Decision Intelligence confidence). The intent **registry**
is extensible (add/adjust a spec without touching the classifier). Imports nothing from any engine.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

#: The intent-detection method/schema version. A rule/shape change is a new version, never an edit.
INTENT_VERSION: str = "int-1"

_PHRASE_WEIGHT = 0.9
_KEYWORD_WEIGHT = 0.7
_SYNONYM_WEIGHT = 0.6

#: Matches a ticker with an explicit suffix (`.NS`) or a `USDT` pair — avoids matching plain words.
_SYMBOL_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,11}\.[A-Z]{1,4}|[A-Z]{2,10}USDT)\b")
#: Matches a long hex identifier (a stored `prediction_id` / `decision_id`).
_PREDICTION_ID_RE = re.compile(r"\b([0-9a-fA-F]{16,64})\b")


# --------------------------------------------------------------------------- enums / errors
class Intent(str, Enum):
    """The supported conversation intents (`UNKNOWN` is the deterministic fallback)."""

    EXPLAIN_PREDICTION = "EXPLAIN_PREDICTION"
    SHOW_EVIDENCE = "SHOW_EVIDENCE"
    WHY_CONFIDENCE = "WHY_CONFIDENCE"
    HISTORICAL_COMPARISON = "HISTORICAL_COMPARISON"
    SIMILAR_CASES = "SIMILAR_CASES"
    LEARNING_SUMMARY = "LEARNING_SUMMARY"
    DECISION_SUMMARY = "DECISION_SUMMARY"
    HEALTH = "HEALTH"
    VERSION = "VERSION"
    HELP = "HELP"
    UNKNOWN = "UNKNOWN"


class IntentError(Exception):
    """Base class for intent-detection errors."""


class InvalidIntentInputError(IntentError):
    """The classifier input is not a string."""


class UnknownIntentSpecError(IntentError):
    """A registry lookup for an intent that has no spec."""


_ORDER = list(Intent)


# --------------------------------------------------------------------------- registry
@dataclass(frozen=True)
class IntentSpec:
    """A registry entry: how an intent is recognised + what it requires. Extensible — add or adjust
    a spec without changing the classifier."""

    intent: Intent
    description: str
    phrases: tuple[str, ...] = ()          # multi-word substrings (strongest signal)
    keywords: tuple[str, ...] = ()          # whole-word tokens
    synonyms: tuple[str, ...] = ()          # whole-word alternates (weaker)
    required_entities: tuple[str, ...] = ()  # e.g. ("subject",) — a prediction_id or symbol
    supported_parameters: tuple[str, ...] = ()
    priority: int = 100                     # lower wins ties


#: The intent registry. The classifier iterates this — extend it without editing classifier logic.
INTENT_REGISTRY: dict[Intent, IntentSpec] = {
    Intent.HEALTH: IntentSpec(
        Intent.HEALTH, "System / service readiness.",
        phrases=("system status", "is the system up", "health check", "are you online"),
        keywords=("health", "status"), synonyms=("alive", "online"), priority=10),
    Intent.VERSION: IntentSpec(
        Intent.VERSION, "The engine / API versions.",
        phrases=("what version", "which version"), keywords=("version",), priority=10),
    Intent.HELP: IntentSpec(
        Intent.HELP, "What the assistant can do.",
        phrases=("what can you do", "help me", "how do i use", "list commands"),
        keywords=("help", "commands"), priority=10),
    Intent.EXPLAIN_PREDICTION: IntentSpec(
        Intent.EXPLAIN_PREDICTION, "Explain a prediction.",
        phrases=("explain this prediction", "explain the prediction", "explain prediction",
                 "what does this prediction mean"),
        keywords=("explain", "prediction"), synonyms=("predict",), required_entities=("subject",),
        supported_parameters=("prediction_id", "symbol"), priority=20),
    Intent.SHOW_EVIDENCE: IntentSpec(
        Intent.SHOW_EVIDENCE, "Show the supporting evidence.",
        phrases=("show evidence", "show me the evidence", "what evidence", "what is the evidence"),
        keywords=("evidence",), synonyms=("proof", "support"), required_entities=("subject",),
        supported_parameters=("prediction_id", "symbol"), priority=20),
    Intent.WHY_CONFIDENCE: IntentSpec(
        Intent.WHY_CONFIDENCE, "Why the (evidence-quality) confidence is what it is.",
        phrases=("why confidence", "why the confidence", "how confident", "how strong is the evidence"),
        keywords=("confidence",), synonyms=("confident", "trustworthy"),
        required_entities=("subject",), supported_parameters=("prediction_id", "symbol"), priority=20),
    Intent.DECISION_SUMMARY: IntentSpec(
        Intent.DECISION_SUMMARY, "Summarise the composed decision.",
        phrases=("decision summary", "overall decision", "summarise the decision",
                 "summarize the decision", "what does the system think"),
        keywords=("decision",), synonyms=("overall", "summary"), required_entities=("subject",),
        supported_parameters=("prediction_id", "symbol"), priority=35),
    Intent.HISTORICAL_COMPARISON: IntentSpec(
        Intent.HISTORICAL_COMPARISON, "How similar setups fared historically.",
        phrases=("historical comparison", "how did it do historically", "historical behaviour",
                 "historical behavior", "compared to history"),
        keywords=("historical", "history"), required_entities=("subject",),
        supported_parameters=("prediction_id", "symbol"), priority=30),
    Intent.SIMILAR_CASES: IntentSpec(
        Intent.SIMILAR_CASES, "Similar historical cases.",
        phrases=("similar cases", "similar setups", "seen this before", "similar predictions"),
        keywords=("similar",), synonyms=("neighbours", "neighbors"), required_entities=("subject",),
        supported_parameters=("prediction_id", "symbol"), priority=30),
    Intent.LEARNING_SUMMARY: IntentSpec(
        Intent.LEARNING_SUMMARY, "The Learning Engine's observations.",
        phrases=("learning summary", "what has it learned", "learning observations"),
        keywords=("learning",), synonyms=("learned", "patterns"), priority=40),
    Intent.UNKNOWN: IntentSpec(Intent.UNKNOWN, "Unrecognised request (fallback).", priority=999),
}


def available_intents() -> list[Intent]:
    """The intents the registry understands (stable order)."""
    return list(INTENT_REGISTRY)


def spec_for(intent: Intent, registry: "Mapping[Intent, IntentSpec] | None" = None) -> IntentSpec:
    reg = registry or INTENT_REGISTRY
    if intent not in reg:
        raise UnknownIntentSpecError(f"no spec for {intent}")
    return reg[intent]


# --------------------------------------------------------------------------- results
@dataclass(frozen=True)
class IntentValidation:
    """Deterministic validation of a classification against its intent's requirements."""

    valid: bool
    missing_entities: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()

    def stable_dict(self) -> dict[str, Any]:
        return {"valid": self.valid, "missing_entities": list(self.missing_entities),
                "issues": list(self.issues)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "IntentValidation":
        return cls(valid=bool(data.get("valid")),
                   missing_entities=tuple(data.get("missing_entities") or ()),
                   issues=tuple(data.get("issues") or ()))


@dataclass(frozen=True)
class IntentClassification:
    """The deterministic result of classifying one user request.

    `confidence` is the **rule-match strength** (0..1) — classification confidence only, never to be
    confused with Decision Intelligence confidence."""

    intent: Intent
    confidence: float
    matched_rules: tuple[str, ...]
    entities: dict[str, str]
    validation: IntentValidation
    version: str = INTENT_VERSION

    def stable_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.value, "confidence": round(self.confidence, 4),
            "matched_rules": sorted(self.matched_rules),
            "entities": {k: self.entities[k] for k in sorted(self.entities)},
            "validation": self.validation.stable_dict(), "version": self.version,
        }

    to_dict = stable_dict

    def serialize(self) -> str:
        return json.dumps(self.stable_dict(), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "IntentClassification":
        return cls(
            intent=Intent(data.get("intent")), confidence=float(data.get("confidence", 0.0)),
            matched_rules=tuple(data.get("matched_rules") or ()),
            entities=dict(data.get("entities") or {}),
            validation=IntentValidation.from_dict(data.get("validation") or {}),
            version=data.get("version", INTENT_VERSION),
        )


# --------------------------------------------------------------------------- the classifier
def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def extract_entities(text: str, context: "Mapping[str, Any] | None" = None) -> dict[str, str]:
    """Deterministically extract a `prediction_id` (long hex) and/or `symbol` (ticker) from the raw
    text; a supplied `context` fills anything the text omits (never invents)."""
    entities: dict[str, str] = {}
    pid = _PREDICTION_ID_RE.search(text)
    if pid:
        entities["prediction_id"] = pid.group(1)
    sym = _SYMBOL_RE.search(text)
    if sym:
        entities["symbol"] = sym.group(1)
    for key in ("prediction_id", "symbol"):
        if key not in entities and context and context.get(key):
            entities[key] = str(context[key])
    return entities


class IntentClassifier:
    """Deterministic, rule-based intent classifier (no LLM, no embeddings, no retrieval)."""

    def __init__(self, registry: "Mapping[Intent, IntentSpec] | None" = None) -> None:
        self.registry: Mapping[Intent, IntentSpec] = registry or INTENT_REGISTRY

    def classify(self, text: str, *, context: "Mapping[str, Any] | None" = None) -> IntentClassification:
        """Classify a user request into a deterministic intent + entities + validation.

        Raises:
            InvalidIntentInputError: `text` is not a string.
        """
        if not isinstance(text, str):
            raise InvalidIntentInputError("intent input must be a string")
        entities = extract_entities(text, context)
        norm = _normalise(text)
        if not norm:
            return IntentClassification(Intent.UNKNOWN, 0.0, (), entities,
                                        IntentValidation(False, (), ("empty input",)))

        tokens = set(re.findall(r"[a-z0-9]+", norm))
        candidates: list[tuple[float, int, int, Intent, tuple[str, ...]]] = []
        for intent, spec in self.registry.items():
            if intent is Intent.UNKNOWN:
                continue
            matched, score = self._match(spec, norm, tokens)
            if matched:
                candidates.append((score, spec.priority, _ORDER.index(intent), intent, tuple(matched)))

        if not candidates:
            return IntentClassification(Intent.UNKNOWN, 0.0, (), entities, IntentValidation(True))
        # deterministic winner: highest score, then lowest priority, then registry order
        candidates.sort(key=lambda c: (-c[0], c[1], c[2]))
        score, _prio, _idx, intent, matched = candidates[0]
        validation = self._validate(self.registry[intent], entities)
        return IntentClassification(intent, round(score, 4), tuple(sorted(matched)), entities, validation)

    @staticmethod
    def _match(spec: IntentSpec, norm: str, tokens: set[str]) -> tuple[list[str], float]:
        matched: list[str] = []
        weights: list[float] = []
        for phrase in spec.phrases:
            if phrase in norm:
                matched.append(phrase)
                weights.append(_PHRASE_WEIGHT)
        for keyword in spec.keywords:
            if keyword in tokens:
                matched.append(keyword)
                weights.append(_KEYWORD_WEIGHT)
        for synonym in spec.synonyms:
            if synonym in tokens:
                matched.append(synonym)
                weights.append(_SYNONYM_WEIGHT)
        if not matched:
            return [], 0.0
        score = min(1.0, max(weights) + 0.05 * (len(matched) - 1))
        return matched, score

    @staticmethod
    def _validate(spec: IntentSpec, entities: Mapping[str, str]) -> IntentValidation:
        missing: list[str] = []
        for required in spec.required_entities:
            if required == "subject":
                if not (entities.get("prediction_id") or entities.get("symbol")):
                    missing.append("subject")
            elif required not in entities:
                missing.append(required)
        return IntentValidation(valid=not missing, missing_entities=tuple(missing))
