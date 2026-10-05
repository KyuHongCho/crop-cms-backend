"""Routing plumbing against a STUBBED classifier driven by the question set.

The stub answers each question from tests/routing_questions.py, so these tests prove
the router honours a tool result: out_of_scope declines without touching retrieval, a
lookup runs retrieval, and a named crop scopes it. They say NOTHING about how well a real
model routes: that is measured by hand with scripts/live_chat_eval.py.
"""
import pytest

import app.chat.dispatch as dispatch
from app.chat.classify import classify
from app.chat.llm import ChatLLM, ClassifierResult, ToolCall
from app.main import app
from app.chat.llm import get_chat_llm
from app.router.chat import get_chat_embedder
from scripts import seed
from tests.routing_questions import DOCUMENT_LOOKUP, OUT_OF_SCOPE, QUESTIONS
from tests.test_chat import ask, corpus, secret_key, sql, StubGenerator, token  # noqa: F401  (fixtures)
from tests.test_topic_expansion import FixedEmbedder

BY_TEXT = {q.text: q for q in QUESTIONS}
IDS = [q.text for q in QUESTIONS]


class TableClassifier:
    """Returns the tool call the question's label implies, as a model that is always right would."""

    def __init__(self):
        self.questions = []

    def classify(self, question):
        self.questions.append(question)
        expected = BY_TEXT[question]
        if expected.intent == OUT_OF_SCOPE:
            return ClassifierResult(ToolCall(OUT_OF_SCOPE, {"reason": "stub"}), 10)
        arguments = {"crop_slug": expected.crop_slug} if expected.crop_slug else {}
        return ClassifierResult(ToolCall(DOCUMENT_LOOKUP, arguments), 10)


class CountingEmbedder(FixedEmbedder):
    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        return super().embed(texts)


def test_the_question_set_is_well_formed():
    assert 12 <= len(QUESTIONS) <= 20
    assert len(BY_TEXT) == len(QUESTIONS)  # no duplicate question
    assert {q.intent for q in QUESTIONS} == {DOCUMENT_LOOKUP, OUT_OF_SCOPE}
    assert "how do I propagate basil from cuttings?" in BY_TEXT
    assert "how should I clean between cycles?" in BY_TEXT
    assert any(q.intent == DOCUMENT_LOOKUP and q.crop_slug is None for q in QUESTIONS)
    assert all(q.crop_slug is None for q in QUESTIONS if q.intent == OUT_OF_SCOPE)


@pytest.mark.parametrize("question", QUESTIONS, ids=IDS)
def test_classify_turns_the_tool_result_into_the_labelled_intent_and_crop(question):
    result = classify(question.text, TableClassifier())
    assert (result.intent, result.crop_slug) == (question.intent, question.crop_slug)


@pytest.mark.parametrize("question", QUESTIONS, ids=IDS)
def test_dispatch_honours_the_routing_decision(client, corpus, token, monkeypatch, question):
    classifier, generator, embedder = TableClassifier(), StubGenerator(), CountingEmbedder()
    stub = ChatLLM(classifier, generator)
    app.dependency_overrides[get_chat_llm] = lambda: stub
    app.dependency_overrides[get_chat_embedder] = lambda: embedder
    scored = []  # crop_id of every retrieval scoring call
    real_score_topics = dispatch.score_topics
    monkeypatch.setattr(
        dispatch, "score_topics",
        lambda session, vector, k, crop_id=None: (scored.append(crop_id), real_score_topics(session, vector, k, crop_id))[1],
    )
    try:
        response = ask(client, token, question.text)
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200, response.text
    assert seed.DRAFT_BODY_MARKER not in response.text  # Done 5, across the whole set
    body = response.json()
    assert classifier.questions == [question.text]

    if question.intent == OUT_OF_SCOPE:
        assert body["abstained"] == {"reason": "out_of_scope"}
        assert body["documents"] == []
        assert scored == [] and embedder.calls == 0  # retrieval never touched
        assert generator.calls == []
        return

    assert body["abstained"] is None or body["abstained"]["reason"] == "no_relevant_topics"
    assert embedder.calls == 1 and len(scored) == 1
    if question.crop_slug:
        expected_id = sql("SELECT id FROM crops WHERE slug = :s", s=question.crop_slug).scalar_one()
        assert scored == [expected_id]
    else:
        assert scored == [None]  # every crop competes
