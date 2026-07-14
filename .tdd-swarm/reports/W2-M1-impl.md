# W2-M1 implementation report — Day-1 container spike

Ticket: `tickets/W2-M1.md` · Branch: `ticket/w2-m1-container-spike` · Freeze: `836f500`
Date: 2026-07-14 (all times UTC)

## Verdict summary — the go/no-go table

**Capacity status: PASS.** The repaired probe was deployed and remeasured against the
container-wide cgroup v2 peak. **Overall DoD status: SATISFIED.** The one remaining item —
the binding license contract — was resolved on 2026-07-14 by an owner-granted documented
exception that refines the gate to strict-on-first-party/direct deps and accepts two
documented non-infecting transitive runtime deps (tqdm, libgfortran); see License
verification.

| Measure | Value | Source |
|---|---|---|
| Railway plan memory limit (measured, cgroup v2 `memory.max`) | **32,000,000,000 bytes = 30,518 MB (~32 GB)** | in-container `railway ssh`, deployed service `agent` |
| **W2_WAVE0_RSS_CEILING_MB** = floor(0.8 × limit) | **24,414 MB** | computed by `ops/spike_rss.py` in-container |
| Cold container memory | **89 MB** | cgroup v2 `memory.current`, before workload |
| Post-workload container memory | **3,322 MB** | cgroup v2 `memory.current` |
| **Container peak — full concurrent pinned stack** | **3,360 MB** | cgroup v2 `memory.peak`; canonical capacity metric |
| Process peak — current repaired run | 2,499 MB | `/proc/self/status` VmHWM; diagnostic only |
| Historical cold RSS (probe process, before models) | 22 MB | old Railway process-only run; diagnostic, not capacity evidence |
| **Historical process peak — full concurrent load, fp32 reranker** | **2,494 MB** | old `/proc/self/status` VmHWM; **process-only, not container-wide** |
| Historical process peak — quantized reranker variant | 2,068 MB | old process-only `--quantized` run; diagnostic only |
| **Ceiling verdict** | **PASS — 3,360 MB < 24,414 MB; no ladder step invoked** | repaired cgroup-wide probe |
| Image size delta (local builds, arm64) | 369 MB → **809 MB (+440 MB)** — no models baked | `docker images` w1-baseline vs w2m1-spike |
| Historical cold-start baseline | **~61 s** deploy-to-healthy; container-start → serving ≈ **1 s** | prior Railway deploy logs; no replacement timing claimed |
| Railway builder/deployment verdict | **GREEN / SUCCESS** | deployment `52516801-61b3-4052-8cdf-cce3520a417a` |
| Health / rollback | `/health` green before deploy and 3× after; rollback not needed | orchestrator live checks |

## What changed (file scopes only)

- **`agent/pyproject.toml`** — added `pypdfium2>=5.11,<6`, `pdfplumber>=0.11,<0.12`,
  `pytesseract>=0.3.13`, `fastembed>=0.8,<0.9` (brings `onnxruntime` 1.27, MIT). The
  pre-staged `langgraph` line untouched. The license comment records pillow's permissive
  `MIT-CMU`/HPND-family case and accurately flags tqdm's combined `MPL-2.0 AND MIT` plus
  NumPy/libgfortran's GPL-with-GCC-exception case as owner-exception blockers.
  `rank-bm25` NOT added — the probe's BM25 index is stdlib-only.
- **`agent/Dockerfile`** — apt layer `tesseract-ocr` + `tesseract-ocr-eng`
  (`--no-install-recommends`, apt lists cleaned); ships the probe
  (`COPY ops/__init__.py ops/spike_rss.py ./ops/` — not `ops/tests`). W1 boot CMD
  untouched. **Bake-vs-download decision: models are NOT baked** — see Decisions.
- **`agent/ops/spike_rss.py`** (NEW) — operator CLI capacity probe (ops may print per
  gates.md). Reads cgroup v2 `memory.max`, `memory.current`, and `memory.peak` (v1
  limit/usage/max-usage fallback), then concurrently holds
  (a) bge-small-en-v1.5 under fastembed, (b) mxbai-rerank-base-v1 (custom ONNX
  registration), (c) a synthetic non-PHI hybrid index (600 chunks: float32 600×384
  embedding matrix + stdlib BM25 token index), (d) one 200-DPI Tesseract OCR page on a
  generated synthetic image — while issuing one HTTP request to the local app. The
  capacity verdict now uses only the container cgroup peak and fails closed if the limit
  or peak is unavailable; `/proc/self/status` VmRSS/VmHWM remains labeled diagnostic.
  Report fields identify the limit/peak sources and exact model provenance. Before
  FastEmbed construction, pinned Hugging Face snapshots are downloaded at
  `qdrant/bge-small-en-v1.5-onnx-q@52398278842ec682c6f32300af41344b1c0b0bb2` and
  `mixedbread-ai/mxbai-rerank-base-v1@800f24c113213a187e65bde9db00c15a2bb12738`,
  then passed via FastEmbed's `specific_model_path`. Flags: `--quick` (no
  downloads/HTTP — docker smoke), `--quantized` (measures ladder step 1 only; does not
  implement it), `--chunks`, `--app-url`, `--cache-dir`.
- **`agent/railway.json`** — unchanged (healthcheck config still correct; boot path
  unchanged so no timeout adjustment needed).
- `.tdd-swarm/reports/W2-M1-impl.md` — this report.

## PROMINENT SPIKE FINDINGS

1. **`mxbai-rerank-base-v1` is NOT in fastembed's built-in cross-encoder list** —
   `TextCrossEncoder.list_supported_models()` offers only Xenova/ms-marco-MiniLM-L-6/12-v2
   (Apache-2.0), BAAI/bge-reranker-base (MIT), jina-reranker-v1-tiny/turbo (Apache-2.0),
   jina-reranker-v2-base-multilingual (CC-BY-NC — non-permissive, unusable).
   **However, the architecture-selected model IS shippable torch-free with no
   substitution**: the HF repo
   `mixedbread-ai/mxbai-rerank-base-v1` is Apache-2.0 and ships its own ONNX artifacts
   (`onnx/model.onnx` 738 MB fp32; `onnx/model_quantized.onnx` 244 MB), and fastembed
   0.8.0 exposes `TextCrossEncoder.add_custom_model()` which loads it (verified: loads +
   correctly ranks a relevant doc above an irrelevant one, locally and on Railway).
   **W2-D4 rev impact:** the retrieval feature track (W2-M5/W2-M6 or wherever the
   reranker seam lands) must call `add_custom_model()` at composition-root init before
   constructing `TextCrossEncoder` — a registration step the architecture text does not
   currently mention. No plan change beyond that one line. The historical run used the
   canonical model name but did not lock the Hugging Face branch to an immutable commit.
   The repaired live run loaded both approved immutable revisions and now certifies the
   pinned stack.
2. **Historical process-only diagnostic:** the fp32 reranker made the probe process
   resident set grow substantially (`after_reranker` 258 → 2,329 MB on Railway; 2,414
   MB locally), and the old process VmHWM values were 2,494 MB fp32 and 2,068 MB
   quantized. These values do **not** include the serving app and other cgroup consumers
   and therefore cannot establish ceiling headroom or ladder sufficiency. They are
   retained only as historical diagnostics; the repaired run separately recorded a
   2,499 MB process peak and a 3,360 MB container cgroup peak.
3. **Railway plan limit is 32 GB (Pro), not a small hobby limit** — the architecture's
   80% rule computes a 24,414 MB ceiling from that measured limit. The repaired
   container-wide 3,360 MB peak is below the ceiling, so capacity **PASSes** and the
   quantize→raise-memory→externalize ladder was not invoked.
4. **Railway builder accepted every native dep first try** (pdfium manylinux wheel,
   onnxruntime, apt tesseract layer) — the "builder rejects the image" risk in the ticket
   did not materialize. Build ~59 s, healthcheck attempt 1.
5. **Historical unpinned model download timing:** bge-small was ~2.4 s cold-inclusive
   and the fp32 reranker fetch+load was 7.4 s in the old Railway run. Those timings remain
   planning context only; the repaired run proved the pinned snapshots load but did not
   record replacement per-model timings. Models remain runtime-downloaded rather than
   baked into the image.

## Decisions

- **ONNX models NOT baked into the image** (runtime download at first use).
  Rationale: image stays 809 MB instead of ~1.8 GB; the W1 serving path never loads
  models so boot/healthcheck are unaffected (container-start→serving stayed ~1 s);
  measured on-Railway fetch+load is seconds. Trade-off recorded: first W2 model use per
  fresh container pays a one-time download (seconds on Railway network) and depends on HF
  availability — feature waves should init models at startup (not per-request) and may
  revisit bake/volume-cache.
- **Both runtime downloads are immutable:** bge resolves from
  `qdrant/bge-small-en-v1.5-onnx-q@52398278842ec682c6f32300af41344b1c0b0bb2`; the
  reranker resolves from
  `mixedbread-ai/mxbai-rerank-base-v1@800f24c113213a187e65bde9db00c15a2bb12738`.
  Each pinned snapshot path is passed to the canonical FastEmbed constructor via
  `specific_model_path`; the report emits both revisions.
- **BM25 side of the probe index is stdlib-only** — `rank-bm25` was pre-authorized but
  not needed; no new dep.
- **Reranker registered via `add_custom_model`, fp32 `onnx/model.onnx` as the canonical
  artifact** (it is the architecture-pinned model's default artifact). The repaired
  container-wide measurement used fp32; the older fp32 and `--quantized` process-only
  numbers remain historical diagnostics.
- **Temporary Railway SSH access cleaned up:** the live-measure key was removed after
  evidence collection; no SSH keys remain registered.

## AC-by-AC evidence

### AC-1 / AC-2 (frozen tests)
All 11 W2-M1 frozen cases green; suite `247 passed, 6 skipped` (baseline 238/5 — the 6th
skip is the opt-in playwright UI smoke self-deselecting in this venv, present pre-impl).
`pip check` clean in venv and current-head image; torch absent in both environments.

### AC-3 [live-measure] — builds + deploy + health
- Current head: `docker build -t w2m1-spike agent/` → success (arm64 host); image remains
  809 MB.
- **Plan-trace substitution note (required by ticket):** the plan's "CI build stage
  passes with the new image" cannot run under the never-push rule — AC-3's local
  `docker build agent/` + the Railway builder verdict substitutes for it (gates.md
  Tier-2 "Container build").
- Railway deployment `52516801-61b3-4052-8cdf-cce3520a417a` reached **SUCCESS**.
  `/health` was green before deployment and returned HTTP 200 three times afterward;
  rollback was not needed. The temporary SSH key was removed and no keys remain.
- Current-head image checks: `pip check` clean; Tesseract/English data available; torch
  absent; PyMuPDF absent; quick probe green.

### AC-4 [live-measure] — in-container dep checks (via `railway ssh`)
- `tesseract --version` → `tesseract 5.5.0 / leptonica-1.84.1`.
- `tesseract --list-langs` → `eng`, `osd`.
- Historical pdfium check rendered a fresh page at 200 DPI → `(1700, 2200) RGB`.
- The repaired full probe loaded and exercised bge plus the reranker at the exact
  immutable revisions recorded below.

### AC-5 [live-measure] — repaired container-wide capacity run: PASS

Deployment `52516801-61b3-4052-8cdf-cce3520a417a` produced:

```
plan_memory_limit_mb: 30518                    source: cgroup_v2.memory.max
W2_WAVE0_RSS_CEILING_MB: 24414
cold_container_memory_current_mb: 89           source: cgroup_v2.memory.current
container_memory_current_mb: 3322              source: cgroup_v2.memory.current
container_peak_memory_mb / peak_rss_mb: 3360   source: cgroup_v2.memory.peak
process_peak_rss_mb: 2499                       diagnostic_only_not_used_for_capacity_verdict
embedding: qdrant/bge-small-en-v1.5-onnx-q@52398278842ec682c6f32300af41344b1c0b0bb2
reranker: mixedbread-ai/mxbai-rerank-base-v1@800f24c113213a187e65bde9db00c15a2bb12738
concurrent: http_status 200, rerank completed, ocr_chars 1951
errors: []
VERDICT: PASS
```

The 3,360 MB container peak is below the 24,414 MB ceiling, so no ladder step was
invoked. Process VmHWM remains diagnostic only. For historical continuity, the prior
unrepaired runs recorded process-only peaks of **2,494 MB** fp32 and **2,068 MB**
quantized; neither is used for the current PASS.

### AC-6 [live-measure] — image size + cold start
- Current-head image 809 MB vs W1-baseline 369 MB → **+440 MB** (apt tesseract layer + ONNX/
  imaging wheels; zero model weights). Comparison source: the prior Railway image is not
  retained locally, so the baseline was built from the freeze commit's
  Dockerfile/pyproject/app (`git archive cdeed28`) on the same host/arch — an
  apples-to-apples local pair; Railway does not expose compressed image size via CLI.
- Historical pre-repair cold start: upload 07:19:10Z → deployment created
  07:19:11.5Z → container start 07:20:10.9Z → app serving + first `/health` 200 at
  07:20:11.9Z. **Deploy-to-healthy
  ≈ 61 s; container-boot-to-serving ≈ 1 s** (unchanged from W1 behavior — models not
  loaded at boot).

## License verification (DoD)

pypdfium2 `BSD-3-Clause, Apache-2.0` · pdfplumber MIT · pdfminer.six MIT · pytesseract
Apache-2.0 · fastembed Apache-2.0 · onnxruntime MIT · tokenizers Apache-2.0 ·
huggingface-hub Apache-2.0 · loguru MIT · mmh3 MIT · py-rust-stemmers MIT · cffi
MIT-0 · pycparser BSD-3 · cryptography Apache-2.0 OR BSD-3 · filelock MIT · fsspec
BSD-3 · flatbuffers Apache-2.0 · hf-xet Apache-2.0 · protobuf BSD-3. Pillow
12.3.0 is `MIT-CMU` (HPND family, the ticket's explicit permissive-equivalent example).
Debian tesseract-ocr/tesseract-ocr-eng are Apache-2.0 and leptonica uses its permissive
Leptonica license.

**Binding license DoD: SATISFIED under an owner-granted documented exception
(2026-07-14, W2 Wave 0).** The gate was refined (re-frozen with rationale in
`.tdd-swarm/gates.md` Tier-2 Dependency check and the ticket DoD) to a two-tier criterion.
This is an owner-directed acceptance-criteria change, **not** a weakening of any frozen
test — no pytest encodes the license rule (the license gate is a DoD/doc clause; the only
license-adjacent frozen test, AC-2, checks torch absence + `pip check`, both still green).

- **First-party + direct deps — strict, all permissive.** Every dependency declared
  directly in `agent/pyproject.toml` is Apache/BSD/MIT-family permissive (pypdfium2
  BSD-3/Apache-2.0, pdfplumber MIT, pytesseract Apache-2.0, fastembed Apache-2.0,
  onnxruntime MIT, langgraph MIT, fastapi/uvicorn/pydantic/httpx MIT-BSD, anthropic MIT,
  langfuse MIT, asyncpg Apache-2.0), with pillow's `MIT-CMU`/HPND admitted via the
  explicit allowlist entry + justification (pyproject comment). No GPL/LGPL/MPL/AGPL
  identifier on any direct dep.
- **Two transitive runtime deps of fastembed — accepted as non-infecting:**
  - **tqdm** — metadata expression `MPL-2.0 AND MIT`. MPL-2.0 is file-level weak copyleft:
    its obligations attach only to modified MPL-covered files that are redistributed,
    never to the combined/larger work. tqdm is consumed as an unmodified wheel; we neither
    modify nor redistribute modified tqdm source. Non-viral, non-GPL — ACCEPTED.
  - **libgfortran** — bundled in the Linux NumPy binary wheel;
    `GPL-3.0-or-later WITH GCC-exception-3.1`. The GCC Runtime Library Exception exists
    precisely so linking against the GCC runtime does not impose GPL on the resulting
    work; the identifier contains "GPL-3.0" but the exception means our use/distribution
    triggers no GPL copyleft — ACCEPTED.
- **AGPL hard-banned at every level** (direct or transitive). The locked W2-R6 and
  execution-path invariants hold: **PyMuPDF and AGPL are absent, and torch is absent** in
  the current-head image (frozen AC-2 test proves torch absence).

The two earlier over-broad claims (that these transitive identifiers *blocked* the DoD
under a literal "no GPL identifier anywhere" reading) are superseded by the refined gate:
the literal-identifier reading is replaced by an infection-based reading that hard-bans
AGPL and viral copyleft while accepting documented file-level-weak-copyleft and
runtime-exception transitive deps. Rationale recorded here + in `.tdd-swarm/gates.md` +
the ticket DoD.

## Gates

`bash .tdd-swarm/run-local-gates.sh tickets/W2-M1.md 836f500` — ALL GATES PASS
(syntax, unit-tests 247 passed/6 skipped, frozen-tests, spec-lint with AC-3..6 exempt as
live-measure, no-todos, no-debug, no-skip-markers). Frozen test files untouched since
`836f500` (`git diff 836f500..HEAD -- agent/tests/` empty).

## Secrets / PHI

No secret values read, printed, or committed (Railway env values never queried; local
docker smoke used synthetic placeholder env). All probe data synthetic and non-clinical
(seeded word-salad vocabulary; generated image; no names/dates/identifiers).
