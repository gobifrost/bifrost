#!/usr/bin/env bash
# Shared helpers for test.sh and scripts/stack_*.sh.
# Source this file; do not execute directly.

# Derive a Docker Compose project name scoped to this worktree.
# Two worktrees with the same repo name get distinct stacks because the
# hash is taken over the absolute repo root path.
#
# Args:
#   $1 (optional): path to compute name from (default: current dir)
#
# Env:
#   BIFROST_PROJECT_PREFIX: prefix to use (default: "bifrost-test")
#                           debug.sh sets this to "bifrost-debug"
compute_project_name() {
    local repo_root
    repo_root="$(git -C "${1:-.}" rev-parse --show-toplevel 2>/dev/null)"
    if [ -z "$repo_root" ]; then
        echo "ERROR: compute_project_name must be called inside a git worktree" >&2
        return 1
    fi
    local hash
    hash="$(printf '%s' "$repo_root" | sha256sum | cut -c1-8)"
    printf '%s-%s' "${BIFROST_PROJECT_PREFIX:-bifrost-test}" "$hash"
}

# Wait for a compose service to be healthy (or responding on a probe command).
# Args: <compose-file> <service> <probe-command...>
# Returns 0 if ready, 1 on timeout.
wait_for_service() {
    local compose_file="$1"; shift
    local service="$1"; shift
    local max_attempts="${WAIT_MAX_ATTEMPTS:-60}"
    local i
    for ((i=1; i<=max_attempts; i++)); do
        if docker compose -f "$compose_file" exec -T "$service" "$@" > /dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    echo "ERROR: $service not ready after ${max_attempts} attempts" >&2
    return 1
}

# Wait for the API to respond 200 on /health/ready — a real readiness probe
# that confirms the API is serving traffic AND its dependencies (DB, Redis,
# S3) are reachable. This is the source-of-truth check used by both
# `stack up` (block until ready) and `require_stack_up` (verify ready before
# running tests). Closes the race where `docker ps` says "running" but uvicorn
# is still booting / migrations are still applying.
#
# Args: <compose-file> [max-seconds]
# Returns 0 if ready, 1 on timeout.
wait_for_api_ready() {
    local compose_file="$1"
    local max_seconds="${2:-${API_READY_TIMEOUT:-90}}"
    local i
    for ((i=1; i<=max_seconds; i++)); do
        if docker compose -f "$compose_file" exec -T api \
            curl -sf -o /dev/null http://localhost:8000/health/ready 2>/dev/null; then
            return 0
        fi
        sleep 1
    done
    echo "ERROR: api did not become ready on /health/ready within ${max_seconds}s" >&2
    return 1
}

# Is the stack for this worktree currently running?
#
# Reads container State columns directly via `--format` rather than relying on
# `--status running --quiet`. The `--status` filter has shown a CI-only race
# (issue #92) where the second `./test.sh` invocation sees no rows immediately
# after the first invocation finished `docker compose up -d --build` — a
# transient daemon state desync, possibly compounded by the buildx builder
# in the GitHub Actions runner.
#
# The fix has two parts:
#   1. Use `ps -a --format '{{.State}}'` and grep for a `running` row. This is
#      a different code path through compose's CLI than the `--status` filter,
#      and empirically does not exhibit the race.
#   2. Retry up to 5×1s on a "no rows seen" result. If the stack is genuinely
#      down (no rows at all) we bail after the second attempt to keep the
#      not-running case fast. Local stacks settle on the first try.
stack_is_up() {
    local project="$1"
    local compose_file="$2"
    local attempt
    for ((attempt=1; attempt<=5; attempt++)); do
        local states
        states="$(docker compose -p "$project" -f "$compose_file" ps -a --format '{{.State}}' 2>/dev/null)"
        if grep -q '^running$' <<<"$states"; then
            return 0
        fi
        # Genuinely down — no compose rows at all. Skip the rest of the retry
        # budget; this isn't the race we're trying to absorb.
        if [ -z "$states" ] && [ "$attempt" -ge 2 ]; then
            return 1
        fi
        sleep 1
    done
    # Last-ditch: dump compose's view of the project so the failure log carries
    # evidence the next time this triggers.
    echo "stack_is_up: no running state seen after 5 attempts. Compose ps output:" >&2
    docker compose -p "$project" -f "$compose_file" ps -a >&2 2>&1 || true
    return 1
}

# Export per-service logs for a running stack to LOG_DIR.
# Args: <project> <compose-file>
# No-op if LOG_DIR is empty or no services are running.
export_logs() {
    local project="$1"
    local compose_file="$2"
    local log_dir="${LOG_DIR:-}"
    [ -z "$log_dir" ] && return 0
    mkdir -p "$log_dir"
    local services
    services=$(docker compose -p "$project" -f "$compose_file" ps --services 2>/dev/null)
    [ -z "$services" ] && return 0
    for svc in $services; do
        docker compose -p "$project" -f "$compose_file" logs --no-color --timestamps "$svc" > "$log_dir/$svc.log" 2>&1 || true
        [ -s "$log_dir/$svc.log" ] || rm -f "$log_dir/$svc.log"
    done
}
