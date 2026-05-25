# Bifrost - Unified Container Image
# Used by all services: API, Discovery, Scheduler, Worker
# Multi-stage build for efficient image size

# =============================================================================
# Stage 1: Builder
# =============================================================================
# python:3.14-slim
FROM python:3.14-slim@sha256:c845af9399020c7e562969a13689e929074a10fd057acd1b1fad06a2fb068e97 AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency manifests and install.
# We install from requirements.lock with --require-hashes so every transitive
# dep is pinned by sha256, satisfying Scorecard's Pinned-Dependencies check.
# pyproject.toml is the human-edited source; regen instructions are in
# the header of requirements.lock.
#
# We deliberately do NOT run `pip install --upgrade pip` first — Scorecard
# flags unpinned `pip install <pkg>` invocations, and the pip shipped in
# python:3.14-slim already supports --require-hashes (>= 8.0).
COPY pyproject.toml requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

# =============================================================================
# Stage 2: Runtime
# =============================================================================
# python:3.14-slim
FROM python:3.14-slim@sha256:c845af9399020c7e562969a13689e929074a10fd057acd1b1fad06a2fb068e97

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    gosu \
    git \
    awscli \
    nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.14/site-packages /usr/local/lib/python3.14/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Install `bifrost` CLI shim. The bifrost Python package is mounted at
# /app/bifrost/ via PYTHONPATH=/app, so `python -m bifrost` works. The shim
# below exposes that as the `bifrost` console script on PATH, matching the
# console-script entry declared in api/pyproject.toml. Workflows shell out
# to `bifrost <verb>` from worker subprocesses (see Buildfrost spec at
# Sync/Projects/buildfrost/backend-ask.md).
RUN printf '#!/bin/sh\nexec python -m bifrost "$@"\n' > /usr/local/bin/bifrost \
    && chmod +x /usr/local/bin/bifrost

# Copy application code (paths relative to root context)
COPY api/src/ /app/src/
COPY api/shared/ /app/shared/
COPY api/alembic/ /app/alembic/
COPY api/alembic.ini /app/
COPY api/bifrost/ /app/bifrost/
COPY api/bifrost.py /app/
COPY api/scripts/ /app/scripts/

# Install app compiler Node.js dependencies
COPY api/src/services/app_compiler/package.json api/src/services/app_compiler/package-lock.json* /app/src/services/app_compiler/
COPY api/src/services/app_compiler/compile.js /app/src/services/app_compiler/
COPY api/src/services/app_compiler/tailwind.js /app/src/services/app_compiler/
RUN cd /app/src/services/app_compiler && npm install --production

# Install app bundler Node.js dependencies (esbuild)
COPY api/src/services/app_bundler/package.json api/src/services/app_bundler/package-lock.json* /app/src/services/app_bundler/
RUN cd /app/src/services/app_bundler && npm install --production

# Create non-root user
RUN useradd -m -u 1000 bifrost && \
    chown -R bifrost:bifrost /app

# Create directories for workspace and temp
RUN mkdir -p /workspace /tmp/bifrost && \
    chown -R bifrost:bifrost /workspace /tmp/bifrost

# Copy entrypoint script
COPY api/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Don't switch to bifrost here - entrypoint handles user switching
# This allows the entrypoint to fix volume permissions when needed
ENTRYPOINT ["/entrypoint.sh"]

# Environment variables
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

ARG BIFROST_VERSION=unknown
ENV BIFROST_VERSION=${BIFROST_VERSION}

# No default CMD - let docker-compose specify the command per service
