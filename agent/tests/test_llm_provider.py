"""E5 — the llm.complete() provider seam (D4).

The provider is the ONLY place the Anthropic SDK is touched, so the model is a
config swap not a code change (D4). These tests inject a fake SDK client (no network,
no key) and prove: SDK responses are normalized into our own block/usage types
(so the loop never depends on SDK internals), request fields are forwarded, and any
SDK transport failure is wrapped into a single `LLMUnavailable` — the one exception
the orchestrator's D13 fallback keys on. We do NOT assert model output quality here.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

import anthropic

from app.llm.provider import (
    AnthropicLLMProvider,
    LLMClientError,
    LLMRequestTooLarge,
    LLMUnavailable,
    TextBlock,
    ToolUseBlock,
)


def _status_error(status: int, message="boom"):
    """A real anthropic.APIStatusError carrying the given HTTP status (version-robust —
    classification keys on status_code, not the subclass name)."""
    req = httpx.Request("POST", "http://x")
    return anthropic.APIStatusError(message, response=httpx.Response(status_code=status, request=req), body=None)


class _FakeMessages:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc
        self.called_with = None

    async def create(self, **kwargs):
        self.called_with = kwargs
        if self._exc is not None:
            raise self._exc
        return self._response


class _FakeClient:
    def __init__(self, response=None, exc=None):
        self.messages = _FakeMessages(response=response, exc=exc)


def _sdk_usage(**kw):
    base = dict(input_tokens=0, output_tokens=0,
                cache_creation_input_tokens=0, cache_read_input_tokens=0)
    base.update(kw)
    return SimpleNamespace(**base)


def _text_response():
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text="a brief")],
        stop_reason="end_turn",
        usage=_sdk_usage(input_tokens=10, output_tokens=5, cache_read_input_tokens=8),
        model="claude-sonnet-4-6",
    )


def _tool_use_response():
    return SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="let me check"),
            SimpleNamespace(type="tool_use", id="toolu_1", name="get_conditions", input={}),
        ],
        stop_reason="tool_use",
        usage=_sdk_usage(input_tokens=20, output_tokens=7),
        model="claude-sonnet-4-6",
    )


async def test_normalizes_text_response_and_usage():
    prov = AnthropicLLMProvider(api_key="k", model="claude-sonnet-4-6",
                                client=_FakeClient(_text_response()))
    resp = await prov.complete(system=[], messages=[{"role": "user", "content": "hi"}], tools=[])
    assert resp.stop_reason == "end_turn"
    assert resp.text() == "a brief"
    assert isinstance(resp.content[0], TextBlock)
    assert resp.usage.input_tokens == 10 and resp.usage.cache_read_input_tokens == 8


async def test_normalizes_tool_use_blocks():
    prov = AnthropicLLMProvider(api_key="k", model="claude-sonnet-4-6",
                                client=_FakeClient(_tool_use_response()))
    resp = await prov.complete(system=[], messages=[{"role": "user", "content": "hi"}], tools=[])
    assert resp.stop_reason == "tool_use"
    tus = resp.tool_uses()
    assert len(tus) == 1 and isinstance(tus[0], ToolUseBlock)
    assert tus[0].id == "toolu_1" and tus[0].name == "get_conditions" and tus[0].input == {}


async def test_forwards_request_fields_and_own_model_max_tokens():
    fc = _FakeClient(_text_response())
    prov = AnthropicLLMProvider(api_key="k", model="claude-sonnet-4-6", client=fc, max_tokens=1234)
    system = [{"type": "text", "text": "S", "cache_control": {"type": "ephemeral"}}]
    await prov.complete(system=system, messages=[{"role": "user", "content": "q"}],
                        tools=[{"name": "t", "description": "d", "input_schema": {}}])
    kw = fc.messages.called_with
    assert kw["model"] == "claude-sonnet-4-6"
    assert kw["max_tokens"] == 1234
    assert kw["system"] == system  # cache_control breakpoints reach the API verbatim
    assert kw["tools"] == [{"name": "t", "description": "d", "input_schema": {}}]


async def test_omits_empty_system_and_tools():
    fc = _FakeClient(_text_response())
    prov = AnthropicLLMProvider(api_key="k", model="claude-sonnet-4-6", client=fc)
    await prov.complete(system=[], messages=[{"role": "user", "content": "q"}], tools=[])
    kw = fc.messages.called_with
    assert "system" not in kw  # empty system/tools omitted, not sent as []
    assert "tools" not in kw


async def test_transport_failure_wrapped_as_llm_unavailable():
    # A connection error has no status → transient (graceful degradation).
    exc = anthropic.APIConnectionError(request=httpx.Request("POST", "http://x"))
    prov = AnthropicLLMProvider(api_key="k", model="claude-sonnet-4-6", client=_FakeClient(exc=exc))
    with pytest.raises(LLMUnavailable):
        await prov.complete(system=[], messages=[{"role": "user", "content": "hi"}], tools=[])


@pytest.mark.parametrize("status", [429, 500, 529])
async def test_transient_statuses_wrap_as_unavailable(status):
    prov = AnthropicLLMProvider(api_key="k", model="claude-sonnet-4-6",
                                client=_FakeClient(exc=_status_error(status)))
    with pytest.raises(LLMUnavailable) as ei:
        await prov.complete(system=[], messages=[{"role": "user", "content": "hi"}], tools=[])
    # sibling classes — a transient must NOT be mistaken for a persistent client error
    assert not isinstance(ei.value, LLMClientError)


@pytest.mark.parametrize("status", [400, 401, 403, 422])
async def test_client_error_statuses_wrap_as_client_error_with_status(status):
    prov = AnthropicLLMProvider(api_key="k", model="claude-sonnet-4-6",
                                client=_FakeClient(exc=_status_error(status)))
    with pytest.raises(LLMClientError) as ei:
        await prov.complete(system=[], messages=[{"role": "user", "content": "hi"}], tools=[])
    assert ei.value.status == status
    assert not isinstance(ei.value, LLMRequestTooLarge)  # only 413 is the too-large case
    assert not isinstance(ei.value, LLMUnavailable)      # distinct from transient → distinct alert


async def test_413_wraps_as_request_too_large():
    prov = AnthropicLLMProvider(api_key="k", model="claude-sonnet-4-6",
                                client=_FakeClient(exc=_status_error(413)))
    with pytest.raises(LLMRequestTooLarge) as ei:
        await prov.complete(system=[], messages=[{"role": "user", "content": "hi"}], tools=[])
    assert ei.value.status == 413
    assert isinstance(ei.value, LLMClientError)  # taxonomy: a too-large is a kind of client error
