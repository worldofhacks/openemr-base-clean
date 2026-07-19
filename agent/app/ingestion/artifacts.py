"""Durable extraction-artifact and citation refs (W2-D3/D10; §2/§3).

Refs contain no clinical content.  The in-process resolver is synchronous so it can be
composed with the graph's ``TurnRefRegistry``; production replicas warm that cache from
Postgres before composing a turn.

Authority ledger (AF-P1-03; PDF p.6 "one source of truth per data type, no silent
overwrites"; conservative pending the AF-P2-04 grader answer):

- **Extraction artifacts and citation refs** — *owner:* Agent PostgreSQL
  (``agent_extraction_refs``, migration 004; this module's ``PostgresArtifactStore``).
  *Lineage:* produced by the VLM extraction pipeline from the OpenEMR source document,
  keyed by ``document_id``/``content_hash``/``artifact_version``. *Access:* written
  only by the dedicated document worker under a patient-pinned delegated credential;
  read by report/trend/graph turns through the session's patient pin. *Validation:*
  strict Pydantic models (``ExtractionArtifact``/``CitationV2``); ref-collision inserts
  fail (`ON CONFLICT` readback compare) — never a silent overwrite.
- **The OpenEMR artifact copy** (``/AI-Extractions`` document) is a *verified
  projection*, not an authority: every readback re-reads the Binary bytes and compares
  a SHA-256 digest against the Postgres-authoritative serialization; divergence is
  detected and fails closed (``tests/test_artifact_authority_divergence.py``).
- **Source documents and written vitals** — *owner:* OpenEMR (the EHR record).  The
  agent's write legs are append-only, exactly-once intents (migration 003) verified by
  digest readback; the agent never edits or deletes EHR content.

See ``agent/migrations/README.md`` for the per-table ledger and the real migration
inventory (001, 003–007; there has never been a migration 002).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol, cast

from pydantic import BaseModel

from app.schemas.citations import CitationV2
from app.schemas.extraction import ExtractionArtifact, GroundedField


@dataclass(frozen=True)
class ArtifactRefs:
    artifact_ref: str
    citation_refs: tuple[str, ...]


class ArtifactStore(Protocol):
    async def persist(self, artifact: ExtractionArtifact) -> ArtifactRefs: ...

    async def refs_for_document(self, document_id: str) -> ArtifactRefs | None: ...

    async def warm_for_documents(self, document_ids: list[str]) -> None: ...

    def resolve(self, ref: str) -> object | None: ...


def _refs(artifact: ExtractionArtifact) -> ArtifactRefs:
    prefix = f"document:{artifact.document_id}:extraction:v{artifact.artifact_version}"
    citations = _citations(artifact.extraction)
    return ArtifactRefs(
        artifact_ref=f"{prefix}:artifact",
        citation_refs=tuple(
            f"{prefix}:citation:{index}" for index in range(len(citations))
        ),
    )


def _citations(value: object) -> tuple[CitationV2, ...]:
    found: list[CitationV2] = []
    seen: set[str] = set()

    def visit(item: object) -> None:
        if isinstance(item, GroundedField):
            citation = item.citation
            if citation is not None:
                key = citation.model_dump_json()
                if key not in seen:
                    seen.add(key)
                    found.append(citation)
            return
        if isinstance(item, BaseModel):
            for field_name in type(item).model_fields:
                visit(getattr(item, field_name))
            return
        if isinstance(item, (list, tuple)):
            for child in item:
                visit(child)

    visit(value)
    return tuple(found)


class InMemoryArtifactStore:
    """Persistent-store behavior fake with the same stable ref vocabulary."""

    def __init__(self) -> None:
        self._values: dict[str, object] = {}
        self._by_document: dict[str, ArtifactRefs] = {}

    async def persist(self, artifact: ExtractionArtifact) -> ArtifactRefs:
        refs = _refs(artifact)
        values: tuple[object, ...] = (artifact, *_citations(artifact.extraction))
        for ref, value in zip(
            (refs.artifact_ref, *refs.citation_refs), values, strict=True
        ):
            existing = self._values.get(ref)
            if existing is not None and existing != value:
                raise ValueError(f"artifact ref collision: {ref}")
            self._values[ref] = value
        existing_refs = self._by_document.get(artifact.document_id)
        if existing_refs is not None and existing_refs != refs:
            raise ValueError(f"document artifact ref collision: {artifact.document_id}")
        self._by_document[artifact.document_id] = refs
        return refs

    async def refs_for_document(self, document_id: str) -> ArtifactRefs | None:
        return self._by_document.get(document_id)

    async def warm_for_documents(self, document_ids: list[str]) -> None:
        del document_ids  # already resident

    def resolve(self, ref: str) -> object | None:
        return self._values.get(ref)


class PostgresArtifactStore:
    """THE durable artifact authority, backed by migration 004 (AF-P1-03).

    Report, trend, and graph-turn reads resolve extraction artifacts here; the
    OpenEMR ``/AI-Extractions`` copy is only a digest-verified projection and is
    never served as an artifact read path.  Persist refuses divergent re-inserts
    (ref collision) instead of overwriting.

    ``resolve`` intentionally reads only the local warmed cache.  Call
    ``warm_for_documents`` at the turn boundary; this preserves the registry's
    synchronous resolver contract without hiding database I/O in composition.
    """

    def __init__(self, connect: Callable[[], Awaitable[object]]) -> None:
        self._connect = connect
        self._values: dict[str, object] = {}
        self._by_document: dict[str, ArtifactRefs] = {}

    async def persist(self, artifact: ExtractionArtifact) -> ArtifactRefs:
        refs = _refs(artifact)
        citations = _citations(artifact.extraction)
        rows: list[tuple[str, str, int, str]] = [
            (
                refs.artifact_ref,
                "artifact",
                0,
                artifact.model_dump_json(warnings=False),
            )
        ]
        rows.extend(
            (ref, "citation", index, citation.model_dump_json())
            for index, (ref, citation) in enumerate(
                zip(refs.citation_refs, citations, strict=True)
            )
        )
        conn = await self._connect()
        try:
            async with conn.transaction():  # type: ignore[attr-defined]
                for ref, kind, ordinal, payload in rows:
                    stored = await conn.fetchval(  # type: ignore[attr-defined]
                        """
                        INSERT INTO agent_extraction_refs
                            (ref, document_id, kind, ordinal, payload, created_ts)
                        VALUES ($1,$2,$3,$4,$5::jsonb,NOW())
                        ON CONFLICT (ref) DO UPDATE SET ref=EXCLUDED.ref
                        RETURNING payload::text
                        """,
                        ref,
                        artifact.document_id,
                        kind,
                        ordinal,
                        payload,
                    )
                    if json.loads(str(stored)) != json.loads(payload):
                        raise ValueError(f"artifact ref collision: {ref}")
        finally:
            await _close(conn)
        self._cache(artifact, refs, citations)
        return refs

    async def refs_for_document(self, document_id: str) -> ArtifactRefs | None:
        if document_id not in self._by_document:
            await self.warm_for_documents([document_id])
        return self._by_document.get(document_id)

    async def warm_for_documents(self, document_ids: list[str]) -> None:
        if not document_ids:
            return
        conn = await self._connect()
        try:
            rows = await conn.fetch(  # type: ignore[attr-defined]
                """
                SELECT ref, document_id, kind, ordinal, payload
                  FROM agent_extraction_refs
                 WHERE document_id = ANY($1::text[])
                 ORDER BY document_id, kind, ordinal
                """,
                document_ids,
            )
        finally:
            await _close(conn)

        grouped: dict[str, list[dict[str, object]]] = {}
        for row in rows:
            values = dict(cast(dict[str, object], row))
            grouped.setdefault(str(values["document_id"]), []).append(values)
        for document_id, stored in grouped.items():
            artifact_rows = [row for row in stored if row["kind"] == "artifact"]
            if len(artifact_rows) != 1:
                raise ValueError(f"invalid artifact rows for {document_id}")
            artifact_row = artifact_rows[0]
            artifact = ExtractionArtifact.model_validate_json(
                _json_payload(artifact_row["payload"])
            )
            citation_rows = sorted(
                (row for row in stored if row["kind"] == "citation"),
                key=lambda row: int(str(row["ordinal"])),
            )
            refs = ArtifactRefs(
                artifact_ref=str(artifact_row["ref"]),
                citation_refs=tuple(str(row["ref"]) for row in citation_rows),
            )
            citations = tuple(
                CitationV2.model_validate_json(_json_payload(row["payload"]))
                for row in citation_rows
            )
            self._cache(artifact, refs, citations)

    def resolve(self, ref: str) -> object | None:
        return self._values.get(ref)

    def _cache(
        self,
        artifact: ExtractionArtifact,
        refs: ArtifactRefs,
        citations: tuple[CitationV2, ...],
    ) -> None:
        self._values[refs.artifact_ref] = artifact
        self._values.update(zip(refs.citation_refs, citations, strict=True))
        self._by_document[artifact.document_id] = refs


def _json_payload(value: object) -> str | bytes | bytearray:
    """Preserve Pydantic's strict schema while parsing persisted JSON representations."""

    if isinstance(value, (str, bytes, bytearray)):
        return value
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


async def _close(conn: object) -> None:
    close = getattr(conn, "close", None)
    if close is None:
        return
    result = close()
    if hasattr(result, "__await__"):
        await result
