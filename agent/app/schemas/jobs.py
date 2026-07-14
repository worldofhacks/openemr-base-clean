"""Job-record contract + the closed JobState lifecycle enum (W2_ARCHITECTURE.md §2).

``JobRecord`` is the durable extraction-job row (the write-path unit of work). ``state``
is the CLOSED ``JobState`` lifecycle: an eight-member progression from ``storing``
through ``complete``/``failed``. Leasing fields (``claim_owner``, ``lease_expires_at``,
``heartbeat_at``) support exactly-once worker claiming; ``credential_ref`` is a ref, not
a raw credential (no secrets on the record).

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


class JobState(enum.Enum):
    """The closed §2 job lifecycle — the eight-member state progression."""

    STORING = "storing"
    RECONCILING = "reconciling"
    QUEUED = "queued"
    EXTRACTING = "extracting"
    GROUNDING = "grounding"
    WRITING = "writing"
    COMPLETE = "complete"
    FAILED = "failed"


class JobRecord(BaseModel):
    """The durable extraction-job record (§2).

    ``content_hash`` is the idempotency key; ``credential_ref`` references a stored
    credential (never the raw secret); the lease/heartbeat fields fence a claimed job
    to a single worker; ``attempt_count`` / ``next_attempt_at`` drive bounded retries.
    """

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    patient_id: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    credential_ref: str = Field(min_length=1)
    state: JobState
    claim_owner: Optional[str] = None
    lease_expires_at: Optional[str] = None
    heartbeat_at: Optional[str] = None
    attempt_count: int = Field(ge=0)
    next_attempt_at: Optional[str] = None
    created_ts: str = Field(min_length=1)
    updated_ts: str = Field(min_length=1)
