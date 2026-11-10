"""Model providers: Ollama, any OpenAI-compatible server, Anthropic, and a fake for tests."""

from sidelamp.llm.base import (
    LLM,
    CheckResult,
    Completion,
    LLMError,
    Message,
    TextDelta,
    Usage,
    complete,
    make_llm,
)

__all__ = [
    "LLM",
    "CheckResult",
    "Completion",
    "LLMError",
    "Message",
    "TextDelta",
    "Usage",
    "complete",
    "make_llm",
]
