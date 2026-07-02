# Solutions & File Policies — Adversarial QA Audit

**Date:** 2026-07-02
**Scope:** `main` @ `d9203cd0b`, as checked out. The Solutions feature (installable/read-only/deployable surfaces, PR #347 + follow-ups) and the file-policy evaluation system.
**Method:** Four grounded, read-only sub-agent lenses (invariant/bypass, file-policy correctness, lifecycle completeness, coverage edges), each finding cited to `file:line` and tagged confirmed vs. needs-live-verification. The two highest-severity findings (B1, A1) were re-verified by hand. No code was changed.

> **Headline:** No cross-tenant data leak or read-only-invariant bypass was found through any *normal* user CRUD surface — the core guard model holds and is installed unconditionally in every service. The real risks are at the **edges**: one un-gated MCP tool module, a hard-delete that orphans customer file bytes while reporting them swept, an install path still carrying the pre-#388 synchronous-timeout bug, config values with no solution ownership, and several "ships green but silently incomplete/uninstalls dirty" gaps in the Solutions lifecycle.

---

## TL;DR — ranked findings

| # | Lens | Sev | One-liner | Status |
|---|------|-----|-----------|--------|
| **B1** | Coverage | **High** | MCP `code_editor` tools grant un-policied, non-superuser read/write/delete over the entire `_repo/` workspace to any user with role access to an agent that has those system tools. | confirmed; end-to-end exposure needs-live-verification |
| **A1** | Coverage | **High** | Solution hard-delete orphans every solution **file byte** in S3 (`{location}/{install_id}/…`) while reporting `files_swept=N`; only `_solutions/{id}/` is actually swept. | **confirmed (hand-verified)** |
| **H1** | Lifecycle | **High** | `bifrost solution install` runs the full app build synchronously inside the request behind the default **30s** client timeout — the exact bug #388 fixed for `deploy`, still live on install. | confirmed (code); wall-clock needs-live-verification |
| **H2** | Lifecycle | **High** | Config values carry no `solution_id`; a full-backup restore collides on shared keys and `--replace-secrets` overwrites a sibling solution's live config value. | confirmed (mechanism); clobber path needs-live-verification |
| **P1** | File policy | **High** | `is_allowed(organization_id=None)` gate passes for **any** authenticated user, so one over-broad **global** file policy = platform-wide read. Safe by default (admin-only seed), zero defense-in-depth. | needs-live-verification |
| **S1** | Invariant | **Med** | Renaming a shared policy **rule** cascades a **Core-statement** `$ref` rewrite into solution-managed `FilePolicy`/`Table` JSON — bypasses both guard layers, mutating managed rows outside deploy. | confirmed-broken |
| **F5** | File policy | **Med** | `check_allowed`/`FilePolicyDenied` are dead code — and they were the *only* path that emitted a `policy.deny` audit event; live file denials are **unaudited**. | confirmed-broken |
| **A2** | Coverage | **Med** | Non-destructive uninstall only flips `status="inactive"`; cron schedules keep firing and apps keep serving (no `Solution.status` join). | confirmed; delivery pile-up needs-live-verification |
| **A3** | Coverage | **Med** | `PolicyRule.solution_id` exists for "solution-shippable rules" but there is no capture/export/install path — a solution using named policy rules exports fine and **409s at every install**. | confirmed-broken |
| **A4** | Coverage | **Med** | Solution-tier file policies are un-authorable and invisible (`list_policies` filters them out); file-access customizations don't travel and reset to admin-only on DR/reinstall. | confirmed |
| **A5** | Coverage | **Med** | Agent knowledge namespaces travel but the corpus/embeddings don't, and it isn't an unmet-need — the agent installs green and answers with an empty KB. | confirmed |
| **B2** | Coverage | **Med** | `POST /forms/{id}/upload` presigned PUT bypasses `signed_put` policy and records no `FileMetadata`. | confirmed |
| **F6** | File policy | **Med** | File surfaces honor only `is_platform_admin`, never `is_provider_org` — a silent, undocumented divergence from the platform-wide "bypass = admin OR provider-org" contract. | confirmed (consistent, by design, undocumented) |
| **M3** | Lifecycle | **Med** | `bifrost tables/configs/forms/agents/... ` have no solution awareness; run inside a bound solution workspace they silently create global `_repo`/org entities. | confirmed-missing |
| **M1** | Lifecycle | **Med** | Deploy job orphaned by an API restart stays `running` (reconciler is startup-only, ≥15min stale); CLI poller waits forever with no stall detection. | confirmed-broken |
| **M2/M4** | Lifecycle | **Med** | CLI can't list/uninstall/see-updates/inspect-setup; UI has no deploy-history/deploy-failure surface — both blind to state the API holds. | confirmed-missing |
| **P4/F4** | File policy | **Med** | `when: None` = unconditional allow-everyone and the contract permits it with no guardrail; combined with P1, one no-`when` global rule = everyone. | needs-live-verification |
| **A6** | Coverage | **Med** | Ref scanner/rewriter miss Python `workflows.execute("path::fn")` — chained workflows omitted from capture preview and not rewritten on v1→v2 rename. | confirmed |
| **B3** | Coverage | Med/Low | App source read/write + compiled-asset serving are entity-gated only, never policy-gated (asymmetry with the files-API workspace rule). | confirmed |
| **S2** | Invariant | Low | MCP `events` tools lack the explicit guard; the `before_flush` backstop still blocks the write but surfaces as a raw **500**, not a clean 409. | confirmed |
| **S3** | Invariant | Low | REST `events` update/delete guard fires after an unscoped load → 409-vs-404 existence oracle. | needs-live-verification |
| **M5/M6/M7** | Lifecycle | Low/Med | No export password strength check; export jobs can hang "pending" if scheduler dies; `install_from_repo` finalizes S3 outside the write lock (narrow race). | confirmed / needs-live-verification |
| **L1/L3/A7/A8** | mixed | Low | Write-lock holder never detects lock loss; git auto-pull always `force=True` (silent downgrade); orphaned auto-created roles; export truncation caps warn only in server logs. | confirmed |

---

## High severity

### B1 — MCP `code_editor` tools are un-policied, non-superuser workspace write
**Coverage / bypass · High · confirmed (hand-verified); end-to-end grant path needs-live-verification**

`api/src/services/mcp_server/tools/code_editor.py` implements `list_content`, `search_content`, `read_content_lines`, `get_content`, `patch_content`, `replace_content`, `delete_content` (`TOOLS`, `code_editor.py:730-737`) directly against `RepoStorage()` and `FileStorageService` (`:155, :217, :284, :701-709`). The module contains **no `FilePolicyService` call and no `is_superuser`/`platform_admin` check** (verified by grep + hand-read).

These register as system tools (`tools/__init__.py:11,32`). MCP tool access is computed from the agents a user can reach via roles (`tool_access.py:44-58`, enforced in `middleware.py`), *not* by superuser status. So any non-superuser with role access to an agent whose `system_tools` include e.g. `replace_content` can rewrite anything under `_repo/` — every workflow, all app source, `.bifrost/` manifests.

Contrast: the REST equivalents are deliberately locked down — the `workspace` file location short-circuits to `ctx.user.is_superuser` and `/api/files/editor*` + `/api/files/search` require `CurrentSuperuser` (`files.py:1525-1969`). `code_editor.py` is an older module that predates that rule and was never brought under it.

**User impact:** granting an agent a code-editing tool becomes an undocumented workspace-admin escalation; file policies are irrelevant to this path. **Live check needed:** grant `replace_content` to an agent, open a non-admin MCP session for a user with that agent, confirm a `_repo/` write lands.

### A1 — Hard-delete orphans solution file bytes in S3; `files_swept` is fictional
**Coverage · High · confirmed (hand-verified)**

`DELETE /api/solutions/{id}` enumerates solution files before the cascade and reports `files_swept=len(file_entries)` (`solutions.py:1126,1138`). But the post-commit S3 sweep only deletes the `_solutions/{id}/` prefix (via `SolutionStorage`, `storage.py:28,36`), the source artifact, and app dists (`solutions.py:1146-1153`). Solution **files** live at a different prefix — `{location}/{install_id}/{path}` (`shared/file_paths.py:97`). Their `FileMetadata` rows cascade away, but **no code path deletes those S3 objects**.

**User impact:** the "irreversible hard-delete" leaves every solution data file — potentially sensitive customer data — orphaned in object storage with no index left to find it, while telling the operator N files were swept. This is both a data-hygiene/compliance issue and a false success report.

### H1 — `solution install` still has the pre-#388 synchronous 30s-timeout bug
**Lifecycle · High · confirmed (code); wall-clock needs-live-verification**

`POST /api/solutions/install` runs the entire deploy — including the pre-commit npm/vite app build (`deploy.py:456-459`) — synchronously inside the HTTP request (`solutions.py:1813-1961`); `install/from-repo` also clones in-request (`solutions.py:1717-1808`). The CLI posts with the **default 30s httpx timeout** and no override (`commands/solution.py:1829-1833`; `client.py:352,380`). `deploy` was explicitly fixed for exactly this by #388 (`timeout=600` + async job + poll, `commands/solution.py:1706-1729`).

**User impact:** installing any zip containing a v2 app (the normal case) from the CLI likely dies with a client-side `ReadTimeout` while the server keeps working — and the operator can't tell whether it landed.

### H2 — Config values have no solution ownership; backup restore collides / clobbers
**Lifecycle · High · confirmed (mechanism); clobber path needs-live-verification**

`Config` has no `solution_id` (org + integration + key only; `orm/config.py`, unique `ix_configs_integration_org_key`). This is deliberate for *instance ownership* of secret values (`guard.py:9-12`), but the full-backup import collision check matches **any** Config row in the org's NULL-integration partition by key (`zip_install.py:757-774`) — it can't tell "this solution's retained value" from "an unrelated install's / hand-set value." So a restore (a) 409s if any org config shares a key with the blob, even for an unrelated install; (b) the documented remedy `--replace-secrets` (`commands/solution.py:1848-1853`) then overwrites the other owner's live value. Two installed solutions declaring the same key silently share one row.

**User impact:** restore hits an unexplained collision wall, or corrupts a sibling solution's configuration.

### P1 — One over-broad global file policy = platform-wide read; the org gate is intentionally open
**File policy · High · needs-live-verification**

`_principal_matches_org` returns `True` unconditionally when `organization_id is None` (`file_policy_service.py:434-435`). For any global-tier file (`scope="global"` → org resolves to `None`), the org gate is a no-op and access reduces entirely to what the global policy rows say. Reachable from non-superuser read/list/exists/signed_get endpoints (a solution with `global_repo_access` appends a `global` read tier). The default seed is admin-only (`admin_bypass`), so out-of-the-box this is safe — but there is **zero defense-in-depth**: a single global policy with a broad predicate (or no `when`, see P4) silently grants every authenticated tenant. Write to `global` remains superuser-pinned (confirmed clean).

**User impact:** the global namespace is one mis-scoped policy row away from a cross-tenant read, and the gate that "should" catch it is deliberately open.

---

## Medium severity

- **S1 — policy-rule rename Core-writes into managed rows.** `policy_rule_service._cascade_rename` guards the *rule* itself (`:123`) but then rewrites every consumer's `$ref` via SQLAlchemy Core `sa_update(FilePolicy…)` / `sa_update(Table…)` (`policy_rule_service.py:197-210`), with no `solution_id` filter on the targets. Core statements bypass the `before_flush` backstop by design (that's how deploy writes), so a user-triggered rename silently mutates solution-managed rows outside deploy — invariant violation + drift the next redeploy reverts. **confirmed-broken.**
- **F5 — dead `check_allowed` = unaudited denials.** `check_allowed`/`FilePolicyDenied` have no production caller (only the def + a test, `file_policy_service.py:377-406`). Beyond the no-dead-code rule, `check_allowed` was the *only* path emitting a `policy.deny` audit event; the live path (`is_allowed` + router 403) emits none. **confirmed-broken.**
- **A2 — uninstall doesn't quiesce.** Uninstall flips `status="inactive"` only (`solutions.py:986-991`). The cron scheduler selects on `is_active`/`enabled` with no `Solution.status` join (`cron_scheduler.py:52-72`) → schedules keep firing; apps keep serving (no status gate in `app_code_files.py`). Only workflow/agent *execution* is gated downstream. **confirmed.**
- **A3 — PolicyRule un-shippable.** `PolicyRule.solution_id` exists ("solution-shippable rules", `orm/policy_rule.py:21`) but capture/export/`SolutionBundle` have no policy-rules field and `ManifestPolicyRule.to_orm_values` raises `NotImplementedError` for non-GIT_SYNC (`bifrost/manifest.py:807-809`). Deploy *validates* table-policy `$ref`s against solution-scoped rules (`deploy.py:912-935`), so such a solution exports fine and **409s at every install**. **confirmed-broken.**
- **A4 — solution-tier file policies invisible/un-authorable.** Deploy seeds one `admin_bypass` root per location (`file_locations.py:85-103`); the bundle carries only location name strings. `PUT /api/files/policies` has no solution param, and `list_policies` filters `solution_id.is_(None)` (`file_policy_service.py:210`) so seeded rows never appear. File-access rules for solution data are per-environment hand-config that resets to admin-only on reinstall. **confirmed.**
- **A5 — knowledge corpus doesn't travel.** Agents export `knowledge_sources` as namespace strings; `KnowledgeStore` has no `solution_id` and its content/embeddings aren't exported, nor is a missing corpus an unmet need. Agent installs green and RAG returns nothing. **confirmed.**
- **B2 — form upload bypasses signed_put + metadata.** `POST /forms/{id}/upload` mints a presigned PUT with no `FilePolicyService` check and no metadata recording (`forms.py:1216-1350`), unlike the generic path (`files.py:854-905`). Blast radius bounded by server-generated UUID path. **confirmed.**
- **F6 — files honor only `is_platform_admin`.** `_principal_matches_org` and the websocket path check only `is_platform_admin` (`file_policy_service.py:436`, `websocket.py:473`), dropping the `is_provider_org` arm that the platform-wide bypass contract includes (scope_resolver / repositories README). Consistent across all file surfaces (so not an asymmetry bug) but an undocumented divergence — a provider-org non-admin who can cross-org everywhere else is a plain org user for files. **confirmed; needs a documented decision, not necessarily a code fix.**
- **M3 — entity commands aren't solution-aware.** #412 gates the file-sync family in solution workspaces, but `bifrost tables/configs/forms/agents/workflows/apps` have no `--solution`/binding check and silently create global `_repo`/org entities when run in a bound workspace. **confirmed-missing.**
- **M1 — orphaned deploy jobs wedge the poller.** Deploys run as in-process `BackgroundTasks`; the reconciler runs only at startup and only fails jobs ≥15min stale (`main.py:146-159`, `solutions.py:129-153`). A restart-orphaned job gets a fresh `updated_at` and isn't caught; the CLI poll loop has no stall/overall timeout (`commands/solution.py:1521-1558`). **confirmed-broken.**
- **M2/M4 — CLI and UI blind spots.** No `bifrost solution list`/uninstall/install-from-repo/setup-status, so `update_available_version` is CLI-invisible; the UI never calls `/deploy` or `/deploy-jobs/*`, so a failed/last deploy is invisible in the UI. **confirmed-missing.**
- **P4/F4 — `when: None` allow-everyone with no guardrail.** `evaluate_file_action` treats `when is None` as unconditional allow (`file_policies.py:91-92`) and the contract makes `when` optional with no validation (`policies.py:336`). Combined with P1, a no-`when` global rule grants every authenticated user. **needs-live-verification.**
- **A6 — Python workflow chaining invisible to ref tooling.** `ref_scanner` covers `tables.get`/`config.get`/`useWorkflow*`/`integrations.get` but not Python `workflows.execute("path::fn")` (SDK at `bifrost/workflows.py:69-77`). Chained workflows are omitted from the capture/export preview and not rewritten on v1→v2 rename. **confirmed.**
- **B3 — app source/dist endpoints entity-gated only.** `app_code_files.py` list/read/write/delete + asset serving gate on app access, never `is_allowed`; a non-superuser with app access reads/writes under `_repo/apps/…`. Plausibly intended, but undocumented and asymmetric with the files-API workspace rule. **confirmed.**

---

## Low severity

- **S2 — MCP `events` tools return 500 not 409.** No explicit guard; the `before_flush` backstop blocks the write but raises `SolutionManagedWriteError` → opaque 500. Invariant holds, UX papercut. **confirmed.**
- **S3 — events router existence oracle.** Update/delete load via unscoped `select` then guard → managed row 409 vs invisible row 404. **needs-live-verification** (endpoints appear admin-gated).
- **M5 — no export password strength check.** 1-char passwords accepted; scrypt is the only mitigation (`export_jobs.py:190-203`, `secrets_blob.py:50-83`). **confirmed.**
- **M6 — export jobs can hang "pending".** `expires_at` set only on completion; no stuck-pending auto-fail (`schedulers/solution_export_jobs.py:215-217`). **needs-live-verification.**
- **M7 — `install_from_repo` finalizes S3 outside the write lock.** Install committed + `git_connected=True` before `finalize_s3()` at `solutions.py:1808` with no lock; auto-pull scheduler can interleave. Narrow window. **needs-live-verification.**
- **L1 — write-lock holder never detects lock loss** (`write_lock.py:97-106`); **L3 — git auto-pull always `force=True`**, silently bypassing the downgrade gate (`git_sync.py:192`); **A7 — orphaned auto-created roles** + deletion summary undercounts; **A8 — export truncation caps** (`TABLE_ROW_CAP=50_000`, `FILE_CAP=1_000`) warn only in server logs, no marker in the DR artifact. **confirmed.**

---

## Confirmed safe / no regression

Verified and found sound — scoping the risk:

**Solutions invariant (the core promise held everywhere it was traced):**
- `install_solution_write_guard()` is installed **unconditionally** in `get_session_factory()` (`core/database.py:135-136`); every API/worker/scheduler/consumer session inherits it.
- **No create DTO exposes `solution_id`** (Forms/Agents/Tables/Applications/Events/Claims) — so the backstop's not-checking-`session.new` gap is unreachable; a forged managed row can't be created.
- Explicit 409 guards correctly present on forms, agents, applications, tables, workflows, roles junction assign/unassign, and the policy-rule *itself*; MCP apps/forms/agents/tables guard before their Core child-deletes.
- Deploy/redeploy/uninstall/capture/git-sync are ownership- and `solution_id`-scoped; uninstall is a status flip (no cross-solution/`_repo` clobber); capture refuses to re-stamp an owned row; per-install Redis write-lock serializes manual deploy + git-sync with fencing tokens and release-on-exception; `_solutions/{id}/` and `_apps/{id}/` prefixes are unreachable from user-declared file locations.
- Config values and table row Documents are deliberately instance-owned/editable (criterion 7); role-delete cascade is guarded and names the owning installs.

**File policy correctness:**
- Policy **management** endpoints are superuser-only — org users cannot self-grant (`files.py:700,748`).
- Path normalization has **no `..`/case/backslash check-vs-use mismatch** — policy check and S3 key derive from the identical `path`, and `resolve_s3_key` independently rejects `..`/leading-`/` (`file_paths.py:65-69`).
- `$ref` resolution is pinned to the **policy row's** org cascade, not the caller's — no cross-org rule leak (`file_policy_service.py:340`).
- Signed-URL action map is complete and check-path == sign-path (`files.py:390-394,851-858`).
- Malformed policy docs and unresolvable `$ref`s both **fail closed** (deny).
- Solution-tier metadata/policy keying uses partial-unique indexes + `solution_id IS NULL` filters — no `MultipleResultsFound` 500s.
- Write-side org pinning: non-superusers cannot address another org's or the global write tree.

**Lifecycle claims verified true:**
- Deploy is transactional pre-commit (all gates before any write; app builds compile pre-commit; S3 finalize post-commit, idempotent, retried, honest "re-run heals" messaging).
- Shareable export **scrub is real**: no config values, no table data, no integration credentials, no webhook instance state; secrets travel only in the password-encrypted `.bifrost/secrets.enc`, excluded from the deploy zip.
- Wrong-password import is atomic (decrypt precedes lock and any write).
- One-install-per-`(slug, scope)` enforced by partial unique indexes + race-safe flush catch; versioning stored/compared/bumped coherently; #427's `solution start` fixes are internally consistent.

**File-policy enforcement paths that DO enforce:** REST files API (all actions), SDK `sdk.files.*` (HTTP wrappers inherit enforcement), websocket file watch, MCP file-*policy* tools (thin wrappers), file search + structural explorer (superuser-only, so no policy-denied-content leak).

---

## Suggested fix sequencing

Smallest-blast-radius high-severity first; data-loss and un-gated-access before polish.

1. **B1** (un-policied MCP `code_editor`) — highest blast radius, isolated to one module. Add a superuser/policy gate mirroring the REST workspace rule, or remove the tools from non-admin agent grants. *Confirm the live grant path first.*
2. **A1** (hard-delete S3 orphan) — data-hygiene/compliance + false report. Sweep the enumerated `file_entries` s3_keys in the same post-commit loop; the enumeration already exists.
3. **H1** (install timeout) — mechanical: mirror #388's `timeout=600` + async-job treatment onto the install path.
4. **S1** (policy-rule rename Core-write into managed rows) — add a `solution_id IS NULL` filter (or explicit managed-guard) on the `_cascade_rename` targets. Small, contained.
5. **F5** (unaudited denials) — either wire `check_allowed` into the router path or emit the `policy.deny` audit from `is_allowed`'s caller; delete the dead helper either way.
6. **H2 / A2 / A3 / A4 / A5** — the "ships green but incomplete/uninstalls dirty" cluster. Each is a design decision as much as a bug (config ownership, uninstall quiescence, un-shippable policy rules, non-travelling policies/knowledge); worth a batched design pass rather than point fixes.
7. **P1 / P4** — add a validation warning (CLI + UI) when authoring a global-tier file policy with no `when` or a broad predicate; the runtime default is safe, this is defense-in-depth.
8. Remaining Med/Low (M1–M7, F6, A6–A8, B2–B3, S2–S3, L1/L3) — UX/robustness polish and documented-divergence decisions.

## Caveats

Findings tagged **needs-live-verification** (B1 end-to-end grant, H1/H2 wall-clock/clobber, P1/P4 global-policy exploitation, A2 delivery pile-up, M6/M7/S3) are code-traced but not exercised on a running stack. B1 and A1 were re-verified by hand against source. This audit did not scan `client/` for frontend-only issues beyond confirming which Solutions API surfaces the UI does/doesn't call.
