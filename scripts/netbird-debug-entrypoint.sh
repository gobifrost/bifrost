#!/usr/bin/env bash

set -eEuo pipefail

daemon_pid=""
expose_pid=""

cleanup() {
    if [ -n "$expose_pid" ]; then
        kill -TERM "$expose_pid" 2>/dev/null || true
        wait "$expose_pid" 2>/dev/null || true
    fi
    if [ -n "$daemon_pid" ]; then
        kill -TERM "$daemon_pid" 2>/dev/null || true
        wait "$daemon_pid" 2>/dev/null || true
    fi
}

trap cleanup SIGTERM SIGINT EXIT

netbird service run &
daemon_pid="$!"

for _ in $(seq 1 30); do
    if netbird status --check live >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

if ! netbird status --check live >/dev/null 2>&1; then
    echo "ERROR: NetBird daemon did not become ready" >&2
    exit 1
fi

up_args=(
    --setup-key "${NB_SETUP_KEY:?NETBIRD_SETUP_KEY is required}"
    --hostname "${NB_HOSTNAME:?NETBIRD_HOSTNAME is required}"
)
if [ -n "${NB_EXTRA_DNS_LABELS:-}" ]; then
    up_args+=(--extra-dns-labels "$NB_EXTRA_DNS_LABELS")
fi
netbird up "${up_args[@]}"

echo "NetBird peer connected; waiting for Bifrost to enable public exposure."
while [ ! -f /tmp/bifrost-netbird-expose-ready ]; do
    if ! kill -0 "$daemon_pid" 2>/dev/null; then
        echo "ERROR: NetBird daemon exited before public exposure was enabled" >&2
        exit 1
    fi
    sleep 1
done

netbird expose \
    --with-name-prefix "${NB_EXPOSE_NAME_PREFIX:?NETBIRD expose name is required}" \
    80 &
expose_pid="$!"

# The container owns both processes so Compose shutdown reaches the expose
# command and NetBird removes the ephemeral public service immediately.
wait -n "$daemon_pid" "$expose_pid"
