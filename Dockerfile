# TrendCut runtime image — FastAPI (uvicorn) serving the API + static frontend
# same-origin. Deliberately minimal: only the estimator/api/web source and the
# RUNTIME dependencies (numpy, fastapi, uvicorn). No ruff/pytest/hypothesis/httpx
# — those are the "[dev]" extra and never enter the runtime image.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Copy the whole app (source + pyproject + README used by the build backend).
COPY pyproject.toml README.md ./
COPY estimator/ ./estimator/
COPY api/ ./api/
COPY web/ ./web/

# Install with ONLY runtime deps (no ".[dev]"). Editable so that at runtime the
# static mount — which resolves ``web/`` relative to ``api/app.py`` — points at
# /app/web instead of a site-packages copy. This needs no change to the frozen
# app code; a non-editable install would move api/ into site-packages and break
# that relative path.
RUN pip install -e .

# The SQLite database lives on a mounted volume (see fly.toml [[mounts]]), OUTSIDE
# the image layers, so it survives image rebuilds/redeploys. Path is overridable.
ENV TRENDCUT_DB=/data/trendcut.db
RUN mkdir -p /data

# Fly mounts the volume as root and the DB is created on first write, so the
# process runs as root to avoid volume-ownership friction on a single-user box.
EXPOSE 8080

# Shell form with `exec` so uvicorn becomes PID 1 and receives SIGTERM/SIGINT for
# a clean shutdown, while still letting HOST/PORT come from the environment.
CMD ["sh", "-c", "exec uvicorn api.app:app --host ${HOST:-0.0.0.0} --port ${PORT:-8080}"]
