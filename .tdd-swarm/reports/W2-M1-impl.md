# W2-M1 implementation report — Day-1 container spike

Ticket: `tickets/W2-M1.md` · Branch: `ticket/w2-m1-container-spike` · Freeze: `cdeed28`
Date: 2026-07-14 (all times UTC)

## Verdict summary — the go/no-go table

| Measure | Value | Source |
|---|---|---|
| Railway plan memory limit (measured, cgroup v2 `memory.max`) | **32,000,000,000 bytes = 30,518 MB (~32 GB)** | in-container `railway ssh`, deployed service `agent` |
| **W2_WAVE0_RSS_CEILING_MB** = floor(0.8 × limit) | **24,414 MB** | computed by `ops/spike_rss.py` in-container |
| Cold RSS (probe process, before models) | 22 MB | Railway full-probe run |
| **Peak RSS — full concurrent load, fp32 reranker** | **2,494 MB** | Railway full-probe run (canonical) |
| Peak RSS — quantized reranker variant (ladder step 1 reference) | 2,068 MB | Railway `--quantized` run |
| **Ceiling verdict** | **PASS** (2,494 < 24,414; ~9.8× headroom) — **no ladder step invoked** | probe verdict line |
| Image size delta (local builds, arm64) | 369 MB → **809 MB (+440 MB)** — no models baked | `docker images` w1-baseline vs w2m1-spike |
| Cold-start (`railway up` → first `/health` 200) | **~61 s** (07:19:10.5 upload → 07:20:11.9 healthy); container-start → serving ≈ **1 s** | Railway deploy logs (timestamped) |
| Railway builder verdict | **GREEN** — no dep rejected; healthcheck succeeded on attempt 1/1 | build log, deployment `990a5064` SUCCESS |
| Rollback | **Not needed** — service never left `/health` green | repeated curl checks |

## What changed (file scopes only)

- **`agent/pyproject.toml`** — added `pypdfium2>=5.11,<6`, `pdfplumber>=0.11,<0.12`,
  `pytesseract>=0.3.13`, `fastembed>=0.8,<0.9` (brings `onnxruntime` 1.27, MIT). The
  pre-staged `langgraph` line untouched. License allowlist justification comments added
  for pillow (`MIT-CMU`/HPND family, via pdfplumber) and tqdm (`MPL-2.0 AND MIT` dual,
  via fastembed). `rank-bm25` NOT added — the probe's BM25 index is stdlib-only.
- **`agent/Dockerfile`** — apt layer `tesseract-ocr` + `tesseract-ocr-eng`
  (`--no-install-recommends`, apt lists cleaned); ships the probe
  (`COPY ops/__init__.py ops/spike_rss.py ./ops/` — not `ops/tests`). W1 boot CMD
  untouched. **Bake-vs-download decision: models are NOT baked** — see Decisions.
- **`agent/ops/spike_rss.py`** (NEW) — operator CLI capacity probe (ops may print per
  gates.md). Reads cgroup v2 `memory.max` (v1 fallback), then concurrently holds
  (a) bge-small-en-v1.5 under fastembed, (b) mxbai-rerank-base-v1 (custom ONNX
  registration), (c) a synthetic non-PHI hybrid index (600 chunks: float32 600×384
  embedding matrix + stdlib BM25 token index), (d) one 200-DPI Tesseract OCR page on a
  generated synthetic image — while issuing one HTTP request to the local app — sampling
  cold RSS and peak RSS (`/proc/self/status` VmRSS/VmHWM; macOS `getrusage` fallback).
  Flags: `--quick` (no downloads/HTTP — docker smoke), `--quantized` (measures ladder
  step 1 only; does not implement it), `--chunks`, `--app-url`, `--cache-dir`.
- **`agent/railway.json`** — unchanged (healthcheck config still correct; boot path
  unchanged so no timeout adjustment needed).
- `.tdd-swarm/reports/W2-M1-impl.md` — this report.

## PROMINENT SPIKE FINDINGS

1. **`mxbai-rerank-base-v1` is NOT in fastembed's built-in cross-encoder list** —
   `TextCrossEncoder.list_supported_models()` offers only Xenova/ms-marco-MiniLM-L-6/12-v2
   (Apache-2.0), BAAI/bge-reranker-base (MIT), jina-reranker-v1-tiny/turbo (Apache-2.0),
   jina-reranker-v2-base-multilingual (CC-BY-NC — non-permissive, unusable).
   **However, the pinned model IS shippable torch-free with no substitution**: the HF repo
   `mixedbread-ai/mxbai-rerank-base-v1` is Apache-2.0 and ships its own ONNX artifacts
   (`onnx/model.onnx` 738 MB fp32; `onnx/model_quantized.onnx` 244 MB), and fastembed
   0.8.0 exposes `TextCrossEncoder.add_custom_model()` which loads it (verified: loads +
   correctly ranks a relevant doc above an irrelevant one, locally and on Railway).
   **W2-D4 rev impact:** the retrieval feature track (W2-M5/W2-M6 or wherever the
   reranker seam lands) must call `add_custom_model()` at composition-root init before
   constructing `TextCrossEncoder` — a registration step the architecture text does not
   currently mention. No plan change beyond that one line; capacity was measured with the
   REAL pinned model, not a stand-in.
2. **fp32 reranker RSS is ~2.3 GB resident — 3× its on-disk size.** `after_reranker`
   RSS jumps 258 → 2,329 MB on Railway (2,414 MB locally). Fine under a 32 GB plan
   (PASS, 9.8× headroom), but this number is the one to watch if the plan is ever
   downsized: on a 2 GB plan the fp32 artifact ALONE would breach the 80% ceiling, and
   ladder step 1 (quantized artifact, measured peak 2,068 MB, `after_reranker` 1,897 MB)
   would NOT be sufficient — step 2 (raise memory) would be immediate. Record for W2-O1.
3. **Railway plan limit is 32 GB (Pro), not a small hobby limit** — the architecture's
   memory-budget anxiety (§6 W2-O1) has enormous slack today: ceiling 24,414 MB vs
   6,000–8,000 MB projected W2 worst case. The quantize→raise-memory→externalize ladder
   exists but nothing on the measured curve approaches it.
4. **Railway builder accepted every native dep first try** (pdfium manylinux wheel,
   onnxruntime, apt tesseract layer) — the "builder rejects the image" risk in the ticket
   did not materialize. Build ~59 s, healthcheck attempt 1.
5. **Model download at first use is fast in-region on Railway**: bge-small ~2.4 s
   cold-inclusive; fp32 reranker fetch+load 7.4 s. Baking models into the image is not
   needed for cold-start reasons at current sizes (it WOULD add ~1 GB image and slow
   every deploy). Revisit only if HF availability becomes a serving-path dependency
   concern in the W2-D4 feature wave (an image-bake or volume cache are the obvious
   options; decision deferred, current choice recorded below).

## Decisions

- **ONNX models NOT baked into the image** (runtime download at first use).
  Rationale: image stays 809 MB instead of ~1.8 GB; the W1 serving path never loads
  models so boot/healthcheck are unaffected (container-start→serving stayed ~1 s);
  measured on-Railway fetch+load is seconds. Trade-off recorded: first W2 model use per
  fresh container pays a one-time download (seconds on Railway network) and depends on HF
  availability — feature waves should init models at startup (not per-request) and may
  revisit bake/volume-cache.
- **BM25 side of the probe index is stdlib-only** — `rank-bm25` was pre-authorized but
  not needed; no new dep.
- **Reranker registered via `add_custom_model`, fp32 `onnx/model.onnx` as the canonical
  measured artifact** (it is the architecture-pinned model's default artifact);
  `--quantized` measured as ladder-step-1 evidence only.
- **SSH key registered with Railway** (`w2m1-spike-key`, the host's existing
  `id_ed25519.pub`) — required for `railway ssh`; left registered for future ops use.

## AC-by-AC evidence

### AC-1 / AC-2 (frozen tests)
All 7 new frozen tests green; suite `243 passed, 6 skipped` (baseline 238/5 — the 6th
skip is the opt-in playwright UI smoke self-deselecting in this venv, present pre-impl).
`pip check` clean in venv AND deployed container; torch absent in venv AND deployed
container (checked live: `importlib.util.find_spec('torch') is None`).

### AC-3 [live-measure] — builds + deploy + health
- Local: `docker build -t w2m1-spike agent/` → success (arm64 host).
- **Plan-trace substitution note (required by ticket):** the plan's "CI build stage
  passes with the new image" cannot run under the never-push rule — AC-3's local
  `docker build agent/` + the Railway builder verdict substitutes for it (gates.md
  Tier-2 "Container build").
- Railway: `railway up -d -y` from `agent/` at 07:19:10Z → deployment
  `990a5064-b1cd-44c9-b7d2-c2b0cc2a92fc` status SUCCESS. Build log shows
  `[6/7] RUN pip install` installing all W2 deps (no torch anywhere in the resolved
  set) and `[7/7] COPY ops/...`; healthcheck `[1/1] Healthcheck succeeded!`.
- `/health` green repeatedly post-deploy and again post-probe (5× then 3× HTTP 200);
  body `{"status":"alive"}`. Prior good deployment `2be68f43-…` recorded for rollback;
  rollback never needed.

### AC-4 [live-measure] — in-container dep checks (via `railway ssh`)
- `tesseract --version` → `tesseract 5.5.0 / leptonica-1.84.1`.
- `tesseract --list-langs` → `eng`, `osd`.
- pypdfium2 renders a fresh page at 200 DPI → `(1700, 2200) RGB`.
- bge-small loads under fastembed and embeds → `dim=384`, 2.4 s cold-inclusive.

### AC-5 [live-measure] — capacity probe (canonical Railway run)
`railway ssh -- sh -c "cd /app && python -m ops.spike_rss"`:

```
plan_memory_limit_mb: 30518        (cgroup v2 memory.max = 32000000000)
W2_WAVE0_RSS_CEILING_MB: 24414
cold_rss_mb: 22
peak_rss_mb: 2494                  (fp32 onnx/model.onnx)
stage_rss_mb: bm25 38 → +embed model 258 → +reranker 2329
concurrent: http_status 200, ocr_chars 1951 (200-DPI page), rerank_top1 ok
errors: []                         VERDICT: PASS
```
Quantized variant (ladder step 1 reference, measured not implemented): peak 2,068 MB,
`after_reranker` 1,897 MB, PASS. Local docker cross-check (4 GB limit, arm64): fp32 peak
2,582 MB PASS; quick mode under `--memory 512m` read the 512 MB limit and PASSed at
51 MB peak. Failure path (not invoked): quantize → raise Railway memory → externalize
index.

### AC-6 [live-measure] — image size + cold start
- New image 809 MB vs W1-baseline 369 MB → **+440 MB** (apt tesseract layer + ONNX/
  imaging wheels; zero model weights). Comparison source: the prior Railway image is not
  retained locally, so the baseline was built from the freeze commit's
  Dockerfile/pyproject/app (`git archive cdeed28`) on the same host/arch — an
  apples-to-apples local pair; Railway does not expose compressed image size via CLI.
- Cold start: upload 07:19:10Z → deployment created 07:19:11.5Z → container start
  07:20:10.9Z → app serving + first `/health` 200 at 07:20:11.9Z. **Deploy-to-healthy
  ≈ 61 s; container-boot-to-serving ≈ 1 s** (unchanged from W1 behavior — models not
  loaded at boot).

## License verification (DoD)

pypdfium2 `BSD-3-Clause, Apache-2.0` · pdfplumber MIT · pdfminer.six MIT · pytesseract
Apache-2.0 · fastembed Apache-2.0 · onnxruntime MIT · tokenizers Apache-2.0 ·
huggingface-hub Apache-2.0 · numpy BSD-3 (+0BSD/MIT/Zlib/CC0 components) · loguru MIT ·
mmh3 MIT · py-rust-stemmers MIT (LICENSE file + upstream repo; wheel metadata field
empty) · cffi MIT-0 · pycparser BSD-3 · cryptography Apache-2.0 OR BSD-3 · filelock MIT ·
fsspec BSD-3 · flatbuffers Apache-2.0 · hf-xet Apache-2.0 · protobuf BSD-3.
**Allowlisted with justification comments in pyproject:** pillow 12.3.0 `MIT-CMU`
(HPND family, per ticket DoD example); tqdm `MPL-2.0 AND MIT` (dual-licensed, file-level
weak copyleft, unmodified wheel). Debian layer: tesseract-ocr/tesseract-ocr-eng
Apache-2.0, leptonica Leptonica-License (permissive). **No GPL/AGPL anywhere; PyMuPDF
absent; no torch** (frozen guard + live container check).

## Gates

`bash .tdd-swarm/run-local-gates.sh tickets/W2-M1.md cdeed28` — ALL GATES PASS
(syntax, unit-tests 243 passed/6 skipped, frozen-tests, spec-lint with AC-3..6 exempt as
live-measure, no-todos, no-debug, no-skip-markers). Frozen test files untouched since
`cdeed28` (`git diff cdeed28..HEAD -- agent/tests/` empty).

## Secrets / PHI

No secret values read, printed, or committed (Railway env values never queried; local
docker smoke used synthetic placeholder env). All probe data synthetic and non-clinical
(seeded word-salad vocabulary; generated image; no names/dates/identifiers).
