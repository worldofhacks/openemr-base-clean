"""Single integration seam for the independently built B2 extraction worker.

B2 exposes the same canonical callable alias. Integration replaces/injects this
dependency with ``run_extraction_worker``; no B2 model or persistence API is
duplicated here.

Traceability: W2-D2/W2-D3; W2_ARCHITECTURE.md §2/§3.
"""

from __future__ import annotations

from typing import Any

from app.orchestrator.workers.contracts import WorkerCallable
from app.schemas.workers import WorkerInput, WorkerOutput


def build_extraction_worker(pipeline: Any) -> WorkerCallable:
    """Bind B2's injected ``ExtractionPipeline`` to the canonical graph callable.

    The import is intentionally lazy so this independently committed B3 unit remains
    importable until the B2 branch is merged. There is one implementation of the B2
    pipeline contract: ``workers.intake_extractor``.
    """

    async def run(payload: WorkerInput) -> WorkerOutput:
        from app.orchestrator.workers.intake_extractor import (  # type: ignore[import-not-found]
            run_intake_extractor,
        )

        output = await run_intake_extractor(payload, pipeline=pipeline)
        if not isinstance(output, WorkerOutput):
            output = WorkerOutput.model_validate(output)
        if output.correlation_id != payload.correlation_id:
            raise ValueError("extraction worker correlation mismatch")
        return output

    return run
