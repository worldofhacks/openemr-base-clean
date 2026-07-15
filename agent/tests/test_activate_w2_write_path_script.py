"""Fail-closed activation automation tests (W2-D1/D3/D6/D9/D10; §3/§5).

These tests deliberately replace Railway, OpenEMR, SMART launch, and the live verifier
with in-memory fakes.  They pin the activation *ordering* without permitting a test to
read or supply either owner-managed Railway secret.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
import json
import subprocess
from types import SimpleNamespace
from typing import Any

import pytest
import scripts.activate_w2_write_path as activation_module

from scripts.activate_w2_write_path import (
    LEGACY_ROUTE_VARIABLE_NAMES,
    OWNER_ONLY_SECRET_NAMES,
    ActivationConfig,
    ActivationError,
    ActivationOrchestrator,
    OpenEMRAttestation,
    RailwayCLI,
    RailwayOpenEMRInspectorImpl,
    REQUIRED_SMART_SCOPES,
    RouteAttestationSnapshot,
    SeleniumSmartSession,
    VerifyScript,
)


_OWNER_SECRET_VALUES = {
    "SMART_CLIENT_SECRET": "smart-secret-must-never-be-read-or-printed",
    "DOCUMENT_CREDENTIAL_KEY": "credential-key-must-never-be-read-or-printed",
}

_PATIENT_UUID = "a234b786-539a-4f9a-96a0-432293226f02"
_PATIENT_ID = "731"
_ENCOUNTER_UUID = "12345678-1234-4234-9234-123456789abc"
_ENCOUNTER_ID = "912"
_PATIENT_UUID_2 = "b345c897-64ab-4fab-a7b1-543304337a13"
_PATIENT_ID_2 = "732"
_ENCOUNTER_UUID_2 = "23456789-2345-4345-a345-23456789abcd"
_ENCOUNTER_ID_2 = "913"

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
    "W2_VERIFY_PATIENT_ID": _PATIENT_UUID,
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

    def remove_variables(self, service: str, names: list[str]) -> None:
        assert frozenset(names) == LEGACY_ROUTE_VARIABLE_NAMES
        self.events.append(("remove_variables", service, tuple(names)))

    def import_route_attestations(
        self, service: str, payload: Mapping[str, object]
    ) -> None:
        self.events.append(("import_route_attestations", service, dict(payload)))

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
            secure_upload="1",
            json_mime_enabled="1",
            client_id="replacement-client-public-id",
            patient_rows=[
                (_PATIENT_UUID_2, _PATIENT_ID_2),
                (_PATIENT_UUID, _PATIENT_ID),
            ],
            encounter_rows=[
                (_ENCOUNTER_UUID_2, _ENCOUNTER_ID_2, _PATIENT_UUID_2),
                (_ENCOUNTER_UUID, _ENCOUNTER_ID, _PATIENT_UUID),
            ],
            verification_patient_uuid=_PATIENT_UUID,
        )


@dataclass
class _FakeSmart:
    events: list[tuple[Any, ...]]

    def establish_session(
        self, *, patient_id: str, encounter_id: str
    ) -> Mapping[str, str]:
        self.events.append(("establish_smart_session",))
        assert patient_id == _ENV["W2_VERIFY_PATIENT_ID"]
        assert encounter_id == _ENCOUNTER_UUID
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
    assert config.synthetic_only_acknowledged is True

    with pytest.raises(ActivationError, match="synthetic-only"):
        ActivationConfig.from_env(
            {
                key: value
                for key, value in _ENV.items()
                if key != "W2_VERIFY_SYNTHETIC_ONLY_ACK"
            }
        )


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

    attestation = OpenEMRAttestation.from_rows(
        rows,
        system_error_logging="WARNING",
        secure_upload="1",
        json_mime_enabled="1",
        patient_rows=[(_PATIENT_UUID, _PATIENT_ID)],
        encounter_rows=[(_ENCOUNTER_UUID, _ENCOUNTER_ID, _PATIENT_UUID)],
        verification_patient_uuid=_PATIENT_UUID,
    )
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
                category_rows,
                system_error_logging=logging_value,
                secure_upload="1",
                json_mime_enabled="1",
                patient_rows=[(_PATIENT_UUID, _PATIENT_ID)],
                encounter_rows=[
                    (_ENCOUNTER_UUID, _ENCOUNTER_ID, _PATIENT_UUID)
                ],
                verification_patient_uuid=_PATIENT_UUID,
            )

    for secure_upload, json_mime_enabled in (("0", "1"), ("1", "0")):
        with pytest.raises(ActivationError):
            OpenEMRAttestation.from_rows(
                rows,
                system_error_logging="WARNING",
                secure_upload=secure_upload,
                json_mime_enabled=json_mime_enabled,
                patient_rows=[(_PATIENT_UUID, _PATIENT_ID)],
                encounter_rows=[
                    (_ENCOUNTER_UUID, _ENCOUNTER_ID, _PATIENT_UUID)
                ],
                verification_patient_uuid=_PATIENT_UUID,
            )


def test_route_snapshot_is_sorted_hashed_and_ownership_checked() -> None:
    first = RouteAttestationSnapshot.from_rows(
        [(_PATIENT_UUID_2, _PATIENT_ID_2), (_PATIENT_UUID, _PATIENT_ID)],
        [
            (_ENCOUNTER_UUID_2, _ENCOUNTER_ID_2, _PATIENT_UUID_2),
            (_ENCOUNTER_UUID, _ENCOUNTER_ID, _PATIENT_UUID),
        ],
    )
    second = RouteAttestationSnapshot.from_rows(
        [(_PATIENT_UUID, _PATIENT_ID), (_PATIENT_UUID_2, _PATIENT_ID_2)],
        [
            (_ENCOUNTER_UUID, _ENCOUNTER_ID, _PATIENT_UUID),
            (_ENCOUNTER_UUID_2, _ENCOUNTER_ID_2, _PATIENT_UUID_2),
        ],
    )

    assert first.payload() == second.payload()
    assert first.payload()["patient_count"] == 2
    assert first.payload()["encounter_count"] == 2
    assert (
        first.snapshot_hash
        == "aad1eab678ec41daa446a765f646b8287c5631ba5b95d48e2d78ea13b439b9ac"
    )
    assert [row["patient_uuid"] for row in first.payload()["patients"]] == sorted(
        [_PATIENT_UUID, _PATIENT_UUID_2]
    )

    with pytest.raises(ActivationError, match="unattested patient"):
        RouteAttestationSnapshot.from_rows(
            [(_PATIENT_UUID, _PATIENT_ID)],
            [(_ENCOUNTER_UUID_2, _ENCOUNTER_ID_2, _PATIENT_UUID_2)],
        )
    with pytest.raises(ActivationError, match="duplicated"):
        RouteAttestationSnapshot.from_rows(
            [(_PATIENT_UUID, _PATIENT_ID), (_PATIENT_UUID, _PATIENT_ID_2)],
            [],
        )


def test_read_only_discovery_validates_client_categories_and_all_routes() -> (
    None
):
    encounter_id = _ENCOUNTER_UUID
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
            "https://agent.example/week2/launch",
        ]
    )
    output = "\n".join(
        [
            "CATEGORY\tAI-Extractions\t202\tpatients|docs",
            "CATEGORY\tAI-Source-Documents\t101\tpatients|docs",
            "LOGGING\tWARNING",
            "UPLOAD\t1\t1",
            client_row,
            f"PATIENT\t{_PATIENT_UUID_2}\t{_PATIENT_ID_2}",
            f"PATIENT\t{_PATIENT_UUID}\t{_PATIENT_ID}",
            f"ENCOUNTER\t{_ENCOUNTER_UUID_2}\t{_ENCOUNTER_ID_2}\t{_PATIENT_UUID_2}",
            f"ENCOUNTER\t{encounter_id}\t{_ENCOUNTER_ID}\t{_PATIENT_UUID}",
        ]
    )

    class _ReadOnlyRailway:
        def ssh_keys_available(self) -> bool:
            return True

        def ssh_mysql(self, remote_script: str) -> str:
            assert "SELECT" in remote_script
            assert "initiate_login_uri" in remote_script
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
    assert attestation.verification_patient_uuid == _PATIENT_UUID
    assert attestation.verification_encounter_uuid == _ENCOUNTER_UUID
    assert attestation.route_snapshot is not None
    assert attestation.route_snapshot.payload()["patient_count"] == 2
    assert attestation.route_snapshot.payload()["encounter_count"] == 2
    assert attestation.source_category_id == "101"
    assert attestation.artifact_category_id == "202"
    assert attestation.secure_upload_enabled is True
    assert attestation.json_mime_enabled is True


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


def test_route_registry_import_uses_stdin_not_argv_or_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    railway = RailwayCLI(ActivationConfig.from_env(_ENV))
    monkeypatch.setattr(
        railway,
        "_run",
        lambda command, **kwargs: calls.append((command, kwargs)) or "",
    )
    snapshot = RouteAttestationSnapshot.from_rows(
        [(_PATIENT_UUID, _PATIENT_ID)],
        [(_ENCOUNTER_UUID, _ENCOUNTER_ID, _PATIENT_UUID)],
    )

    railway.import_route_attestations("agent", snapshot.payload())

    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command[-1] == "python -m app.writeback.route_attestations import-stdin"
    assert _PATIENT_UUID not in " ".join(command)
    assert _ENCOUNTER_UUID not in " ".join(command)
    imported = json.loads(str(kwargs["input_text"]))
    assert imported == snapshot.payload()
    assert kwargs["discard"] is True
    assert capsys.readouterr().out == ""


def test_retired_singleton_variables_are_removed_without_listing_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    railway = RailwayCLI(ActivationConfig.from_env(_ENV))
    monkeypatch.setattr(
        railway,
        "_run",
        lambda command, **kwargs: calls.append((command, kwargs)) or "",
    )

    railway.remove_variables("agent", sorted(LEGACY_ROUTE_VARIABLE_NAMES))

    assert len(calls) == 4
    assert all(call[0][1:3] == ["variable", "delete"] for call in calls)
    assert all("list" not in call[0] for call in calls)
    assert all(call[1]["allow_not_found"] is True for call in calls)


def test_retired_variable_removal_is_idempotent_but_not_error_blind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    railway = RailwayCLI(ActivationConfig.from_env(_ENV))

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 1, stdout="", stderr="variable not found"
        ),
    )
    railway.remove_variables("agent", ["OPENEMR_LEGACY_PATIENT_ID"])

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 1, stdout="", stderr="authentication failed"
        ),
    )
    with pytest.raises(ActivationError, match="retired route configuration"):
        railway.remove_variables("agent", ["OPENEMR_LEGACY_PATIENT_ID"])


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


def test_openemr_attestation_provisions_json_mime_and_discovers_the_schema() -> (
    None
):
    config = ActivationConfig.from_env(
        {
            **_ENV,
            "W2_VERIFY_PATIENT_ID": "a234b786-539a-4f9a-96a0-432293226f02",
        }
    )
    inspector = RailwayOpenEMRInspectorImpl(config, object())  # type: ignore[arg-type]

    remote_script = inspector._remote_script()
    semantic_script = remote_script.replace("'\"'\"'", "'")

    assert "information_schema.tables" in semantic_script
    assert "HAVING COUNT(DISTINCT table_name)=6" in semantic_script
    assert "DB=${MYSQLDATABASE" not in semantic_script
    assert "secure_upload" in semantic_script
    assert "files_white_list" in semantic_script
    assert "application/json" in semantic_script
    assert "BINARY option_id='application/json'" in semantic_script
    assert (
        "ON DUPLICATE KEY UPDATE option_id='application/json', "
        "title='application/json', activity=1"
    ) in semantic_script
    assert "DELETE " not in semantic_script
    assert "UPDATE globals" not in semantic_script
    assert "application/*" not in semantic_script
    assert "LIMIT 1" not in semantic_script
    assert "WHERE HEX(pd.uuid)" not in semantic_script
    assert "ORDER BY HEX(pd.uuid), pd.pid" in semantic_script
    assert "CAST(fe.encounter AS CHAR)" in semantic_script


def test_verify_script_runs_in_a_secret_isolated_child_process(capsys) -> None:
    verification_env = {
        "W2_VERIFY_AGENT_BASE_URL": "https://agent.example",
        "W2_VERIFY_SESSION_ID": "opaque-session",
        "W2_VERIFY_PATIENT_ID": _PATIENT_UUID,
        "W2_VERIFY_ENCOUNTER_ID": _ENCOUNTER_UUID,
        "W2_VERIFY_SYNTHETIC_ONLY_ACK": "synthetic-patient-and-documents",
    }
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                "PASS: deployed W2 write path verified "
                "(2 documents, 2 source Binaries, 2 artifact Binaries, "
                "20 grounded citations)\n"
            ),
            stderr="",
        )

    assert VerifyScript(run_command=run).run(verification_env) is True
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command == [
        activation_module.sys.executable,
        str(
            activation_module.Path(activation_module.__file__)
            .resolve()
            .with_name("verify_w2_write_path.py")
        ),
    ]
    assert kwargs["env"] == verification_env
    assert kwargs["cwd"] == activation_module.Path(
        activation_module.__file__
    ).resolve().parents[1]
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert kwargs["timeout"] == 900
    assert kwargs["check"] is False
    assert kwargs["start_new_session"] is True
    assert "20 grounded citations" in capsys.readouterr().out


def test_verify_script_never_forwards_child_failure_output(capsys) -> None:
    leaked = "owner-secret-must-not-render"
    verification_env = {
        "W2_VERIFY_AGENT_BASE_URL": "https://agent.example",
        "W2_VERIFY_SESSION_ID": "opaque-session",
        "W2_VERIFY_PATIENT_ID": _PATIENT_UUID,
        "W2_VERIFY_ENCOUNTER_ID": _ENCOUNTER_UUID,
        "W2_VERIFY_SYNTHETIC_ONLY_ACK": "synthetic-patient-and-documents",
    }

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, stdout="", stderr=leaked)

    with pytest.raises(ActivationError, match="synthetic deployed write-path"):
        VerifyScript(run_command=run).run(verification_env)

    captured = capsys.readouterr()
    assert leaked not in captured.out
    assert leaked not in captured.err


def test_verify_script_surfaces_only_allowlisted_content_free_failure() -> None:
    verification_env = {
        "W2_VERIFY_AGENT_BASE_URL": "https://agent.example",
        "W2_VERIFY_SESSION_ID": "opaque-session",
        "W2_VERIFY_PATIENT_ID": _PATIENT_UUID,
        "W2_VERIFY_ENCOUNTER_ID": _ENCOUNTER_UUID,
        "W2_VERIFY_SYNTHETIC_ONLY_ACK": "synthetic-patient-and-documents",
    }

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr="FAIL: document runtime is not active and ready\n",
        )

    with pytest.raises(
        ActivationError, match="document runtime is not active and ready"
    ):
        VerifyScript(run_command=run).run(verification_env)


def test_smart_browser_failure_reports_only_stage_and_exception_type() -> None:
    session = SeleniumSmartSession(ActivationConfig.from_env(_ENV))
    location = session._browser_location(
        "https://agent.example/callback?code=must-not-render&state=also-secret"
    )
    error = SeleniumSmartSession._browser_failure(
        "synthetic patient selection",
        RuntimeError("must-not-render"),
        location,
        "http=400,category=token-exchange-http-400",
    )

    rendered = str(error)
    assert "synthetic patient selection" in rendered
    assert "RuntimeError" in rendered
    assert "agent:/callback" in rendered
    assert "token-exchange-http-400" in rendered
    assert "must-not-render" not in rendered
    assert "also-secret" not in rendered

    category = SeleniumSmartSession._browser_error_category(
        '{"detail":"could not complete the launch: token exchange failed (HTTP 400)",'
        '"ignored":"must-not-render"}'
    )
    assert category == "token-exchange-http-400"


class _ConsentDriver:
    def __init__(self, result: str) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.default_content_calls = 0
        self.switch_to = SimpleNamespace(default_content=self._default_content)

    def _default_content(self) -> None:
        self.default_content_calls += 1

    def execute_cdp_cmd(self, command: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((command, params))
        return {"result": {"value": self.result}}


class _ConsentElement:
    def __init__(self, *, disabled: str | None = None) -> None:
        self.disabled = disabled
        self.clicks = 0

    def get_dom_attribute(self, name: str) -> str | None:
        assert name == "disabled"
        return self.disabled

    def click(self) -> None:
        self.clicks += 1


class _PreparedConsentDriver:
    def __init__(self, *, buttons: list[_ConsentElement], marker_count: int = 1) -> None:
        self.buttons = buttons
        self.marker_count = marker_count
        self.default_content_calls = 0
        self.switch_to = SimpleNamespace(default_content=self._default_content)

    def _default_content(self) -> None:
        self.default_content_calls += 1

    def find_elements(self, by: str, value: str) -> list[_ConsentElement]:
        if (by, value) == ("id", "authorize-btn"):
            return self.buttons
        if (by, value) == (
            "css selector",
            'form[data-w2-exact-scope-consent="1"]',
        ):
            return [_ConsentElement()] * self.marker_count
        return []


class _AuthorizationWindowSwitch:
    def __init__(self, driver: "_AuthorizationWindowDriver") -> None:
        self.driver = driver

    def window(self, handle: str) -> None:
        self.driver.current_handle = handle
        self.driver.window_switches.append(handle)

    def default_content(self) -> None:
        if self.driver.current_handle in self.driver.detached_handles:
            raise RuntimeError("synthetic detached frame")
        self.driver.default_content_calls += 1


class _AuthorizationWindowDriver:
    def __init__(
        self,
        urls: dict[str, str],
        *,
        consent_handles: set[str],
        detached_handles: set[str] = frozenset(),
    ) -> None:
        self.window_handles = list(urls)
        self.urls = urls
        self.consent_handles = consent_handles
        self.detached_handles = detached_handles
        self.current_handle = self.window_handles[0]
        self.window_switches: list[str] = []
        self.default_content_calls = 0
        self.switch_to = _AuthorizationWindowSwitch(self)

    @property
    def current_url(self) -> str:
        return self.urls[self.current_handle]

    def find_elements(self, by: str, value: str) -> list[_ConsentElement]:
        if (
            (by, value) == ("id", "authorize-btn")
            and self.current_handle in self.consent_handles
        ):
            return [_ConsentElement()]
        return []


def test_smart_consent_attests_native_mixed_versions_without_scope_injection() -> None:
    driver = _ConsentDriver("prepared")

    SeleniumSmartSession._prepare_exact_scope_consent(driver)

    assert driver.default_content_calls == 0
    assert len(driver.calls) == 1
    command, params = driver.calls[0]
    assert command == "Runtime.evaluate"
    assert params["returnByValue"] is True
    expression = params["expression"]
    assert json.dumps(sorted(REQUIRED_SMART_SCOPES)) in expression
    assert ".resource-version-actions" in expression
    assert "JSON.parse" in expression
    assert "w2ExactScopeConsent" in expression
    assert "arguments[0]" not in expression
    assert "button.click()" not in expression
    assert "form.submit()" not in expression
    assert "removeAttribute('name')" not in expression
    assert "document.createElement('input')" not in expression
    assert "input.name =" not in expression
    assert "form.appendChild(input)" not in expression
    assert "form.insertBefore" not in expression
    assert "dataW2ObservationRs" not in expression


def test_smart_consent_reacquires_button_after_context_selection() -> None:
    button = _ConsentElement()
    driver = _PreparedConsentDriver(buttons=[button])

    SeleniumSmartSession._submit_prepared_scope_consent(driver)

    assert driver.default_content_calls == 0
    assert button.clicks == 1


def test_smart_consent_selects_one_live_oauth_window_before_dom_attestation() -> None:
    driver = _AuthorizationWindowDriver(
        {
            "detached": "https://openemr.example/oauth2/default/device/code",
            "consent": "https://openemr.example/oauth2/default/device/code",
            "agent": "https://agent.example/app",
        },
        consent_handles={"consent"},
        detached_handles={"detached"},
    )
    session = SeleniumSmartSession(ActivationConfig.from_env(_ENV))

    session._select_unique_consent_context(driver)

    assert driver.current_handle == "consent"
    assert driver.window_switches[-1] == "consent"
    assert driver.default_content_calls >= 1


def test_smart_consent_stops_when_authorization_window_is_not_unique() -> None:
    driver = _AuthorizationWindowDriver(
        {
            "consent-a": "https://openemr.example/oauth2/default/device/code",
            "consent-b": "https://openemr.example/oauth2/default/device/code",
        },
        consent_handles={"consent-a", "consent-b"},
    )
    session = SeleniumSmartSession(ActivationConfig.from_env(_ENV))

    with pytest.raises(ActivationError, match="authorization window"):
        session._select_unique_consent_context(driver)


class _SyntheticDetachedFrame(Exception):
    pass


def test_smart_consent_recovers_only_read_only_dom_attestation_from_detach(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = SeleniumSmartSession(ActivationConfig.from_env(_ENV))
    driver = SimpleNamespace(
        current_url="https://openemr.example/oauth2/default/device/code"
    )
    selections: list[str] = []
    attempts = 0

    monkeypatch.setattr(
        session,
        "_select_unique_consent_context",
        lambda _driver: selections.append("selected"),
    )

    def prepare(_driver: object) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise _SyntheticDetachedFrame()

    monkeypatch.setattr(session, "_prepare_exact_scope_consent", prepare)

    session._prepare_consent_with_frame_recovery(driver, _SyntheticDetachedFrame)

    assert attempts == 3
    assert selections == ["selected", "selected", "selected"]


def test_smart_consent_frame_recovery_is_bounded_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = SeleniumSmartSession(ActivationConfig.from_env(_ENV))
    driver = SimpleNamespace(
        current_url="https://openemr.example/oauth2/default/device/code"
    )
    attempts = 0

    monkeypatch.setattr(session, "_select_unique_consent_context", lambda _driver: None)

    def prepare(_driver: object) -> None:
        nonlocal attempts
        attempts += 1
        raise _SyntheticDetachedFrame()

    monkeypatch.setattr(session, "_prepare_exact_scope_consent", prepare)

    with pytest.raises(_SyntheticDetachedFrame):
        session._prepare_consent_with_frame_recovery(
            driver, _SyntheticDetachedFrame
        )

    assert attempts == 3


@pytest.mark.parametrize(
    ("buttons", "marker_count"),
    [
        ([], 1),
        ([_ConsentElement(), _ConsentElement()], 1),
        ([_ConsentElement()], 0),
        ([_ConsentElement(disabled="disabled")], 1),
    ],
)
def test_smart_consent_does_not_submit_without_unique_button_and_attestation_marker(
    buttons: list[_ConsentElement], marker_count: int
) -> None:
    driver = _PreparedConsentDriver(buttons=buttons, marker_count=marker_count)

    with pytest.raises(ActivationError, match="prepared SMART consent submission"):
        SeleniumSmartSession._submit_prepared_scope_consent(driver)

    assert all(button.clicks == 0 for button in buttons)


@pytest.mark.parametrize(
    "result",
    [
        "consent_not_ready",
        "scope_metadata_missing",
        "scope_metadata_invalid",
        "non_resource_scope_mismatch",
        "resource_scope_mismatch",
    ],
)
def test_smart_consent_stops_fail_closed_when_exact_submission_is_not_attested(
    result: str,
) -> None:
    driver = _ConsentDriver(result)

    with pytest.raises(ActivationError, match="exact SMART consent preparation"):
        SeleniumSmartSession._prepare_exact_scope_consent(driver)


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
            and event[2]["W2_GRAPH_ENABLED"] == "0"
            for event in preceding
        )
        assert ("require_web_disabled", "agent") in preceding
        assert ("require_stopped", "document-worker") in preceding
        assert any(
            event[0] == "set_variables"
            and event[1] == "agent"
            and event[2]["W2_DOCUMENT_RUNTIME_ENABLED"] == "false"
            and event[2]["W2_GRAPH_ENABLED"] == "0"
            for event in preceding
        )


def test_activation_flips_worker_then_web_last_and_invokes_opaque_verifier() -> None:
    events, _config, _railway, verifier, orchestrator = _components()
    orchestrator.run()

    discover = events.index(("discover_openemr_attestation",))
    imported = next(
        index
        for index, event in enumerate(events)
        if event[0] == "import_route_attestations"
    )
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
        < imported
        < worker_true
        < worker_running
        < web_true
        < web_ready
        < session
        < verify
    )
    pre_import = events[:imported]
    assert ("deploy", "agent") in pre_import
    assert pre_import[-1] == ("require_web_disabled", "agent")
    assert not any(
        event[0] == "set_variables"
        and event[2].get("W2_DOCUMENT_RUNTIME_ENABLED") == "true"
        for event in pre_import
    )

    category_sets = [
        variables
        for _kind, _service, variables in _variable_events(events)
        if "SOURCE_DOCUMENT_CATEGORY_ID" in variables
    ]
    assert category_sets
    assert all(
        variables["W2_GRAPH_ENABLED"] == "1"
        and variables["SOURCE_DOCUMENT_CATEGORY_ID"] == "101"
        and variables["ARTIFACT_DOCUMENT_CATEGORY_ID"] == "202"
        and LEGACY_ROUTE_VARIABLE_NAMES.isdisjoint(variables)
        and variables["OPENEMR_BINARY_READBACK_SAFE"] == "true"
        for variables in category_sets
    )

    removal_services = {
        event[1] for event in events if event[0] == "remove_variables"
    }
    assert removal_services == {"agent", "document-worker"}
    import_event = events[imported]
    assert import_event[1] == "agent"
    assert import_event[2]["patient_count"] == 2
    assert import_event[2]["encounter_count"] == 2
    assert len(import_event[2]["snapshot_hash"]) == 64

    assert len(verifier.calls) == 1
    verification_env = verifier.calls[0]
    assert verification_env == {
        "W2_VERIFY_AGENT_BASE_URL": "https://agent.example",
        "W2_VERIFY_SESSION_ID": "opaque-session-must-never-print",
        "W2_VERIFY_PATIENT_ID": _PATIENT_UUID,
        "W2_VERIFY_ENCOUNTER_ID": _ENCOUNTER_UUID,
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
    assert last_by_service["document-worker"]["W2_GRAPH_ENABLED"] == "0"
    assert last_by_service["agent"]["W2_GRAPH_ENABLED"] == "0"


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
    assert last_by_service["document-worker"]["W2_GRAPH_ENABLED"] == "0"
    assert last_by_service["agent"]["W2_GRAPH_ENABLED"] == "0"

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
