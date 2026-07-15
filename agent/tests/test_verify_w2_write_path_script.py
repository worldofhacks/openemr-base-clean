"""Turnkey deployed write-path verifier tests (W2-D1/D3/D6/D9/D10; §3/§5)."""

from __future__ import annotations

import hashlib
import re
import subprocess
from collections.abc import Iterator

import httpx
import pytest
import scripts.verify_w2_write_path as verify_module

from scripts.verify_w2_write_path import (
    REQUIRED_ENV_NAMES,
    LiveWritePathVerifier,
    VerificationConfig,
    VerificationError,
    main,
)

_ENV = {
    "W2_VERIFY_AGENT_BASE_URL": "https://agent.example",
    "W2_VERIFY_SESSION_ID": "session-must-never-print",
    "W2_VERIFY_PATIENT_ID": "patient-synthetic-must-never-print",
    "W2_VERIFY_ENCOUNTER_ID": "encounter-synthetic-must-never-print",
    "W2_VERIFY_SYNTHETIC_ONLY_ACK": "synthetic-patient-and-documents",
}


def _ready(*, document_detail: str = "ready") -> dict[str, object]:
    return {
        "status": "ready",
        "checks": [
            {"name": "openemr_fhir", "kind": "hard", "ok": True, "detail": "HTTP 200"},
            {"name": "anthropic", "kind": "hard", "ok": True, "detail": "HTTP 200"},
            {"name": "session_store", "kind": "hard", "ok": True, "detail": "ready"},
            {"name": "langfuse", "kind": "soft", "ok": True, "detail": "ready"},
            {"name": "retrieval_index", "kind": "soft", "ok": True, "detail": "ok"},
            {
                "name": "document_runtime",
                "kind": "hard",
                "ok": True,
                "detail": document_detail,
            },
        ],
    }


def _digest(hash_value: str) -> dict[str, object]:
    return {
        "algorithm": "sha256",
        "expected_hash": hash_value,
        "observed_hash": hash_value,
        "verified": True,
    }


def _transport(
    config: VerificationConfig,
    *,
    duplicate: bool = False,
    document_detail: str = "ready",
    corrupt_source: bool = False,
    omit_intake_citation: bool = False,
    initial_worker_restart: bool = False,
    persistent_worker_restart: bool = False,
    initial_writeback_failed: bool = False,
    persistent_writeback_failed: bool = False,
    ready_transport_failures: int = 0,
) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    requests: list[httpx.Request] = []
    upload_index = 0
    status_polls = {"lab-document": 0, "intake-document": 0}
    retries = {"lab-document": 0, "intake-document": 0}
    hashes = {
        "lab-document": hashlib.sha256(config.lab_fixture.read_bytes()).hexdigest(),
        "intake-document": hashlib.sha256(
            config.intake_fixture.read_bytes()
        ).hexdigest(),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal upload_index
        requests.append(request)
        path = request.url.path
        if request.method == "GET" and path == "/ready":
            ready_requests = sum(
                item.method == "GET" and item.url.path == "/ready"
                for item in requests
            )
            if ready_requests <= ready_transport_failures:
                raise httpx.ReadError("synthetic Railway transport drop", request=request)
            return httpx.Response(200, json=_ready(document_detail=document_detail))
        if request.method == "POST" and path == "/documents":
            document_id = ("intake-document", "lab-document")[upload_index % 2]
            upload_index += 1
            return httpx.Response(
                200 if duplicate else 202,
                json={
                    "job_id": f"job-{document_id}",
                    "document_id": document_id,
                    "state": "complete" if duplicate else "queued",
                    "status_url": f"/documents/{document_id}/status",
                    "correlation_id": "correlation-synthetic",
                },
            )
        if request.method == "GET" and path.endswith("/status"):
            document_id = path.split("/")[2]
            status_polls[document_id] += 1
            retryable_reason = (
                "worker_restart"
                if initial_worker_restart
                else "writeback_failed" if initial_writeback_failed else None
            )
            persistent_failure = (
                persistent_worker_restart or persistent_writeback_failed
            )
            if retryable_reason is not None and (
                retries[document_id] == 0 or persistent_failure
            ):
                return httpx.Response(
                    200,
                    json={
                        "document_id": document_id,
                        "state": "failed",
                        "reason": retryable_reason,
                        "correlation_id": "correlation-synthetic",
                        "updated_ts": "2026-07-14T12:00:00Z",
                        "fields_grounded": 0,
                        "fields_unsupported": 0,
                        "attempt_count": 3,
                        "next_retry_at": None,
                    },
                )
            state = "complete" if status_polls[document_id] >= 2 else "queued"
            return httpx.Response(
                200,
                json={
                    "document_id": document_id,
                    "state": state,
                    "reason": None,
                    "correlation_id": "correlation-synthetic",
                    "updated_ts": "2026-07-14T12:00:00Z",
                    "fields_grounded": 6,
                    "fields_unsupported": 0,
                    "attempt_count": 1,
                    "next_retry_at": None,
                },
            )
        if request.method == "POST" and path.endswith("/retry"):
            document_id = path.split("/")[2]
            retries[document_id] += 1
            assert request.url.params["session_id"] == _ENV["W2_VERIFY_SESSION_ID"]
            assert request.read() == b'{"expected_state":"failed"}'
            return httpx.Response(
                202,
                json={
                    "job_id": f"job-{document_id}",
                    "document_id": document_id,
                    "state": "queued",
                    "status_url": f"/documents/{document_id}/status",
                    "correlation_id": "correlation-retry-synthetic",
                },
            )
        if request.method == "GET" and path.endswith("/readback-verification"):
            document_id = path.split("/")[2]
            source = _digest(hashes[document_id])
            if corrupt_source:
                source["observed_hash"] = "f" * 64
                source["verified"] = False
            return httpx.Response(
                200,
                json={
                    "document_id": document_id,
                    "source": source,
                    "artifact": _digest("a" * 64),
                },
            )
        if request.method == "POST" and path == "/chat":
            document_ids = ["lab-document"]
            if not omit_intake_citation:
                document_ids.append("intake-document")
            return httpx.Response(
                200,
                json={
                    "brief": "verified synthetic response",
                    "source": "llm",
                    "degraded": False,
                    "verdicts": ["pass"],
                    "citations": [
                        {
                            "source_type": "uploaded_document",
                            "source_id": document_id,
                            "page_or_section": "1",
                            "field_or_chunk_id": "field.synthetic",
                            "quote_or_value": "synthetic",
                        }
                        for document_id in document_ids
                    ],
                    "patient": None,
                    "correlation_id": "correlation-synthetic",
                },
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler), requests


def _zero_sleep(_seconds: float) -> None:
    return None


def test_config_requires_exact_owner_context_names_and_committed_synthetic_fixtures():
    with pytest.raises(VerificationError) as caught:
        VerificationConfig.from_env({})
    assert all(name in str(caught.value) for name in REQUIRED_ENV_NAMES)

    config = VerificationConfig.from_env(_ENV)
    assert config.agent_base_url == "https://agent.example"
    assert config.lab_fixture.name == "lab-clean-glucose.pdf"
    assert config.intake_fixture.name == "intake-bp-separate-candidates.pdf"
    assert config.lab_fixture.is_file()
    assert config.intake_fixture.is_file()
    assert config.request_timeout_seconds == 120.0

    with pytest.raises(VerificationError, match="attest"):
        VerificationConfig.from_env(
            {**_ENV, "W2_VERIFY_SYNTHETIC_ONLY_ACK": "not-confirmed"}
        )


def test_verifier_proves_ready_upload_poll_binary_attestation_and_grounded_citations():
    config = VerificationConfig.from_env(_ENV)
    transport, requests = _transport(config)
    with httpx.Client(transport=transport) as client:
        result = LiveWritePathVerifier(config, client=client, sleep=_zero_sleep).run()

    assert result.documents_verified == 2
    assert result.source_binaries_verified == 2
    assert result.artifact_binaries_verified == 2
    assert result.uploaded_document_citations >= 2
    assert [request.url.path for request in requests].count("/documents") == 2
    assert [request.url.path for request in requests].count("/ready") == 2
    uploads = [request for request in requests if request.url.path == "/documents"]
    assert b"intake_form" in uploads[0].content
    assert b"lab_pdf" in uploads[1].content
    assert _ENV["W2_VERIFY_ENCOUNTER_ID"].encode() in uploads[0].content
    assert _ENV["W2_VERIFY_ENCOUNTER_ID"].encode() not in uploads[1].content


def test_verifier_is_idempotent_when_uploads_resolve_to_existing_documents():
    config = VerificationConfig.from_env(_ENV)
    transport, _requests = _transport(config, duplicate=True)
    with httpx.Client(transport=transport) as client:
        first = LiveWritePathVerifier(config, client=client, sleep=_zero_sleep).run()
        second = LiveWritePathVerifier(config, client=client, sleep=_zero_sleep).run()
    assert first.documents_verified == second.documents_verified == 2


def test_verifier_retries_only_the_read_only_ready_transport_probe() -> None:
    config = VerificationConfig.from_env(_ENV)
    transport, requests = _transport(config, ready_transport_failures=1)

    with httpx.Client(transport=transport) as client:
        result = LiveWritePathVerifier(config, client=client, sleep=_zero_sleep).run()

    paths = [request.url.path for request in requests]
    assert result.documents_verified == 2
    assert paths.count("/ready") == 3
    assert paths.count("/documents") == 2


def test_verifier_stops_before_upload_after_bounded_ready_transport_failures() -> None:
    config = VerificationConfig.from_env(_ENV)
    transport, requests = _transport(config, ready_transport_failures=3)

    with httpx.Client(transport=transport) as client:
        with pytest.raises(VerificationError, match="deployed agent request failed") as caught:
            LiveWritePathVerifier(config, client=client, sleep=_zero_sleep).run()

    paths = [request.url.path for request in requests]
    assert paths == ["/ready", "/ready", "/ready"]
    assert "ReadError" in str(caught.value)
    assert "synthetic Railway transport drop" not in str(caught.value)


def test_verifier_retries_each_existing_worker_restart_once_via_typed_route():
    config = VerificationConfig.from_env(_ENV)
    transport, requests = _transport(
        config,
        duplicate=True,
        initial_worker_restart=True,
    )
    with httpx.Client(transport=transport) as client:
        result = LiveWritePathVerifier(config, client=client, sleep=_zero_sleep).run()

    retry_paths = [
        request.url.path for request in requests if request.url.path.endswith("/retry")
    ]
    assert retry_paths == [
        "/documents/intake-document/retry",
        "/documents/lab-document/retry",
    ]
    assert result.documents_verified == 2


def test_verifier_never_loops_worker_restart_retry():
    config = VerificationConfig.from_env(_ENV)
    transport, requests = _transport(
        config,
        duplicate=True,
        initial_worker_restart=True,
        persistent_worker_restart=True,
    )
    with httpx.Client(transport=transport) as client:
        with pytest.raises(VerificationError, match="terminal"):
            LiveWritePathVerifier(config, client=client, sleep=_zero_sleep).run()

    assert sum(request.url.path.endswith("/retry") for request in requests) == 1


def test_verifier_retries_existing_writeback_failure_once_via_typed_route():
    config = VerificationConfig.from_env(_ENV)
    transport, requests = _transport(
        config,
        duplicate=True,
        initial_writeback_failed=True,
    )
    with httpx.Client(transport=transport) as client:
        result = LiveWritePathVerifier(config, client=client, sleep=_zero_sleep).run()

    retry_paths = [
        request.url.path for request in requests if request.url.path.endswith("/retry")
    ]
    assert retry_paths == [
        "/documents/intake-document/retry",
        "/documents/lab-document/retry",
    ]
    assert result.documents_verified == 2


def test_verifier_never_loops_writeback_failure_retry():
    config = VerificationConfig.from_env(_ENV)
    transport, requests = _transport(
        config,
        duplicate=True,
        initial_writeback_failed=True,
        persistent_writeback_failed=True,
    )
    with httpx.Client(transport=transport) as client:
        with pytest.raises(VerificationError, match="terminal"):
            LiveWritePathVerifier(config, client=client, sleep=_zero_sleep).run()

    assert sum(request.url.path.endswith("/retry") for request in requests) == 1


@pytest.mark.parametrize(
    ("kwargs", "failure"),
    [
        ({"document_detail": "disabled"}, "active"),
        ({"corrupt_source": True}, "Binary"),
        ({"omit_intake_citation": True}, "citations"),
    ],
)
def test_verifier_fails_closed_on_inactive_runtime_readback_mismatch_or_missing_citation(
    kwargs: dict[str, object], failure: str
):
    config = VerificationConfig.from_env(_ENV)
    transport, _requests = _transport(config, **kwargs)
    with httpx.Client(transport=transport) as client:
        with pytest.raises(VerificationError, match=failure):
            LiveWritePathVerifier(config, client=client, sleep=_zero_sleep).run()


def test_cli_output_never_prints_session_patient_encounter_hash_or_fixture_content(capsys):
    config = VerificationConfig.from_env(_ENV)
    transport, requests = _transport(config)
    client_options: dict[str, object] = {}
    client_factory_calls = 0

    def client_factory(**kwargs: object) -> httpx.Client:
        nonlocal client_factory_calls
        client_factory_calls += 1
        client_options.update(kwargs)
        return httpx.Client(transport=transport, **kwargs)

    assert main(environ=_ENV, client_factory=client_factory, sleep=_zero_sleep) == 0
    assert client_options["headers"] == {
        "User-Agent": "openemr-copilot-w2-verifier/1",
        "Accept-Encoding": "identity",
    }
    assert client_factory_calls == len(requests)
    assert client_factory_calls > 1
    output = capsys.readouterr().out
    assert "PASS" in output
    forbidden: Iterator[str] = iter(
        (
            _ENV["W2_VERIFY_SESSION_ID"],
            _ENV["W2_VERIFY_PATIENT_ID"],
            _ENV["W2_VERIFY_ENCOUNTER_ID"],
            hashlib.sha256(config.lab_fixture.read_bytes()).hexdigest(),
            "Glucose",
        )
    )
    assert all(value not in output for value in forbidden)


def test_cli_defaults_to_the_isolated_curl_transport(monkeypatch, capsys) -> None:
    config = VerificationConfig.from_env(_ENV)
    transport, _requests = _transport(config)
    fallback = httpx.Client(transport=transport)
    ready_timeouts: list[float] = []

    def ready_client(timeout: float) -> httpx.Client:
        ready_timeouts.append(timeout)
        return fallback

    monkeypatch.setattr(verify_module, "_CurlClient", ready_client)
    try:
        assert verify_module.main(environ=_ENV, sleep=_zero_sleep) == 0
    finally:
        fallback.close()

    assert ready_timeouts == [config.request_timeout_seconds]
    assert "PASS" in capsys.readouterr().out


def test_curl_ready_transport_is_https_only_bounded_and_json_typed() -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"status":"ready"}\n200',
            stderr="",
        )

    client = verify_module._CurlClient(
        timeout=17.0,
        curl_path="/usr/bin/curl",
        command_prefix=("/bin/launchctl", "asuser", "501"),
        run_command=run,
    )
    response = client.request("GET", "https://agent.example/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    command, kwargs = calls[0]
    assert command[:4] == ["/bin/launchctl", "asuser", "501", "/usr/bin/curl"]
    assert "--proto" in command and "=https" in command
    assert "--max-time" in command and "17.0" in command
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True


def test_curl_transport_uses_launchd_owned_job_and_private_config() -> None:
    marker = "opaque-session-must-not-reach-process-arguments"
    observed: dict[str, object] = {}
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[1] == "submit":
            config_path = verify_module.Path(command[command.index("--config") + 1])
            response_path = verify_module.Path(command[command.index("-o") + 1])
            command_index = command.index("--")
            wrapper_path = verify_module.Path(command[command_index + 2])
            completion_path = verify_module.Path(command[command_index + 3])
            stderr_path = verify_module.Path(command[command.index("-e") + 1])
            observed.update(
                command=command,
                config_path=config_path,
                config=config_path.read_text(),
                config_mode=config_path.stat().st_mode & 0o777,
                wrapper=wrapper_path.read_text(),
                response_mode=response_path.stat().st_mode & 0o777,
                stderr_mode=stderr_path.stat().st_mode & 0o777,
            )
            response_path.write_text('{"status":"ready"}\n200')
            completion_path.write_text("0\n")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    client = verify_module._CurlClient(
        timeout=17.0,
        curl_path="/usr/bin/curl",
        launchctl_path="/bin/launchctl",
        run_command=run,
    )
    response = client.request(
        "GET",
        "https://agent.example/ready",
        headers={"X-Synthetic-Context": marker},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    command = observed["command"]
    assert command[:2] == ["/bin/launchctl", "submit"]
    assert marker not in " ".join(command)
    assert marker in str(observed["config"])
    assert observed["config_mode"] == 0o600
    assert observed["response_mode"] == 0o600
    assert observed["stderr_mode"] == 0o600
    assert "while :" in str(observed["wrapper"])
    assert not observed["config_path"].exists()
    assert calls[-1][1] == "remove"


def test_launchd_transport_never_replays_failed_curl_and_removes_job() -> None:
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[1] == "submit":
            response_path = verify_module.Path(command[command.index("-o") + 1])
            command_index = command.index("--")
            completion_path = verify_module.Path(command[command_index + 3])
            response_path.write_text('{"status":"ready"}\n200')
            completion_path.write_text("7\n")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    client = verify_module._CurlClient(
        timeout=0.01,
        curl_path="/usr/bin/curl",
        launchctl_path="/bin/launchctl",
        run_command=run,
    )
    with pytest.raises(verify_module.CurlTransportError):
        client.request("GET", "https://agent.example/ready")

    assert sum(command[1] == "submit" for command in calls) == 1
    assert sum(command[1] == "remove" for command in calls) == 1


def test_curl_transport_keeps_context_in_stdin_and_deletes_multipart_tempfile() -> None:
    marker = "opaque-session-must-not-reach-process-arguments"
    observed: dict[str, object] = {}

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        config = str(kwargs["input"])
        upload_match = re.search(r"file=@([^;\"]+)", config)
        assert upload_match is not None
        upload = verify_module.Path(upload_match.group(1))
        observed.update(
            command=command,
            config=config,
            upload=upload,
            content=upload.read_bytes(),
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"document_id":"synthetic"}\n202',
            stderr="",
        )

    client = verify_module._CurlClient(
        timeout=17.0,
        curl_path="/usr/bin/curl",
        command_prefix=(),
        run_command=run,
    )
    response = client.request(
        "POST",
        "https://agent.example/documents",
        data={"session_id": marker, "doc_type": "lab_pdf"},
        files={"file": ("synthetic.pdf", b"synthetic-pdf", "application/pdf")},
        headers={"X-Copilot-Request-Id": "synthetic-request"},
    )

    assert response.status_code == 202
    assert response.json() == {"document_id": "synthetic"}
    assert marker not in " ".join(observed["command"])
    assert marker in str(observed["config"])
    assert observed["content"] == b"synthetic-pdf"
    assert not observed["upload"].exists()
