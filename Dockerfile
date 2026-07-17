# syntax=docker/dockerfile:1

# Base-image digest pinning and non-root runtime follow:
# https://docs.docker.com/build/building/best-practices/
ARG PYTHON_IMAGE=python:3.11.15-slim-bookworm@sha256:28255a3ace7eb4c48bc1b57b90af29e1bc82b4fd6c60614a8e3dce61b87ff941

FROM ${PYTHON_IMAGE} AS builder

# uv's documented non-editable multi-stage pattern:
# https://docs.astral.sh/uv/guides/integration/docker/#non-editable-installs
COPY --from=ghcr.io/astral-sh/uv@sha256:f8f31ad675ac1a6e17ccabe0e5bbb7fe941b2a27f38cb509c799870570b2f1a7 /uv /uvx /bin/

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

RUN groupadd --gid 10001 watermark-worker \
    && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/watermark-worker watermark-worker \
    && mkdir -p /tmp/watermark-worker \
    && chown 10001:10001 /tmp/watermark-worker

WORKDIR /app
COPY --from=builder --chown=10001:10001 /app/.venv /app/.venv

USER 10001:10001
ENTRYPOINT ["python", "-m", "watermark_worker"]
