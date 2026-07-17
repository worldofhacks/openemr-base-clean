"""Refresh/check metadata-only Tier-1 recording bindings without provider calls.

The index contains fixture and contract hashes plus deterministic selector policies. It
never contains prompts, transcripts, model prose, document text, or extracted values.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from evals.execution import (
    PARSER_VERSION,
    RECORDED_MODEL,
    SANITIZER_VERSION,
    fixture_sha256,
    prompt_hash,
    schema_hash,
)
from evals.golden_loader import DEFAULT_MANIFEST, load_golden_cases
from evals.recorded_executor import (
    ANSWER_REPLAY_VERSION,
    DEFAULT_RECORDINGS,
    _recording_digest,
    answer_context_schema_hash,
    answer_prompt_hash,
    answer_question_hash,
    answer_tool_schema_hash,
)


def build_recording_index(*, manifest_path: str | Path = DEFAULT_MANIFEST) -> dict[str, object]:
    recordings: list[dict[str, object]] = []
    source_anchor_by_hash: dict[str, str] = {}
    for case in load_golden_cases(manifest_path):
        fixture_hash = fixture_sha256(case.fixture_path)
        source_anchor = source_anchor_by_hash.setdefault(
            fixture_hash, f"fixture:{case.case_id}"
        )
        entry: dict[str, object] = {
            "case_id": case.case_id,
            "fixture_sha256": fixture_hash,
            "prompt_hash": prompt_hash(),
            "tool_schema_hash": schema_hash(case.doc_type),
            "answer_prompt_hash": answer_prompt_hash(),
            "answer_question_hash": answer_question_hash(),
            "answer_context_schema_hash": answer_context_schema_hash(),
            "answer_tool_schema_hash": answer_tool_schema_hash(),
            "answer_replay_version": ANSWER_REPLAY_VERSION,
            "model": RECORDED_MODEL,
            "sanitizer_version": SANITIZER_VERSION,
            "parser_version": PARSER_VERSION,
            # Content-addressed duplicate uploads retain the opaque document id assigned
            # to the first occurrence, matching both the production repository and the
            # live executor's ``source_id_by_hash`` behavior.
            "source_document_anchor": source_anchor,
            "page_selector": "all-readable-pages",
            "bbox_selector": "label-value-lines",
        }
        entry["recording_sha256"] = _recording_digest(entry)
        recordings.append(entry)
    return {"version": 2, "recordings": recordings}


def _serialized_index(*, manifest_path: str | Path) -> str:
    return json.dumps(
        build_recording_index(manifest_path=manifest_path),
        indent=2,
        ensure_ascii=True,
    ) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_RECORDINGS)
    args = parser.parse_args(argv)

    expected = _serialized_index(manifest_path=args.manifest)
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8") != expected:
            raise SystemExit("recording bindings are stale; run make record-evals")
        return 0
    args.output.write_text(expected, encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the CLI tests
    raise SystemExit(main())
