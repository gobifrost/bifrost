#!/usr/bin/env bash
# Bifrost test runner — verb-style subcommand interface.
#
# Stack lifecycle (long-lived, per worktree):
#   ./test.sh stack up                  Boot the stack.
#   ./test.sh stack down                Tear it down + remove volumes.
#   ./test.sh stack reset               Fast state reset (DB clone, redis flush, object storage wipe).
#   ./test.sh stack status              Print project name and running services.
#
# Backend tests (stack must be up):
#   ./test.sh                           Unit tests only (fast default).
#   ./test.sh unit                      Same as above.
#   ./test.sh e2e                       Backend e2e tests.
#   ./test.sh all                       All backend tests, including slow tests.
#   ./test.sh tests/path/... [args]     Pass through to pytest.
#
# Quality checks:
#   ./test.sh quality api               Run API pyright + ruff inside Docker.
#
# Client tests:
#   ./test.sh client unit               Vitest on the host (no stack).
#   ./test.sh client e2e                Playwright in the stack's client container.
#   ./test.sh client smoke              Critical zero-retry Playwright merge gate.
#   ./test.sh client nightly            Full product Playwright (docs capture excluded).
#   ./test.sh client e2e --screenshots  Capture a screenshot for every test (UX review).
#   ./test.sh client e2e e2e/auth.unauth.spec.ts   Pass through to playwright.
#
# CI escape hatch:
#   ./test.sh pre-pr                    Required local PR/merge gate for a clean commit.
#   ./test.sh ci                        Full isolated run: up, all tests, down.
#
# Global flags (apply to most subcommands):
#   --no-reset    Skip state reset before running tests.
#   --coverage    Enable coverage reporting (backend only).
#   --wait        On failure, pause before cleanup.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# shellcheck source=scripts/lib/test_helpers.sh
source "$SCRIPT_DIR/scripts/lib/test_helpers.sh"

COMPOSE_FILE="docker-compose.test.yml"
export COMPOSE_PROJECT_NAME
COMPOSE_PROJECT_NAME="$(compute_project_name .)"

LOG_DIR="/tmp/bifrost-$COMPOSE_PROJECT_NAME"
mkdir -p "$LOG_DIR"
# Pre-create the fixture subdir the api container bind-mounts (install/preview-repo
# e2e tests stage file:// git repos here). Creating it host-side first means Docker
# binds an existing host-owned dir instead of auto-creating a root-owned mountpoint.
mkdir -p "$LOG_DIR/solution-repo-fixtures"
mkdir -p "$SCRIPT_DIR/client/playwright-results"
chmod 777 "$SCRIPT_DIR/client/playwright-results" 2>/dev/null || true
export LOG_DIR

# Load .env.test for optional secrets (GitHub PAT, LLM keys, etc.). Since the
# file is intentionally ignored, linked worktrees do not receive it from Git;
# fall back to the primary checkout's copy when the worktree has none.
BIFROST_TEST_ENV_FILE="$SCRIPT_DIR/.env.test"
if [ ! -f "$BIFROST_TEST_ENV_FILE" ]; then
    BIFROST_COMMON_GIT_DIR="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
    if [ -n "$BIFROST_COMMON_GIT_DIR" ]; then
        BIFROST_PRIMARY_TEST_ENV_FILE="$(dirname "$BIFROST_COMMON_GIT_DIR")/.env.test"
        if [ -f "$BIFROST_PRIMARY_TEST_ENV_FILE" ]; then
            BIFROST_TEST_ENV_FILE="$BIFROST_PRIMARY_TEST_ENV_FILE"
        fi
    fi
fi
if [ -f "$BIFROST_TEST_ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$BIFROST_TEST_ENV_FILE"
    set +a
fi

# =============================================================================
# Common helpers
# =============================================================================

print_project() {
    echo "Worktree: $(git rev-parse --show-toplevel)"
    echo "Project:  $COMPOSE_PROJECT_NAME"
}

require_stack_up() {
    if ! stack_is_up "$COMPOSE_PROJECT_NAME" "$COMPOSE_FILE"; then
        echo "ERROR: stack not running for this worktree. Run:" >&2
        echo "  ./test.sh stack up" >&2
        exit 1
    fi
    # Containers may report "running" before the API is actually serving
    # traffic (uvicorn boot, alembic, app startup hooks). Block here until
    # /health/ready returns 200 so callers don't race the API. Idempotent
    # and fast (<1s) when the API is already ready.
    if ! wait_for_api_ready "$COMPOSE_FILE"; then
        echo "ERROR: stack containers running but API not serving traffic." >&2
        echo "  Check: docker compose -f $COMPOSE_FILE logs api" >&2
        exit 1
    fi
}

reset_state() {
    echo "Resetting state..."

    # Reload scheduler-fixtures too: its source is bind-mounted but its simple
    # HTTP server has no hot reloader. Without this, a changed deterministic
    # fixture can keep serving its pre-edit behavior for an entire broad run.
    docker compose -f "$COMPOSE_FILE" stop \
        api worker scheduler scheduler-fixtures pgbouncer 2>/dev/null || true

    docker compose -f "$COMPOSE_FILE" exec -T postgres psql -U bifrost -d postgres -c \
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'bifrost_test' AND pid <> pg_backend_pid();" \
        > /dev/null 2>&1 || true
    docker compose -f "$COMPOSE_FILE" exec -T postgres psql -U bifrost -d postgres -c \
        "DROP DATABASE IF EXISTS bifrost_test;" > /dev/null
    docker compose -f "$COMPOSE_FILE" exec -T postgres psql -U bifrost -d postgres -c \
        "CREATE DATABASE bifrost_test TEMPLATE bifrost_test_template;" > /dev/null

    docker compose -f "$COMPOSE_FILE" exec -T redis redis-cli FLUSHDB > /dev/null
    docker compose -f "$COMPOSE_FILE" rm -sf seaweedfs > /dev/null
    docker compose -f "$COMPOSE_FILE" up -d seaweedfs > /dev/null

    rm -f client/e2e/.auth/credentials.json \
          client/e2e/.auth/platform_admin.json \
          client/e2e/.auth/org1_user.json \
          client/e2e/.auth/org2_user.json

    docker compose -f "$COMPOSE_FILE" start pgbouncer > /dev/null
    wait_for_service "$COMPOSE_FILE" pgbouncer pg_isready -h localhost -p 5432 -U bifrost
    docker compose -f "$COMPOSE_FILE" --profile e2e start \
        api worker scheduler scheduler-fixtures > /dev/null
    wait_for_api_ready "$COMPOSE_FILE"

    echo "State reset complete."
}

prepare_test_state() {
    local boot_marker="$LOG_DIR/.clean-boot-consumed"

    # GitHub-hosted jobs boot one new, empty Compose project and run exactly one
    # suite. Re-cloning the template DB and restarting every service immediately
    # afterward is equivalent state with roughly a minute of avoidable churn.
    # The marker makes this a one-shot optimization: a second suite in the same
    # project still receives the normal full reset.
    if [ "${BIFROST_TEST_USE_CLEAN_BOOT:-0}" = "1" ] && [ ! -e "$boot_marker" ]; then
        mkdir -p "$LOG_DIR"
        touch "$boot_marker"
        echo "Using clean state from the newly booted test stack."
        return
    fi

    reset_state
}

# =============================================================================
# stack up|down|reset|status
# =============================================================================

cmd_stack() {
    local subcmd="${1:-status}"
    shift || true

    case "$subcmd" in
        up) stack_up "$@" ;;
        down) stack_down ;;
        reset) stack_reset ;;
        status) stack_status ;;
        *)
            echo "Unknown stack subcommand: $subcmd" >&2
            echo "Valid: up, down, reset, status" >&2
            exit 2
            ;;
    esac
}

stack_up() {
    print_project

    if stack_is_up "$COMPOSE_PROJECT_NAME" "$COMPOSE_FILE"; then
        # Idempotent: still confirm API is serving traffic before returning
        # success. A previous `stack up` may have exited before the API
        # finished booting; without this, the next test command would race.
        if wait_for_api_ready "$COMPOSE_FILE"; then
            echo "Stack already up."
            return 0
        fi
        echo "Stack containers running but API not ready — see logs above." >&2
        exit 1
    fi

    echo "Booting infrastructure..."
    docker compose -f "$COMPOSE_FILE" up -d postgres rabbitmq redis seaweedfs

    wait_for_service "$COMPOSE_FILE" postgres pg_isready -U bifrost -d postgres
    wait_for_service "$COMPOSE_FILE" rabbitmq rabbitmq-diagnostics check_running
    wait_for_service "$COMPOSE_FILE" redis redis-cli ping

    docker compose -f "$COMPOSE_FILE" up -d pgbouncer
    wait_for_service "$COMPOSE_FILE" pgbouncer pg_isready -h localhost -p 5432 -U bifrost

    # CI pre-builds the API dev image via docker/build-push-action with GHA cache
    # and tags it as bifrost-test-api-dev:latest before calling stack up.
    # Setting BIFROST_SKIP_BUILD=1 tells compose to
    # use those local images directly instead of building. Local dev leaves
    # this unset so `--build` continues to apply (image layer cache makes the
    # rebuilds fast after the first one).
    local build_flag="--build"
    if [ "${BIFROST_SKIP_BUILD:-0}" = "1" ]; then
        build_flag="--no-build"
        echo "BIFROST_SKIP_BUILD=1 — using pre-built images from local docker."
    fi

    echo "Building template database..."
    "$SCRIPT_DIR/scripts/stack_template_init.sh"

    echo "Starting API + Worker + Scheduler..."
    docker compose -f "$COMPOSE_FILE" --profile e2e up -d "$build_flag"
    echo "Waiting for API to be serving traffic on /health/ready..."
    wait_for_api_ready "$COMPOSE_FILE"

    echo ""
    echo "Stack is up. Project: $COMPOSE_PROJECT_NAME"
}

stack_down() {
    print_project
    echo "Tearing down stack..."
    export_logs "$COMPOSE_PROJECT_NAME" "$COMPOSE_FILE"
    docker compose -f "$COMPOSE_FILE" --profile e2e --profile test --profile client down -v
    echo "Done."
}

stack_reset() {
    require_stack_up
    # Stop DB consumers before template_init runs — template_init may need to
    # DROP bifrost_test (when migrations changed), and it cannot do so while
    # api/worker/scheduler hold live connections to it. reset_state will
    # restart them afterward. Don't stop pgbouncer — compose's `start` later
    # won't re-attach its network endpoint cleanly, and its pool only proxies
    # bifrost_test anyway (nothing here connects to bifrost_test through it
    # while it's stopped).
    docker compose -f "$COMPOSE_FILE" stop api worker scheduler 2>/dev/null || true
    "$SCRIPT_DIR/scripts/stack_template_init.sh"
    reset_state
}

stack_status() {
    print_project
    if stack_is_up "$COMPOSE_PROJECT_NAME" "$COMPOSE_FILE"; then
        echo "Status: UP"
        docker compose -f "$COMPOSE_FILE" ps
    else
        echo "Status: DOWN"
    fi
}

# =============================================================================
# Test subcommands
# =============================================================================

run_pytest() {
    local runner_lock_fd runner_name runner_status

    # One worktree owns one mutable Docker test stack.  A second pytest process
    # against that stack can reset the database underneath the first process and
    # produce order-impossible failures.  The flock covers the normal case; the
    # stable container name is a second guard when the parent shell is killed and
    # Docker leaves the already-running one-off container behind.
    exec {runner_lock_fd}>"$LOG_DIR/test-runner.lock"
    if ! flock -n "$runner_lock_fd"; then
        echo "ERROR: another pytest run is already using this worktree's test stack." >&2
        echo "Wait for it to finish before starting another test command." >&2
        exec {runner_lock_fd}>&-
        return 1
    fi

    runner_name="${COMPOSE_PROJECT_NAME}-pytest-runner"
    if docker container inspect "$runner_name" > /dev/null 2>&1; then
        echo "ERROR: pytest runner container '$runner_name' still exists." >&2
        echo "Wait for it to finish; if its parent was interrupted, stop that container explicitly." >&2
        exec {runner_lock_fd}>&-
        return 1
    fi

    cleanup_pytest_runner() {
        docker rm -f "$runner_name" > /dev/null 2>&1 || true
    }
    trap cleanup_pytest_runner INT TERM

    # Note: we deliberately do NOT re-run stack_template_init.sh here.
    # `stack reset` / `stack up` is where migration changes flow into the
    # template. `run_pytest` clones the current template, so if the user
    # changed migrations they should run `./test.sh stack reset` once.
    require_stack_up
    prepare_test_state
    # LOG_DIR is mkdir'd on the host as the runner/host user, then bind-mounted
    # into the test-runner container at /tmp/bifrost. The container runs as
    # uid 1000 (non-root, hardened), so it cannot write pytest's --junitxml file
    # into a dir it doesn't own -> PermissionError [Errno 13] at pytest exit, and
    # the whole session is reported as ERROR even though every test ran. Make the
    # mount dir world-writable so the uid-1000 container can write results into it.
    chmod 777 "$LOG_DIR" 2>/dev/null || true
    runner_status=0
    docker compose -f "$COMPOSE_FILE" --profile test run --rm \
        --name "$runner_name" test-runner \
        pytest "$@" --junitxml="/tmp/bifrost/test-results.xml" 2>&1 \
        | tee "$LOG_DIR/test-runner.log" || runner_status="${PIPESTATUS[0]}"

    cleanup_pytest_runner
    trap - INT TERM
    exec {runner_lock_fd}>&-
    return "$runner_status"
}

# `unit` is the fast every-PR lane: it deselects `@pytest.mark.slow` tests
# (real-process forks, memory-profiling, subprocess import scans — seconds each,
# not the ms a unit test should cost). Those still run in `all` and nightly, so
# no coverage is dropped — just moved off the per-PR critical path. A caller can
# re-include them ad hoc with `./test.sh unit -m slow` or `-m ""`.
cmd_unit() { run_pytest tests/ --ignore=tests/e2e/ -m "not slow" -v "$@"; }
cmd_e2e()  { run_pytest tests/e2e/ -v "$@"; }
cmd_all()  { run_pytest tests/ -v "$@"; }

cmd_quality() {
    local sub="${1:-}"
    shift || true
    case "$sub" in
        api) quality_api "$@" ;;
        *)
            echo "Usage: ./test.sh quality api [args]" >&2
            exit 2
            ;;
    esac
}

quality_api() {
    print_project

    if [ "${BIFROST_SKIP_BUILD:-0}" != "1" ]; then
        docker compose -f "$COMPOSE_FILE" build test-runner
    else
        echo "BIFROST_SKIP_BUILD=1 — using pre-built test-runner image from local docker."
    fi

    chmod 777 "$LOG_DIR" 2>/dev/null || true
    docker compose -f "$COMPOSE_FILE" --profile test run --rm --no-deps test-runner \
        sh /app/scripts/quality_api.sh \
        2>&1 | tee "$LOG_DIR/quality-api.log"
    return "${PIPESTATUS[0]}"
}

cmd_client() {
    local sub="${1:-}"
    shift || true
    case "$sub" in
        unit) client_unit "$@" ;;
        e2e) client_e2e "$@" ;;
        smoke) client_smoke "$@" ;;
        nightly) client_nightly "$@" ;;
        docs) client_docs "$@" ;;
        *)
            echo "Usage: ./test.sh client {unit|e2e|smoke|nightly|docs} [args]" >&2
            exit 2
            ;;
    esac
}

client_unit() {
    echo "Running vitest on host..."
    (cd client && npm test "$@")
}

client_ci_checks() {
    echo "Building the production client and running Node 26 CI checks..."
    docker compose -f "$COMPOSE_FILE" --profile client-check build client-check-runner
    docker compose -f "$COMPOSE_FILE" --profile client-check run --rm --no-deps \
        client-check-runner
}

repository_ci_checks() {
    echo "Checking GitHub Action pins..."
    python3 api/scripts/check_github_action_pins.py --verify-versions

    echo "Checking generated Codex skill mirrors..."
    scripts/sync-codex-skills.sh
    if ! git diff --quiet -- plugins/bifrost/skills .codex/skills; then
        echo "ERROR: Codex skill mirrors were stale and have been regenerated." >&2
        echo "Commit the generated changes, then rerun ./test.sh pre-pr." >&2
        return 1
    fi
}

build_local_api_candidate() {
    local head_sha version image_tag
    head_sha="$(git rev-parse --short=12 HEAD)"
    version="$(scripts/compute-dev-version.sh)"
    image_tag="bifrost-local-api-candidate:${head_sha}"

    echo "Building and exercising the production API candidate..."
    docker build \
        --file api/Dockerfile \
        --build-arg "BIFROST_VERSION=$version" \
        --tag "$image_tag" \
        .
    docker run --rm \
        --env "EXPECTED_VERSION=$version" \
        --entrypoint python \
        "$image_tag" \
        -c "import os; from shared.version import get_version; from src.main import app; assert app is not None; assert get_version() == os.environ['EXPECTED_VERSION']"
}

start_test_client() {
    # Playwright runs against the production client image. Keep frontend
    # startup out of stack_up so backend-only lanes never build or boot a
    # client they do not use. Both product and documentation browser projects
    # call this helper before starting the Playwright runner.
    if [ "${BIFROST_SKIP_BUILD:-0}" != "1" ]; then
        docker compose -f "$COMPOSE_FILE" build client
    fi
    docker compose -f "$COMPOSE_FILE" --profile client up -d --no-build --no-deps client
    echo "Waiting for production client to be healthy..."
    for i in {1..120}; do
        local client_cid
        client_cid=$(docker compose -f "$COMPOSE_FILE" ps -q client 2>/dev/null)
        if [ -n "$client_cid" ]; then
            local client_status
            client_status=$(docker inspect -f '{{.State.Health.Status}}' "$client_cid" 2>/dev/null || echo unknown)
            if [ "$client_status" = "healthy" ]; then
                echo "Production client ready."
                return
            fi
        fi
        if [ "$i" -eq 120 ]; then
            echo "ERROR: production client not healthy after 120s" >&2
            exit 1
        fi
        sleep 1
    done
}

client_e2e() {
    require_stack_up
    local screenshots_all=false
    local passthrough=()
    for a in "$@"; do
        if [ "$a" = "--screenshots" ]; then screenshots_all=true
        else passthrough+=("$a")
        fi
    done

    prepare_test_state

    start_test_client

    local env_args=()
    if [ "$screenshots_all" = true ]; then
        env_args=(-e PLAYWRIGHT_SCREENSHOT_ALL=1)
    fi

    if [ ${#passthrough[@]} -gt 0 ]; then
        docker compose -f "$COMPOSE_FILE" --profile client run --rm --no-deps "${env_args[@]}" \
            playwright-runner node e2e/support/run-playwright.mjs "${passthrough[@]}"
    else
        docker compose -f "$COMPOSE_FILE" --profile client run --rm --no-deps "${env_args[@]}" \
            playwright-runner node e2e/support/run-playwright.mjs
    fi
}

client_smoke() {
    client_e2e --grep @smoke "$@"
}

client_nightly() {
    client_e2e \
        --project=platform-admin \
        --project=org-user \
        --project=unauthenticated \
        --project=chromium \
        "$@"
}

client_docs() {
    require_stack_up
    if [ -z "${DOCS_REPO_PATH:-}" ]; then
        echo "DOCS_REPO_PATH must be set to the absolute path of the gobifrost checkout." >&2
        exit 2
    fi
    if [ ! -f "$DOCS_REPO_PATH/screenshots.yaml" ]; then
        echo "No screenshots.yaml at $DOCS_REPO_PATH — run scripts/docs/bootstrap-manifest.mjs first." >&2
        exit 2
    fi

    local capture_ids="${DOCS_CAPTURE_IDS:-}"
    local passthrough=()
    for a in "$@"; do
        passthrough+=("$a")
    done

    reset_state
    start_test_client

    export DOCS_REPO_PATH
    docker compose \
        -f "$COMPOSE_FILE" \
        -f docker-compose.docs.yml \
        --profile client run --rm \
        -e "DOCS_CAPTURE_IDS=$capture_ids" \
        playwright-runner \
        node e2e/support/run-playwright.mjs --project=docs "${passthrough[@]}"
}

cmd_ci() {
    print_project
    # Install teardown trap BEFORE stack_up so a boot-time failure still tears
    # down the partially-booted stack instead of leaking containers/volumes.
    trap 'export_logs "$COMPOSE_PROJECT_NAME" "$COMPOSE_FILE"; stack_down' EXIT
    stack_up
    cmd_all
    client_unit
    client_e2e
}

cmd_pre_pr() {
    local head_sha stack_was_up

    if [ -n "$(git status --porcelain --untracked-files=all)" ]; then
        echo "ERROR: ./test.sh pre-pr requires a clean worktree." >&2
        echo "Commit the exact candidate first so the result maps to one SHA." >&2
        git status --short >&2
        return 1
    fi

    echo "Refreshing origin/main before the local PR gate..."
    git fetch --quiet origin main
    if ! git merge-base --is-ancestor origin/main HEAD; then
        echo "ERROR: HEAD does not contain the latest origin/main." >&2
        echo "Rebase or merge origin/main, then rerun ./test.sh pre-pr." >&2
        return 1
    fi

    head_sha="$(git rev-parse HEAD)"
    stack_was_up=0
    if stack_is_up "$COMPOSE_PROJECT_NAME" "$COMPOSE_FILE"; then
        stack_was_up=1
    else
        # Install the trap before boot so a partial startup is cleaned up too.
        trap 'export_logs "$COMPOSE_PROJECT_NAME" "$COMPOSE_FILE"; stack_down' EXIT
    fi

    print_project
    echo "Pre-PR candidate: $head_sha"
    repository_ci_checks
    client_ci_checks
    stack_up
    quality_api
    cmd_unit
    cmd_e2e
    client_smoke
    build_local_api_candidate

    if [ "$(git rev-parse HEAD)" != "$head_sha" ] || \
       [ -n "$(git status --porcelain --untracked-files=all)" ]; then
        echo "ERROR: the candidate changed while ./test.sh pre-pr was running." >&2
        echo "Commit the final state and rerun the gate." >&2
        return 1
    fi

    echo "Local PR gate passed for $head_sha"
    if [ "$stack_was_up" = "1" ]; then
        echo "The pre-existing test stack remains running."
    fi
}

# =============================================================================
# Dispatch
# =============================================================================

if [ $# -eq 0 ]; then
    cmd_unit
    exit $?
fi

case "$1" in
    stack) shift; cmd_stack "$@" ;;
    unit) shift; cmd_unit "$@" ;;
    e2e) shift; cmd_e2e "$@" ;;
    all) shift; cmd_all "$@" ;;
    quality) shift; cmd_quality "$@" ;;
    client) shift; cmd_client "$@" ;;
    pre-pr) shift; cmd_pre_pr "$@" ;;
    ci) cmd_ci ;;
    -h|--help|help)
        sed -n '2,35p' "$0"
        ;;
    # Legacy flags from the pre-refactor test.sh. Point the user at the new
    # verb before silent pytest "unrecognized argument" errors confuse them.
    --client|--client-only|--client-dev|--local|--reset-db|--no-reset|--e2e|--coverage|--wait|--ci)
        cat >&2 <<EOF
ERROR: '$1' is no longer supported. The test.sh interface is now verb-style.

  old                          new
  ./test.sh --e2e              ./test.sh e2e
  ./test.sh --client           ./test.sh client e2e
  ./test.sh --client-dev       ./test.sh client e2e   (stack stays up between runs)
  ./test.sh --client-only      ./test.sh client e2e
  ./test.sh --local            ./test.sh stack up     (then run playwright locally)
  ./test.sh --reset-db         ./test.sh stack reset
  ./test.sh --coverage         ./test.sh all --coverage     (pytest passthrough)
  ./test.sh --ci               ./test.sh ci

Run './test.sh --help' for the full command list.
EOF
        exit 2
        ;;
    tests/*|--*)
        run_pytest "$@"
        ;;
    *)
        echo "Unknown subcommand: $1" >&2
        echo "Run: ./test.sh --help" >&2
        exit 2
        ;;
esac
