# Runbook: jackmusick/bifrost → gobifrost org transfer

Date: 2026-06-09 · Companion: `2026-06-09-ci-pipeline-restructure-plan.md` (Phase 2) ·
Pre-staged: the [HOLD] ghcr-migration draft PR + `bifrost-ghcr-gobifrost` branch in `~/GitHub/kubernetes`.
Pre-flight already done: empty `gobifrost/bifrost` placeholder deleted (2026-06-09).

Legend: **[JACK]** = requires the human (UI/owner auth). **[AGENT]** = Claude does it on request.

## Before transfer day (any time)

1. **[JACK] gobifrost org policy prep** (Org → Settings):
   - Actions → General: allow all actions; **Workflow permissions = Read and write**; allow Actions
     to create/approve PRs if currently used.
   - Member privileges: confirm you (owner) can create repos / accept transfers.
   - Code security defaults: make sure org defaults won't *disable* Dependabot/code scanning on
     transferred repos.
2. **[JACK] Create the legacy-publish PAT**: jackmusick account → classic PAT, scope `write:packages`
   only, 90-day expiry. Hold the value for step 6.
3. **[AGENT] Snapshots**: ruleset JSON (`gh api repos/jackmusick/bifrost/rules/branches/main`),
   secret NAMES list, webhook list — saved for post-transfer diff.
4. Let the CI-speed PR soak (queue batches should be 10-min runs, not 17).

## Transfer morning (order matters)

5. **[JACK] The transfer**: repo Settings → Danger Zone → Transfer ownership → `gobifrost`. (~2 min.)
6. **[JACK] Add the PAT** as repo secret `GHCR_LEGACY_TOKEN` (paste value; agent can run
   `gh secret set` if you prefer to paste into the terminal).
7. **[AGENT] Verify carryover** against the snapshots: secrets present (they transfer with the
   repo — verify, don't re-create), webhooks, ruleset 15329014, Actions history, Dependabot +
   code-scanning alerts, environments. Re-point local remotes in all checkouts/worktrees.
8. **[AGENT] Enable merge queue** per `2026-05-07-merge-queue-design.md`: require queue on main;
   disable "require branches up to date"; required checks Lint & Type Check / Unit Tests /
   E2E Tests; squash; batch ≤5; concurrency 1; timeout 60m. (Ruleset API via your gh auth;
   you approve the command.)
9. **[AGENT] Retire Kodiak**: delete `.kodiak.toml`, drop the automerge-label flow, update
   bifrost-issues + bifrost-release skills to `gh pr merge --auto --squash`.
   **[JACK, optional, later]** uninstall the Kodiak GitHub App from the personal account.
10. **[AGENT] Merge the [HOLD] ghcr migration PR** (it transferred with the repo), watch the first
    `build-dev`: images land at `ghcr.io/gobifrost/*`, legacy step still advances
    `ghcr.io/jackmusick/*:dev`.
11. **[JACK] Make the new packages public** — first push creates them **private** by default:
    gobifrost org → Packages → `bifrost-api`, `bifrost-client` → Package settings → Change
    visibility → Public. (One-time; anonymous pulls + Keel depend on it. Agent can attempt the
    packages API first; the UI toggle is the fallback.)
12. **[AGENT] Merge `bifrost-ghcr-gobifrost`** in `~/GitHub/kubernetes` (4 manifests), watch Keel
    roll prod onto the new namespace. `cosign verify` one new image with the gobifrost identity.

## Aftercare

13. **[AGENT]** Two-PR smoke of the merge queue (two trivial PRs queued together → ONE group run).
14. **[JACK, 5 min, whenever]** Codecov: re-link the repo under gobifrost, refresh `CODECOV_TOKEN`
    if uploads fail.
15. **[AGENT]** Sunset tracking: remove the legacy dual-publish step + mark old packages
    "moved to ghcr.io/gobifrost/…" after 2 stable releases or 60 days, whichever first.
16. **[AGENT]** Badge/Scorecard URL sweep (README, docs site) — git redirects cover clones, not
    registries or badges that embed the old slug.

## Your total human surface

Org policy prep (one settings pass) · create one PAT · click the transfer · paste one secret ·
two package-visibility toggles · optional Kodiak uninstall + Codecov re-link. Everything else is
agent work with your gh auth.
