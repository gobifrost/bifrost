#!/bin/sh
# Bifrost API Entrypoint
# Ensures proper permissions and starts the application

set -e

# This image also runs workers, init, and CLI commands. The helper's defaults
# apply only to the API role; the direct `uvicorn` shim handles entrypoint
# bypasses such as Kubernetes command overrides.
. /usr/local/bin/api-allocator.sh

if [ "${BIFROST_API_PROCESS:-}" = "1" ]; then
    configure_api_allocator
else
    case "$1" in
        uvicorn|*/uvicorn) configure_api_allocator ;;
    esac
fi

# Fix ownership of mounted volumes if running as root
if [ "$(id -u)" = "0" ]; then
    # Fix workspace permissions if needed
    if [ -d "/workspace" ]; then
        chown -R bifrost:bifrost /workspace 2>/dev/null || true
    fi

    # Fix temp directory permissions if needed
    if [ -d "/tmp/bifrost" ]; then
        chown -R bifrost:bifrost /tmp/bifrost 2>/dev/null || true
    fi

    # Fix coverage directory permissions if needed (test runner)
    if [ -d "/coverage" ]; then
        chown -R bifrost:bifrost /coverage 2>/dev/null || true
    fi

    # Drop to bifrost user and exec the command
    exec gosu bifrost "$@"
else
    # Already running as bifrost, just exec
    exec "$@"
fi
