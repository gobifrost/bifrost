#!/usr/bin/env bash
# Bifrost development launcher — verb-style subcommand interface.
#
# Per-worktree isolation: Compose project name is derived from the worktree
# path, so two worktrees can run debug stacks in parallel without collisions
# (mirrors how test.sh works).
#
# Two boot modes, auto-detected:
#   Mode A (netbird): NETBIRD_SETUP_KEY is set in env or ~/.config/bifrost/debug.env.
#                     Stack gets a private peer hostname and an ephemeral public
#                     HTTPS URL through NetBird Peer Expose (no host ports).
#   Mode B (port):    no key. Client is exposed on a free host port (auto-picked,
#                     deterministic per worktree). Stack reachable at http://localhost:PORT.
#
# Subcommands:
#   ./debug.sh              boot the stack (default verb: up)
#   ./debug.sh up           same
#   ./debug.sh down         tear down + remove volumes for THIS worktree
#   ./debug.sh status       print mode, project name, URL, login
#   ./debug.sh fixtures     seed and run real local scheduler workloads
#   ./debug.sh logs [svc]   docker compose logs -f, optionally for one service
#
# Login (configured via .env.debug + the seed-user provisioning fix):
#   email:    dev@gobifrost.com
#   password: generated per worktree in NetBird mode; password in port mode
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

# Generate one strong password per worktree and retain it across stack and host
# restarts. NetBird-mode stacks are exposed through a public HTTPS proxy, so the
# shared dev password must never be used there.
configure_netbird_public_credentials() {
    local state_root credential_dir password_file
    state_root="${XDG_STATE_HOME:-$HOME/.local/state}"
    credential_dir="$state_root/bifrost/debug/$COMPOSE_PROJECT_NAME"
    password_file="$credential_dir/admin-password"

    mkdir -p "$credential_dir"
    chmod 700 "$credential_dir"
    if [ ! -f "$password_file" ]; then
        umask 077
        od -An -N24 -tx1 /dev/urandom | tr -d ' \n' > "$password_file"
    fi
    chmod 600 "$password_file"
    BIFROST_DEFAULT_USER_PASSWORD="$(<"$password_file")"
    export BIFROST_DEFAULT_USER_PASSWORD
}

compute_netbird_expose_prefix() {
    local worktree_hash
    worktree_hash="${COMPOSE_PROJECT_NAME##*-}"
    sanitize_hostname "bifrost-$worktree_hash" | cut -c1-32
}

netbird_container_id() {
    docker ps -q \
        --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME" \
        --filter "label=com.docker.compose.service=netbird" 2>/dev/null \
        | head -1
}

netbird_public_url() {
    local cid="$1"
    docker logs "$cid" 2>&1 \
        | awk '/^[[:space:]]*URL:[[:space:]]+https:\/\// {url=$2} END {print url}'
}

service_public_url() {
    local service="$1" cid
    cid=$(docker ps -q \
        --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME" \
        --filter "label=com.docker.compose.service=$service" 2>/dev/null \
        | head -1)
    [ -z "$cid" ] && return 0
    docker inspect "$cid" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
        | awk -F= '$1 == "BIFROST_PUBLIC_URL" {print substr($0, index($0, "=") + 1); exit}'
}

service_admin_password() {
    local service="$1" cid
    cid=$(docker ps -q \
        --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME" \
        --filter "label=com.docker.compose.service=$service" 2>/dev/null \
        | head -1)
    [ -z "$cid" ] && return 0
    docker inspect "$cid" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
        | awk -F= '$1 == "BIFROST_DEFAULT_USER_PASSWORD" {print substr($0, index($0, "=") + 1); exit}'
}

apply_netbird_secure_credentials() {
    if [ "$(service_admin_password api)" != "$BIFROST_DEFAULT_USER_PASSWORD" ]; then
        echo "Applying the generated public-debug credential..."
        docker compose -f "$COMPOSE_FILE" --profile netbird \
            up -d --no-deps --force-recreate api
        wait_for_api_ready "$COMPOSE_FILE" 180
    fi

    local api_cid
    api_cid=$(docker ps -q \
        --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME" \
        --filter "label=com.docker.compose.service=api" 2>/dev/null \
        | head -1)
    if ! docker exec "$api_cid" sh -c \
        'curl -sf -o /dev/null \
            -H "Content-Type: application/x-www-form-urlencoded" \
            --data-urlencode "username=$BIFROST_DEFAULT_USER_EMAIL" \
            --data-urlencode "password=$BIFROST_DEFAULT_USER_PASSWORD" \
            http://localhost:8000/auth/login'; then
        echo "ERROR: generated debug credential was not accepted; public exposure remains disabled" >&2
        return 1
    fi
}

apply_netbird_public_url() {
    local public_url="$1" public_host service needs_recreate="false"
    public_host="${public_url#https://}"
    public_host="${public_host%%/*}"

    BIFROST_PUBLIC_URL="$public_url"
    BIFROST_WEBAUTHN_ORIGIN="$public_url"
    BIFROST_WEBAUTHN_RP_ID="$public_host"
    export BIFROST_PUBLIC_URL BIFROST_WEBAUTHN_ORIGIN BIFROST_WEBAUTHN_RP_ID

    for service in api scheduler worker; do
        if [ "$(service_public_url "$service")" != "$public_url" ]; then
            needs_recreate="true"
            break
        fi
    done

    if [ "$needs_recreate" = "true" ]; then
        echo "Applying public URL to Bifrost services..."
        docker compose -f "$COMPOSE_FILE" --profile netbird \
            up -d --no-deps --force-recreate api
        wait_for_api_ready "$COMPOSE_FILE" 180
        docker compose -f "$COMPOSE_FILE" --profile netbird \
            up -d --no-deps --force-recreate scheduler worker
    fi
}

ensure_netbird_public_expose() {
    local cid entrypoint public_url i
    apply_netbird_secure_credentials
    cid="$(netbird_container_id)"
    entrypoint=""
    if [ -n "$cid" ]; then
        entrypoint=$(docker inspect "$cid" --format '{{index .Config.Entrypoint 0}}' 2>/dev/null || true)
    fi

    if [ -z "$cid" ] || [ "$entrypoint" != "/usr/local/bin/bifrost-netbird-entrypoint.sh" ]; then
        echo "Starting NetBird peer and ephemeral public proxy..."
        docker compose -f "$COMPOSE_FILE" --profile netbird \
            up -d --no-deps --force-recreate netbird
        for ((i=1; i<=30; i++)); do
            cid="$(netbird_container_id)"
            [ -n "$cid" ] && break
            sleep 1
        done
    fi

    if [ -z "$cid" ]; then
        echo "ERROR: NetBird container did not start" >&2
        return 1
    fi

    docker exec "$cid" touch /tmp/bifrost-netbird-expose-ready
    for ((i=1; i<=90; i++)); do
        public_url="$(netbird_public_url "$cid")"
        if [ -n "$public_url" ]; then
            apply_netbird_public_url "$public_url"
            return 0
        fi
        if ! docker ps -q --filter "id=$cid" | grep -q .; then
            echo "ERROR: NetBird exited while creating the public proxy" >&2
            docker logs "$cid" >&2 2>&1 || true
            return 1
        fi
        sleep 1
    done

    echo "ERROR: NetBird did not issue a public URL within 90 seconds" >&2
    docker logs "$cid" >&2 2>&1 || true
    return 1
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
        if [ "$(detect_mode)" = "netbird" ]; then
            configure_netbird_public_credentials
            NETBIRD_HOSTNAME="${NETBIRD_HOSTNAME:-$(compute_netbird_hostname)}"
            NETBIRD_EXPOSE_NAME_PREFIX="${NETBIRD_EXPOSE_NAME_PREFIX:-$(compute_netbird_expose_prefix)}"
            export NETBIRD_HOSTNAME NETBIRD_EXPOSE_NAME_PREFIX
            ensure_netbird_public_expose
        fi
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
        configure_netbird_public_credentials
        NETBIRD_HOSTNAME="${NETBIRD_HOSTNAME:-$(compute_netbird_hostname)}"
        NETBIRD_EXPOSE_NAME_PREFIX="${NETBIRD_EXPOSE_NAME_PREFIX:-$(compute_netbird_expose_prefix)}"
        export NETBIRD_HOSTNAME NETBIRD_EXPOSE_NAME_PREFIX
        echo "Mode:     netbird"
        echo "Hostname: $NETBIRD_HOSTNAME"
        # Mode A: no host port is bound. The sidecar provides both private mesh
        # access and an ephemeral public HTTPS proxy.
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

    if [ "$mode" = "netbird" ]; then
        ensure_netbird_public_expose
    fi
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
        configure_netbird_public_credentials
        echo "Mode:     netbird"
        # FQDN comes from `netbird status` once the peer registers (Netbird
        # appends a numeric suffix when the chosen NB_HOSTNAME isn't unique).
        local nb_fqdn
        nb_fqdn=$(docker exec "$nb_cid" netbird status 2>/dev/null \
            | awk -F': ' '/^FQDN:/ {print $2; exit}' | tr -d '\r')
        local public_url
        public_url="$(netbird_public_url "$nb_cid")"
        if [ -n "$public_url" ]; then
            echo "Open:     $public_url"
            if [ -n "$nb_fqdn" ]; then
                echo "Private:  http://$nb_fqdn"
            fi
        elif [ -n "$nb_fqdn" ]; then
            echo "Open:     http://$nb_fqdn  (public proxy still provisioning)"
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

cmd_fixtures() {
    print_header
    if ! stack_is_running; then
        echo "ERROR: debug stack is not running. Run ./debug.sh up first." >&2
        return 1
    fi

    echo "Starting local OAuth and Git fixtures..."
    docker compose -f "$COMPOSE_FILE" up -d --build scheduler-fixtures

    echo "Waiting for scheduler fixtures to be ready (up to 60s)..."
    local fixture_cid i
    for ((i=1; i<=60; i++)); do
        fixture_cid=$(docker ps -q \
            --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME" \
            --filter "label=com.docker.compose.service=scheduler-fixtures" \
            2>/dev/null | head -1)
        if [ -n "$fixture_cid" ] && docker exec "$fixture_cid" \
            curl -sf -o /dev/null http://localhost:8080/health 2>/dev/null; then
            break
        fi
        if [ $i -eq 60 ]; then
            echo "ERROR: scheduler fixtures did not become ready in 60s." >&2
            return 1
        fi
        sleep 1
    done

    echo "Running scheduler fixture suite through the central job runner..."
    docker compose -f "$COMPOSE_FILE" exec -T -e BIFROST_DEBUG=false api \
        python -m src.dev.scheduler_fixtures
    echo ""
    cmd_status
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
    fixtures) shift; cmd_fixtures "$@" ;;
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
