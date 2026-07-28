# syntax=docker/dockerfile:1@sha256:87999aa3d42bdc6bea60565083ee17e86d1f3339802f543c0d03998580f9cb89

# Base-image digest pinning and non-root runtime follow:
# https://docs.docker.com/build/building/best-practices/
ARG PYTHON_IMAGE=python:3.11.15-slim-trixie@sha256:00af38ae2ed311628970782e8a2d7f014d8909dbc63cb97bc0a158187f4db045

FROM ${PYTHON_IMAGE} AS builder

# uv's documented non-editable multi-stage pattern:
# https://docs.astral.sh/uv/guides/integration/docker/#non-editable-installs
COPY --from=ghcr.io/astral-sh/uv:0.10.9@sha256:10902f58a1606787602f303954cea099626a4adb02acbac4c69920fe9d278f82 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable --no-cache

FROM ${PYTHON_IMAGE} AS runtime

ENV HOME=/home/watermark-worker \
    PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TMPDIR=/tmp/watermark-worker

RUN python -m pip uninstall --yes pip setuptools wheel \
    && groupadd --gid 10001 watermark-worker \
    && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/watermark-worker watermark-worker \
    && mkdir -p /tmp/watermark-worker \
    && chown 10001:10001 /tmp/watermark-worker

WORKDIR /app
COPY --from=builder --chown=10001:10001 /app/.venv /app/.venv

USER 10001:10001
ENTRYPOINT ["python", "-m", "watermark_worker"]
