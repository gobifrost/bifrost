# Bifrost - Unified Container Image
# Used by all services: API, Discovery, Scheduler, Worker
# Multi-stage build for efficient image size

# =============================================================================
# Stage 1: Builder
# =============================================================================
# python:3.14.5-slim-trixie
FROM python:3.14-slim@sha256:7a500125bc50693f2214e842a621440a1b1b9cbb2188f74ab045d29ed2ea5856 AS builder

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
# Stage 2: Node dependencies
# =============================================================================
# Keep npm and its Debian node-* dependency tree out of the runtime image.
# The runtime services execute Node scripts directly with `node`.
FROM python:3.14-slim@sha256:7a500125bc50693f2214e842a621440a1b1b9cbb2188f74ab045d29ed2ea5856 AS node-deps

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# Install app compiler Node.js dependencies
COPY api/src/services/app_compiler/package.json api/src/services/app_compiler/package-lock.json* /app/src/services/app_compiler/
RUN cd /app/src/services/app_compiler && \
    test -f package-lock.json || { echo "package-lock.json is required for app_compiler"; exit 1; } && \
    npm ci --omit=dev

# Install app bundler Node.js dependencies (esbuild)
COPY api/src/services/app_bundler/package.json api/src/services/app_bundler/package-lock.json* /app/src/services/app_bundler/
RUN cd /app/src/services/app_bundler && \
    test -f package-lock.json || { echo "package-lock.json is required for app_bundler"; exit 1; } && \
    npm ci --omit=dev

# =============================================================================
# Stage 3: Runtime
# =============================================================================
# python:3.14.5-slim-trixie
FROM python:3.14-slim@sha256:7a500125bc50693f2214e842a621440a1b1b9cbb2188f74ab045d29ed2ea5856

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    libcap2 \
    curl \
    gosu \
    git \
    awscli \
    nodejs \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.14/site-packages /usr/local/lib/python3.14/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code (paths relative to root context)
COPY api/src/ /app/src/
COPY api/alembic/ /app/alembic/
COPY api/alembic.ini /app/
COPY api/bifrost/ /app/bifrost/
COPY api/bifrost.py /app/
COPY api/scripts/ /app/scripts/
COPY api/shared/ /app/shared/

# Copy app compiler Node.js runtime
COPY api/src/services/app_compiler/compile.js /app/src/services/app_compiler/
COPY api/src/services/app_compiler/tailwind.js /app/src/services/app_compiler/
COPY --from=node-deps /app/src/services/app_compiler/node_modules /app/src/services/app_compiler/node_modules

# Copy app bundler Node.js runtime (esbuild)
COPY --from=node-deps /app/src/services/app_bundler/node_modules /app/src/services/app_bundler/node_modules

# Create non-root user
RUN useradd -m -u 1000 bifrost && \
    chown -R bifrost:bifrost /app

# Create directories for workspace and temp
RUN mkdir -p /workspace /tmp/bifrost && \
    chown -R bifrost:bifrost /workspace /tmp/bifrost

# Copy entrypoint script
COPY api/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Run non-root by default. Compose/test harnesses that need startup ownership
# repair can still override this to root; entrypoint drops back to bifrost.
USER bifrost
ENTRYPOINT ["/entrypoint.sh"]

# Environment variables
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

ARG BIFROST_VERSION=unknown
ENV BIFROST_VERSION=${BIFROST_VERSION}

# No default CMD - let docker-compose specify the command per service
