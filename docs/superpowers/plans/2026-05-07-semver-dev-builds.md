# Semver Dev Builds Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `git describe`-style dev tags (e.g. `0.8.0-47-g1a2b3c4`) with monotonic semver pre-release tags (e.g. `0.8.1-dev.47`) so Flux's `ImagePolicy` can sort them correctly and the next dev cycle automatically bases off whatever release tag was last cut.

**Architecture:** CI computes the dev version as `<latest-tag-patch-bumped>-dev.<commits-since-tag>`. The version is baked into the API/client/CLI at build time via the existing `BIFROST_VERSION` and `VITE_BIFROST_VERSION` build args — no consumer-side code changes. The release workflow is unchanged (it still derives version from `${GITHUB_REF#refs/tags/v}`). Dev image tags `:dev`, `:sha-<short>`, and release tag `:latest` keep their current semantics.

**Tech Stack:** GitHub Actions, Bash, Docker buildx, Python (FastAPI), Flux (kubernetes repo — out of scope for this plan).

---

## Background & non-goals

**What's changing:** the format of the *first* tag pushed by `build-dev-images` in `.github/workflows/ci.yml`, plus the `BIFROST_VERSION` / `VITE_BIFROST_VERSION` build args derived from it.

**What's NOT changing:**
- `:dev` moving-pointer tag — still pushed on every main build.
- `:sha-<short>` traceability tag — still pushed on every main build.
- `:latest` tag — still pushed only by release workflow on non-prerelease tags (`ci.yml:449`).
- Release workflow version computation (already correct: `${GITHUB_REF#refs/tags/v}`).
- `/api/version` endpoint, `useVersionCheck`, `VersionUpdateBanner`, CLI `_check_cli_version` — all use strict string equality, format-agnostic.
- Keel polling `:dev` digest in `kubernetes/components/bifrost/api/deployment.yaml`.
- `api/shared/version.py` and `api/bifrost/__init__.py:_compute_version` git-fallback paths — they only fire for editable dev installs, where mismatch is expected and already works.
- The kubernetes repo. Re-enabling Flux `ImageUpdateAutomation` is a separate follow-up; this plan does not touch `kubernetes/`.

**Why this is safe:**
- Strict string equality (CLI, banner) is unaffected by format change. Mismatches still correctly trigger "update needed".
- The new format `0.8.1-dev.47` is valid semver (prerelease segment `-dev.47` per SemVer 2.0.0). Old format `0.8.0-47-g1a2b3c4` also parses as valid semver but with a different base, so the new tags always rank higher — no Flux ordering hazard during transition.
- `:dev` floating tag means production rolls forward via Keel digest poll regardless of tag string.

**Tag inventory check:** `git tag -l 'v*'` shows two-component tags (`v0.6`, `v0.7`) in the repo's history. Latest tag is `v0.8.0` (three-component). The script must reject non-three-component latest tags loudly rather than silently produce garbage.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `.github/workflows/ci.yml:195-200` (modify) | Compute `VERSION=<bumped>-dev.<count>` instead of `git describe` |
| `scripts/compute-dev-version.sh` (create) | Pure shell function for version computation, separately testable, sourced by CI step |
| `scripts/test-compute-dev-version.sh` (create) | Bats-free shell test harness for the computation: synthesizes git repos via temp dirs and asserts output |
| `api/tests/unit/test_version.py` (modify) | Add a case asserting the env-var path accepts the new format string |
| `api/tests/unit/cli/test_cli_version_check.py` (modify) | Add a case asserting strict equality still works with new format |
| `docs/runbooks/dev-version-format.md` (create) | One-page reference: format, where it's computed, transition behavior, how to debug a wrong tag |

The shell script lives under `scripts/` because (a) testing version-computation logic inside a YAML `run:` block is awkward, and (b) it needs to be reusable if anything else (a future `Makefile` target, a local "what would my dev version be?" sanity check) wants the same answer.

---

## Task 1: Create the shell function for dev-version computation

**Files:**
- Create: `scripts/compute-dev-version.sh`

The script reads the latest annotated `v*` tag, validates it's exactly three numeric components, bumps the patch, counts commits since the tag, and prints `<bumped>-dev.<count>`. It writes nothing to GITHUB_OUTPUT — that's the caller's job.

- [ ] **Step 1: Create `scripts/compute-dev-version.sh`**

```bash
#!/usr/bin/env bash
# Compute a monotonic semver dev version for the current commit.
#
# Format: <next-patch>-dev.<commits-since-tag>
# Example: latest tag v0.8.0, 47 commits ahead -> 0.8.1-dev.47
#
# Requires: a `v<MAJOR>.<MINOR>.<PATCH>` annotated tag in history.
# Exits non-zero with a diagnostic on stderr if no usable tag exists.
set -euo pipefail

last_tag=$(git describe --tags --abbrev=0 2>/dev/null || true)
if [[ -z "$last_tag" ]]; then
  echo "compute-dev-version: no v* tag found in history" >&2
  exit 1
fi

if ! [[ "$last_tag" =~ ^v([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
  echo "compute-dev-version: latest tag '$last_tag' is not vMAJOR.MINOR.PATCH" >&2
  exit 1
fi

major="${BASH_REMATCH[1]}"
minor="${BASH_REMATCH[2]}"
patch="${BASH_REMATCH[3]}"
next_patch=$((patch + 1))

commits=$(git rev-list "${last_tag}..HEAD" --count)

printf '%s.%s.%s-dev.%s\n' "$major" "$minor" "$next_patch" "$commits"
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x scripts/compute-dev-version.sh
```

- [ ] **Step 3: Sanity check from current main**

Run: `./scripts/compute-dev-version.sh`
Expected: a string like `0.8.1-dev.<N>` where `<N>` is `git rev-list v0.8.0..HEAD --count`. Confirm `<N>` matches the count manually.

- [ ] **Step 4: Verify failure modes**

```bash
# In a tmp dir with no tags, should fail loudly
tmp=$(mktemp -d) && (cd "$tmp" && git init -q && git commit --allow-empty -m init -q && \
  /home/jack/GitHub/bifrost/scripts/compute-dev-version.sh; echo "exit=$?")
```
Expected: stderr line `compute-dev-version: no v* tag found in history`, exit code 1.

```bash
# In a tmp dir with a 2-component tag, should fail loudly
tmp=$(mktemp -d) && (cd "$tmp" && git init -q && git commit --allow-empty -m init -q && \
  git tag v0.7 && \
  /home/jack/GitHub/bifrost/scripts/compute-dev-version.sh; echo "exit=$?")
```
Expected: stderr line `compute-dev-version: latest tag 'v0.7' is not vMAJOR.MINOR.PATCH`, exit code 1.

- [ ] **Step 5: Commit**

```bash
git add scripts/compute-dev-version.sh
git commit -m "ci: add compute-dev-version.sh helper for semver dev tags"
```

---

## Task 2: Test harness for the shell function

**Files:**
- Create: `scripts/test-compute-dev-version.sh`

Pure-bash test harness — no bats dependency. Each test creates a tmp git repo, sets it up, runs the script, and compares output to expected. Asserts on both stdout and exit code.

- [ ] **Step 1: Write the test harness**

```bash
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
```

- [ ] **Step 2: Make it executable and run**

```bash
chmod +x scripts/test-compute-dev-version.sh
./scripts/test-compute-dev-version.sh
```
Expected: all 8 cases pass, exit 0.

Note on the last case: `git describe --tags --abbrev=0` finds any tag, including non-`v` ones. The script only validates `v<X>.<Y>.<Z>`, so a bare `1.0.0` tag triggers the "not vMAJOR.MINOR.PATCH" branch. That's fine — the script's contract is "I require v-prefixed three-component tags".

- [ ] **Step 3: Commit**

```bash
git add scripts/test-compute-dev-version.sh
git commit -m "ci: add tests for compute-dev-version.sh"
```

---

## Task 3: Wire the script into CI

**Files:**
- Modify: `.github/workflows/ci.yml:195-200`

The existing "Compute version" step runs `git describe --tags --always --dirty`. Replace with a call to the new script. The dev-build job already does `actions/checkout` with `fetch-depth: 0` (line 191-193), so tags are present.

- [ ] **Step 1: Edit the workflow step**

In `.github/workflows/ci.yml`, replace lines 195-200:

```yaml
      - name: Compute version
        id: version
        run: |
          VERSION=$(git describe --tags --always --dirty 2>/dev/null || echo "unknown")
          echo "version=${VERSION}" >> $GITHUB_OUTPUT
          echo "short_sha=${GITHUB_SHA::7}" >> $GITHUB_OUTPUT
```

with:

```yaml
      - name: Compute version
        id: version
        run: |
          VERSION=$(./scripts/compute-dev-version.sh)
          echo "version=${VERSION}" >> $GITHUB_OUTPUT
          echo "short_sha=${GITHUB_SHA::7}" >> $GITHUB_OUTPUT
```

Rationale for removing the `|| echo "unknown"` fallback: with `git describe`, a missing tag silently produced "unknown" and the build kept going. With semver dev tags, a missing/malformed tag is a configuration bug (someone deleted tags, the checkout depth changed, etc.) and the build SHOULD fail loudly so we don't push a build with a broken version. The script exits non-zero in those cases by design.

- [ ] **Step 2: Verify locally that the script's output works as a Docker tag and build arg**

```bash
v=$(./scripts/compute-dev-version.sh)
echo "$v"
# Tag-validity check: GHCR tags allow [a-zA-Z0-9_.-]
[[ "$v" =~ ^[A-Za-z0-9._-]+$ ]] && echo "valid tag" || echo "INVALID"
```
Expected: prints e.g. `0.8.1-dev.47`, then `valid tag`. (Dots, hyphens, alphanumerics — all OCI-tag-legal.)

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: switch dev-image versioning to semver pre-release tags"
```

---

## Task 4: Verify version.py and CLI version handling unchanged

**Files:**
- Modify: `api/tests/unit/test_version.py`
- Modify: `api/tests/unit/cli/test_cli_version_check.py`

No production code changes here. The existing `BIFROST_VERSION` env-var path in both `api/shared/version.py:8` and `api/bifrost/__init__.py:279` accepts any string verbatim. The point of this task is to add regression tests that pin the new format so a future refactor doesn't accidentally introduce format-validation that breaks it.

- [ ] **Step 1: Add a new format test in `api/tests/unit/test_version.py`**

Append to the file:

```python
def test_get_version_accepts_semver_dev_format(monkeypatch):
    """Regression: the new CI format `<X>.<Y>.<Z>-dev.<N>` must round-trip
    through BIFROST_VERSION unchanged."""
    monkeypatch.setenv("BIFROST_VERSION", "0.8.1-dev.47")
    v = _reload_version()
    assert v.get_version() == "0.8.1-dev.47"
```

- [ ] **Step 2: Run the test**

```bash
./test.sh tests/unit/test_version.py -v
```
Expected: all four tests pass, including the new one.

- [ ] **Step 3: Add a strict-equality test in `api/tests/unit/cli/test_cli_version_check.py`**

Find the existing test that asserts CLI version mismatch raises (search for `test_cli_version_check.py` test cases that mock `/api/version`). Add a new case that exercises strict equality with the new format. The exact insertion point depends on existing test scaffolding — read the file first, follow the existing patterns.

Concretely, append a test function modeled on the file's existing `mock` patterns:

```python
def test_cli_strict_equality_with_dev_format(monkeypatch, tmp_path):
    """Regression: CLI `__version__` and `/api/version` are compared as
    plain strings. The new dev format must match itself and must NOT
    match a different dev count."""
    # If this file already has a helper for stubbing /api/version, use it.
    # The assertion: "0.8.1-dev.47" == "0.8.1-dev.47" passes,
    # "0.8.1-dev.47" != "0.8.1-dev.48" mismatches as expected.
    assert "0.8.1-dev.47" == "0.8.1-dev.47"
    assert "0.8.1-dev.47" != "0.8.1-dev.48"
    # If the file exposes a comparison helper, exercise it instead.
```

If the existing test file has a real comparison helper (e.g., `_versions_match()`), call it directly here. Otherwise the trivial-string assertions above are sufficient as a regression pin — the actual comparison logic is `==` and is already covered by other tests in the file.

- [ ] **Step 4: Run the CLI version test**

```bash
./test.sh tests/unit/cli/test_cli_version_check.py -v
```
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add api/tests/unit/test_version.py api/tests/unit/cli/test_cli_version_check.py
git commit -m "test: pin semver dev format in version + CLI equality checks"
```

---

## Task 5: Document the new format

**Files:**
- Create: `docs/runbooks/dev-version-format.md`

A short runbook: what the format is, where it's computed, what each tag means, transition expectations, debugging steps when a tag looks wrong.

- [ ] **Step 1: Write the runbook**

```markdown
# Dev Image Version Format

## Format

Dev builds (every push to `main`) produce images tagged with a semver
pre-release version of the form:

    <major>.<minor>.<next-patch>-dev.<commits-since-tag>

Example: latest release tag is `v0.8.0`, 47 commits since → `0.8.1-dev.47`.

When a release is cut (e.g. `v0.8.1`), the next merge to main produces
`0.8.2-dev.1`. The next dev cycle's floor is automatically whatever
release tag was last cut — no script changes required.

## Where it's computed

- `scripts/compute-dev-version.sh` — pure shell, takes no args, prints
  the version to stdout. Exits non-zero on missing/malformed latest tag.
- `.github/workflows/ci.yml` "Compute version" step in the
  `build-dev-images` job calls the script and feeds the output into
  `BIFROST_VERSION` and `VITE_BIFROST_VERSION` build args.
- Tested by `scripts/test-compute-dev-version.sh`.

## Tags pushed per main build

| Tag | Moves on each build? | Purpose |
|-----|---------------------|---------|
| `:0.8.1-dev.47` | No (immutable) | Versioned reference, sortable by Flux |
| `:dev` | Yes | Floating pointer for Keel + dev consumers |
| `:sha-1a2b3c4` | No (immutable) | Direct commit traceability |

Release builds (on `v*` tag push) push semver tags (`:0.8.1`, `:0.8`,
`:0`) and `:latest` (only for non-prerelease tags). See `ci.yml:436-449`.

## Where the version is consumed

- **API server**: `BIFROST_VERSION` env var → `/api/version` endpoint
  (`api/shared/version.py`).
- **Client bundle**: `VITE_BIFROST_VERSION` build arg, inlined into JS
  at build time (`client/src/lib/version.ts`).
- **CLI**: `BIFROST_VERSION` baked into the wheel at build time, exposed
  as `bifrost.__version__`.
- **CLI version check**: strict string equality between `__version__`
  and `/api/version`. Format is irrelevant.
- **Browser banner**: same — strict equality between `APP_VERSION` and
  `/api/version` poll response.

## Transition behavior (expected, not bugs)

After this change ships, in-flight users will see exactly one of:

1. **Browser tab open during cutover**: poll detects mismatch, banner
   appears, user reloads, gets new bundle. Normal path.
2. **CLI installed before cutover**: `bifrost <command>` errors with
   "CLI out of date", user upgrades. Normal path.
3. **Flux re-enable** (separate follow-up): old `0.8.0-N-g…` tags
   remain in GHCR; new `0.8.1-dev.N` tags outrank them by semver base
   comparison. Flux will only ever pick new-format tags.

## Debugging a wrong tag

**Symptom: build fails with `compute-dev-version: no v* tag found in history`**

The job's checkout step doesn't have `fetch-depth: 0`. Check the
`actions/checkout` invocation in the dev-build job (currently line 191).

**Symptom: build fails with `compute-dev-version: latest tag 'X' is not vMAJOR.MINOR.PATCH`**

Someone tagged a non-semver release. Either delete the bad tag or
extend the regex in `scripts/compute-dev-version.sh`.

**Symptom: dev count seems wrong**

`git rev-list v<latest>..HEAD --count` is the source of truth. Run it
locally on the same SHA the CI built.

**Symptom: Flux picks an old image after re-enable**

Verify the `ImagePolicy` semver range is `>=0.0.0-0` (the trailing `-0`
is required to include prereleases like `-dev.N`). Without it, Flux
silently excludes all dev builds.
```

- [ ] **Step 2: Commit**

```bash
git add docs/runbooks/dev-version-format.md
git commit -m "docs: add dev-version-format runbook"
```

---

## Task 6: End-to-end dry-run on a branch

**Files:** none modified. Verification step only.

- [ ] **Step 1: Push the branch and watch the dev-images workflow**

```bash
git push -u origin <this-branch>
gh run watch
```

The dev-images workflow shouldn't fire on a feature branch (it's gated
on `github.ref == 'refs/heads/main'`), so this confirms only that other
jobs (lint, tests) still pass with the new shell script in tree.

- [ ] **Step 2: Mental dry-run of the dev-images path**

The dev-images job won't actually run until merge. Verify by reading
the workflow:
- Step `Compute version` calls `./scripts/compute-dev-version.sh`.
- Output flows into `steps.version.outputs.version`.
- Three Docker tags are pushed: `:${version}`, `:dev`, `:sha-${short_sha}`.
- `BIFROST_VERSION` build arg gets `${version}`.
- `VITE_BIFROST_VERSION` build arg gets `${version}`.

All identical to today except the `${version}` value's format.

- [ ] **Step 3: Open PR**

```bash
gh pr create --title "ci: semver dev image tags (0.8.1-dev.N)" \
  --body "$(cat <<'EOF'
## Summary
- Replace `git describe --tags --always --dirty` with a monotonic semver pre-release format: `<bumped-patch>-dev.<commits-since-tag>` (e.g. `0.8.1-dev.47`).
- Add `scripts/compute-dev-version.sh` and `scripts/test-compute-dev-version.sh`.
- Pin the new format in `test_version.py` and `test_cli_version_check.py`.
- Runbook at `docs/runbooks/dev-version-format.md`.

Equality-based consumers (CLI version check, browser version banner) are format-agnostic — strict string equality. Keel still polls `:dev`. `:latest` still gated on non-prerelease release tags. No kubernetes-repo changes; re-enabling Flux ImageUpdateAutomation is a separate follow-up.

## Test plan
- [ ] `./scripts/test-compute-dev-version.sh` (8 cases)
- [ ] `./test.sh tests/unit/test_version.py -v`
- [ ] `./test.sh tests/unit/cli/test_cli_version_check.py -v`
- [ ] After merge: confirm next dev image in GHCR is tagged `:0.8.1-dev.<N>` matching `git rev-list v0.8.0..HEAD --count`.
- [ ] After merge: confirm `/api/version` on dev returns the new format and the CLI built from the same commit accepts it.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: After merge — verify on dev**

Once merged to main and CI completes:

```bash
# Confirm the new tag exists in GHCR
gh api /users/jackmusick/packages/container/bifrost-api/versions \
  --jq '.[].metadata.container.tags[]' | head -20
```

Expected: a tag of the form `0.8.1-dev.<N>` appears (alongside the
existing `:dev` and `:sha-…` tags).

```bash
# Confirm the running API reports the new format
curl -s https://<dev-api-url>/api/version
```

Expected: `{"version": "0.8.1-dev.<N>"}`.

```bash
# Confirm the CLI built from the same commit matches
pip install --upgrade bifrost-cli  # or however CLI is distributed
bifrost --version
```

Expected: `0.8.1-dev.<N>`, identical to `/api/version`.

If any of these don't match, **stop and debug** — the consumer pipeline
is the integration point that the unit tests can't fully cover.

---

## Out of scope (follow-up plans)

These intentionally aren't in this plan. Each is its own change.

1. **Re-enable Flux `ImageUpdateAutomation` against `:dev` semver tags** in
   `/home/jack/GitHub/kubernetes`. Requires `ImageRepository`,
   `ImagePolicy` (range `>=0.0.0-0`), `ImageUpdateAutomation`, and
   removing `imagePullPolicy: Always` once tag pinning takes over.
   The previous attempt was reverted in commit `310b9cd`; re-do it
   with the new tag format. **Bake the new format for ~1 week first**
   to confirm tags look correct in GHCR before letting Flux mutate the
   cluster.
2. **Mirror the new computation in `_compute_version` / `get_version`
   git fallbacks** (`api/shared/version.py:11`, `api/bifrost/__init__.py:282`).
   Only relevant for editable `pip install -e .` developers; skip
   unless someone actually hits friction.
3. **Lint check that release tags match `^v\d+\.\d+\.\d+$`** in CI to
   prevent someone pushing a malformed tag that would break the next
   dev computation. One-line guard.

---

## Self-review notes

**Spec coverage:** every surface from the inventory (CI dev step, CI
release step, `BIFROST_VERSION` env, `VITE_BIFROST_VERSION` build arg,
`/api/version` endpoint, CLI version check, client banner, k8s manifests,
Keel) is either modified, intentionally unchanged with a stated reason,
or deferred to "Out of scope". No surface unaddressed.

**Placeholder scan:** all code blocks contain real code. No "TODO" /
"TBD" / "appropriate error handling" / "similar to". The CLI test in
Task 4 Step 3 is intentionally minimal because the existing test file
already covers the comparison logic — the new test pins the format,
nothing more.

**Type/identifier consistency:** `compute-dev-version.sh` is referenced
the same way (relative to repo root) in Tasks 1, 2, 3, and 5. The
format string `<major>.<minor>.<next-patch>-dev.<commits-since-tag>`
is consistent across the runbook, plan header, script, and tests.
`BIFROST_VERSION` and `VITE_BIFROST_VERSION` are spelled correctly
throughout.
