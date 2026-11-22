# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS web
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN --mount=type=secret,id=build_ca \
    if [ -s /run/secrets/build_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/build_ca; fi && \
    npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:0.13.9 AS uv

FROM python:3.12-slim-bookworm
WORKDIR /app
COPY --from=uv /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_SYSTEM_CERTS=1 \
    STATE_DB=/data/13-labs.db \
    PATH=/app/.venv/bin:$PATH

COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=secret,id=build_ca \
    if [ -s /run/secrets/build_ca ]; then export SSL_CERT_FILE=/run/secrets/build_ca; fi && \
    uv sync --frozen --no-dev --no-install-project
COPY src/ src/
RUN --mount=type=secret,id=build_ca \
    if [ -s /run/secrets/build_ca ]; then export SSL_CERT_FILE=/run/secrets/build_ca; fi && \
    uv sync --frozen --no-dev
COPY --from=web /build/web/dist/ web/dist/
COPY scripts/export_replay.py scripts/start_demo.sh scripts/

ARG COMMIT_SHA=dev
ENV COMMIT_SHA=$COMMIT_SHA
RUN mkdir -p /data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c 'import os, urllib.request; urllib.request.urlopen("http://127.0.0.1:" + os.getenv("PORT", "8080") + "/api/health", timeout=4)'

CMD ["sh", "scripts/start_demo.sh"]
