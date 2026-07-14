# W2-M4 implementation report — PDF words+boxes reader spike

Ticket: `tickets/W2-M4.md` · Branch: `ticket/w2-m4-reader-spike` · Freeze SHA: `81ebb04`
Status: DONE — all 7 frozen tests green, all Tier-1 gates pass, AC-6 live-measure evidence below.

## What changed

| File | Change |
|---|---|
| `agent/app/ingestion/__init__.py` (NEW) | New ingestion package. |
| `agent/app/ingestion/reader.py` (NEW) | The words+boxes reader. `RENDER_DPI=200`; frozen strict Pydantic v2 `NormBBox` (range + non-degenerate/non-inverted validation in a `model_validator`, `frozen=True, extra="forbid"`), `Word`, `PageWords`, `WordsBoxes`. `read_words_and_boxes(pdf_path, *, ocr_runner=None, per_page_ocr_timeout_s=30.0)`: **text-layer first** (pdfplumber `extract_words`, media-box normalization; pdfplumber's `top`/`bottom` are already y-down — the PDF y-up space flipped), each page recording `render_dpi==200` + `page_pixel_dims` computed at 200 DPI from the media box; a **junk-density heuristic** (`_text_layer_is_trustworthy`) routes sparse/non-word-like pages to OCR; the **OCR path** renders via pypdfium2 at 200 DPI → PIL → Tesseract `image_to_data`, normalized by pixel dims; each OCR page runs under a **hard per-page timeout** in a spawned `multiprocessing.Process` that is `terminate()`-d on timeout (see OCR-kill below). No `fitz`/PyMuPDF import anywhere. |
| `agent/ops/spike_reader.py` (NEW) | AC-6 operator CLI — pypdfium2 vs pdfplumber word-segmentation bake-off over the seed fixtures (ops scripts may print). |
| `agent/evals/fixtures/documents/generate_fixtures.py` (NEW) | Deterministic (seed `20260714`) generator; writes fixtures next to `__file__`; generation gated under `if __name__ == "__main__":`. One shared `_LAYOUT` table drives all three fixtures. |
| `agent/evals/fixtures/documents/{clean,degraded,junk_layer}.pdf` (NEW) | The three committed seed artifacts. |

Untouched, per ticket: `agent/pyproject.toml`, `agent/Dockerfile`, `agent/tests/`, all W1/W2 binding docs, all OpenEMR PHP. No new dependencies.

## Gate evidence (final run: `bash .tdd-swarm/run-local-gates.sh tickets/W2-M4.md 81ebb04`)

```
GATE syntax: PASS
GATE unit-tests: PASS
347 passed, 6 skipped, 1 warning in 5.89s
GATE frozen-tests: PASS
spec-lint: W2-M4:AC-6 -> live-measure evidence row (exempt from frozen-test mapping)
GATE spec-lint: PASS
GATE no-todos: PASS
GATE no-debug: PASS
GATE no-skip-markers: PASS
----
ALL GATES PASS
```

Suite arithmetic: post-W2-M1 baseline on this branch/venv is **341 passed, 6 skipped** (the `test_reader_geometry.py` license test already passed at freeze since it never imports the missing module; the other 6 reader tests failed at collection/assertion). This ticket adds the reader + fixtures so all 7 reader tests go green: 341 + 6 = **347 passed, 6 skipped**, zero prior tests affected. The single warning is a pre-existing `StarletteDeprecationWarning` from fastapi's testclient (a `StarletteDeprecationWarning`, NOT a `DeprecationWarning`, so it does not trip `filterwarnings=["error::DeprecationWarning"]`); my reader/generator import + both read paths emit zero `DeprecationWarning` (verified under `-W error::DeprecationWarning`).

## OCR-kill mechanism (AC-4) and how it proves it kills

**Mechanism: a spawned `multiprocessing.Process` + a result `Queue`, `terminate()`-d on timeout.** Each OCR page renders to a PIL image, whose raw bytes + mode + size are passed to a module-level, picklable target `_ocr_subprocess_entry` running in a `get_context("spawn")` child; the child calls the (possibly injected) `ocr_runner` and `put`s the result on a `Queue`. The parent does `result_queue.get(timeout=per_page_ocr_timeout_s)`; on `queue.Empty` it `process.terminate()` (SIGTERM), `join`s with a grace period, escalates to `process.kill()` if still alive, and returns the typed unreadable outcome (`unreadable=True`, `source="ocr"`, `words=[]`) — then CONTINUES to the next page. A thread is never used because a thread cannot be force-killed.

The AC-4 test injects the module-level `slow_ocr_runner` (sleeps `FAKE_OCR_SLEEP_S=120s`) with `per_page_ocr_timeout_s=0.5` over the multi-page `junk_layer.pdf`. **Measured elapsed in isolation: 0.85 s** (budget 20 s). Page 0 (text layer) processes normally; page 1 (routed to OCR) is killed and marked `unreadable`. The interpreter exits cleanly at ~1.2 s total — proving no 120 s child lingers.

**Key trap found and avoided (spike finding):** my first attempt used `concurrent.futures.ProcessPoolExecutor` with `shutdown(wait=False, cancel_futures=True)`. `cancel_futures` cancels only *queued* futures — an already-RUNNING worker is NOT terminated, and the interpreter's atexit join then blocked for the full 120 s (the child's real sleep), even though `future.result(timeout=...)` returned in 0.5 s. A raw `Process` + explicit `terminate()` is required for a genuine kill. **W2-M6+ must use `Process.terminate()`/`kill()`, never `ProcessPoolExecutor.shutdown(cancel_futures=True)`, for hard cancellation.**

## AC-6 segmentation winner + rationale (live-measure)

`ops/spike_reader.py` ran both candidates over `clean.pdf` and `junk_layer.pdf`. Both produced the identical word set `['TOP', 'SYNTHETIC', 'NON-CLINICAL', 'FIXTURE']` on the synthetic layout — no split/merge divergence on clean type.

**Winner: `pdfplumber.extract_words`.** Rationale on fixture evidence:
- **Native word tokenizer with per-word boxes.** pypdfium2 exposes only char-level boxes (`get_charbox`) and rect runs — it has no word tokenizer, so words must be reconstructed by hand (whitespace-split the char stream, union constituent char boxes). Extra code, extra bug surface.
- **Canonical-ready coordinates.** pdfplumber reports `top`/`bottom` from the page top (y-down) — the canonical §2 space. pypdfium2's char boxes are PDF-native y-up (`left, bottom, right, top`) and need an explicit flip.
- **Tunable segmentation** (`x_tolerance`, `keep_blank_chars`) for real layouts later.

pypdfium2 is retained for what it is best at — **200-DPI page rendering** for the OCR fallback — and its char boxes remain a fallback segmenter if pdfplumber ever regresses.

## Cross-engine tolerance behavior (AC-1)

`NORMBBOX_TOL = 0.02` (per-coordinate, normalized). On the committed fixtures the AC-1 comparand `SYNTHETIC` (text-layer box on `clean.pdf` vs OCR ink box on `degraded.pdf`) agrees within:

```
x0 delta=0.0024   y0 delta=0.0023   x1 delta=0.0087   y1 delta=0.0058
MAX delta = 0.0087  →  headroom 0.0113 (56% of the 0.02 tolerance to spare)
```

**Tolerance headroom finding:** the binding coordinate is `x1` (the right edge). The text-layer box is the font *advance* box; the OCR box is the *ink* box, and that gap grows with word width. Font size was the tuning lever: at 40 pt the `SYNTHETIC` x1 delta was 0.0210 (just OVER tolerance); at **24 pt** it drops to 0.0087. 24 pt is the committed choice — comfortably inside tolerance while still large enough that Tesseract reads every marker through the seeded scan noise. I tuned the FIXTURE (font size), never the test.

## AC-7 reproducibility (geometry-identical branch) + content safety

- **Chosen branch: geometry-identical** (the test's single deterministic pass condition). Re-running the committed generator into a scratch dir reproduces `clean.pdf` such that the reader emits the same word order and each box within `NORMBBOX_TOL` — confirmed green.
- **Bonus byte-stability finding:** the two *hand-emitted* PDFs (`clean.pdf`, `junk_layer.pdf`) ARE byte-stable across regeneration (no timestamps/`/ID`/producer string emitted); `degraded.pdf` is NOT byte-stable — PIL's `Image.save(path, "PDF")` embeds non-deterministic bytes even with fully seeded content. This is exactly why the ticket Context pre-authorized geometry-identity as the robust branch: byte-hashing would be brittle for the raster fixture. Recorded so W2-M7 does not attempt hash-pinning the raster fixtures.
- **Content safety:** all fixtures carry the synthetic markers (`SYNTHETIC`, `NON-CLINICAL`, `FIXTURE`, plus the literal `TOP`) and NONE of the forbidden clinical/PHI tripwire strings (`metformin`, `diagnosis`, `patient name`, `date of birth`, `mrn`, `ssn`). Verified over the reader's extracted text (substring, tesseract-version-tolerant). No PHI, no secrets anywhere.

## Fixture design (all synthetic, non-clinical)

One shared `_LAYOUT` table (word, x-left-pt, baseline-from-top-pt) drives all three fixtures so text-layer boxes and OCR ink boxes coincide:
- `clean.pdf` — born-digital: a hand-emitted single-page PDF (raw objects + `BT/ET`, `/F1 Tf`, `Tm`, `Tj`) with a real Helvetica text layer → text_layer path.
- `degraded.pdf` — the same layout rasterized at 200 DPI via PIL (`anchor="ls"` so ink aligns to the text baseline) with light SEEDED salt-and-pepper noise, saved image-only (`Image.save(..., "PDF")`) → no text layer → density heuristic → OCR path. The `SYNTHETIC` marker's OCR box matches the clean text-layer box within tolerance (AC-1).
- `junk_layer.pdf` — **multi-page** (makes AC-4's "remaining pages still process" non-vacuous): page 0 is a normal readable text-layer page; page 1 carries a garbage text layer (`#$%^&*`, `!!!~~~`, …) rendered with the invisible text-render mode (`3 Tr`) at plausible positions so the density heuristic rejects it, over a full-page embedded raster of real glyphs (`FIXTURE`, `SYNTHETIC`) so the default Tesseract path has ink to read → page 1 routes to OCR.

## Spike findings for the feature build

1. **NormBBox → W2-M6 unification.** The frozen `NormBBox` (strict Pydantic v2, `frozen=True, extra="forbid"`, range + non-degenerate/non-inverted validated in a `model_validator(mode="after")`) is the shape to lift verbatim into the canonical §2 contract module. `Word`/`PageWords`/`WordsBoxes` are all `frozen`/`extra="forbid"` too. Do NOT improvise a second box shape when schemas freeze.
2. **OCR hard-cancel is `Process.terminate()`, not executor cancel_futures** (see AC-4 above) — carry this into the production OCR worker (W2-M6+). The picklable seam is: a module-level subprocess target that CALLS the injected runner, with the runner passed by reference (works because pytest keeps `tests.test_reader_geometry` importable in the spawned child).
3. **Geometry-identity, not byte-hashing, for the raster fixture corpus** (W2-M7). PIL's PDF writer is not byte-deterministic; hash-pinning would falsely fail on regeneration. Assert reader geometry within tolerance instead.
4. **Cross-engine box convention gap is font-advance vs ink, worst on `x1`, scales with word width** — a real-doc grounding pipeline should expect the text-layer box to be slightly wider than the OCR box on the right edge; a per-coordinate tolerance (not IoU alone) catches a missing y-flip cleanly (~0.5 error) while tolerating this ~0.01 metric drift.
5. **`page_pixel_dims` is recorded on EVERY page including text-layer pages** (computed `px = pts/72*200` from the media box), so the bbox-overlay endpoint (§2a, later ticket) can map a normalized box back to render pixels without re-opening the PDF for text-layer pages.

## Hygiene

- Frozen tests untouched (`git diff 81ebb04..HEAD -- agent/tests/` empty; frozen-tests gate PASS).
- No pyproject/Dockerfile/OpenEMR/binding-doc edits; no new dependencies; PyMuPDF absent (AC-5 green).
- Synthetic non-clinical fixtures only; no PHI, no secrets in code, fixtures, logs, or this report.
- No `print(` in `agent/app/` (ops script prints, as permitted); no TODO/FIXME/HACK; no skip markers.
