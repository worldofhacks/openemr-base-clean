#!/usr/bin/env python3
"""Verify the deployed W2 write path using committed synthetic documents only.

The owner first completes a real SMART launch, then passes only the resulting opaque
session/patient/encounter context through environment variables. This command does not
mint, accept, or print a bearer token. It exercises the deployed public API and requires:

* every readiness dependency green and ``document_runtime`` actively ready;
* idempotent lab + intake uploads and completed background jobs;
* fresh, byte-exact source and grounded-artifact FHIR Binary attestations; and
* rendered, grounded uploaded-document citations for both documents.

W2-D1/D3/D6/D9/D10; W2_ARCHITECTURE §3/§5.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

import httpx

REQUIRED_ENV_NAMES = (
    "W2_VERIFY_AGENT_BASE_URL",
    "W2_VERIFY_SESSION_ID",
    "W2_VERIFY_PATIENT_ID",
    "W2_VERIFY_ENCOUNTER_ID",
    "W2_VERIFY_SYNTHETIC_ONLY_ACK",
)
_SYNTHETIC_ONLY_ACK = "synthetic-patient-and-documents"

_EXPECTED_READY_CHECKS = frozenset(
    {
        "openemr_fhir",
        "anthropic",
        "session_store",
        "langfuse",
        "retrieval_index",
        "document_runtime",
    }
)
_QUERY = "type 2 diabetes; HbA1c; blood pressure"
_TERMINAL_FAILURE_STATES = frozenset({"failed", "reconciling"})
_SAFE_RETRY_REASONS = frozenset({"worker_restart", "writeback_failed"})
_READY_TRANSPORT_ATTEMPTS = 3


class VerificationError(RuntimeError):
    """A content-free verification failure suitable for terminal output."""


@dataclass(frozen=True)
class VerificationConfig:
    agent_base_url: str
    session_id: str
    patient_id: str
    encounter_id: str
    lab_fixture: Path
    intake_fixture: Path
    request_timeout_seconds: float = 30.0
    poll_timeout_seconds: float = 300.0
    poll_interval_seconds: float = 2.0

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "VerificationConfig":
        values = os.environ if environ is None else environ
        missing = [name for name in REQUIRED_ENV_NAMES if not values.get(name, "").strip()]
        if missing:
            raise VerificationError(
                "missing required environment variables: " + ", ".join(missing)
            )
        if values["W2_VERIFY_SYNTHETIC_ONLY_ACK"].strip() != _SYNTHETIC_ONLY_ACK:
            raise VerificationError(
                "W2_VERIFY_SYNTHETIC_ONLY_ACK must attest synthetic-patient-and-documents"
            )
        base_url = values["W2_VERIFY_AGENT_BASE_URL"].strip().rstrip("/")
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise VerificationError(
                "W2_VERIFY_AGENT_BASE_URL must be an HTTPS origin without credentials"
            )
        agent_dir = Path(__file__).resolve().parents[1]
        fixtures = agent_dir / "evals" / "fixtures" / "golden"
        config = cls(
            agent_base_url=base_url,
            session_id=values["W2_VERIFY_SESSION_ID"].strip(),
            patient_id=values["W2_VERIFY_PATIENT_ID"].strip(),
            encounter_id=values["W2_VERIFY_ENCOUNTER_ID"].strip(),
            lab_fixture=fixtures / "lab-clean-glucose.pdf",
            intake_fixture=fixtures / "intake-bp-separate-candidates.pdf",
        )
        if not config.lab_fixture.is_file() or not config.intake_fixture.is_file():
            raise VerificationError("committed synthetic verification fixtures are missing")
        return config


@dataclass(frozen=True)
class VerificationResult:
    documents_verified: int
    source_binaries_verified: int
    artifact_binaries_verified: int
    uploaded_document_citations: int


@dataclass(frozen=True)
class _UploadedDocument:
    label: str
    document_id: str
    content_hash: str


class _FreshRequestClient:
    """Fully read each response on a fresh transport connection before closing it."""

    def __init__(
        self,
        factory: Callable[..., httpx.Client],
        options: Mapping[str, object],
    ) -> None:
        self._factory = factory
        self._options = dict(options)

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        with self._factory(**self._options) as client:
            return client.request(method, url, **kwargs)


class _TransportError(RuntimeError):
    """Content-free local transport failure."""


@dataclass(frozen=True)
class _CurlResponse:
    status_code: int
    body: str

    def json(self) -> object:
        return json.loads(self.body)


class _CurlClient:
    """System-curl transport with all context supplied through private stdin."""

    def __init__(
        self,
        timeout: float,
        *,
        curl_path: str | None = None,
        run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self._timeout = timeout
        self._curl_path = curl_path or shutil.which("curl") or ""
        self._run_command = run_command

    def request(self, method: str, url: str, **kwargs: Any) -> _CurlResponse:
        parsed = urlsplit(url)
        if (
            not self._curl_path
            or method not in {"GET", "POST"}
            or parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise _TransportError("curl transport contract rejected")
        params = kwargs.pop("params", None)
        json_body = kwargs.pop("json", None)
        data = kwargs.pop("data", None)
        files = kwargs.pop("files", None)
        caller_headers = dict(kwargs.pop("headers", {}))
        if kwargs or (json_body is not None and (data is not None or files is not None)):
            raise _TransportError("curl transport contract rejected")
        if params is not None:
            query = urlencode(dict(params), doseq=True)
            url += ("&" if parsed.query else "?") + query

        headers = {
            **caller_headers,
            "User-Agent": "openemr-copilot-w2-verifier/1",
            "Accept-Encoding": "identity",
        }
        config = [
            _curl_config("url", url),
            _curl_config("request", method),
        ]
        for name, value in headers.items():
            config.append(_curl_config("header", f"{name}: {value}"))

        command = [
            self._curl_path,
            "--silent",
            "--show-error",
            "--proto",
            "=https",
            "--max-time",
            str(self._timeout),
            "--config",
            "-",
            "--write-out",
            "\n%{http_code}",
        ]
        try:
            with tempfile.TemporaryDirectory(prefix="w2-verify-upload-") as directory:
                if files is not None:
                    if not isinstance(data, Mapping) or not isinstance(files, Mapping):
                        raise ValueError("invalid multipart contract")
                    for name, value in data.items():
                        config.append(_curl_config("form-string", f"{name}={value}"))
                    for index, (name, file_value) in enumerate(files.items()):
                        if (
                            not isinstance(file_value, tuple)
                            or len(file_value) != 3
                            or not isinstance(file_value[0], str)
                            or not isinstance(file_value[1], bytes)
                            or not isinstance(file_value[2], str)
                        ):
                            raise ValueError("invalid multipart file contract")
                        filename, content, content_type = file_value
                        upload = Path(directory) / f"upload-{index}"
                        upload.write_bytes(content)
                        upload.chmod(0o600)
                        config.append(
                            _curl_config(
                                "form",
                                f"{name}=@{upload};type={content_type};"
                                f"filename={filename}",
                            )
                        )
                elif json_body is not None:
                    if not any(name.casefold() == "content-type" for name in headers):
                        config.append(_curl_config("header", "Content-Type: application/json"))
                    config.append(
                        _curl_config(
                            "data",
                            json.dumps(json_body, separators=(",", ":")),
                        )
                    )
                elif data is not None:
                    if not isinstance(data, Mapping):
                        raise ValueError("invalid form contract")
                    config.append(
                        _curl_config("data", urlencode(dict(data), doseq=True))
                    )
                completed = self._run_command(
                    command,
                    input="\n".join(config) + "\n",
                    capture_output=True,
                    text=True,
                    timeout=self._timeout + 5,
                    check=False,
                    env={},
                )
                body, status = completed.stdout.rsplit("\n", 1)
                status_code = int(status)
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            raise _TransportError("curl transport failed") from exc
        if completed.returncode != 0 or not 100 <= status_code <= 599:
            raise _TransportError("curl transport failed")
        return _CurlResponse(status_code, body)


def _curl_config(name: str, value: object) -> str:
    text = str(value)
    if any(ord(character) < 32 or ord(character) == 127 for character in text):
        raise ValueError("curl config contains a control character")
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'{name} = "{escaped}"'


class LiveWritePathVerifier:
    """Run the live contract without ever handling raw SMART credentials."""

    def __init__(
        self,
        config: VerificationConfig,
        *,
        client: httpx.Client | _FreshRequestClient | _CurlClient,
        ready_client: object | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._client = client
        self._ready_client = ready_client or client
        self._sleep = sleep
        self._monotonic = monotonic

    def run(self) -> VerificationResult:
        self._require_ready()
        documents = (
            # Intake goes first: its delegated FHIR encounter-ownership check executes
            # before repository creation/write. A wrong encounter therefore fails before
            # either synthetic document can be written.
            self._upload(
                label="intake",
                fixture=self._config.intake_fixture,
                doc_type="intake_form",
                encounter_id=self._config.encounter_id,
            ),
            self._upload(
                label="lab",
                fixture=self._config.lab_fixture,
                doc_type="lab_pdf",
                encounter_id=None,
            ),
        )
        for document in documents:
            self._poll_complete(document)
            self._verify_binary_readback(document)
        citation_count = self._verify_answer(documents)
        self._require_ready()
        return VerificationResult(
            documents_verified=len(documents),
            source_binaries_verified=len(documents),
            artifact_binaries_verified=len(documents),
            uploaded_document_citations=citation_count,
        )

    def _require_ready(self) -> None:
        body = self._request_json(
            "GET",
            "/ready",
            expected_status={200},
            transport_attempts=_READY_TRANSPORT_ATTEMPTS,
            request_client=self._ready_client,
        )
        if body.get("status") != "ready":
            raise VerificationError("readiness is not green across all dependencies")
        checks = body.get("checks")
        if not isinstance(checks, list):
            raise VerificationError("readiness response has an invalid checks contract")
        indexed = {
            str(item.get("name")): item
            for item in checks
            if isinstance(item, dict) and item.get("name")
        }
        if not _EXPECTED_READY_CHECKS.issubset(indexed):
            raise VerificationError("readiness response is missing required dependencies")
        if any(item.get("ok") is not True for item in indexed.values()):
            raise VerificationError("readiness contains a dependency that is not green")
        document_runtime = indexed["document_runtime"]
        if document_runtime.get("kind") != "hard" or document_runtime.get("detail") != "ready":
            # In particular, `ok=true, detail=disabled` is not an active write path.
            raise VerificationError("document runtime is not active and ready")

    def _upload(
        self,
        *,
        label: str,
        fixture: Path,
        doc_type: str,
        encounter_id: str | None,
    ) -> _UploadedDocument:
        content = fixture.read_bytes()
        content_hash = hashlib.sha256(content).hexdigest()
        form = {
            "session_id": self._config.session_id,
            "patient_id": self._config.patient_id,
            "doc_type": doc_type,
            "content_hash": content_hash,
        }
        if encounter_id is not None:
            form["encounter_id"] = encounter_id
        body = self._request_json(
            "POST",
            "/documents",
            expected_status={200, 202},
            data=form,
            files={"file": (fixture.name, content, "application/pdf")},
            headers={"X-Copilot-Request-Id": f"w2-verify-{uuid.uuid4()}"},
        )
        document_id = body.get("document_id")
        if not isinstance(document_id, str) or not document_id:
            raise VerificationError(f"{label} upload returned an invalid typed response")
        return _UploadedDocument(label, document_id, content_hash)

    def _poll_complete(self, document: _UploadedDocument) -> None:
        deadline = self._monotonic() + self._config.poll_timeout_seconds
        path = f"/documents/{quote(document.document_id, safe='')}/status"
        terminal_retry_used = False
        while True:
            body = self._request_json(
                "GET",
                path,
                expected_status={200},
                params={"session_id": self._config.session_id},
            )
            state = body.get("state")
            if state == "complete":
                grounded = body.get("fields_grounded")
                if not isinstance(grounded, int) or isinstance(grounded, bool) or grounded < 1:
                    raise VerificationError(
                        f"{document.label} document completed without grounded fields"
                    )
                return
            if (
                state == "failed"
                and body.get("reason") in _SAFE_RETRY_REASONS
                and not terminal_retry_used
            ):
                # A fresh deployment may encounter a fixture job failed by the prior
                # revision or a definitive pre-commit write rejection after its required
                # MIME policy was provisioned. Request exactly one optimistic-state-guarded
                # retry through the public typed route. The route refuses every unknown
                # write intent, and each pending leg still reconciles before posting.
                self._retry_failed_job(document)
                terminal_retry_used = True
                continue
            if state in _TERMINAL_FAILURE_STATES:
                raise VerificationError(
                    f"{document.label} document job reached a non-complete terminal state"
                )
            if not isinstance(state, str) or not state:
                raise VerificationError(
                    f"{document.label} document status response is invalid"
                )
            if self._monotonic() >= deadline:
                raise VerificationError(
                    f"{document.label} document job did not complete before timeout"
                )
            self._sleep(self._config.poll_interval_seconds)

    def _retry_failed_job(self, document: _UploadedDocument) -> None:
        path = f"/documents/{quote(document.document_id, safe='')}/retry"
        body = self._request_json(
            "POST",
            path,
            expected_status={202},
            params={"session_id": self._config.session_id},
            json={"expected_state": "failed"},
            headers={"X-Copilot-Request-Id": f"w2-verify-{uuid.uuid4()}"},
        )
        if (
            body.get("document_id") != document.document_id
            or body.get("state") != "queued"
            or not isinstance(body.get("job_id"), str)
            or not body.get("job_id")
            or not isinstance(body.get("status_url"), str)
            or not body.get("status_url")
            or not isinstance(body.get("correlation_id"), str)
            or not body.get("correlation_id")
        ):
            raise VerificationError(
                f"{document.label} retry returned an invalid typed response"
            )

    def _verify_binary_readback(self, document: _UploadedDocument) -> None:
        path = (
            f"/documents/{quote(document.document_id, safe='')}"
            "/readback-verification"
        )
        body = self._request_json(
            "GET",
            path,
            expected_status={200},
            params={"session_id": self._config.session_id},
        )
        source = body.get("source")
        artifact = body.get("artifact")
        if not _digest_verified(source, expected_hash=document.content_hash):
            raise VerificationError(
                f"{document.label} source FHIR Binary is not byte-exact"
            )
        if not _digest_verified(artifact):
            raise VerificationError(
                f"{document.label} artifact FHIR Binary is not byte-exact"
            )

    def _verify_answer(self, documents: tuple[_UploadedDocument, ...]) -> int:
        body = self._request_json(
            "POST",
            "/chat",
            expected_status={200},
            json={
                "session_id": self._config.session_id,
                "patient_id": self._config.patient_id,
                "message": _QUERY,
            },
            headers={"X-Copilot-Request-Id": f"w2-verify-{uuid.uuid4()}"},
        )
        citations = body.get("citations")
        if not isinstance(citations, list):
            raise VerificationError("answer returned an invalid citations contract")
        uploaded = [
            citation
            for citation in citations
            if isinstance(citation, dict)
            and citation.get("source_type") == "uploaded_document"
            and isinstance(citation.get("page_or_section"), str)
            and bool(citation.get("page_or_section"))
            and isinstance(citation.get("field_or_chunk_id"), str)
            and bool(citation.get("field_or_chunk_id"))
            and isinstance(citation.get("quote_or_value"), str)
            and bool(citation.get("quote_or_value"))
        ]
        cited_source_ids = {citation.get("source_id") for citation in uploaded}
        if any(document.document_id not in cited_source_ids for document in documents):
            raise VerificationError(
                "answer is missing grounded uploaded-document citations"
            )
        return len(uploaded)

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        expected_status: set[int],
        transport_attempts: int = 1,
        request_client: object | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        client = request_client or self._client
        for attempt in range(transport_attempts):
            try:
                response = client.request(  # type: ignore[attr-defined]
                    method, self._config.agent_base_url + path, **kwargs
                )
                break
            except (httpx.HTTPError, _TransportError) as exc:
                if attempt + 1 >= transport_attempts:
                    raise VerificationError(
                        "deployed agent request failed "
                        f"({type(exc).__name__})"
                    ) from exc
                self._sleep(self._config.poll_interval_seconds)
        if response.status_code not in expected_status:
            # Do not render URL, headers, response body, or owner context.
            raise VerificationError(
                f"deployed agent returned HTTP {response.status_code}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise VerificationError("deployed agent returned invalid JSON") from exc
        if not isinstance(body, dict):
            raise VerificationError("deployed agent returned an invalid response contract")
        return body


def _digest_verified(value: object, *, expected_hash: str | None = None) -> bool:
    if not isinstance(value, dict):
        return False
    expected = value.get("expected_hash")
    observed = value.get("observed_hash")
    return (
        value.get("algorithm") == "sha256"
        and value.get("verified") is True
        and isinstance(expected, str)
        and len(expected) == 64
        and isinstance(observed, str)
        and observed == expected
        and (expected_hash is None or expected == expected_hash)
    )


def main(
    *,
    environ: Mapping[str, str] | None = None,
    client_factory: Callable[..., httpx.Client] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    try:
        config = VerificationConfig.from_env(environ)
        if client_factory is None:
            client: (
                httpx.Client
                | _FreshRequestClient
                | _CurlClient
            ) = _CurlClient(config.request_timeout_seconds)
            ready_client: object = client
        else:
            client = _FreshRequestClient(
                client_factory,
                {
                    "timeout": config.request_timeout_seconds,
                    "follow_redirects": False,
                    "headers": {
                        "User-Agent": "openemr-copilot-w2-verifier/1",
                        "Accept-Encoding": "identity",
                    },
                },
            )
            ready_client = client
        result = LiveWritePathVerifier(
            config,
            client=client,
            ready_client=ready_client,
            sleep=sleep,
        ).run()
    except VerificationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(
        "PASS: deployed W2 write path verified "
        f"({result.documents_verified} documents, "
        f"{result.source_binaries_verified} source Binaries, "
        f"{result.artifact_binaries_verified} artifact Binaries, "
        f"{result.uploaded_document_citations} grounded citations)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
