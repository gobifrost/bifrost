#!/bin/sh
# Kubernetes invokes `uvicorn` directly and bypasses the image entrypoint.

set -e

script_dir=$(CDPATH= cd "$(dirname "$0")" && pwd)
. "$script_dir/api-allocator.sh"
configure_api_allocator

exec "$script_dir/uvicorn-real" "$@"
