# Clinical Co-Pilot agent — container image (ARCHITECTURE.md §2, E1.1/E9).
# Runtime deps only (no [dev]); the eval-gate + tests run in CI, not in the image.
FROM python:3.12-slim

WORKDIR /app

# Install runtime deps first (cached layer), then the app package.
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .

# Railway injects $PORT; bind to it. No secrets in the image — all config is env (D-secrets).
ENV PORT=8000
EXPOSE 8000

# One worker: the demo uses the in-process session store + token cache (single instance).
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
