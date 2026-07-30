"""Prompt Builder for the Conversation Intelligence Engine (Sprint 6 · Milestone 4).

Converts a **retrieval result** (M3) + the user request into a **deterministic** prompt for the LLM.
It assembles prompts **only**: it never retrieves information, calls an LLM, generates a response,
classifies intents, or calculates confidence — and it **never invents information, reorders evidence
incorrectly, or modifies retrieved content**. Given identical retrieval results it always produces an
identical prompt (a SHA-256 checksum proves it).

Fixed section order (never changed): System Instructions → Conversation Context → Retrieved Decision
Intelligence → Evidence → Composite Confidence → Historical Context → Similar Cases → Learning
Summary → Citations → User Request. Retrieved content is assembled verbatim with its availability and
citations preserved; a deterministic token budget trims lowest-priority context first while always
preserving the system instructions, the user request, and the citations. Imports no engine or LLM.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

from app.conversation.intent import Intent
from app.conversation.models import Citation
from app.conversation.retrieval import RetrievalResult, RetrievalTarget

#: The Prompt Builder method/schema version.
PROMPT_VERSION: str = "prm-1"
#: Default token budget for the assembled prompt (deterministic estimate; configurable).
DEFAULT_TOKEN_BUDGET: int = 2000
_OMITTED = "[section omitted to fit the token budget]"


# --------------------------------------------------------------------------- sections / order
class PromptSection(str, Enum):
    """The fixed prompt sections, in their canonical (never-reordered) order."""

    SYSTEM = "SYSTEM"
    CONVERSATION_CONTEXT = "CONVERSATION_CONTEXT"
    DECISION_INTELLIGENCE = "DECISION_INTELLIGENCE"
    EVIDENCE = "EVIDENCE"
    COMPOSITE_CONFIDENCE = "COMPOSITE_CONFIDENCE"
    HISTORICAL_CONTEXT = "HISTORICAL_CONTEXT"
    SIMILAR_CASES = "SIMILAR_CASES"
    LEARNING_SUMMARY = "LEARNING_SUMMARY"
    CITATIONS = "CITATIONS"
    USER_REQUEST = "USER_REQUEST"


_SECTION_ORDER = {section: i for i, section in enumerate(PromptSection)}
_SECTION_TITLE = {
    PromptSection.SYSTEM: "System Instructions",
    PromptSection.CONVERSATION_CONTEXT: "Conversation Context",
    PromptSection.DECISION_INTELLIGENCE: "Retrieved Decision Intelligence",
    PromptSection.EVIDENCE: "Evidence",
    PromptSection.COMPOSITE_CONFIDENCE: "Composite Confidence (evidence quality, not a prediction)",
    PromptSection.HISTORICAL_CONTEXT: "Historical Context",
    PromptSection.SIMILAR_CASES: "Similar Cases",
    PromptSection.LEARNING_SUMMARY: "Learning Summary",
    PromptSection.CITATIONS: "Citations",
    PromptSection.USER_REQUEST: "User Request",
}
#: Higher priority ⇒ kept longer under the budget. SYSTEM / USER_REQUEST / CITATIONS never trimmed.
_SECTION_PRIORITY = {
    PromptSection.SYSTEM: 1000, PromptSection.USER_REQUEST: 1000, PromptSection.CITATIONS: 900,
    PromptSection.COMPOSITE_CONFIDENCE: 80, PromptSection.DECISION_INTELLIGENCE: 70,
    PromptSection.EVIDENCE: 60, PromptSection.HISTORICAL_CONTEXT: 50, PromptSection.SIMILAR_CASES: 40,
    PromptSection.LEARNING_SUMMARY: 40, PromptSection.CONVERSATION_CONTEXT: 30,
}
_NEVER_TRIM = {PromptSection.SYSTEM, PromptSection.USER_REQUEST, PromptSection.CITATIONS}

#: Which retrieval target feeds which prompt section.
_TARGET_SECTION = {
    RetrievalTarget.DECISION_SUMMARY: PromptSection.DECISION_INTELLIGENCE,
    RetrievalTarget.EXPLANATION: PromptSection.DECISION_INTELLIGENCE,
    RetrievalTarget.HEALTH: PromptSection.DECISION_INTELLIGENCE,
    RetrievalTarget.VERSION: PromptSection.DECISION_INTELLIGENCE,
    RetrievalTarget.EVIDENCE: PromptSection.EVIDENCE,
    RetrievalTarget.COMPOSITE_CONFIDENCE: PromptSection.COMPOSITE_CONFIDENCE,
    RetrievalTarget.HISTORICAL_CONTEXT: PromptSection.HISTORICAL_CONTEXT,
    RetrievalTarget.SIMILAR_CASES: PromptSection.SIMILAR_CASES,
    RetrievalTarget.LEARNING_SUMMARY: PromptSection.LEARNING_SUMMARY,
}
#: Targets whose content is cited evidence (health/version are system info, no citation required).
_EVIDENCE_TARGETS = {
    RetrievalTarget.DECISION_SUMMARY, RetrievalTarget.EXPLANATION, RetrievalTarget.EVIDENCE,
    RetrievalTarget.COMPOSITE_CONFIDENCE, RetrievalTarget.HISTORICAL_CONTEXT,
    RetrievalTarget.SIMILAR_CASES, RetrievalTarget.LEARNING_SUMMARY,
}


# --------------------------------------------------------------------------- prompts (system/instruction)
SYSTEM_PROMPT: str = (
    "You are the AEGIS explanation assistant. You EXPLAIN existing analysis only. You MUST NOT "
    "predict, forecast, or give buy/sell/hold or any trading advice, and you MUST NOT invent "
    "information. Use ONLY the retrieved Decision Intelligence provided below — never add facts that "
    "are not present. Preserve every citation and attribute each statement to its source. When "
    "information is INSUFFICIENT_DATA, NOT_AVAILABLE, or NOT_SUPPORTED, say so honestly rather than "
    "guessing. All figures are historical/backtest context, not a prediction of future results."
)

#: Per-intent instruction templates (reusable + versioned as part of `prm-1`).
INSTRUCTION_TEMPLATES: dict[Intent, str] = {
    Intent.EXPLAIN_PREDICTION: "Explain this prediction using only the retrieved Decision "
                               "Intelligence. Describe what was decided and why, citing the evidence.",
    Intent.SHOW_EVIDENCE: "Present the retrieved evidence for this decision, with its sources. Do not "
                          "add evidence that is not shown.",
    Intent.WHY_CONFIDENCE: "Explain the composite confidence as an evidence-quality measure (how "
                           "complete/consistent the evidence is) — NOT a probability of success.",
    Intent.HISTORICAL_COMPARISON: "Describe the retrieved historical context for this setup, honestly "
                                  "noting when it is insufficient.",
    Intent.SIMILAR_CASES: "Summarise the retrieved similar historical cases and their honest outcome "
                          "stats. Do not imply any future outcome.",
    Intent.LEARNING_SUMMARY: "Summarise the retrieved validated learning observations, with sample "
                             "sizes; never present them as advice.",
    Intent.DECISION_SUMMARY: "Give a plain-language summary of the composed decision from the "
                             "retrieved Decision Intelligence, citing evidence.",
    Intent.HEALTH: "Report the retrieved service readiness. This is system status only.",
    Intent.VERSION: "Report the retrieved version information.",
    Intent.HELP: "Explain, at a high level, what kinds of questions you can answer about existing "
                 "AEGIS analysis. Offer no predictions or advice.",
    Intent.UNKNOWN: "The request was not recognised. Ask the user to rephrase; do not guess.",
}


# --------------------------------------------------------------------------- errors
class PromptError(Exception):
    """Base class for prompt-building errors."""


class MissingCitationError(PromptError):
    """Cited-evidence content is present but the prompt carries no citation."""


class PromptValidationError(PromptError):
    """The assembled prompt failed validation (missing section / ordering / template)."""


class TemplateError(PromptError):
    """No instruction template exists for the intent."""


def _tokens(text: str) -> int:
    """A deterministic token estimate (~4 chars/token)."""
    return max(1, (len(text) + 3) // 4)


def _sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# --------------------------------------------------------------------------- models
@dataclass(frozen=True)
class PromptBlock:
    """One rendered prompt section — content verbatim, with its availability + citation flag."""

    section: PromptSection
    title: str
    content: str
    priority: int
    tokens: int
    availability: str = "AVAILABLE"
    requires_citation: bool = False
    trimmed: bool = False

    def stable_dict(self) -> dict[str, Any]:
        return {"section": self.section.value, "title": self.title, "content": self.content,
                "availability": self.availability, "requires_citation": self.requires_citation,
                "trimmed": self.trimmed, "tokens": self.tokens}


@dataclass(frozen=True)
class Prompt:
    """A deterministic, validated prompt assembled from retrieved Decision Intelligence."""

    intent: Intent
    blocks: tuple[PromptBlock, ...]
    citations: tuple[Citation, ...]
    versions: dict[str, str | None]
    token_stats: dict[str, Any]
    checksum: str
    version: str = PROMPT_VERSION

    def render(self) -> str:
        """The full prompt text (non-trimmed blocks, in fixed section order)."""
        parts = []
        for block in self.blocks:
            body = _OMITTED if block.trimmed else block.content
            parts.append(f"## {block.title}\n{body}")
        return "\n\n".join(parts)

    def stable_dict(self) -> dict[str, Any]:
        return {"intent": self.intent.value, "blocks": [b.stable_dict() for b in self.blocks],
                "citations": [c.stable_dict() for c in self.citations], "versions": self.versions,
                "token_stats": self.token_stats, "version": self.version}

    def to_dict(self) -> dict[str, Any]:
        return {**self.stable_dict(), "checksum": self.checksum, "text": self.render()}

    def serialize(self) -> str:
        return json.dumps(self.stable_dict(), sort_keys=True, separators=(",", ":"))


# --------------------------------------------------------------------------- the builder
class PromptBuilder:
    """Assembles deterministic, validated prompts from a retrieval result (no retrieval, no LLM)."""

    def __init__(self, *, token_budget: int = DEFAULT_TOKEN_BUDGET, system_prompt: str = SYSTEM_PROMPT,
                 templates: "dict[Intent, str] | None" = None) -> None:
        self._budget = token_budget
        self._system_prompt = system_prompt
        self._templates = INSTRUCTION_TEMPLATES if templates is None else templates

    def build(self, *, retrieval: RetrievalResult, user_request: str) -> Prompt:
        """Build the prompt for a retrieval result + the user's request.

        Raises:
            TemplateError / MissingCitationError / PromptValidationError.
        """
        intent = retrieval.request.intent
        if intent not in self._templates:
            raise TemplateError(f"no instruction template for {intent}")

        blocks: list[PromptBlock] = []
        # 1. System instructions (+ the intent's instruction template).
        system_text = f"{self._system_prompt}\n\nTASK: {self._templates[intent]}"
        blocks.append(self._block(PromptSection.SYSTEM, system_text))
        # 2. Conversation context (subject + versions) — verbatim, never modified.
        ctx = retrieval.context
        ctx_text = json.dumps({"subject_kind": ctx.subject_kind, "subject_id": ctx.subject_id,
                               "versions": ctx.versions}, sort_keys=True, indent=2)
        blocks.append(self._block(PromptSection.CONVERSATION_CONTEXT, ctx_text))
        # 3–8. Retrieved Decision Intelligence content, assembled verbatim by section.
        blocks.extend(self._content_blocks(retrieval))
        # 9. Citations (deterministic).
        blocks.append(self._block(PromptSection.CITATIONS, self._format_citations(retrieval.citations)))
        # 10. User request (verbatim).
        blocks.append(self._block(PromptSection.USER_REQUEST, user_request))

        blocks.sort(key=lambda b: _SECTION_ORDER[b.section])
        self._validate(blocks, retrieval.citations)
        blocks, token_stats = self._apply_budget(blocks)

        versions = {
            "prompt_version": PROMPT_VERSION, "conversation_version": "cnv-1",
            "retrieval_version": retrieval.version,
            "decision_intelligence_version": retrieval.decision_intelligence_version,
        }
        checksum = _sha256({"blocks": [b.stable_dict() for b in blocks],
                            "citations": [c.stable_dict() for c in retrieval.citations],
                            "versions": versions})
        return Prompt(intent=intent, blocks=tuple(blocks), citations=retrieval.citations,
                      versions=versions, token_stats=token_stats, checksum=checksum)

    # ---------------------------------------------------------------- assembly
    def _content_blocks(self, retrieval: RetrievalResult) -> list[PromptBlock]:
        by_section: dict[PromptSection, list[str]] = {}
        requires: dict[PromptSection, bool] = {}
        availability: dict[PromptSection, str] = {}
        for component in retrieval.components:
            section = _TARGET_SECTION[component.target]
            if component.content is None:                   # unavailable — an honest note, never faked
                text = f"[{component.availability.value}] {component.note or 'no data'}"
            else:
                text = json.dumps(component.content, sort_keys=True, indent=2, ensure_ascii=False)
            by_section.setdefault(section, []).append(f"({component.target.value}) {text}")
            requires[section] = requires.get(section, False) or component.target in _EVIDENCE_TARGETS
            # a section is AVAILABLE if any of its components is
            if component.availability.value == "AVAILABLE" or availability.get(section) is None:
                availability[section] = component.availability.value
        blocks = []
        for section, parts in by_section.items():
            blocks.append(self._block(section, "\n".join(parts),
                                      availability=availability.get(section, "AVAILABLE"),
                                      requires_citation=requires.get(section, False)))
        return blocks

    @staticmethod
    def _format_citations(citations: tuple[Citation, ...]) -> str:
        if not citations:
            return "(no citations)"
        return "\n".join(f"[{i}] {c.kind}:{c.ref_id} ({c.source})" for i, c in enumerate(citations, 1))

    @staticmethod
    def _block(section: PromptSection, content: str, *, availability: str = "AVAILABLE",
               requires_citation: bool = False) -> PromptBlock:
        return PromptBlock(section=section, title=_SECTION_TITLE[section], content=content,
                           priority=_SECTION_PRIORITY[section], tokens=_tokens(content),
                           availability=availability, requires_citation=requires_citation)

    # ---------------------------------------------------------------- token budget
    def _apply_budget(self, blocks: list[PromptBlock]) -> tuple[list[PromptBlock], dict[str, Any]]:
        total = sum(b.tokens for b in blocks)
        trimmed: list[str] = []
        if total > self._budget:
            trimmable = sorted((b for b in blocks if b.section not in _NEVER_TRIM),
                               key=lambda b: (b.priority, _SECTION_ORDER[b.section]))
            index = {id(b): i for i, b in enumerate(blocks)}
            for block in trimmable:
                if total <= self._budget:
                    break
                marker_tokens = _tokens(_OMITTED)
                total -= (block.tokens - marker_tokens)
                blocks[index[id(block)]] = replace(block, content=_OMITTED, tokens=marker_tokens,
                                                   trimmed=True)
                trimmed.append(block.section.value)
        return blocks, {"total_tokens": total, "budget": self._budget,
                        "within_budget": total <= self._budget, "trimmed_sections": trimmed}

    # ---------------------------------------------------------------- validation
    @staticmethod
    def _validate(blocks: list[PromptBlock], citations: tuple[Citation, ...]) -> None:
        sections = [b.section for b in blocks]
        if PromptSection.SYSTEM not in sections or PromptSection.USER_REQUEST not in sections:
            raise PromptValidationError("missing required system/user section")
        if PromptSection.CITATIONS not in sections:
            raise PromptValidationError("missing citations section")
        order = [_SECTION_ORDER[s] for s in sections]
        if order != sorted(order):
            raise PromptValidationError("prompt sections are out of order")
        # cited-evidence content present but no citation ⇒ reject
        if any(b.requires_citation and b.availability == "AVAILABLE" for b in blocks) and not citations:
            raise MissingCitationError("cited evidence present without any citation")
