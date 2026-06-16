#!/bin/bash
# Orchestrate the full docs screenshot pipeline:
#   1. decide-captures.mjs → list of entry IDs
#   2. test.sh client docs (capture spec runs inside playwright-runner)
#   3. post-process.mjs → crop/callout, pixel-diff, commit changed PNGs
#
# Args:
#   --docs-repo <path>    required (absolute)
#   --bifrost-repo <path> required (absolute)
#   --full                bypass diff-mode shortlist and capture every entry
#   --ids <id1,id2>       capture only the named entries (overrides --full)
#   --threshold <0..1>    pixel-diff threshold for commit (default 0.001)
set -euo pipefail

DOCS_REPO=""
BIFROST_REPO=""
MODE_FLAG=""
IDS=""
THRESHOLD="0.001"

while [ $# -gt 0 ]; do
    case "$1" in
        --docs-repo) DOCS_REPO="$2"; shift 2 ;;
        --bifrost-repo) BIFROST_REPO="$2"; shift 2 ;;
        --full) MODE_FLAG="--full"; shift ;;
        --ids) IDS="$2"; shift 2 ;;
        --threshold) THRESHOLD="$2"; shift 2 ;;
        -h|--help)
            sed -n '2,12p' "$0"
            exit 0
            ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

if [ -z "$DOCS_REPO" ] || [ -z "$BIFROST_REPO" ]; then
    echo "--docs-repo and --bifrost-repo are required" >&2
    exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# decide-captures/post-process need their own node deps (js-yaml, sharp, …).
# Auto-install on first run so step [1/3] doesn't die with ERR_MODULE_NOT_FOUND.
if [ ! -d "$SCRIPT_DIR/node_modules" ]; then
    echo "=== installing scripts/docs deps (first run) ==="
    (cd "$SCRIPT_DIR" && npm install --silent)
fi

# Reset the test-stack state before capturing. The capture spec's global setup
# registers fixture users (bob@example.com etc.); a non-fresh DB fails with
# "Resource already exists" and aborts the whole run. Reset makes capture
# deterministic. Best-effort: skip if the stack isn't up yet (test.sh boots it).
(cd "$BIFROST_REPO" && ./test.sh stack reset >/dev/null 2>&1) || true

# Wipe stale tmp captures from prior runs. They are owned by root (containers
# run as root), so use sudo if available, else just remove what we can.
if [ -d "$DOCS_REPO/.tmp-captures" ]; then
    rm -rf "$DOCS_REPO/.tmp-captures" 2>/dev/null || sudo rm -rf "$DOCS_REPO/.tmp-captures" 2>/dev/null || true
fi

echo "=== [1/3] decide-captures ==="
DECIDE_ARGS=(--docs-repo "$DOCS_REPO" --bifrost-repo "$BIFROST_REPO")
if [ -n "$IDS" ]; then
    DECIDE_ARGS+=(--ids "$IDS")
elif [ -n "$MODE_FLAG" ]; then
    DECIDE_ARGS+=("$MODE_FLAG")
fi
CAPTURE_IDS="$(node "$SCRIPT_DIR/decide-captures.mjs" "${DECIDE_ARGS[@]}")"

if [ -z "$CAPTURE_IDS" ]; then
    echo "No entries to capture (everything is up to date)."
    exit 0
fi

echo "Capturing: $CAPTURE_IDS"

echo "=== [2/3] capture ==="
export DOCS_REPO_PATH="$DOCS_REPO"
export DOCS_CAPTURE_IDS="$CAPTURE_IDS"
# Do NOT let a single failed entry abort the run — post-process must still place
# the entries that DID capture (results.json records per-entry status). Capture
# the exit code and surface it at the end instead.
CAPTURE_RC=0
(cd "$BIFROST_REPO" && ./test.sh client docs) || CAPTURE_RC=$?
if [ "$CAPTURE_RC" -ne 0 ]; then
    echo "⚠ capture step exited $CAPTURE_RC — some entries failed; post-processing the ones that succeeded."
fi

echo "=== [3/3] post-process ==="
# Post-process places every entry whose results.json status is 'captured' and
# skips 'error' ones, so a partial capture still lands its good PNGs.
node "$SCRIPT_DIR/post-process.mjs" \
    --docs-repo "$DOCS_REPO" \
    --bifrost-repo "$BIFROST_REPO" \
    --threshold "$THRESHOLD"

if [ "$CAPTURE_RC" -ne 0 ]; then
    echo "⚠ Some capture entries failed (see [2/3] above). Successful captures were placed; fix the failing entries' wait_for/mocks and re-run with --ids for just those."
    exit "$CAPTURE_RC"
fi
