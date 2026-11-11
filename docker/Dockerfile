# syntax=docker/dockerfile:1
#
# Outlawz in one container. Build from the repository root:
#   docker build -f docker/Dockerfile -t outlawz .
# or use docker/compose.yaml, which also publishes the port on 127.0.0.1 only.

# ---------------------------------------------------------------- 1. the web app (static export)
FROM node:26-alpine AS web
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# ---------------------------------------------------------------- 2. a wheel with the web app inside
FROM python:3.12-slim AS wheel
# Patient downloads: a slow or dropped connection retries instead of failing the build.
ENV PIP_DEFAULT_TIMEOUT=60 \
    PIP_RETRIES=10 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /src
# The build backend is installed once here (and cached as a layer), so building the wheel needs no
# second, isolated download step.
RUN pip install "build>=1.2" "hatchling>=1.25"
COPY backend/ backend/
COPY --from=web /src/frontend/out/ backend/outlawz/web/
RUN python -m build --wheel --no-isolation --outdir /dist backend

# ---------------------------------------------------------------- 3. the app
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=60 \
    PIP_RETRIES=10 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    OUTLAWZ_HOST=0.0.0.0 \
    OUTLAWZ_PORT=8642 \
    OUTLAWZ_LIBRARY=/library \
    OUTLAWZ_DATA_DIR=/data \
    OUTLAWZ_OPEN_BROWSER=false \
    OUTLAWZ_DOCKER=true \
    OUTLAWZ_OLLAMA_URL=http://host.docker.internal:11434

# A plain user (uid 1000) owns the two volumes; the app never runs as root.
RUN groupadd --gid 1000 outlawz \
 && useradd --uid 1000 --gid 1000 --home-dir /home/outlawz --create-home --shell /usr/sbin/nologin outlawz \
 && mkdir -p /library /data \
 && chown outlawz:outlawz /library /data

COPY --from=wheel /dist/ /tmp/dist/
RUN pip install /tmp/dist/*.whl && rm -rf /tmp/dist

USER 1000:1000
WORKDIR /home/outlawz
VOLUME ["/library", "/data"]
EXPOSE 8642

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8642/api/health', timeout=4).status == 200 else 1)"]

CMD ["outlawz", "start", "--no-browser"]
