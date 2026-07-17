"""Metadata-bound, network-disabled Tier-1 executor."""

from __future__ import annotations

import hashlib
import json
import socket
import time
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from pydantic import BaseModel, ConfigDict, Field

from app.evidence.packet import build_evidence_packet
from app.llm.provider import LLMResponse, ToolUseBlock, Usage
from app.observability.langfuse import InMemoryTraceSink, RequestTracer
from app.observability.trace import AccountabilityContext
from app.orchestrator.composer import compose_answer
from app.orchestrator.loop import (
    SUBMIT_CLAIMS_TOOL,
    SYSTEM_PROMPT,
    BriefResult,
    Orchestrator,
    ToolRegistry,
)
from app.schemas.answers import GroundedAnswerContext
from app.schemas.citations import CitationSourceType
from evals.execution import (
    EVAL_ANSWER_QUESTION,
    ExecutionOutput,
    PARSER_VERSION,
    RECORDED_MODEL,
    SANITIZER_VERSION,
    execute_source,
    fixture_sha256,
    observe_canonical_answer_evidence,
    observe_rendered_claims,
    prompt_hash,
    schema_hash,
)
from evals.w2_models import (
    CanonicalAnswerEvidenceObservation,
    CaseObservation,
    GeneratedSurfaces,
    GoldenCase,
    RenderedClaimObservation,
)


DEFAULT_RECORDINGS = Path(__file__).parent / "recordings" / "index.json"
ANSWER_REPLAY_VERSION = "first-document-first-guideline-v1"


class RecordingIntegrityError(RuntimeError):
    """A recording is absent, stale, mismatched, or corrupt."""


class RecordedProviderAnchor(BaseModel):
    """Sanitized provider recording: selectors and hashes, never extracted values."""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    fixture_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_schema_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    answer_prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    answer_question_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    answer_context_schema_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    answer_tool_schema_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    answer_replay_version: str = Field(min_length=1)
    model: str = Field(min_length=1)
    sanitizer_version: str = Field(min_length=1)
    parser_version: str = Field(min_length=1)
    source_document_anchor: str = Field(pattern=r"^fixture:[A-Za-z0-9._-]+$")
    page_selector: str = Field(min_length=1)
    bbox_selector: str = Field(min_length=1)
    recording_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _recording_digest(data: dict[str, object]) -> str:
    unsigned = {key: value for key, value in data.items() if key != "recording_sha256"}
    canonical = json.dumps(unsigned, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def answer_prompt_hash() -> str:
    """Bind recordings to the exact context-aware answer instruction contract."""

    return hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()


def answer_question_hash() -> str:
    """Bind recordings to the exact question shared with the live executor."""

    return hashlib.sha256(EVAL_ANSWER_QUESTION.encode("utf-8")).hexdigest()


def answer_context_schema_hash() -> str:
    """Bind recordings to the closed verified-evidence context schema."""

    canonical = json.dumps(
        GroundedAnswerContext.model_json_schema(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def answer_tool_schema_hash() -> str:
    """Bind recordings to the exact forced typed-claim tool contract."""

    canonical = json.dumps(
        SUBMIT_CLAIMS_TOOL,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_recordings(path: str | Path = DEFAULT_RECORDINGS) -> dict[str, RecordedProviderAnchor]:
    source = Path(path)
    if not source.is_file():
        raise RecordingIntegrityError("recording index is missing")
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("version") != 2:
            raise ValueError("unsupported recording index")
        entries = raw.get("recordings")
        if not isinstance(entries, list):
            raise ValueError("recordings must be a list")
        recordings: dict[str, RecordedProviderAnchor] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("invalid recording entry")
            model = RecordedProviderAnchor.model_validate(entry)
            if model.case_id in recordings:
                raise ValueError("duplicate recording case")
            if _recording_digest(entry) != model.recording_sha256:
                raise ValueError("recording digest mismatch")
            recordings[model.case_id] = model
        return recordings
    except RecordingIntegrityError:
        raise
    except Exception as exc:
        raise RecordingIntegrityError("recording index is corrupt") from exc


@contextmanager
def network_disabled() -> Iterator[None]:
    """Reject IP/DNS egress while permitting local Unix sockets used by OCR subprocesses."""

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_create_connection = socket.create_connection
    original_getaddrinfo = socket.getaddrinfo

    def blocked_connect(instance: socket.socket, address: object) -> object:
        if instance.family in {socket.AF_INET, socket.AF_INET6}:
            raise RuntimeError("Tier-1 network access is disabled")
        return original_connect(instance, address)  # type: ignore[arg-type]

    def blocked_create_connection(*_args: object, **_kwargs: object) -> socket.socket:
        raise RuntimeError("Tier-1 network access is disabled")

    def blocked_connect_ex(instance: socket.socket, address: object) -> int:
        if instance.family in {socket.AF_INET, socket.AF_INET6}:
            raise RuntimeError("Tier-1 network access is disabled")
        return original_connect_ex(instance, address)  # type: ignore[arg-type]

    def blocked_getaddrinfo(*_args: object, **_kwargs: object) -> list[tuple]:
        raise RuntimeError("Tier-1 network access is disabled")

    setattr(socket.socket, "connect", blocked_connect)
    setattr(socket.socket, "connect_ex", blocked_connect_ex)
    socket.create_connection = blocked_create_connection
    socket.getaddrinfo = blocked_getaddrinfo
    try:
        yield
    finally:
        setattr(socket.socket, "connect", original_connect)
        setattr(socket.socket, "connect_ex", original_connect_ex)
        socket.create_connection = original_create_connection
        socket.getaddrinfo = original_getaddrinfo


class RecordedExecutor:
    """Runs every case from source bytes plus its reviewed metadata binding."""

    def __init__(self, *, recordings_path: str | Path = DEFAULT_RECORDINGS) -> None:
        self._recordings = load_recordings(recordings_path)
        self.call_count = 0
        self.retrieval_hit_count = 0
        self.grounding_rates: list[float] = []
        self.latencies_ms: list[float] = []
        self._seen_content_hashes: set[str] = set()

    @property
    def recording_case_ids(self) -> frozenset[str]:
        """Exact metadata index membership for manifest-integrity validation."""

        return frozenset(self._recordings)

    def _recording_for(self, case: GoldenCase) -> RecordedProviderAnchor:
        recording = self._recordings.get(case.case_id)
        if recording is None:
            raise RecordingIntegrityError("case recording is missing")
        checks = {
            "fixture": recording.fixture_sha256 == fixture_sha256(case.fixture_path),
            "prompt": recording.prompt_hash == prompt_hash(),
            "schema": recording.tool_schema_hash == schema_hash(case.doc_type),
            "answer_prompt": recording.answer_prompt_hash == answer_prompt_hash(),
            "answer_question": (
                recording.answer_question_hash == answer_question_hash()
            ),
            "answer_context_schema": (
                recording.answer_context_schema_hash
                == answer_context_schema_hash()
            ),
            "answer_schema": (
                recording.answer_tool_schema_hash == answer_tool_schema_hash()
            ),
            "answer_replay": (
                recording.answer_replay_version == ANSWER_REPLAY_VERSION
            ),
            "model": recording.model == RECORDED_MODEL,
            "sanitizer": recording.sanitizer_version == SANITIZER_VERSION,
            "parser": recording.parser_version == PARSER_VERSION,
            "page_selector": recording.page_selector == "all-readable-pages",
            "bbox_selector": recording.bbox_selector == "label-value-lines",
        }
        if not all(checks.values()):
            failed = ",".join(name for name, passed in checks.items() if not passed)
            raise RecordingIntegrityError(f"recording binding mismatch ({failed})")
        return recording

    async def __call__(self, case: GoldenCase) -> CaseObservation:
        self.call_count += 1
        started = time.perf_counter()
        recording = self._recording_for(case)
        with network_disabled():
            result = await execute_source(
                case_id=case.case_id,
                doc_type=case.doc_type,
                source_path=case.fixture_path,
                source_document_id=recording.source_document_anchor,
            )
            canonical_answer_evidence, rendered_claims, traces = await _run_recorded_answer(
                case_id=case.case_id,
                result=result,
            )
        result = replace(result, rendered_claim_count=len(rendered_claims))
        content_hash = recording.fixture_sha256
        if content_hash in self._seen_content_hashes:
            result = replace(result, verdict="duplicate_noop", refusal=None)
        else:
            self._seen_content_hashes.add(content_hash)
        self.retrieval_hit_count += result.retrieval_hit_count
        self.grounding_rates.append(result.grounding_rate)
        self.latencies_ms.append((time.perf_counter() - started) * 1000)
        # Generated surfaces contain metadata only. Clinical fixture values, model prose,
        # prompts, and transcripts never enter the observation's artifact channels.
        metadata = {
            "recording_sha256": recording.recording_sha256,
            "retrieval_hit_count": result.retrieval_hit_count,
            "rendered_claim_count": result.rendered_claim_count,
        }
        return CaseObservation(
            case_id=case.case_id,
            fields=result.fields,
            citations=result.citations,
            canonical_answer_evidence=canonical_answer_evidence,
            rendered_claims=rendered_claims,
            verdict=result.verdict,
            refusal=result.refusal,
            safety_events=result.safety_events,
            generated=GeneratedSurfaces(recordings=[metadata], traces=traces),
        )


class _RecordedAnswerProvider:
    """Forced typed-answer response over only the context supplied by the composer."""

    model = RECORDED_MODEL

    def __init__(
        self,
        *,
        selected_document_field_id: str | None,
        selected_chunk_id: str | None,
    ) -> None:
        self._selected_document_field_id = selected_document_field_id
        self._selected_chunk_id = selected_chunk_id
        self.calls = 0

    async def complete(
        self,
        *,
        system: list[dict],
        messages: list[dict],
        tools: list[dict],
        tool_choice: dict[str, Any] | None = None,
    ) -> LLMResponse:
        self.calls += 1
        answer_tool = next(
            (item for item in tools if item.get("name") == "submit_claims"), None
        )
        if not system or not messages or answer_tool is None:
            raise ValueError("recorded answer request contract drifted")
        if self._selected_document_field_id is not None:
            context_blocks = (
                messages[0].get("content", [])
                if messages and isinstance(messages[0], dict)
                else []
            )
            context_text = "\n".join(
                str(block.get("text", ""))
                for block in context_blocks
                if isinstance(block, dict)
            )
            if (
                "document-claim-1" not in context_text
                or self._selected_document_field_id not in context_text
            ):
                raise ValueError("recorded document selector was absent from answer context")
        claims: list[dict[str, object]] = []
        if self._selected_document_field_id is not None:
            claims.append(
                {
                    "type": "document",
                    "claim_id": "document-claim-1",
                    "field_id": self._selected_document_field_id,
                    "evidence_ids": [],
                }
            )
        if self._selected_chunk_id is not None:
            claims.append(
                {
                    "type": "guideline",
                    "chunk_id": self._selected_chunk_id,
                    "evidence_ids": [],
                }
            )
        return LLMResponse(
            content=[
                ToolUseBlock(
                    id="recorded-answer-response",
                    name="submit_claims",
                    input={"claims": claims},
                )
            ],
            stop_reason="tool_use",
            usage=Usage(),
            model=self.model,
        )


async def _run_recorded_answer(
    *, case_id: str, result: ExecutionOutput
) -> tuple[
    list[CanonicalAnswerEvidenceObservation],
    list[RenderedClaimObservation],
    list[dict[str, object]],
]:
    """Run the real context-aware answer/composer path and retain sanitized trace output."""

    verified_facts = result.verified_facts
    snippets = result.evidence_snippets
    citations = result.answer_citations
    selected_document_field_id = (
        verified_facts[0].citation.field_or_chunk_id if verified_facts else None
    )
    selected = snippets[0].chunk_id if snippets else None
    provider = _RecordedAnswerProvider(
        selected_document_field_id=selected_document_field_id,
        selected_chunk_id=selected,
    )
    trace_sink = InMemoryTraceSink()
    tracer = RequestTracer(trace_sink)
    correlation = f"eval-{hashlib.sha256(case_id.encode()).hexdigest()[:16]}"
    accountability = AccountabilityContext(
        correlation_id=correlation,
        client_id="eval-tier1",
        exercised_scopes=(),
        request_url="/chat",
        user_id="synthetic-clinician",
        patient_id="synthetic-patient",
        utc_timestamp=datetime.now(timezone.utc).isoformat(),
    )
    packet = build_evidence_packet("synthetic-patient", {})
    canonical_answer_evidence: list[CanonicalAnswerEvidenceObservation] | None = None

    async def unsupported_legacy_path() -> BriefResult:
        raise AssertionError("recorded evaluation requires grounded answer context")

    async def run_with_context(context: GroundedAnswerContext) -> BriefResult:
        nonlocal canonical_answer_evidence
        canonical_answer_evidence = observe_canonical_answer_evidence(context)
        return await Orchestrator(provider).run_previsit_brief(
            packet,
            EVAL_ANSWER_QUESTION,
            tools=ToolRegistry([]),
            tracer=tracer,
            accountability=accountability,
            answer_context=context,
        )

    composed = await compose_answer(
        verified_facts=verified_facts,
        evidence_snippets=snippets,
        citations=citations,
        run_brief=unsupported_legacy_path,
        run_brief_with_context=run_with_context,
    )
    if provider.calls != 1:
        raise ValueError("recorded answer provider call count drifted")
    if canonical_answer_evidence is None:
        raise RecordingIntegrityError("recorded answer context was not observed")
    rendered_claims = observe_rendered_claims(composed.composition.claims)
    if selected_document_field_id is not None and not any(
        claim.citation is not None
        and claim.source_class is CitationSourceType.UPLOADED_DOCUMENT
        and getattr(claim.citation, "field_or_chunk_id", None)
        == selected_document_field_id
        for claim in rendered_claims
    ):
        raise RecordingIntegrityError("recorded document selector did not resolve")
    if selected is not None and not any(
        claim.citation is not None
        and claim.source_class is CitationSourceType.GUIDELINE
        and getattr(claim.citation, "field_or_chunk_id", None) == selected
        for claim in rendered_claims
    ):
        raise RecordingIntegrityError("recorded guideline selector did not resolve")
    return canonical_answer_evidence, rendered_claims, [
        asdict(trace) for trace in trace_sink.traces
    ]


def make_recorded_executor(
    *, recordings_path: str | Path = DEFAULT_RECORDINGS
) -> RecordedExecutor:
    return RecordedExecutor(recordings_path=recordings_path)
