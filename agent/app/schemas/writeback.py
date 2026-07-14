"""Write-back intent/result contracts + the closed leg/state enums (§2, W2-D10).

The exactly-once write path (W2_ARCHITECTURE.md §2):

* ``WriteLeg`` — the three write legs (``source_document``, ``extraction_artifact``,
  ``vital``).
* ``WriteState`` — the three write states (``pending``, ``unknown``, ``complete``);
  ``unknown`` is the reconcilable in-doubt state after an interrupted write.
* ``WriteIntent`` — the durable intent row (idempotency via
  ``correlation_marker`` + ``payload_hash``).
* ``WriteResult`` — the outcome of executing an intent, including remote id and the
  read-back ``verified`` flag.

@package   OpenEMR — Clinical Co-Pilot agent
@link      https://www.open-emr.org
@author    Claude Code
@copyright Copyright (c) 2026 OpenEMR contributors
@license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
"""

from __future__ import annotations

import enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.documents import FailureReason


class WriteLeg(enum.Enum):
    """The closed §2 write-leg vocabulary — the three durable write targets."""

    SOURCE_DOCUMENT = "source_document"
    EXTRACTION_ARTIFACT = "extraction_artifact"
    VITAL = "vital"


class WriteState(enum.Enum):
    """The closed §2 write-state vocabulary.

    ``pending`` (not yet confirmed written), ``unknown`` (in-doubt after an interrupted
    write — reconcilable), ``complete`` (confirmed and verified).
    """

    PENDING = "pending"
    UNKNOWN = "unknown"
    COMPLETE = "complete"


class WriteIntent(BaseModel):
    """A durable write intent (§2, W2-D10).

    ``correlation_marker`` + ``payload_hash`` provide the idempotency key so a retried
    write is de-duplicated against the same remote resource; ``version`` guards
    optimistic concurrency; ``remote_id`` is populated once the leg lands.
    """

    model_config = ConfigDict(extra="forbid")

    intent_id: str = Field(min_length=1)
    patient_id: str = Field(min_length=1)
    document_id_or_content_hash: str = Field(min_length=1)
    leg: WriteLeg
    version: int
    field_id: Optional[str] = None
    correlation_marker: str = Field(min_length=1)
    payload_hash: str = Field(min_length=1)
    state: WriteState
    remote_id: Optional[str] = None
    attempt_count: int = Field(ge=0)
    updated_ts: str = Field(min_length=1)


class WriteResult(BaseModel):
    """The outcome of executing a ``WriteIntent`` (§2).

    ``verified`` is the read-back confirmation (the write was observed to have landed);
    ``failure_reason`` is a closed ``FailureReason`` when the write did not complete.
    """

    model_config = ConfigDict(extra="forbid")

    intent_id: str = Field(min_length=1)
    state: WriteState
    remote_id: Optional[str] = None
    payload_hash: str = Field(min_length=1)
    verified: bool
    failure_reason: Optional[FailureReason] = None
