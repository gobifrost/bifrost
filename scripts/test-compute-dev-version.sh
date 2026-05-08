#!/usr/bin/env bash
# Test harness for scripts/compute-dev-version.sh
#
# Runs each test in an isolated tmp git repo. Asserts stdout and exit code.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPUTE="$SCRIPT_DIR/compute-dev-version.sh"

pass=0
fail=0

# run_test <name> <setup_block> <expected_stdout> <expected_exit>
run_test() {
  local name="$1" setup="$2" want_stdout="$3" want_exit="$4"
  local tmp got_stdout got_exit
  tmp=$(mktemp -d)
  (
    cd "$tmp"
    git init -q
    git config user.email t@t && git config user.name t
    eval "$setup"
  )
  set +e
  got_stdout=$(cd "$tmp" && "$COMPUTE" 2>/dev/null)
  got_exit=$?
  set -e
  rm -rf "$tmp"

  if [[ "$got_stdout" == "$want_stdout" && "$got_exit" == "$want_exit" ]]; then
    echo "PASS: $name"
    pass=$((pass + 1))
  else
    echo "FAIL: $name"
    echo "  want stdout='$want_stdout' exit=$want_exit"
    echo "  got  stdout='$got_stdout' exit=$got_exit"
    fail=$((fail + 1))
  fi
}

run_test "tag at HEAD, no commits ahead" \
  'git commit --allow-empty -m init -q && git tag v0.8.0' \
  '0.8.1-dev.0' 0

run_test "tag with 1 commit ahead" \
  'git commit --allow-empty -m init -q && git tag v0.8.0 && git commit --allow-empty -m c1 -q' \
  '0.8.1-dev.1' 0

run_test "tag with 47 commits ahead" \
  'git commit --allow-empty -m init -q && git tag v0.8.0 && for i in $(seq 47); do git commit --allow-empty -m "c$i" -q; done' \
  '0.8.1-dev.47' 0

run_test "release sets next dev cycle floor" \
  'git commit --allow-empty -m init -q && git tag v0.8.0 && git commit --allow-empty -m c1 -q && git tag v0.9.0 && git commit --allow-empty -m c2 -q' \
  '0.9.1-dev.1' 0

run_test "minor with double-digit patch" \
  'git commit --allow-empty -m init -q && git tag v1.2.10 && git commit --allow-empty -m c1 -q' \
  '1.2.11-dev.1' 0

run_test "no tags fails loudly" \
  'git commit --allow-empty -m init -q' \
  '' 1

run_test "two-component tag fails loudly" \
  'git commit --allow-empty -m init -q && git tag v0.7' \
  '' 1

run_test "non-v prefixed tag is ignored, falls back" \
  'git commit --allow-empty -m init -q && git tag 1.0.0' \
  '' 1

echo
echo "$pass passed, $fail failed"
exit $((fail > 0))
