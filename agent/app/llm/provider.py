"""The `llm.complete()` provider seam (ARCHITECTURE.md §2 Zone C, D4).

This is the ONLY module that imports the Anthropic SDK. The orchestrator depends on
the normalized `LLMResponse`/`Usage`/block types below — never on SDK internals — so
swapping models (Sonnet 4.6 ↔ Haiku 4.5, D4) is a config change, not a code change.

Failure contract (classified, so E7 can alert on the two kinds separately):
  * `LLMUnavailable` — TRANSIENT: 429 / 5xx (500/529) / timeout / connection error, after
    the SDK's own retry-with-backoff (`max_retries`). This is genuine graceful degradation
    → the orchestrator renders the D13 fallback.
  * `LLMClientError` — PERSISTENT client error: a 4xx (400/401/403/422 …) that will recur on
    retry because it signals a bug, misconfig, or bad request. Carries the HTTP status. The
    orchestrator still returns a grounded answer, but flags it distinctly so E7 alerts on it
    as a defect, not as normal degradation.
  * `LLMRequestTooLarge` — 413 specifically (a `LLMClientError`): the assembled prompt is too
    big. The orchestrator routes this to the trim policy (shrink the evidence packet and
    retry) rather than a blanket fallback.
All three derive from `LLMError`, so "the physician always gets something grounded" (§6) still
has a single base to catch, while the subclasses drive distinct handling and alerting.

Prompt caching (R1) is not done here: the caller passes `system`/`messages` content
blocks that already carry `cache_control` breakpoints (assembled in the orchestrator),
and this seam forwards them verbatim so the 90%-off cache read is available across turns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import anthropic


class LLMError(RuntimeError):
    """Base for every failure surfaced by the provider seam."""


class LLMUnavailable(LLMError):
    """TRANSIENT failure (429 / 5xx / timeout / connection) after the SDK's own retries.
    Genuine graceful degradation → the orchestrator renders the deterministic D13 fallback."""


class LLMClientError(LLMError):
    """PERSISTENT client-side error (a 4xx that recurs on retry): a bug, misconfig, or bad
    request. Carries the HTTP status so E7 alerts on it as a defect, not normal degradation."""

    def __init__(self, message: str, *, status: int):
        super().__init__(message)
        self.status = status


class LLMRequestTooLarge(LLMClientError):
    """413 — the assembled prompt is too large. Routed to the trim policy (shrink the
    evidence packet and retry), not a blanket fallback."""


def classify_llm_error(exc: BaseException) -> LLMError:
    """Map a raised SDK/transport error to the seam's taxonomy by HTTP status. Status-driven
    (not subclass-name-driven) so it is robust across anthropic SDK versions."""
    status = getattr(exc, "status_code", None)
    label = f"{type(exc).__name__}: {exc}"
    if status == 413:
        return LLMRequestTooLarge(label, status=413)
    if status is not None and 400 <= status < 500 and status != 429:
        return LLMClientError(label, status=status)  # 400/401/403/422/… — recurs → defect
    # 429, 5xx, or no status (connection/timeout) → transient, keep serving via D13.
    return LLMUnavailable(label)


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
            # SDK already backed off on 429/5xx (max_retries). Classify what remains so the
            # orchestrator can degrade (transient), flag a defect (client error), or trim (413).
            raise classify_llm_error(exc) from exc
        return LLMResponse(
            content=_normalize_content(getattr(raw, "content", [])),
            stop_reason=getattr(raw, "stop_reason", None),
            usage=_normalize_usage(getattr(raw, "usage", None)),
            model=getattr(raw, "model", self.model),
        )
