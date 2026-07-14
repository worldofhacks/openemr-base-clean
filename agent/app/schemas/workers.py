"""Supervisor/worker payload contracts (W2_ARCHITECTURE.md §2).

``WorkerInput`` and ``WorkerOutput`` are the ONLY payloads that cross the
supervisor-worker boundary. They carry REFS (trace-addressable ids), never raw PHI —
``patient_ref``/``document_refs``/``evidence_refs`` in, ``artifact_refs``/``citation_refs``
out — so no clinical value ever rides the handoff. ``reason_code`` on the output is the
closed ``ReasonCode`` (a worker cannot invent one).

@package   OpenEMR — Clinical Co-Pilot agent
@link      https://www.open-emr.org
@author    Claude Code
@copyright Copyright (c) 2026 OpenEMR contributors
@license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.handoff import ReasonCode


class WorkerInput(BaseModel):
    """The supervisor → worker payload (§2). Refs only, never raw PHI."""

    model_config = ConfigDict(extra="forbid")

    correlation_id: str = Field(min_length=1)
    turn: int = Field(ge=0)
    patient_ref: str = Field(min_length=1)
    document_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    request_kind: str = Field(min_length=1)


class WorkerOutput(BaseModel):
    """The worker → supervisor payload (§2). Refs only, never raw PHI.

    ``artifact_refs``/``citation_refs`` point at persisted artifacts and citations;
    ``reason_code`` is the closed ``ReasonCode`` (``None`` when the worker completed with
    no notable reason).
    """

    model_config = ConfigDict(extra="forbid")

    correlation_id: str = Field(min_length=1)
    worker: str = Field(min_length=1)
    status: str = Field(min_length=1)
    artifact_refs: list[str] = Field(default_factory=list)
    citation_refs: list[str] = Field(default_factory=list)
    reason_code: Optional[ReasonCode] = None
