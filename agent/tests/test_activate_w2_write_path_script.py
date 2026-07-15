"""Fail-closed activation automation tests (W2-D1/D3/D6/D9/D10; §3/§5).

These tests deliberately replace Railway, OpenEMR, SMART launch, and the live verifier
with in-memory fakes.  They pin the activation *ordering* without permitting a test to
read or supply either owner-managed Railway secret.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
import importlib
import subprocess
from types import SimpleNamespace
from typing import Any

import pytest
import scripts.activate_w2_write_path as activation_module

from scripts.activate_w2_write_path import (
    OWNER_ONLY_SECRET_NAMES,
    ActivationConfig,
    ActivationError,
    ActivationOrchestrator,
    OpenEMRAttestation,
    RailwayCLI,
    RailwayOpenEMRInspectorImpl,
    REQUIRED_SMART_SCOPES,
    VerifyScript,
)


_OWNER_SECRET_VALUES = {
    "SMART_CLIENT_SECRET": "smart-secret-must-never-be-read-or-printed",
    "DOCUMENT_CREDENTIAL_KEY": "credential-key-must-never-be-read-or-printed",
}

_ENV = {
    "W2_ACTIVATE_RAILWAY_PROJECT_ID": "project-test",
    "W2_ACTIVATE_RAILWAY_ENVIRONMENT": "production",
    "W2_ACTIVATE_WEB_SERVICE": "agent",
    "W2_ACTIVATE_WORKER_SERVICE": "document-worker",
    "W2_VERIFY_AGENT_BASE_URL": "https://agent.example",
    "W2_ACTIVATE_OPENEMR_BASE_URL": "https://openemr.example",
    "OPENEMR_FHIR_BASE_URL": "https://openemr.example/apis/default/fhir",
    "OPENEMR_OAUTH_BASE_URL": "https://openemr.example/oauth2/default",
    "OPENEMR_REST_BASE_URL": "https://openemr.example/apis/default",
    "AGENT_CALLBACK_URL": "https://agent.example/callback",
    "W2_VERIFY_PATIENT_ID": "synthetic-patient-uuid",
    "W2_VERIFY_SYNTHETIC_ONLY_ACK": "synthetic-patient-and-documents",
    "OE_USERNAME": "synthetic-launch-user",
    "OE_ADMIN_PASS": "launch-password-must-never-print",
    "SELENIUM_URL": "http://selenium.example:4444/wd/hub",
    **_OWNER_SECRET_VALUES,
}


class _TrackingEnvironment(Mapping[str, str]):
    """Mapping that records every key the activation config attempts to read."""

    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = dict(values)
        self.read_keys: list[str] = []

    def __getitem__(self, key: str) -> str:
        self.read_keys.append(key)
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def get(self, key: str, default: str | None = None) -> str | None:
        self.read_keys.append(key)
        return self._values.get(key, default)


@dataclass
class _FakeRailway:
    events: list[tuple[Any, ...]]
    services: set[str] = field(default_factory=set)
    creates: int = 0
    fail_deploy: str | None = None
    fail_running: str | None = None
    fail_ready: bool = False
    fail_stopped_after: int | None = None
    stopped_checks: int = 0

    def ensure_worker_service(self, service: str) -> None:
        self.events.append(("ensure_worker", service))
        if service not in self.services:
            self.services.add(service)
            self.creates += 1

    def set_variables(self, service: str, variables: Mapping[str, str]) -> None:
        copied = dict(variables)
        assert OWNER_ONLY_SECRET_NAMES.isdisjoint(copied)
        self.events.append(("set_variables", service, copied))

    def deploy(self, service: str) -> None:
        self.events.append(("deploy", service))
        if self.fail_deploy == service:
            raise ActivationError(f"{service} deployment did not become successful")

    def require_service_running(self, service: str) -> None:
        self.events.append(("require_running", service))
        if self.fail_running == service:
            raise ActivationError(f"{service} process did not remain running")

    def require_web_ready(self, service: str) -> None:
        self.events.append(("require_web_ready", service))
        if self.fail_ready:
            raise ActivationError("web readiness did not become green")

    def require_web_disabled(self, service: str) -> None:
        self.events.append(("require_web_disabled", service))

    def stop_service(self, service: str) -> None:
        self.events.append(("stop_service", service))

    def require_service_stopped(self, service: str) -> None:
        self.events.append(("require_stopped", service))
        self.stopped_checks += 1
        if (
            self.fail_stopped_after is not None
            and self.stopped_checks >= self.fail_stopped_after
        ):
            raise ActivationError(f"{service} stop could not be verified")

    def list_variables(self, _service: str) -> Mapping[str, str]:
        raise AssertionError("activation must never fetch Railway variable values")


@dataclass
class _FakeOpenEMR:
    events: list[tuple[Any, ...]]

    def discover_attestation(self) -> OpenEMRAttestation:
        self.events.append(("discover_openemr_attestation",))
        return OpenEMRAttestation.from_rows(
            [
                {
                    "name": "AI-Source-Documents",
                    "path": "/AI-Source-Documents",
                    "id": "101",
                    "aco_spec": "patients|docs",
                },
                {
                    "name": "AI-Extractions",
                    "path": "/AI-Extractions",
                    "id": "202",
                    "aco_spec": "patients|docs",
                },
            ],
            system_error_logging="WARNING",
            client_id="replacement-client-public-id",
            encounter_id="synthetic-encounter-uuid",
        )


@dataclass
class _FakeSmart:
    events: list[tuple[Any, ...]]

    def establish_session(
        self, *, patient_id: str, encounter_id: str
    ) -> Mapping[str, str]:
        self.events.append(("establish_smart_session",))
        assert patient_id == _ENV["W2_VERIFY_PATIENT_ID"]
        assert encounter_id == "synthetic-encounter-uuid"
        return {
            "W2_VERIFY_SESSION_ID": "opaque-session-must-never-print",
            "W2_VERIFY_PATIENT_ID": patient_id,
            "W2_VERIFY_ENCOUNTER_ID": encounter_id,
        }


@dataclass
class _FakeVerifier:
    events: list[tuple[Any, ...]]
    calls: list[dict[str, str]] = field(default_factory=list)

    def run(self, environ: Mapping[str, str]) -> object:
        copied = dict(environ)
        self.events.append(("verify_live_write_path",))
        self.calls.append(copied)
        return object()


def _components(
    *,
    railway: _FakeRailway | None = None,
    smart: _FakeSmart | None = None,
) -> tuple[
    list[tuple[Any, ...]],
    ActivationConfig,
    _FakeRailway,
    _FakeVerifier,
    ActivationOrchestrator,
]:
    events: list[tuple[Any, ...]] = []
    config = ActivationConfig.from_env(_ENV)
    railway = railway or _FakeRailway(events)
    # Allow callers to build a failure fake before the shared event list exists.
    railway.events = events
    verifier = _FakeVerifier(events)
    orchestrator = ActivationOrchestrator(
        config,
        railway=railway,
        openemr=_FakeOpenEMR(events),
        smart=smart or _FakeSmart(events),
        verifier=verifier,
    )
    return events, config, railway, verifier, orchestrator


def _variable_events(
    events: list[tuple[Any, ...]],
) -> list[tuple[str, str, dict[str, str]]]:
    return [event for event in events if event[0] == "set_variables"]


def _enable_event_index(
    events: list[tuple[Any, ...]], service: str, enabled: str
) -> int:
    return next(
        index
        for index, event in enumerate(events)
        if event[0] == "set_variables"
        and event[1] == service
        and event[2].get("W2_DOCUMENT_RUNTIME_ENABLED") == enabled
    )


def test_config_is_environment_only_and_does_not_read_owner_secret_values() -> None:
    environ = _TrackingEnvironment(_ENV)
    config = ActivationConfig.from_env(environ)

    assert config.smart_client_id == ""
    assert OWNER_ONLY_SECRET_NAMES == frozenset(
        {"SMART_CLIENT_SECRET", "DOCUMENT_CREDENTIAL_KEY"}
    )
    assert OWNER_ONLY_SECRET_NAMES.isdisjoint(environ.read_keys)
    rendered = repr(config)
    assert all(value not in rendered for value in _OWNER_SECRET_VALUES.values())
    assert _ENV["OE_ADMIN_PASS"] not in rendered

    # The public client/encounter IDs are discovered from read-only OpenEMR state;
    # neither is an owner copy/paste prerequisite.
    assert config.encounter_id == ""


def test_openemr_attestation_requires_exact_categories_acl_and_warning() -> None:
    rows = [
        {
            "name": "AI-Source-Documents",
            "path": "/AI-Source-Documents",
            "id": "101",
            "aco_spec": "patients|docs",
        },
        {
            "name": "AI-Extractions",
            "path": "/AI-Extractions",
            "id": "202",
            "aco_spec": "patients|docs",
        },
    ]

    attestation = OpenEMRAttestation.from_rows(rows, system_error_logging="WARNING")
    assert attestation.source_category_id == "101"
    assert attestation.artifact_category_id == "202"

    invalid: list[tuple[list[dict[str, str]], str]] = [
        (rows[:1], "WARNING"),
        (rows + [dict(rows[0])], "WARNING"),
        ([{**rows[0], "path": "/nested/AI-Source-Documents"}, rows[1]], "WARNING"),
        ([{**rows[0], "aco_spec": "patients"}, rows[1]], "WARNING"),
        ([{**rows[0], "id": ""}, rows[1]], "WARNING"),
        (rows, "DEBUG"),
    ]
    for category_rows, logging_value in invalid:
        with pytest.raises(ActivationError):
            OpenEMRAttestation.from_rows(
                category_rows, system_error_logging=logging_value
            )


def test_read_only_discovery_validates_client_categories_logging_and_encounter() -> (
    None
):
    encounter_id = "12345678-1234-4234-9234-123456789abc"
    client_row = "\t".join(
        [
            "CLIENT",
            "public-client-id",
            "1",
            "1",
            "1",
            "https://agent.example/callback",
            "authorization_code refresh_token",
            " ".join(sorted(REQUIRED_SMART_SCOPES)),
            "0",
        ]
    )
    output = "\n".join(
        [
            "CATEGORY\tAI-Extractions\t202\tpatients|docs",
            "CATEGORY\tAI-Source-Documents\t101\tpatients|docs",
            "LOGGING\tWARNING",
            client_row,
            f"ENCOUNTER\t{encounter_id}",
        ]
    )

    class _ReadOnlyRailway:
        def ssh_keys_available(self) -> bool:
            return True

        def ssh_mysql(self, remote_script: str) -> str:
            assert "SELECT" in remote_script
            assert all(
                value not in remote_script for value in _OWNER_SECRET_VALUES.values()
            )
            return output

    config = ActivationConfig.from_env(
        {
            **_ENV,
            "W2_VERIFY_PATIENT_ID": "a234b786-539a-4f9a-96a0-432293226f02",
        }
    )
    inspector = RailwayOpenEMRInspectorImpl(config, _ReadOnlyRailway())  # type: ignore[arg-type]

    attestation = inspector.discover_attestation()

    assert attestation.client_id == "public-client-id"
    assert attestation.encounter_id == encounter_id
    assert attestation.source_category_id == "101"
    assert attestation.artifact_category_id == "202"


def test_railway_ssh_preserves_the_attestation_as_one_remote_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(
            command, 0, stdout="safe-output\n", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    config = ActivationConfig.from_env(_ENV)
    output = RailwayCLI(config).ssh_mysql("set -eu; echo safe")

    assert output == "safe-output\n"
    assert calls[0][-1] == "sh -lc 'set -eu; echo safe'"


def test_railway_stop_scales_each_live_worker_region_to_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], object]] = []
    railway = RailwayCLI(ActivationConfig.from_env(_ENV))
    monkeypatch.setattr(
        railway,
        "_service_metadata",
        lambda _service: {
            "deploymentId": "deployment-id",
            "regions": [{"name": "us-west2"}, {"name": "eu-west"}],
        },
    )
    monkeypatch.setattr(
        railway,
        "_run",
        lambda command, **kwargs: calls.append((command, kwargs.get("cwd"))) or "",
    )

    railway.stop_service("document-worker")

    assert len(calls) == 2
    link_command, link_cwd = calls[0]
    scale_command, scale_cwd = calls[1]
    assert link_command[:2] == ["railway", "link"]
    assert scale_command[:3] == ["railway", "service", "scale"]
    assert scale_command[-2:] == ["eu-west=0", "us-west2=0"]
    assert link_cwd is not None
    assert scale_cwd == link_cwd


def test_stopped_check_accepts_railway_flag_when_replica_metadata_is_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    railway = RailwayCLI(ActivationConfig.from_env(_ENV))
    monkeypatch.setattr(
        railway,
        "_service_metadata",
        lambda _service: {
            "deploymentId": "stopped-deployment-id",
            "deploymentStopped": True,
            "replicas": {"configured": 1, "running": 0, "crashed": 1},
        },
    )
    ticks = iter([0.0, 0.0, 181.0])
    monkeypatch.setattr(activation_module.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(activation_module.time, "sleep", lambda _seconds: None)

    railway.require_service_stopped("document-worker")


def test_web_disabled_check_requires_the_explicit_hard_ready_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = SimpleNamespace(
        status_code=200,
        json=lambda: {
            "status": "ready",
            "checks": [
                {
                    "name": "document_runtime",
                    "ok": True,
                    "kind": "hard",
                    "detail": "disabled",
                }
            ],
        },
    )
    monkeypatch.setattr(
        activation_module.httpx, "get", lambda *_args, **_kwargs: response
    )

    RailwayCLI(ActivationConfig.from_env(_ENV)).require_web_disabled("agent")


def test_deploy_tracks_the_exact_new_deployment_from_deployment_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    railway = RailwayCLI(ActivationConfig.from_env(_ENV))
    histories = iter(
        [
            [
                {"id": "new-deployment", "status": "BUILDING"},
                {"id": "old-deployment", "status": "SUCCESS"},
            ],
            [
                {"id": "unrelated-later-deployment", "status": "SUCCESS"},
                {"id": "new-deployment", "status": "SUCCESS"},
                {"id": "old-deployment", "status": "REMOVED"},
            ],
        ]
    )
    monkeypatch.setattr(
        railway, "_deployment_history", lambda _service: next(histories)
    )
    monkeypatch.setattr(activation_module.time, "sleep", lambda _seconds: None)

    railway._wait_for_success("agent", previous_deployments={"old-deployment"})


def test_upload_uses_the_explicit_context_as_archive_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], object]] = []
    railway = RailwayCLI(ActivationConfig.from_env(_ENV))
    monkeypatch.setattr(
        railway,
        "_run",
        lambda command, **kwargs: calls.append((command, kwargs.get("cwd"))) or "",
    )

    railway._upload("document-worker", activation_module.Path("/tmp/worker-context"))

    assert len(calls) == 1
    command, cwd = calls[0]
    assert "--path-as-root" in command
    assert command[-1] == "."
    assert cwd == activation_module.Path("/tmp/worker-context")


def test_openemr_attestation_discovers_the_schema_instead_of_trusting_template_db() -> (
    None
):
    config = ActivationConfig.from_env(
        {
            **_ENV,
            "W2_VERIFY_PATIENT_ID": "a234b786-539a-4f9a-96a0-432293226f02",
        }
    )
    inspector = RailwayOpenEMRInspectorImpl(config, object())  # type: ignore[arg-type]

    remote_script = inspector._remote_script(config.patient_id)

    assert "information_schema.tables" in remote_script
    assert "HAVING COUNT(DISTINCT table_name)=5" in remote_script
    assert "DB=${MYSQLDATABASE" not in remote_script


def test_verify_script_imports_its_sibling_under_direct_script_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    imported: list[str] = []

    def fake_import(name: str) -> object:
        imported.append(name)
        return SimpleNamespace(main=lambda **_kwargs: 0)

    monkeypatch.setattr(activation_module, "__package__", "")
    monkeypatch.setattr(importlib, "import_module", fake_import)

    assert VerifyScript().run({}) is True
    assert imported == ["verify_w2_write_path"]


def test_worker_ensure_and_disabled_prepare_are_idempotent_without_secret_reads() -> (
    None
):
    events, _config, railway, _verifier, orchestrator = _components()
    orchestrator.run()
    orchestrator.run()

    assert railway.creates == 1
    assert railway.services == {"document-worker"}
    variable_events = _variable_events(events)
    assert variable_events
    assert OWNER_ONLY_SECRET_NAMES.isdisjoint(
        name for _kind, _service, variables in variable_events for name in variables
    )
    # Every pass first pins both processes disabled before querying mutable prerequisites.
    for discovery_index in [
        index
        for index, event in enumerate(events)
        if event == ("discover_openemr_attestation",)
    ]:
        preceding = events[:discovery_index]
        assert any(
            event[0] == "set_variables"
            and event[1] == "document-worker"
            and event[2]["W2_DOCUMENT_RUNTIME_ENABLED"] == "false"
            for event in preceding
        )
        assert ("require_web_disabled", "agent") in preceding
        assert ("require_stopped", "document-worker") in preceding
        assert any(
            event[0] == "set_variables"
            and event[1] == "agent"
            and event[2]["W2_DOCUMENT_RUNTIME_ENABLED"] == "false"
            for event in preceding
        )


def test_activation_flips_worker_then_web_last_and_invokes_opaque_verifier() -> None:
    events, _config, _railway, verifier, orchestrator = _components()
    orchestrator.run()

    discover = events.index(("discover_openemr_attestation",))
    session = events.index(("establish_smart_session",))
    worker_true = _enable_event_index(events, "document-worker", "true")
    web_true = _enable_event_index(events, "agent", "true")
    worker_running = events.index(("require_running", "document-worker"))
    web_ready = events.index(("require_web_ready", "agent"))
    verify = events.index(("verify_live_write_path",))

    # The enabled web deploy intentionally precedes SMART launch: its in-memory token
    # cache would otherwise be erased by the deployment.
    assert (
        discover
        < worker_true
        < worker_running
        < web_true
        < web_ready
        < session
        < verify
    )

    category_sets = [
        variables
        for _kind, _service, variables in _variable_events(events)
        if "SOURCE_DOCUMENT_CATEGORY_ID" in variables
    ]
    assert category_sets
    assert all(
        variables["SOURCE_DOCUMENT_CATEGORY_ID"] == "101"
        and variables["ARTIFACT_DOCUMENT_CATEGORY_ID"] == "202"
        and variables["OPENEMR_BINARY_READBACK_SAFE"] == "true"
        for variables in category_sets
    )

    assert len(verifier.calls) == 1
    verification_env = verifier.calls[0]
    assert verification_env == {
        "W2_VERIFY_AGENT_BASE_URL": "https://agent.example",
        "W2_VERIFY_SESSION_ID": "opaque-session-must-never-print",
        "W2_VERIFY_PATIENT_ID": "synthetic-patient-uuid",
        "W2_VERIFY_ENCOUNTER_ID": "synthetic-encounter-uuid",
        "W2_VERIFY_SYNTHETIC_ONLY_ACK": "synthetic-patient-and-documents",
    }
    assert OWNER_ONLY_SECRET_NAMES.isdisjoint(verification_env)


def test_smart_session_must_match_the_exact_synthetic_patient_and_encounter() -> None:
    class _MismatchedSmart(_FakeSmart):
        def establish_session(
            self, *, patient_id: str, encounter_id: str
        ) -> Mapping[str, str]:
            self.events.append(("establish_smart_session",))
            return {
                "W2_VERIFY_SESSION_ID": "opaque-session-must-never-print",
                "W2_VERIFY_PATIENT_ID": "different-patient",
                "W2_VERIFY_ENCOUNTER_ID": encounter_id,
            }

    events: list[tuple[Any, ...]] = []
    smart = _MismatchedSmart(events)
    events, _config, _railway, verifier, orchestrator = _components(smart=smart)
    smart.events = events

    with pytest.raises(ActivationError, match="patient"):
        orchestrator.run()

    assert verifier.calls == []
    last_by_service: dict[str, dict[str, str]] = {}
    for _kind, service, variables in _variable_events(events):
        last_by_service[service] = variables
    assert last_by_service["document-worker"]["W2_DOCUMENT_RUNTIME_ENABLED"] == "false"
    assert last_by_service["agent"]["W2_DOCUMENT_RUNTIME_ENABLED"] == "false"


@pytest.mark.parametrize(
    ("failure", "expected_failure"),
    [
        ({"fail_deploy": "document-worker"}, "deployment"),
        ({"fail_deploy": "agent"}, "deployment"),
        ({"fail_running": "document-worker"}, "running"),
        ({"fail_ready": True}, "readiness"),
    ],
)
def test_failed_deployment_or_readiness_resets_both_services_disabled(
    failure: dict[str, object], expected_failure: str
) -> None:
    railway = _FakeRailway([], **failure)
    events, _config, _railway, verifier, orchestrator = _components(railway=railway)

    with pytest.raises(ActivationError, match=expected_failure):
        orchestrator.run()

    assert verifier.calls == []
    variable_events = _variable_events(events)
    last_by_service: dict[str, dict[str, str]] = {}
    for _kind, service, variables in variable_events:
        last_by_service[service] = variables
    assert last_by_service["document-worker"]["W2_DOCUMENT_RUNTIME_ENABLED"] == "false"
    assert last_by_service["agent"]["W2_DOCUMENT_RUNTIME_ENABLED"] == "false"

    if (
        failure.get("fail_deploy") == "document-worker"
        or failure.get("fail_running") == "document-worker"
    ):
        assert not any(
            event[0] == "set_variables"
            and event[1] == "agent"
            and event[2].get("W2_DOCUMENT_RUNTIME_ENABLED") == "true"
            for event in events
        )


def test_unverifiable_rollback_is_reported_instead_of_suppressed() -> None:
    railway = _FakeRailway([], fail_ready=True, fail_stopped_after=2)
    _events, _config, _railway, verifier, orchestrator = _components(railway=railway)

    with pytest.raises(ActivationError, match="rollback could not be verified"):
        orchestrator.run()

    assert verifier.calls == []
