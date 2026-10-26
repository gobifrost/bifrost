#!/bin/sh
# Bifrost API Entrypoint
# Ensures proper permissions and starts the application

set -e

# The API's JSON response workload benefits from a purging allocator. Scope its
# defaults to the API service process because this image also runs workers, init, and
# CLI commands. Operators can retain explicit allocator and instrumentation settings.
configure_api_allocator() {
    if [ -z "${PYTHONMALLOC+x}" ]; then
        export PYTHONMALLOC=malloc
    fi
    if [ -z "${MALLOC_CONF+x}" ]; then
        export MALLOC_CONF=background_thread:true,dirty_decay_ms:1000,muzzy_decay_ms:1000
    fi
    export LD_PRELOAD="${LD_PRELOAD:+$LD_PRELOAD:}libjemalloc.so.2"
}

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
