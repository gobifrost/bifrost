#!/usr/bin/env bash
# Bifrost development launcher — verb-style subcommand interface.
#
# Per-worktree isolation: Compose project name is derived from the worktree
# path, so two worktrees can run debug stacks in parallel without collisions
# (mirrors how test.sh works).
#
# Two boot modes, auto-detected:
#   Mode A (netbird): NETBIRD_SETUP_KEY is set in env or ~/.config/bifrost/debug.env.
#                     Stack reachable only via Netbird peer hostname (no host ports).
#   Mode B (port):    no key. Client is exposed on a free host port (auto-picked,
#                     deterministic per worktree). Stack reachable at http://localhost:PORT.
#
# Subcommands:
#   ./debug.sh              boot the stack (default verb: up)
#   ./debug.sh up           same
#   ./debug.sh down         tear down + remove volumes for THIS worktree
#   ./debug.sh status       print mode, project name, URL, login
#   ./debug.sh logs [svc]   docker compose logs -f, optionally for one service
#
# Login (configured via .env.debug + the seed-user provisioning fix):
#   email:    dev@gobifrost.com
#   password: password
#   MFA:      off

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# shellcheck source=scripts/lib/test_helpers.sh
source "$SCRIPT_DIR/scripts/lib/test_helpers.sh"

COMPOSE_FILE="docker-compose.debug.yml"
BIFROST_PROJECT_PREFIX="bifrost-debug"
export BIFROST_PROJECT_PREFIX
export COMPOSE_PROJECT_NAME
COMPOSE_PROJECT_NAME="$(compute_project_name .)"

LOG_DIR="/tmp/bifrost-$COMPOSE_PROJECT_NAME"
mkdir -p "$LOG_DIR"

# =============================================================================
# Env loading
# =============================================================================
# Order (later overrides earlier):
#   1. .env             — repo defaults (POSTGRES_PASSWORD, BIFROST_SECRET_KEY, etc.)
#   2. .env.debug       — checked-in debug defaults (dev user, MFA off)
#   3. ~/.config/bifrost/debug.env  — per-user overrides (NETBIRD_SETUP_KEY)
#   4. process env      — already exported, wins everything
load_env_files() {
    set -a
    if [ -f "$SCRIPT_DIR/.env" ]; then
        # shellcheck disable=SC1091
        source "$SCRIPT_DIR/.env"
    fi
    if [ -f "$SCRIPT_DIR/.env.debug" ]; then
        # shellcheck disable=SC1091
        source "$SCRIPT_DIR/.env.debug"
    fi
    if [ -f "$HOME/.config/bifrost/debug.env" ]; then
        # shellcheck disable=SC1091
        source "$HOME/.config/bifrost/debug.env"
    fi
    set +a

    # Explicit port-mode opt-out. `env -u NETBIRD_SETUP_KEY ./debug.sh up` does
    # NOT force port mode on its own, because the global ~/.config/bifrost/debug.env
    # above re-introduces NETBIRD_SETUP_KEY under `set -a`. Browser/Playwright work
    # (and the Solutions QA fan-out) needs a deterministic way to force port mode
    # regardless of the global config, so honor BIFROST_FORCE_PORT=1 by dropping
    # the netbird key after sourcing. (Netbird-mode users are unaffected — they
    # simply don't set this flag.)
    if [ "${BIFROST_FORCE_PORT:-}" = "1" ]; then
        unset NETBIRD_SETUP_KEY
    fi
}

# =============================================================================
# Mode detection + helpers
# =============================================================================

detect_mode() {
    if [ -n "${NETBIRD_SETUP_KEY:-}" ]; then
        echo "netbird"
    else
        echo "port"
    fi
}

# Sanitize a string for use as a hostname: lowercase, [a-z0-9-], <=63 chars.
sanitize_hostname() {
    printf '%s' "$1" \
        | tr '[:upper:]' '[:lower:]' \
        | tr -c 'a-z0-9-' '-' \
        | sed -e 's/^-*//' -e 's/-*$//' -e 's/--*/-/g' \
        | cut -c1-63
}

# Derive a stable Netbird hostname for this worktree.
compute_netbird_hostname() {
    local repo_root
    repo_root="$(git rev-parse --show-toplevel 2>/dev/null)"
    local base
    base="$(basename "$repo_root")"
    sanitize_hostname "bifrost-debug-${base}"
}

# Pick a free TCP port deterministically per-worktree.
# Strategy: hash the project name into the 30000-39999 range, scan forward
# from there until we find one nothing's listening on.
compute_client_port() {
    local hash_int
    hash_int=$(printf '%s' "$COMPOSE_PROJECT_NAME" | sha256sum | cut -c1-8)
    local base=$((30000 + (0x${hash_int} % 10000)))
    local port=$base
    local tries=0
    while [ $tries -lt 1000 ]; do
        if ! is_port_in_use "$port"; then
            printf '%d' "$port"
            return 0
        fi
        port=$((port + 1))
        if [ $port -ge 40000 ]; then port=30000; fi
        tries=$((tries + 1))
    done
    echo "ERROR: could not find a free port in 30000-39999" >&2
    return 1
}

is_port_in_use() {
    local port="$1"
    if ss -ltn "sport = :$port" 2>/dev/null | grep -q "LISTEN"; then
        return 0
    fi
    return 1
}

# Read the published port for the running client container. Returns empty if
# no host port is bound (Mode A / netbird).
running_client_port() {
    local cid
    cid=$(docker ps -q --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME" --filter "label=com.docker.compose.service=client" 2>/dev/null | head -1)
    [ -z "$cid" ] && return 1
    docker port "$cid" 80/tcp 2>/dev/null | head -1 | awk -F: '{print $NF}'
}

# Is the stack for this worktree currently running?
stack_is_running() {
    docker ps -q --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME" 2>/dev/null | grep -q .
}

print_header() {
    echo "Worktree: $(git rev-parse --show-toplevel)"
    echo "Project:  $COMPOSE_PROJECT_NAME"
}

print_login() {
    echo "Login:    ${BIFROST_DEFAULT_USER_EMAIL:-dev@gobifrost.com} / ${BIFROST_DEFAULT_USER_PASSWORD:-password}"
}

# =============================================================================
# Subcommands
# =============================================================================

cmd_up() {
    print_header

    if stack_is_running; then
        echo "Stack already running."
        echo ""
        cmd_status
        return 0
    fi

    # Ensure node_modules dirs exist for Docker anonymous volume mountpoints.
    mkdir -p api/src/services/app_compiler/node_modules
    mkdir -p api/src/services/app_bundler/node_modules

    BIFROST_VERSION=$(git describe --tags --always --dirty 2>/dev/null || echo "debug")
    export BIFROST_VERSION
    export VITE_BIFROST_VERSION="$BIFROST_VERSION"

    local mode
    mode="$(detect_mode)"

    if [ "$mode" = "netbird" ]; then
        NETBIRD_HOSTNAME="${NETBIRD_HOSTNAME:-$(compute_netbird_hostname)}"
        export NETBIRD_HOSTNAME
        echo "Mode:     netbird"
        echo "Hostname: $NETBIRD_HOSTNAME"
        # Mode A: no host port bound for client; netbird sidecar shares its
        # network namespace and surfaces the stack on the Netbird mesh.
        docker compose -f "$COMPOSE_FILE" --profile netbird up -d --build
    else
        DEBUG_CLIENT_PORT="$(compute_client_port)"
        export DEBUG_CLIENT_PORT
        echo "Mode:     port"
        echo "Port:     $DEBUG_CLIENT_PORT"
        # Mode B: stack the port-binding overlay onto the base file.
        docker compose -f "$COMPOSE_FILE" -f docker-compose.debug.port.yml up -d --build
    fi

    echo "Waiting for API to be ready (up to 180s)..."
    local api_cid i
    for ((i=1; i<=180; i++)); do
        api_cid=$(docker ps -q --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME" --filter "label=com.docker.compose.service=api" 2>/dev/null | head -1)
        if [ -n "$api_cid" ] && docker exec "$api_cid" \
            curl -sf -o /dev/null http://localhost:8000/health/ready 2>/dev/null; then
            break
        fi
        if [ $i -eq 180 ]; then
            echo "ERROR: api did not become ready in 180s. Check logs:" >&2
            echo "  ./debug.sh logs api" >&2
            return 1
        fi
        sleep 1
    done
    echo "Stack is up."
    echo ""
    cmd_status
}

cmd_down() {
    print_header
    echo "Tearing down stack..."
    docker compose -f "$COMPOSE_FILE" --profile netbird down -v
    echo "Done."
}

cmd_status() {
    print_header
    if ! stack_is_running; then
        echo "Status:   DOWN"
        return 0
    fi
    echo "Status:   UP"

    local nb_cid
    nb_cid=$(docker ps -q --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME" --filter "label=com.docker.compose.service=netbird" 2>/dev/null | head -1)
    if [ -n "$nb_cid" ]; then
        echo "Mode:     netbird"
        # FQDN comes from `netbird status` once the peer registers (Netbird
        # appends a numeric suffix when the chosen NB_HOSTNAME isn't unique).
        local nb_fqdn
        nb_fqdn=$(docker exec "$nb_cid" netbird status 2>/dev/null \
            | awk -F': ' '/^FQDN:/ {print $2; exit}' | tr -d '\r')
        if [ -n "$nb_fqdn" ]; then
            echo "Open:     http://$nb_fqdn"
        else
            local nb_host
            nb_host=$(docker exec "$nb_cid" sh -c 'echo $NB_HOSTNAME' 2>/dev/null | tr -d '\r')
            echo "Open:     http://$nb_host  (peer still registering)"
        fi
    else
        local port
        port="$(running_client_port || echo "")"
        echo "Mode:     port"
        if [ -n "$port" ]; then
            echo "Open:     http://localhost:$port"
        else
            echo "Open:     (client port not bound — see 'docker compose ps')"
        fi
    fi
    print_login
}

cmd_logs() {
    if [ $# -gt 0 ]; then
        docker compose -f "$COMPOSE_FILE" logs -f "$@"
    else
        docker compose -f "$COMPOSE_FILE" logs -f
    fi
}

# =============================================================================
# Dispatch
# =============================================================================

load_env_files

if [ $# -eq 0 ]; then
    cmd_up
    exit $?
fi

case "$1" in
    up)     shift; cmd_up "$@" ;;
    down)   shift; cmd_down "$@" ;;
    status) shift; cmd_status "$@" ;;
    logs)   shift; cmd_logs "$@" ;;
    -h|--help|help)
        sed -n '2,30p' "$0"
        ;;
    *)
        echo "Unknown subcommand: $1" >&2
        echo "Run: ./debug.sh --help" >&2
        exit 2
        ;;
esac
