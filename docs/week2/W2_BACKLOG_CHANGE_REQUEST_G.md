# W2 Backlog — Change request G: document-understanding enhancements (tables, figures/graphs, image intake)

> **Relocated 2026-07-19** from W2_IMPLEMENTATION_PLAN.md §9 so non-audit feature work cannot
> compete with final-submission blockers. NOT part of the audit remediation scope; execute only
> after the submission tracks in the implementation plan. Corrections applied on relocation:
> the full-suite baseline is 936 passed / 5 skipped (verified 2026-07-19; the 951/2 figure was
> not reproducible), and the 50-case golden gate entrypoint is `evals.w2_runner` —
> `evals.runner` is the retired Week-1 10-case runner.

## 9. Change request G — document-understanding enhancements (tables, figures/graphs, image intake)

**Source of scope:** Owner direction, 2026-07-19. Original request: "replace Tesseract with PyMuPDF."
Phase-1/2 investigation (§9.1-§9.2) found the replacement neither necessary nor sufficient for the
stated capability gaps; the owner redirected to capability-first enhancements: "it does not handle
tables or graphs or pngs very well. it does great on pdfs, so maybe we can add something on top of
it or alongside it."

**Relationship to sections 1-8:** This section is a NON-AUDIT change request. It does not alter,
renumber, or reprioritize tasks A01/R01-R06/C01-C02/E01/O01-O03/S01, the closure checklist, or the
release verdict rule. G-tasks must not merge ahead of any open P0 gate work they would conflict with.

**Governing decisions:** W2-R6's LIBRARY SELECTION (pypdfium2 + pdfplumber + Tesseract) is kept on
capability grounds; its PyMuPDF/AGPL ban clause was REMOVED by owner decision **G-D2**
(2026-07-19, W2_DECISIONS.md — the ban was self-imposed at /arch-finalize, verified absent from
the AgentForge W2 PDF). Kept intact: W2-D3 (text-layer first, local Tesseract OCR fallback,
$0/no-egress grounding), §2 locked `RENDER_DPI = 200` (agent/app/ingestion/reader.py:62), and the
words+boxes NormBBox space.

### 9.1 Phase-1 inventory — every Tesseract usage in the repository

Case-insensitive repo-wide search (`tesseract`, `pytesseract`), 2026-07-19. No usage exists in the
OpenEMR PHP tree — all runtime usage is inside `agent/`. Function classes: (a) digital text
extraction, (b) true OCR of scanned/raster content, (c) image preprocessing, (d) availability
check/boot gate.

| Location | Usage class | Function |
|---|---|---|
| agent/app/ingestion/reader.py:48 | import | — |
| agent/app/ingestion/reader.py:128-130 | runtime call (`image_to_data`, Output.DICT) | (b) OCR fallback for junk/absent text-layer PDF pages |
| agent/app/ingestion/reader.py:307-376 | runtime (subprocess timeout harness around OCR) | (b) |
| agent/app/ingestion/image_reader.py:9-29 | runtime (reuses `_default_ocr_runner`) | (b) direct PNG/JPEG intake OCR |
| agent/evals/artifact_scan.py:24, 236-239 | eval call (`image_to_string`, timeout=5.0) | (b) PHI/secret scan of generated images |
| agent/ops/spike_rss.py:282-285 | ops probe (`image_to_string`) | (b) RSS capacity probe step |
| agent/tests/test_app_boot.py:126-142 | test | (d) boot gate: binary + traineddata resolvable |
| agent/tests/test_reader_geometry.py:110, 145, 191, 286, 448 | test | (b)/(d) OCR-path geometry, version-tolerant assertions (lines post-G-D2 edit) |
| agent/tests/test_w2_serving_integration.py:520 | test assertion | (d) Dockerfile must contain tesseract packages |
| agent/pyproject.toml:45 (comments 41-44, 59) | dependency (`pytesseract>=0.3.13`, Apache-2.0) | — |
| agent/Dockerfile:16-17 (comments 7-9) | system dep (`tesseract-ocr`, `tesseract-ocr-eng`) | — |
| .github/workflows/agent-quality.yml:45, 135 | CI system dep | — |
| .github/workflows/agent-eval-gate.yml:48, 122 | CI system dep | — |
| .github/workflows/agent-eval-live-subset.yml:45 | CI system dep | — |
| .gitlab-ci.yml:10 | CI system dep | — |
| agent/evals/fixtures/documents/generate_fixtures.py:18, 51, 176, 191, 200, 221 | fixture design comments | (b) fixtures give Tesseract "ink to read" |
| Docs/tickets/reports (references only): W2_ARCHITECTURE.md:28; tickets/W2-M1.md; tickets/W2-M4.md; docs/week2/{W2_PRESEARCH.md:193,211, W2_RESEARCH.md:124,180,188, W2_DECISIONS.md:98,109,428, W2_DEVLOG.md:59, W2_ARCHITECTURE_DRAFT.md:20,75, W2_DEFENSE_PREP.md:112}; .tdd-swarm/{gates.md, progress.md, reports/W2-M1-impl.md, reports/W2-M4-impl.md}; agent/W2_PIPELINE_RESUME.md:213 | docs | — |

**Class (a) does not exist:** digital text extraction is pdfplumber
(agent/app/ingestion/reader.py:202-245); rendering is pypdfium2 (reader.py:251-256). Tesseract is
exclusively the (b) OCR engine plus its (d) gates. Any "replacement" therefore targets only the OCR
role.

### 9.2 PyMuPDF evaluation and adoption decision (G-D1)

Facts verified against current upstream documentation, 2026-07-19:

- **Version:** PyMuPDF 1.28.0 (released 2026-06-29); pip package `pymupdf`, import `pymupdf`/`fitz`.
- **License:** dual AGPL-3.0 / Artifex commercial. AGPL's network clause applies to this agent
  (a served FastAPI application).
- **OCR reality:** PyMuPDF has no OCR engine of its own. `Page.get_textpage_ocr()` invokes the
  Tesseract engine embedded in its wheel and requires Tesseract language data
  (`TESSDATA_PREFIX` env or `tessdata` argument). A "full replacement" would still run Tesseract's
  engine for every scanned page, minus only the CLI binary.
- **Repo governance (as investigated — since superseded):** W2-R6 carried a hard AGPL/PyMuPDF ban
  with active enforcement (gates, pyproject comments, reader docstrings, and an AC-5 test that
  failed CI if `fitz` was importable). Owner decision **G-D2 (2026-07-19)** REMOVED the ban at
  every enforcement point after verifying the AgentForge W2 PDF imposes no dependency-license
  rule — see W2_DECISIONS.md G-D2; AC-5 is now `test_reader_deps_declare_license_metadata`
  (test_reader_geometry.py:402). Historical ban records stay in tickets/W2-M1.md:49,
  tickets/W2-M4.md:32, 150.

Gap-fit analysis against the owner's stated failures:

| Gap | Does PyMuPDF fix it? | Why |
|---|---|---|
| Tables in scanned pages / images | No | Its OCR is the same Tesseract engine; no table-structure output from OCR |
| Graphs/charts | No | Plotted values have no text to extract with any OCR engine |
| PNG/photo intake quality | No | Same engine; quality is a preprocessing/config problem |
| Tables in digital PDFs | Parity only | `page.find_tables()` ≈ pdfplumber `find_tables()` already installed (pyproject.toml:41) |
| Speed / single-library ops | Yes, partially | Real but not a stated gap; a migration cost with no payoff for these gaps (license obstacle removed by G-D2) |

**Decision G-D1 (recorded by G01): Tesseract is RETAINED and enhanced; PyMuPDF is NOT adopted —
on capability grounds alone.** With G-D2 (2026-07-19) having removed the license ban, no
governance obstacle remains; the swap is simply unjustified because every stated gap is
unaddressed by it while it still costs a reader/tests/Dockerfile/CI migration. Appendix G-B
preserves the complete, executable swap mechanics should a capability case ever emerge.

### 9.3 Why the gaps exist — architecture context for the fixes

Extraction is VLM-proposed and locally grounded: `AnthropicVlmExtractor` sends the document plus
the OCR words+boxes layer to the provider under a forced tool schema
(agent/app/llm/vlm.py:454-525); every proposed `GroundedField` is then verified against the local
words+boxes evidence by `GroundingVerifier.ground_value` (agent/app/grounding/verifier.py:1044-1059)
— "local deterministic grounding alone decides whether a proposed value becomes a located, cited
fact" (vlm.py:75-77). Ungrounded values must remain visible-but-uncited (W2-REQ-97, gap-audit row
docs/week2/W2_gap-audit.md:303).

Consequently, weak word evidence — not weak VLM reading — is what degrades tables, charts, and
photos:

- **Tables:** `_source_lines` reconstructs rows by clustering word-box y-midpoints with a 0.0045
  tolerance (vlm.py:215-239, 227). Multi-column and dense tables interleave into wrong rows, so
  completeness validation and value grounding mis-associate cells. On OCR pages,
  `_ocr_words_from_data` discards Tesseract's block/paragraph/line structure entirely
  (reader.py:259-287 keeps only text+box).
- **PNGs/photos:** `read_image_words_and_boxes` runs raw OCR with no preprocessing (EXIF transpose
  and RGB convert only), no upscaling, no binarization/deskew, no PSM tuning — and, unlike the PDF
  path, NO per-page subprocess timeout (direct `runner(image)` call, image_reader.py:27-28 vs
  reader.py:307-376). A runaway image OCR can hang a request.
- **Graphs/charts:** plotted values produce no words, so chart-derived claims can never ground —
  correct per W2-REQ-97, but today the system also cannot cite that a figure exists or ground its
  axis/legend text as context.

Requirements served: W2-REQ-91/92 (useful/safe on imperfect scans/incomplete records,
W2_gap-audit.md:297-298), W2-REQ-97/98 (grounding/citation posture, W2_gap-audit.md:303-304).
Distinct scope note: W2-REQ-45's lab trend chart (W2_gap-audit.md:251, AF-P2-04) is about the app
RENDERING a chart from extracted Observations — not reading charts — and stays in A01's
clarification lane.

### 9.4 Dependency order

    G01 ──► G02 ──┐
        ├─► G03 ──┼─► G05 ──► G06
        └─► G04 ──┘

G02/G03/G04 are mutually independent after G01. G05 consumes G02/G03's `TableBlock`/region schema.
G06 lands fixtures incrementally but its gate evidence closes last. No G-task blocks, or is blocked
by, any section-5 audit task; C01/C02 gate changes (if merged first) apply to G-task PRs like any
other.

### G01 — Record decision G-D1 and the G-scope guardrails

- **Findings and requirements:** Owner direction 2026-07-19; W2-R6; W2-D3; W2-REQ-94/95 (synthetic
  data, sensitive artifacts).
- **Objective and rationale:** Make the investigation outcome durable and bind the G-tasks to
  explicit guardrails so "enhance ingestion" cannot drift into a stack swap, a license change, or a
  grounding-posture change without a recorded decision.
- **Current evidence:** §9.1-§9.3 of this plan; no decision entry exists yet in
  docs/week2/W2_DECISIONS.md (Tesseract entries at lines 98, 109, 428 predate this request).
- **Files and systems:** docs/week2/W2_DECISIONS.md (append new entry; no existing entry edited),
  docs/week2/W2_DEVLOG.md (append).
- **Implementation:**
  1. Append decision **G-D1** to W2_DECISIONS.md: Tesseract retained + enhanced; PyMuPDF not
     adopted (capability grounds; the license ban is already gone — G-D2 recorded 2026-07-19);
     link §9.2 and Appendix G-B.
  2. Record open question **OPEN-G1** (owner answer required before G05 exceeds its default scope):
     may chart/figure-derived VLM claims ever pass verification with a REGION-level citation
     (figure bbox) instead of word-level grounding? Default until answered: NO — figure claims stay
     ungrounded-and-visible; only figure existence and axis/legend text are cited.
  3. Record guardrails binding G02-G06: every new runtime dependency's declared license documented
     in pyproject and covered by the AC-5 inventory test (test_reader_geometry.py:402 — no
     license-family ban per G-D2), no torch, no PHI egress beyond the existing Zone-C VLM call,
     `RENDER_DPI = 200` unchanged, image RSS bounded (G04/G06), recordings/fixture hashes
     regenerated whenever the VLM request payload changes.
- **Tests:** None (documentation task).
- **Verification:**

      rg -n "G-D1|OPEN-G1" docs/week2/W2_DECISIONS.md docs/week2/W2_DEVLOG.md

- **Acceptance:** Decision + open question + guardrails appear verbatim in W2_DECISIONS.md; DEVLOG
  entry links this section; no existing decision text modified.
- **Dependencies and blockers:** None.
- **Risks and rollback:** None; append-only documentation.
- **Effort:** 0.5 day.

### G02 — Typed table extraction on digital text-layer pages (pdfplumber, zero new deps)

- **Findings and requirements:** Owner gap "does not handle tables"; W2-REQ-91/97/98.
- **Objective and rationale:** Emit table structure (cells with text + NormBBox) from the ALREADY
  INSTALLED pdfplumber (pyproject.toml:42) so row/label/value association is structural instead of
  y-midpoint clustering, and table values ground per-cell.
- **Current evidence:** Reader emits flat words only (reader.py:91-118 `Word`/`PageWords`);
  `_source_lines` y-clustering mis-rows dense tables (vlm.py:215-239); no table API is used
  anywhere (`rg -n "find_tables|extract_tables" agent/` returns nothing).
- **Files and systems:** NEW agent/app/ingestion/tables.py; agent/app/schemas/extraction.py
  (additive `TableCell`/`TableBlock` frozen models); agent/app/ingestion/reader.py (populate);
  agent/app/llm/vlm.py (table-aware source lines); agent/app/grounding/verifier.py (cell-scoped
  candidate pass); NEW agent/tests/test_reader_tables.py.
- **Implementation:**
  1. `TableCell{row:int, col:int, text:str, bbox:NormBBox}` and
     `TableBlock{bbox:NormBBox, n_rows:int, n_cols:int, cells:tuple[TableCell,...], source:Literal["text_layer","ocr_reconstruction"]}`
     in app/schemas/extraction.py — frozen, extra="forbid", mirroring NormBBox conventions (§2).
  2. `PageWords` gains additive field `tables: tuple[TableBlock, ...] = ()` (reader.py:100-110).
     Additive with a default — every existing constructor call site and persisted payload stays
     valid; model_config stays frozen.
  3. app/ingestion/tables.py: `extract_text_layer_tables(plumber_page) -> list[TableBlock]` via
     `plumber_page.find_tables()` with the default lines strategy, then a second pass with
     `{"vertical_strategy": "text", "horizontal_strategy": "text"}` for borderless tables; cell
     text via `Table.extract()`; cell/table bboxes normalized exactly as words are —
     divide by `plumber_page.width/height`, top/bottom already y-down (reader.py:205-206, 236-242),
     clamp via the shared `_make_bbox` (reader.py:172-180).
  4. Emission filter (false-positive guard): keep a candidate only if `n_rows >= 2`, `n_cols >= 2`,
     and ≥60% of non-empty cells intersect at least one extracted `Word` box — cells must be
     word-substantiated, since grounding evidence remains the word layer.
  5. Populate in `_read_open_documents` for text-layer pages only (reader.py:442-454);
     OCR pages get tables from G03.
  6. vlm.py: build source lines table-aware — for each `TableBlock`, emit one line per row as
     `"<row-label>: <cell values in column order>"` ahead of the residual (non-table) word
     clustering; words inside an emitted table's bbox are excluded from `_source_lines` clustering
     to prevent duplicate/interleaved rows. Lab/intake completeness validators
     (vlm.py:285-335, 338-399) consume the same `label: value` shape and are asserted unchanged.
  7. verifier.py: in `ground_value`, try an exact cell-text match (normalized) within `TableBlock`
     cells FIRST; on match, ground with the cell bbox and cite the cell; else fall back to the
     existing word search. Never ground a value absent from both cells and words.
  8. The serialized `ocr_layer` (vlm.py:472 `words_boxes.model_dump_json()`) now carries `tables`.
     Static prompt strings are unchanged, so `VLM_PROMPT_HASH` (vlm.py:92-112) is unchanged, but
     recorded provider fixtures keyed to request payloads MUST be regenerated via
     agent/evals/refresh_recordings.py (index: agent/evals/recordings/index.json) — see G06.
- **Tests:** test_reader_tables.py: bordered fixture → exact n_rows/n_cols, cell text, cell NormBBox
  within `NORMBBOX_TOL` (test_reader_geometry.py:96); borderless fixture → text-strategy pass finds
  the grid; non-table prose page → `tables == ()` (false-positive guard); frozen-model
  immutability; vlm source-line ordering with a table + prose page; verifier cell-grounding
  precedence; regression: existing suites untouched-green.
- **Verification:**

      cd agent && .venv/bin/pytest -q tests/test_reader_tables.py tests/test_reader_geometry.py
      cd agent && .venv/bin/pytest -q   # full suite: full-suite baseline preserved (936 passed / 5 skipped, verified 2026-07-19)

- **Acceptance:** Table PDF fixture yields per-cell grounded, cell-cited values end-to-end;
  zero-page PDF still returns `WordsBoxes(pages=[])`; encrypted PDF still fails with the current
  typed upload/reader rejection (regression-asserted, behavior unchanged); image-only PDF pages
  carry `tables == ()` from this task (G03 supplies them); no new dependency appears in
  pyproject.toml.
- **Dependencies and blockers:** G01. Coordinate with R01/R02 only at merge time (shared
  evals/recordings regeneration).
- **Risks and rollback:** find_tables false positives on boxed intake forms — mitigated by step 4's
  word-substantiation filter and fixtures; recordings churn — G06 regenerates once for all G-tasks.
  Rollback: stop populating `tables` (additive default `()` restores exact current payloads and
  prompts).
- **Effort:** 1.5 days.

### G03 — Table/row reconstruction on OCR pages from Tesseract's own structure (no new deps)

- **Findings and requirements:** Owner gap "tables" on scanned/raster pages; W2-REQ-91; W2-D3.
- **Objective and rationale:** Tesseract's `image_to_data` ALREADY returns `block_num`, `par_num`,
  `line_num`, `word_num`, and `conf` per word — the reader currently throws that structure away
  (reader.py:259-287 reads only text/left/top/width/height). Reconstruct lines and column-aligned
  grids deterministically from it, emitting the same `TableBlock` schema as G02.
- **Current evidence:** reader.py:128-130 (DICT output produced), reader.py:259-287 (structure
  discarded); scanned-table content today degrades into mis-clustered rows via vlm.py:215-239.
- **Files and systems:** agent/app/ingestion/tables.py (add `reconstruct_ocr_tables(data, width_px,
  height_px) -> list[TableBlock]`); agent/app/ingestion/reader.py (`_ocr_page` result assembly,
  reader.py:365-376); agent/app/ingestion/image_reader.py (same call, via G04's shared path);
  agent/tests/test_reader_tables.py (extend).
- **Implementation:**
  1. Group words by `(block_num, par_num, line_num)` into lines; drop words with `conf < 0` (
     Tesseract's non-word sentinel) for structure decisions while keeping them out of cells.
  2. Column inference: over the lines of one block, cluster word x-intervals by overlap across ≥2
     consecutive lines (interval-graph connected components); a block qualifies as a table only
     with ≥2 columns × ≥2 lines and column-count regularity ≥70% of lines. Thresholds are named
     module constants (mirroring reader.py:75-80 style), never inline literals.
  3. Emit `TableBlock(source="ocr_reconstruction")` with cell bboxes as the union of member word
     boxes (already normalized top-left/y-down by pixel dims, reader.py:262-263).
  4. PSM selection experiment (fixture-decided, then pinned): default vs `--psm 6` via
     `pytesseract.image_to_data(image, config="--psm 6", ...)` on the G06 scanned-table fixture;
     record the outcome in the G06 evidence file; pin the chosen config as a constant next to
     `_default_ocr_runner` (reader.py:128-130). One config for all pages — no per-page dynamic PSM.
  5. All geometry/content assertions tesseract-version-tolerant (substring/tolerance, matching
     test_reader_geometry.py:60, 191 conventions).
- **Tests:** Scanned-table fixture → grid found with expected shape (± version tolerance), cell
  bboxes within tolerance of glyph clusters; degraded-scan fixture (existing) → NO false table;
  junk-layer multi-page fixture (`junk_layer.pdf`, .tdd-swarm/reports/W2-M4-impl.md:77) → page 1
  still routes to OCR and now may emit reconstruction without breaking page-0 text-layer behavior;
  conf<0 words excluded.
- **Verification:**

      cd agent && .venv/bin/pytest -q tests/test_reader_tables.py tests/test_reader_geometry.py

- **Acceptance:** Image-only/scanned table pages emit `TableBlock`s that ground at least the G06
  golden threshold; prose scans emit none; OCR timeout kill path (reader.py:307-376) is unchanged
  and still green (AC-4 suite).
- **Dependencies and blockers:** G01; shares tables.py + fixtures with G02 (same PR train ok).
- **Risks and rollback:** Over-detection on ragged OCR — regularity thresholds + fixtures;
  reconstruction cost is O(words) per page, no new RSS-relevant allocation (G06 probe confirms).
  Rollback: emission flag constant → `()`.
- **Effort:** 1.5 days.

### G04 — Image intake hardening: preprocessing, timeout parity, quality gate (PNG/JPEG)

- **Findings and requirements:** Owner gap "pngs"; W2-REQ-91/92; robustness parity with the PDF
  path (W2-D3's kill discipline).
- **Objective and rationale:** Photos/screenshots of forms are the worst OCR inputs and currently
  get the least help: no preprocessing, no upscale, no PSM choice, and no runaway-kill. Close all
  four with Pillow-only preprocessing and the existing subprocess harness.
- **Current evidence:** image_reader.py:19-41 — EXIF transpose + RGB only (24-25), direct
  `runner(image)` with no timeout (27-28), `render_dpi=200` recorded for arbitrary-DPI photos
  (35); upload validation already accepts PNG/JPEG intake (uploads.py:26-33).
- **Files and systems:** NEW agent/app/ingestion/preprocess.py; agent/app/ingestion/reader.py
  (extract shared `_run_ocr_with_timeout(image, runner, timeout_s) -> object | None` from
  `_ocr_page`, reader.py:323-363, without behavior change); agent/app/ingestion/image_reader.py
  (use both); agent/app/config.py (`Settings`/`get_settings`, config.py:187 — flag + caps); NEW
  agent/tests/test_image_intake.py.
- **Implementation:**
  1. preprocess.py (Pillow + stdlib only — numpy is avoided so the reader-stack license/dep
     inventory in test_reader_geometry.py:106 is untouched): pipeline =
     EXIF transpose → grayscale("L") → autocontrast → min-edge upscale to ≥1600 px (LANCZOS,
     never downscale here) → Otsu binarization via pure-Python 256-bin histogram scan →
     projection-profile deskew: score rotations in [-5°, +5°] step 0.5° by row-darkness variance,
     apply argmax if |angle| ≥ 0.5°. Deterministic; no randomness.
  2. Total-pixel cap BEFORE upscale math: if raw W×H > 16 MP, downscale to 16 MP first (RSS
     guard; cap is a named constant, probed in G06).
  3. Two-pass strategy: pass 1 = current behavior (raw image, default config) under
     `_run_ocr_with_timeout`; quality gate = fewer than `_MIN_TRUSTWORTHY_WORDS` words
     (reader.py:75) OR mean `conf` of kept words < 40 → pass 2 = preprocessed image with the
     G03-pinned config, also under the timeout. Keep whichever pass yields more trustworthy words;
     record `PageWords.unreadable=True` only if both passes fail/killed. Each pass gets the full
     per-page budget (`_DEFAULT_PER_PAGE_OCR_TIMEOUT_S`, reader.py:69); worst-case latency is
     2× budget, documented in the route's operational notes.
  4. Feature flag `IMAGE_PREPROCESS_ENABLED` (default on) in settings; off = exact current
     single-pass behavior.
  5. `read_image_words_and_boxes` keeps its signature and injectable `ocr_runner` seam
     (image_reader.py:19-21) so existing tests/fakes stay valid.
- **Tests:** test_image_intake.py: clean PNG → pass-1 accepted, preprocessing NOT applied
  (two-pass short-circuit proof); degraded/skewed synthetic photo (G06 fixture) → pass 2 recovers
  ≥ the golden word-recall threshold; runaway runner (reuse `slow_ocr_runner` pattern,
  test_reader_geometry.py:145-154) → killed within wall-clock budget, `unreadable=True`, no hang —
  the NEW assertion this path lacked; 16 MP cap; flag-off parity with current outputs;
  determinism (same bytes → identical words twice).
- **Verification:**

      cd agent && .venv/bin/pytest -q tests/test_image_intake.py tests/test_reader_geometry.py
      cd agent && .venv/bin/python -m ops.spike_rss   # in-container, G06 extended probe

- **Acceptance:** Degraded-photo golden case moves from failing to passing at the G06 threshold;
  clean-image outputs byte-identical to today with the flag off AND unchanged-within-tolerance
  with it on; image OCR can no longer hang a request (kill test green); no new pip/apt dependency;
  Dockerfile and CI untouched.
- **Dependencies and blockers:** G01; G03's pinned config constant (soft — default config until
  pinned).
- **Risks and rollback:** Preprocessing can HURT clean scans — mitigated by two-pass gating (pass 2
  only runs when pass 1 already failed the gate); doubled worst-case OCR latency on bad pages —
  bounded by existing per-page budget × 2; memory spike on huge photos — 16 MP cap + probe.
  Rollback: flag off.
- **Effort:** 1 day.

### G05 — Figure/graph regions: detection, axis-text grounding, honest surfacing

- **Findings and requirements:** Owner gap "graphs"; W2-REQ-97/98 (never invent grounding);
  OPEN-G1 (G01).
- **Objective and rationale:** No OCR engine reads plotted values — the correct capability is to
  (i) know a figure exists and where, (ii) ground the figure's TEXT (axis labels, legend, title)
  as citable context, and (iii) surface VLM figure readings as explicitly ungrounded, with the
  figure's location — instead of silently losing everything about charts.
- **Current evidence:** pdfplumber exposes embedded raster regions per page (`page.images` bboxes)
  — unused today (no reference in agent/app); PNG uploads are whole-image figures when the quality
  gate finds few words; VLM already receives the full page/image (vlm.py:161-187) and proposes
  values the verifier then rejects for lack of word evidence (verifier.py:1140 "invention never
  becomes grounding").
- **Files and systems:** agent/app/ingestion/tables.py or NEW figures.py (`FigureRegion{bbox:
  NormBBox, kind:Literal["embedded_image","full_page_image"]}`); app/schemas/extraction.py
  (additive `PageWords.figures: tuple[FigureRegion, ...] = ()`); reader.py (populate from
  `plumber_page.images`, normalized like words); image_reader.py (full-page region when word count
  < `_MIN_TRUSTWORTHY_WORDS`); composer surfacing (app/orchestrator/composer.py:198-263 walk) so
  answers can say "figure at page N, region cited" and mark chart-derived numbers as
  not-source-grounded; tests.
- **Implementation:**
  1. Populate `figures` on both paths (embedded-image bboxes on text-layer pages; full-page on
     figure-dominant images/raster pages).
  2. Grounding: NO change to pass/fail semantics (OPEN-G1 default). Words inside a figure bbox
     (axis/legend/title read by OCR) ground normally already; add the figure bbox to the citation
     payload of such words' claims as containing-region metadata only.
  3. Composer/answer surface: claims the verifier left ungrounded that the VLM attributed to a
     figure-bearing page render with the existing unsupported-claim treatment PLUS the figure
     region reference — visible, never invented (W2-REQ-97 language, W2_gap-audit.md:303).
  4. If the owner answers OPEN-G1 "yes," a follow-up task (not this one) may add a distinct
     region-level citation class; G05 must not pre-implement it.
- **Tests:** PDF-with-embedded-chart fixture → figure bbox emitted within tolerance; chart PNG →
  full-page figure + axis words grounded; end-to-end: chart-value claim stays uncited and visible
  while its axis-label context claim cites word evidence; no grounding-rate regression on
  non-figure fixtures (graph.py:279-499 metrics unchanged there).
- **Verification:**

      cd agent && .venv/bin/pytest -q tests/test_reader_tables.py tests/test_image_intake.py -k figure
      cd agent && .venv/bin/pytest -q

- **Acceptance:** Figure regions present in the words+boxes payload for both fixtures; chart
  numeric claims NEVER acquire word-level citations; unsupported-claim visibility preserved;
  OPEN-G1 untouched.
- **Dependencies and blockers:** G02/G03 (schema file + fixtures), G01 (OPEN-G1 recorded).
- **Risks and rollback:** Scope creep into region-grounding — blocked by OPEN-G1 gate; payload
  growth — figures are a handful of bboxes. Rollback: stop populating `figures` (additive default).
- **Effort:** 1 day.

### G06 — Fixtures, golden cases, RSS probe extension, and gate evidence

- **Findings and requirements:** Everything above needs deterministic proof; W2-REQ-94/95
  (synthetic-only, sensitive-artifact hygiene); artifact_scan compatibility.
- **Objective and rationale:** New capability without new evals is unverifiable. Extend the
  existing deterministic fixture generator and golden harness with table/figure/photo cases and a
  measured RSS bound.
- **Current evidence:** generate_fixtures.py (seeded, synthetic-marker discipline:
  SYNTHETIC/NON-CLINICAL/FIXTURE, test_reader_geometry.py:112-114); golden harness
  (agent/evals/golden_loader.py, w2_models.py); artifact scanner OCRs generated surfaces
  (artifact_scan.py:236-239); RSS probe steps (spike_rss.py:15, 282-285); baseline suite 936
  passed / 5 skipped (verified 2026-07-19).
- **Files and systems:** agent/evals/fixtures/documents/generate_fixtures.py (add 4 fixtures);
  golden manifest + cases; agent/ops/spike_rss.py (new step); docs/week2/evidence/ (NEW
  W2_G_EVIDENCE.md — new file only, per week-scoped doc rules); .tdd-swarm not touched.
- **Implementation:**
  1. Fixtures (same seed discipline, markers, and forbidden-clinical tripwires,
     test_reader_geometry.py:117-124): `table_clean.pdf` (bordered + borderless digital tables),
     `table_scan.pdf` (rasterized table page routed to OCR via the junk/absent-layer mechanics
     already used by `junk_layer.pdf`), `chart_page.png` (synthetic bar chart, labeled axes),
     `photo_degraded.png` (seeded noise + 3° skew + low contrast).
  2. Golden cases: table cell-grounding rate ≥ 0.8 on `table_clean`; row-association exactness on
     a 2-column label/value table; `table_scan` reconstruction grid-shape tolerance case;
     `photo_degraded` word-recall ≥ pinned threshold (set from the first measured run, then
     frozen); `chart_page` figure-region + axis-text case; negative case: prose page emits no
     tables.
  3. Regenerate provider recordings once for the payload change (G02 step 8) via
     agent/evals/refresh_recordings.py, then re-run the 50-case golden gate (evals/w2_runner.py; evals/runner.py is the retired W1 10-case runner); if
     R02's production-retrieval evaluator work has merged, run THAT evaluator — do not fork a
     second harness.
  4. spike_rss.py: add step (e) "preprocess+OCR photo_degraded at cap" reusing
     make_synthetic_page-style generation (spike_rss.py:261-285); record peak RSS delta in
     W2_G_EVIDENCE.md next to the W2-M1 baseline numbers (.tdd-swarm/reports/W2-M1-impl.md:179).
  5. artifact_scan: no code change expected (fixtures are canonical inputs, excluded by design,
     artifact_scan.py:3-4); assert the scanner still passes over newly generated eval outputs.
  6. W2_G_EVIDENCE.md: PSM experiment result (G03), before/after grounding rates per fixture,
     RSS numbers, recordings-regeneration note, and the full-suite count (baseline 936+5 → new
     total, all green).
- **Tests:** Generator reproducibility extends the AC-7 pattern (geometry-identical re-run,
  test_reader_geometry.py:423-428) to the four new fixtures; golden cases wired into the standard
  eval run; CI needs NO new system packages (all four workflows' tesseract steps already present,
  §9.1).
- **Verification:**

      cd agent && .venv/bin/python evals/fixtures/documents/generate_fixtures.py && git diff --stat
      cd agent && .venv/bin/pytest -q
      cd agent && .venv/bin/python -m evals.w2_runner run --tier recorded   # or the R02-merged evaluator entrypoint

- **Acceptance:** All new golden cases pass at pinned thresholds; full suite green at or above the
  verified 936-passing baseline; RSS delta recorded and within the Railway ceiling tracked by W2-M1
  evidence; no PHI/secret findings from artifact_scan on regenerated outputs.
- **Dependencies and blockers:** G02-G05 for the capabilities under test; R02 only if already
  merged (use its evaluator, not a fork).
- **Risks and rollback:** Threshold-pinning flakiness across Tesseract versions — thresholds
  tolerance-banded per the existing convention (test_reader_geometry.py:60); recordings churn
  conflicts with in-flight audit PRs — coordinate the single regeneration in the PR train.
  Rollback: fixtures/cases are additive files; reverting them restores the prior eval surface.
- **Effort:** 1 day.

### 9.5 Acceptance summary and required edge cases (all G-tasks)

- Encrypted PDF: current typed rejection preserved and regression-asserted (G02).
- Zero-page PDF: `WordsBoxes(pages=[])` preserved (G02).
- Image-only PDF: OCR path + G03 reconstruction; AC-4 kill discipline untouched.
- Junk-text-layer multi-page PDF: page routing behavior identical; page 0 text-layer, page 1 OCR.
- Runaway OCR on IMAGE uploads: now killed (G04) — closes the parity gap with reader.py:307-376.
- Oversized photo: 16 MP cap, probed RSS (G04/G06).
- Chart values: never silently invented, never silently lost — visible, region-referenced,
  ungrounded (G05, W2-REQ-97).
- No torch anywhere; license posture per G-D2 (no family ban, complete inventory) — the amended
  AC-5 metadata-completeness test stays green (test_reader_geometry.py:402).

### Appendix G-B — full PyMuPDF swap mechanics (NOT adopted; preserved for a future reversal)

Executable at any time — G-D2 (2026-07-19) removed the license ban, so adoption is purely a
capability/ops decision. Compliance note: this public GPL-3 repo satisfies AGPL
source-availability; procure an Artifex commercial license only if the repo ever goes
private/proprietary. Facts as of 2026-07-19: pymupdf 1.28.0 (2026-06-29), AGPL-3.0/commercial.

**API mapping (current → PyMuPDF):**

| Current call | PyMuPDF equivalent | Geometry notes |
|---|---|---|
| `pdfplumber.open(path/BytesIO)` (reader.py:401, 420) | `pymupdf.open(path)` / `pymupdf.open(stream=bytes, filetype="pdf")` | — |
| `plumber_page.extract_words(use_text_flow=False)` (reader.py:208) | `page.get_text("words")` → `(x0, y0, x1, y1, word, block_no, line_no, word_no)` | Both y-down top-left; normalize by `page.rect.width/height` instead of plumber width/height |
| `pypdfium2 render(scale=200/72).to_pil()` (reader.py:251-256) | `page.get_pixmap(dpi=200)` → `Image.frombytes("RGB", (pix.width, pix.height), pix.samples)` | Same 200-DPI pixel space |
| `pytesseract.image_to_data(image, Output.DICT)` (reader.py:130) | `page.get_textpage_ocr(dpi=200, full=True, tessdata=...)` then `page.get_text("words", textpage=tp)` | ENGINE IS STILL TESSERACT; needs traineddata via `TESSDATA_PREFIX`/`tessdata` |
| pdfplumber `find_tables()` (G02) | `page.find_tables()` | Near-parity feature |
| `pytesseract.get_tesseract_version()` boot gate (test_app_boot.py:126-142) | Replace with a tessdata-resolvable check | Binary no longer needed; DATA still is |

**Touchpoint checklist (every site is in §9.1):** pyproject.toml:37/42/45 (drop pypdfium2,
pdfplumber, pytesseract; add `pymupdf>=1.28,<2`) and comment blocks 32-59; Dockerfile:13-18 (keep
`tesseract-ocr-eng` for traineddata — or COPY eng.traineddata — and set
`ENV TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata`; `tesseract-ocr` binary package
removable); CI installs agent-quality.yml:45/135, agent-eval-gate.yml:48/122,
agent-eval-live-subset.yml:45, .gitlab-ci.yml:10 (same keep-data rule); rewrite reader.py,
image_reader.py, artifact_scan.py:236, spike_rss.py:282-285; license governance ALREADY CLEARED
by G-D2 (gates.md:39/46-84 amended; AC-5 is metadata-only at test_reader_geometry.py:402 — just
update READER_DEPS at :110 to the new dep set); test_w2_serving_integration.py:520;
test_app_boot.py:126-142; docs/tickets references (W2-M1, W2-M4, TICKETS.md:38,
.tdd-swarm/progress.md:107) and defense docs. Scanned-page OCR capability, W2-D3 semantics, and
the per-page kill harness must be re-proven over the same fixtures; the subprocess timeout wraps
`get_textpage_ocr` instead of the pytesseract runner.

**Why this stays an appendix:** the swap resolves none of §9.2's gap table; with the license
question settled by G-D2, the only remaining argument is speed/ops consolidation — real, but not
a Week-2 capability gap.
