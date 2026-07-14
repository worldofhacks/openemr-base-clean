#!/usr/bin/env python3
"""W2-M1 capacity probe — measure RSS headroom for the W2 stack in-container.

Operator CLI (ops scripts may print — .tdd-swarm/gates.md). Canonical run is
INSIDE the deployed Railway container (`railway ssh`, then
`python -m ops.spike_rss`). It reads the container memory limit (cgroup v2,
cgroup v1 fallback), then CONCURRENTLY holds:

  (a) bge-small-en-v1.5 embeddings under fastembed (ONNX, torch-free — W2-D4),
  (b) the local reranker mxbai-rerank-base-v1 (registered via fastembed's
      ``TextCrossEncoder.add_custom_model`` — it is not in fastembed's built-in
      list; Apache-2.0, ships its own ONNX artifacts),
  (c) a representative synthetic hybrid index (several hundred non-PHI chunks:
      an embeddings matrix + a stdlib BM25 token index — no rank-bm25 dep),
  (d) one 200-DPI Tesseract OCR page over a synthetic generated image,

while exercising one HTTP request against the local app, sampling cold RSS and
container-wide memory from cgroup v2 ``memory.current``/``memory.peak`` (with
cgroup v1 usage/max-usage fallback). Process RSS remains a diagnostic only. The
report prints the measured plan limit, W2_WAVE0_RSS_CEILING_MB = floor(0.8 *
limit), container peak, metric sources, immutable model provenance, and a
PASS/FAIL verdict against the ceiling (locked-decision, W2_ARCHITECTURE.md §6
W2-O1). Unknown limit or container peak fails closed with a nonzero exit. On
FAIL the ladder applies in order: quantize ONNX models -> raise Railway service
memory -> externalize the index (this probe never implements the ladder;
``--quantized`` exists purely to MEASURE ladder step 1).

All probe data is synthetic and non-clinical. No secrets are read or printed.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass, field

from huggingface_hub import snapshot_download

CGROUP_V2_LIMIT = "/sys/fs/cgroup/memory.max"
CGROUP_V2_CURRENT = "/sys/fs/cgroup/memory.current"
CGROUP_V2_PEAK = "/sys/fs/cgroup/memory.peak"
CGROUP_V1_LIMIT = "/sys/fs/cgroup/memory/memory.limit_in_bytes"
CGROUP_V1_CURRENT = "/sys/fs/cgroup/memory/memory.usage_in_bytes"
CGROUP_V1_PEAK = "/sys/fs/cgroup/memory/memory.max_usage_in_bytes"
# cgroup v1 reports "no limit" as a huge page-aligned number; treat anything
# above 4 TiB as unlimited.
UNLIMITED_SENTINEL_BYTES = 4 * 1024**4

EMBED_MODEL = "BAAI/bge-small-en-v1.5"
EMBED_SOURCE_REPO = "qdrant/bge-small-en-v1.5-onnx-q"
EMBED_REVISION = "52398278842ec682c6f32300af41344b1c0b0bb2"
EMBED_ONNX = "model_optimized.onnx"
EMBED_DIM = 384
RERANK_MODEL = "mixedbread-ai/mxbai-rerank-base-v1"
RERANK_REVISION = "800f24c113213a187e65bde9db00c15a2bb12738"
RERANK_ONNX_FP32 = "onnx/model.onnx"
RERANK_ONNX_QUANTIZED = "onnx/model_quantized.onnx"

_HF_COMMON_FILES = (
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "preprocessor_config.json",
)

# Synthetic, deliberately non-clinical vocabulary (no names, no dates, no PHI).
_VOCAB = (
    "synthetic corpus chunk retrieval hybrid index embedding vector lexical "
    "token overlap ranking window section paragraph figure table caption "
    "reference appendix summary protocol procedure guidance dosage schedule "
    "measurement threshold baseline capacity memory budget container probe "
    "sample document page render extract verify record evidence report"
).split()


def log(msg: str) -> None:
    print(f"[spike_rss] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Memory limit + RSS plumbing
# ---------------------------------------------------------------------------


def _read_cgroup_int(path: str) -> int | None:
    """Read one non-negative numeric cgroup value, or ``None`` if unavailable."""
    try:
        raw = open(path, encoding="ascii").read().strip()
    except OSError:
        return None
    if raw == "max":
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def read_memory_limit_measurement() -> tuple[int | None, str | None]:
    """Container limit and source: cgroup v2 first, then cgroup v1."""
    for path, source in (
        (CGROUP_V2_LIMIT, "cgroup_v2.memory.max"),
        (CGROUP_V1_LIMIT, "cgroup_v1.memory.limit_in_bytes"),
    ):
        try:
            raw = open(path, encoding="ascii").read().strip()
        except OSError:
            continue
        if raw == "max":
            continue
        try:
            value = int(raw)
        except ValueError:
            continue
        if value <= 0 or value >= UNLIMITED_SENTINEL_BYTES:
            continue
        return value, source
    return None, None


def read_memory_limit_bytes() -> int | None:
    """Backward-compatible value-only view of the container memory limit."""
    return read_memory_limit_measurement()[0]


def read_container_memory_measurement() -> tuple[
    int | None, int | None, str | None, str | None
]:
    """Read container current/peak bytes and their cgroup metric sources.

    A cgroup-v2 controller is selected if either v2 usage metric exists. The
    v1 memory controller is consulted only when v2 usage metrics are absent, so
    values from different controller hierarchies are never mixed.
    """
    current = _read_cgroup_int(CGROUP_V2_CURRENT)
    peak = _read_cgroup_int(CGROUP_V2_PEAK)
    if current is not None or peak is not None:
        return (
            current,
            peak,
            "cgroup_v2.memory.current" if current is not None else None,
            "cgroup_v2.memory.peak" if peak is not None else None,
        )

    current = _read_cgroup_int(CGROUP_V1_CURRENT)
    peak = _read_cgroup_int(CGROUP_V1_PEAK)
    return (
        current,
        peak,
        "cgroup_v1.memory.usage_in_bytes" if current is not None else None,
        "cgroup_v1.memory.max_usage_in_bytes" if peak is not None else None,
    )


def _proc_status_kb(key: str) -> int | None:
    try:
        with open("/proc/self/status", encoding="ascii") as fh:
            for line in fh:
                if line.startswith(key + ":"):
                    return int(line.split()[1])  # kB
    except OSError:
        return None
    return None


def rss_mb() -> float | None:
    """Current RSS in MB (Linux /proc only; None elsewhere)."""
    kb = _proc_status_kb("VmRSS")
    return kb / 1024.0 if kb is not None else None


def peak_rss_mb() -> float | None:
    """Peak RSS (high-water mark) in MB. Linux: VmHWM; macOS: getrusage."""
    kb = _proc_status_kb("VmHWM")
    if kb is not None:
        return kb / 1024.0
    if sys.platform == "darwin":
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024.0**2)
    return None


def best_rss_mb() -> float | None:
    """Current RSS where available, else the high-water mark (macOS)."""
    return rss_mb() if rss_mb() is not None else peak_rss_mb()


# ---------------------------------------------------------------------------
# (c) synthetic hybrid index — embeddings matrix + stdlib BM25 token index
# ---------------------------------------------------------------------------


@dataclass
class Bm25Index:
    """Minimal BM25 built with the stdlib (no rank-bm25 dependency)."""

    doc_tokens: list[dict[str, int]] = field(default_factory=list)
    doc_lengths: list[int] = field(default_factory=list)
    doc_freq: dict[str, int] = field(default_factory=dict)
    k1: float = 1.5
    b: float = 0.75

    @staticmethod
    def tokenize(text: str) -> list[str]:
        return re.findall(r"[a-z0-9]+", text.lower())

    def add(self, text: str) -> None:
        counts: dict[str, int] = {}
        tokens = self.tokenize(text)
        for tok in tokens:
            counts[tok] = counts.get(tok, 0) + 1
        self.doc_tokens.append(counts)
        self.doc_lengths.append(len(tokens))
        for tok in counts:
            self.doc_freq[tok] = self.doc_freq.get(tok, 0) + 1

    def scores(self, query: str) -> list[float]:
        n = len(self.doc_tokens)
        avgdl = (sum(self.doc_lengths) / n) if n else 1.0
        out: list[float] = []
        q_tokens = self.tokenize(query)
        for counts, dl in zip(self.doc_tokens, self.doc_lengths):
            score = 0.0
            for tok in q_tokens:
                tf = counts.get(tok, 0)
                if not tf:
                    continue
                df = self.doc_freq.get(tok, 0)
                idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
                denom = tf + self.k1 * (1.0 - self.b + self.b * dl / avgdl)
                score += idf * tf * (self.k1 + 1.0) / denom
            out.append(score)
        return out


def synthetic_chunks(count: int, seed: int = 20260714) -> list[str]:
    rng = random.Random(seed)
    chunks: list[str] = []
    for i in range(count):
        words = rng.choices(_VOCAB, k=rng.randint(40, 80))
        chunks.append(f"chunk {i}: " + " ".join(words))
    return chunks


# ---------------------------------------------------------------------------
# (d) one 200-DPI synthetic OCR page
# ---------------------------------------------------------------------------


def make_synthetic_page_200dpi():
    """A US-letter page at 200 DPI (1700x2200) of synthetic printed text."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("L", (1700, 2200), color=255)
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=40)
    except TypeError:  # very old pillow without size kwarg
        font = ImageFont.load_default()
    rng = random.Random(7)
    y = 100
    draw.text((100, y), "SYNTHETIC CAPACITY PROBE PAGE", fill=0, font=font)
    y += 90
    for _ in range(30):
        line = " ".join(rng.choices(_VOCAB, k=8))
        draw.text((100, y), line, fill=0, font=font)
        y += 64
    return img


def run_ocr(img) -> str:
    import pytesseract

    return pytesseract.image_to_string(img)


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------


def download_pinned_model(
    repo_id: str, revision: str, cache_dir: str, model_file: str
) -> str:
    """Download exactly one approved Hugging Face snapshot for FastEmbed."""
    return snapshot_download(
        repo_id=repo_id,
        revision=revision,
        cache_dir=cache_dir,
        allow_patterns=[*_HF_COMMON_FILES, model_file],
    )


def register_reranker(quantized: bool) -> None:
    from fastembed.common.model_description import ModelSource
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    TextCrossEncoder.add_custom_model(
        model=RERANK_MODEL,
        sources=ModelSource(hf=RERANK_MODEL),
        model_file=RERANK_ONNX_QUANTIZED if quantized else RERANK_ONNX_FP32,
        description="W2-D4 local reranker (registered by W2-M1 spike probe)",
        license="apache-2.0",
        size_in_gb=0.24 if quantized else 0.74,
    )


def http_get(url: str, timeout: float = 15.0) -> int:
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 — local app URL
        resp.read()
        return int(resp.status)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--chunks", type=int, default=600, help="synthetic corpus size")
    parser.add_argument(
        "--quantized",
        action="store_true",
        help="measure ladder step 1: use the reranker's quantized ONNX artifact",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help=(
            "smoke mode: no model downloads, no HTTP — random embedding matrix, "
            "BM25 index, OCR page, limit/RSS readout only"
        ),
    )
    parser.add_argument(
        "--app-url",
        default=None,
        help="app URL to exercise (default http://127.0.0.1:$PORT/health)",
    )
    parser.add_argument(
        "--cache-dir",
        default=os.environ.get("FASTEMBED_CACHE_DIR", "/tmp/fastembed-spike"),
        help="fastembed model cache dir",
    )
    args = parser.parse_args(argv)

    limit_bytes, limit_source = read_memory_limit_measurement()
    limit_mb = limit_bytes / (1024.0**2) if limit_bytes is not None else None
    ceiling_mb = math.floor(0.8 * limit_mb) if limit_mb is not None else None
    cold_process_rss = best_rss_mb()
    (
        cold_container_current_bytes,
        _cold_container_peak_bytes,
        cold_container_current_source,
        _cold_container_peak_source,
    ) = read_container_memory_measurement()
    log(f"mode={'quick' if args.quick else 'full'} quantized={args.quantized}")
    log(
        "memory limit: "
        + (f"{limit_mb:.0f} MB ({limit_source})" if limit_mb is not None else "UNKNOWN")
    )
    log(
        "cold process RSS (diagnostic only): "
        + (f"{cold_process_rss:.0f} MB" if cold_process_rss is not None else "unavailable")
    )

    import numpy as np

    stages: dict[str, float | None] = {}
    texts = synthetic_chunks(args.chunks)

    # (c) BM25 half of the hybrid index (stdlib).
    bm25 = Bm25Index()
    for text in texts:
        bm25.add(text)
    stages["after_bm25_index"] = best_rss_mb()

    embedder = None
    reranker = None
    if args.quick:
        matrix = np.asarray(
            np.random.default_rng(0).standard_normal((args.chunks, EMBED_DIM)),
            dtype=np.float32,
        )
    else:
        from fastembed import TextEmbedding
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        # (a) bge-small embeddings — pin the exact HF snapshot, then pass its
        # directory through FastEmbed's supported specific_model_path seam.
        t0 = time.time()
        embed_model_path = download_pinned_model(
            EMBED_SOURCE_REPO,
            EMBED_REVISION,
            args.cache_dir,
            EMBED_ONNX,
        )
        embedder = TextEmbedding(
            EMBED_MODEL,
            cache_dir=args.cache_dir,
            specific_model_path=embed_model_path,
        )
        log(
            f"{EMBED_MODEL} ({EMBED_SOURCE_REPO}@{EMBED_REVISION}) "
            f"loaded in {time.time() - t0:.1f}s"
        )
        stages["after_embed_model"] = best_rss_mb()
        t0 = time.time()
        matrix = np.asarray(list(embedder.embed(texts)), dtype=np.float32)
        log(f"embedded {len(texts)} chunks in {time.time() - t0:.1f}s")

        # (b) the local reranker.
        register_reranker(args.quantized)
        t0 = time.time()
        artifact = RERANK_ONNX_QUANTIZED if args.quantized else RERANK_ONNX_FP32
        rerank_model_path = download_pinned_model(
            RERANK_MODEL,
            RERANK_REVISION,
            args.cache_dir,
            artifact,
        )
        reranker = TextCrossEncoder(
            RERANK_MODEL,
            cache_dir=args.cache_dir,
            specific_model_path=rerank_model_path,
        )
        log(
            f"{RERANK_MODEL}@{RERANK_REVISION} ({artifact}) "
            f"loaded in {time.time() - t0:.1f}s"
        )
        stages["after_reranker"] = best_rss_mb()
    stages["after_index_matrix"] = best_rss_mb()
    log(f"hybrid index held: matrix {matrix.shape}, bm25 vocab {len(bm25.doc_freq)}")

    # Concurrent phase: OCR + HTTP + a hybrid query, all while models + index
    # stay resident; a sampler thread watches RSS throughout.
    results: dict[str, object] = {}
    errors: list[str] = []
    sampled_peak = 0.0
    stop_sampling = threading.Event()

    def sampler() -> None:
        nonlocal sampled_peak
        while not stop_sampling.is_set():
            now = best_rss_mb()
            if now is not None:
                sampled_peak = max(sampled_peak, now)
            time.sleep(0.1)

    def ocr_task() -> None:
        try:
            text = run_ocr(make_synthetic_page_200dpi())
            results["ocr_chars"] = len(text)
            if "SYNTHETIC" not in text.upper():
                errors.append("OCR output missing expected synthetic marker")
        except Exception as exc:
            errors.append(f"OCR failed: {exc!r}")

    def http_task() -> None:
        url = args.app_url or f"http://127.0.0.1:{os.environ.get('PORT', '8000')}/health"
        try:
            results["http_status"] = http_get(url)
        except Exception as exc:
            errors.append(f"HTTP request to app failed ({url}): {exc!r}")

    def query_task() -> None:
        try:
            query = "capacity memory budget threshold"
            lexical = bm25.scores(query)
            if args.quick or embedder is None or reranker is None:
                top = sorted(range(len(texts)), key=lambda i: -lexical[i])[:20]
                results["rerank_top1"] = top[0]
                return
            q_vec = np.asarray(list(embedder.embed([query])), dtype=np.float32)[0]
            dense = matrix @ q_vec
            fused = [
                (0.5 * lexical[i] + 0.5 * float(dense[i]), i) for i in range(len(texts))
            ]
            top = [i for _, i in sorted(fused, reverse=True)[:20]]
            scores = list(reranker.rerank(query, [texts[i] for i in top]))
            results["rerank_top1"] = top[max(range(len(scores)), key=scores.__getitem__)]
        except Exception as exc:
            errors.append(f"hybrid query failed: {exc!r}")

    threads = [threading.Thread(target=t) for t in (ocr_task, query_task)]
    if not args.quick or args.app_url:
        threads.append(threading.Thread(target=http_task))
    sampler_thread = threading.Thread(target=sampler)
    sampler_thread.start()
    t0 = time.time()
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    stop_sampling.set()
    sampler_thread.join()
    log(f"concurrent phase done in {time.time() - t0:.1f}s: {results}")

    process_peak = peak_rss_mb()
    if process_peak is None or (sampled_peak and sampled_peak > process_peak):
        process_peak = sampled_peak or process_peak

    (
        container_current_bytes,
        container_peak_bytes,
        container_current_source,
        container_peak_source,
    ) = read_container_memory_measurement()
    container_current_mb = (
        container_current_bytes / (1024.0**2)
        if container_current_bytes is not None
        else None
    )
    container_peak_mb = (
        container_peak_bytes / (1024.0**2) if container_peak_bytes is not None else None
    )

    missing: list[str] = []
    if limit_mb is None:
        missing.append("memory limit")
    if container_peak_mb is None:
        missing.append("container cgroup peak")

    if missing:
        verdict = f"NO-VERDICT ({' and '.join(missing)} unavailable)"
    elif errors:
        verdict = "FAIL (probe workload errors)"
    else:
        verdict = "PASS" if container_peak_mb < ceiling_mb else "FAIL"
    ok = verdict == "PASS"

    report = {
        "mode": "quick" if args.quick else "full",
        "reranker_artifact": (
            None if args.quick else (RERANK_ONNX_QUANTIZED if args.quantized else RERANK_ONNX_FP32)
        ),
        "model_provenance": {
            "embedding": {
                "model": EMBED_MODEL,
                "source_repo": EMBED_SOURCE_REPO,
                "revision": EMBED_REVISION,
            },
            "reranker": {
                "model": RERANK_MODEL,
                "source_repo": RERANK_MODEL,
                "revision": RERANK_REVISION,
            },
        },
        "chunks": args.chunks,
        "plan_memory_limit_mb": round(limit_mb) if limit_mb is not None else None,
        "plan_memory_limit_source": limit_source,
        "W2_WAVE0_RSS_CEILING_MB": ceiling_mb,
        "capacity_metric_scope": "container_cgroup",
        "capacity_metric_source": container_peak_source,
        "cold_container_memory_current_mb": (
            round(cold_container_current_bytes / (1024.0**2))
            if cold_container_current_bytes is not None
            else None
        ),
        "cold_container_memory_current_source": cold_container_current_source,
        "container_memory_current_mb": (
            round(container_current_mb) if container_current_mb is not None else None
        ),
        "container_memory_current_source": container_current_source,
        "container_peak_memory_mb": (
            round(container_peak_mb) if container_peak_mb is not None else None
        ),
        "container_peak_memory_source": container_peak_source,
        # Kept for operator/report compatibility; unlike the historical
        # implementation this now carries the container cgroup peak.
        "peak_rss_mb": round(container_peak_mb) if container_peak_mb is not None else None,
        "cold_rss_mb": (
            round(cold_process_rss) if cold_process_rss is not None else None
        ),
        "process_peak_rss_mb": (
            round(process_peak) if process_peak is not None else None
        ),
        "process_rss_metric_source": (
            "/proc/self/status VmRSS/VmHWM"
            if sys.platform != "darwin"
            else "resource.getrusage(RUSAGE_SELF).ru_maxrss"
        ),
        "process_rss_role": "diagnostic_only_not_used_for_capacity_verdict",
        "stage_rss_mb": {k: (round(v) if v is not None else None) for k, v in stages.items()},
        "concurrent_results": results,
        "errors": errors,
        "verdict": verdict,
        "ladder_on_fail": "quantize ONNX models -> raise Railway memory -> externalize index",
    }
    print(json.dumps(report, indent=2))
    log(f"VERDICT: {verdict}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
