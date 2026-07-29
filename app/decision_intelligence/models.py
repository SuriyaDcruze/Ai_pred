"""Domain models for the Decision Intelligence Engine (Sprint 5 · Milestone 1).

The Decision Intelligence Engine is the **read-only composition layer** that (in later milestones)
assembles the four prior engines — Prediction/Outcome (stored), Historical Memory, Similarity, and
Learning — into a single explainable, evidence-bound **Decision Intelligence object**. This module
defines **only the domain model and the composition contract**: the object's identity, canonical
states, provenance metadata, version stamps, the per-subsystem section contract, and validation.

**Milestone 1 is structure only.** It performs **no composition**, reads **no** engine, computes
**no** statistics/explanations/confidence, exposes **no** HTTP, and imports **neither** the
Prediction nor the Outcome engine (nor Memory/Similarity/Learning). Every section is a **placeholder**
(`payload is None`) that later milestones fill; the object records where each section *will* come
from and carries the provenance/versioning needed to keep it deterministic and auditable.

Determinism: `decision_id` is a pure function of `(version, prediction_id)` — never random — and a
SHA-256 `checksum` over the object's stable content (volatile fields excluded) proves the same inputs
always yield the same object.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

#: The Decision Intelligence method/shape version. A method change is a new version, never an edit.
DECISION_INTELLIGENCE_VERSION: str = "di-1"


# --------------------------------------------------------------------------- canonical states
class DecisionStatus(str, Enum):
    """The canonical lifecycle states of a Decision Intelligence object. No others are permitted."""

    EMPTY = "EMPTY"                          # nothing composed yet (the M1 default)
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"  # corpus too thin for a meaningful composed view
    PARTIAL = "PARTIAL"                      # some sections present, others unavailable
    COMPLETE = "COMPLETE"                    # every contributor section present
    STALE = "STALE"                          # composed against upstream versions no longer current
    ERROR = "ERROR"                          # a section failed to compose


# --------------------------------------------------------------------------- subsystems / contract
class Subsystem(str, Enum):
    """The subsystems that contribute to (or compose) a Decision Intelligence object."""

    PREDICTION = "prediction"                # Prediction + Outcome + Risk (stored outputs)
    HISTORICAL_MEMORY = "historical_memory"
    SIMILARITY = "similarity"
    LEARNING = "learning"
    DECISION_INTELLIGENCE = "decision_intelligence"  # the composer itself (object provenance only)


#: The four subsystems that own a section (the composer is not a contributor).
CONTRIBUTORS: tuple[Subsystem, ...] = (
    Subsystem.PREDICTION, Subsystem.HISTORICAL_MEMORY, Subsystem.SIMILARITY, Subsystem.LEARNING,
)

#: The composition contract: each contributor owns **exactly one** section (named for the subsystem).
#: No subsystem may populate another subsystem's section (enforced in :class:`DecisionComponent`).
COMPOSITION_CONTRACT: dict[Subsystem, str] = {sub: sub.value for sub in CONTRIBUTORS}
#: Reverse lookup: section name → the subsystem that owns it.
SECTION_OWNER: dict[str, Subsystem] = {section: sub for sub, section in COMPOSITION_CONTRACT.items()}


def section_for(subsystem: Subsystem) -> str:
    """The section a contributor subsystem owns."""
    if subsystem not in COMPOSITION_CONTRACT:
        raise InvalidComponentError(f"{subsystem} is not a contributor subsystem")
    return COMPOSITION_CONTRACT[subsystem]


def owner_of(section: str) -> Subsystem:
    """The subsystem that owns a section name."""
    if section not in SECTION_OWNER:
        raise InvalidComponentError(f"unknown section {section!r}")
    return SECTION_OWNER[section]


# --------------------------------------------------------------------------- errors
class DecisionIntelligenceError(Exception):
    """Base class for every Decision Intelligence error."""


class InvalidStateError(DecisionIntelligenceError):
    """A status is not one of the canonical :class:`DecisionStatus` states."""


class InvalidProvenanceError(DecisionIntelligenceError):
    """Provenance metadata is missing or malformed (required even when data is unavailable)."""


class InvalidComponentError(DecisionIntelligenceError):
    """A component violates the composition contract (wrong owner / section / subsystem)."""


class SchemaConsistencyError(DecisionIntelligenceError):
    """The object's shape is inconsistent (bad id, missing/duplicate/extra contributor sections)."""


class UnsupportedVersionError(DecisionIntelligenceError):
    """A Decision Intelligence version this build does not support."""


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _get(row: Mapping[str, Any], key: str, default: Any = None) -> Any:
    try:
        value = row[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


def _round(value: float | None, ndigits: int = 10) -> float | None:
    if value is None:
        return None
    v = float(value)
    if v != v or v in (float("inf"), float("-inf")):
        return v
    return round(v, ndigits)


# --------------------------------------------------------------------------- evidence reference
@dataclass(frozen=True)
class EvidenceRef:
    """A pointer from a composed element back to its **source** — the traceability primitive.

    Every figure a later milestone composes will carry one or more of these so the whole object is
    auditable (a prediction, a validated pattern, a recommendation, a similar neighbour, …)."""

    kind: str                       # "prediction" | "pattern" | "recommendation" | "neighbour" | ...
    ref_id: str                     # the source identifier (prediction_id / pattern_key / rec_id …)
    subsystem: Subsystem
    note: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.subsystem, Subsystem):
            raise InvalidComponentError("EvidenceRef.subsystem must be a Subsystem")
        if not self.kind or not self.ref_id:
            raise InvalidComponentError("EvidenceRef requires a kind and a ref_id")

    def stable_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "ref_id": self.ref_id, "subsystem": self.subsystem.value,
                "note": self.note}

    to_dict = stable_dict

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvidenceRef":
        return cls(kind=_get(data, "kind"), ref_id=_get(data, "ref_id"),
                   subsystem=Subsystem(_get(data, "subsystem")), note=_get(data, "note"))


# --------------------------------------------------------------------------- provenance
@dataclass(frozen=True)
class Provenance:
    """Where a section (or the object) came from — **required even when the data is unavailable**.

    A placeholder section still carries a Provenance (its subsystem, with the rest null), so every
    part of the object is attributable. Volatile-free by construction: timestamps/checksums here are
    the *source's* (deterministic given the same source), not wall-clock at composition time."""

    subsystem: Subsystem
    source: str | None = None               # source identifier (e.g. a prediction_id / run id)
    subsystem_version: str | None = None
    timestamp: str | None = None            # the source data's timestamp (not composition time)
    checksum: str | None = None
    confidence: float | None = None
    evidence_ref: EvidenceRef | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.subsystem, Subsystem):
            raise InvalidProvenanceError("Provenance.subsystem must be a Subsystem")

    def stable_dict(self) -> dict[str, Any]:
        return {
            "subsystem": self.subsystem.value, "source": self.source,
            "subsystem_version": self.subsystem_version, "timestamp": self.timestamp,
            "checksum": self.checksum, "confidence": _round(self.confidence),
            "evidence_ref": self.evidence_ref.stable_dict() if self.evidence_ref else None,
        }

    to_dict = stable_dict

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Provenance":
        ev = _get(data, "evidence_ref")
        return cls(
            subsystem=Subsystem(_get(data, "subsystem")), source=_get(data, "source"),
            subsystem_version=_get(data, "subsystem_version"), timestamp=_get(data, "timestamp"),
            checksum=_get(data, "checksum"), confidence=_get(data, "confidence"),
            evidence_ref=EvidenceRef.from_dict(ev) if ev else None,
        )


# --------------------------------------------------------------------------- upstream versions
@dataclass(frozen=True)
class UpstreamVersions:
    """The upstream versions a Decision Intelligence object composed — recorded for **staleness
    detection** (no compatibility logic here; that is a later concern)."""

    prediction_model_version: str | None = None
    outcome_model_version: str | None = None
    feature_version: str | None = None
    embedding_version: str | None = None
    learning_version: str | None = None
    dataset_version: str | None = None

    def stable_dict(self) -> dict[str, Any]:
        return {
            "prediction_model_version": self.prediction_model_version,
            "outcome_model_version": self.outcome_model_version,
            "feature_version": self.feature_version, "embedding_version": self.embedding_version,
            "learning_version": self.learning_version, "dataset_version": self.dataset_version,
        }

    to_dict = stable_dict

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "UpstreamVersions":
        return cls(
            prediction_model_version=_get(data, "prediction_model_version"),
            outcome_model_version=_get(data, "outcome_model_version"),
            feature_version=_get(data, "feature_version"),
            embedding_version=_get(data, "embedding_version"),
            learning_version=_get(data, "learning_version"),
            dataset_version=_get(data, "dataset_version"),
        )


# --------------------------------------------------------------------------- component (section)
@dataclass(frozen=True)
class DecisionComponent:
    """One section of the composed object, owned by exactly one contributor subsystem.

    **Metadata + a payload placeholder only** (M1: `payload is None`). It records the section's
    status, its provenance (always present), and the evidence it *will* reference. The composition
    contract is enforced here: a component's `section` must equal `subsystem.value`, and its
    provenance's subsystem must match — so **no subsystem can populate another subsystem's section**."""

    subsystem: Subsystem
    section: str
    status: DecisionStatus
    provenance: Provenance
    payload: Any = None                     # placeholder — populated by later milestones
    evidence: tuple[EvidenceRef, ...] = ()

    def __post_init__(self) -> None:
        if self.subsystem not in COMPOSITION_CONTRACT:
            raise InvalidComponentError(f"{self.subsystem} is not a contributor subsystem")
        if self.section != COMPOSITION_CONTRACT[self.subsystem]:
            raise InvalidComponentError(
                f"section {self.section!r} is not owned by {self.subsystem} "
                f"(owner: {SECTION_OWNER.get(self.section)})"
            )
        if not isinstance(self.status, DecisionStatus):
            raise InvalidStateError(f"invalid component status {self.status!r}")
        if not isinstance(self.provenance, Provenance):
            raise InvalidProvenanceError(f"{self.section}: provenance is required")
        if self.provenance.subsystem is not self.subsystem:
            raise InvalidProvenanceError(
                f"{self.section}: provenance subsystem {self.provenance.subsystem} != {self.subsystem}"
            )

    @classmethod
    def placeholder(cls, subsystem: Subsystem, *, status: DecisionStatus = DecisionStatus.EMPTY,
                    subsystem_version: str | None = None) -> "DecisionComponent":
        """An empty section for a contributor — payload `None`, provenance present (metadata required
        even when data is unavailable)."""
        return cls(
            subsystem=subsystem, section=section_for(subsystem), status=status,
            provenance=Provenance(subsystem=subsystem, subsystem_version=subsystem_version),
            payload=None, evidence=(),
        )

    def stable_dict(self) -> dict[str, Any]:
        return {
            "subsystem": self.subsystem.value, "section": self.section, "status": self.status.value,
            "provenance": self.provenance.stable_dict(),
            "payload": self.payload,                    # None in M1
            "evidence": [e.stable_dict() for e in self.evidence],
        }

    to_dict = stable_dict

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DecisionComponent":
        return cls(
            subsystem=Subsystem(_get(data, "subsystem")), section=_get(data, "section"),
            status=DecisionStatus(_get(data, "status")),
            provenance=Provenance.from_dict(_get(data, "provenance") or {"subsystem": _get(data, "subsystem")}),
            payload=_get(data, "payload"),
            evidence=tuple(EvidenceRef.from_dict(e) for e in (_get(data, "evidence") or [])),
        )


# --------------------------------------------------------------------------- id / checksum helpers
def decision_id_for(prediction_id: str, version: str = DECISION_INTELLIGENCE_VERSION) -> str:
    """The deterministic, immutable id of a Decision Intelligence object — a function of its
    identity `(version, prediction_id)`, so the same decision always keys the same (never random)."""
    raw = f"{version}|{prediction_id}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _checksum(prediction_id: str, version: str, status: DecisionStatus,
              upstream: UpstreamVersions, components: "tuple[DecisionComponent, ...]") -> str:
    payload = {
        "decision_id": decision_id_for(prediction_id, version), "prediction_id": prediction_id,
        "decision_intelligence_version": version, "status": status.value,
        "upstream_versions": upstream.stable_dict(),
        "components": [c.stable_dict() for c in components],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _validate_components(components: "tuple[DecisionComponent, ...]") -> "tuple[DecisionComponent, ...]":
    """Ensure exactly the four contributor sections are present (no missing / duplicate / extra),
    then return them in a deterministic order (by section)."""
    subsystems = [c.subsystem for c in components]
    if len(subsystems) != len(set(subsystems)):
        raise SchemaConsistencyError("duplicate contributor section")
    if set(subsystems) != set(CONTRIBUTORS):
        missing = set(CONTRIBUTORS) - set(subsystems)
        extra = set(subsystems) - set(CONTRIBUTORS)
        raise SchemaConsistencyError(f"contributor sections mismatch (missing={missing}, extra={extra})")
    return tuple(sorted(components, key=lambda c: c.section))


# --------------------------------------------------------------------------- the object
@dataclass(frozen=True)
class DecisionIntelligence:
    """The canonical Decision Intelligence object — the deterministic, versioned container every
    later milestone composes into.

    In Milestone 1 it is **structure only**: the four contributor sections are placeholders
    (`payload is None`) and `evidence_graph` / `narrative` / `composite_confidence` are reserved
    (`None`) for M3/M4. Its `decision_id` is immutable and deterministic; its `checksum` fingerprints
    the (volatile-free) content; it records the upstream versions it will compose for staleness."""

    decision_id: str
    prediction_id: str
    decision_intelligence_version: str
    status: DecisionStatus
    upstream_versions: UpstreamVersions
    components: tuple[DecisionComponent, ...]
    checksum: str
    provenance: Provenance
    evidence_graph: Any = None              # reserved for M3
    narrative: Any = None                   # reserved for M3
    composite_confidence: Any = None        # reserved for M4
    created_at: str = field(default_factory=_utc_now_iso)

    def __post_init__(self) -> None:
        if self.decision_intelligence_version != DECISION_INTELLIGENCE_VERSION:
            raise UnsupportedVersionError(
                f"unsupported version {self.decision_intelligence_version!r}"
            )
        if not self.prediction_id:
            raise SchemaConsistencyError("prediction_id is required")
        if not isinstance(self.status, DecisionStatus):
            raise InvalidStateError(f"invalid status {self.status!r}")
        expected = decision_id_for(self.prediction_id, self.decision_intelligence_version)
        if self.decision_id != expected:
            raise SchemaConsistencyError(
                f"decision_id {self.decision_id!r} != deterministic {expected!r}"
            )
        _validate_components(self.components)   # exactly the four contributors, no dupes/extras

    @classmethod
    def create(
        cls, *, prediction_id: str, status: DecisionStatus = DecisionStatus.EMPTY,
        upstream_versions: UpstreamVersions | None = None,
        components: "list[DecisionComponent] | tuple[DecisionComponent, ...] | None" = None,
        version: str = DECISION_INTELLIGENCE_VERSION,
    ) -> "DecisionIntelligence":
        """Build a Decision Intelligence object with a deterministic id + checksum and the four
        contributor sections (placeholders by default). Validates the composition contract."""
        if version != DECISION_INTELLIGENCE_VERSION:
            raise UnsupportedVersionError(f"unsupported version {version!r}")
        if not prediction_id:
            raise SchemaConsistencyError("prediction_id is required")
        if not isinstance(status, DecisionStatus):
            raise InvalidStateError(f"invalid status {status!r}")
        upstream = upstream_versions or UpstreamVersions()
        comps = _validate_components(
            tuple(components) if components is not None
            else tuple(DecisionComponent.placeholder(sub) for sub in CONTRIBUTORS)
        )
        checksum = _checksum(prediction_id, version, status, upstream, comps)
        provenance = Provenance(
            subsystem=Subsystem.DECISION_INTELLIGENCE, source=prediction_id,
            subsystem_version=version, checksum=checksum,
        )
        return cls(
            decision_id=decision_id_for(prediction_id, version), prediction_id=prediction_id,
            decision_intelligence_version=version, status=status, upstream_versions=upstream,
            components=comps, checksum=checksum, provenance=provenance,
        )

    # ---- accessors -------------------------------------------------------
    def component(self, subsystem: Subsystem) -> DecisionComponent:
        """The section owned by a contributor subsystem."""
        for c in self.components:
            if c.subsystem is subsystem:
                return c
        raise SchemaConsistencyError(f"no section for {subsystem}")

    @property
    def is_placeholder(self) -> bool:
        """Whether every section is still an empty placeholder (the M1 state)."""
        return all(c.payload is None for c in self.components)

    # ---- serialization (the storage foundation — no repository/DB logic here) ----------
    def stable_dict(self) -> dict[str, Any]:
        """Deterministic content (excludes `created_at` / `checksum` / object provenance) — the
        basis for the checksum and for equality of content across runs."""
        return {
            "decision_id": self.decision_id, "prediction_id": self.prediction_id,
            "decision_intelligence_version": self.decision_intelligence_version,
            "status": self.status.value, "upstream_versions": self.upstream_versions.stable_dict(),
            "components": [c.stable_dict() for c in self.components],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.stable_dict(),
            "checksum": self.checksum, "provenance": self.provenance.stable_dict(),
            "evidence_graph": self.evidence_graph, "narrative": self.narrative,
            "composite_confidence": self.composite_confidence, "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DecisionIntelligence":
        return cls(
            decision_id=_get(data, "decision_id"), prediction_id=_get(data, "prediction_id"),
            decision_intelligence_version=_get(data, "decision_intelligence_version"),
            status=DecisionStatus(_get(data, "status")),
            upstream_versions=UpstreamVersions.from_dict(_get(data, "upstream_versions") or {}),
            components=tuple(DecisionComponent.from_dict(c) for c in (_get(data, "components") or [])),
            checksum=_get(data, "checksum"),
            provenance=Provenance.from_dict(_get(data, "provenance")
                                            or {"subsystem": Subsystem.DECISION_INTELLIGENCE.value}),
            evidence_graph=_get(data, "evidence_graph"), narrative=_get(data, "narrative"),
            composite_confidence=_get(data, "composite_confidence"),
            created_at=_get(data, "created_at"),
        )

    def to_row(self) -> dict[str, Any]:
        """The flattened row shape a future (optional, M4) append-only audit table would store —
        provided as the storage *foundation* only. **No table, migration, or repository exists yet.**"""
        return {
            "decision_id": self.decision_id, "prediction_id": self.prediction_id,
            "decision_intelligence_version": self.decision_intelligence_version,
            "status": self.status.value, "checksum": self.checksum,
            "upstream_versions_json": json.dumps(self.upstream_versions.stable_dict()),
            "components_json": json.dumps([c.stable_dict() for c in self.components]),
            "provenance_json": json.dumps(self.provenance.stable_dict()),
            "created_at": self.created_at,
        }

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "DecisionIntelligence":
        return cls.from_dict({
            "decision_id": _get(row, "decision_id"), "prediction_id": _get(row, "prediction_id"),
            "decision_intelligence_version": _get(row, "decision_intelligence_version"),
            "status": _get(row, "status"), "checksum": _get(row, "checksum"),
            "upstream_versions": json.loads(_get(row, "upstream_versions_json") or "{}"),
            "components": json.loads(_get(row, "components_json") or "[]"),
            "provenance": json.loads(_get(row, "provenance_json") or "{}"),
            "created_at": _get(row, "created_at"),
        })
