#!/bin/sh
# Shared API allocator setup for the image entrypoint and direct uvicorn command.

configure_api_allocator() {
    if [ -z "${PYTHONMALLOC+x}" ]; then
        export PYTHONMALLOC=malloc
    fi
    if [ -z "${MALLOC_CONF+x}" ]; then
        export MALLOC_CONF=background_thread:true,dirty_decay_ms:1000,muzzy_decay_ms:1000
    fi

    # The dynamic loader accepts colon or whitespace-separated preload entries.
    # Normalize only for duplicate detection; preserve an operator's original chain.
    preload_entries=$(printf '%s' "${LD_PRELOAD-}" | tr ':[:space:]' ' ')
    case " $preload_entries " in
        *" libjemalloc.so.2 "* | *"/libjemalloc.so.2 "*) ;;
        *) export LD_PRELOAD="${LD_PRELOAD:+$LD_PRELOAD:}libjemalloc.so.2" ;;
    esac
}
