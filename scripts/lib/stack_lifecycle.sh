#!/usr/bin/env bash
# Command-owned Compose lifecycle. Start it after acquiring the project's lock.

stack_lifecycle_start() {
    STACK_CLEANUP_FUNCTION="$1"
    STACK_SESSION_PIDS=()
    trap stack_lifecycle_cleanup EXIT
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM
}

stack_lifecycle_cleanup() {
    local command_status=$? cleanup_status
    # Cleanup owns the lock until it finishes; a second signal cannot abort it.
    trap - EXIT
    trap '' HUP INT TERM
    set +e
    if ((${#STACK_SESSION_PIDS[@]})); then
        kill "${STACK_SESSION_PIDS[@]}" 2>/dev/null
        wait "${STACK_SESSION_PIDS[@]}" 2>/dev/null
    fi
    "$STACK_CLEANUP_FUNCTION"
    cleanup_status=$?
    if [ "$cleanup_status" -ne 0 ]; then
        echo "ERROR: stack cleanup failed; run the stack's down command to finish cleanup." >&2
        if [ "$command_status" -eq 0 ]; then command_status=$cleanup_status; fi
    fi
    exit "$command_status"
}

stack_session_wait() {
    local container
    local -a containers=()
    mapfile -t containers < <(docker ps -q \
        --filter "label=com.docker.compose.project=$COMPOSE_PROJECT_NAME")
    if ((${#containers[@]} == 0)); then
        echo "ERROR: no running containers remain in the debug session." >&2
        return 1
    fi

    echo "Debug session is running. Press Ctrl+C to tear down its containers and volumes."
    # Only monitor running services: init is expected to have exited already.
    for container in "${containers[@]}"; do
        docker wait "$container" >/dev/null &
        STACK_SESSION_PIDS+=("$!")
    done
    docker compose -f "$COMPOSE_FILE" --profile '*' logs --follow --tail=20 &
    STACK_SESSION_PIDS+=("$!")
    wait -n "${STACK_SESSION_PIDS[@]}" || true
    echo "A debug service stopped; tearing down the session." >&2
    return 1
}
