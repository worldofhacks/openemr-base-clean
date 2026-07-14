"""The sole structured-log event envelope (W2_ARCHITECTURE.md §2; D5 / W2-D7 PHI posture).

``LogEventEnvelope`` is the ONE structured-log envelope every W2 component emits. Its
``attributes`` map permits ONLY the approved PHI-free scalar/list schema — never raw
document text, extracted clinical values, token material, or a free-form exception body.
The value type is deliberately constrained so the ``no_phi_in_logs`` invariant (W2-D5) is
enforced STRUCTURALLY at the source:

* nested dicts / structured objects are rejected — that is exactly where raw document
  text, extracted values, or exception bodies would ride;
* string values are single-line CODES/labels of bounded length (a reason CODE, a leg
  name, a hash), NOT arbitrary multi-line message bodies (a traceback-shaped blob is
  rejected).

Optional IDs (``case_id``, ``job_id``, ``correlation_id``) are explicit ``None``-able.

@package   OpenEMR — Clinical Co-Pilot agent
@link      https://www.open-emr.org
@author    Claude Code
@copyright Copyright (c) 2026 OpenEMR contributors
@license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
"""

from __future__ import annotations

from typing import Annotated, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

#: The maximum length of a single log-attribute string value. A code/label/hash fits
#: comfortably; a raw document excerpt or a traceback blob does not.
_MAX_ATTRIBUTE_STR_LEN: int = 256

#: An approved attribute string: single-line (NO newlines/carriage returns — a
#: multi-line exception body is rejected) and length-bounded. It is a CODE or label, not
#: a free-form message body.
LogScalarStr = Annotated[
    str,
    StringConstraints(max_length=_MAX_ATTRIBUTE_STR_LEN, pattern=r"^[^\r\n]*$"),
]

#: An approved attribute scalar: a bounded single-line string, or a plain number/bool.
#: ``bool`` precedes ``int`` so a JSON boolean is not silently narrowed to ``0``/``1``.
LogScalar = Union[LogScalarStr, bool, int, float]

#: An approved attribute value: a scalar, or a flat list of scalars. NOT a nested dict —
#: a structured/nested object is where PHI would hide, so it is structurally forbidden.
LogAttributeValue = Union[LogScalar, list[LogScalar]]


class LogEventEnvelope(BaseModel):
    """The sole structured-log envelope (§2).

    ``schema_version`` versions the envelope; ``event_type`` is the event name
    (``job.claimed``, ``job.failed``, …); ``occurred_at`` is the ISO-8601 UTC instant;
    the optional IDs correlate the event to a case/job/request; ``component`` and
    ``severity`` classify the emitter; ``attributes`` carries only PHI-free
    scalar/list values.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int
    event_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    occurred_at: str = Field(min_length=1)
    case_id: Optional[str] = None
    job_id: Optional[str] = None
    correlation_id: Optional[str] = None
    component: str = Field(min_length=1)
    severity: str = Field(min_length=1)
    attributes: dict[str, LogAttributeValue] = Field(default_factory=dict)
