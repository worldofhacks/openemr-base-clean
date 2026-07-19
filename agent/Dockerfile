# Clinical Co-Pilot agent — container image (ARCHITECTURE.md §2, E1.1/E9).
# Runtime deps only (no [dev]); the eval-gate + tests run in CI, not in the image.
FROM python:3.12-slim

WORKDIR /app

# W2-M1: native OCR layer (W2-D3 — local Tesseract grounds every extraction).
# Debian slim package names per ticket: tesseract-ocr + tesseract-ocr-eng
# (pulls libtesseract/leptonica runtime libs).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgomp1 \
        tesseract-ocr \
        tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

# Install runtime deps first (cached layer), then the app package.
COPY pyproject.toml ./
COPY app ./app
COPY corpus ./corpus
COPY migrations ./migrations
RUN pip install --no-cache-dir .

# R07: pre-bake the pinned retrieval weights (bge-small embedder + mxbai
# reranker ONNX) into the image. This REVERSES the W2-M1 spike decision to
# lazy-download at first use: a cold container was downloading the weights
# inside the `active_reranker` soft-probe budget, so cache-busted `/ready`
# intermittently reported `active_reranker: timeout`. Both revisions are
# immutable commit SHAs, so huggingface_hub resolves from this cache with no
# network at runtime. The bake imports the runtime pins (repo/revision/onnx
# filenames + allow_patterns) from corpus/retrieval.py — image and runtime
# cannot drift.
ENV FASTEMBED_CACHE_DIR=/app/model-cache
RUN python -c "\
import os; \
from huggingface_hub import snapshot_download; \
import corpus.retrieval as r; \
cache = os.environ['FASTEMBED_CACHE_DIR']; \
snapshot_download(repo_id=r.EMBED_SOURCE_REPO, revision=r.EMBED_REVISION, cache_dir=cache, allow_patterns=[*r._HF_COMMON_FILES, r.EMBED_ONNX]); \
snapshot_download(repo_id=r.RERANK_MODEL, revision=r.RERANK_REVISION, cache_dir=cache, allow_patterns=[*r._HF_COMMON_FILES, r.RERANK_ONNX])"

# R07: the composition root (AgentServices.startup) warms the retriever in a
# background thread when this is set, so the first cache-busted /ready probe
# hits loaded models. Default-off outside the image keeps tests/local boots
# model-free (W2-D4 invariant).
ENV RETRIEVAL_WARMUP=1

# W2-M1 capacity probe (operator CLI, run in-container via `railway ssh`):
# `python -m ops.spike_rss`. Only the probe module is shipped — not ops/tests.
COPY ops/__init__.py ops/spike_rss.py ./ops/

# Railway injects $PORT; bind to it. No secrets in the image — all config is env (D-secrets).
ENV PORT=8000
EXPOSE 8000

# One worker: the demo uses the in-process session store + token cache (single instance).
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --no-access-log"]
