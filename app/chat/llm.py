"""The two model clients behind POST /chat: a classifier and a generator.

Separate objects so tests stub them without a key; the SDK is imported inside `_create`, so
this module needs no `anthropic`. No sampling parameters: the pinned SDK has no `temperature`.
"""
import os
from dataclasses import dataclass, field
from typing import Protocol

# same default for both: the split is for configurability, not cost.
DEFAULT_MODEL = "claude-haiku-4-5"


def _model_from_env(name: str) -> str:
    """The one place the default lives; an empty value (compose passes '') falls back too."""
    return os.environ.get(name) or DEFAULT_MODEL


CHAT_MODEL_CLASSIFY = _model_from_env("CHAT_MODEL_CLASSIFY")
CHAT_MODEL_GENERATE = _model_from_env("CHAT_MODEL_GENERATE")

CLASSIFIER_MAX_TOKENS = 256
GENERATOR_MAX_TOKENS = 1024

# the classifier's whole tool list. The choice is forced, so declining is itself a tool:
# out_of_scope.
TOOLS = [
    {
        "name": "document_lookup",
        "description": (
            "Look up what the published crop-growing documents say about the question: "
            "temperature, propagation, pests and disease, and similar growing topics."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "crop_slug": {
                    "type": "string",
                    "description": (
                        "The crop's slug (lower case, hyphenated, e.g. 'basil' or 'sweet-pepper') "
                        "when the question names exactly one crop. Omit it when the question names "
                        "two or more crops (for example a comparison between basil and tomato) or no crop."
                    ),
                },
            },
        },
    },
    {
        "name": "out_of_scope",
        "description": (
            "The question is not about growing crops (temperature, propagation, pests and "
            "disease, and similar growing topics), so the library cannot answer it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "One short sentence on why the question is not about growing crops.",
                },
            },
            "required": ["reason"],
        },
    },
]

CLASSIFIER_SYSTEM_PROMPT = (
    "You route questions for a crop-growing library. Call document_lookup for any question "
    "about growing crops. Set crop_slug only when the question names exactly one crop; a "
    "question that compares or mentions several crops must not set it. "
    "Call out_of_scope, with a short reason, for anything else."
)

GENERATOR_SYSTEM_PROMPT = (
    "You answer crop-growing questions using ONLY the numbered sources provided. "
    "Cite every claim with its source key in square brackets, such as [S1]; cite only "
    "keys that appear in the sources. When sources disagree, report each of them with "
    "its citation and do not pick a winner.\n"
    "Each source states the Condition it applies under. If a source's Condition does not "
    "match what the question asks (for example the source covers raising seedlings from "
    "seed and the question asks about stem cuttings), say first and plainly what the "
    "sources do NOT cover, then give only what the sources actually cover, with their "
    "citations. Never answer the unasked question as if it were the asked one."
)


@dataclass
class ToolCall:
    name: str
    arguments: dict = field(default_factory=dict)


@dataclass
class ClassifierResult:
    tool_call: ToolCall | None  # None is unexpected (choice is forced): treated as malformed
    tokens: int  # provider-reported input + output


@dataclass
class GeneratorResult:
    text: str
    tokens: int
    truncated: bool = False  # provider stopped at max_tokens: text may be incomplete


class Classifier(Protocol):
    def classify(self, question: str) -> ClassifierResult: ...


class Generator(Protocol):
    def generate(self, system: str, user: str) -> GeneratorResult: ...


class LLMUnavailable(Exception):
    """The model service cannot be used: missing key or SDK, or a provider error. The SDK has
    already retried transient errors (max_retries=2); usage-limit errors will not clear on retry."""


@dataclass
class ChatLLM:
    classifier: Classifier
    generator: Generator


def _create(client, **request):
    """One messages.create call; any failure becomes LLMUnavailable (cause chained, logged by
    the router, never sent to the client)."""
    try:
        if client is None:  # built here, not at wiring time, so a missing key or SDK is a runtime error
            import anthropic

            client = anthropic.Anthropic()
        return client.messages.create(**request)
    except Exception as exc:  # ImportError, no key, APIStatusError, connection errors
        raise LLMUnavailable(type(exc).__name__) from exc


class AnthropicClassifier:
    def __init__(self, client=None) -> None:
        # `client` is for the offline transport test; None builds one per call.
        self._client = client

    def classify(self, question: str) -> ClassifierResult:
        message = _create(
            self._client,
            model=CHAT_MODEL_CLASSIFY,
            max_tokens=CLASSIFIER_MAX_TOKENS,
            system=CLASSIFIER_SYSTEM_PROMPT,
            tools=TOOLS,
            tool_choice={"type": "any"},  # forced: the model must call one of TOOLS
            messages=[{"role": "user", "content": question}],
        )
        call = next(
            (ToolCall(block.name, dict(block.input)) for block in message.content
             if block.type == "tool_use"),
            None,
        )
        return ClassifierResult(call, message.usage.input_tokens + message.usage.output_tokens)


class AnthropicGenerator:
    def __init__(self, client=None) -> None:
        self._client = client

    def generate(self, system: str, user: str) -> GeneratorResult:
        message = _create(
            self._client,
            model=CHAT_MODEL_GENERATE,
            max_tokens=GENERATOR_MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(block.text for block in message.content if block.type == "text")
        return GeneratorResult(
            text,
            message.usage.input_tokens + message.usage.output_tokens,
            truncated=message.stop_reason == "max_tokens",
        )


def get_chat_llm() -> ChatLLM:
    """FastAPI dependency; tests override it with stubs.

    Cheap and cannot fail: the SDK client is built inside each model call, after the budget check,
    so a missing key is a 503 and an over-budget member still gets the 429 first."""
    return ChatLLM(AnthropicClassifier(), AnthropicGenerator())
