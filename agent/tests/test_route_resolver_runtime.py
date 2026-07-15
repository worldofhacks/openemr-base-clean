"""Per-patient route resolution at web and worker boundaries (W2-D9/D10; §3)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from app.session.store import Session
from app.writeback.rest_client import DelegatedPrincipal


PATIENT_A = "11111111-1111-4111-8111-111111111111"
PATIENT_B = "22222222-2222-4222-8222-222222222222"
ENCOUNTER_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
ENCOUNTER_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def _settings():
    return SimpleNamespace(
        openemr_rest_base_url="https://openemr.test/apis/default",
        source_document_path="/AI-Source-Documents",
        source_document_category_id="17",
        source_document_category_acl="patients|docs",
        artifact_document_path="/AI-Extractions",
        artifact_document_category_id="27",
        artifact_document_category_acl="patients|docs",
    )


def _session(patient_id: str, encounter_id: str | None = None) -> Session:
    now = datetime.now(timezone.utc)
    return Session(
        session_id=f"session-{patient_id}",
        clinician_sub="Practitioner/synthetic",
        patient_id=patient_id,
        created_at=now,
        last_activity_at=now,
        token_expires_at=now + timedelta(hours=1),
        idle_timeout_s=1800,
        turn_cap=20,
        encounter_id=encounter_id,
    )


class _Credentials:
    async def reference_for_session(self, session: Session) -> str:
        return f"credential:{session.patient_id}"

    async def principal_for(self, credential_ref: str, *, expected_patient_id: str):
        assert credential_ref.endswith(expected_patient_id)
        return DelegatedPrincipal(
            clinician_sub="Practitioner/synthetic",
            patient_id=expected_patient_id,
            access_token=SecretStr("synthetic-token"),
        )


class _Resolver:
    async def resolve_patient(self, patient_uuid: str, *, generation_id=None):
        from app.writeback.route_attestations import PatientRouteBinding

        routes = {PATIENT_A: "731", PATIENT_B: "845"}
        return PatientRouteBinding(
            patient_uuid=patient_uuid,
            legacy_patient_id=routes[patient_uuid],
            generation_id=generation_id or "a" * 64,
        )

    async def resolve_encounter(
        self, patient_uuid: str, encounter_uuid: str, *, generation_id=None
    ):
        from app.writeback.route_attestations import (
            EncounterRouteBinding,
            RouteAttestationNotFound,
        )

        routes = {
            (PATIENT_A, ENCOUNTER_A): "912",
            (PATIENT_B, ENCOUNTER_B): "1044",
        }
        legacy_id = routes.get((patient_uuid, encounter_uuid))
        if legacy_id is None:
            raise RouteAttestationNotFound("encounter route unavailable")
        return EncounterRouteBinding(
            encounter_uuid=encounter_uuid,
            legacy_encounter_id=legacy_id,
            patient_uuid=patient_uuid,
            generation_id=generation_id or "a" * 64,
        )


@pytest.mark.asyncio
async def test_gateway_factory_resolves_distinct_patient_routes_without_global_bleed():
    from app.ingestion.runtime import _GatewayFactory

    factory = _GatewayFactory(_settings(), _Credentials(), _Resolver())

    _, _, gateway_a = await factory.for_session(
        _session(PATIENT_A, ENCOUNTER_A), encounter_id=ENCOUNTER_A
    )
    _, _, gateway_b = await factory.for_session(
        _session(PATIENT_B, ENCOUNTER_B), encounter_id=ENCOUNTER_B
    )

    assert gateway_a._legacy_routes.patient_uuid == PATIENT_A
    assert gateway_a._legacy_routes.patient_id == "731"
    assert gateway_a._legacy_routes.encounter_id == "912"
    assert gateway_b._legacy_routes.patient_uuid == PATIENT_B
    assert gateway_b._legacy_routes.patient_id == "845"
    assert gateway_b._legacy_routes.encounter_id == "1044"
    assert gateway_a._legacy_routes != gateway_b._legacy_routes


@pytest.mark.asyncio
async def test_gateway_factory_refuses_cross_patient_encounter_before_openemr_io():
    from app.ingestion.runtime import _GatewayFactory
    from app.writeback.live_gateway import EncounterRouteMismatch

    factory = _GatewayFactory(_settings(), _Credentials(), _Resolver())

    with pytest.raises(EncounterRouteMismatch):
        await factory.for_session(
            _session(PATIENT_A, ENCOUNTER_B), encounter_id=ENCOUNTER_B
        )


@pytest.mark.asyncio
async def test_gateway_factory_allows_patient_route_without_guessing_encounter():
    from app.ingestion.runtime import _GatewayFactory

    factory = _GatewayFactory(_settings(), _Credentials(), _Resolver())

    _, _, gateway = await factory.for_session(
        _session(PATIENT_A), encounter_id=None
    )

    assert gateway._legacy_routes.patient_id == "731"
    assert gateway._legacy_routes.encounter_uuid is None
    assert gateway._legacy_routes.encounter_id is None
