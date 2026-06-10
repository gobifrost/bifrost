# Plan: CI/Merge Pipeline Restructure — Speed + Release Channels

Date: 2026-06-09 · Status: execution-ready (facts verified against live runs, repo tree, branch experiments, GitHub docs)
Companion: WS-18 in `docs/plans/2026-06-09-platform-50ft-action-plan.md`

**Repo:** `jackmusick/bifrost` (public, **User-owned** — confirmed `owner_type: User` via API) · **Org:** `gobifrost` exists, **free plan**, contains an *empty placeholder* repo named `bifrost` (size 0, created 2025-08-11, never pushed)

---

## 0. Verified facts (measured, not vibes)

### 0.1 What the pipeline actually is

| Trigger | What runs | Source |
|---|---|---|
| `pull_request` → main | Lint & Type Check, Unit Tests, E2E Tests (all three **required** checks, `strict_required_status_checks_policy: true` = branches must be up to date) | `.github/workflows/ci.yml:69-179`; ruleset 15329014 via `gh api repos/jackmusick/bifrost/rules/branches/main` |
| `push` → main | **Tests are skipped** (`if:` guards on all 3 test jobs) → `build-dev` (:dev + semver-dev + sha images, GHA-cached, cosign-signed) → `deploy-dev` (rolls the DO cluster) | `ci.yml:72,136,165,184-426` |
| `push` tag `v*` | lint+unit+e2e re-run on the tag ref → `build-api`/`build-client` (needs all three) → `create-release` | `ci.yml:431-759` |
| `merge_group` | Full CI — **trigger already wired but dormant**: merge queue cannot be enabled on a user-owned repo | `ci.yml:54`; `docs/superpowers/specs/2026-05-07-merge-queue-design.md` |
| Docs-only PRs | `ci-noop.yml` reports the same three check names as always-green stubs | `.github/workflows/ci-noop.yml` |

Merging is owned by **Kodiak** (`.kodiak.toml`): `automerge` label + `update_branch_immediately = true`, `optimistic_updates = false`. With strict up-to-date required, every merge to main invalidates every other queued PR, which Kodiak then rebases and **fully re-tests, one at a time**. The merge-queue spec's ci.yml half already landed (push-to-main test skip + `merge_group:` trigger); the queue itself is the blocked half.

Coverage fact: CI's "E2E Tests" is **backend pytest only**. Client vitest and Playwright (`./test.sh client unit|e2e`) run in **no CI job at all** — `test.sh:240-283` exists, ci.yml never calls it.

### 0.2 Timing reality (live runs, 2026-06-09)

PR CI over the last 100 runs: **54 successes, avg 17 min wall (min 15, max 18)**. Representative run 27238964717, per-job:

| Job | Wall | Breakdown |
|---|---|---|
| Lint & Type Check | **2m37s** | ruff + pyright + tsc + eslint |
| Unit Tests | **7m29s** | ~4m04s docker test-stack build/boot (**uncached — built from scratch every run**) + **2m32s pytest (4,055 tests)** |
| E2E Tests | **18m49s** | ~4m45s stack build/boot + **13m48s pytest (1,347 tests, serial — no xdist, no sharding; no `-n` in addopts)** |
| Post-merge (push main) | **6m34s** | build-dev 3m27s (GHA-cached) + deploy-dev 3m07s — unattended |

**The "17 minutes" decomposed:** the merge critical path is the E2E job (~19 min) on the PR ref, **multiplied by Kodiak's rebase-retest serialization**. Real example (2026-06-09, "CLI contract-version gate" PR): CI runs at 21:28, 21:36, 21:45 (failed — a *real* e2e catch, MCP-protocol regressions), 22:06 → merged 22:25. Throughput: **36 commits to main in 14 days, peak 9/day** — on a 9-merge day, strict-up-to-date serialization alone is ~2.5 hours of redundant re-test. PR-run failure rate: **19/73 (26%)** — a mix of real catches and the known flake clusters (builtin_events / data_providers ReadTimeout). Post-merge cost (6.5 unattended min) is **not** part of the felt pain.

### 0.3 The abandoned experiments already solved half of this

| Branch | What it built | State |
|---|---|---|
| `ci/cache-dev-images` @ `51e42b71` | GHA layer-cache for the test stack's dev images (`Dockerfile.dev` via build-push-action, `BIFROST_SKIP_BUILD=1`, all five api-stack services on one image tag) — attacks the ~4–4.7 min per-job stack-build tax, paid twice per PR | Complete, never merged |
| `ci/cache-dev-images` @ `075c1853` | **2-way e2e sharding**: `scripts/e2e_shard.py` (deterministic, heavies bin-packed first — weights from a real run: test_executions.py 70s, large_file_memory 35s, worker_memory 32s…), matrix job, ci-noop stubs updated, notes the required-check rename | Complete, never merged |
| `experiment/ci-speedup` @ `6111312c` | `--durations=50` profiling on e2e | Trivial, harvest |
| `.worktrees/ci-cache-clean` | Checkout of the above (+ `--no-reset` flag restore) | Same commits |
| `experiment/ci-max-speed` | No commits beyond main (dead) | Ignore |

Combined effect, estimated honestly: e2e job ≈ max(shard) ≈ 2m cached boot + ~7m tests ≈ **9–10 min**; unit ≈ **~5 min**. **PR CI wall drops ~17 → ~10 min with zero coverage loss and zero org-transfer dependency.**

### 0.4 Org-transfer facts (GitHub docs, 2026-06)

- **Merge queue availability:** public repos **owned by an organization** (any plan, free included — gobifrost qualifies); private needs Enterprise Cloud; **not available on user-owned repos, period.** The constraint is real.
- **Transfers preserve:** issues, PRs, wiki, stars, watchers, forks, **webhooks, secrets, deploy keys**, Actions history, LFS. **Git remote URLs redirect** automatically (clones, badges, `gh`).
- **Transfers do NOT redirect: ghcr.io namespaces.** `ghcr.io/jackmusick/bifrost-api` does not become `ghcr.io/gobifrost/bifrost-api`; there is **no registry redirect mechanism**. Existing packages stay under `jackmusick`; the transferred repo's `GITHUB_TOKEN` pushes to `ghcr.io/gobifrost/*`. Every image reference is a manual migration: `~/GitHub/kubernetes/components/bifrost/*/deployment.yaml` (4 manifests, Keel-polling `:dev`), the flux experiment worktree, `docker-compose.yml`, `k8s/*/deployment.yaml`, `README.md`, the `bifrost-release` skills, the release-notes template inside `ci.yml:718-754` (docker pull commands, **cosign `--certificate-identity-regexp 'https://github\.com/jackmusick/bifrost/.*'`**, `gh attestation verify --owner jackmusick`), docs site.
- **Other breakage points:** OIDC subject changes (`repo:gobifrost/bifrost:*`) → new artifacts sign under the new identity (old release verify instructions stay valid for old artifacts); Codecov re-link + fresh token; Kodiak installed on the personal account (moot if the queue replaces it); Dependabot/code-scanning data expected to carry but org security defaults must be checked; Scorecard/badge URLs re-key; ruleset 15329014 moves with the repo. **Pre-flight blocker:** the empty `gobifrost/bifrost` placeholder must be deleted/renamed or the transfer fails on name collision.

### 0.5 Channels today

`:dev` = every merge → `build-dev` → Keel (poll @15m, force) + CI's `deploy-dev`. Prod (`~/GitHub/kubernetes`, gocovi-apps) **tracks `:dev`**. Tag flow already distinguishes pre-releases: `v*-rc.N` tags get full e2e gating, version-string image tags, **no `:latest`** (`enable=${{ !contains(github.ref, '-') }}`), Release marked prerelease. Missing for a pre-release *channel*: a **floating `:pre-release` docker tag**, a Keel/Flux policy change in the kubernetes repo (the `bifrost-flux-image-automation` worktree already prototyped semver pinning), and user guidance. Friction: `build-api`'s guard requires plugin-manifest versions to equal the tag (`ci.yml:460-477`) — rc tags would force manifest churn unless the guard skips prereleases.

---

## 1. Options weighed

**A. Owner's proposal — main merges run unit+lint only; e2e moves to pre-release/release tags. REJECTED as primary lever.** It attacks the right number from the wrong side. Buys: PR wall ~7.5 min — *less* than Option C buys (~10 min) once you account for Kodiak still re-running CI per queued PR (serialization is per-merge, not per-minute). Costs: (1) **broken-main windows** — e2e demonstrably catches real pre-merge regressions (the 2026-06-09 MCP-protocol failure; 26% PR failure rate is not all flakes), and a solo maintainer has no second person to fix main; (2) **batched discovery** — a tag-time e2e failure is a bisect across ~10–20 commits at exactly release time; (3) the flake clusters convert from a per-PR retry annoyance into **release-blocking roulette**; (4) prod-on-:dev means a post-merge e2e regression reaches prod within ~15 min via Keel unless the channel move ships first. The channel half of the proposal is good (C3); the gating half is not.

**B. Stay user-owned, drop strict up-to-date (Kodiak merges without rebase-retest).** Cheapest serialization fix, but lands logically-conflicting PRs on a main that runs no tests on push. The merge-queue spec already rejected this. REJECTED.

**C. Composite — RECOMMENDED:**
- **C1 — speed (no transfer needed):** resurrect `ci/cache-dev-images` (cached test images + 2-way e2e shard). PR wall ~17 → ~10 min. The work is already written; it just never landed.
- **C2 — transfer to gobifrost + native merge queue:** kills Kodiak's N×17-min serialization (N PR runs in parallel + one batched queue run); `ci.yml` is already `merge_group`-ready; the design spec is written and still correct. Only *hard* cost is the ghcr namespace break — fully mitigable with a dual-publish window. Branding alignment (gobifrost.com) is a free bonus. **Worth it.**
- **C3 — channels:** keep `:dev` building per merge (free, unattended), but **move prod + recommended-user-default to a pre-release/release train**: rc tags → full e2e gate (exists) → `:pre-release` floating tag → kubernetes repo tracks it. This decouples prod and users from per-merge risk — the *legitimate* core of the owner's instinct — without removing the e2e merge gate.

**Parked alternatives:** path-filtered e2e (small win post-sharding; `merge_group` doesn't support path filters); xdist (shared-stack isolation is why the suite is stack-per-run; sharding gets parallelism without it); test-impact selection (only if e2e creeps past ~12 min again); auto-revert culture (wrong fit solo); Kodiak batch mode (inferior to native queue).

---

## 2. Recommendation

**Do not take e2e off the merge path. Make it cheap to keep, then make merges stop serializing, then give prod and users a calmer channel.** Target state:

| Event | Runs | Wall |
|---|---|---|
| PR | lint+types (+vitest, new) · unit · e2e ×2 shards (all cached) | **~10 min** |
| Merge queue group (batch ≤5) | same suite once per batch | ~10 min amortized, unattended |
| Push main (post-queue) | build :dev + deploy dev cluster only | 6.5 min, unattended |
| Tag `v*-rc.N` | full suite → images + **`:pre-release` floating tag** → prod converges via Keel | release-gated |
| Tag `v*` stable | unchanged (+`:latest`) | unchanged |

User guidance after Phase 3: **`:latest`/pinned for production, `:pre-release` for early adopters, `:dev` explicitly "CI edge, may break."** Sequence the transfer (Phase 2) *after* the speed work (Phase 1) so the queue's batched runs are 10-minute runs, not 17.

---

## 3. Task breakdown (mid-tier executor sized, with verification)

### Phase 1 — CI speed (repo-local, no transfer, ship this week)

**Task 1.1 — Rebase and land the cache commit.** Cherry-pick `51e42b71` from `ci/cache-dev-images` onto main (expect drift in `ci.yml` — the branch forked pre-merge_group — and `docker-compose.test.yml`; re-apply by intent, not blind cherry-pick: pre-build `api/Dockerfile.dev` + `client/Dockerfile.dev` with `cache-from/to: type=gha`, fixed tags, `BIFROST_SKIP_BUILD=1` in both test jobs, all five api-stack services on one `image:` tag).
*Verify:* cache hits on second PR run; Unit job ≤ 5 min; `./test.sh stack up` still works locally with `BIFROST_SKIP_BUILD` unset.

**Task 1.2 — Land the e2e shard commit.** Cherry-pick `075c1853` (+ `6111312c` `--durations=50`). Refresh `scripts/e2e_shard.py` WEIGHTS from a current `--durations` run (suite has grown since May). Update `ci-noop.yml` stub matrix.
*Verify:* both shards green; shard wall spread < 3 min; **then** update ruleset 15329014 required checks: replace `E2E Tests` with `E2E Tests (shard 1/2)` + `(shard 2/2)` — same hour (between landing and ruleset update, PRs gate only on lint+unit).

**Task 1.3 — Add client vitest to CI (gap closure, cheap).** Append vitest to the Lint job after eslint, or as a parallel ~2-min job.
*Verify:* lint job ≤ 5 min; a deliberately broken component test fails CI.

**Task 1.4 — Measure.** *Done when:* PR CI wall ≤ 11 min over 5 consecutive runs. If e2e shards exceed 10 min, go to 3 shards (`--total`).

### Phase 2 — Org transfer + merge queue (one morning, after Phase 1 soaks ~1 week)

**Task 2.1 — Pre-flight (day before).** Delete the empty `gobifrost/bifrost` placeholder (re-verify `size: 0` first); decide fate of `bifrost-c44b10d0`. Confirm org role for transfer. Snapshot ruleset JSON + secrets list (names) for post-transfer diff.

**Task 2.2 — Transfer + same-day checklist.**
1. Verify carried over: ruleset, secrets (`CODECOV_TOKEN`, `DIGITALOCEAN_ACCESS_TOKEN`, `DO_CLUSTER_NAME`), webhooks, Actions history, Dependabot + code-scanning history; check org security defaults.
2. **Enable merge queue** per the existing spec: require queue; **disable** "require branches up to date"; required checks = Lint & Type Check, Unit Tests, both e2e shards; squash; concurrency 1; batch ≤5; timeout 60m.
3. **Remove Kodiak**: delete `.kodiak.toml`, drop the `automerge` label flow, switch to `gh pr merge --auto --squash`; update the `bifrost-issues` skill (hard-codes the Kodiak protocol) and `bifrost-release` skill.
4. Update local remotes.
*Verify:* trivial PR auto-queues → `merge_group` run fires → merges → `build-dev` fires. Two PRs queued together produce **one** queue run.

**Task 2.3 — ghcr namespace migration (the one real risk — own it).** Switch `ci.yml` env to `gobifrost/bifrost-api|client`. Transitional dual-publish of `:dev` + `:latest` to old `ghcr.io/jackmusick/*` via a PAT secret (`write:packages`), sunset after 2 stable releases or 60 days. Same PR: `docker-compose.yml`, `k8s/*/deployment.yaml`, `README.md`, both `bifrost-release` skills, `ci.yml` release body (pulls, cosign identity regexp → `gobifrost/bifrost`, attestation `--owner gobifrost`). Separate PR to `~/GitHub/kubernetes` (4 manifests + flux worktree). Mark old packages "moved".
*Verify:* `docker pull ghcr.io/gobifrost/bifrost-api:dev` works; old-namespace `:dev` digest still advances during the window; prod Keel rolls cleanly; `cosign verify` passes with the new identity.

### Phase 3 — Pre-release channel (independent of Phase 2; can precede it)

**Task 3.1 — `:pre-release` floating tag + guard relaxation.** In `build-api`/`build-client` metadata: `type=raw,value=pre-release,enable=${{ contains(github.ref, '-') }}`. Plugin-manifest guard skips prerelease tags. Optional explicit decision: multiarch on tag builds only — don't slow PR/dev builds for it.
*Verify:* push `v0.X.Y-rc.1` → full e2e gate → `:pre-release` on ghcr, `:latest` untouched, Release marked prerelease.

**Task 3.2 — Point prod at the channel.** `~/GitHub/kubernetes`: four bifrost deployments `:dev` → `:pre-release` (Keel annotations unchanged), or adopt the flux image-automation prototype with a semver-with-prerelease ImagePolicy. CI's `deploy-dev` DO rollout stays on `:dev` (it is the dev environment).
*Verify:* cut an rc → prod converges within one Keel poll; `/api/version` reports the rc; a subsequent merge to main does **not** move prod.

**Task 3.3 — User guidance + train cadence.** README/docs/gobifrost.com channel table (release ▸ pre-release ▸ dev with explicit stability promises). `bifrost-release` skill cadence note: rc when main has meaningful change (weekly-ish), stable after an rc soaks in prod.
*Done when:* docs published; one full train (merge → rc → prod soak → stable) executed.

### Explicitly rejected (do not relitigate monthly)
Unit-only merges to main — rejected for broken-main windows, bisect cost, flake-at-release-time, and prod-on-:dev exposure. Revisit only if post-sharding e2e wall exceeds ~12 min sustained.

---

Sources for §0.4: GitHub Docs (managing a merge queue; transferring a repository), community discussions #176157 (ghcr on transfer) and #51483 (merge queue availability), GitHub changelog (merge queue public beta).

## Critical files
- `.github/workflows/ci.yml` · `.github/workflows/ci-noop.yml` · `.kodiak.toml` (deleted in Phase 2)
- `docs/superpowers/specs/2026-05-07-merge-queue-design.md` (queue rollout design source)
- `~/GitHub/kubernetes/components/bifrost/*/deployment.yaml` (4 prod manifests, `:dev` → `:pre-release`)
- Harvest: branch `ci/cache-dev-images` commits `51e42b71` (image caching) + `075c1853` (e2e sharding, `scripts/e2e_shard.py`)
