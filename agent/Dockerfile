# Clinical Co-Pilot agent — container image (ARCHITECTURE.md §2, E1.1/E9).
# Runtime deps only (no [dev]); the eval-gate + tests run in CI, not in the image.
FROM python:3.12-slim

WORKDIR /app

# W2-M1: native OCR layer (W2-D3 — local Tesseract grounds every extraction).
# Debian slim package names per ticket: tesseract-ocr + tesseract-ocr-eng
# (pulls libtesseract/leptonica runtime libs). ONNX models (bge-small-en-v1.5,
# mxbai-rerank-base-v1) are NOT baked into the image — they download at first
# use (spike decision, recorded in the W2-M1 report: keeps the image ~1 GB
# smaller and the W1 app boot untouched; revisit at the W2-D4 feature wave).
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
RUN pip install --no-cache-dir .

# W2-M1 capacity probe (operator CLI, run in-container via `railway ssh`):
# `python -m ops.spike_rss`. Only the probe module is shipped — not ops/tests.
COPY ops/__init__.py ops/spike_rss.py ./ops/

# Railway injects $PORT; bind to it. No secrets in the image — all config is env (D-secrets).
ENV PORT=8000
EXPOSE 8000

# One worker: the demo uses the in-process session store + token cache (single instance).
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
