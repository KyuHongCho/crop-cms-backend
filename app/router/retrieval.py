"""Topic-set retrieval: crop + topic in, the complete published document set
out. See app/crud/retrieval.py for why there is no `limit` parameter here and
none is ever coming -- app/model/model.py:92-94 is the constraint.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

import app.crud.retrieval as retrieval_crud
import app.schema.retrieval as retrieval_schema
from app.crud.retrieval import (
    CHARS_PER_TOKEN,
    CONTEXT_CHAR_BUDGET,
    TopicBudgetExceeded,
    TopicCandidate,
    assemble_within_budget,
    document_context_chars,
)
from app.db.db import get_db

router = APIRouter()


@router.get(
    "/retrieval/{crop_slug}/{topic}",
    response_model=retrieval_schema.TopicSetResponse,
)
async def get_topic_set(
    crop_slug: str,
    topic: str,
    db: AsyncSession = Depends(get_db),
):
    """The complete published document set for one crop and one topic.

    No `limit`. Every published document sharing `topic` for this crop comes
    back, or the request is refused (413) naming the topic and its document
    count, per Rule 3 clause 2 -- never a silent slice.
    """
    crop_id = await retrieval_crud.get_crop_id_by_slug(db, crop_slug)
    if crop_id is None:
        raise HTTPException(status_code=404, detail=f"crop {crop_slug!r} not found")

    documents = await retrieval_crud.get_topic_set(db, crop_id, topic)

    # A single candidate today -- there is no topic *selection* yet
    # (Rules 1/2). Routed through the same Rule 3 policy multi-topic selection
    # will use, so the refusal path is real, tested code, not a stub.
    candidate = TopicCandidate(topic=topic, score=0.0, documents=documents)
    try:
        _kept, budget_dropped = assemble_within_budget([candidate])
    except TopicBudgetExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=retrieval_schema.BudgetRefusal(
                topic=exc.topic,
                document_count=exc.document_count,
                context_chars=exc.context_chars,
                context_char_budget=exc.budget,
                message=str(exc),
            ).model_dump(),
        ) from exc

    return retrieval_schema.TopicSetResponse(
        crop_slug=crop_slug,
        topic=topic,
        document_count=len(documents),
        documents=documents,
        context_chars=document_context_chars(documents),
        context_char_budget=CONTEXT_CHAR_BUDGET,
        chars_per_token=CHARS_PER_TOKEN,
        dropped=[
            retrieval_schema.DroppedTopic(
                topic=d.topic, score=d.score,
                document_count=d.document_count, context_chars=d.context_chars,
            )
            for d in budget_dropped
        ],
    )
