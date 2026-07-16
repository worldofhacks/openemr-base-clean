"""Deterministic lab-trend projection over completed grounded artifacts.

No chart resource is written and no LOINC aliasing, unit conversion, thresholding, or
clinical interpretation occurs here.  A completed document is the durable pipeline
state reached only after source/artifact exactly-once writes passed readback verification.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
import unicodedata

from app.ingestion.artifacts import ArtifactStore
from app.ingestion.reports import ExtractionReportIntegrityError, project_extraction_report
from app.ingestion.repository import DocumentRepository
from app.schemas.citations import CitationSourceType
from app.schemas.extraction import ExtractionArtifact, GroundedField, LabPdfExtraction
from app.schemas.lab_trends import LabTrendPoint, LabTrendResponse, LabTrendSeries


class LabTrendIntegrityError(RuntimeError):
    """A completed lab artifact cannot safely support the trend response."""


def normalize_test_name(value: str) -> str:
    """Apply only the approved Unicode/whitespace/case normalization."""

    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def _display_test_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _required_grounded(
    field: GroundedField, *, document_id: str, expected_path: str
) -> object | None:
    if not field.grounded:
        return None
    if (
        field.value is None
        or field.page is None
        or field.bbox is None
        or field.citation is None
    ):
        raise LabTrendIntegrityError("grounded trend field is incomplete")
    citation = field.citation
    if (
        citation.source_type is not CitationSourceType.UPLOADED_DOCUMENT
        or citation.source_id != document_id
        or citation.page_or_section != str(field.page)
        or citation.field_or_chunk_id != expected_path
    ):
        raise LabTrendIntegrityError("grounded trend citation is unresolved")
    return field.value


async def project_lab_trends(
    *,
    repository: DocumentRepository,
    artifact_store: ArtifactStore,
    patient_id: str,
) -> LabTrendResponse:
    """Project numeric points for one pinned patient, deterministically and read-only."""

    records = await repository.list_for_patient(patient_id, state="complete")
    grouped: dict[tuple[str, str], list[tuple[str, LabTrendPoint]]] = defaultdict(list)

    for record in records:
        if record.doc_type != "lab_pdf":
            continue
        try:
            refs = await artifact_store.refs_for_document(record.document_id)
            artifact = None if refs is None else artifact_store.resolve(refs.artifact_ref)
        except Exception as exc:  # noqa: BLE001 - fail closed on persisted-store faults
            raise LabTrendIntegrityError("lab trend artifact is unavailable") from exc
        if not isinstance(artifact, ExtractionArtifact):
            raise LabTrendIntegrityError("completed lab document has no artifact")
        if (
            artifact.artifact_version != 1
            or artifact.document_id != record.document_id
            or artifact.content_hash != record.content_hash
            or artifact.doc_type != "lab_pdf"
            or not isinstance(artifact.extraction, LabPdfExtraction)
        ):
            raise LabTrendIntegrityError("lab trend artifact identity mismatch")
        try:
            project_extraction_report(artifact)
        except (ExtractionReportIntegrityError, ValueError) as exc:
            raise LabTrendIntegrityError("lab trend artifact failed integrity checks") from exc

        for result_index, result in enumerate(artifact.extraction.results):
            base = f"results[{result_index}]"
            test_name = _required_grounded(
                result.test_name,
                document_id=record.document_id,
                expected_path=f"{base}.test_name",
            )
            raw_value = _required_grounded(
                result.value,
                document_id=record.document_id,
                expected_path=f"{base}.value",
            )
            unit = _required_grounded(
                result.unit,
                document_id=record.document_id,
                expected_path=f"{base}.unit",
            )
            collection_date = _required_grounded(
                result.collection_date,
                document_id=record.document_id,
                expected_path=f"{base}.collection_date",
            )
            if any(value is None for value in (test_name, raw_value, unit, collection_date)):
                continue
            if (
                not isinstance(test_name, str)
                or not isinstance(raw_value, str)
                or not isinstance(unit, str)
                or not isinstance(collection_date, date)
            ):
                raise LabTrendIntegrityError("lab trend field has an invalid type")
            display_name = _display_test_name(test_name)
            normalized_name = normalize_test_name(test_name)
            if not display_name or not normalized_name or not unit:
                continue
            try:
                numeric_value = Decimal(raw_value)
            except (InvalidOperation, ValueError):
                continue
            if not numeric_value.is_finite():
                continue
            value_citation = result.value.citation
            date_citation = result.collection_date.citation
            assert value_citation is not None and date_citation is not None
            assert result.value.page is not None and result.value.bbox is not None
            point = LabTrendPoint(
                document_id=record.document_id,
                result_index=result_index,
                collection_date=collection_date,
                value=numeric_value,
                display_value=raw_value,
                citation=value_citation,
                date_citation=date_citation,
                page=result.value.page,
                bbox=result.value.bbox,
            )
            # Unit text is intentionally exact: no normalization or conversion.
            grouped[(normalized_name, unit)].append((display_name, point))

    series: list[LabTrendSeries] = []
    for (normalized_name, unit), named_points in grouped.items():
        test_name = min(name for name, _point in named_points)
        points = sorted(
            (point for _name, point in named_points),
            key=lambda point: (
                point.collection_date,
                point.document_id,
                point.result_index,
            ),
        )
        series.append(LabTrendSeries(test_name=test_name, unit=unit, points=points))
    series.sort(key=lambda item: (normalize_test_name(item.test_name), item.unit, item.test_name))
    return LabTrendResponse(series=series)
