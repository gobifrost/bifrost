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
