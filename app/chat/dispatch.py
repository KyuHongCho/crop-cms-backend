"""Run one chat question: classify, select topics, generate.

Citations are keys, not inline provenance: the prompt carries "[S1] title +
condition + body", the server holds {"S1": document} and renders provenance
into the response from the retrieved set. That server-rendered `documents` list
is the authoritative provenance. The model's prose is returned as written and
its keys are not validated, so an invented "[S#]" in the answer text is NOT
prevented.

Token usage is recorded right after each model call (reported counts).
"""
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

import app.crud.retrieval as retrieval_crud
from app.auth.budget import record_usage
from app.chat.classify import OUT_OF_SCOPE, classify
from app.chat.embeddings import Embedder
from app.chat.llm import GENERATOR_SYSTEM_PROMPT, ChatLLM, LLMUnavailable
from app.chat.retrieval import NoRelevantTopics, fetch_candidates, score_topics, select_topics
from app.crud.retrieval import TOPIC_SELECTION_K, TopicCandidate, assemble_within_budget
from app.schema.chat import Abstention, ChatResponse, CitedDocument, DroppedChatTopic
from app.schema.retrieval import RetrievedDocument


DECLINE_MESSAGE = (
    "I can only answer questions about growing crops, such as temperature, propagation, "
    "and pests and disease, from the published crop documents."
)
NO_TOPIC_MESSAGE = (
    "The published crop documents have nothing relevant to this question, so I have not "
    "tried to answer it."
)


def build_prompt(
    question: str, kept: list[TopicCandidate], crop_slugs: dict[int, str] | None = None
) -> tuple[str, dict[str, CitedDocument]]:
    """`crop_slugs` is given only when the retrieval was not crop-scoped; sources
    are then labelled and grouped by crop (kept is highest score first, so the
    groups follow the best-scoring topic of each crop)."""
    sources: dict[str, CitedDocument] = {}
    blocks = []
    if crop_slugs is not None:
        crop_order = list(dict.fromkeys(c.crop_id for c in kept))
        kept = sorted(kept, key=lambda c: crop_order.index(c.crop_id))  # stable
    for candidate in kept:
        slug = crop_slugs.get(candidate.crop_id) if crop_slugs is not None else None
        label = f"({slug}) " if slug and len(set(c.crop_id for c in kept)) > 1 else ""
        for item in candidate.documents:
            key = f"S{len(sources) + 1}"
            sources[key] = CitedDocument(key=key, crop_slug=slug, **RetrievedDocument.model_validate(item).model_dump())
            blocks.append(
                f"[{key}] {label}{item.title}\n"
                f"Condition: {item.condition or 'not stated'}\n"
                f"{item.body}"
            )
    return f"Question: {question}\n\nSources:\n\n" + "\n\n".join(blocks), sources


async def answer(
    db: AsyncSession, member_id: int, question: str, llm: ChatLLM, embedder: Embedder
) -> ChatResponse:
    classification = await run_in_threadpool(classify, question, llm.classifier)
    await record_usage(db, member_id, classification.tokens)
    if classification.intent == OUT_OF_SCOPE:  # gate 1: before retrieval, nothing else runs
        return ChatResponse(answer=DECLINE_MESSAGE, abstained=Abstention(reason=OUT_OF_SCOPE))

    crop_id = None
    if classification.crop_slug:
        # An unknown slug leaves retrieval unscoped; the response then names each
        # topic's crop (D7), exactly as when no crop was given.
        crop_id = await retrieval_crud.get_crop_id_by_slug(db, classification.crop_slug)

    # Rules 0-3 of app/chat/retrieval.py's retrieve_topics, with the embedding
    # call moved off the event loop; the selection itself is unchanged.
    try:
        (query_vector,) = await run_in_threadpool(embedder.embed, [question])
    except Exception as exc:  # missing key, usage limit, outage: all 503, cause logged by the router
        raise LLMUnavailable("embedding failed") from exc
    candidates = await db.run_sync(
        lambda session: fetch_candidates(
            session, score_topics(session, query_vector, TOPIC_SELECTION_K, crop_id)
        )
    )
    try:
        selected = select_topics(candidates)
    except NoRelevantTopics:  # gate 3: no generation call
        return ChatResponse(answer=NO_TOPIC_MESSAGE, abstained=Abstention(reason="no_relevant_topics"))
    kept, dropped = assemble_within_budget(selected)

    # Crop labels only when retrieval was not crop-scoped; slugs resolved once.
    crop_slugs = None
    if crop_id is None:
        crop_slugs = await retrieval_crud.get_crop_slugs(db, {c.crop_id for c in selected})

    prompt, sources = build_prompt(question, kept, crop_slugs)
    result = await run_in_threadpool(llm.generator.generate, GENERATOR_SYSTEM_PROMPT, prompt)
    await record_usage(db, member_id, result.tokens)

    return ChatResponse(
        answer=result.text,
        documents=list(sources.values()),
        topics_used=[candidate.topic for candidate in kept],
        topics_used_crops=[crop_slugs.get(c.crop_id, "") for c in kept] if crop_slugs is not None else [],
        dropped=[
            DroppedChatTopic(
                topic=d.topic, score=d.score,
                crop_slug=crop_slugs.get(d.crop_id) if crop_slugs is not None else None,
                document_count=d.document_count, context_chars=d.context_chars,
            )
            for d in dropped
        ],
    )
