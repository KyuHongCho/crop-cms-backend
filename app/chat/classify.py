"""The scope and capability checks: the classifier's tool call, as a typed result.

The scope check is the tool name `out_of_scope`; nothing else is declined. The capability check: a
missing tool call, an unknown tool name or badly typed arguments fall back to
`document_lookup`, never an error. There is no catch-all `else` for the decline.
"""
import re
from dataclasses import dataclass

from app.chat.llm import Classifier, ToolCall

DOCUMENT_LOOKUP = "document_lookup"
OUT_OF_SCOPE = "out_of_scope"


@dataclass
class Classification:
    intent: str
    crop_slug: str | None
    tokens: int


def classify(question: str, classifier: Classifier) -> Classification:
    result = classifier.classify(question)
    tokens = getattr(result, "tokens", 0)
    if not isinstance(tokens, int) or isinstance(tokens, bool) or tokens < 0:
        tokens = 0
    call = getattr(result, "tool_call", None)
    if not isinstance(call, ToolCall):
        return Classification(DOCUMENT_LOOKUP, None, tokens)
    if call.name == OUT_OF_SCOPE:
        return Classification(OUT_OF_SCOPE, None, tokens)
    crop_slug = call.arguments.get("crop_slug") if isinstance(call.arguments, dict) else None
    # Normalise the input, as topic_set_statement does for topics: the tool asks for a lower-case,
    # hyphenated slug but cannot force one, and the model is not given the slug list (a live run
    # emitted "sweet pepper" for "sweet-pepper"). An exact-match miss silently unscopes retrieval.
    crop_slug = re.sub(r"[\s_]+", "-", crop_slug.strip().lower()) if isinstance(crop_slug, str) else None
    return Classification(DOCUMENT_LOOKUP, crop_slug or None, tokens)
