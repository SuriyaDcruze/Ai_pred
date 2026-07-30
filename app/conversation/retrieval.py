"""Retrieval Orchestrator for the Conversation Intelligence Engine (Sprint 6 · Milestone 3).

Connects the conversation layer to the **completed Decision Intelligence Engine** and assembles the
deterministic information a conversation needs into a **retrieval payload**. It performs **retrieval
only**: it never generates responses, builds prompts, calls an LLM, classifies intents, computes
evidence, or calculates confidence — it *coordinates* retrieval and *merges* existing Decision
Intelligence data verbatim.

It reaches the Decision Intelligence Engine **only through a transport-independent source adapter**
(:class:`DecisionIntelligenceSource`), so REST / in-process / RPC transports are interchangeable
without changing orchestration logic. It **never** accesses the Prediction, Memory, Similarity, or
Learning engines directly — only Decision Intelligence — and imports no engine at all (this core
module depends only on the M1/M2 conversation types + stdlib).
"""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

from app.conversation.intent import Intent, IntentClassification
from app.conversation.models import Citation, ConversationContext

#: The Retrieval Orchestrator method/schema version.
RETRIEVAL_VERSION: str = "ret-1"
_SOURCE = "decision_intelligence"


# --------------------------------------------------------------------------- enums / errors
class RetrievalTarget(str, Enum):
    """What the orchestrator can retrieve — all from Decision Intelligence interfaces only."""

    DECISION_SUMMARY = "DECISION_SUMMARY"
    EVIDENCE = "EVIDENCE"
    EXPLANATION = "EXPLANATION"
    COMPOSITE_CONFIDENCE = "COMPOSITE_CONFIDENCE"
    HISTORICAL_CONTEXT = "HISTORICAL_CONTEXT"
    SIMILAR_CASES = "SIMILAR_CASES"
    LEARNING_SUMMARY = "LEARNING_SUMMARY"
    HEALTH = "HEALTH"
    VERSION = "VERSION"


class RetrievalAvailability(str, Enum):
    """The honesty vocabulary for a retrieved component (never fabricated)."""

    AVAILABLE = "AVAILABLE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    NOT_SUPPORTED = "NOT_SUPPORTED"
    ERROR = "ERROR"


class RetrievalError(Exception):
    """Base class for retrieval-orchestration errors."""


class InvalidRetrievalRequestError(RetrievalError):
    """The classification handed to the orchestrator is malformed."""


#: Which Decision Intelligence targets each intent needs. Extend without touching the pipeline.
RETRIEVAL_ROUTING: dict[Intent, tuple[RetrievalTarget, ...]] = {
    Intent.EXPLAIN_PREDICTION: (RetrievalTarget.DECISION_SUMMARY, RetrievalTarget.EXPLANATION),
    Intent.SHOW_EVIDENCE: (RetrievalTarget.EVIDENCE,),
    Intent.WHY_CONFIDENCE: (RetrievalTarget.COMPOSITE_CONFIDENCE,),
    Intent.HISTORICAL_COMPARISON: (RetrievalTarget.HISTORICAL_CONTEXT,),
    Intent.SIMILAR_CASES: (RetrievalTarget.SIMILAR_CASES,),
    Intent.LEARNING_SUMMARY: (RetrievalTarget.LEARNING_SUMMARY,),
    Intent.DECISION_SUMMARY: (RetrievalTarget.DECISION_SUMMARY, RetrievalTarget.EVIDENCE,
                              RetrievalTarget.COMPOSITE_CONFIDENCE),
    Intent.HEALTH: (RetrievalTarget.HEALTH,),
    Intent.VERSION: (RetrievalTarget.VERSION,),
    Intent.HELP: (),
    Intent.UNKNOWN: (),
}

#: Targets that need a subject (a prediction) vs. the standalone status targets.
_STANDALONE = {RetrievalTarget.HEALTH, RetrievalTarget.VERSION}
#: A composed section's status → the retrieval availability it maps to.
_AVAIL_FROM_STATUS = {
    "COMPLETE": RetrievalAvailability.AVAILABLE, "PARTIAL": RetrievalAvailability.AVAILABLE,
    "INSUFFICIENT_DATA": RetrievalAvailability.INSUFFICIENT_DATA,
    "EMPTY": RetrievalAvailability.NOT_AVAILABLE, "ERROR": RetrievalAvailability.ERROR,
}


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


# --------------------------------------------------------------------------- source adapter
class DecisionIntelligenceSource(ABC):
    """Transport-independent access to the Decision Intelligence Engine. Concrete adapters (in-process,
    REST, RPC) implement these; the orchestrator depends only on this interface."""

    @abstractmethod
    def fetch_decision(self, prediction_id: str) -> dict[str, Any] | None:
        """The full `/intelligence/{prediction_id}` payload, or ``None`` if not found."""

    @abstractmethod
    def fetch_decision_by_symbol(self, symbol: str) -> dict[str, Any] | None:
        """The full payload for a symbol's latest prediction, or ``None`` if none."""

    @abstractmethod
    def fetch_health(self) -> dict[str, Any]:
        """The `/intelligence/health` payload."""

    @abstractmethod
    def fetch_version(self) -> dict[str, Any]:
        """The `/intelligence/version` payload."""


# --------------------------------------------------------------------------- models
@dataclass(frozen=True)
class RetrievalRequest:
    """A deterministic description of what to retrieve for a classified intent."""

    intent: Intent
    entities: dict[str, str]
    targets: tuple[RetrievalTarget, ...]
    version: str = RETRIEVAL_VERSION

    def stable_dict(self) -> dict[str, Any]:
        return {"intent": self.intent.value,
                "entities": {k: self.entities[k] for k in sorted(self.entities)},
                "targets": [t.value for t in self.targets], "version": self.version}


@dataclass(frozen=True)
class RetrievalComponent:
    """One retrieved Decision Intelligence component — content verbatim, with its availability +
    citations + provenance. Never modifies retrieved content."""

    target: RetrievalTarget
    availability: RetrievalAvailability
    content: dict[str, Any] | None
    citations: tuple[Citation, ...] = ()
    provenance: dict[str, Any] = field(default_factory=dict)
    note: str | None = None

    def stable_dict(self) -> dict[str, Any]:
        return {"target": self.target.value, "availability": self.availability.value,
                "content": self.content, "citations": [c.stable_dict() for c in self.citations],
                "provenance": self.provenance, "note": self.note}


@dataclass(frozen=True)
class RetrievalResult:
    """The merged, deterministic retrieval payload for a conversation turn. `retrieved_at` is
    metadata only — excluded from the checksum, so identical retrievals fingerprint identically."""

    request: RetrievalRequest
    components: tuple[RetrievalComponent, ...]
    context: ConversationContext
    availability: RetrievalAvailability
    citations: tuple[Citation, ...]
    decision_intelligence_version: str | None
    checksum: str
    version: str = RETRIEVAL_VERSION
    retrieved_at: str = field(default_factory=_utc_now_iso)

    def stable_dict(self) -> dict[str, Any]:
        """Deterministic content (excludes `retrieved_at`) — the checksum basis."""
        return {
            "request": self.request.stable_dict(),
            "components": [c.stable_dict() for c in self.components],
            "context": self.context.stable_dict(), "availability": self.availability.value,
            "citations": [c.stable_dict() for c in self.citations],
            "decision_intelligence_version": self.decision_intelligence_version, "version": self.version,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.stable_dict(), "checksum": self.checksum, "retrieved_at": self.retrieved_at}

    def serialize(self) -> str:
        return json.dumps(self.stable_dict(), sort_keys=True, separators=(",", ":"))


def _sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# --------------------------------------------------------------------------- the orchestrator
class RetrievalOrchestrator:
    """Coordinates deterministic retrieval from Decision Intelligence into a conversation payload.

    Stateless + re-entrant (pure over the source's returned data); retrieval only — no generation."""

    def __init__(self, source: DecisionIntelligenceSource,
                 routing: "Mapping[Intent, tuple[RetrievalTarget, ...]] | None" = None) -> None:
        self._source = source
        self._routing = routing or RETRIEVAL_ROUTING

    def retrieve(self, classification: IntentClassification) -> RetrievalResult:
        """Run the deterministic retrieval pipeline for a classified intent.

        Raises:
            InvalidRetrievalRequestError: the classification is malformed.
        """
        if not isinstance(classification, IntentClassification):
            raise InvalidRetrievalRequestError("expected an IntentClassification")
        intent = classification.intent
        entities = dict(classification.entities)
        targets = tuple(self._routing.get(intent, ()))
        request = RetrievalRequest(intent=intent, entities=entities, targets=targets)

        if not targets:                                     # HELP / UNKNOWN — nothing to retrieve
            return self._assemble(request, [], entities, None)

        # Fetch the composed decision once if any subject target is requested.
        payload, fetch_state, payload_di_version = None, "n/a", None
        if any(t not in _STANDALONE for t in targets):
            payload, fetch_state = self._fetch_decision(entities)
            if payload:
                payload_di_version = (payload.get("decision", {}).get("decision_intelligence_version")
                                      or payload.get("versions", {}).get("decision_intelligence_version"))

        components = [self._component(t, payload, fetch_state) for t in targets]
        return self._assemble(request, components, entities, payload_di_version)

    # ---------------------------------------------------------------- fetch
    def _fetch_decision(self, entities: Mapping[str, str]) -> tuple[dict | None, str]:
        pid, symbol = entities.get("prediction_id"), entities.get("symbol")
        if not pid and not symbol:
            return None, "missing_subject"
        try:
            payload = self._source.fetch_decision(pid) if pid else self._source.fetch_decision_by_symbol(symbol)
        except Exception:                                   # noqa: BLE001 — a source failure is ERROR
            return None, "error"
        return payload, ("ok" if payload is not None else "not_found")

    # ---------------------------------------------------------------- component
    def _component(self, target: RetrievalTarget, payload: dict | None, fetch_state: str) -> RetrievalComponent:
        if target is RetrievalTarget.HEALTH:
            return self._standalone(target, self._safe(self._source.fetch_health))
        if target is RetrievalTarget.VERSION:
            return self._standalone(target, self._safe(self._source.fetch_version))
        # subject targets
        if fetch_state == "missing_subject":
            return RetrievalComponent(target, RetrievalAvailability.NOT_AVAILABLE, None,
                                      note="no prediction or symbol provided")
        if fetch_state == "error":
            return RetrievalComponent(target, RetrievalAvailability.ERROR, None,
                                      note="decision intelligence source error")
        if payload is None:
            return RetrievalComponent(target, RetrievalAvailability.NOT_AVAILABLE, None,
                                      note="prediction not found")
        return self._slice(target, payload)

    @staticmethod
    def _standalone(target: RetrievalTarget, content: dict | None) -> RetrievalComponent:
        if content is None:
            return RetrievalComponent(target, RetrievalAvailability.ERROR, None, note="source error")
        return RetrievalComponent(target, RetrievalAvailability.AVAILABLE, content)

    def _slice(self, target: RetrievalTarget, payload: dict) -> RetrievalComponent:
        decision = payload.get("decision", {})
        decision_citation = Citation(kind="decision", ref_id=decision.get("decision_id", "?"),
                                     source=_SOURCE)
        if target is RetrievalTarget.DECISION_SUMMARY:
            pred = self._section(payload, "prediction")
            content = {"decision_id": decision.get("decision_id"),
                       "prediction_id": decision.get("prediction_id"), "status": decision.get("status"),
                       "prediction": (pred or {}).get("payload"),
                       "prioritisation": payload.get("prioritisation")}
            avail = _AVAIL_FROM_STATUS.get(decision.get("status"), RetrievalAvailability.AVAILABLE)
            return RetrievalComponent(target, avail, content, (decision_citation,),
                                      {"checksum": decision.get("checksum")})
        if target is RetrievalTarget.EVIDENCE:
            return RetrievalComponent(target, RetrievalAvailability.AVAILABLE, payload.get("evidence"),
                                      (decision_citation,),
                                      {"checksum": (payload.get("evidence") or {}).get("checksum")})
        if target is RetrievalTarget.EXPLANATION:
            return RetrievalComponent(target, RetrievalAvailability.AVAILABLE, payload.get("explanation"),
                                      (decision_citation,))
        if target is RetrievalTarget.COMPOSITE_CONFIDENCE:
            return RetrievalComponent(target, RetrievalAvailability.AVAILABLE, payload.get("confidence"),
                                      (decision_citation,),
                                      {"checksum": (payload.get("confidence") or {}).get("checksum")})
        # subsystem-section targets (memory / similarity / learning)
        subsystem = {RetrievalTarget.HISTORICAL_CONTEXT: "historical_memory",
                     RetrievalTarget.SIMILAR_CASES: "similarity",
                     RetrievalTarget.LEARNING_SUMMARY: "learning"}[target]
        section = self._section(payload, subsystem)
        if section is None:
            return RetrievalComponent(target, RetrievalAvailability.NOT_AVAILABLE, None,
                                      (decision_citation,), note=f"no {subsystem} section")
        avail = _AVAIL_FROM_STATUS.get(section.get("status"), RetrievalAvailability.NOT_AVAILABLE)
        citations = (decision_citation, *(Citation(kind=ev.get("kind", "evidence"),
                                                   ref_id=ev.get("ref_id", "?"), source=_SOURCE)
                                          for ev in (section.get("evidence") or [])))
        return RetrievalComponent(target, avail, section, citations,
                                  {"subsystem": subsystem, "status": section.get("status")})

    @staticmethod
    def _section(payload: dict, subsystem: str) -> dict | None:
        for component in (payload.get("decision", {}).get("components") or []):
            if component.get("subsystem") == subsystem:
                return component
        return None

    @staticmethod
    def _safe(call: Any) -> dict | None:
        try:
            return call()
        except Exception:                                   # noqa: BLE001
            return None

    # ---------------------------------------------------------------- merge / assemble
    def _assemble(self, request: RetrievalRequest, components: list[RetrievalComponent],
                  entities: Mapping[str, str], payload_di_version: str | None) -> RetrievalResult:
        # Context merger — preserve ordering, provenance, citations, availability; never modify content.
        seen: set[tuple[str, str, str]] = set()
        citations: list[Citation] = []
        for component in components:
            for citation in component.citations:
                key = (citation.kind, citation.ref_id, citation.source)
                if key not in seen:
                    seen.add(key)
                    citations.append(citation)
        di_version = payload_di_version or self._di_version(components)
        subject_id = entities.get("prediction_id") or entities.get("symbol")
        context = ConversationContext(
            subject_kind=("prediction" if entities.get("prediction_id")
                          else "symbol" if entities.get("symbol") else None),
            subject_id=subject_id,
            data={c.target.value: c.content for c in components if c.content is not None},
            versions={"retrieval_version": RETRIEVAL_VERSION, "decision_intelligence_version": di_version},
        )
        availability = self._overall(request.targets, components)
        result = RetrievalResult(
            request=request, components=tuple(components), context=context, availability=availability,
            citations=tuple(citations), decision_intelligence_version=di_version, checksum="",
        )
        return _with_checksum(result)

    @staticmethod
    def _di_version(components: list[RetrievalComponent]) -> str | None:
        for component in components:
            content = component.content or {}
            for key in ("versions", "decision"):
                block = content.get(key) if isinstance(content, dict) else None
                if isinstance(block, dict) and block.get("decision_intelligence_version"):
                    return block["decision_intelligence_version"]
            if isinstance(content, dict) and content.get("decision_intelligence_version"):
                return content["decision_intelligence_version"]
        return None

    @staticmethod
    def _overall(targets: tuple[RetrievalTarget, ...],
                 components: list[RetrievalComponent]) -> RetrievalAvailability:
        if not targets:
            return RetrievalAvailability.NOT_SUPPORTED
        states = {c.availability for c in components}
        for level in (RetrievalAvailability.AVAILABLE, RetrievalAvailability.INSUFFICIENT_DATA,
                      RetrievalAvailability.ERROR):
            if level in states:
                return level
        return RetrievalAvailability.NOT_AVAILABLE


def _with_checksum(result: RetrievalResult) -> RetrievalResult:
    from dataclasses import replace
    return replace(result, checksum=_sha256(result.stable_dict()))
