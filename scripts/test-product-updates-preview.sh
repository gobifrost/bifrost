#!/usr/bin/env bash
# Exercise the production feed against this worktree's running debug stack.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

docker build --file client/Dockerfile.playwright --tag bifrost-test-playwright:latest client
mkdir -p client/playwright-results/preview
source scripts/lib/test_helpers.sh
preview_project="$(BIFROST_PROJECT_PREFIX=bifrost-debug compute_project_name .)"
# This browser path owns receipts only for the seeded debug account.
# Reset before/after so it can prove first presentation without resetting the stack.
clear_test_receipts() {
    docker exec "${preview_project}-postgres-1" psql -U bifrost -d bifrost -q -c \
        "DELETE FROM product_update_receipts WHERE admin_id IN (SELECT id FROM users WHERE email = 'dev@gobifrost.com');" >/dev/null
}
clear_test_receipts
trap clear_test_receipts EXIT
# Credentials stay in the pipe and the Playwright child, never shell variables,
# Docker arguments, source files, traces, or printed status output.
./debug.sh status | docker run --rm -i --network "${preview_project}_default" \
    -e BIFROST_PREVIEW_URL="${BIFROST_PRODUCT_UPDATES_TEST_URL:-http://client}" \
    -v "$repo_root/client:/app" \
    -v /app/node_modules \
    bifrost-test-playwright:latest \
    node e2e/preview/run-product-updates-preview.cjs
