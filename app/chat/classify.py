"""Gate 1 (scope) and gate 2 (capability): the classifier's tool call, as a typed result.

Gate 1 is the tool name `out_of_scope`; nothing else is declined. Gate 2: a
missing tool call, an unknown tool name or badly typed arguments fall back to
`document_lookup`, never an error. There is no catch-all `else` for the decline.
"""
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
    if not isinstance(crop_slug, str):
        crop_slug = None
    return Classification(DOCUMENT_LOOKUP, crop_slug or None, tokens)
