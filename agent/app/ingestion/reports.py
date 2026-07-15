"""Project persisted grounded artifacts into the safe extraction-report surface."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Iterable

from pydantic import BaseModel

from app.schemas.extraction import ExtractionArtifact, GroundedField
from app.schemas.extraction_report import (
    DocumentExtractionReport,
    ExtractionReportField,
)


class ExtractionReportIntegrityError(RuntimeError):
    """A persisted artifact cannot satisfy the browser render contract."""


def _walk(
    value: object, path: str = ""
) -> Iterable[tuple[str, GroundedField[object]]]:
    if isinstance(value, GroundedField):
        yield path, value
        return
    if isinstance(value, BaseModel):
        for name in type(value).model_fields:
            child_path = f"{path}.{name}" if path else name
            yield from _walk(getattr(value, name), child_path)
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _walk(item, f"{path}.{index}")


def _display(value: object) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def project_extraction_report(
    artifact: ExtractionArtifact,
) -> DocumentExtractionReport:
    """Expose only persisted schema-valid leaves; unsupported proposals are redacted."""

    fields: list[ExtractionReportField] = []
    for path, field in _walk(artifact.extraction):
        if not path:
            raise ExtractionReportIntegrityError("artifact contains an unaddressed field")
        if field.grounded:
            if field.value is None:
                raise ExtractionReportIntegrityError(
                    "grounded artifact field has no display value"
                )
            fields.append(
                ExtractionReportField(
                    field_path=path,
                    verdict="grounded",
                    display_value=_display(field.value),
                    page=field.page,
                    bbox=field.bbox,
                    citation=field.citation,
                )
            )
        else:
            fields.append(
                ExtractionReportField(
                    field_path=path,
                    verdict="unsupported",
                    display_value=None,
                    page=field.page,
                    bbox=field.bbox,
                    citation=None,
                )
            )

    grounded = sum(field.verdict == "grounded" for field in fields)
    unsupported = sum(field.verdict == "unsupported" for field in fields)
    expected = artifact.grounding_summary
    if expected != {
        "fields_grounded": grounded,
        "fields_unsupported": unsupported,
    }:
        raise ExtractionReportIntegrityError(
            "artifact grounding summary does not match projected fields"
        )
    return DocumentExtractionReport(
        document_id=artifact.document_id,
        doc_type=artifact.doc_type,
        state="complete",
        fields_grounded=grounded,
        fields_unsupported=unsupported,
        fields=fields,
    )
