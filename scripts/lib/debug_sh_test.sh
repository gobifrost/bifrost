#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(git rev-parse --show-toplevel)"

source_debug_sh() {
    set -- __debug_sh_test_source
    BIFROST_DEBUG_SH_SOURCE_ONLY=1 source "$repo_dir/debug.sh"
}

test_public_smoke_rejects_netbird_html_status() {
    source_debug_sh

    curl() {
        if [[ "$*" == *"/auth/status"* ]]; then
            printf '200 text/html <html>NetBird 404</html>\n'
            return 0
        fi
        printf '200 application/gzip\n'
        return 0
    }

    if netbird_public_smoke_ready "https://example.netbird.services"; then
        echo "FAIL: NetBird HTML status response must not mark public URL ready" >&2
        exit 1
    fi
}

test_public_smoke_rejects_json_without_bifrost_status_shape() {
    source_debug_sh

    curl() {
        if [[ "$*" == *"/auth/status"* ]]; then
            printf '200 application/json {"not_bifrost":true}\n'
            return 0
        fi
        printf '200 application/gzip\n'
        return 0
    }

    if netbird_public_smoke_ready "https://example.netbird.services"; then
        echo "FAIL: JSON without Bifrost auth status keys must not mark public URL ready" >&2
        exit 1
    fi
}

test_public_smoke_requires_cli_gzip() {
    source_debug_sh

    curl() {
        if [[ "$*" == *"/auth/status"* ]]; then
            printf '200 application/json {"needs_setup":false,"password_login_enabled":true}\n'
            return 0
        fi
        printf '200 text/html\n'
        return 0
    }

    if netbird_public_smoke_ready "https://example.netbird.services"; then
        echo "FAIL: non-gzip CLI download must not mark public URL ready" >&2
        exit 1
    fi
}

test_public_smoke_accepts_bifrost_status_and_cli_download() {
    source_debug_sh

    curl() {
        if [[ "$*" == *"/auth/status"* ]]; then
            printf '200 application/json {"needs_setup":false,"password_login_enabled":true}\n'
            return 0
        fi
        printf '200 application/gzip\n'
        return 0
    }

    netbird_public_smoke_ready "https://example.netbird.services" || {
        echo "FAIL: Bifrost auth status JSON plus CLI gzip should mark public URL ready" >&2
        exit 1
    }
}

test_public_smoke_rejects_transport_failure() {
    source_debug_sh

    curl() {
        return 7
    }

    if netbird_public_smoke_ready "https://example.netbird.services"; then
        echo "FAIL: transport failure must not mark public URL ready" >&2
        exit 1
    fi
}

test_public_smoke_uses_bounded_curl_timeouts() {
    source_debug_sh

    local calls_file
    calls_file="$(mktemp)"
    curl() {
        printf '%s\n' "$*" >> "$calls_file"
        if [[ "$*" == *"/auth/status"* ]]; then
            printf '200 application/json {"needs_setup":false,"password_login_enabled":true}\n'
            return 0
        fi
        printf '200 application/gzip\n'
        return 0
    }

    netbird_public_smoke_ready "https://example.netbird.services" || {
        echo "FAIL: bounded curl smoke should pass with Bifrost responses" >&2
        rm -f "$calls_file"
        exit 1
    }

    local calls
    calls="$(<"$calls_file")"
    rm -f "$calls_file"
    [[ "$calls" == *"--connect-timeout 2"* ]] || {
        echo "FAIL: smoke curl missing connect timeout: $calls" >&2
        exit 1
    }
    [[ "$calls" == *"/auth/status"* ]] || {
        echo "FAIL: smoke must use side-effect-free auth status endpoint: $calls" >&2
        exit 1
    }
    [[ "$calls" != *"/auth/login"* ]] || {
        echo "FAIL: smoke must not use login endpoint: $calls" >&2
        exit 1
    }
    [[ "$calls" == *"--max-time 3"* ]] || {
        echo "FAIL: auth status smoke missing short max time: $calls" >&2
        exit 1
    }
    [[ "$calls" == *"--max-time 5"* ]] || {
        echo "FAIL: CLI smoke curl missing max time: $calls" >&2
        exit 1
    }
}

test_public_expose_retries_until_public_smoke_passes() {
    source_debug_sh

    local apply_count=0
    local smoke_count=0
    local touched_ready=0
    local now=1000

    apply_netbird_secure_credentials() { :; }
    netbird_container_id() { printf 'netbird-container\n'; }
    netbird_public_url() { printf 'https://example.netbird.services\n'; }
    apply_netbird_public_url() { apply_count=$((apply_count + 1)); }
    netbird_public_smoke_ready() {
        smoke_count=$((smoke_count + 1))
        [[ "$smoke_count" -ge 2 ]]
    }
    netbird_now_seconds() { printf '%s\n' "$now"; }
    sleep() { :; }
    docker() {
        if [[ "$1" == "inspect" ]]; then
            printf '/usr/local/bin/bifrost-netbird-entrypoint.sh\n'
            return 0
        fi
        if [[ "$1" == "exec" && "$3" == "touch" ]]; then
            touched_ready=1
            return 0
        fi
        if [[ "$1" == "ps" ]]; then
            printf 'netbird-container\n'
            return 0
        fi
        return 0
    }

    ensure_netbird_public_expose

    [[ "$touched_ready" -eq 1 ]] || { echo "FAIL: did not enable netbird expose" >&2; exit 1; }
    [[ "$smoke_count" -eq 2 ]] || { echo "FAIL: expected retry until smoke pass, got $smoke_count" >&2; exit 1; }
    [[ "$apply_count" -eq 1 ]] || { echo "FAIL: public URL applied before smoke pass or not applied, got $apply_count" >&2; exit 1; }
}

test_public_expose_stops_at_deadline_not_probe_count() {
    source_debug_sh

    local smoke_count=0
    local now=1000

    apply_netbird_secure_credentials() { :; }
    netbird_container_id() { printf 'netbird-container\n'; }
    netbird_public_url() { printf 'https://example.netbird.services\n'; }
    netbird_public_smoke_ready() {
        smoke_count=$((smoke_count + 1))
        return 1
    }
    netbird_now_seconds() { printf '%s\n' "$now"; }
    sleep() { now=$((now + 31)); }
    docker() {
        if [[ "$1" == "inspect" ]]; then
            printf '/usr/local/bin/bifrost-netbird-entrypoint.sh\n'
            return 0
        fi
        if [[ "$1" == "exec" && "$3" == "touch" ]]; then
            return 0
        fi
        if [[ "$1" == "ps" ]]; then
            printf 'netbird-container\n'
            return 0
        fi
        if [[ "$1" == "logs" ]]; then
            return 0
        fi
        return 0
    }

    local output
    if output="$(ensure_netbird_public_expose 2>&1)"; then
        echo "FAIL: expose should fail when deadline expires" >&2
        exit 1
    fi
    [[ "$smoke_count" -lt 90 ]] || {
        echo "FAIL: deadline should stop before 90 slow probes, got $smoke_count" >&2
        exit 1
    }
    [[ "$output" == *"within the 90-second readiness deadline"* ]] || {
        echo "FAIL: deadline error was not truthful, got: $output" >&2
        exit 1
    }
}

test_apply_public_url_recreates_app_services_when_public_env_changes() {
    source_debug_sh

    local api_recreate_count=0
    local worker_recreate_count=0
    local sequence=""
    wait_for_api_ready() { sequence="${sequence}wait "; }
    service_public_url() { printf 'https://old.example.test\n'; }
    docker() {
        if [[ "$1" == "compose" && "$*" == *" up "* && "$*" == *" api"* ]]; then
            api_recreate_count=$((api_recreate_count + 1))
            sequence="${sequence}api "
        fi
        if [[ "$1" == "compose" && "$*" == *" up "* && "$*" == *"scheduler worker"* ]]; then
            worker_recreate_count=$((worker_recreate_count + 1))
            sequence="${sequence}scheduler-worker "
        fi
        return 0
    }

    apply_netbird_public_url "https://new.example.test"

    [[ "$BIFROST_PUBLIC_URL" == "https://new.example.test" ]] || {
        echo "FAIL: public URL env was not applied in-process" >&2
        exit 1
    }
    [[ "$api_recreate_count" -eq 1 ]] || {
        echo "FAIL: applying changed public URL must recreate api once, got $api_recreate_count" >&2
        exit 1
    }
    [[ "$worker_recreate_count" -eq 1 ]] || {
        echo "FAIL: applying changed public URL must recreate scheduler/worker once, got $worker_recreate_count" >&2
        exit 1
    }
    [[ "$sequence" == "api wait scheduler-worker " ]] || {
        echo "FAIL: expected recreate order 'api wait scheduler-worker', got: $sequence" >&2
        exit 1
    }
}

test_status_hides_unready_public_url() {
    source_debug_sh

    stack_is_running() { return 0; }
    configure_netbird_public_credentials() { :; }
    netbird_public_url() { printf 'https://example.netbird.services\n'; }
    netbird_public_smoke_ready() { return 1; }
    print_header() { :; }
    print_login() { :; }
    docker() {
        if [[ "$1" == "ps" ]]; then
            printf 'netbird-container\n'
            return 0
        fi
        if [[ "$1" == "exec" && "$3" == "netbird" ]]; then
            printf 'FQDN: bifrost-debug.example.netbird.cloud\n'
            return 0
        fi
        return 0
    }

    local output
    output="$(cmd_status)"
    [[ "$output" != *"Open:     https://example.netbird.services"* ]] || {
        echo "FAIL: status must not print unready public URL" >&2
        exit 1
    }
    [[ "$output" == *"Open:     http://bifrost-debug.example.netbird.cloud  (public proxy still provisioning)"* ]] || {
        echo "FAIL: status should show private/provisioning fallback, got: $output" >&2
        exit 1
    }
}

test_public_smoke_rejects_netbird_html_status
test_public_smoke_rejects_json_without_bifrost_status_shape
test_public_smoke_requires_cli_gzip
test_public_smoke_accepts_bifrost_status_and_cli_download
test_public_smoke_rejects_transport_failure
test_public_smoke_uses_bounded_curl_timeouts
test_public_expose_retries_until_public_smoke_passes
test_public_expose_stops_at_deadline_not_probe_count
test_apply_public_url_recreates_app_services_when_public_env_changes
test_status_hides_unready_public_url

echo "PASS: debug.sh public readiness"
