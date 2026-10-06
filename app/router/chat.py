"""POST /chat: a question in, a cited answer out. Members only."""
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

import app.model.model as model
import app.schema.chat as chat_schema
import app.schema.retrieval as retrieval_schema
from app.auth.budget import check_budget
from app.auth.dependency import get_current_member
from app.chat import dispatch
from app.chat.embeddings import Embedder, get_embedder
from app.chat.llm import ChatLLM, LLMUnavailable, get_chat_llm
from app.crud.retrieval import TopicBudgetExceeded
from app.db.db import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


class LazyEmbedder:
    """Builds the real embedder on first use, so constructing this cannot fail."""

    def __init__(self) -> None:
        self._embedder: Embedder | None = None

    @property
    def model(self) -> str:
        return self._get().model

    def _get(self) -> Embedder:
        if self._embedder is None:
            self._embedder = get_embedder()
        return self._embedder

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._get().embed(texts)


def get_chat_embedder() -> Embedder:
    """FastAPI dependency; tests override it with the fake embedder.

    Cheap and cannot fail, like get_chat_llm: the OpenAI client is built on the first
    embed(), after the budget check, so a missing OPENAI_API_KEY is LLMUnavailable (503)
    (dispatch wraps every embed failure), and a bad body (422) or an over-budget member
    (429) never reaches it."""
    return LazyEmbedder()


@router.post("/chat", response_model=chat_schema.ChatResponse)
async def post_chat(
    body: chat_schema.ChatRequest,
    current_member: model.Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
    llm: ChatLLM = Depends(get_chat_llm),
    embedder: Embedder = Depends(get_chat_embedder),
):
    await check_budget(db, current_member.id)  # 429 before any model call
    try:
        return await dispatch.answer(db, current_member.id, body.question, llm, embedder)
    except TopicBudgetExceeded as exc:
        # Same refusal, same shape, as GET /retrieval: a topic alone is too big.
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
    except LLMUnavailable as exc:
        logger.warning("chat model unavailable: %s", exc.__cause__, exc_info=exc)
        # Missing key, usage limit, rate limit, outage: retrying does not help, and the
        # cause is logged here and never sent to the client.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The answering service is unavailable. Please try again later.",
        ) from exc
