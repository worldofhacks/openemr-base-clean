"""Citation + evidence-snippet contracts (W2_ARCHITECTURE.md §2, W2-D6).

``CitationV2`` is the citation-contract-v2 shape: exactly the five prescribed PRD
fields, with a CLOSED ``source_type`` so patient facts and guideline evidence never
blur (the UI renders them as visually distinct classes). An incomplete citation
(any of the five fields missing) is invalid — an incomplete citation means the
claim does not render as fact.

``EvidenceSnippet`` is one retrieved guideline chunk returned by the evidence
search path (§2 retrieval inventory).

@package   OpenEMR — Clinical Co-Pilot agent
@link      https://www.open-emr.org
@author    Claude Code
@copyright Copyright (c) 2026 OpenEMR contributors
@license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
"""

from __future__ import annotations

import enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CitationSourceType(enum.Enum):
    """Closed §2/W2-D6 source vocabulary — the three separated evidence classes.

    ``patient_record`` (facts read from OpenEMR), ``uploaded_document`` (an
    attached/extracted document), and ``guideline`` (the VA/DoD corpus). The
    separation is load-bearing: patient facts and guideline evidence are rendered
    visually distinct, so a free-text source class is forbidden.
    """

    PATIENT_RECORD = "patient_record"
    UPLOADED_DOCUMENT = "uploaded_document"
    GUIDELINE = "guideline"


class CitationV2(BaseModel):
    """The five prescribed PRD citation fields (§2, W2-D6).

    All five are REQUIRED — a citation missing any field is invalid and the claim it
    would support does not render as fact. ``source_type`` is the closed
    ``CitationSourceType``. Guideline ``source_id`` values embed the corpus version
    (``vadod-htn-2020@<manifest-hash>``) so a citation resolves against exactly the
    ingested corpus build.

    ``strict`` is intentionally OFF so ``source_type`` accepts its closed enum value by
    string (``"uploaded_document"``); an unknown string (``"wikipedia"``) still rejects,
    keeping the vocabulary closed.

    ``page_or_section`` is REQUIRED (the key must be present — an incomplete citation is
    invalid) but conditionally nullable: chart facts require NULL because a record id has
    no page/section; uploaded documents and guidelines require a non-blank page/section.
    These source-specific rules hold at construction, before a citation can reach a
    composer, API response, or UI renderer.
    """

    model_config = ConfigDict(extra="forbid")

    source_type: CitationSourceType
    source_id: str = Field(min_length=1)
    page_or_section: str | None
    field_or_chunk_id: str = Field(min_length=1)
    quote_or_value: str = Field(min_length=1)

    @model_validator(mode="after")
    def _source_location_is_valid(self) -> "CitationV2":
        if self.source_type is CitationSourceType.PATIENT_RECORD:
            if self.page_or_section is not None:
                raise ValueError("patient-record citations require page_or_section=null")
        elif (
            self.page_or_section is None
            or not self.page_or_section.strip()
        ):
            raise ValueError(
                "uploaded-document and guideline citations require a page or section"
            )
        return self


class EvidenceSnippet(BaseModel):
    """One retrieved guideline chunk (§2 retrieval inventory).

    ``corpus_version`` pins the exact ingested build the snippet came from; ``score``
    is the retriever/reranker relevance score.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    source_id: str = Field(min_length=1)
    section: str
    chunk_id: str = Field(min_length=1)
    quote: str
    score: float
    corpus_version: str = Field(min_length=1)
