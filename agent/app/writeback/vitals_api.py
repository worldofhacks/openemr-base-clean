"""Concrete encounter-pinned OpenEMR vital backend.

The standard vital row and its FHIR ``Observation?category=vital-signs`` projection must
both reproduce the permanent intent fingerprint before verification succeeds
(W2-D1/D9/D10; §2/§3/§5).
"""

from __future__ import annotations

import hmac
from typing import Mapping

from app.writeback.gateway import OpenEMRVitalGateway
from app.writeback.intents import RemoteMatch
from app.writeback.rest_client import OpenEMRWriteError, strip_caller_attribution
from app.writeback.transports import VitalWritePayload

_MIN_HASH_PREFIX = 12


def _note_values(note: str) -> Mapping[str, str]:
    values: dict[str, str] = {}
    for part in note.split(";"):
        key, separator, value = part.partition(":")
        if separator and key and value:
            values[key] = value
    return values


def _hash_token_matches(token: str | None, payload_hash: str) -> bool:
    if token is None:
        return False
    if hmac.compare_digest(token, payload_hash):
        return True
    if len(token) < _MIN_HASH_PREFIX or len(token) > len(payload_hash):
        return False
    return hmac.compare_digest(token, payload_hash[: len(token)])


def _note_matches(note: str, *, marker: str, payload_hash: str | None = None) -> bool:
    values = _note_values(note)
    if not hmac.compare_digest(values.get("copilot-intent", ""), marker):
        return False
    if payload_hash is None:
        return True
    return _hash_token_matches(values.get("payload"), payload_hash)


class OpenEMRVitalBackend:
    """Adapt injected live gateway operations to ``VitalBackend``."""

    def __init__(self, gateway: OpenEMRVitalGateway, *, encounter_id: str) -> None:
        if not encounter_id:
            raise ValueError("vital backend requires an encounter id")
        self._gateway = gateway
        self._encounter_id = encounter_id

    async def find_vitals(
        self, *, patient_id: str, marker: str, payload_hash: str
    ) -> list[RemoteMatch]:
        records = await self._gateway.list_vitals(
            patient_id=patient_id, encounter_id=self._encounter_id
        )
        return [
            RemoteMatch(record.remote_id, record.payload_hash)
            for record in records
            if hmac.compare_digest(record.payload_hash, payload_hash)
            and _note_matches(record.note, marker=marker, payload_hash=payload_hash)
        ]

    async def create_vital(
        self,
        *,
        patient_id: str,
        marker: str,
        payload: VitalWritePayload,
    ) -> str | None:
        if payload.encounter_id != self._encounter_id:
            raise OpenEMRWriteError("vital encounter differs from configured encounter")
        clean = strip_caller_attribution(payload.values)
        note = clean.get("note")
        if not isinstance(note, str) or not _note_matches(note, marker=marker):
            raise OpenEMRWriteError(
                "vital note does not contain the correlation marker"
            )
        return await self._gateway.create_vital(
            patient_id=patient_id,
            encounter_id=self._encounter_id,
            payload=clean,
        )

    async def verify_vital(
        self, *, patient_id: str, remote_id: str, payload_hash: str
    ) -> bool:
        readback = await self._gateway.read_vital(
            patient_id=patient_id,
            encounter_id=self._encounter_id,
            remote_id=remote_id,
        )
        if readback is None or not hmac.compare_digest(readback.remote_id, remote_id):
            return False
        note = _note_values(readback.note)
        marker = note.get("copilot-intent")
        if not marker or not _hash_token_matches(note.get("payload"), payload_hash):
            return False
        return (
            hmac.compare_digest(readback.standard_payload_hash, payload_hash)
            and readback.fhir_payload_hash is not None
            and hmac.compare_digest(readback.fhir_payload_hash, payload_hash)
        )
