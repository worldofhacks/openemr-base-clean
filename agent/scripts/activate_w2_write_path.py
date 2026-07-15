#!/usr/bin/env python3
"""Idempotently activate and verify the deployed Week 2 document write path.

The owner performs only the trust-boundary actions that automation must not perform:
register/enable the SMART client, create categories/grant ACLs, and place the two
owner-managed secrets on both Railway services.  This script discovers and attests the
remaining state, creates/configures/deploys the worker, enables worker then web, creates
an opaque patient-pinned SMART session, and runs the synthetic live verifier.

No command reads Railway variable values.  In particular, this module never reads,
accepts as arguments, or prints SMART_CLIENT_SECRET or DOCUMENT_CREDENTIAL_KEY.  Their
presence/correctness is proven indirectly by successful enabled process startup and the
full write/readback verification.

W2-D1/D3/D6/D9/D10; W2_ARCHITECTURE §3/§5.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, urlsplit

import httpx


OWNER_ONLY_SECRET_NAMES = frozenset({"SMART_CLIENT_SECRET", "DOCUMENT_CREDENTIAL_KEY"})

DEFAULT_PROJECT_ID = "1bddbc72-6307-4ec9-b6dd-8184310fbdcf"
DEFAULT_ENVIRONMENT = "production"
DEFAULT_AGENT_BASE_URL = "https://agent-production-9f62.up.railway.app"
DEFAULT_OPENEMR_BASE_URL = "https://openemr-production-cc95.up.railway.app"
DEFAULT_SYNTHETIC_PATIENT_ID = "a234b786-539a-4f9a-96a0-432293226f02"
DEFAULT_SMART_CLIENT_NAME = "AgentForge Week 2 Write Client"
SYNTHETIC_ACK = "synthetic-patient-and-documents"

REQUIRED_SMART_SCOPES = frozenset(
    {
        "openid",
        "offline_access",
        "launch",
        "launch/patient",
        "api:oemr",
        "user/Patient.read",
        "user/Condition.read",
        "user/MedicationRequest.read",
        "user/AllergyIntolerance.read",
        "user/Observation.read",
        "user/Encounter.read",
        "user/document.crs",
        "user/DocumentReference.rs",
        "user/Binary.read",
        "user/vital.crus",
        "user/Observation.rs",
    }
)
REQUIRED_GRANT_TYPES = frozenset({"authorization_code", "refresh_token"})

_OPAQUE_SESSION_RE = re.compile(r"[A-Za-z0-9_-]+")
_SAFE_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9_.:-]+")
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class ActivationError(RuntimeError):
    """A content-free activation failure that is safe to show in a terminal."""


def _env(values: Mapping[str, str], name: str, default: str = "") -> str:
    value = values.get(name, default)
    return str(value or "").strip()


def _https_origin(value: str, name: str) -> str:
    candidate = value.rstrip("/")
    try:
        parsed = urlsplit(candidate)
        parsed.port
    except ValueError:
        raise ActivationError(f"{name} must be a valid HTTPS origin") from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ActivationError(f"{name} must be a valid HTTPS origin")
    return candidate


def _service_url(value: str, name: str) -> str:
    candidate = value.rstrip("/")
    try:
        parsed = urlsplit(candidate)
        parsed.port
    except ValueError:
        raise ActivationError(f"{name} must be a valid service URL") from None
    loopback_http = parsed.scheme == "http" and parsed.hostname in _LOOPBACK_HOSTS
    if (
        (parsed.scheme != "https" and not loopback_http)
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ActivationError(
            f"{name} must use HTTPS (HTTP is allowed only on loopback)"
        )
    return candidate


def _require_safe_identifier(value: str, name: str) -> str:
    if not value or _SAFE_IDENTIFIER_RE.fullmatch(value) is None:
        raise ActivationError(f"{name} contains unsupported characters")
    return value


@dataclass(frozen=True)
class ActivationConfig:
    project_id: str
    environment: str
    web_service: str
    worker_service: str
    mysql_service: str
    agent_base_url: str
    openemr_base_url: str
    openemr_fhir_base_url: str
    openemr_oauth_base_url: str
    openemr_rest_base_url: str
    callback_url: str
    smart_client_name: str
    smart_client_id: str = ""
    patient_id: str = DEFAULT_SYNTHETIC_PATIENT_ID
    encounter_id: str = ""
    selenium_url: str = "http://localhost:4444/wd/hub"
    oe_username: str = "admin"
    oe_password: str = field(default="", repr=False)
    deploy_timeout_seconds: float = 600.0
    ready_timeout_seconds: float = 180.0
    agent_root: Path = field(
        default_factory=lambda: Path(__file__).resolve().parents[1]
    )

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "ActivationConfig":
        values = os.environ if environ is None else environ
        # Deliberately do not access either OWNER_ONLY_SECRET_NAMES entry here.
        project_id = _require_safe_identifier(
            _env(values, "W2_ACTIVATE_RAILWAY_PROJECT_ID", DEFAULT_PROJECT_ID),
            "W2_ACTIVATE_RAILWAY_PROJECT_ID",
        )
        environment = _require_safe_identifier(
            _env(values, "W2_ACTIVATE_RAILWAY_ENVIRONMENT", DEFAULT_ENVIRONMENT),
            "W2_ACTIVATE_RAILWAY_ENVIRONMENT",
        )
        web_service = _require_safe_identifier(
            _env(values, "W2_ACTIVATE_WEB_SERVICE", "agent"),
            "W2_ACTIVATE_WEB_SERVICE",
        )
        worker_service = _require_safe_identifier(
            _env(values, "W2_ACTIVATE_WORKER_SERVICE", "document-worker"),
            "W2_ACTIVATE_WORKER_SERVICE",
        )
        mysql_service = _require_safe_identifier(
            _env(values, "W2_ACTIVATE_MYSQL_SERVICE", "MySQL"),
            "W2_ACTIVATE_MYSQL_SERVICE",
        )

        agent_base = _https_origin(
            _env(values, "W2_VERIFY_AGENT_BASE_URL", DEFAULT_AGENT_BASE_URL),
            "W2_VERIFY_AGENT_BASE_URL",
        )
        openemr_base = _https_origin(
            _env(values, "W2_ACTIVATE_OPENEMR_BASE_URL", DEFAULT_OPENEMR_BASE_URL),
            "W2_ACTIVATE_OPENEMR_BASE_URL",
        )
        fhir = _env(
            values, "OPENEMR_FHIR_BASE_URL", f"{openemr_base}/apis/default/fhir"
        ).rstrip("/")
        oauth = _env(
            values, "OPENEMR_OAUTH_BASE_URL", f"{openemr_base}/oauth2/default"
        ).rstrip("/")
        rest = _env(
            values, "OPENEMR_REST_BASE_URL", f"{openemr_base}/apis/default"
        ).rstrip("/")
        callback = _env(values, "AGENT_CALLBACK_URL", f"{agent_base}/callback")
        for url, name in (
            (fhir, "OPENEMR_FHIR_BASE_URL"),
            (oauth, "OPENEMR_OAUTH_BASE_URL"),
            (rest, "OPENEMR_REST_BASE_URL"),
            (callback, "AGENT_CALLBACK_URL"),
        ):
            _https_origin(url, name)
        if urlsplit(callback).path != "/callback":
            raise ActivationError(
                "AGENT_CALLBACK_URL must end at the exact /callback path"
            )
        exact_urls = {
            "OPENEMR_FHIR_BASE_URL": (fhir, f"{openemr_base}/apis/default/fhir"),
            "OPENEMR_OAUTH_BASE_URL": (oauth, f"{openemr_base}/oauth2/default"),
            "OPENEMR_REST_BASE_URL": (rest, f"{openemr_base}/apis/default"),
            "AGENT_CALLBACK_URL": (callback, f"{agent_base}/callback"),
        }
        for name, (actual, expected) in exact_urls.items():
            if actual != expected:
                raise ActivationError(
                    f"{name} does not match the pinned deployed origin"
                )

        return cls(
            project_id=project_id,
            environment=environment,
            web_service=web_service,
            worker_service=worker_service,
            mysql_service=mysql_service,
            agent_base_url=agent_base,
            openemr_base_url=openemr_base,
            openemr_fhir_base_url=fhir,
            openemr_oauth_base_url=oauth,
            openemr_rest_base_url=rest,
            callback_url=callback,
            smart_client_name=DEFAULT_SMART_CLIENT_NAME,
            # Optional overrides are compared with discovered live state.  They are
            # non-secret, but the normal path needs neither copied by hand.
            smart_client_id=_env(values, "SMART_CLIENT_ID"),
            patient_id=_env(
                values, "W2_VERIFY_PATIENT_ID", DEFAULT_SYNTHETIC_PATIENT_ID
            ),
            encounter_id=_env(values, "W2_VERIFY_ENCOUNTER_ID"),
            selenium_url=_env(values, "SELENIUM_URL", "http://localhost:4444/wd/hub"),
            oe_username=_env(values, "OE_USERNAME", "admin"),
            oe_password=_env(values, "OE_ADMIN_PASS"),
        )


@dataclass(frozen=True)
class OpenEMRAttestation:
    source_category_id: str
    artifact_category_id: str
    system_error_logging: str
    client_id: str = ""
    encounter_id: str = ""

    @classmethod
    def from_rows(
        cls,
        category_rows: Sequence[Mapping[str, object]],
        *,
        system_error_logging: str,
        client_id: str = "",
        encounter_id: str = "",
    ) -> "OpenEMRAttestation":
        if system_error_logging != "WARNING":
            raise ActivationError("OpenEMR system_error_logging is not exactly WARNING")
        if len(category_rows) != 2:
            raise ActivationError(
                "OpenEMR must contain exactly the two required root document categories"
            )
        expected = {
            "AI-Source-Documents": "/AI-Source-Documents",
            "AI-Extractions": "/AI-Extractions",
        }
        indexed: dict[str, Mapping[str, object]] = {}
        for row in category_rows:
            name = str(row.get("name") or "")
            if name not in expected or name in indexed:
                raise ActivationError(
                    "OpenEMR category names are missing, duplicated, or unexpected"
                )
            if str(row.get("path") or "") != expected[name]:
                raise ActivationError("OpenEMR document category path is not canonical")
            if str(row.get("aco_spec") or "") != "patients|docs":
                raise ActivationError(
                    "OpenEMR document category ACL is not patients|docs"
                )
            category_id = str(row.get("id") or "").strip()
            if not category_id.isdigit() or int(category_id) <= 0:
                raise ActivationError("OpenEMR document category ID is invalid")
            indexed[name] = row
        ids = {str(row["id"]) for row in indexed.values()}
        if len(ids) != 2:
            raise ActivationError("OpenEMR document category IDs are not distinct")
        return cls(
            source_category_id=str(indexed["AI-Source-Documents"]["id"]),
            artifact_category_id=str(indexed["AI-Extractions"]["id"]),
            system_error_logging=system_error_logging,
            client_id=client_id,
            encounter_id=encounter_id,
        )


class RailwayControl(Protocol):
    def ensure_worker_service(self, service: str) -> None: ...
    def set_variables(self, service: str, variables: Mapping[str, str]) -> None: ...
    def deploy(self, service: str) -> None: ...
    def require_service_running(self, service: str) -> None: ...
    def require_web_ready(self, service: str) -> None: ...
    def require_web_disabled(self, service: str) -> None: ...
    def stop_service(self, service: str) -> None: ...
    def require_service_stopped(self, service: str) -> None: ...


class OpenEMRInspector(Protocol):
    def discover_attestation(self) -> OpenEMRAttestation: ...


class SmartSessionFactory(Protocol):
    def establish_session(
        self, *, patient_id: str, encounter_id: str
    ) -> Mapping[str, str]: ...


class LiveVerifier(Protocol):
    def run(self, environ: Mapping[str, str]) -> object: ...


class ActivationOrchestrator:
    """Pin the only safe activation order and roll both services closed on failure."""

    def __init__(
        self,
        config: ActivationConfig,
        *,
        railway: RailwayControl,
        openemr: OpenEMRInspector,
        smart: SmartSessionFactory,
        verifier: LiveVerifier,
    ) -> None:
        self._config = config
        self._railway = railway
        self._openemr = openemr
        self._smart = smart
        self._verifier = verifier

    def run(self) -> object:
        config = self._config
        self._railway.ensure_worker_service(config.worker_service)
        try:
            # First action on every pass is an explicit fail-closed pin. Because Railway
            # variable updates intentionally skip deploys, prove the currently running
            # revisions are already closed; otherwise deploy/scale a verified baseline.
            self._set_enabled(
                config.worker_service, False, variables=self._base_variables()
            )
            self._set_enabled(
                config.web_service, False, variables=self._base_variables()
            )
            self._require_or_establish_disabled_baseline()

            attestation = self._openemr.discover_attestation()
            client_id = self._resolved_client_id(attestation)
            encounter_id = self._resolved_encounter_id(attestation)
            variables = self._attested_variables(attestation, client_id=client_id)
            self._set_enabled(config.worker_service, False, variables=variables)
            self._set_enabled(config.web_service, False, variables=variables)

            # Worker must boot and remain alive before the request-serving process can
            # advertise document_runtime=ready.
            self._set_enabled(config.worker_service, True, variables=variables)
            self._railway.deploy(config.worker_service)
            self._railway.require_service_running(config.worker_service)

            # This is the last configuration flip.  A fresh SMART session is minted only
            # after the deploy because the web token cache is intentionally in-process.
            self._set_enabled(config.web_service, True, variables=variables)
            self._railway.deploy(config.web_service)
            self._railway.require_web_ready(config.web_service)

            session = dict(
                self._smart.establish_session(
                    patient_id=config.patient_id, encounter_id=encounter_id
                )
            )
            verification_env = self._verification_environment(
                session, encounter_id=encounter_id
            )
            result = self._verifier.run(verification_env)
            self._railway.require_web_ready(config.web_service)
            return result
        except Exception as exc:
            # Never leave a partially activated pair, and never claim fail-closed unless
            # both the disabled web revision and stopped worker are observable.
            try:
                self._rollback_disabled()
            except Exception:
                raise ActivationError(
                    "activation failed and rollback could not be verified"
                ) from None
            if isinstance(exc, ActivationError):
                raise
            raise ActivationError(
                f"activation stopped at {type(exc).__name__}; both services were pinned disabled"
            ) from None

    def _require_or_establish_disabled_baseline(self) -> None:
        try:
            self._railway.require_web_disabled(self._config.web_service)
            self._railway.require_service_stopped(self._config.worker_service)
        except Exception:
            self._rollback_disabled()

    def _base_variables(self) -> dict[str, str]:
        config = self._config
        return {
            "OPENEMR_FHIR_BASE_URL": config.openemr_fhir_base_url,
            "OPENEMR_OAUTH_BASE_URL": config.openemr_oauth_base_url,
            "OPENEMR_REST_BASE_URL": config.openemr_rest_base_url,
            "AGENT_CALLBACK_URL": config.callback_url,
            "SOURCE_DOCUMENT_PATH": "/AI-Source-Documents",
            "SOURCE_DOCUMENT_CATEGORY_ACL": "patients|docs",
            "ARTIFACT_DOCUMENT_PATH": "/AI-Extractions",
            "ARTIFACT_DOCUMENT_CATEGORY_ACL": "patients|docs",
            "OPENEMR_BINARY_READBACK_SAFE": "false",
            "DOCUMENT_WORKER_ID": "document-worker-production",
            "DOCUMENT_WORKER_POLL_SECONDS": "1.0",
            "DOCUMENT_WORKER_LEASE_SECONDS": "60",
            "DOCUMENT_WORKER_MAX_ATTEMPTS": "3",
            "DOCUMENT_WORKER_BASE_BACKOFF_SECONDS": "5",
            "RERANKER": "local",
            "LANGFUSE_LOG_CONTENT": "false",
        }

    def _attested_variables(
        self, attestation: OpenEMRAttestation, *, client_id: str
    ) -> dict[str, str]:
        variables = self._base_variables()
        variables.update(
            {
                "SMART_CLIENT_ID": client_id,
                "SOURCE_DOCUMENT_CATEGORY_ID": attestation.source_category_id,
                "ARTIFACT_DOCUMENT_CATEGORY_ID": attestation.artifact_category_id,
                "OPENEMR_BINARY_READBACK_SAFE": "true",
            }
        )
        return variables

    def _set_enabled(
        self, service: str, enabled: bool, *, variables: Mapping[str, str]
    ) -> None:
        values = dict(variables)
        if service == self._config.worker_service:
            # Railway references copy no value through this process; the platform resolves
            # them inside the project.  The two owner-only secrets are intentionally absent.
            values.update(
                {
                    "ANTHROPIC_API_KEY": "${{agent.ANTHROPIC_API_KEY}}",
                    "SESSION_STORE_DSN": "${{agent.SESSION_STORE_DSN}}",
                    "LANGFUSE_HOST": "${{agent.LANGFUSE_HOST}}",
                    "LANGFUSE_PUBLIC_KEY": "${{agent.LANGFUSE_PUBLIC_KEY}}",
                    "LANGFUSE_SECRET_KEY": "${{agent.LANGFUSE_SECRET_KEY}}",
                }
            )
        if OWNER_ONLY_SECRET_NAMES.intersection(values):
            raise ActivationError(
                "automation attempted to cross an owner-only secret boundary"
            )
        values["W2_DOCUMENT_RUNTIME_ENABLED"] = "true" if enabled else "false"
        self._railway.set_variables(service, values)

    def _resolved_client_id(self, attestation: OpenEMRAttestation) -> str:
        discovered = attestation.client_id.strip()
        configured = self._config.smart_client_id.strip()
        if configured and discovered and configured != discovered:
            raise ActivationError(
                "configured SMART client ID differs from live registration"
            )
        client_id = discovered or configured
        if not client_id:
            raise ActivationError("enabled replacement SMART client was not discovered")
        return client_id

    def _resolved_encounter_id(self, attestation: OpenEMRAttestation) -> str:
        discovered = attestation.encounter_id.strip()
        configured = self._config.encounter_id.strip()
        if configured and discovered and configured != discovered:
            raise ActivationError(
                "configured synthetic encounter differs from live discovery"
            )
        encounter_id = discovered or configured
        if not encounter_id:
            raise ActivationError(
                "no encounter was found for the canonical synthetic patient"
            )
        return encounter_id

    def _verification_environment(
        self, session: Mapping[str, str], *, encounter_id: str
    ) -> dict[str, str]:
        session_id = str(session.get("W2_VERIFY_SESSION_ID") or "")
        patient_id = str(session.get("W2_VERIFY_PATIENT_ID") or "")
        returned_encounter = str(session.get("W2_VERIFY_ENCOUNTER_ID") or "")
        if _OPAQUE_SESSION_RE.fullmatch(session_id) is None:
            raise ActivationError("SMART launch did not return a valid opaque session")
        if patient_id != self._config.patient_id:
            raise ActivationError(
                "SMART session patient does not match synthetic patient"
            )
        if returned_encounter != encounter_id:
            raise ActivationError(
                "SMART session encounter does not match synthetic encounter"
            )
        return {
            "W2_VERIFY_AGENT_BASE_URL": self._config.agent_base_url,
            "W2_VERIFY_SESSION_ID": session_id,
            "W2_VERIFY_PATIENT_ID": patient_id,
            "W2_VERIFY_ENCOUNTER_ID": encounter_id,
            "W2_VERIFY_SYNTHETIC_ONLY_ACK": SYNTHETIC_ACK,
        }

    def _rollback_disabled(self) -> None:
        variables = self._base_variables()
        failures: list[Exception] = []

        # Stage both services closed before changing either live revision. Continue every
        # rollback action even after an error so a web failure cannot leave the worker up
        # (or vice versa), then report that the safe state could not be proven.
        for service in (self._config.web_service, self._config.worker_service):
            try:
                self._set_enabled(service, False, variables=variables)
            except Exception as exc:
                failures.append(exc)
        # A redundant deploy/scale command may fail even when the live process is already
        # closed. The observable end-state is authoritative: command errors are safe only
        # when the independent checks below still prove web disabled and worker stopped.
        for action in (
            lambda: self._railway.deploy(self._config.web_service),
            lambda: self._railway.stop_service(self._config.worker_service),
        ):
            try:
                action()
            except Exception:
                pass
        for check in (
            lambda: self._railway.require_web_disabled(self._config.web_service),
            lambda: self._railway.require_service_stopped(self._config.worker_service),
        ):
            try:
                check()
            except Exception as exc:
                failures.append(exc)
        if failures:
            raise ActivationError("rollback could not be verified")


class RailwayCLI:
    """Narrow Railway CLI adapter that never invokes a variable-read command."""

    def __init__(self, config: ActivationConfig) -> None:
        self._config = config

    def ensure_authenticated(self) -> None:
        self._run(["railway", "whoami"], label="Railway authentication", discard=True)

    def ensure_worker_service(self, service: str) -> None:
        self.ensure_authenticated()
        payload = self._run_json(
            [
                "railway",
                "service",
                "list",
                "--project",
                self._config.project_id,
                "--environment",
                self._config.environment,
                "--json",
            ],
            label="Railway service discovery",
        )
        matches = [
            item
            for item in payload
            if isinstance(item, dict) and item.get("name") == service
        ]
        if len(matches) > 1:
            raise ActivationError(
                "multiple Railway worker services have the requested name"
            )
        if matches:
            return
        with tempfile.TemporaryDirectory(prefix="w2-railway-link-") as directory:
            self._run(
                [
                    "railway",
                    "link",
                    "--project",
                    self._config.project_id,
                    "--environment",
                    self._config.environment,
                    "--json",
                ],
                label="Railway project link",
                cwd=Path(directory),
                discard=True,
            )
            self._run(
                ["railway", "add", "--service", service, "--json"],
                label="Railway worker creation",
                cwd=Path(directory),
                discard=True,
            )

    def set_variables(self, service: str, variables: Mapping[str, str]) -> None:
        if OWNER_ONLY_SECRET_NAMES.intersection(variables):
            raise ActivationError(
                "owner-only secret variable crossed the automation boundary"
            )
        assignments = [f"{name}={variables[name]}" for name in sorted(variables)]
        self._run(
            [
                "railway",
                "variable",
                "set",
                "--project",
                self._config.project_id,
                "--environment",
                self._config.environment,
                "--service",
                service,
                "--skip-deploys",
                "--json",
                *assignments,
            ],
            label=f"Railway non-secret configuration for {service}",
            discard=True,
        )

    def deploy(self, service: str) -> None:
        previous_deployments = {
            str(item.get("id"))
            for item in self._deployment_history(service)
            if item.get("id")
        }
        if service == self._config.worker_service:
            with self._worker_context() as context:
                self._upload(service, context)
        else:
            self._upload(service, self._config.agent_root)
        self._wait_for_success(service, previous_deployments=previous_deployments)

    def require_service_running(self, service: str) -> None:
        deadline = time.monotonic() + self._config.ready_timeout_seconds
        while time.monotonic() < deadline:
            payload = self._service_metadata(service)
            replicas = payload.get("replicas") if isinstance(payload, dict) else None
            if (
                payload.get("status") == "SUCCESS"
                and isinstance(replicas, dict)
                and int(replicas.get("running") or 0) >= 1
                and int(replicas.get("crashed") or 0) == 0
            ):
                return
            time.sleep(3)
        raise ActivationError(f"{service} process did not remain running")

    def require_web_ready(self, service: str) -> None:
        self._require_web_runtime_detail(service, "ready")

    def require_web_disabled(self, service: str) -> None:
        self._require_web_runtime_detail(service, "disabled")

    def stop_service(self, service: str) -> None:
        metadata = self._service_metadata(service)
        if not metadata.get("deploymentId"):
            return
        regions = metadata.get("regions")
        names = {
            str(item.get("name") or "")
            for item in regions or []
            if isinstance(item, dict) and item.get("name")
        }
        if not names:
            raise ActivationError(f"{service} active regions were not discoverable")
        # Railway CLI 5.x requires a linked directory for service scaling even when
        # project/environment flags are supplied. Keep that local state ephemeral.
        with self._linked_context(service) as context:
            self._run(
                [
                    "railway",
                    "service",
                    "scale",
                    "--environment",
                    self._config.environment,
                    "--service",
                    service,
                    "--json",
                    *(f"{name}=0" for name in sorted(names)),
                ],
                label=f"Railway stop for {service}",
                cwd=context,
                discard=True,
            )

    def require_service_stopped(self, service: str) -> None:
        deadline = time.monotonic() + self._config.ready_timeout_seconds
        while time.monotonic() < deadline:
            payload = self._service_metadata(service)
            if not payload.get("deploymentId"):
                return
            replicas = payload.get("replicas")
            if payload.get("deploymentStopped") is True and (
                not isinstance(replicas, dict) or int(replicas.get("running") or 0) == 0
            ):
                return
            if (
                isinstance(replicas, dict)
                and int(replicas.get("configured") or 0) == 0
                and int(replicas.get("running") or 0) == 0
            ):
                return
            time.sleep(3)
        raise ActivationError(f"{service} stop could not be verified")

    def _linked_context(self, service: str):
        cli = self

        class _Context:
            def __init__(self) -> None:
                self.temporary: tempfile.TemporaryDirectory[str] | None = None

            def __enter__(self) -> Path:
                self.temporary = tempfile.TemporaryDirectory(prefix="w2-railway-link-")
                directory = Path(self.temporary.name)
                cli._run(
                    [
                        "railway",
                        "link",
                        "--project",
                        cli._config.project_id,
                        "--environment",
                        cli._config.environment,
                        "--service",
                        service,
                        "--json",
                    ],
                    label=f"Railway link for {service}",
                    cwd=directory,
                    discard=True,
                )
                return directory

            def __exit__(self, *_args: object) -> None:
                assert self.temporary is not None
                self.temporary.cleanup()

        return _Context()

    def _require_web_runtime_detail(self, service: str, detail: str) -> None:
        del service  # URL is intentionally pinned rather than inferred from CLI output.
        deadline = time.monotonic() + self._config.ready_timeout_seconds
        while time.monotonic() < deadline:
            try:
                response = httpx.get(
                    f"{self._config.agent_base_url}/ready",
                    timeout=15.0,
                    follow_redirects=False,
                    headers={"User-Agent": "openemr-w2-activator/1"},
                )
                body = response.json() if response.status_code == 200 else {}
            except (httpx.HTTPError, ValueError):
                body = {}
            checks = body.get("checks") if isinstance(body, dict) else None
            runtime = next(
                (
                    item
                    for item in checks or []
                    if isinstance(item, dict) and item.get("name") == "document_runtime"
                ),
                None,
            )
            if (
                body.get("status") == "ready"
                and isinstance(runtime, dict)
                and runtime.get("ok") is True
                and runtime.get("kind") == "hard"
                and runtime.get("detail") == detail
            ):
                return
            time.sleep(3)
        raise ActivationError(
            f"web readiness did not become green with document_runtime {detail}"
        )

    def ssh_keys_available(self) -> bool:
        output = self._run(
            ["railway", "ssh", "keys", "list"],
            label="Railway SSH-key discovery",
        )
        return "No SSH keys registered" not in output

    def ssh_mysql(self, remote_script: str) -> str:
        return self._run(
            [
                "railway",
                "ssh",
                "--project",
                self._config.project_id,
                "--environment",
                self._config.environment,
                "--service",
                self._config.mysql_service,
                # Railway joins trailing command arguments without preserving the
                # argv boundary required by ``sh -lc``. Send one shell-quoted remote
                # command or only the first word reaches the login shell.
                f"sh -lc {shlex.quote(remote_script)}",
            ],
            label="read-only OpenEMR attestation",
        )

    def _upload(self, service: str, directory: Path) -> None:
        self._run(
            [
                "railway",
                "up",
                "--project",
                self._config.project_id,
                "--environment",
                self._config.environment,
                "--service",
                service,
                "--detach",
                "--yes",
                "--json",
                "--path-as-root",
                ".",
            ],
            label=f"Railway deployment upload for {service}",
            cwd=directory,
            discard=True,
        )

    def _wait_for_success(
        self, service: str, *, previous_deployments: set[str]
    ) -> None:
        deadline = time.monotonic() + self._config.deploy_timeout_seconds
        deployment_id = ""
        while time.monotonic() < deadline:
            history = self._deployment_history(service)
            indexed = {str(item.get("id")): item for item in history if item.get("id")}
            if not deployment_id:
                new_ids = set(indexed).difference(previous_deployments)
                if len(new_ids) > 1:
                    raise ActivationError(
                        f"{service} deployment was ambiguous with a concurrent upload"
                    )
                if len(new_ids) == 1:
                    deployment_id = new_ids.pop()
            status = str(indexed.get(deployment_id, {}).get("status") or "")
            if deployment_id and status == "SUCCESS":
                return
            if deployment_id and status in {
                "FAILED",
                "CRASHED",
                "REMOVED",
                "REMOVING",
            }:
                raise ActivationError(f"{service} deployment did not become successful")
            time.sleep(5)
        raise ActivationError(
            f"{service} deployment did not become successful before timeout"
        )

    def _deployment_history(self, service: str) -> list[dict[str, Any]]:
        return self._run_json(
            [
                "railway",
                "deployment",
                "list",
                "--project",
                self._config.project_id,
                "--environment",
                self._config.environment,
                "--service",
                service,
                "--limit",
                "20",
                "--json",
            ],
            label=f"Railway deployment history for {service}",
        )

    def _service_metadata(self, service: str) -> dict[str, Any]:
        payload = self._run_json(
            [
                "railway",
                "service",
                "list",
                "--project",
                self._config.project_id,
                "--environment",
                self._config.environment,
                "--json",
            ],
            label="Railway deployment status",
        )
        matches = [
            item
            for item in payload
            if isinstance(item, dict) and item.get("name") == service
        ]
        if len(matches) != 1:
            raise ActivationError(
                f"Railway service {service} was not uniquely available"
            )
        return matches[0]

    def _worker_context(self):
        class _Context:
            def __init__(self, agent_root: Path) -> None:
                self.agent_root = agent_root
                self.temporary: tempfile.TemporaryDirectory[str] | None = None

            def __enter__(self) -> Path:
                self.temporary = tempfile.TemporaryDirectory(prefix="w2-worker-deploy-")
                target = Path(self.temporary.name) / "agent"
                shutil.copytree(
                    self.agent_root,
                    target,
                    ignore=shutil.ignore_patterns(
                        ".venv",
                        ".pytest_cache",
                        ".ruff_cache",
                        "__pycache__",
                        "*.pyc",
                        "tests",
                        "bruno",
                        "evals",
                        "load",
                    ),
                )
                shutil.copy2(target / "railway.worker.json", target / "railway.json")
                return target

            def __exit__(self, *_args: object) -> None:
                assert self.temporary is not None
                self.temporary.cleanup()

        return _Context(self._config.agent_root)

    def _run_json(self, command: list[str], *, label: str) -> list[dict[str, Any]]:
        output = self._run(command, label=label)
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            raise ActivationError(f"{label} returned invalid metadata") from None
        if not isinstance(payload, list):
            raise ActivationError(f"{label} returned an invalid metadata contract")
        return payload

    def _run(
        self,
        command: list[str],
        *,
        label: str,
        cwd: Path | None = None,
        discard: bool = False,
    ) -> str:
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                text=True,
                capture_output=True,
                check=False,
                timeout=max(self._config.deploy_timeout_seconds, 60.0),
            )
        except (OSError, subprocess.TimeoutExpired):
            raise ActivationError(f"{label} could not be executed") from None
        if result.returncode != 0:
            # Never include CLI output: an upstream tool may unexpectedly render values.
            raise ActivationError(f"{label} failed (exit {result.returncode})")
        return "" if discard else result.stdout


class RailwayOpenEMRInspectorImpl:
    """Read exact non-secret OpenEMR state through Railway's authenticated SSH tunnel."""

    def __init__(self, config: ActivationConfig, railway: RailwayCLI) -> None:
        self._config = config
        self._railway = railway

    def discover_attestation(self) -> OpenEMRAttestation:
        try:
            patient = str(uuid.UUID(self._config.patient_id))
        except ValueError:
            raise ActivationError(
                "synthetic patient ID must be a canonical UUID"
            ) from None
        if patient != self._config.patient_id:
            raise ActivationError(
                "synthetic patient ID must use canonical lowercase UUID form"
            )
        if not self._railway.ssh_keys_available():
            raise ActivationError(
                "Railway has no registered SSH key for read-only OpenEMR discovery; run "
                "`railway ssh keys add`, then rerun this command"
            )
        output = self._railway.ssh_mysql(self._remote_script(patient))
        return self._parse_output(output)

    def _parse_output(self, output: str) -> OpenEMRAttestation:
        categories: list[dict[str, str]] = []
        logging_values: list[str] = []
        clients: list[list[str]] = []
        encounters: list[str] = []
        for line in output.splitlines():
            fields = line.split("\t")
            if not fields:
                continue
            if fields[0] == "CATEGORY" and len(fields) == 4:
                categories.append(
                    {
                        "name": fields[1],
                        "path": f"/{fields[1]}",
                        "id": fields[2],
                        "aco_spec": fields[3],
                    }
                )
            elif fields[0] == "LOGGING" and len(fields) == 2:
                logging_values.append(fields[1])
            elif fields[0] == "CLIENT" and len(fields) == 9:
                clients.append(fields[1:])
            elif fields[0] == "ENCOUNTER" and len(fields) == 2:
                encounters.append(fields[1])
        if len(logging_values) != 1:
            raise ActivationError(
                "OpenEMR logging attestation was not uniquely available"
            )
        if len(clients) != 1:
            raise ActivationError(
                "replacement SMART client was missing or not uniquely registered"
            )
        if len(encounters) != 1:
            raise ActivationError(
                "canonical synthetic patient has no uniquely selected latest encounter"
            )
        client_id = self._validate_client(clients[0])
        encounter = self._canonical_uuid(encounters[0], "synthetic encounter")
        return OpenEMRAttestation.from_rows(
            categories,
            system_error_logging=logging_values[0],
            client_id=client_id,
            encounter_id=encounter,
        )

    def _validate_client(self, values: list[str]) -> str:
        (
            client_id,
            enabled,
            confidential,
            has_secret,
            redirect_uri,
            grant_types,
            scopes,
            skip_ehr_launch,
        ) = values
        if enabled != "1" or confidential != "1" or has_secret != "1":
            raise ActivationError(
                "replacement SMART client is not enabled, private, and secret-backed"
            )
        if skip_ehr_launch != "0":
            raise ActivationError(
                "replacement SMART client bypasses EHR launch authorization"
            )
        if set(redirect_uri.split("|")) != {self._config.callback_url}:
            raise ActivationError("replacement SMART client redirect URI is not exact")
        grants = {item for item in re.split(r"[|,\s]+", grant_types) if item}
        if grants != REQUIRED_GRANT_TYPES:
            raise ActivationError("replacement SMART client grant types are not exact")
        if set(scopes.split()) != REQUIRED_SMART_SCOPES:
            raise ActivationError(
                "replacement SMART client scopes are not the exact 16"
            )
        if not client_id or any(character.isspace() for character in client_id):
            raise ActivationError("replacement SMART client ID is invalid")
        return client_id

    @staticmethod
    def _canonical_uuid(value: str, label: str) -> str:
        try:
            canonical = str(uuid.UUID(value))
        except ValueError:
            raise ActivationError(f"{label} ID is not a UUID") from None
        if canonical != value:
            raise ActivationError(f"{label} ID is not canonical")
        return canonical

    def _remote_script(self, patient_id: str) -> str:
        # The command references database credentials only by environment-variable name.
        # The selected output is restricted to public IDs, ACL state, and booleans.
        base_sql = """
SELECT 'CATEGORY', c.name, c.id, c.aco_spec
FROM categories AS c
JOIN categories AS root ON root.id=c.parent
WHERE root.id=1 AND root.name='Categories'
  AND BINARY c.name IN ('AI-Source-Documents','AI-Extractions')
ORDER BY c.name;
SELECT 'LOGGING', gl_value
FROM globals WHERE gl_name='system_error_logging' AND gl_index=0;
""".strip()
        client_sql = f"""
SELECT 'CLIENT', client_id, CAST(is_enabled AS CHAR), CAST(is_confidential AS CHAR),
       IF(client_secret IS NOT NULL AND client_secret<>'', '1', '0'), redirect_uri,
       grant_types, scope, CAST(skip_ehr_launch_authorization_flow AS CHAR)
FROM oauth_clients
WHERE BINARY client_name='{DEFAULT_SMART_CLIENT_NAME}' AND revoke_date IS NULL;
""".strip()
        encounter_sql = f"""
SELECT 'ENCOUNTER', LOWER(CONCAT(
       SUBSTR(HEX(fe.uuid),1,8),'-',SUBSTR(HEX(fe.uuid),9,4),'-',
       SUBSTR(HEX(fe.uuid),13,4),'-',SUBSTR(HEX(fe.uuid),17,4),'-',
       SUBSTR(HEX(fe.uuid),21,12)))
FROM form_encounter AS fe
JOIN patient_data AS pd ON pd.pid=fe.pid
WHERE HEX(pd.uuid)=REPLACE(UPPER('{patient_id}'),'-','') AND fe.uuid IS NOT NULL
ORDER BY fe.date DESC, fe.id DESC LIMIT 1;
""".strip()
        schema_sql = """
SELECT table_schema
FROM information_schema.tables
WHERE table_name IN ('categories','globals','oauth_clients','form_encounter','patient_data')
  AND table_schema NOT IN ('information_schema','mysql','performance_schema','sys')
GROUP BY table_schema
HAVING COUNT(DISTINCT table_name)=5
ORDER BY table_schema;
""".strip()
        return (
            "set -eu; "
            "DBUSER=${MYSQLUSER:-${MYSQL_USER:-root}}; "
            "DBPASS=${MYSQLPASSWORD:-${MYSQL_PASSWORD:-${MYSQL_ROOT_PASSWORD:-}}}; "
            'test -n "$DBPASS"; export MYSQL_PWD="$DBPASS"; '
            f'DB=$(mysql --batch --skip-column-names --raw -u "$DBUSER" -e {shlex.quote(schema_sql)}); '
            "DB_COUNT=$(printf '%s\\n' \"$DB\" | awk 'NF{n++} END{print n+0}'); "
            'test "$DB_COUNT" -eq 1; '
            f'mysql --batch --skip-column-names --raw -u "$DBUSER" "$DB" -e {shlex.quote(base_sql)}; '
            f'mysql --batch --skip-column-names --raw -u "$DBUSER" "$DB" -e {shlex.quote(client_sql)}; '
            f'mysql --batch --skip-column-names --raw -u "$DBUSER" "$DB" -e {shlex.quote(encounter_sql)}'
        )


class SeleniumSmartSession:
    """Drive exact-patient SMART launch while keeping all credentials/token data private."""

    def __init__(self, config: ActivationConfig) -> None:
        self._config = config

    def establish_session(
        self, *, patient_id: str, encounter_id: str
    ) -> Mapping[str, str]:
        stage = "configuration validation"
        if not self._config.oe_password:
            raise ActivationError(
                "OE_ADMIN_PASS is required in the process environment"
            )
        patient = self._canonical_uuid(patient_id, "synthetic patient")
        encounter = self._canonical_uuid(encounter_id, "synthetic encounter")
        selenium_url = _service_url(self._config.selenium_url, "SELENIUM_URL")
        self._ensure_loopback_selenium(selenium_url)
        try:
            from selenium import webdriver
            from selenium.common.exceptions import NoSuchFrameException
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.webdriver.support.ui import WebDriverWait
        except ImportError:
            raise ActivationError(
                'Selenium is missing; run `cd agent && python -m pip install -e ".[dev]"`'
            ) from None

        driver = None
        try:
            stage = "browser startup"
            driver = webdriver.Remote(command_executor=selenium_url, options=Options())
            driver.set_page_load_timeout(60)
            driver.execute_cdp_cmd("Network.enable", {})
            driver.execute_cdp_cmd(
                "Network.setBlockedURLs",
                {"urls": [f"{self._config.agent_base_url}/chat*"]},
            )
            stage = "agent launch redirect"
            driver.get(f"{self._config.agent_base_url}/launch")
            self._require_origin(
                driver.current_url, self._config.openemr_base_url, "login"
            )
            stage = "OpenEMR login form"
            wait = WebDriverWait(driver, 60)
            wait.until(EC.presence_of_element_located((By.NAME, "username"))).send_keys(
                self._config.oe_username
            )
            driver.find_element(By.NAME, "password").send_keys(self._config.oe_password)
            stage = "OpenEMR role selection"
            role_buttons = [
                button
                for button in driver.find_elements(
                    By.CSS_SELECTOR, "button[name='user_role']"
                )
                if "OpenEMR" in button.text
            ]
            if len(role_buttons) != 1:
                raise ActivationError("OpenEMR login role was not uniquely available")
            role_buttons[0].click()
            self._require_origin(
                driver.current_url, self._config.openemr_base_url, "patient selection"
            )
            stage = "synthetic patient selection"
            buttons = wait.until(
                EC.presence_of_all_elements_located(
                    (By.CSS_SELECTOR, "button.patient-btn")
                )
            )
            matches = [
                button
                for button in buttons
                if button.get_dom_attribute("data-patient-id") == patient
            ]
            if len(matches) != 1:
                raise ActivationError(
                    "exact canonical synthetic patient was not uniquely available"
                )
            matches[0].click()

            def session_from_callback(current: Any) -> str | bool:
                parsed = urlsplit(current.current_url)
                if parsed.path != "/app":
                    return False
                self._require_origin(
                    current.current_url, self._config.agent_base_url, "agent callback"
                )
                query = parse_qs(parsed.query, keep_blank_values=True)
                values = query.get("sid", [])
                if set(query) != {"sid"} or len(values) != 1:
                    raise ActivationError(
                        "agent callback did not return one opaque session"
                    )
                if _OPAQUE_SESSION_RE.fullmatch(values[0]) is None:
                    raise ActivationError("agent callback session was not opaque")
                return values[0]

            stage = "SMART authorization"
            next_step = wait.until(
                lambda current: (
                    session_from_callback(current)
                    or EC.element_to_be_clickable((By.ID, "authorize-btn"))(current)
                )
            )
            if isinstance(next_step, str):
                session_id = next_step
            else:
                stage = "SMART consent preparation"
                self._prepare_consent_with_frame_recovery(
                    driver, NoSuchFrameException
                )
                stage = "SMART consent submission"
                self._submit_prepared_scope_consent(driver)
                stage = "SMART callback"
                session_id = wait.until(session_from_callback)
            return {
                "W2_VERIFY_SESSION_ID": str(session_id),
                "W2_VERIFY_PATIENT_ID": patient,
                "W2_VERIFY_ENCOUNTER_ID": encounter,
            }
        except ActivationError:
            raise
        except Exception as exc:
            location = self._browser_location(
                driver.current_url if driver is not None else ""
            )
            diagnostic = self._browser_diagnostic(driver)
            raise self._browser_failure(stage, exc, location, diagnostic) from None
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass

    def _select_unique_consent_context(self, driver: Any) -> None:
        """Reset a detached frame by selecting one live top-level OAuth window."""

        candidates: list[str] = []
        try:
            handles = list(driver.window_handles)
        except Exception:
            handles = []
        for handle in handles:
            try:
                driver.switch_to.window(handle)
                driver.switch_to.default_content()
                if self._browser_location(driver.current_url) != "openemr:oauth":
                    continue
                if len(driver.find_elements("id", "authorize-btn")) == 1:
                    candidates.append(handle)
            except Exception:
                continue
        if len(candidates) != 1:
            raise ActivationError(
                "SMART authorization window was not uniquely available"
            )
        try:
            driver.switch_to.window(candidates[0])
            driver.switch_to.default_content()
        except Exception:
            raise ActivationError(
                "SMART authorization window was not available at submission"
            ) from None

    def _prepare_consent_with_frame_recovery(
        self, driver: Any, frame_error: type[Exception]
    ) -> None:
        """Retry only idempotent DOM attestation across a detached-frame reload."""

        for attempt in range(3):
            self._select_unique_consent_context(driver)
            self._require_origin(
                driver.current_url, self._config.openemr_base_url, "authorization"
            )
            try:
                self._prepare_exact_scope_consent(driver)
                return
            except frame_error:
                if attempt == 2:
                    raise

    @staticmethod
    def _prepare_exact_scope_consent(driver: Any) -> None:
        """Patch only OpenEMR's known mixed V1/V2 Observation UI collision.

        OpenEMR's server still validates every submitted scope against both the
        original authorization request and the registered client.  The browser
        helper first reconstructs the page's prospective grant and requires that
        its *only* gap from the frozen 16-scope manifest is ``Observation.rs``
        collapsed into the preceding V1 ``Observation.read`` card.  It then adds
        that one requested scope outside the container OpenEMR rebuilds on click;
        the normal authorize button, CSRF check, and server validation remain in
        control of the submission.
        """

        manifest = json.dumps(sorted(REQUIRED_SMART_SCOPES))
        expression = r"""
            ((manifest) => {
            const expected = new Set(manifest);
            const form = document.getElementById('userLogin');
            const button = document.getElementById('authorize-btn');
            const dynamic = document.getElementById('dynamic-scopes-container');
            if (!form || !button || !dynamic || button.disabled) {
                return 'consent_not_ready';
            }

            const prospective = new Set();
            document.querySelectorAll('input.app-scope').forEach((input) => {
                if (input.type === 'hidden' || input.checked) {
                    prospective.add(input.value);
                }
            });

            const actionOrder = ['c', 'r', 'u', 'd', 's'];
            document.querySelectorAll('.resource-context').forEach((contextInput) => {
                const resource = contextInput.dataset.resource;
                const context = contextInput.value;
                const versionInput = document.querySelector(
                    `.resource-version[data-resource="${resource}"]`
                );
                const version = versionInput ? versionInput.value : 'v2';
                const actions = Array.from(document.querySelectorAll(
                    `.action-checkbox[data-resource="${resource}"]:checked`
                )).map((input) => input.dataset.action);
                if (actions.length === 0) return;

                if (version === 'v1') {
                    if (actions.includes('r') || actions.includes('s')) {
                        prospective.add(`${context}/${resource}.read`);
                    }
                    if (actions.includes('c') || actions.includes('u') || actions.includes('d')) {
                        prospective.add(`${context}/${resource}.write`);
                    }
                    return;
                }

                actions.sort((left, right) =>
                    actionOrder.indexOf(left) - actionOrder.indexOf(right)
                );
                const suffix = actions.join('');
                const master = document.querySelector(
                    `.resource-master-checkbox[data-resource="${resource}"]`
                );
                const masterChecked = master ? master.checked : true;
                const unrestricted = master && master.dataset.unrestricted === '1';
                const restrictions = Array.from(document.querySelectorAll(
                    `.restriction-checkbox[data-resource="${resource}"]:checked`
                )).map((input) => input.dataset.restriction);
                if (!masterChecked || !unrestricted) {
                    restrictions.forEach((restriction) => prospective.add(
                        `${context}/${resource}.${suffix}?category=${restriction}`
                    ));
                } else {
                    prospective.add(`${context}/${resource}.${suffix}`);
                }
            });

            const target = 'user/Observation.rs';
            const missing = Array.from(expected).filter((scope) => !prospective.has(scope));
            const unexpected = Array.from(prospective).filter((scope) => !expected.has(scope));
            const nonResource = (scope) => !/^(patient|user|system)\//.test(scope);
            if (missing.some(nonResource) || unexpected.some(nonResource)) {
                return 'non_resource_scope_mismatch';
            }
            if (missing.length !== 1 || missing[0] !== target || unexpected.length !== 0) {
                return 'resource_scope_mismatch';
            }

            const observationContext = document.querySelector(
                '.resource-context[data-resource="Observation"]'
            );
            const observationVersion = document.querySelector(
                '.resource-version[data-resource="Observation"]'
            );
            const observationMaster = document.querySelector(
                '.resource-master-checkbox[data-resource="Observation"]'
            );
            const observationActions = Array.from(document.querySelectorAll(
                '.action-checkbox[data-resource="Observation"]:checked'
            )).map((input) => input.dataset.action).sort();
            if (
                !observationContext || observationContext.value !== 'user' ||
                !observationVersion || observationVersion.value !== 'v1' ||
                !observationMaster || !observationMaster.checked ||
                observationActions.join(',') !== 'r,s' ||
                !prospective.has('user/Observation.read') ||
                !prospective.has('api:oemr')
            ) {
                return 'collision_not_exact';
            }

            const existing = form.querySelectorAll('[data-w2-observation-rs="1"]');
            if (existing.length > 1) return 'collision_not_exact';
            if (existing.length === 0) {
                const input = document.createElement('input');
                input.type = 'hidden';
                input.name = 'scope[user/Observation.rs]';
                input.value = target;
                input.dataset.w2ObservationRs = '1';
                form.insertBefore(input, dynamic);
            } else if (
                existing[0].name !== 'scope[user/Observation.rs]' ||
                existing[0].value !== target || existing[0].type !== 'hidden'
            ) {
                return 'collision_not_exact';
            }
            prospective.add(target);
            if (
                prospective.size !== expected.size ||
                Array.from(expected).some((scope) => !prospective.has(scope))
            ) {
                return 'collision_not_exact';
            }
            return 'prepared';
            })
            """ + f"({manifest})"
        response = driver.execute_cdp_cmd(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True},
        )
        try:
            result = response["result"]["value"]
        except (KeyError, TypeError):
            result = "runtime_evaluation_failed"
        if result != "prepared":
            raise ActivationError(
                "exact SMART consent preparation failed closed "
                f"({result if isinstance(result, str) else 'unknown'})"
            )

    @staticmethod
    def _submit_prepared_scope_consent(driver: Any) -> None:
        """Reacquire the consent button from a valid top-level browser context."""

        markers = driver.find_elements(
            "css selector", 'input[data-w2-observation-rs="1"]'
        )
        buttons = driver.find_elements("id", "authorize-btn")
        if (
            len(markers) != 1
            or len(buttons) != 1
            or buttons[0].get_dom_attribute("disabled") is not None
        ):
            raise ActivationError(
                "prepared SMART consent submission was not uniquely available"
            )
        buttons[0].click()

    def _ensure_loopback_selenium(self, selenium_url: str) -> None:
        parsed = urlsplit(selenium_url)
        if parsed.hostname not in _LOOPBACK_HOSTS:
            return
        try:
            response = httpx.get(
                selenium_url.rsplit("/wd/hub", 1)[0] + "/status", timeout=2
            )
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        compose = (
            self._config.agent_root.parent
            / "docker/development-easy/docker-compose.yml"
        )
        if not compose.is_file():
            raise ActivationError(
                "local Selenium is unavailable and compose file is missing"
            )
        try:
            result = subprocess.run(
                [
                    "docker",
                    "compose",
                    "-f",
                    str(compose),
                    "up",
                    "--detach",
                    "--wait",
                    "selenium",
                ],
                cwd=self._config.agent_root.parent,
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise ActivationError(
                "local Selenium is unavailable; run the documented docker compose command"
            ) from None
        if result.returncode != 0:
            raise ActivationError(
                "local Selenium could not start; run the documented docker compose command"
            )

    @staticmethod
    def _browser_failure(
        stage: str,
        exc: Exception,
        location: str = "unknown",
        diagnostic: str = "http=unknown,category=unclassified",
    ) -> ActivationError:
        return ActivationError(
            f"SMART browser session failed during {stage} "
            f"at {location} ({diagnostic}; {type(exc).__name__}); "
            "no credential detail retained"
        )

    @classmethod
    def _browser_diagnostic(cls, driver: Any | None) -> str:
        if driver is None:
            return "http=unknown,category=unclassified"
        status = "unknown"
        try:
            raw_status = driver.execute_script(
                "const n=performance.getEntriesByType('navigation');"
                "return n.length ? n[n.length-1].responseStatus : 0;"
            )
            parsed_status = int(raw_status)
            if 100 <= parsed_status <= 599:
                status = str(parsed_status)
        except Exception:
            pass
        try:
            category = cls._browser_error_category(str(driver.page_source or ""))
        except Exception:
            category = "unclassified"
        return f"http={status},category={category}"

    @staticmethod
    def _browser_error_category(page_source: str) -> str:
        lowered = page_source.lower()
        token_failure = re.search(r"token exchange failed \(http (\d{3})\)", lowered)
        if token_failure:
            return f"token-exchange-http-{token_failure.group(1)}"
        categories = (
            ("unknown or replayed oauth state", "oauth-state-rejected"),
            ("no launch/patient context", "patient-context-missing"),
            ("co-pilot oauth client is not enabled", "oauth-client-rejected"),
            ("missing code/state on callback", "callback-parameters-missing"),
            ("authorization failed", "authorization-rejected"),
            ("internal server error", "callback-internal-error"),
        )
        return next(
            (category for marker, category in categories if marker in lowered),
            "unclassified",
        )

    def _browser_location(self, actual: str) -> str:
        try:
            parsed = urlsplit(actual)
            actual_port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError:
            return "unknown"

        def matches(origin: str) -> bool:
            expected = urlsplit(origin)
            expected_port = expected.port or (443 if expected.scheme == "https" else 80)
            return (
                parsed.scheme == expected.scheme
                and parsed.hostname == expected.hostname
                and actual_port == expected_port
            )

        if matches(self._config.agent_base_url):
            path = (
                parsed.path
                if parsed.path in {"/launch", "/callback", "/app"}
                else "other"
            )
            return f"agent:{path}"
        if matches(self._config.openemr_base_url):
            if "/oauth2/" in parsed.path:
                return "openemr:oauth"
            if "login" in parsed.path.lower():
                return "openemr:login"
            return "openemr:other"
        return "unexpected-origin"

    @staticmethod
    def _canonical_uuid(value: str, label: str) -> str:
        try:
            canonical = str(uuid.UUID(value))
        except ValueError:
            raise ActivationError(f"{label} must be a canonical UUID") from None
        if value != canonical:
            raise ActivationError(f"{label} must use canonical lowercase UUID form")
        return canonical

    @staticmethod
    def _require_origin(actual: str, expected: str, stage: str) -> None:
        actual_parts = urlsplit(actual)
        expected_parts = urlsplit(expected)
        actual_port = actual_parts.port or (
            443 if actual_parts.scheme == "https" else 80
        )
        expected_port = expected_parts.port or (
            443 if expected_parts.scheme == "https" else 80
        )
        if (
            actual_parts.scheme != expected_parts.scheme
            or actual_parts.hostname != expected_parts.hostname
            or actual_port != expected_port
        ):
            raise ActivationError(f"SMART {stage} reached an unexpected origin")


class VerifyScript:
    def run(self, environ: Mapping[str, str]) -> object:
        module_name = (
            f"{__package__}.verify_w2_write_path"
            if __package__
            else "verify_w2_write_path"
        )
        verify_main = importlib.import_module(module_name).main

        if verify_main(environ=environ) != 0:
            raise ActivationError("synthetic deployed write-path verification failed")
        return True


def build_orchestrator(config: ActivationConfig) -> ActivationOrchestrator:
    railway = RailwayCLI(config)
    return ActivationOrchestrator(
        config,
        railway=railway,
        openemr=RailwayOpenEMRInspectorImpl(config, railway),
        smart=SeleniumSmartSession(config),
        verifier=VerifyScript(),
    )


def main(*, environ: Mapping[str, str] | None = None) -> int:
    try:
        config = ActivationConfig.from_env(environ)
        build_orchestrator(config).run()
    except ActivationError as exc:
        print(f"FAIL-CLOSED: {exc}", file=sys.stderr)
        return 1
    print("PASS: Week 2 write path activated and live synthetic verification completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
