"""Tests for the Intent Detection Engine (Sprint 6 · Milestone 2).

Cover exact-phrase / keyword / synonym matches for every supported intent, ambiguous + unknown +
malformed requests, entity extraction, validation (required subject), deterministic output +
serialization, registry behaviour / extensibility, and no-engine/LLM imports. Deterministic
rule-based only — no LLM, no retrieval, no API.
"""

from __future__ import annotations

import pytest

from app.conversation.intent import (
    INTENT_REGISTRY,
    Intent,
    IntentClassification,
    IntentClassifier,
    IntentSpec,
    InvalidIntentInputError,
    available_intents,
    extract_entities,
    spec_for,
)


@pytest.fixture()
def clf():
    return IntentClassifier()


# --------------------------------------------------------------- matching
def test_exact_phrase_matches(clf):
    assert clf.classify("Explain this prediction abc123def456789a").intent is Intent.EXPLAIN_PREDICTION
    assert clf.classify("show me the evidence for BTCUSDT").intent is Intent.SHOW_EVIDENCE
    assert clf.classify("why the confidence for RELIANCE.NS?").intent is Intent.WHY_CONFIDENCE
    assert clf.classify("system status").intent is Intent.HEALTH
    assert clf.classify("what version are you").intent is Intent.VERSION
    assert clf.classify("what can you do").intent is Intent.HELP


def test_keyword_and_synonym_matches(clf):
    assert clf.classify("any similar cases for BTCUSDT").intent is Intent.SIMILAR_CASES
    assert clf.classify("show the learning observations").intent is Intent.LEARNING_SUMMARY
    assert clf.classify("historical behaviour of RELIANCE.NS").intent is Intent.HISTORICAL_COMPARISON
    # synonym: 'neighbours' → SIMILAR_CASES
    assert clf.classify("what neighbours does INFY.NS have").intent is Intent.SIMILAR_CASES


def test_confidence_is_rule_strength(clf):
    strong = clf.classify("similar cases for BTCUSDT")          # phrase
    weak = clf.classify("anything similar for BTCUSDT")         # keyword only
    assert strong.confidence >= 0.9 and weak.confidence == pytest.approx(0.7)
    assert strong.intent is Intent.SIMILAR_CASES and weak.intent is Intent.SIMILAR_CASES


def test_unknown_and_empty(clf):
    u = clf.classify("the quick brown fox jumps")
    assert u.intent is Intent.UNKNOWN and u.confidence == 0.0
    e = clf.classify("   ")
    assert e.intent is Intent.UNKNOWN and "empty input" in e.validation.issues


def test_ambiguous_is_deterministic(clf):
    a = clf.classify("explain the prediction confidence for BTCUSDT")
    b = clf.classify("explain the prediction confidence for BTCUSDT")
    assert a.intent is b.intent and a.serialize() == b.serialize()   # stable, whatever it resolves to


# --------------------------------------------------------------- entities
def test_entity_extraction():
    ents = extract_entities("explain prediction ab12cd34ef56ab78 for RELIANCE.NS")
    assert ents["prediction_id"] == "ab12cd34ef56ab78" and ents["symbol"] == "RELIANCE.NS"
    assert extract_entities("what version") == {}                 # no false positives


def test_context_fills_missing_entity(clf):
    r = clf.classify("explain this prediction", context={"prediction_id": "deadbeefdeadbeef"})
    assert r.entities["prediction_id"] == "deadbeefdeadbeef" and r.validation.valid


# --------------------------------------------------------------- validation
def test_subject_required_when_missing(clf):
    r = clf.classify("show me the evidence")                      # no prediction/symbol
    assert r.intent is Intent.SHOW_EVIDENCE and not r.validation.valid
    assert "subject" in r.validation.missing_entities


def test_subject_satisfied_by_symbol(clf):
    r = clf.classify("show me the evidence for BTCUSDT")
    assert r.validation.valid and not r.validation.missing_entities


def test_no_subject_intents_are_valid(clf):
    for text, intent in [("system status", Intent.HEALTH), ("what version", Intent.VERSION),
                         ("help me", Intent.HELP), ("learning summary", Intent.LEARNING_SUMMARY)]:
        r = clf.classify(text)
        assert r.intent is intent and r.validation.valid


# --------------------------------------------------------------- determinism / serialization
def test_deterministic_classification(clf):
    a = clf.classify("why confidence for RELIANCE.NS")
    b = clf.classify("why confidence for RELIANCE.NS")
    assert a.stable_dict() == b.stable_dict() and a.serialize() == b.serialize()


def test_serialization_round_trip(clf):
    r = clf.classify("show evidence for BTCUSDT")
    assert IntentClassification.from_dict(r.to_dict()) == r


def test_invalid_input_rejected(clf):
    with pytest.raises(InvalidIntentInputError):
        clf.classify(1234)                                        # type: ignore[arg-type]


# --------------------------------------------------------------- registry
def test_registry_covers_all_intents():
    assert set(available_intents()) == set(Intent)
    assert spec_for(Intent.HEALTH).priority == 10


def test_registry_extensible_without_touching_classifier():
    # A custom registry that teaches HELP a new keyword 'menu' — classifier reads the registry.
    custom = dict(INTENT_REGISTRY)
    base = custom[Intent.HELP]
    custom[Intent.HELP] = IntentSpec(Intent.HELP, base.description,
                                     phrases=base.phrases, keywords=base.keywords + ("menu",),
                                     priority=base.priority)
    assert IntentClassifier(custom).classify("show the menu").intent is Intent.HELP
    assert IntentClassifier().classify("show the menu").intent is Intent.UNKNOWN  # default unchanged


# --------------------------------------------------------------- isolation
def test_intent_module_imports_no_engine_or_llm():
    import ast

    import app.conversation.intent as it
    with open(it.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    forbidden = ("app.ai.sklearn_model", "app.ai.outcome_model", "app.decision_intelligence",
                 "app.chat", "openai", "app.api")
    for name in imported:
        assert not name.startswith(forbidden), f"M2 must not import {name}"
