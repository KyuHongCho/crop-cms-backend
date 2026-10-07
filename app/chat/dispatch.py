"""Run one chat question: classify, select topics, generate.

Provenance is server-rendered from the retrieved set; the model's prose is returned as written,
so an invented "[S#]" is NOT prevented (a warning is logged for unsent keys).
"""
import logging
import re

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


logger = logging.getLogger(__name__)


def _unknown_keys(text: str, sources: dict) -> list[str]:
    """Source keys the answer cites that were never sent, e.g. [S4] with three sources."""
    cited = {key for group in re.findall(r"\[([^\]]*)\]", text) for key in re.findall(r"S\d+", group)}
    return sorted(cited - sources.keys())


DECLINE_MESSAGE = (
    "I can only answer questions about growing crops, such as temperature, propagation, "
    "and pests and disease, from the published crop documents."
)
NO_TOPIC_MESSAGE = (
    "The published crop documents have nothing relevant to this question, so I have not "
    "tried to answer it."
)
TRUNCATION_NOTICE = "[Answer cut off at the length limit; it may be incomplete.]"


def build_prompt(
    question: str, kept: list[TopicCandidate], crop_slugs: dict[int, str] | None = None
) -> tuple[str, dict[str, CitedDocument]]:
    """`crop_slugs` is given only when retrieval was not crop-scoped; sources are then labelled
    and grouped by crop, following each crop's best-scoring topic."""
    sources: dict[str, CitedDocument] = {}
    blocks = []
    if crop_slugs is not None:
        crop_order = list(dict.fromkeys(c.crop_id for c in kept))
        kept = sorted(kept, key=lambda c: crop_order.index(c.crop_id))  # stable
    for candidate in kept:
        slug = crop_slugs.get(candidate.crop_id) if crop_slugs is not None else None
        label = f"({slug}) " if slug else ""
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
    if classification.intent == OUT_OF_SCOPE:  # before retrieval; nothing else runs
        return ChatResponse(answer=DECLINE_MESSAGE, abstained=Abstention(reason=OUT_OF_SCOPE))

    crop_id = None
    if classification.crop_slug:
        # an unknown slug leaves retrieval unscoped; the response then names each topic's crop.
        crop_id = await retrieval_crud.get_crop_id_by_slug(db, classification.crop_slug)

    # Rules 0-3 of retrieve_topics, with the embedding call moved off the event loop.
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
    except NoRelevantTopics:  # no generation call
        return ChatResponse(answer=NO_TOPIC_MESSAGE, abstained=Abstention(reason="no_relevant_topics"))
    kept, dropped = assemble_within_budget(selected)

    # crop labels only when retrieval was not crop-scoped.
    crop_slugs = None
    if crop_id is None:
        crop_slugs = await retrieval_crud.get_crop_slugs(db, {c.crop_id for c in selected})

    prompt, sources = build_prompt(question, kept, crop_slugs)
    result = await run_in_threadpool(llm.generator.generate, GENERATOR_SYSTEM_PROMPT, prompt)
    await record_usage(db, member_id, result.tokens)
    if unknown := _unknown_keys(result.text, sources):  # logged, not refused
        logger.warning("answer cites keys that were not sent: %s (sent: %s)", unknown, list(sources))

    return ChatResponse(
        answer=f"{result.text}\n\n{TRUNCATION_NOTICE}" if result.truncated else result.text,
        documents=list(sources.values()),
        topics_used=[candidate.topic for candidate in kept],
        truncated=result.truncated,
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
