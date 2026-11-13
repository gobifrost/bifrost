# npm image hygiene and runtime dependency audit

Issue: #1381. Baseline: `7be95b942c055a0ba0e52981a845435598a9cdec`.

## Scope

Clean the npm cache in the same layer as each project installation in the API
production/dev images and client dev image. Clean the cache after the pinned
global npm installation too. Removing cache in a later layer would leave its
bytes in the earlier layer.

The SDK builder loads esbuild from `app_bundler/node_modules/esbuild`. Its own
manifest retains TypeScript, React declarations and lucide for SDK declaration
emission and hosted SDK builds. All three service dependency directories and
their existing anonymous-volume mounts remain independent.

## Runtime keep/drop decisions

- **Keep npm and Node.** `solutions/app_build.py` runs `npm ci` or `npm install`
  and refreshes SDK lockfiles during server-side builds. A Node-only image would
  remove supported App/Solution build functionality. The npm resolver remains
  checksum-pinned by `api/scripts/install-npm.sh`.
- **Keep tiktoken.** The installed `pydantic-ai-slim` 2.66.0 metadata requires
  `tiktoken>=0.12.0` for its `openai` extra, which Bifrost selects. Its
  `embeddings/openai.py` imports tiktoken and calls `encoding_for_model` for
  local token counting; it also has realtime consumers. This transitive
  dependency remains required even without direct Bifrost imports.
  Preserve the hash-locked dependency closure (`tiktoken` 0.14.0).
- **Keep textual and rich.** The unified image exposes the Bifrost CLI for
  workflow shell-outs and includes the CLI package. `bifrost/cli.py` imports
  `WatchApp` for interactive watch, `interactive_sync` for interactive sync,
  and `ProgressApp` for sync progress. Those runtime imports sit outside
  `TYPE_CHECKING`. `bifrost/tui/__init__.py` imports the UI modules, which use
  textual and rich. Installed textual 8.2.4 requires rich >=14.2.0. Retaining
  these packages preserves the complete supported CLI; noninteractive paths
  alone would not prove interactive functionality can be dropped.

No role-specific images, Python dependency split, awscli replacement, pnpm
migration, or changes to customer dependencies are included. #1379 and #1380
retain their separate scopes.

## Measurement

A controlled Linux amd64 image build isolated the three project `npm ci` layers
using the same pinned Node 26.13.0 image, its bundled npm 11.20.0, and each
baseline/candidate lockfile. `docker image inspect --format '{{.Size}}'` measured
536,931,039 bytes before and 480,250,768 bytes after: 56,680,271 bytes
(54.05 MiB) less in those image layers. This is an isolated layer comparison,
not a published-image pull-size measurement, and excludes the pinned-global-npm
cache improvement. The retained `/root/.npm` directory contains logs rather
than `_cacache` after cleaning. The issue's 150–250 MB estimate is not a
measured result.

## Full production image comparison

Both production images were built on Linux amd64 with `api/Dockerfile`, the
same pinned bases, the same `BIFROST_VERSION=1381-check` argument, and identical
Python dependency locks. The baseline context was extracted with
`git archive 7be95b942c055a0ba0e52981a845435598a9cdec`; the candidate used this
worktree. No dependency versions were changed.

`docker image inspect --format '{{.Size}}'` reported 2,017,831,195 bytes for
`bifrost-1381-prod-before` and 1,921,776,732 bytes for
`bifrost-1381-prod-after`: a reduction of **96,054,463 bytes (91.60 MiB)**.
This is local cumulative image size, not compressed registry transfer size.
It includes global npm cache cleanup and the production ownership layer,
which copies the smaller dependency tree. The isolated npm-layer comparison
above explains why its delta differs.

To repeat, extract the baseline into a scratch directory and run each context
with `docker build --build-arg BIFROST_VERSION=1381-check -f api/Dockerfile -t <tag> .`,
then inspect the two tags. Keep architecture and baseline commit fixed.

## Focused verification

- `./test.sh tests/unit/test_sdk_package.py tests/unit/test_sdk_package_fingerprint.py tests/unit/test_app_bundler.py -v`:
  34 passed, zero skipped. Real SDK tarball builds cover JavaScript exports,
  generated declarations, license preservation and hosted factory construction
  while the SDK's local esbuild directory is absent.
- `./test.sh tests/unit/test_npm_install_integrity.py tests/integration/test_app_bundler_pipeline.py tests/integration/test_app_bundler_tailwind_e2e.py -v`:
  13 passed, zero skipped. Covers the pinned npm installer and real Tailwind
  compiler pipeline, including per-App configuration and authored CSS.
- `./test.sh quality api`: pyright reported zero errors/warnings; ruff passed.
- Broader backend, client and browser suites were not run locally. No API,
  CLI, SDK wire contract or customer installation requirements changed.
