#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPORTING_DIR="$(dirname "$SCRIPT_DIR")/deploy/usage-reporting"
REPORTING_TEST_IMAGE="${BIFROST_REPORTING_TEST_IMAGE:-node:24.13.0-bookworm-slim}"

docker run --rm \
    --user "$(id -u):$(id -g)" \
    --env npm_config_cache=/tmp/npm-cache \
    --env WRANGLER_SEND_METRICS=false \
    --env CI=1 \
    --mount "type=bind,src=$REPORTING_DIR,dst=/workspace" \
    --mount type=bind,src=/etc/ssl/certs/ca-certificates.crt,dst=/etc/ssl/certs/ca-certificates.crt,readonly \
    --workdir /workspace \
    "$REPORTING_TEST_IMAGE" \
    sh -c 'npm ci --no-audit --no-fund && npm run check && npm test -- "$@"' sh "$@"
