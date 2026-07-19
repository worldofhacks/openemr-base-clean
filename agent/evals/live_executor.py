"""Live Anthropic Tier-2 executor with closed judge and inconclusive semantics."""

from __future__ import annotations

import json
import hashlib
import os
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol, cast

import anthropic

from app.evidence.packet import build_evidence_packet
from app.grounding.verifier import GroundingVerifier
from app.ingestion.repository import InMemoryDocumentRepository, NewDocument
from app.ingestion.pipeline import _reground
from app.ingestion.reader import WordsBoxes, read_pdf_bytes_words_and_boxes
from app.llm.cost import estimate_cost
from app.llm.provider import LLMResponse, Usage, _normalize_content, _normalize_usage
from app.llm.vlm import (
    AnthropicVlmExtractor,
    VlmResponseRejected,
    VlmTimeout,
    VlmUnavailable,
)
from app.orchestrator.composer import compose_answer
from app.orchestrator.loop import BriefResult, Orchestrator, ToolRegistry
from app.schemas.answers import GroundedAnswerContext
from app.schemas.extraction import IntakeFormExtraction, LabPdfExtraction
from evals.execution import (
    EVAL_ANSWER_QUESTION,
    _HEADINGS,
    _lines,
    finalize_typed_extraction,
    fixture_path,
    observe_canonical_answer_evidence,
    observe_rendered_claims,
    SideEffectCapture,
)
from evals.harness import EvalInconclusiveError
from evals.w2_models import CaseObservation, GeneratedSurfaces, GoldenCase


DEFAULT_JUDGE_CONFIG = Path(__file__).parent / "judge_config.yaml"


class LiveParseError(RuntimeError):
    """A retryable provider/schema error with optional aggregate call accounting."""

    def __init__(self, message: str, *, call: LiveCall | None = None) -> None:
        super().__init__(message)
        self.call = call


@dataclass(frozen=True)
class LiveCall:
    value: object
    usage: Usage
    model: str
    latency_ms: float


class LiveProvider(Protocol):
    async def extract(
        self,
        *,
        doc_type: str,
        source: bytes,
        words_boxes: WordsBoxes,
        source_document_id: str,
    ) -> LiveCall: ...

    async def answer(self, *, context: GroundedAnswerContext) -> LiveCall: ...

    async def judge(self, *, context: GroundedAnswerContext, answer: str) -> LiveCall: ...


def load_judge_config(path: str | Path = DEFAULT_JUDGE_CONFIG) -> dict[str, object]:
    try:
        config = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError("judge config is invalid") from exc
    required = {
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
        "temperature": 0,
        "max_retries": 1,
    }
    if not isinstance(config, dict) or any(config.get(key) != value for key, value in required.items()):
        raise ValueError("judge config drifted from the pinned contract")
    schema = config.get("result_schema")
    if not isinstance(schema, dict) or schema.get("additionalProperties") is not False:
        raise ValueError("judge result schema must be closed")
    return config


def _verified_context_payload(context: GroundedAnswerContext) -> dict[str, object]:
    """Serialize only canonical verified claims and the bounded top-five snippets."""

    return {
        "chart_claims": [
            {
                "evidence_id": claim.citation.field_or_chunk_id,
                "text": claim.text,
            }
            for claim in context.chart_claims
        ],
        "document_claims": [
            {
                "field_id": claim.citation.field_or_chunk_id,
                "text": claim.text,
            }
            for claim in context.document_claims
        ],
        "guideline_snippets": [
            {"chunk_id": snippet.chunk_id, "quote": snippet.quote}
            for snippet in context.guideline_snippets
        ],
    }


class AnthropicLiveProvider:
    """Temperature-zero provider with SDK retries disabled; the eval owns retry policy."""

    def __init__(self, api_key: str, *, config: dict[str, object]) -> None:
        if not api_key:
            raise EvalInconclusiveError("live provider key is unavailable")
        self.model = str(config["model"])
        self._config = config
        self._client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=0, timeout=45.0)
        self._completion_serial = 0
        self._last_completion_usage = Usage()
        self._last_completion_model = self.model
        self._completion_accounting: list[tuple[Usage, str]] = []

    async def complete(
        self,
        *,
        system: list[dict],
        messages: list[dict],
        tools: list[dict],
        tool_choice: dict[str, Any] | None = None,
    ) -> LLMResponse:
        kwargs: dict[str, object] = {
            "model": self.model,
            "max_tokens": 4096,
            "temperature": 0,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        try:
            raw = await self._client.messages.create(**kwargs)
        except (anthropic.APIError, TimeoutError) as exc:
            raise EvalInconclusiveError("live provider infrastructure exhausted") from exc
        response = LLMResponse(
            content=_normalize_content(getattr(raw, "content", [])),
            stop_reason=getattr(raw, "stop_reason", None),
            usage=_normalize_usage(getattr(raw, "usage", None)),
            model=getattr(raw, "model", self.model),
        )
        # Retain aggregate accounting metadata only. Raw content, prompts, and
        # transcripts are never stored on the provider.
        self._completion_serial += 1
        self._last_completion_usage = response.usage
        self._last_completion_model = response.model
        self._completion_accounting.append((response.usage, response.model))
        return response

    async def extract(
        self,
        *,
        doc_type: str,
        source: bytes,
        words_boxes: WordsBoxes,
        source_document_id: str,
    ) -> LiveCall:
        started = time.perf_counter()
        extractor = AnthropicVlmExtractor(self)
        prior_serial = self._completion_serial
        try:
            value = await extractor.extract(
                doc_type=doc_type,
                source=source,
                words_boxes=words_boxes,
                source_document_id=source_document_id,
            )
        except EvalInconclusiveError:
            raise
        except (VlmResponseRejected, VlmTimeout, VlmUnavailable) as exc:
            call = (
                LiveCall(
                    None,
                    self._last_completion_usage,
                    self._last_completion_model,
                    (time.perf_counter() - started) * 1000,
                )
                if self._completion_serial == prior_serial + 1
                else None
            )
            raise LiveParseError(
                "live extraction response was rejected", call=call
            ) from exc
        if self._completion_serial != prior_serial + 1:
            raise LiveParseError("extractor violated the single-call accounting contract")
        return LiveCall(
            value,
            self._last_completion_usage,
            self._last_completion_model,
            (time.perf_counter() - started) * 1000,
        )

    async def answer(self, *, context: GroundedAnswerContext) -> LiveCall:
        started = time.perf_counter()
        accounting_start = len(self._completion_accounting)
        packet = build_evidence_packet("session-pinned-patient", {})
        brief = await Orchestrator(self).run_previsit_brief(
            packet,
            EVAL_ANSWER_QUESTION,
            tools=ToolRegistry([]),
            answer_context=context,
            emit_summary=False,
        )
        accounting = self._completion_accounting[accounting_start:]
        usage = Usage()
        for call_usage, _model in accounting:
            usage = usage.add(call_usage)
        aggregate = (
            LiveCall(
                brief,
                usage,
                accounting[-1][1],
                (time.perf_counter() - started) * 1000,
            )
            if accounting
            else None
        )
        if aggregate is None or "submit_claims" not in brief.tool_calls:
            raise LiveParseError(
                "answer violated the forced typed-claim contract", call=aggregate
            )
        return LiveCall(
            brief,
            usage,
            accounting[-1][1],
            (time.perf_counter() - started) * 1000,
        )

    async def judge(self, *, context: GroundedAnswerContext, answer: str) -> LiveCall:
        started = time.perf_counter()
        schema = self._config["result_schema"]
        response = await self.complete(
            system=[{"type": "text", "text": str(self._config["system_prompt"])}],
            messages=[{
                "role": "user",
                "content": (
                    "<verified_untrusted_data>"
                    + json.dumps(
                        _verified_context_payload(context),
                        separators=(",", ":"),
                        default=str,
                    )
                    + "</verified_untrusted_data><candidate_untrusted_data>"
                    + answer
                    + "</candidate_untrusted_data>"
                ),
            }],
            tools=[{
                "name": "submit_judgement",
                "description": "Return the closed Boolean factual-consistency result.",
                "input_schema": schema,
            }],
            tool_choice={
                "type": "tool",
                "name": "submit_judgement",
                "disable_parallel_tool_use": True,
            },
        )
        blocks = response.tool_uses()
        if response.stop_reason != "tool_use" or len(blocks) != 1:
            raise LiveParseError(
                "judge response violated the forced-tool contract",
                call=LiveCall(
                    None,
                    response.usage,
                    response.model,
                    (time.perf_counter() - started) * 1000,
                ),
            )
        value = blocks[0].input
        if set(value) != {"factually_consistent"} or not isinstance(value["factually_consistent"], bool):
            raise LiveParseError(
                "judge response violated the Boolean schema",
                call=LiveCall(
                    None,
                    response.usage,
                    response.model,
                    (time.perf_counter() - started) * 1000,
                ),
            )
        return LiveCall(
            value["factually_consistent"],
            response.usage,
            response.model,
            (time.perf_counter() - started) * 1000,
        )


class LiveExecutor:
    def __init__(self, provider: LiveProvider, *, config: dict[str, object]) -> None:
        self._provider = provider
        self._config = config
        self.call_count = 0
        self.retries = 0
        self.usage = Usage()
        self.cost_usd = 0.0
        self.latencies_ms: list[float] = []
        self.retrieval_hit_count = 0
        self.grounding_rates: list[float] = []
        self._documents = InMemoryDocumentRepository()
        self._source_id_by_hash: dict[str, str] = {}

    def _record(self, call: LiveCall) -> None:
        self.usage = self.usage.add(call.usage)
        self.cost_usd += estimate_cost(call.usage, call.model)
        self.latencies_ms.append(call.latency_ms)

    def _record_failed_call(
        self, exc: EvalInconclusiveError | LiveParseError
    ) -> None:
        if isinstance(exc, LiveParseError) and exc.call is not None:
            self._record(exc.call)

    def _record_retry(self, exc: EvalInconclusiveError | LiveParseError) -> None:
        self._record_failed_call(exc)
        self.retries += 1

    async def __call__(self, case: GoldenCase) -> CaseObservation:
        self.call_count += 1
        source = fixture_path(case.fixture_path).read_bytes()
        content_hash = hashlib.sha256(source).hexdigest()
        _record, created = await self._documents.get_or_create(
            NewDocument(
                patient_id="session-pinned-patient",
                content_hash=content_hash,
                doc_type=case.doc_type,
                filename="recorded-source.pdf",
                content_type="application/pdf",
                encounter_id="eval-encounter" if case.doc_type == "intake_form" else None,
                correlation_id=(
                    f"eval-{hashlib.sha256(case.case_id.encode()).hexdigest()[:16]}"
                ),
                credential_ref="eval-memory-credential",
            )
        )
        words_boxes = read_pdf_bytes_words_and_boxes(source)
        lines = _lines(words_boxes)
        source_id = self._source_id_by_hash.setdefault(
            content_hash, f"fixture:{case.case_id}"
        )
        extracted: LiveCall | None = None
        for attempt in range(2):
            try:
                extracted = await self._provider.extract(
                    doc_type=case.doc_type,
                    source=source,
                    words_boxes=words_boxes,
                    source_document_id=source_id,
                )
                break
            except (EvalInconclusiveError, LiveParseError) as exc:
                if attempt == 1:
                    self._record_failed_call(exc)
                    raise EvalInconclusiveError(
                        "live extraction infrastructure/parse exhausted"
                    ) from exc
                self._record_retry(exc)
        assert extracted is not None
        self._record(extracted)

        schema = LabPdfExtraction if case.doc_type == "lab_pdf" else IntakeFormExtraction
        proposed = schema.model_validate(extracted.value, strict=True)
        grounded, _ = _reground(
            proposed,
            words_boxes=words_boxes,
            document_id=source_id,
            verifier=GroundingVerifier(),
        )
        sections = {
            " ".join(line.text.casefold().split())
            for line in lines
            if " ".join(line.text.casefold().split()) in _HEADINGS
        }
        result = finalize_typed_extraction(
            case_id=case.case_id,
            doc_type=case.doc_type,
            extraction=grounded,
            sections_seen=sections,
            source_lines=lines,
            side_effects=SideEffectCapture(),
        )
        self.retrieval_hit_count += result.retrieval_hit_count
        self.grounding_rates.append(result.grounding_rate)
        if not created:
            result = replace(result, verdict="duplicate_noop", refusal=None)
        answer_call: LiveCall | None = None
        answer_context: GroundedAnswerContext | None = None

        async def unsupported_legacy_answer() -> BriefResult:
            raise AssertionError("live evaluation requires grounded answer context")

        async def run_typed_answer(context: GroundedAnswerContext) -> BriefResult:
            nonlocal answer_call, answer_context
            answer_context = context
            for attempt in range(2):
                try:
                    answer_call = await self._provider.answer(context=context)
                    self._record(answer_call)
                    if not isinstance(answer_call.value, BriefResult):
                        raise LiveParseError("answer result was not typed")
                    return cast(BriefResult, answer_call.value)
                except (EvalInconclusiveError, LiveParseError) as exc:
                    if attempt == 1:
                        self._record_failed_call(exc)
                        raise EvalInconclusiveError(
                            "live answer infrastructure/parse exhausted"
                        ) from exc
                    self._record_retry(exc)
            raise AssertionError("bounded answer retry loop did not return")

        composed = await compose_answer(
            verified_facts=result.verified_facts,
            evidence_snippets=result.evidence_snippets,
            citations=result.answer_citations,
            run_brief=unsupported_legacy_answer,
            run_brief_with_context=run_typed_answer,
        )
        if answer_context is None or answer_call is None:
            raise RuntimeError("answer context was not supplied")

        served_answer = "\n".join(
            claim.text for claim in composed.composition.claims
        ).strip()
        if not served_answer:
            served_answer = composed.brief.text
        canonical_answer_evidence = observe_canonical_answer_evidence(answer_context)
        rendered_claims = observe_rendered_claims(composed.composition.claims)

        judgement: LiveCall | None = None
        for attempt in range(2):
            try:
                judgement = await self._provider.judge(
                    context=answer_context, answer=served_answer
                )
                self._record(judgement)
                break
            except (EvalInconclusiveError, LiveParseError) as exc:
                if attempt == 1:
                    self._record_failed_call(exc)
                    raise EvalInconclusiveError("live judge infrastructure/parse exhausted") from exc
                self._record_retry(exc)
        assert judgement is not None
        # A valid False is a final case result. It does not enter the retry branch above.
        return CaseObservation(
            case_id=case.case_id,
            fields=result.fields,
            citations=result.citations,
            canonical_answer_evidence=canonical_answer_evidence,
            rendered_claims=rendered_claims,
            verdict=result.verdict,
            refusal=result.refusal,
            factual_judgement=bool(judgement.value),
            safety_events=result.safety_events,
            retrieval=result.retrieval_observation,
            generated=GeneratedSurfaces(
                traces=[
                    {
                        "ordered_steps": ["extract", "answer", "judge"],
                        "step_latencies_ms": self.latencies_ms[-3:],
                        "input_tokens": self.usage.input_tokens,
                        "output_tokens": self.usage.output_tokens,
                        "cost_usd": round(self.cost_usd, 8),
                    }
                ]
            ),
        )


def make_live_executor(
    *,
    provider: LiveProvider | None = None,
    judge_config_path: str | Path = DEFAULT_JUDGE_CONFIG,
) -> LiveExecutor:
    config = load_judge_config(judge_config_path)
    selected = provider or AnthropicLiveProvider(
        os.environ.get("ANTHROPIC_API_KEY", ""), config=config
    )
    return LiveExecutor(selected, config=config)
