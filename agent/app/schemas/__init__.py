"""Canonical Pydantic v2 schema inventory (W2_ARCHITECTURE.md §2; W2-M6).

The SINGLE source of truth for every extraction, citation, handoff, document,
retrieval, job, writeback, worker, and boundary contract in the W2 agent. Every
model is strict (``extra="forbid"``) and no task may improvise a parallel shape —
the M4 reader's ``NormBBox`` and the M3 orchestrator's ``HandoffRecord`` family are
UNIFIED here (their former homes re-export these class objects by identity).

Composition rule (§2, safety-critical): every leaf clinical value is a
``GroundedField[T]``; ``grounded=True`` requires a complete ``CitationV2`` AND a
``NormBBox``; ``grounded=False`` forbids a citation, renders UNSUPPORTED, and must
never write as fact.

@package   OpenEMR — Clinical Co-Pilot agent
@link      https://www.open-emr.org
@author    Claude Code
@copyright Copyright (c) 2026 OpenEMR contributors
@license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
"""
