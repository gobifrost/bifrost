# Bifrost - Unified Container Image
# Used by all services: API, Discovery, Scheduler, Worker
# Multi-stage build for efficient image size

# Use the same supported Node runtime for every App build surface.
FROM node:26.13.1-bookworm-slim@sha256:5df79989ed685e09f0a98019db4cebfb22fb9df1a53c0161664a011f7ff6b0f1 AS node-toolchain

# Native Python dependencies require Rust >=1.98; keep it build-only and pinned.
FROM rust:1.100.0-slim-bookworm@sha256:7c7d4eb4d05b7781187a327809466cfddf29753f6defdc3d19a0e291951b7a37 AS rust-toolchain

# =============================================================================
# Stage 1: Builder
# =============================================================================
# Python security patch: interpreter denylist and patched bundled pip.
FROM python:3.16.0a2-slim@sha256:b743a8ab2708d630f1c1c008ef5c99d4b635ec16bb2012284f731d9957a14a1c AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    uuid-dev \
    curl \
    xz-utils \
    libpq-dev \
    libxml2-dev \
    libxslt1-dev \
    libffi-dev \
    zlib1g-dev \
    libjpeg-dev \
    libtiff-dev \
    libfreetype-dev \
    liblcms2-dev \
    libwebp-dev \
    libopenjp2-7-dev \
    libraqm-dev \
    libavif-dev \
    libxcb1-dev \
    && rm -rf /var/lib/apt/lists/*

# Use the verified compiler instead of the older distro Rust packages.
COPY --from=rust-toolchain /usr/local/cargo /opt/cargo
COPY --from=rust-toolchain /usr/local/rustup /opt/rustup
ENV CARGO_HOME=/opt/cargo RUSTUP_HOME=/opt/rustup
ENV PATH="/opt/cargo/bin:${PATH}"
RUN rustc --version && cargo --version

# Copy dependency manifests and install.
# We install from requirements.lock with --require-hashes so every transitive
# dep is pinned by sha256, satisfying Scorecard's Pinned-Dependencies check.
# pyproject.toml is the human-edited source; regen instructions are in
# the header of requirements.lock.
#
# requirements.lock is the runtime-only closure: test/debug packages live
# in requirements-dev.lock (installed by dev/CI images) and never ship in this
# production image. See [project.optional-dependencies] in pyproject.toml
# (issue #1379).
#
# We deliberately do NOT run `pip install --upgrade pip` first — Scorecard
# flags unpinned `pip install <pkg>` invocations. The bundled pip already
# supports --require-hashes (>= 8.0).
COPY pyproject.toml requirements.lock ./
# Pillow 12.3 checks this obsolete C89 capability macro, removed in Python 3.16a2.
# Our GCC supports prototypes; scope the definition to dependency compilation.
RUN CPPFLAGS="-DHAVE_PROTOTYPES=1" pip install --no-cache-dir --require-hashes -r requirements.lock

# aiormq 7.2.5 still assigns an asyncio marker removed in Python 3.16.
COPY api/scripts/patch_aiormq.py /tmp/patch_aiormq.py
RUN python /tmp/patch_aiormq.py && rm /tmp/patch_aiormq.py

# Repair UUID copy/pickle crashes in the pinned interpreter before packaging.
COPY api/scripts/build_uuid_extension.sh /tmp/build_uuid_extension.sh
RUN sh /tmp/build_uuid_extension.sh && rm /tmp/build_uuid_extension.sh

# =============================================================================
# Stage 2: Runtime
# =============================================================================
# Python security patch: interpreter denylist and patched bundled pip.
FROM python:3.16.0a2-slim@sha256:b743a8ab2708d630f1c1c008ef5c99d4b635ec16bb2012284f731d9957a14a1c

WORKDIR /app
ARG BIFROST_VERSION=unknown

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    libxml2 \
    libxslt1.1 \
    libffi8 \
    libjpeg62-turbo \
    libtiff6 \
    libfreetype6 \
    liblcms2-2 \
    libwebp7 \
    libwebpmux3 \
    libwebpdemux2 \
    libopenjp2-7 \
    libraqm0 \
    libavif16 \
    libxcb1 \
    libcairo2 \
    curl \
    gosu \
    libnss-wrapper \
    git \
    libjemalloc2 \
    libatomic1 \
    libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

# npm 10 also crashes in peer-set resolution on valid retained App sources.
# Vendor a supported Node runtime, then install the verified npm resolver.
COPY --from=node-toolchain /usr/local/bin/node /usr/local/bin/node
COPY --from=node-toolchain /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
COPY api/scripts/install-npm.sh /usr/local/bin/install-npm.sh
RUN sh /usr/local/bin/install-npm.sh && rm /usr/local/bin/install-npm.sh

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.16/site-packages /usr/local/lib/python3.16/site-packages
COPY --from=builder /usr/local/lib/python3.16/lib-dynload/_uuid*.so /usr/local/lib/python3.16/lib-dynload/
COPY --from=builder /usr/local/bin /usr/local/bin
RUN mv /usr/local/bin/uvicorn /usr/local/bin/uvicorn-real

# Keep OpenSSL's system/private CA selection. Exclude only retired distro
# roots, including their hashed-directory entries, and persist those exclusions.
COPY api/scripts/prune_retired_ca.py /tmp/prune_retired_ca.py
RUN python /tmp/prune_retired_ca.py && rm /tmp/prune_retired_ca.py

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
COPY api/product-updates.bundle.json /app/product-updates.bundle.json
COPY api/alembic/ /app/alembic/
COPY api/alembic.ini /app/
COPY api/bifrost/ /app/bifrost/
COPY api/bifrost.py /app/
COPY api/scripts/ /app/scripts/
COPY --chmod=644 LICENSE /app/LICENSE
COPY assets/ /app/assets/
RUN chmod -R a+rX /app/assets

# Build the CLI once as an immutable image artifact. The API serves this file;
# it never packages the live source tree in a request handler.
RUN PYTHONPATH=/app python /app/scripts/build_cli_artifact.py \
      --source /app/bifrost \
      --output /app/artifacts \
      --version "${BIFROST_VERSION}"

# Compile platform source after CLI packaging, before npm adds dependency trees.
# Checked hashes preserve source-change invalidation; keep the full preload graph.
RUN python -m compileall -f --invalidation-mode checked-hash -q /app/src /app/shared /app/bifrost

# Install app compiler Node.js dependencies
COPY api/src/services/app_compiler/package.json api/src/services/app_compiler/package-lock.json* /app/src/services/app_compiler/
COPY api/src/services/app_compiler/compile.js /app/src/services/app_compiler/
COPY api/src/services/app_compiler/tailwind.js /app/src/services/app_compiler/
RUN cd /app/src/services/app_compiler && npm ci --omit=dev && npm cache clean --force

# Install app bundler Node.js dependencies (esbuild)
COPY api/src/services/app_bundler/package.json api/src/services/app_bundler/package-lock.json* /app/src/services/app_bundler/
RUN cd /app/src/services/app_bundler && npm ci --omit=dev && npm cache clean --force

# SDK package builder (esbuild) + the v2 `bifrost` SDK source it bundles. The
# SDK source of truth lives in client/src/lib/app-sdk; it is copied here so
# /api/sdk/download can bundle + serve the installable `bifrost` package without
# the client/ tree at runtime. A unit test enforces this file list stays in sync.
COPY api/src/services/sdk_package/package.json api/src/services/sdk_package/package-lock.json /app/src/services/sdk_package/
COPY api/src/services/sdk_package/build_sdk.js api/src/services/sdk_package/build_types.js api/src/services/sdk_package/tsconfig.types.json /app/src/services/sdk_package/
RUN cd /app/src/services/sdk_package && npm ci --omit=dev && npm cache clean --force
COPY client/src/lib/app-sdk/provider.tsx \
     client/src/lib/app-sdk/tables.ts \
     client/src/lib/app-sdk/transport.ts \
     client/src/lib/app-sdk/use-table.ts \
     client/src/lib/app-sdk/use-infinite-table.ts \
     client/src/lib/app-sdk/ws-client.ts \
     client/src/lib/app-sdk/execution-stream.ts \
     client/src/lib/app-sdk/use-workflow.ts \
     client/src/lib/app-sdk/use-workflow-hooks.ts \
     client/src/lib/app-sdk/files.ts \
     client/src/lib/app-sdk/use-files.ts \
     client/src/lib/app-sdk/use-user.tsx \
     client/src/lib/app-sdk/use-api-query.ts \
     client/src/lib/app-sdk/use-branding.ts \
     client/src/lib/app-sdk/use-organizations.ts \
     client/src/lib/app-sdk/branding-theme.ts \
     client/src/lib/app-sdk/brand-palette.ts \
     client/src/lib/app-sdk/bifrost-header.tsx \
     client/src/lib/app-sdk/errors.ts \
     client/src/lib/app-sdk/hosted-binding.tsx \
     client/src/lib/app-sdk/hosted-contract.ts \
     client/src/lib/app-sdk/hosted-loader.ts \
     /app/src/services/sdk_package/sdk_src/
COPY client/src/lib/app-sdk/index.v2.ts /app/src/services/sdk_package/sdk_src/index.v2.ts
COPY client/src/lib/app-sdk/index.v2.ts /app/src/services/sdk_package/sdk_src/index.ts
COPY client/src/lib/v1.d.ts /app/src/services/sdk_package/sdk_src/v1.d.ts
# Contract version stamp (read directly by Python, not bundled by esbuild) —
# see sdk_contract_version() in sdk_package/__init__.py.
COPY client/src/lib/app-sdk/sdk-contract.json /app/src/services/sdk_package/sdk_src/sdk-contract.json
RUN mkdir -p /app/artifacts/app-sdk \
    && node /app/src/services/sdk_package/build_sdk.js hosted \
      /app/src/services/sdk_package/sdk_src /app/artifacts/app-sdk/hosted-v1.js

# Create non-root user
RUN useradd -m -u 1000 bifrost && \
    chown -R bifrost:bifrost /app

# Create directories for workspace and temp
RUN mkdir -p /workspace /tmp/bifrost && \
    chown -R bifrost:bifrost /workspace /tmp/bifrost

# Copy shared API startup scripts. The Uvicorn shim keeps Kubernetes command
# overrides on the API allocator path without invoking the root entrypoint.
COPY api/entrypoint.sh /entrypoint.sh
COPY api/scripts/api-allocator.sh /usr/local/bin/api-allocator.sh
COPY api/scripts/uvicorn-shim.sh /usr/local/bin/uvicorn
RUN chmod 755 /entrypoint.sh /usr/local/bin/api-allocator.sh /usr/local/bin/uvicorn

# Don't switch to bifrost here - entrypoint handles user switching
# This allows the entrypoint to fix volume permissions when needed
ENTRYPOINT ["/entrypoint.sh"]

# Environment variables
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

ENV BIFROST_VERSION=${BIFROST_VERSION}

# No default CMD - let docker-compose specify the command per service
