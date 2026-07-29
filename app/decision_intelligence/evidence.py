"""Evidence & Explanation Engine for Decision Intelligence (Sprint 5 · Milestone 3).

Turns a **composed** :class:`DecisionIntelligence` object (M2) into a fully **traceable, explainable**
structure: a deterministic **evidence graph** (root → subsystem → facet), a **provenance map**, a
**For/Against** breakdown, a **missing-evidence** list, and a **descriptive** human-readable
explanation. It answers *where every piece of information came from, why it exists, and what supports
it* — **without** creating any new prediction, recommendation, statistic, confidence, score, or
ranking, and without re-running any engine.

It is a **pure, read-only, deterministic** transform: it never modifies the input object, it derives
everything from the already-composed sections (re-labelling verbatim data into a graph — never
computing), and identical inputs always produce identical output (a SHA-256 checksum proves it). It
imports **neither** the Prediction nor the Outcome engine.

Milestone 3 is evidence + explanation only — **no composite confidence, no ranking, no REST API, no
persistence** (those are later milestones).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from app.decision_intelligence.models import (
    CONTRIBUTORS,
    DecisionIntelligence,
    DecisionIntelligenceError,
    DecisionStatus,
    EvidenceRef,
    Provenance,
    Subsystem,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)

#: Human labels for the subsystem nodes (the graph's second level).
_LABELS: dict[Subsystem, str] = {
    Subsystem.PREDICTION: "Prediction",
    Subsystem.HISTORICAL_MEMORY: "Historical Memory",
    Subsystem.SIMILARITY: "Similarity",
    Subsystem.LEARNING: "Learning",
}


# --------------------------------------------------------------------------- enums / errors
class Stance(str, Enum):
    """Which side of the composed picture an evidence statement lands on."""

    FOR = "FOR"          # evidence supporting the composed decision
    AGAINST = "AGAINST"  # evidence reducing confidence/completeness (never advice)


class MissingReason(str, Enum):
    """Why a facet of evidence is absent (never fabricated)."""

    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"  # the subsystem has data, but too thin to stand on
    NOT_AVAILABLE = "NOT_AVAILABLE"          # the subsystem could not be read / errored / empty
    NOT_SUPPORTED = "NOT_SUPPORTED"          # the composition did not carry this facet


class ExplanationError(DecisionIntelligenceError):
    """The input is not a well-formed Decision Intelligence object."""


class OrphanedEvidenceError(DecisionIntelligenceError):
    """An explanation references a node with no provenance, or a node not in the graph."""


class DuplicateEvidenceError(DecisionIntelligenceError):
    """Two evidence nodes share an identifier (a determinism/keying bug)."""


# --------------------------------------------------------------------------- facet registry
def _pct(value: Any) -> str:
    return f"{float(value) * 100:.0f}%"


def _prediction_facets(p: dict) -> list[tuple[str, str, bool, str | None]]:
    conf = p.get("direction_prob")
    outcome = p.get("outcome_prob")
    direction = p.get("direction")
    entry, stop = p.get("entry"), p.get("stop")
    return [
        ("confidence", "Confidence", conf is not None,
         (f"directional probability {_pct(conf)}" + (f", outcome probability {_pct(outcome)}"
                                                      if outcome is not None else "")) if conf is not None else None),
        ("direction", "Direction", bool(direction),
         f"{direction} (recommendation {p.get('recommendation')})" if direction else None),
        ("risk", "Risk", entry is not None and stop is not None,
         (f"entry {entry}, stop {stop}, targets {p.get('target1')}/{p.get('target2')}")
         if (entry is not None and stop is not None) else None),
    ]


def _memory_facets(p: dict) -> list[tuple[str, str, bool, str | None]]:
    present = bool(p.get("record_present"))
    resolved = bool(p.get("resolved"))
    return [
        ("records", "Records", present,
         ("resolved history present" if resolved else "record present (unresolved)") if present else None),
        # M2 does not compose rollups into the object → honestly NOT_SUPPORTED here.
        ("aggregates", "Aggregates", False, None),
    ]


def _similarity_facets(p: dict) -> list[tuple[str, str, bool, str | None]]:
    count = p.get("neighbour_count")
    win = p.get("win_rate")
    return [
        ("neighbours", "Neighbours", bool(count),
         f"{count} similar historical case(s)" if count else None),
        ("similarity_score", "Similarity Score", win is not None,
         f"neighbour win rate {_pct(win)} over {p.get('sample_size')} case(s)" if win is not None else None),
    ]


def _learning_facets(p: dict) -> list[tuple[str, str, bool, str | None]]:
    patterns = p.get("pattern_count")
    recs = p.get("recommendation_count")
    return [
        ("patterns", "Patterns", bool(patterns), f"{patterns} validated pattern(s)" if patterns else None),
        ("statistics", "Statistics", bool(patterns), "validated statistics available" if patterns else None),
        ("recommendations", "Recommendations", bool(recs),
         f"{recs} evidence-bound recommendation(s)" if recs else None),
    ]


#: subsystem → a function projecting its composed payload into ordered facets
#: ``(key, label, present, detail)`` — verbatim re-labelling only, never computation.
_FACETS: dict[Subsystem, Callable[[dict], list[tuple[str, str, bool, str | None]]]] = {
    Subsystem.PREDICTION: _prediction_facets,
    Subsystem.HISTORICAL_MEMORY: _memory_facets,
    Subsystem.SIMILARITY: _similarity_facets,
    Subsystem.LEARNING: _learning_facets,
}


# --------------------------------------------------------------------------- graph models
def _node_id(decision_id: str, path: str) -> str:
    return hashlib.sha1(f"{decision_id}|{path}".encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class EvidenceNode:
    """One node of the evidence graph — the root, a subsystem, or a facet leaf. Every node carries
    its provenance and (where applicable) the evidence references that trace it to a source."""

    node_id: str
    path: str
    label: str
    subsystem: Subsystem
    status: DecisionStatus
    provenance: Provenance
    detail: str | None = None
    evidence: tuple[EvidenceRef, ...] = ()
    children: tuple["EvidenceNode", ...] = ()

    def walk(self) -> "list[EvidenceNode]":
        nodes = [self]
        for child in self.children:
            nodes.extend(child.walk())
        return nodes

    def stable_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id, "path": self.path, "label": self.label,
            "subsystem": self.subsystem.value, "status": self.status.value, "detail": self.detail,
            "provenance": self.provenance.stable_dict(),
            "evidence": [e.stable_dict() for e in self.evidence],
            "children": [c.stable_dict() for c in self.children],
        }


@dataclass(frozen=True)
class EvidenceGraph:
    """The deterministic evidence graph rooted at the decision, plus its checksum."""

    root: EvidenceNode
    decision_id: str
    checksum: str

    def nodes(self) -> list[EvidenceNode]:
        return self.root.walk()

    def provenance_map(self) -> dict[str, dict[str, Any]]:
        """node_id → provenance (the Provenance Resolver output). No orphaned evidence."""
        return {n.node_id: n.provenance.stable_dict() for n in self.nodes()}

    def stable_dict(self) -> dict[str, Any]:
        return {"decision_id": self.decision_id, "root": self.root.stable_dict()}


# --------------------------------------------------------------------------- explanation models
@dataclass(frozen=True)
class ForAgainstItem:
    """A single descriptive, evidence-backed statement (never persuasive/predictive/advisory)."""

    stance: Stance
    subsystem: Subsystem
    node_id: str                       # the evidence node this statement is traceable to
    statement: str
    evidence: tuple[EvidenceRef, ...] = ()

    def stable_dict(self) -> dict[str, Any]:
        return {"stance": self.stance.value, "subsystem": self.subsystem.value,
                "node_id": self.node_id, "statement": self.statement,
                "evidence": [e.stable_dict() for e in self.evidence]}


@dataclass(frozen=True)
class MissingEvidenceItem:
    """A facet of evidence that is honestly absent — with the reason (never fabricated)."""

    subsystem: Subsystem
    facet: str | None
    reason: MissingReason
    statement: str

    def stable_dict(self) -> dict[str, Any]:
        return {"subsystem": self.subsystem.value, "facet": self.facet,
                "reason": self.reason.value, "statement": self.statement}


@dataclass(frozen=True)
class Explanation:
    """The descriptive, deterministic explanation — a summary + For/Against + missing evidence."""

    summary: str
    disclaimer: str
    for_items: tuple[ForAgainstItem, ...]
    against_items: tuple[ForAgainstItem, ...]
    missing: tuple[MissingEvidenceItem, ...]

    def stable_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary, "disclaimer": self.disclaimer,
            "for": [i.stable_dict() for i in self.for_items],
            "against": [i.stable_dict() for i in self.against_items],
            "missing": [m.stable_dict() for m in self.missing],
        }


@dataclass(frozen=True)
class ExplainedDecision:
    """The Evidence & Explanation Engine's output — the graph + provenance + explanation + checksum.
    Never modifies the source Decision Intelligence object (it references it by id/version/checksum)."""

    decision_id: str
    prediction_id: str
    decision_intelligence_version: str
    source_checksum: str               # the composed object's checksum (traceability)
    evidence_graph: EvidenceGraph
    explanation: Explanation
    provenance_map: dict[str, dict[str, Any]]
    checksum: str

    def stable_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id, "prediction_id": self.prediction_id,
            "decision_intelligence_version": self.decision_intelligence_version,
            "source_checksum": self.source_checksum,
            "evidence_graph": self.evidence_graph.stable_dict(),
            "explanation": self.explanation.stable_dict(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.stable_dict(), "provenance_map": self.provenance_map, "checksum": self.checksum}

    def serialize(self) -> str:
        """Canonical, deterministic JSON — identical objects serialize byte-for-byte identically."""
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))


_DISCLAIMER = ("Descriptive composition of existing evidence — not a prediction, recommendation, "
               "or advice.")


def _sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# --------------------------------------------------------------------------- the engine
class EvidenceEngine:
    """Builds the evidence graph + provenance + For/Against + explanation for a composed object.

    Stateless, deterministic, thread-safe, and re-entrant (pure over its input); never mutates the
    Decision Intelligence object and never re-runs any subsystem.
    """

    def explain(self, decision: DecisionIntelligence) -> ExplainedDecision:
        """Produce the fully-traceable explanation for a composed Decision Intelligence object.

        Raises:
            ExplanationError: the input is not a DecisionIntelligence.
            OrphanedEvidenceError / DuplicateEvidenceError: an invalid evidence graph.
        """
        if not isinstance(decision, DecisionIntelligence):
            raise ExplanationError("expected a DecisionIntelligence object")

        subsystem_nodes = [self._subsystem_node(decision, sub) for sub in CONTRIBUTORS]
        root = EvidenceNode(
            node_id=_node_id(decision.decision_id, "decision"), path="decision",
            label="Decision Intelligence", subsystem=Subsystem.DECISION_INTELLIGENCE,
            status=decision.status, provenance=decision.provenance,
            detail=f"composed status {decision.status.value}", evidence=(),
            children=tuple(subsystem_nodes),
        )
        graph = EvidenceGraph(root=root, decision_id=decision.decision_id,
                              checksum=_sha256(root.stable_dict()))

        for_items, against_items = self._for_against(decision)
        missing = self._missing(decision)
        explanation = Explanation(
            summary=self._summary(decision), disclaimer=_DISCLAIMER,
            for_items=tuple(for_items), against_items=tuple(against_items), missing=tuple(missing),
        )
        explained = ExplainedDecision(
            decision_id=decision.decision_id, prediction_id=decision.prediction_id,
            decision_intelligence_version=decision.decision_intelligence_version,
            source_checksum=decision.checksum, evidence_graph=graph, explanation=explanation,
            provenance_map=graph.provenance_map(),
            checksum=_sha256({"graph": graph.stable_dict(), "explanation": explanation.stable_dict()}),
        )
        self._validate(explained, graph)
        logger.info("evidence: %s status=%s for=%d against=%d missing=%d", decision.decision_id,
                    decision.status.value, len(for_items), len(against_items), len(missing))
        return explained

    # ---------------------------------------------------------------- graph
    def _subsystem_node(self, decision: DecisionIntelligence, subsystem: Subsystem) -> EvidenceNode:
        component = decision.component(subsystem)
        payload = component.payload if isinstance(component.payload, dict) else {}
        facets = _FACETS[subsystem](payload)
        children: list[EvidenceNode] = []
        for key, label, present, detail in facets:
            if component.status is DecisionStatus.COMPLETE:
                facet_status = DecisionStatus.COMPLETE if present else DecisionStatus.EMPTY
            else:
                facet_status = component.status
            children.append(EvidenceNode(
                node_id=_node_id(decision.decision_id, f"{subsystem.value}/{key}"),
                path=f"{subsystem.value}/{key}", label=label, subsystem=subsystem,
                status=facet_status, provenance=component.provenance, detail=detail if present else None,
                evidence=component.evidence,
            ))
        return EvidenceNode(
            node_id=_node_id(decision.decision_id, subsystem.value), path=subsystem.value,
            label=_LABELS[subsystem], subsystem=subsystem, status=component.status,
            provenance=component.provenance, evidence=component.evidence, children=tuple(children),
        )

    # ---------------------------------------------------------------- for / against
    def _for_against(
        self, decision: DecisionIntelligence
    ) -> tuple[list[ForAgainstItem], list[ForAgainstItem]]:
        for_items: list[ForAgainstItem] = []
        against_items: list[ForAgainstItem] = []
        for subsystem in CONTRIBUTORS:                       # fixed, deterministic order
            component = decision.component(subsystem)
            node_id = _node_id(decision.decision_id, subsystem.value)
            payload = component.payload if isinstance(component.payload, dict) else {}
            if component.status is DecisionStatus.COMPLETE:
                for_items.append(ForAgainstItem(
                    stance=Stance.FOR, subsystem=subsystem, node_id=node_id,
                    statement=self._for_statement(subsystem, payload), evidence=component.evidence,
                ))
            else:
                against_items.append(ForAgainstItem(
                    stance=Stance.AGAINST, subsystem=subsystem, node_id=node_id,
                    statement=f"{_LABELS[subsystem]}: {self._reason_phrase(component.status)}.",
                    evidence=component.evidence,
                ))
            # factual, stored-figure conflict signals (no new computation)
            if subsystem is Subsystem.PREDICTION and payload.get("outcome_prob") is not None \
                    and float(payload["outcome_prob"]) < 0.5:
                against_items.append(ForAgainstItem(
                    stance=Stance.AGAINST, subsystem=subsystem, node_id=node_id,
                    statement=(f"Outcome model assessed target-before-stop probability at "
                               f"{_pct(payload['outcome_prob'])} (below 50%)."),
                    evidence=component.evidence,
                ))
            if subsystem is Subsystem.SIMILARITY and component.status is DecisionStatus.COMPLETE \
                    and payload.get("win_rate") is not None and float(payload["win_rate"]) < 0.5:
                against_items.append(ForAgainstItem(
                    stance=Stance.AGAINST, subsystem=subsystem, node_id=node_id,
                    statement=f"Similar cases historically resolved below 50% ({_pct(payload['win_rate'])}).",
                    evidence=component.evidence,
                ))
        return for_items, against_items

    @staticmethod
    def _for_statement(subsystem: Subsystem, payload: dict) -> str:
        if subsystem is Subsystem.PREDICTION:
            conf = payload.get("direction_prob")
            c = f" (confidence {_pct(conf)})" if conf is not None else ""
            return f"Prediction present: {payload.get('direction')}{c}."
        if subsystem is Subsystem.HISTORICAL_MEMORY:
            return "Prior resolved history is available for context."
        if subsystem is Subsystem.SIMILARITY:
            n, w = payload.get("neighbour_count"), payload.get("win_rate")
            wr = f" (win rate {_pct(w)})" if w is not None else ""
            return f"{n} similar historical case(s){wr}."
        return f"{payload.get('recommendation_count', 0)} validated learning observation(s) match this setup."

    @staticmethod
    def _reason_phrase(status: DecisionStatus) -> str:
        if status is DecisionStatus.INSUFFICIENT_DATA:
            return "insufficient historical data"
        if status is DecisionStatus.ERROR:
            return "evidence unavailable (source error)"
        return "no data available"

    # ---------------------------------------------------------------- missing
    def _missing(self, decision: DecisionIntelligence) -> list[MissingEvidenceItem]:
        missing: list[MissingEvidenceItem] = []
        for subsystem in CONTRIBUTORS:
            component = decision.component(subsystem)
            if component.status is DecisionStatus.INSUFFICIENT_DATA:
                missing.append(MissingEvidenceItem(
                    subsystem=subsystem, facet=None, reason=MissingReason.INSUFFICIENT_DATA,
                    statement=f"{_LABELS[subsystem]} evidence is insufficient.",
                ))
            elif component.status in (DecisionStatus.ERROR, DecisionStatus.EMPTY):
                missing.append(MissingEvidenceItem(
                    subsystem=subsystem, facet=None, reason=MissingReason.NOT_AVAILABLE,
                    statement=f"{_LABELS[subsystem]} evidence is not available.",
                ))
            else:  # COMPLETE — report any facet the composition did not carry
                payload = component.payload if isinstance(component.payload, dict) else {}
                for key, label, present, _detail in _FACETS[subsystem](payload):
                    if not present:
                        missing.append(MissingEvidenceItem(
                            subsystem=subsystem, facet=key, reason=MissingReason.NOT_SUPPORTED,
                            statement=f"{_LABELS[subsystem]} · {label} was not composed.",
                        ))
        return missing

    # ---------------------------------------------------------------- summary
    @staticmethod
    def _summary(decision: DecisionIntelligence) -> str:
        parts = []
        for subsystem in CONTRIBUTORS:
            parts.append(f"{_LABELS[subsystem]}: {decision.component(subsystem).status.value}")
        return (f"Decision {decision.decision_id} (prediction {decision.prediction_id}) — composed "
                f"status {decision.status.value}. " + "; ".join(parts) + ".")

    # ---------------------------------------------------------------- validation
    @staticmethod
    def _validate(explained: ExplainedDecision, graph: EvidenceGraph) -> None:
        nodes = graph.nodes()
        ids = [n.node_id for n in nodes]
        if len(ids) != len(set(ids)):
            raise DuplicateEvidenceError("duplicate evidence node id")
        for node in nodes:
            if node.provenance is None:                      # no orphaned (provenance-less) evidence
                raise OrphanedEvidenceError(f"node {node.path} has no provenance")
        known = set(ids)
        for item in (*explained.explanation.for_items, *explained.explanation.against_items):
            if item.node_id not in known:                    # every statement traces to a real node
                raise OrphanedEvidenceError(f"explanation references unknown node {item.node_id}")
