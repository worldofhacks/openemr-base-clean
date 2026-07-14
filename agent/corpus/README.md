# VA/DoD guideline corpus (Track B)

This directory is the rebuildable, text-only W2-M13/W2-M14 corpus and index defined by
W2-D4 and supported by W2-R2/R3. It contains only public U.S.-government guideline text;
no patient data, PHI, secrets, logos, or embedded figures are present.

## Scope and curation

The corpus pins the VA/DoD Diabetes (Version 6.0, 2023), Hypertension (Version 4.0,
2020), and Lipids (Version 5.0, 2025) guidelines. Full-guideline input is limited to
recommendation/management sections, management appendices, and the VA-authored text
alternatives for graphical algorithms. Research-priority, methodology,
evidence-review, evidence-table, search-strategy, participant, abbreviation, and reference
appendices are excluded. The current Diabetes release has no artifact titled "Pocket
Card"; its official Version 6.0 Quick Reference Guide is the compact clinician artifact.
The outdated Version 5.0/2017 Diabetes Pocket Card is explicitly excluded in the manifest.

The HTN office/home measurement blocks and adapted measurement tables from an AHA
publication are conservatively excluded, as are the matching full-guideline appendices. Lipids
appendices with reproduced/adapted third-party tables are also excluded.

The VA copyright policy states that government-produced materials on VA websites are not
copyright protected. The build nevertheless strips every embedded image/figure by using
committed text-only JSONL; it retains only verbatim normalized text spans and records the
policy URL and source PDF hash per document.

## Deterministic build

From `agent/`:

```bash
python -m pip install -r corpus/requirements.txt
python -m corpus.build
python -m corpus.check_index_manifest
pytest -q corpus/tests
```

`build.py` recreates `chunks.jsonl` and `index/dense.f32` from the committed source JSONL.
It downloads only the pinned `qdrant/bge-small-en-v1.5-onnx-q` snapshot at revision
`52398278842ec682c6f32300af41344b1c0b0bb2`, then runs it through FastEmbed/ONNX.
The standalone integrity command checks manifest, chunks, dense bytes, dimensions, and
corpus-version hashes; any mismatch exits nonzero.

To refresh the curated text after an explicit reviewed version change, download the six
manifest-pinned PDFs outside the repo and run:

```bash
python -m corpus.extract_sources --input-dir /path/to/pinned-pdfs
```

The extractor verifies all PDF hashes before writing. A refresh requires reviewing source
versions, page ranges, licenses, manifest hashes/counts, and the resulting diff; it is not a
runtime network path.

## Parallel-integration handoff

This isolated lane does not own shared files. The integration owner must add the runtime
requirements to `agent/pyproject.toml`, copy/build `agent/corpus/` in `agent/Dockerfile`,
include `app.routes.evidence.router` in `app/main.py`, and connect retrieval health to
`/ready`. No workaround for those merge points is hidden here.
