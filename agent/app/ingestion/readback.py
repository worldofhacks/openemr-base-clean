"""Content-free FHIR Binary digest attestations (W2-D1/D9/D10; §3/§5).

The public verification surface returns only SHA-256 digests computed from bytes that
were independently re-read through the patient-bound OpenEMR gateway. It never returns
the source document, grounded artifact, delegated credential, or response body.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class BinaryReadbackVerification(BaseModel):
    """One expected-vs-observed SHA-256 result over FHIR Binary bytes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    algorithm: Literal["sha256"] = "sha256"
    expected_hash: str = Field(pattern=_SHA256_PATTERN)
    observed_hash: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    verified: bool


class DocumentReadbackVerification(BaseModel):
    """Source and optional grounded-artifact Binary attestations for one document."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str = Field(min_length=1)
    source: BinaryReadbackVerification
    artifact: BinaryReadbackVerification | None
