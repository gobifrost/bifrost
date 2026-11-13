#!/bin/sh
set -eu

edge_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
client_dir=$(CDPATH= cd -- "$edge_dir/../../../client" && pwd)
image="bifrost-frontend-assets-$$"
container=""
cleanup() {
    if [ -n "$container" ]; then docker rm "$container" >/dev/null; fi
    docker image rm "$image" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# Reuse the production client build, including its locked dependencies and SDK stubs.
docker build --target builder --tag "$image" "$client_dir"
container=$(docker create "$image")
rm -rf "$edge_dir/assets"
mkdir -p "$edge_dir/assets"
docker cp "$container:/app/dist/." "$edge_dir/assets/"
