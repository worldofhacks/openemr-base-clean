"""Live delegated OpenEMR standard-REST/FHIR gateway (W2-D1/D9/D10; §3/§5).

The gateway has no category discovery or creation capability.  It accepts only an OA3
attestation supplied by deployment wiring, uses append-only standard REST creates, and
performs document/vital verification through patient-pinned FHIR readback.  Tokens and
clinical response bodies are never logged.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Mapping, Sequence, cast
from urllib.parse import SplitResult, quote, urlsplit
from uuid import UUID

import httpx

from app.middleware.correlation import outbound_headers
from app.schemas.documents import FailureReason
from app.writeback.gateway import (
    CategoryRecord,
    DocumentRecord,
    VitalReadback,
    VitalRecord,
)
from app.writeback.preflight import CategoryMismatch
from app.writeback.rest_client import (
    DelegatedPrincipal,
    OpenEMRRestClient,
    OpenEMRWriteError,
    strip_caller_attribution,
)

_VITAL_FIELDS = (
    "bps",
    "bpd",
    "weight",
    "height",
    "temperature",
    "pulse",
    "respiration",
    "oxygen_saturation",
)
_VITAL_CODES = {
    "weight": "29463-7",
    "height": "8302-2",
    "temperature": "8310-5",
    "pulse": "8867-4",
    "respiration": "9279-1",
    "oxygen_saturation": "2708-6",
}
_BP_PANEL_CODE = "85354-9"
_BP_COMPONENT_CODES = {"bps": "8480-6", "bpd": "8462-4"}
_NOTE = re.compile(
    r"\Acopilot-intent:(?P<marker>[^;]+);payload:(?P<hash>[0-9a-fA-F]{12,64})\Z"
)
_BINARY_ID = re.compile(r"\A[A-Za-z0-9.-]+\Z")
_LEGACY_ID = re.compile(r"\A[1-9][0-9]*\Z")


@dataclass(frozen=True)
class CategoryAttestation:
    """One administrator-verified canonical path → ID/ACL binding."""

    path: str
    category_id: str
    writable: bool

    def __post_init__(self) -> None:
        if (
            not self.path.startswith("/")
            or ".." in self.path.split("/")
            or not self.category_id
        ):
            raise ValueError("invalid category attestation")

    def as_record(self) -> CategoryRecord:
        return CategoryRecord(self.path, self.category_id, self.writable)


@dataclass(frozen=True)
class LegacyRouteAttestation:
    """One resolved patient route and, when supplied, one owned encounter route."""

    patient_uuid: str
    patient_id: str
    encounter_uuid: str | None = None
    encounter_id: str | None = None

    def __post_init__(self) -> None:
        if (self.encounter_uuid is None) != (self.encounter_id is None):
            raise ValueError("encounter route UUID and ID must be supplied together")
        uuid_values = [("patient_uuid", self.patient_uuid)]
        if self.encounter_uuid is not None:
            uuid_values.append(("encounter_uuid", self.encounter_uuid))
        for name, value in uuid_values:
            try:
                canonical = str(UUID(value))
            except (ValueError, TypeError, AttributeError):
                raise ValueError(f"{name} must be a canonical UUID") from None
            if canonical != value:
                raise ValueError(f"{name} must be a canonical UUID")
        legacy_values = [("patient_id", self.patient_id)]
        if self.encounter_id is not None:
            legacy_values.append(("encounter_id", self.encounter_id))
        for name, value in legacy_values:
            if not isinstance(value, str) or _LEGACY_ID.fullmatch(value) is None:
                raise ValueError(f"{name} must be a positive canonical decimal")


@dataclass(frozen=True)
class BinaryReadGuard:
    """Explicit OA6 evidence; unknown or DEBUG is unsafe."""

    system_error_logging: str | None

    def require_safe(self) -> None:
        setting = (self.system_error_logging or "").strip()
        if not setting or setting.casefold() == "debug":
            raise BinaryReadbackUnsafe("FHIR Binary readback is not attested safe")


class LiveGatewayError(RuntimeError):
    """A read failed without exposing a response body or delegated token."""


class PatientRouteMismatch(LiveGatewayError):
    """The delegated patient lacks the exact attested UUID→pid route binding."""

    reason = FailureReason.PATIENT_MISMATCH


class EncounterRouteMismatch(LiveGatewayError):
    """The requested encounter lacks an attested route owned by this patient."""

    reason = FailureReason.ENCOUNTER_MISMATCH


class BinaryReadbackUnsafe(LiveGatewayError):
    """The deployment did not prove a non-DEBUG Binary logging posture."""

    reason = FailureReason.BINARY_READBACK_UNSAFE


@dataclass(frozen=True)
class _StandardVital:
    payload_hash: str
    field_id: str
    value: Decimal
    measured_at: datetime
    note: str


class OpenEMRLiveGateway:
    """Concrete implementation of both live OpenEMR gateway protocols."""

    def __init__(
        self,
        *,
        base_url: str,
        principal: DelegatedPrincipal,
        category_attestations: Sequence[CategoryAttestation],
        legacy_route_attestation: LegacyRouteAttestation,
        binary_guard: BinaryReadGuard,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 15.0,
    ) -> None:
        parts = urlsplit(base_url)
        if parts.scheme != "https" or not parts.netloc:
            raise ValueError("OpenEMR base URL must use https")
        attestations = {item.path: item for item in category_attestations}
        if len(attestations) != len(category_attestations):
            raise ValueError("duplicate category attestation path")
        self._base = base_url.rstrip("/")
        self._base_parts = urlsplit(self._base)
        self._principal = principal
        self._attestations = attestations
        self._legacy_routes = legacy_route_attestation
        self._binary_guard = binary_guard
        self._http = http_client
        self._timeout = timeout
        self._writer = OpenEMRRestClient(
            base_url=self._base,
            principal=principal,
            http_client=http_client,
            timeout=timeout,
        )
        self._document_names: dict[tuple[str, str], str] = {}

    async def resolve_document_categories(self, path: str) -> list[CategoryRecord]:
        attested = self._attestations.get(path)
        return [attested.as_record()] if attested is not None else []

    async def list_documents(
        self, *, patient_id: str, category_path: str
    ) -> list[DocumentRecord]:
        self._authorize_patient(patient_id)
        self._require_attestation(category_path, writable=False)
        document_patient_id = self._legacy_patient_id(patient_id)
        body = await self._get_json(
            f"{self._base}/api/patient/{quote(document_patient_id, safe='')}/document",
            params={"path": category_path},
            missing_ok=True,
        )
        # OpenEMR's legacy document controller maps a valid empty result to 404.
        # The patient UUID→pid binding and category attestation have already been
        # verified above, so this endpoint-specific 404 is safe to interpret as empty.
        if body is None:
            return []
        documents: list[DocumentRecord] = []
        for item in _records(body):
            remote_id = item.get("id")
            filename = item.get("filename") or item.get("name")
            if remote_id is None or not isinstance(filename, str) or not filename:
                continue
            record = DocumentRecord(str(remote_id), filename)
            documents.append(record)
            self._document_names[(patient_id, record.remote_id)] = filename
        return documents

    async def read_document_bytes(
        self, *, patient_id: str, remote_id: str
    ) -> bytes | None:
        self._authorize_patient(patient_id)
        self._binary_guard.require_safe()
        filename = self._document_names.get((patient_id, remote_id))
        if filename is None:
            return None
        patient_fhir_id = self._principal.patient_fhir_id or patient_id
        bundle = await self._get_json(
            f"{self._base}/fhir/DocumentReference",
            params={"patient": patient_fhir_id, "_count": "100"},
            fhir=True,
        )
        binary_ids = {
            binary_id
            for resource in _resources(bundle)
            if _reference_matches(resource.get("subject"), "Patient", patient_fhir_id)
            for binary_id in _binary_ids(resource, filename, self._base_parts)
        }
        if len(binary_ids) == 1:
            binary_id = next(iter(binary_ids))
        elif not binary_ids and _LEGACY_ID.fullmatch(remote_id) is not None:
            # This OpenEMR fork's regular DocumentReference patient search currently
            # maps the FHIR ``patient`` parameter to ``puuid`` but then looks for the
            # original key, yielding an empty Bundle. Its FHIR Binary controller
            # explicitly accepts either a document UUID or numeric document ID.
            #
            # The fallback does not accept an ambient/caller ID: ``filename`` proves
            # this exact numeric ID was first returned by the attested patient/category
            # standard list and cached under the delegated patient's UUID. Binary still
            # performs OpenEMR's independent user/category ACL check, and the non-DEBUG
            # guard above remains unbypassable.
            binary_id = remote_id
        else:
            return None
        response = await self._get_response(
            f"{self._base}/fhir/Binary/{quote(binary_id, safe='')}",
            fhir=True,
            missing_ok=True,
        )
        return response.content if response is not None else None

    async def create_document(
        self,
        *,
        patient_id: str,
        category_path: str,
        filename: str,
        content_type: str,
        content: bytes,
    ) -> str | None:
        self._authorize_patient(patient_id)
        self._require_attestation(category_path, writable=True)
        document_patient_id = self._legacy_patient_id(patient_id)
        return await self._writer.create_document(
            patient_id=patient_id,
            document_patient_id=document_patient_id,
            category_path=category_path,
            filename=filename,
            content_type=content_type,
            content=content,
        )

    async def list_vitals(
        self, *, patient_id: str, encounter_id: str
    ) -> list[VitalRecord]:
        self._authorize_patient(patient_id)
        body = await self._get_json(
            self._vital_url(patient_id, encounter_id),
            missing_ok=True,
        )
        # OpenEMR's standard vital collection route maps a valid empty result to 404.
        # Both legacy IDs were matched to the delegated UUIDs by ``_vital_url`` before
        # dispatch, so this endpoint-specific response is safe to reconcile as empty.
        if body is None:
            return []
        records: list[VitalRecord] = []
        for row in _records(body):
            remote_id = row.get("id")
            verified = _standard_vital(row)
            if remote_id is None or verified is None:
                continue
            records.append(
                VitalRecord(str(remote_id), verified.note, verified.payload_hash)
            )
        return records

    async def read_vital(
        self, *, patient_id: str, encounter_id: str, remote_id: str
    ) -> VitalReadback | None:
        self._authorize_patient(patient_id)
        body = await self._get_json(
            f"{self._vital_url(patient_id, encounter_id)}/{quote(remote_id, safe='')}",
            missing_ok=True,
        )
        if body is None or not isinstance(body, Mapping):
            return None
        row = cast(Mapping[str, object], body)
        if row.get("id") is not None and str(row["id"]) != remote_id:
            return None
        standard = _standard_vital(row)
        if standard is None:
            return None

        patient_fhir_id = self._principal.patient_fhir_id or patient_id
        # This OpenEMR fork's vitals search does not implement the FHIR
        # ``encounter`` parameter. Keep the server-side patient boundary and
        # require the exact encounter locally in ``_fhir_vital_matches``.
        bundle = await self._get_json(
            f"{self._base}/fhir/Observation",
            params={
                "patient": patient_fhir_id,
                "category": "vital-signs",
                "_count": "100",
            },
            fhir=True,
        )
        fhir_verified = _fhir_vital_matches(
            _resources(bundle),
            standard=standard,
            patient_id=patient_fhir_id,
            encounter_id=encounter_id,
        )
        return VitalReadback(
            remote_id=remote_id,
            note=standard.note,
            standard_payload_hash=standard.payload_hash,
            fhir_payload_hash=standard.payload_hash if fhir_verified else None,
        )

    async def create_vital(
        self,
        *,
        patient_id: str,
        encounter_id: str,
        payload: Mapping[str, object],
    ) -> str | None:
        self._authorize_patient(patient_id)
        clean = strip_caller_attribution(payload)
        unknown = set(clean) - {*_VITAL_FIELDS, "date", "note"}
        if unknown:
            raise OpenEMRWriteError("vital payload contains unsupported fields")
        note = clean.get("note")
        parsed = _parse_note(note)
        if parsed is None:
            raise OpenEMRWriteError("vital note marker is invalid")
        marker, supplied_hash = parsed
        payload_hash = vital_payload_hash(clean)
        if not hmac.compare_digest(
            supplied_hash.casefold(), payload_hash[: len(supplied_hash)].casefold()
        ):
            raise OpenEMRWriteError("vital note fingerprint does not match payload")
        clean["note"] = f"copilot-intent:{marker};payload:{payload_hash}"
        return await self._writer.create_vital(
            patient_id=patient_id,
            encounter_id=encounter_id,
            legacy_patient_id=self._legacy_patient_id(patient_id),
            legacy_encounter_id=self._legacy_encounter_id(encounter_id),
            payload=clean,
        )

    def _authorize_patient(self, patient_id: str) -> None:
        if patient_id != self._principal.patient_id:
            raise OpenEMRWriteError(
                "delegated principal is bound to a different patient"
            )

    def _legacy_patient_id(self, patient_id: str) -> str:
        if not hmac.compare_digest(self._legacy_routes.patient_uuid, patient_id):
            raise PatientRouteMismatch(
                "OpenEMR patient mapping did not match delegation"
            )
        return self._legacy_routes.patient_id

    def _legacy_encounter_id(self, encounter_id: str) -> str:
        attested_uuid = self._legacy_routes.encounter_uuid
        attested_id = self._legacy_routes.encounter_id
        if (
            attested_uuid is None
            or attested_id is None
            or not hmac.compare_digest(attested_uuid, encounter_id)
        ):
            raise EncounterRouteMismatch(
                "OpenEMR encounter mapping did not match the pinned patient"
            )
        return attested_id

    def _require_attestation(
        self, category_path: str, *, writable: bool
    ) -> CategoryAttestation:
        attested = self._attestations.get(category_path)
        if attested is None or (writable and not attested.writable):
            raise CategoryMismatch(
                "document category is not attested for this operation"
            )
        return attested

    def _vital_url(self, patient_id: str, encounter_id: str) -> str:
        legacy_patient_id = self._legacy_patient_id(patient_id)
        legacy_encounter_id = self._legacy_encounter_id(encounter_id)
        return (
            f"{self._base}/api/patient/{quote(legacy_patient_id, safe='')}"
            f"/encounter/{quote(legacy_encounter_id, safe='')}/vital"
        )

    def _headers(self, *, fhir: bool) -> dict[str, str]:
        return {
            "Authorization": (
                f"Bearer {self._principal.access_token.get_secret_value()}"
            ),
            "Accept": "application/fhir+json" if fhir else "application/json",
            **outbound_headers(),
        }

    async def _get_json(
        self,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        fhir: bool = False,
        missing_ok: bool = False,
    ) -> object | None:
        response = await self._get_response(
            url, params=params, fhir=fhir, missing_ok=missing_ok
        )
        if response is None:
            return None
        try:
            return response.json()
        except Exception as exc:
            raise LiveGatewayError("OpenEMR returned invalid JSON") from exc

    async def _get_response(
        self,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        fhir: bool = False,
        missing_ok: bool = False,
    ) -> httpx.Response | None:
        try:
            if self._http is not None:
                response = await self._http.get(
                    url,
                    params=params,
                    headers=self._headers(fhir=fhir),
                    timeout=self._timeout,
                )
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.get(
                        url, params=params, headers=self._headers(fhir=fhir)
                    )
        except httpx.HTTPError as exc:
            raise LiveGatewayError(type(exc).__name__) from exc
        if missing_ok and response.status_code == 404:
            return None
        if not 200 <= response.status_code < 300:
            raise LiveGatewayError(f"OpenEMR read returned HTTP {response.status_code}")
        return response


def vital_payload_hash(payload: Mapping[str, object]) -> str:
    """Match the pipeline's canonical per-field clinical payload hash."""

    clean = strip_caller_attribution(payload)
    clinical = {
        key: _json_value(value)
        for key, value in clean.items()
        if key in _VITAL_FIELDS or key == "date"
    }
    if "date" not in clinical or sum(key in clinical for key in _VITAL_FIELDS) != 1:
        raise OpenEMRWriteError("vital payload must contain one field and date")
    encoded = json.dumps(clinical, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _json_value(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    return value


def _records(body: object | None) -> list[Mapping[str, object]]:
    if isinstance(body, Mapping) and isinstance(body.get("data"), list):
        body = body["data"]
    if not isinstance(body, list):
        raise LiveGatewayError("OpenEMR list response has an invalid shape")
    return [
        cast(Mapping[str, object], item) for item in body if isinstance(item, Mapping)
    ]


def _resources(bundle: object | None) -> list[Mapping[str, object]]:
    if not isinstance(bundle, Mapping) or bundle.get("resourceType") != "Bundle":
        raise LiveGatewayError("FHIR search response is not a Bundle")
    entries = bundle.get("entry", [])
    if not isinstance(entries, list):
        raise LiveGatewayError("FHIR Bundle entries have an invalid shape")
    return [
        cast(Mapping[str, object], entry["resource"])
        for entry in entries
        if isinstance(entry, Mapping) and isinstance(entry.get("resource"), Mapping)
    ]


def _binary_ids(
    resource: Mapping[str, object], filename: str, base_parts: SplitResult
) -> set[str]:
    if resource.get("resourceType") != "DocumentReference":
        return set()
    found: set[str] = set()
    content = resource.get("content", [])
    if not isinstance(content, list):
        return found
    for item in content:
        if not isinstance(item, Mapping) or not isinstance(
            item.get("attachment"), Mapping
        ):
            continue
        attachment = cast(Mapping[str, object], item["attachment"])
        title = attachment.get("title")
        url = attachment.get("url")
        if not isinstance(title, str) or not hmac.compare_digest(title, filename):
            continue
        if not isinstance(url, str):
            continue
        parsed = urlsplit(url)
        if parsed.scheme and (
            parsed.scheme != base_parts.scheme or parsed.netloc != base_parts.netloc
        ):
            continue
        prefix = f"{base_parts.path}/fhir/Binary/"
        if not parsed.path.startswith(prefix):
            continue
        binary_id = parsed.path[len(prefix) :]
        if _BINARY_ID.fullmatch(binary_id):
            found.add(binary_id)
    return found


def _parse_note(value: object) -> tuple[str, str] | None:
    if not isinstance(value, str):
        return None
    matched = _NOTE.fullmatch(value)
    if matched is None:
        return None
    return matched.group("marker"), matched.group("hash")


def _standard_vital(row: Mapping[str, object]) -> _StandardVital | None:
    parsed = _parse_note(row.get("note"))
    if parsed is None:
        return None
    _marker, noted_hash = parsed
    if len(noted_hash) != 64:
        return None
    measured_at = _datetime(row.get("date"))
    if measured_at is None:
        return None
    dates = _datetime_text_variants(measured_at)
    matches: list[tuple[str, Decimal]] = []
    for field_id in _VITAL_FIELDS:
        value = _decimal(row.get(field_id))
        if value is None:
            continue
        for text in _decimal_text_variants(value):
            for date in dates:
                candidate = vital_payload_hash({field_id: text, "date": date})
                if hmac.compare_digest(candidate, noted_hash.casefold()):
                    matches.append((field_id, value))
                    break
            else:
                continue
            break
    if len(matches) != 1:
        return None
    field_id, value = matches[0]
    return _StandardVital(
        noted_hash.casefold(), field_id, value, measured_at, str(row["note"])
    )


def _decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _decimal_text_variants(value: Decimal) -> tuple[str, ...]:
    """Recover the exact submitted Decimal scale from a DECIMAL(…,6) readback hash."""

    normalized = _decimal_text(value)
    whole, dot, fraction = normalized.partition(".")
    variants = [normalized]
    if dot:
        variants.extend(
            f"{whole}.{fraction}{'0' * count}"
            for count in range(1, max(0, 6 - len(fraction)) + 1)
        )
    else:
        variants.extend(f"{whole}.{'0' * count}" for count in range(1, 7))
    return tuple(variants)


def _datetime_text_variants(value: datetime) -> tuple[str, ...]:
    offset = value.isoformat()
    return tuple(
        dict.fromkeys(
            (
                value.strftime("%Y-%m-%d %H:%M:%S"),
                offset,
                offset.replace("+00:00", "Z"),
            )
        )
    )


def _datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _reference_matches(value: object, resource_type: str, expected_id: str) -> bool:
    if not isinstance(value, Mapping) or not isinstance(value.get("reference"), str):
        return False
    reference = str(value["reference"]).rstrip("/").split("/")
    return len(reference) >= 2 and reference[-2:] == [resource_type, expected_id]


def _reference_id(value: object, resource_type: str) -> str | None:
    if not isinstance(value, Mapping) or not isinstance(value.get("reference"), str):
        return None
    reference = str(value["reference"]).rstrip("/").split("/")
    if len(reference) < 2 or reference[-2] != resource_type:
        return None
    return reference[-1]


def _codes(resource: Mapping[str, object]) -> set[str]:
    code = resource.get("code")
    if not isinstance(code, Mapping) or not isinstance(code.get("coding"), list):
        return set()
    return {
        str(item["code"])
        for item in code["coding"]
        if isinstance(item, Mapping) and item.get("code") is not None
    }


def _note_texts(resource: Mapping[str, object]) -> set[str]:
    notes = resource.get("note", [])
    if not isinstance(notes, list):
        return set()
    return {
        str(item["text"])
        for item in notes
        if isinstance(item, Mapping) and isinstance(item.get("text"), str)
    }


def _fhir_vital_matches(
    resources: list[Mapping[str, object]],
    *,
    standard: _StandardVital,
    patient_id: str,
    encounter_id: str,
) -> bool:
    panels = [
        resource
        for resource in resources
        if resource.get("resourceType") == "Observation"
        and standard.note in _note_texts(resource)
        and _reference_matches(resource.get("subject"), "Patient", patient_id)
        and _reference_matches(resource.get("encounter"), "Encounter", encounter_id)
    ]
    if len(panels) != 1:
        return False
    members = panels[0].get("hasMember", [])
    if not isinstance(members, list):
        return False
    member_ids = {
        member_id
        for member in members
        if (member_id := _reference_id(member, "Observation")) is not None
    }
    candidates = [
        resource
        for resource in resources
        if str(resource.get("id", "")) in member_ids
        and _reference_matches(resource.get("subject"), "Patient", patient_id)
        and _reference_matches(resource.get("encounter"), "Encounter", encounter_id)
        and _same_instant(resource.get("effectiveDateTime"), standard.measured_at)
    ]
    return any(_fhir_field_matches(resource, standard) for resource in candidates)


def _same_instant(value: object, expected: datetime) -> bool:
    parsed = _datetime(value)
    return parsed is not None and parsed == expected


def _fhir_field_matches(
    resource: Mapping[str, object], standard: _StandardVital
) -> bool:
    if standard.field_id in _BP_COMPONENT_CODES:
        if _BP_PANEL_CODE not in _codes(resource):
            return False
        components = resource.get("component", [])
        if not isinstance(components, list):
            return False
        wanted = _BP_COMPONENT_CODES[standard.field_id]
        return any(
            isinstance(component, Mapping)
            and wanted in _codes(cast(Mapping[str, object], component))
            and _quantity_matches(component.get("valueQuantity"), standard.value)
            for component in components
        )
    wanted = _VITAL_CODES.get(standard.field_id)
    return (
        wanted is not None
        and wanted in _codes(resource)
        and _quantity_matches(resource.get("valueQuantity"), standard.value)
    )


def _quantity_matches(value: object, expected: Decimal) -> bool:
    if not isinstance(value, Mapping):
        return False
    actual = _decimal(value.get("value"))
    return actual is not None and actual == expected
