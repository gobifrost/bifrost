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
#   ./test.sh e2e-smoke                 PR-gating backend e2e smoke tests.
#   ./test.sh e2e                       Backend e2e tests.
#   ./test.sh all                       Unit + e2e (mirrors CI).
#   ./test.sh tests/path/... [args]     Pass through to pytest.
#
# Client tests:
#   ./test.sh client unit               Vitest on the host (no stack).
#   ./test.sh client e2e                Playwright in the stack's client container.
#   ./test.sh client e2e --screenshots  Capture a screenshot for every test (UX review).
#   ./test.sh client e2e e2e/auth.unauth.spec.ts   Pass through to playwright.
#
# CI escape hatch:
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
export LOG_DIR

# Load .env.test for optional secrets (GitHub PAT, LLM keys, etc.)
if [ -f ".env.test" ]; then
    set -a
    # shellcheck disable=SC1091
    source .env.test
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
    for _ in {1..10}; do
        if stack_is_up "$COMPOSE_PROJECT_NAME" "$COMPOSE_FILE"; then
            if wait_for_api_ready "$COMPOSE_FILE"; then
                return 0
            fi
            echo "Stack containers are running, waiting for API readiness..." >&2
        fi
        sleep 1
    done

    if stack_is_up "$COMPOSE_PROJECT_NAME" "$COMPOSE_FILE"; then
        echo "ERROR: stack containers running but API not serving traffic." >&2
        echo "  Check: docker compose -f $COMPOSE_FILE logs api" >&2
    else
        echo "ERROR: stack not running for this worktree. Run:" >&2
        echo "  ./test.sh stack up" >&2
    fi
    exit 1
}

reset_state() {
    echo "Resetting state..."

    docker compose -f "$COMPOSE_FILE" stop api worker scheduler pgbouncer 2>/dev/null || true

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
    docker compose -f "$COMPOSE_FILE" --profile e2e start api worker scheduler > /dev/null
    wait_for_api_ready "$COMPOSE_FILE"

    echo "State reset complete."
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

    echo "Building template database..."
    "$SCRIPT_DIR/scripts/stack_template_init.sh"

    echo "Starting API + Worker + Scheduler..."
    docker compose -f "$COMPOSE_FILE" --profile e2e up -d --build
    echo "Waiting for API to be serving traffic on /health/ready..."
    wait_for_api_ready "$COMPOSE_FILE"

    echo "Starting client..."
    docker compose -f "$COMPOSE_FILE" --profile client up -d --build client
    echo "Waiting for client to be healthy..."
    for i in {1..120}; do
        cid=$(docker compose -f "$COMPOSE_FILE" ps -q client 2>/dev/null)
        if [ -n "$cid" ]; then
            status=$(docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null || echo unknown)
            if [ "$status" = "healthy" ]; then
                echo "Client ready."
                break
            fi
        fi
        if [ $i -eq 120 ]; then
            echo "ERROR: client not healthy after 120s" >&2
            exit 1
        fi
        sleep 1
    done

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
    # Note: we deliberately do NOT re-run stack_template_init.sh here.
    # `stack reset` / `stack up` is where migration changes flow into the
    # template. `run_pytest` clones the current template, so if the user
    # changed migrations they should run `./test.sh stack reset` once.
    require_stack_up
    reset_state
    docker compose -f "$COMPOSE_FILE" --profile test run --rm test-runner \
        pytest "$@" --durations=25 --junitxml="$LOG_DIR/test-results.xml" 2>&1 | tee "$LOG_DIR/test-runner.log"
    return "${PIPESTATUS[0]}"
}

cmd_unit() { run_pytest tests/ --ignore=tests/e2e/ -v "$@"; }
cmd_coverage() {
    local target="${1:-coverage.xml}"
    docker compose -f "$COMPOSE_FILE" --profile test run --rm test-runner \
        sh -lc 'cat /coverage/coverage.xml' > "$target"
}
cmd_e2e_smoke() {
    run_pytest \
        tests/e2e/api/test_auth.py::TestHealthCheck \
        tests/e2e/api/test_auth.py::TestRegistrationFlow::test_platform_admin_can_access_protected_endpoints \
        tests/e2e/api/test_workflows.py::TestWorkflowListing \
        tests/e2e/api/test_workflows.py::TestWorkflowValidation::test_validate_valid_workflow \
        tests/e2e/platform/test_cli_ephemeral_login.py::test_ephemeral_env_vars_authenticate_bifrost_api_subprocess \
        -v "$@"
}
cmd_e2e()  { run_pytest tests/e2e/ -v "$@"; }
cmd_all()  { run_pytest tests/ -v "$@"; }

cmd_client() {
    local sub="${1:-}"
    shift || true
    case "$sub" in
        unit) client_unit "$@" ;;
        e2e) client_e2e "$@" ;;
        docs) client_docs "$@" ;;
        *)
            echo "Usage: ./test.sh client {unit|e2e|docs} [args]" >&2
            exit 2
            ;;
    esac
}

client_unit() {
    echo "Running vitest on host..."
    (cd client && npm test "$@")
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

    reset_state

    local env_args=()
    if [ "$screenshots_all" = true ]; then
        env_args=(-e PLAYWRIGHT_SCREENSHOT_ALL=1)
    fi

    if [ ${#passthrough[@]} -gt 0 ]; then
        docker compose -f "$COMPOSE_FILE" --profile client run --rm "${env_args[@]}" \
            playwright-runner npx playwright test "${passthrough[@]}"
    else
        docker compose -f "$COMPOSE_FILE" --profile client run --rm "${env_args[@]}" \
            playwright-runner npx playwright test --reporter=list,html \
                --project=platform-admin \
                --project=org-user \
                --project=unauthenticated \
                --project=chromium
    fi
}

client_docs() {
    require_stack_up
    if [[ -z "${DOCS_REPO_PATH:-}" ]]; then
        echo "DOCS_REPO_PATH must be set to the absolute path of the bifrost-integrations-docs checkout." >&2
        exit 2
    fi
    if [[ ! -f "$DOCS_REPO_PATH/screenshots.yaml" ]]; then
        echo "No screenshots.yaml at $DOCS_REPO_PATH — run scripts/docs/bootstrap-manifest.mjs first." >&2
        exit 2
    fi

    local capture_ids="${DOCS_CAPTURE_IDS:-}"
    local passthrough=()
    for a in "$@"; do
        passthrough+=("$a")
    done

    reset_state

    export DOCS_REPO_PATH
    docker compose \
        -f "$COMPOSE_FILE" \
        -f docker-compose.docs.yml \
        --profile client run --rm \
        -e "DOCS_CAPTURE_IDS=$capture_ids" \
        playwright-runner \
        npx playwright test --project=docs "${passthrough[@]}"
    return 0
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
    coverage) shift; cmd_coverage "$@" ;;
    e2e-smoke) shift; cmd_e2e_smoke "$@" ;;
    e2e) shift; cmd_e2e "$@" ;;
    all) shift; cmd_all "$@" ;;
    client) shift; cmd_client "$@" ;;
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
