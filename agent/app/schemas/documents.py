"""Document upload / retry / status contracts + the closed FailureReason enum (§2).

The HTTP-boundary shapes for the documents surface (W2_ARCHITECTURE.md §2):

* ``UploadRequest`` / ``UploadAccepted`` — the attach-and-extract request/ack.
* ``RetryRequest`` (``expected_state`` pinned to the literal ``"failed"`` — only a
  failed job is retryable) / ``RetryAccepted``.
* ``DocumentStatus`` — the polled status, whose ``reason`` is a ``FailureReason`` or
  ``None`` (never a free-text substitute).
* ``FailureReason`` — the CLOSED failure vocabulary. Every member maps to a §5 row, a
  log event, and a negative test; ``unit_mismatch`` / ``range_violation`` may be
  field-leg skip reasons while the overall artifact still succeeds (W2-D10).

@package   OpenEMR — Clinical Co-Pilot agent
@link      https://www.open-emr.org
@author    Claude Code
@copyright Copyright (c) 2026 OpenEMR contributors
@license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
"""

from __future__ import annotations

import enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class FailureReason(enum.Enum):
    """The complete CLOSED §2 failure vocabulary — no free-text reason substitutes.

    The enumerated names below are the authoritative set (the §2 prose's "21-member"
    figure is a miscount of this same explicit list — it is exactly twenty).
    """

    PATIENT_MISMATCH = "patient_mismatch"
    ENCOUNTER_MISMATCH = "encounter_mismatch"
    UNIT_MISMATCH = "unit_mismatch"
    RANGE_VIOLATION = "range_violation"
    SCOPE_MISMATCH = "scope_mismatch"
    CATEGORY_MISMATCH = "category_mismatch"
    BINARY_READBACK_UNSAFE = "binary_readback_unsafe"
    UPLOAD_REJECTED = "upload_rejected"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    SIZE_OR_PAGE_CAP_EXCEEDED = "size_or_page_cap_exceeded"
    STORAGE_WRITE_FAILED = "storage_write_failed"
    OCR_FAILED = "ocr_failed"
    VLM_TIMEOUT = "vlm_timeout"
    VLM_UNAVAILABLE = "vlm_unavailable"
    SCHEMA_VIOLATION = "schema_violation"
    AUTH_EXPIRED = "auth_expired"
    WRITEBACK_FAILED = "writeback_failed"
    WRITEBACK_VERIFY_FAILED = "writeback_verify_failed"
    DOC_TYPE_MISMATCH = "doc_type_mismatch"
    WORKER_RESTART = "worker_restart"


class UploadRequest(BaseModel):
    """The attach-and-extract request body (§2).

    ``doc_type`` selects the extraction path; the file itself is stored via the
    documents API out-of-band and referenced here by ``filename``/``content_hash``.
    """

    model_config = ConfigDict(extra="forbid")

    patient_id: str = Field(min_length=1)
    doc_type: Literal["lab_pdf", "intake_form"]
    filename: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)


class UploadAccepted(BaseModel):
    """The 202-style ack for an accepted upload (§2)."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    state: str = Field(min_length=1)
    status_url: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)


class RetryRequest(BaseModel):
    """A retry request (§2). ``expected_state`` is pinned to ``"failed"`` — a retry is
    only legal against a job that is currently failed (optimistic-state guard)."""

    model_config = ConfigDict(extra="forbid")

    expected_state: Literal["failed"]


class RetryAccepted(BaseModel):
    """The ack for an accepted retry (§2)."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    state: str = Field(min_length=1)
    status_url: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)


class DocumentStatus(BaseModel):
    """The polled document/extraction status (§2).

    ``reason`` is a ``FailureReason`` or ``None`` — a free-text reason is rejected, so
    every failure maps to a closed enum member. ``fields_grounded`` /
    ``fields_unsupported`` report the grounding tally; ``next_retry_at`` is set only
    when a retry is scheduled.
    """

    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1)
    state: str = Field(min_length=1)
    reason: Optional[FailureReason] = None
    correlation_id: str = Field(min_length=1)
    updated_ts: str = Field(min_length=1)
    fields_grounded: int = Field(ge=0)
    fields_unsupported: int = Field(ge=0)
    attempt_count: int = Field(ge=0)
    next_retry_at: Optional[str] = None
