"""Tests for the Prompt Builder (Sprint 6 · Milestone 4).

Cover prompt construction + fixed section ordering, template selection, citation preservation +
missing-citation rejection, token budgeting + deterministic truncation, validation, serialization,
deterministic output, versioning, an end-to-end retrieval→prompt path, and no-engine/LLM imports.
Assembly only — no retrieval, no LLM, no generation.
"""

from __future__ import annotations

import pytest

from app.conversation.intent import (
    Intent,
    IntentClassification,
    IntentValidation,
)
from app.conversation.models import Citation, ConversationContext
from app.conversation.prompt import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    MissingCitationError,
    PromptBuilder,
    PromptSection,
    TemplateError,
)
from app.conversation.retrieval import (
    DecisionIntelligenceSource,
    RetrievalAvailability,
    RetrievalComponent,
    RetrievalOrchestrator,
    RetrievalRequest,
    RetrievalResult,
    RetrievalTarget,
)

_CITE = Citation(kind="decision", ref_id="d1", source="decision_intelligence")


def _result(components, *, intent=Intent.SHOW_EVIDENCE, citations=(_CITE,), di_version="di-1"):
    request = RetrievalRequest(intent=intent, entities={"prediction_id": "p1"},
                               targets=tuple(c.target for c in components))
    ctx = ConversationContext(subject_kind="prediction", subject_id="p1",
                              versions={"retrieval_version": "ret-1",
                                        "decision_intelligence_version": di_version})
    return RetrievalResult(request=request, components=tuple(components), context=ctx,
                           availability=RetrievalAvailability.AVAILABLE, citations=tuple(citations),
                           decision_intelligence_version=di_version, checksum="")


def _comp(target, content, *, availability=RetrievalAvailability.AVAILABLE, citations=(_CITE,)):
    return RetrievalComponent(target=target, availability=availability, content=content,
                              citations=tuple(citations))


# --------------------------------------------------------------- construction / ordering
def test_prompt_construction_and_fixed_order():
    result = _result([_comp(RetrievalTarget.EVIDENCE, {"graph": {}, "checksum": "echk"})])
    prompt = PromptBuilder().build(retrieval=result, user_request="show evidence for p1")
    sections = [b.section for b in prompt.blocks]
    assert sections == sorted(sections, key=lambda s: list(PromptSection).index(s))
    assert PromptSection.SYSTEM == sections[0] and PromptSection.USER_REQUEST == sections[-1]
    text = prompt.render()
    assert "System Instructions" in text and "show evidence for p1" in text


def test_template_selection():
    ev = _result([_comp(RetrievalTarget.EVIDENCE, {"x": 1})], intent=Intent.SHOW_EVIDENCE)
    conf = _result([_comp(RetrievalTarget.COMPOSITE_CONFIDENCE, {"level": "LOW"})],
                   intent=Intent.WHY_CONFIDENCE)
    p_ev = PromptBuilder().build(retrieval=ev, user_request="q")
    p_conf = PromptBuilder().build(retrieval=conf, user_request="q")
    sys_ev = next(b for b in p_ev.blocks if b.section is PromptSection.SYSTEM).content
    sys_conf = next(b for b in p_conf.blocks if b.section is PromptSection.SYSTEM).content
    assert "present the retrieved evidence" in sys_ev.lower()
    assert "evidence-quality" in sys_conf.lower() and sys_ev != sys_conf


def test_missing_template_rejected():
    result = _result([_comp(RetrievalTarget.EVIDENCE, {"x": 1})])
    with pytest.raises(TemplateError):
        PromptBuilder(templates={}).build(retrieval=result, user_request="q")


# --------------------------------------------------------------- content verbatim / availability
def test_content_is_verbatim():
    result = _result([_comp(RetrievalTarget.EVIDENCE, {"checksum": "echk", "graph": {"n": 1}})])
    block = next(b for b in PromptBuilder().build(retrieval=result, user_request="q").blocks
                 if b.section is PromptSection.EVIDENCE)
    assert '"checksum": "echk"' in block.content and "EVIDENCE" in block.content


def test_unavailable_rendered_honestly():
    result = _result([_comp(RetrievalTarget.HISTORICAL_CONTEXT, None,
                            availability=RetrievalAvailability.INSUFFICIENT_DATA, citations=(_CITE,))],
                     intent=Intent.HISTORICAL_COMPARISON)
    block = next(b for b in PromptBuilder().build(retrieval=result, user_request="q").blocks
                 if b.section is PromptSection.HISTORICAL_CONTEXT)
    assert "INSUFFICIENT_DATA" in block.content


# --------------------------------------------------------------- citations
def test_citations_preserved_and_formatted():
    result = _result([_comp(RetrievalTarget.EVIDENCE, {"x": 1})])
    prompt = PromptBuilder().build(retrieval=result, user_request="q")
    cites = next(b for b in prompt.blocks if b.section is PromptSection.CITATIONS).content
    assert "[1] decision:d1 (decision_intelligence)" in cites and prompt.citations == (_CITE,)


def test_missing_citation_rejected():
    # AVAILABLE cited-evidence content but no citations on the result → rejected.
    result = _result([_comp(RetrievalTarget.EVIDENCE, {"x": 1}, citations=())], citations=())
    with pytest.raises(MissingCitationError):
        PromptBuilder().build(retrieval=result, user_request="q")


def test_health_needs_no_citation():
    result = _result([_comp(RetrievalTarget.HEALTH, {"status": "ready"}, citations=())],
                     intent=Intent.HEALTH, citations=())
    prompt = PromptBuilder().build(retrieval=result, user_request="status")   # no error
    assert prompt.citations == ()


# --------------------------------------------------------------- token budget / truncation
def test_token_budget_trims_lowest_priority_first():
    big = {"blob": "x" * 3000}
    result = _result([_comp(RetrievalTarget.EVIDENCE, big),
                      _comp(RetrievalTarget.LEARNING_SUMMARY, {"note": "y" * 3000})],
                     intent=Intent.DECISION_SUMMARY)
    prompt = PromptBuilder(token_budget=400).build(retrieval=result, user_request="summary")
    stats = prompt.token_stats
    assert stats["within_budget"] and stats["trimmed_sections"]
    trimmed = stats["trimmed_sections"]
    # lowest-priority context trimmed first: CONVERSATION_CONTEXT(30) < LEARNING_SUMMARY(40) < EVIDENCE(60)
    assert trimmed[0] == "CONVERSATION_CONTEXT"
    assert "LEARNING_SUMMARY" in trimmed and (
        "EVIDENCE" not in trimmed or trimmed.index("LEARNING_SUMMARY") < trimmed.index("EVIDENCE"))
    kept = {b.section for b in prompt.blocks if not b.trimmed}
    assert {PromptSection.SYSTEM, PromptSection.USER_REQUEST, PromptSection.CITATIONS} <= kept


def test_truncation_is_deterministic():
    big = {"blob": "x" * 3000}
    r = lambda: _result([_comp(RetrievalTarget.EVIDENCE, big)], intent=Intent.SHOW_EVIDENCE)
    a = PromptBuilder(token_budget=300).build(retrieval=r(), user_request="q")
    b = PromptBuilder(token_budget=300).build(retrieval=r(), user_request="q")
    assert a.checksum == b.checksum and a.token_stats == b.token_stats


# --------------------------------------------------------------- determinism / serialization / versioning
def test_deterministic_prompt():
    result = _result([_comp(RetrievalTarget.EVIDENCE, {"x": 1})])
    a = PromptBuilder().build(retrieval=result, user_request="q")
    b = PromptBuilder().build(retrieval=result, user_request="q")
    assert a.checksum == b.checksum and a.serialize() == b.serialize() and a.render() == b.render()


def test_serialization_shape_and_versions():
    result = _result([_comp(RetrievalTarget.EVIDENCE, {"x": 1})])
    prompt = PromptBuilder().build(retrieval=result, user_request="q")
    d = prompt.to_dict()
    assert d["checksum"] and "text" in d and d["version"] == PROMPT_VERSION
    assert prompt.versions == {"prompt_version": PROMPT_VERSION, "conversation_version": "cnv-1",
                               "retrieval_version": "ret-1", "decision_intelligence_version": "di-1"}


def test_system_prompt_forbids_advice_and_prediction():
    assert "must not" in SYSTEM_PROMPT.lower()
    for phrase in ("predict", "advice", "invent"):
        assert phrase in SYSTEM_PROMPT.lower()


# --------------------------------------------------------------- end-to-end (retrieval → prompt)
class _FakeSource(DecisionIntelligenceSource):
    def fetch_decision(self, prediction_id):
        return {"versions": {"decision_intelligence_version": "di-1"},
                "decision": {"decision_id": "d1", "prediction_id": prediction_id,
                             "decision_intelligence_version": "di-1", "status": "PARTIAL",
                             "components": [{"subsystem": "prediction", "section": "prediction",
                                             "status": "COMPLETE", "provenance": {}, "evidence": [],
                                             "payload": {"direction": "BUY"}}], "checksum": "dchk"},
                "evidence": {"graph": {}, "provenance_map": {}, "checksum": "echk"},
                "explanation": {"summary": "s"}, "confidence": {"level": "LOW", "checksum": "cchk"},
                "prioritisation": {"score": 0.25, "level": "LOW"}}

    def fetch_decision_by_symbol(self, symbol):
        return self.fetch_decision("p1")

    def fetch_health(self):
        return {"status": "ready", "decision_intelligence_version": "di-1"}

    def fetch_version(self):
        return {"decision_intelligence_version": "di-1"}


def test_end_to_end_retrieval_to_prompt():
    clf = IntentClassification(intent=Intent.EXPLAIN_PREDICTION, confidence=1.0, matched_rules=(),
                               entities={"prediction_id": "p1"}, validation=IntentValidation(True))
    result = RetrievalOrchestrator(_FakeSource()).retrieve(clf)
    prompt = PromptBuilder().build(retrieval=result, user_request="explain p1")
    assert prompt.intent is Intent.EXPLAIN_PREDICTION
    assert any(b.section is PromptSection.DECISION_INTELLIGENCE for b in prompt.blocks)
    assert "d1" in next(b for b in prompt.blocks if b.section is PromptSection.CITATIONS).content


# --------------------------------------------------------------- isolation
def test_prompt_module_imports_no_engine_or_llm():
    import ast

    import app.conversation.prompt as pr
    with open(pr.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    forbidden = ("app.ai.sklearn_model", "app.ai.outcome_model", "app.decision_intelligence",
                 "app.memory", "app.similarity", "app.learning", "app.forward_testing", "openai")
    for name in imported:
        assert not name.startswith(forbidden), f"prompt builder must not import {name}"
