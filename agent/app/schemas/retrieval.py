"""Evidence-search request/response contracts (W2_ARCHITECTURE.md §2).

``POST /evidence/search`` uses these NAMED models, never an anonymous ``{query, k}``.
``EvidenceSearchRequest`` bounds the fan-out: ``query`` is 1–180 characters and
``1 ≤ k ≤ K_MAX`` (``K_MAX`` is the named upper bound). ``EvidenceSearchResponse`` returns the
ranked ``EvidenceSnippet`` items pinned to a ``corpus_version`` and the request's
``correlation_id``.

@package   OpenEMR — Clinical Co-Pilot agent
@link      https://www.open-emr.org
@author    Claude Code
@copyright Copyright (c) 2026 OpenEMR contributors
@license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.citations import EvidenceSnippet

#: The named upper bound on the retrieval fan-out ``k`` (§2). A request may ask for at
#: most this many snippets; a larger ``k`` is rejected before it reaches the retriever.
K_MAX: int = 20


class EvidenceSearchRequest(BaseModel):
    """A bounded evidence request (§2). ``query`` is 1–180 chars; ``1 ≤ k ≤ K_MAX``."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=180)
    k: int = Field(ge=1, le=K_MAX)


class EvidenceSearchResponse(BaseModel):
    """The ranked evidence-search result (§2).

    ``items`` are the ranked snippets; ``corpus_version`` pins the exact ingested build
    they came from; ``correlation_id`` ties the response to the originating request.
    """

    model_config = ConfigDict(extra="forbid")

    items: list[EvidenceSnippet] = Field(default_factory=list)
    corpus_version: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
