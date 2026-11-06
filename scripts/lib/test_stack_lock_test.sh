#!/usr/bin/env bash
# Prove conflicting runner/lifecycle commands fail before touching Docker.
set -euo pipefail
repo_dir="$(git rev-parse --show-toplevel)"
source "$repo_dir/scripts/lib/test_helpers.sh"
project="$(compute_project_name "$repo_dir")"
lock_path="/tmp/bifrost-$project/test-stack.lock"
mkdir -p "$(dirname "$lock_path")"
exec {held_lock}>"$lock_path"
owns_lock=0
if flock -n "$held_lock"; then owns_lock=1; fi
for command in 'client e2e' 'stack down' 'unit' 'pre-pr'; do
    read -r -a arguments <<< "$command"
    if output=$(bash "$repo_dir/test.sh" "${arguments[@]}" 2>&1); then
        echo "FAIL: $command acquired an already owned stack" >&2
        exit 1
    fi
    [[ "$output" == *"another test command owns this worktree's test stack"* ]] || {
        echo "FAIL: $command did not reject the held lock" >&2
        exit 1
    }
done
bash "$repo_dir/test.sh" --help >/dev/null
exec {held_lock}>&-
# The owning shell releasing its descriptor must allow the next invocation.
if [ "$owns_lock" = 1 ]; then flock -n "$lock_path" true; fi
echo 'PASS: shared stack lock rejects browser, backend, lifecycle and pre-PR overlap'
