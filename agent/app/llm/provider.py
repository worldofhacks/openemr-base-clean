"""The `llm.complete()` provider seam (ARCHITECTURE.md §2 Zone C, D4).

This is the ONLY module that imports the Anthropic SDK. The orchestrator depends on
the normalized `LLMResponse`/`Usage`/block types below — never on SDK internals — so
swapping models (Sonnet 4.6 ↔ Haiku 4.5, D4) is a config change, not a code change.

Failure contract: the Anthropic SDK already retries 429/5xx with backoff (its
`max_retries`). When it still fails — transport error, timeout, retries exhausted —
we wrap it into a single `LLMUnavailable`. That is the one exception the orchestrator's
D13 deterministic fallback keys on, so "the physician always gets something grounded"
(§6) has exactly one trigger to catch, not a scattered SDK exception surface.

Prompt caching (R1) is not done here: the caller passes `system`/`messages` content
blocks that already carry `cache_control` breakpoints (assembled in the orchestrator),
and this seam forwards them verbatim so the 90%-off cache read is available across turns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import anthropic


class LLMUnavailable(RuntimeError):
    """The model could not be reached / failed after the SDK's own retries. Signals the
    orchestrator to fall back to the deterministic D13 render (never a raw error to the user)."""


# --- normalized response types (SDK-agnostic; the loop depends only on these) ---

@dataclass(frozen=True)
class TextBlock:
    text: str


@dataclass(frozen=True)
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]


ContentBlock = TextBlock | ToolUseBlock


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    def add(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_creation_input_tokens=self.cache_creation_input_tokens + other.cache_creation_input_tokens,
            cache_read_input_tokens=self.cache_read_input_tokens + other.cache_read_input_tokens,
        )


@dataclass(frozen=True)
class LLMResponse:
    content: list[ContentBlock] = field(default_factory=list)
    stop_reason: str | None = None
    usage: Usage = field(default_factory=Usage)
    model: str = ""

    def text(self) -> str:
        return "".join(b.text for b in self.content if isinstance(b, TextBlock))

    def tool_uses(self) -> list[ToolUseBlock]:
        return [b for b in self.content if isinstance(b, ToolUseBlock)]


@runtime_checkable
class LLMProvider(Protocol):
    """The seam the orchestrator programs against. `model` is exposed for cost accounting."""

    model: str

    async def complete(self, *, system: list[dict], messages: list[dict],
                       tools: list[dict]) -> LLMResponse: ...


def _normalize_usage(raw: Any) -> Usage:
    return Usage(
        input_tokens=getattr(raw, "input_tokens", 0) or 0,
        output_tokens=getattr(raw, "output_tokens", 0) or 0,
        cache_creation_input_tokens=getattr(raw, "cache_creation_input_tokens", 0) or 0,
        cache_read_input_tokens=getattr(raw, "cache_read_input_tokens", 0) or 0,
    )


def _normalize_content(raw_blocks: Any) -> list[ContentBlock]:
    blocks: list[ContentBlock] = []
    for b in raw_blocks or []:
        btype = getattr(b, "type", None)
        if btype == "text":
            blocks.append(TextBlock(text=getattr(b, "text", "")))
        elif btype == "tool_use":
            raw_input = getattr(b, "input", {})
            blocks.append(ToolUseBlock(
                id=getattr(b, "id", ""),
                name=getattr(b, "name", ""),
                input=dict(raw_input) if isinstance(raw_input, dict) else {},
            ))
        # thinking / other block types are not consumed by the pre-visit loop; ignore.
    return blocks


class AnthropicLLMProvider:
    """Concrete `LLMProvider` over `anthropic.AsyncAnthropic` (D4)."""

    def __init__(self, *, api_key: str, model: str, client: Any | None = None,
                 max_tokens: int = 2048, timeout: float = 30.0, max_retries: int = 2):
        self.model = model
        self.max_tokens = max_tokens
        # Injectable client for tests (no network / no key needed); real client otherwise.
        self._client = client or anthropic.AsyncAnthropic(
            api_key=api_key, timeout=timeout, max_retries=max_retries)

    async def complete(self, *, system: list[dict], messages: list[dict],
                       tools: list[dict]) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system  # cache_control breakpoints forwarded verbatim (R1)
        if tools:
            kwargs["tools"] = tools
        try:
            raw = await self._client.messages.create(**kwargs)
        except (anthropic.APIError, TimeoutError) as exc:
            # SDK already backed off on 429/5xx (max_retries); a failure here is terminal
            # for this turn → single D13 trigger. Original chained for the trace/logs.
            raise LLMUnavailable(f"{type(exc).__name__}: {exc}") from exc
        return LLMResponse(
            content=_normalize_content(getattr(raw, "content", [])),
            stop_reason=getattr(raw, "stop_reason", None),
            usage=_normalize_usage(getattr(raw, "usage", None)),
            model=getattr(raw, "model", self.model),
        )
