# Retrieval lane

This is the data/runtime handoff for `POST /evidence/search` (W2-D4, W2-R2/R3,
W2_ARCHITECTURE §2/§4/§5). The endpoint contract is available as the mergeable fragment
[`openapi/evidence.yaml`](openapi/evidence.yaml), and
[`bruno/evidence-search.bru`](bruno/evidence-search.bru) is the secret-free sample request.

## VA/DoD corpus and provenance

`corpus/manifest.json` pins six VA/DoD artifacts: the 2023 Diabetes CPG and Quick
Reference Guide, the 2020 Hypertension CPG and Pocket Card, and the 2025 Lipids CPG and
Pocket Card. Each manifest entry records its source URL, version, source-PDF SHA-256,
included page ranges, exclusions, and verified license status. The cited VA copyright
policy describes these VA-produced materials as U.S. Government works that are not
copyright protected. Diabetes Version 6.0 has a Quick Reference Guide rather than a
current artifact titled Pocket Card; the obsolete 2017 pocket card is excluded.

Only recommendation and management text is indexed. Methodology/evidence-review
appendices, references, embedded figures, and third-party adapted material are excluded;
the committed corpus is text-only. A source whose license cannot be verified is excluded
rather than investigated or ingested.

## Rebuild and verify the index

From `agent/`:

```bash
python -m pip install -r corpus/requirements.txt
python -m corpus.build
python -m corpus.check_index_manifest
```

The build recreates BM25 plus the pinned `bge-small-en-v1.5` FastEmbed/ONNX dense index.
The final command fails if the chunks, dense bytes, dimensions, or index metadata do not
match the corpus manifest hash. A reviewed source-version refresh first runs
`python -m corpus.extract_sources --input-dir /path/to/pinned-pdfs`; it refuses PDFs that
do not match the manifest-pinned hashes.

## Reranker seam and degradation

`RERANKER=local` selects the pinned `mxbai-rerank-base-v1` ONNX fallback.
`RERANKER=cohere` selects Cohere v2 and reads `COHERE_API_KEY` from the process environment
only. Never put the key in source, Bruno, fixtures, logs, or command arguments. Tests stub
Cohere and CI never calls it live. If Cohere is unavailable, the local path is attempted;
if reranking remains unavailable, normalized hybrid results are returned in the same
`EvidenceSearchResponse` envelope and the degraded condition is recorded internally.

## PHI-free query contract

Queries contain condition/test terms only, for example `type 2 diabetes; HbA1c`; comma,
semicolon, and vertical-bar separators are supported. Do not send names, identifiers,
dates of birth, contact details, demographics, free-text notes, or document excerpts.
The route strips surrounding whitespace, rejects nonconforming terms, caps the query at
180 characters and `k` at 10, and applies the outbound PHI screen before any Cohere call.
