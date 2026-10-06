#!/usr/bin/env bash
# Exercise the development-only feed against this worktree's running debug stack.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

docker build --file client/Dockerfile.playwright --tag bifrost-test-playwright:latest client
mkdir -p client/playwright-results/preview
# Credentials stay in the pipe and the Playwright child, never shell variables,
# Docker arguments, source files, traces, or printed status output.
./debug.sh status | docker run --rm -i --network host \
    -v "$repo_root/client:/app" \
    -v /app/node_modules \
    bifrost-test-playwright:latest \
    node e2e/preview/run-product-updates-preview.cjs
