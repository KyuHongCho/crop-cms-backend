"""POST /chat, happy path: classify -> select topics -> generate, with stubs.

No network and no API key: the classifier and generator are stubs that record
what they were given, the embedder is a fixed-vector fake, and the seeded
corpus (scripts/seed.py) is re-embedded with hand-set vectors so retrieval is
deterministic. A stub can prove what the prompt carries; it cannot prove the
model obeys the prompt.
"""
import logging
import secrets

import pytest
from sqlalchemy import text

from app.chat.embeddings import FakeEmbedder
from app.chat.llm import CLASSIFIER_SYSTEM_PROMPT, TOOLS, ChatLLM, ClassifierResult, GeneratorResult, ToolCall
from app.chat.dispatch import TRUNCATION_NOTICE, _unknown_keys
from app.db.migrate_db import engine as sync_engine
from app.main import app
from app.router.chat import get_chat_embedder
from app.chat.llm import get_chat_llm
from scripts import seed
from scripts.reindex import reindex
from tests.test_topic_expansion import FixedEmbedder, _crop, _doc, mix

PASSWORD = "correct horse battery"
QUESTION = "what temperature does basil want?"
CLASSIFIER_TOKENS = 40
GENERATOR_TOKENS = 700


class StubClassifier:
    def __init__(self, tool_call):
        self.tool_call = tool_call
        self.questions = []

    def classify(self, question):
        self.questions.append(question)
        return ClassifierResult(self.tool_call, CLASSIFIER_TOKENS)


class StubGenerator:
    def __init__(self):
        self.calls = []  # (system, user)

    def generate(self, system, user):
        self.calls.append((system, user))
        return GeneratorResult("Basil wants warmth [S1].", GENERATOR_TOKENS)


@pytest.fixture(autouse=True)
def secret_key(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", secrets.token_hex(32))


@pytest.fixture
def llm():
    stub = ChatLLM(StubClassifier(ToolCall("document_lookup", {"crop_slug": "basil"})), StubGenerator())
    app.dependency_overrides[get_chat_llm] = lambda: stub
    app.dependency_overrides[get_chat_embedder] = lambda: FixedEmbedder()
    yield stub
    app.dependency_overrides.clear()


@pytest.fixture
def token(client):
    client.post("/members/signup", json={"email": "grower@example.com", "password": PASSWORD})
    response = client.post("/members/login", json={"email": "grower@example.com", "password": PASSWORD})
    return response.json()["access_token"]


def ask(client, token, question=QUESTION):
    return client.post("/chat", json={"question": question},
                       headers={"Authorization": f"Bearer {token}"})


def sql(statement, **params):
    with sync_engine.begin() as connection:
        return connection.execute(text(statement), params)


@pytest.fixture
def corpus():
    """The seeded corpus, re-embedded; basil's temperature and propagation topics
    (the draft's chunk included) sit near the query, every other basil chunk far."""
    seed.main()
    reindex(sync_engine, FakeEmbedder(), out=lambda *_: None)

    def place(topic, similarity):
        sql(
            "UPDATE item_chunks SET embedding = CAST(:v AS vector) WHERE item_id IN ("
            "SELECT i.id FROM items i JOIN crops c ON c.id = i.crop_id "
            "WHERE c.slug = 'basil' AND i.topic = :topic)",
            v=str(mix(similarity)), topic=topic,
        )

    place("optimal-temperature", 0.9)
    place("propagation", 0.8)


def stored(source):
    row = sql("SELECT reference, url, licence_note, condition FROM items WHERE source = :s", s=source).one()
    return row._asdict()


# --- auth ----------------------------------------------------------------------

def test_chat_without_a_token_is_401_and_calls_no_model(client, llm):
    response = client.post("/chat", json={"question": QUESTION})
    assert response.status_code == 401
    assert llm.classifier.questions == [] and llm.generator.calls == []


# --- the happy path ------------------------------------------------------------

def test_a_basil_temperature_question_gets_a_cited_answer_with_all_three_sources(client, llm, corpus, token):
    response = ask(client, token)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["answer"] == "Basil wants warmth [S1]."
    assert body["abstained"] is None
    assert "optimal-temperature" in body["topics_used"]
    assert llm.classifier.questions == [QUESTION]
    assert len(llm.generator.calls) == 1

    keys = [d["key"] for d in body["documents"]]
    assert keys == [f"S{n}" for n in range(1, len(keys) + 1)]
    temperature = {d["source"] for d in body["documents"] if d["topic"] == "optimal-temperature"}
    assert temperature == {
        "FAO ECOCROP (id 1547)", "Chang, Alderson & Wright (2005)", "Walters & Currey (2019)",
    }


def test_the_classifier_tool_list_is_document_lookup_and_out_of_scope():
    assert [tool["name"] for tool in TOOLS] == ["document_lookup", "out_of_scope"]
    assert "D-2" not in CLASSIFIER_SYSTEM_PROMPT
    out_of_scope = TOOLS[1]["input_schema"]
    assert out_of_scope["required"] == ["reason"] and "reason" in out_of_scope["properties"]


def test_the_crop_slug_from_the_classifier_scopes_retrieval(client, llm, corpus, token):
    other = _crop("sorrel")
    _doc(other, "sorrel-only-topic", 0, [mix(0.99)])  # beats every basil topic
    assert "sorrel-only-topic" not in ask(client, token).json()["topics_used"]

    llm.classifier.tool_call = ToolCall("document_lookup", {})  # no crop named: every crop competes
    assert "sorrel-only-topic" in ask(client, token).json()["topics_used"]


# --- the condition and the "does not cover" rule are in the prompt -----------

def test_the_generator_prompt_carries_each_conditions_and_the_not_covered_rule(client, llm, corpus, token):
    body = ask(client, token, "how do I propagate basil from cuttings?").json()
    ((system, user),) = llm.generator.calls

    assert "do NOT cover" in system
    assert "[S1]" in user
    conditions = [sql("SELECT condition FROM items WHERE id = :i", i=d["id"]).scalar_one()
                  for d in body["documents"]]
    assert any(conditions)  # the seeded documents do carry conditions
    for condition in conditions:
        assert f"Condition: {condition or 'not stated'}" in user
    # Next to its key, not just somewhere in the prompt.
    first = sql("SELECT title, condition FROM items WHERE id = :i", i=body["documents"][0]["id"]).one()
    assert f"[S1] {first.title}\nCondition: {first.condition or 'not stated'}" in user


def test_provenance_is_rendered_by_the_server_not_put_in_the_prompt(client, llm, corpus, token):
    body = ask(client, token).json()
    ((_, user),) = llm.generator.calls
    for document in body["documents"]:
        assert document["reference"] not in user
        if document["licence_note"]:
            assert document["licence_note"] not in user


# --- reference, url and licence_note verbatim --------------------------------

@pytest.mark.parametrize("source", ["Chang, Alderson & Wright (2005)", "FAO ECOCROP (id 1547)"])
def test_a_documents_reference_url_and_licence_note_come_back_verbatim(client, llm, corpus, token, source):
    want = stored(source)
    assert want["licence_note"]  # the CC BY via-note / the FAO note
    documents = ask(client, token).json()["documents"]
    (got,) = [d for d in documents if d["source"] == source]
    assert (got["reference"], got["url"], got["licence_note"]) == (
        want["reference"], want["url"], want["licence_note"],
    )


# --- drafts never leak -------------------------------------------------------------

def test_the_unpublished_draft_never_reaches_the_prompt_or_the_response(client, llm, corpus, token):
    marker = seed.DRAFT_BODY_MARKER
    assert sql("SELECT count(*) FROM items WHERE body LIKE :m AND NOT published",
               m=f"%{marker}%").scalar_one() == 1
    assert sql("SELECT count(*) FROM item_chunks c JOIN items i ON i.id = c.item_id "
               "WHERE i.body LIKE :m", m=f"%{marker}%").scalar_one() >= 1  # it is embedded

    response = ask(client, token, "how do I propagate basil?")
    assert "propagation" in response.json()["topics_used"]
    ((system, user),) = llm.generator.calls
    assert marker not in response.text and marker not in user and marker not in system


# --- the token budget -----------------------------------------------------------------

def test_reported_usage_of_both_calls_is_recorded(client, llm, corpus, token):
    assert ask(client, token).status_code == 200
    assert sql("SELECT tokens_used_today FROM members").scalar_one() == CLASSIFIER_TOKENS + GENERATOR_TOKENS


def test_an_exhausted_member_gets_429_before_any_model_call(client, llm, corpus, token):
    sql("UPDATE members SET tokens_used_today = tokens_budget_daily")
    response = ask(client, token)
    assert response.status_code == 429 and "retry-after" in response.headers
    assert llm.classifier.questions == [] and llm.generator.calls == []


def test_a_topic_too_big_for_the_context_budget_is_413_before_generation(client, llm, token):
    crop = _crop("basil")
    (item_id,) = _doc(crop, "huge", 0, [mix(0.9)])
    sql("UPDATE items SET body = :b WHERE id = :i", b="x" * 40_000, i=item_id)
    response = ask(client, token)
    assert response.status_code == 413
    assert response.json()["detail"]["topic"] == "huge"
    assert llm.generator.calls == []


# --- an unknown crop slug ----------------------------------------------------------

def test_an_unknown_crop_slug_runs_unscoped_and_names_the_crops(client, llm, corpus, token):
    other = _crop("sorrel")
    _doc(other, "sorrel-only-topic", 0, [mix(0.99)])
    llm.classifier.tool_call = ToolCall("document_lookup", {"crop_slug": "no-such-crop"})
    body = ask(client, token).json()
    assert "sorrel-only-topic" in body["topics_used"]  # every crop competed
    assert body["topics_used_crops"][body["topics_used"].index("sorrel-only-topic")] == "sorrel"
    assert all(d["crop_slug"] for d in body["documents"])


# --- crop labels when the crop was not fixed -------------------------------------

def test_an_unscoped_question_distinguishes_a_topic_name_shared_by_two_crops(client, llm, corpus, token):
    endive, sorrel = _crop("endive"), _crop("sorrel")
    _doc(endive, "shared-topic", 0, [mix(0.99)])
    _doc(sorrel, "shared-topic", 0, [mix(0.98)])
    llm.classifier.tool_call = ToolCall("document_lookup", {})
    body = ask(client, token).json()
    pairs = list(zip(body["topics_used"], body["topics_used_crops"]))
    assert ("shared-topic", "endive") in pairs and ("shared-topic", "sorrel") in pairs
    shared = [d for d in body["documents"] if d["topic"] == "shared-topic"]
    assert {d["crop_slug"] for d in shared} == {"endive", "sorrel"}
    prompt = llm.generator.calls[-1][1]
    assert "(endive) " in prompt and "(sorrel) " in prompt


def test_unscoped_prompt_groups_each_crops_sources_and_orders_crops_by_best_topic(client, llm, token):
    endive, sorrel = _crop("endive"), _crop("sorrel")
    _doc(endive, "t1", 0, [mix(0.9)])
    _doc(sorrel, "t2", 0, [mix(0.8)])
    _doc(endive, "t3", 0, [mix(0.7)])  # score order: endive, sorrel, endive
    llm.classifier.tool_call = ToolCall("document_lookup", {})
    body = ask(client, token).json()
    assert body["topics_used"] == ["t1", "t2", "t3"]
    assert len(body["topics_used_crops"]) == len(body["topics_used"])
    prompt = llm.generator.calls[-1][1]
    assert [d["key"] for d in body["documents"]] == ["S1", "S2", "S3"]
    assert [d["crop_slug"] for d in body["documents"]] == ["endive", "endive", "sorrel"]
    assert [d["title"] for d in body["documents"]] == ["t1 0-0", "t3 0-0", "t2 0-0"]
    positions = [prompt.index(f"[{d['key']}] ({d['crop_slug']}) {d['title']}") for d in body["documents"]]
    assert positions == sorted(positions)


def test_unscoped_single_crop_result_is_labelled_in_the_prompt_and_names_the_crop(client, llm, token):
    endive = _crop("endive")
    _doc(endive, "t1", 0, [mix(0.9)])
    llm.classifier.tool_call = ToolCall("document_lookup", {})
    body = ask(client, token).json()
    assert [d["crop_slug"] for d in body["documents"]] == ["endive"]
    assert body["topics_used_crops"] == ["endive"]
    assert "(endive)" in llm.generator.calls[-1][1]


def test_unscoped_dropped_topics_name_their_crop(client, llm, corpus, token, monkeypatch):
    import app.chat.dispatch as dispatch
    real = dispatch.assemble_within_budget
    monkeypatch.setattr(dispatch, "assemble_within_budget", lambda c: real(c, budget=max(x.context_chars for x in c)))
    llm.classifier.tool_call = ToolCall("document_lookup", {})
    body = ask(client, token).json()
    assert body["dropped"] and all(d["crop_slug"] for d in body["dropped"])


def test_a_crop_scoped_response_names_no_crops_and_labels_nothing(client, llm, corpus, token):
    body = ask(client, token).json()  # classifier fixes basil
    assert body["topics_used_crops"] == []
    assert all(d["crop_slug"] is None for d in body["documents"] + body["dropped"])
    assert "(basil)" not in llm.generator.calls[-1][1]


# --- refusal paths ------------------------------------------------------------

class Boom:
    """Fails the test if retrieval is reached."""

    def embed(self, texts):
        raise AssertionError("retrieval must not run")


OUT_OF_SCOPE_QUESTIONS = ["what is the capital of France?", "write me a poem", "how do I fix my car?"]


@pytest.mark.parametrize("question", OUT_OF_SCOPE_QUESTIONS)
def test_an_out_of_scope_tool_call_declines_without_retrieval_or_generation(client, llm, corpus, token, question):
    llm.classifier.tool_call = ToolCall("out_of_scope", {"reason": "not about crops"})
    app.dependency_overrides[get_chat_embedder] = lambda: Boom()
    response = ask(client, token, question)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["abstained"] == {"reason": "out_of_scope"}
    assert "growing crops" in body["answer"]  # names what the service can answer
    assert body["documents"] == [] and body["topics_used"] == []
    assert llm.generator.calls == []
    assert "not about crops" not in response.text  # model output is not echoed
    assert sql("SELECT tokens_used_today FROM members").scalar_one() == CLASSIFIER_TOKENS


def test_a_question_with_no_relevant_topic_abstains_without_generation(client, llm, token):
    _crop("sorrel")  # a crop with no chunks: nothing to score
    llm.classifier.tool_call = ToolCall("document_lookup", {"crop_slug": "sorrel"})
    response = ask(client, token)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["abstained"] == {"reason": "no_relevant_topics"}
    assert body["documents"] == [] and body["topics_used"] == []
    assert llm.generator.calls == []


@pytest.mark.parametrize("tool_call", [
    None,
    ToolCall("no_such_tool", {"crop_slug": "basil"}),
    ToolCall("document_lookup", {"crop_slug": 7}),
    ToolCall("document_lookup", {"crop_slug": ["basil"]}),
    ToolCall("document_lookup", {}),
    ToolCall("document_lookup", None),
    "garbage",
])
def test_a_malformed_routing_result_falls_back_to_document_lookup_never_500(client, llm, corpus, token, tool_call):
    llm.classifier.tool_call = tool_call
    response = ask(client, token)
    assert response.status_code == 200, response.text
    assert response.json()["abstained"] is None
    assert len(llm.generator.calls) == 1


def test_a_classifier_result_that_is_not_a_classifier_result_falls_back(client, llm, corpus, token):
    llm.classifier.classify = lambda question: object()
    assert ask(client, token).status_code == 200
    assert len(llm.generator.calls) == 1


def test_the_draft_marker_appears_in_no_response_across_the_question_set(client, llm, corpus, token):
    marker = seed.DRAFT_BODY_MARKER
    questions = [QUESTION, "how do I propagate basil?", "how do I propagate basil from cuttings?",
                 *OUT_OF_SCOPE_QUESTIONS]
    for n, question in enumerate(questions):
        llm.classifier.tool_call = (
            ToolCall("out_of_scope", {"reason": "x"}) if n >= 3 else ToolCall("document_lookup", {"crop_slug": "basil"})
        )
        assert marker not in ask(client, token, question).text
    llm.classifier.tool_call = ToolCall("document_lookup", {"crop_slug": "no-such-crop"})
    assert marker not in ask(client, token).text


# --- provider errors, real clients through a mock transport ---------------------

def _provider_error_response(status, error_type, message, headers=None):
    import httpx2

    return httpx2.Response(status, headers=headers or {},
                           json={"type": "error", "error": {"type": error_type, "message": message}})


PROVIDER_ERRORS = [
    (400, "invalid_request_error", "You have reached your specified API usage limits."),
    (429, "rate_limit_error", "enforced_spend_limit_reached"),
    (500, "api_error", "Internal server error"),
]


@pytest.mark.parametrize("status_code,error_type,message", PROVIDER_ERRORS)
def test_a_provider_error_is_a_plain_503_without_the_provider_text(client, corpus, token, status_code, error_type, message, caplog):
    from app.chat.llm import AnthropicClassifier, AnthropicGenerator

    calls = []

    def handler(request):
        calls.append(request)
        return _provider_error_response(status_code, error_type, message)

    app.dependency_overrides[get_chat_llm] = lambda: ChatLLM(
        AnthropicClassifier(_mock_client(handler)), AnthropicGenerator(_mock_client(handler)))
    app.dependency_overrides[get_chat_embedder] = lambda: FixedEmbedder()
    try:
        response = ask(client, token)
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 503, response.text
    assert set(response.json()) == {"detail"}
    assert message not in response.text and "usage limits" not in response.text
    assert any(message in record.getMessage() or (record.exc_info and message in str(record.exc_info[1].__cause__))
               for record in caplog.records)  # the operator can see the cause
    assert len(calls) == 1  # no retry (max_retries=0 in the test client), no generation call
    assert sql("SELECT tokens_used_today FROM members").scalar_one() == 0


def test_a_generator_provider_error_is_also_503(client, corpus, token):
    from app.chat.llm import AnthropicGenerator

    def handler(request):
        return _provider_error_response(429, "rate_limit_error", "enforced_spend_limit_reached")

    app.dependency_overrides[get_chat_llm] = lambda: ChatLLM(
        StubClassifier(ToolCall("document_lookup", {"crop_slug": "basil"})),
        AnthropicGenerator(_mock_client(handler)))
    app.dependency_overrides[get_chat_embedder] = lambda: FixedEmbedder()
    try:
        response = ask(client, token)
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 503, response.text
    assert sql("SELECT tokens_used_today FROM members").scalar_one() == CLASSIFIER_TOKENS


def test_a_missing_api_key_is_503_not_500_and_an_over_budget_member_still_gets_429(client, token, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    app.dependency_overrides[get_chat_embedder] = lambda: FixedEmbedder()
    try:  # the real get_chat_llm, no override
        response = ask(client, token)
        assert response.status_code == 503, response.text
        assert set(response.json()) == {"detail"}

        sql("UPDATE members SET tokens_used_today = tokens_budget_daily")
        assert ask(client, token).status_code == 429
    finally:
        app.dependency_overrides.clear()


# --- the REAL get_chat_embedder (no override), EMBEDDER=openai, empty key ------------
# Every other /chat test overrides get_chat_embedder; these do not, so the real
# OpenAI path runs. No network: an empty OPENAI_API_KEY fails before anything is sent.

@pytest.fixture
def real_embedder(monkeypatch, llm):
    monkeypatch.setenv("EMBEDDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    del app.dependency_overrides[get_chat_embedder]  # the llm fixture stubs the model, not the embedder


def test_real_embedder_without_a_key_is_503_for_a_normal_member(client, token, real_embedder):
    response = ask(client, token)
    assert response.status_code == 503, response.text
    assert set(response.json()) == {"detail"}
    assert "OPENAI" not in response.text


def test_real_embedder_without_a_key_still_gives_an_exhausted_member_429(client, token, real_embedder):
    sql("UPDATE members SET tokens_used_today = tokens_budget_daily")
    assert ask(client, token).status_code == 429


def test_real_embedder_without_a_key_still_gives_a_bad_body_422(client, token, real_embedder):
    response = client.post("/chat", json={}, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 422


def test_an_embedder_that_raises_is_503_and_the_classifier_call_stays_charged(client, token, llm):
    class Boom:
        model = "boom"

        def embed(self, texts):
            raise RuntimeError("provider outage sk-secret")

    app.dependency_overrides[get_chat_embedder] = lambda: Boom()
    response = ask(client, token)
    assert response.status_code == 503, response.text
    assert "sk-secret" not in response.text
    # The classifier runs before embed, so its tokens are charged with no answer
    # (charging the classifier call with no answer is deliberate).
    assert sql("SELECT tokens_used_today FROM members").scalar_one() == CLASSIFIER_TOKENS


def test_a_whitespace_only_question_is_422_and_never_reaches_the_classifier(client, token, llm):
    assert ask(client, token, "   \n\t ").status_code == 422
    assert llm.classifier.questions == []


def test_the_question_is_stripped(client, token, llm):
    ask(client, token, "  " + QUESTION + " ")
    assert llm.classifier.questions == [QUESTION]


def test_an_absent_sdk_is_503(client, token, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "anthropic", None)  # import anthropic -> ImportError
    app.dependency_overrides[get_chat_embedder] = lambda: FixedEmbedder()
    try:
        assert ask(client, token).status_code == 503
    finally:
        app.dependency_overrides.clear()


# --- the real clients, through a mock transport (no network, no key) -----------------
# Imports `anthropic` for real: needs the image built from requirements.txt. A missing
# SDK fails this test loudly rather than skipping it.

def _mock_client(handler):
    import anthropic
    import httpx2

    return anthropic.Anthropic(
        api_key="test-key", max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(handler))
    )


def _message(content, input_tokens, output_tokens, stop_reason="end_turn"):
    return {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "m",
        "content": content, "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


def test_the_real_classifier_sends_a_forced_tool_choice_and_parses_the_tool_call():
    import json

    import httpx2

    from app.chat.llm import AnthropicClassifier

    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx2.Response(200, json=_message(
            [{"type": "tool_use", "id": "t1", "name": "document_lookup",
              "input": {"crop_slug": "basil"}}], 11, 4))

    result = AnthropicClassifier(_mock_client(handler)).classify(QUESTION)
    (body,) = sent
    assert body["tool_choice"] == {"type": "any"}
    assert body["tools"] == TOOLS
    assert "temperature" not in body and 0 < body["max_tokens"] <= 256
    assert result.tool_call == ToolCall("document_lookup", {"crop_slug": "basil"})
    assert result.tokens == 15


def test_the_real_generator_sends_the_system_prompt_and_sums_reported_usage():
    import json

    import httpx2

    from app.chat.llm import AnthropicGenerator

    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx2.Response(200, json=_message([{"type": "text", "text": "Warm [S1]."}], 30, 5))

    result = AnthropicGenerator(_mock_client(handler)).generate("SYSTEM", "USER")
    (body,) = sent
    assert body["system"] == "SYSTEM"
    assert body["messages"] == [{"role": "user", "content": "USER"}]
    assert "temperature" not in body and "tools" not in body and body["max_tokens"] > 0
    assert (result.text, result.tokens) == ("Warm [S1].", 35)


# --- truncated: the answer was cut at the generator's max_tokens -------------------

def _real_generator_llm(stop_reason):
    import httpx2

    from app.chat.llm import AnthropicGenerator

    def handler(request):
        return httpx2.Response(200, json=_message(
            [{"type": "text", "text": "Warm, and then the answer is cut"}], 120, 1024, stop_reason))

    app.dependency_overrides[get_chat_llm] = lambda: ChatLLM(
        StubClassifier(ToolCall("document_lookup", {"crop_slug": "basil"})),
        AnthropicGenerator(_mock_client(handler)))
    app.dependency_overrides[get_chat_embedder] = lambda: FixedEmbedder()


def test_a_max_tokens_stop_sets_truncated_appends_the_notice_and_leaves_usage_alone(client, corpus, token):
    _real_generator_llm("max_tokens")
    try:
        response = ask(client, token)
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["truncated"] is True
    assert body["answer"] == "Warm, and then the answer is cut" + "\n\n" + TRUNCATION_NOTICE
    assert sql("SELECT tokens_used_today FROM members").scalar_one() == CLASSIFIER_TOKENS + 1144


def test_an_end_turn_stop_is_not_truncated(client, corpus, token):
    _real_generator_llm("end_turn")
    try:
        response = ask(client, token)
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["truncated"] is False
    assert TRUNCATION_NOTICE not in body["answer"]


def test_decline_and_abstain_responses_are_not_truncated(client, llm, token):
    llm.classifier.tool_call = ToolCall("out_of_scope", {"reason": "x"})
    body = ask(client, token).json()
    assert body["truncated"] is False and TRUNCATION_NOTICE not in body["answer"]
    _crop("sorrel")
    llm.classifier.tool_call = ToolCall("document_lookup", {"crop_slug": "sorrel"})
    body = ask(client, token).json()
    assert body["abstained"] == {"reason": "no_relevant_topics"} and body["truncated"] is False
    assert TRUNCATION_NOTICE not in body["answer"]


def test_model_names_come_from_the_env_and_an_empty_value_falls_back(monkeypatch):
    from app.chat.llm import DEFAULT_MODEL, _model_from_env

    monkeypatch.delenv("CHAT_MODEL_X", raising=False)
    assert _model_from_env("CHAT_MODEL_X") == DEFAULT_MODEL
    monkeypatch.setenv("CHAT_MODEL_X", "")
    assert _model_from_env("CHAT_MODEL_X") == DEFAULT_MODEL
    monkeypatch.setenv("CHAT_MODEL_X", "some-model")
    assert _model_from_env("CHAT_MODEL_X") == "some-model"


# --- crop_slug normalisation: the tool asks for lower case but cannot force it -------

@pytest.mark.parametrize("slug", ["Basil", " basil", "BASIL", "basil "])
def test_a_crop_slug_with_case_or_padding_still_scopes_retrieval(client, llm, token, slug):
    _doc(_crop("basil"), "t1", 0, [mix(0.90)])
    _doc(_crop("tomato"), "t2", 0, [mix(0.95)])  # unscoped, tomato would come first
    llm.classifier.tool_call = ToolCall("document_lookup", {"crop_slug": slug})
    body = ask(client, token, "what temperature does basil want?").json()
    assert body["topics_used_crops"] == []  # empty: the crop was fixed
    assert [d["title"] for d in body["documents"]] == ["t1 0-0"]


def test_a_cased_slug_keeps_other_crops_out_of_the_prompt(client, llm, token):
    _doc(_crop("basil"), "t1", 0, [mix(0.90)])
    tomato = _crop("tomato")
    for n, score in enumerate([0.97, 0.96, 0.95]):
        _doc(tomato, f"tomato-{n}", 0, [mix(score)])
    llm.classifier.tool_call = ToolCall("document_lookup", {"crop_slug": "Basil"})
    body = ask(client, token, "what temperature does basil want?").json()
    prompt = llm.generator.calls[-1][1]
    assert "(tomato)" not in prompt and "tomato" not in prompt
    assert [d["title"] for d in body["documents"]] == ["t1 0-0"]


@pytest.mark.parametrize("slug", ["sweet pepper", "sweet_pepper", "Sweet Pepper", " sweet-pepper\n", "SWEET-PEPPER"])
def test_a_multi_word_slug_with_spaces_or_underscores_still_scopes_retrieval(client, llm, token, slug):
    _doc(_crop("sweet-pepper"), "p1", 0, [mix(0.50)])
    _doc(_crop("tomato"), "t2", 0, [mix(0.95)])  # unscoped, tomato would come first
    llm.classifier.tool_call = ToolCall("document_lookup", {"crop_slug": slug})
    body = ask(client, token, "what temperature does sweet pepper want?").json()
    assert body["topics_used_crops"] == []
    assert [d["title"] for d in body["documents"]] == ["p1 0-0"]


def test_a_doubled_hyphen_slug_stays_unscoped(client, llm, token):
    """Limitation, deliberate: only whitespace and underscores are mapped to a hyphen,
    so 'sweet--pepper' (like an en dash or a quoted slug) is not a known crop and
    retrieval runs unscoped."""
    _doc(_crop("sweet-pepper"), "p1", 0, [mix(0.50)])
    _doc(_crop("tomato"), "t2", 0, [mix(0.95)])
    llm.classifier.tool_call = ToolCall("document_lookup", {"crop_slug": "sweet--pepper"})
    body = ask(client, token, "what temperature does sweet pepper want?").json()
    assert body["topics_used_crops"] != []


@pytest.mark.parametrize("slug", [123, "", "   ", None])
def test_a_non_string_or_blank_slug_leaves_retrieval_unscoped(client, llm, token, slug):
    _doc(_crop("basil"), "t1", 0, [mix(0.90)])
    _doc(_crop("tomato"), "t2", 0, [mix(0.95)])
    llm.classifier.tool_call = ToolCall("document_lookup", {"crop_slug": slug})
    response = ask(client, token, "what temperature does basil want?")
    assert response.status_code == 200, response.text
    assert response.json()["topics_used_crops"] != []  # unscoped: crops are named


# --- answers that cite keys that were not sent are logged, not refused -----------------

class TextGenerator:
    def __init__(self, text):
        self.text = text

    def generate(self, system, user):
        return GeneratorResult(self.text, GENERATOR_TOKENS)


def _three_crops():
    for crop, score in [("basil", 0.9), ("tomato", 0.8), ("lettuce", 0.7)]:
        _doc(_crop(crop), f"{crop}-t", 0, [mix(score)])


def _ask_with_text(client, token, text):
    app.dependency_overrides[get_chat_llm] = lambda: ChatLLM(
        StubClassifier(ToolCall("document_lookup", {})), TextGenerator(text))
    app.dependency_overrides[get_chat_embedder] = lambda: FixedEmbedder()
    try:
        return ask(client, token)
    finally:
        app.dependency_overrides.clear()


def test_an_answer_citing_a_key_that_was_not_sent_is_returned_and_logged(client, token, caplog):
    _three_crops()
    text = "Basil fruits best at 25 C [S1, S4]."
    with caplog.at_level(logging.WARNING, logger="app.chat.dispatch"):
        response = _ask_with_text(client, token, text)
    assert response.status_code == 200, response.text
    assert response.json()["answer"] == text
    records = [r for r in caplog.records if r.name == "app.chat.dispatch"]
    assert len(records) == 1 and "S4" in records[0].getMessage()


def test_an_answer_citing_only_sent_keys_logs_nothing(client, token, caplog):
    _three_crops()
    with caplog.at_level(logging.WARNING, logger="app.chat.dispatch"):
        response = _ask_with_text(client, token, "Basil [S1], tomato [S2], lettuce [S3].")
    assert response.status_code == 200, response.text
    assert [r for r in caplog.records if r.name == "app.chat.dispatch"] == []


@pytest.mark.parametrize("text,expected", [
    ("claim [S4].", ["S4"]),
    ("claim [S1, S4].", ["S4"]),
    ("claim [S1][S4].", ["S4"]),
    ("claim [S10].", ["S10"]),
    ("claim [S1] and [S2].", []),
    ("[S1, S2, S3]", []),
    ("no citations", []),
    ("claim (S4).", []),  # documented limitation: only square brackets are read
    ("claim [s4].", []),
    ("claim [S4", []),
])
def test_unknown_keys(text, expected):
    assert _unknown_keys(text, {"S1": 1, "S2": 2, "S3": 3}) == expected


def test_the_real_classifier_sends_the_several_crops_wording():
    """Proves the wording is what is sent to the model. It cannot prove the model obeys
    it: that is measured only by the live eval."""
    import json

    import httpx2

    from app.chat.llm import AnthropicClassifier

    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx2.Response(200, json=_message(
            [{"type": "tool_use", "id": "t1", "name": "document_lookup", "input": {}}], 11, 4))

    AnthropicClassifier(_mock_client(handler)).classify(QUESTION)
    (body,) = sent
    assert "compares or mentions several crops" in body["system"]
    lookup = next(tool for tool in body["tools"] if tool["name"] == "document_lookup")
    assert "two or more crops" in lookup["input_schema"]["properties"]["crop_slug"]["description"]
