"""Dynamic golden-manifest loader for W2-D5 §7."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evals.w2_models import GoldenCase


DEFAULT_MANIFEST = Path(__file__).parent / "golden" / "cases.json"


def load_golden_cases(path: str | Path = DEFAULT_MANIFEST) -> list[GoldenCase]:
    """Load and type every manifest entry in file order.

    There is intentionally no expected count and no allowlist of case IDs: appending
    an entry automatically adds it to the gate.  Fixture paths are data references;
    this loader does not read or scan canonical fixture inputs.
    """

    manifest_path = Path(path)
    raw: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("golden manifest must be a JSON array")

    cases = [GoldenCase.model_validate(entry) for entry in raw]
    seen: set[str] = set()
    for case in cases:
        if case.case_id in seen:
            raise ValueError(f"duplicate case_id: {case.case_id}")
        seen.add(case.case_id)
    return cases
