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

from pydantic import BaseModel, ConfigDict, Field


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
    invalid) but NULLABLE: §2a's binding W1→CitationV2 migration maps every chart-fact
    (``source_type=patient_record``) citation with ``page_or_section=null`` (a record id
    has no page/section). Guideline/uploaded_document citations carry a real section/page;
    the composer's render rule (incomplete citation = does not render) enforces that at
    the surface, not this schema.
    """

    model_config = ConfigDict(extra="forbid")

    source_type: CitationSourceType
    source_id: str = Field(min_length=1)
    page_or_section: str | None
    field_or_chunk_id: str = Field(min_length=1)
    quote_or_value: str = Field(min_length=1)


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
