# Solutions + File Policies — Audit Fixes

## Context

An adversarial four-lens QA audit (findings committed at `docs/plans/2026-07-02-solutions-file-policies-audit.md`) found the Solutions feature and file-policy system fundamentally sound — the read-only guard holds on every surface, file policies fail closed, no cross-tenant leaks. After the user adjudicated the findings, six real items remain (dropped: P1 global-policy warning, B2 form-upload; deferred: H2 config ownership). This plan implements them, sequenced smallest/most-contained first.

Two through-lines:
- **`is_provider_org` never reached the principal/policy layer (F6).** The workflow engine honors provider-org members (platform-org staff who hop into client orgs via the portal) through `resolve_effective_scope(is_provider_org=...)`, computed from a DB lookup on `Organization.is_provider`. But `UserPrincipal` has no such field, so file/table policies can't reference it and file scope-hopping (`resolve_target_org`, which checks `is_superuser` only) silently pins those members to their own org. The client portal in `../bifrost-workspace` relies on this working — it's broken for files/tables.
- **Solution-owned file policies don't round-trip (A3, user's release blocker).** `ManifestFilePolicy` already exists and supports `Destination.INSTALL`; the ORM, cascade resolver, and deploy seed are done. Only the capture→bundle→export→zip-parse→reconcile wiring is missing.

Governing rule the user articulated, to be documented: **"global-deny applies only to entity types that CAN be solution-scoped."** For an entity with no solution scoping yet (knowledge today), global access is the intended bridge, not a bug.

---

## Fixes (sequenced smallest-first)

### F5 — Prune dead `check_allowed` + restore deny auditing
`check_allowed`/`FilePolicyDenied` (`file_policy_service.py:29-30, 377-406`) have zero production callers (only a unit test). Critically, `check_allowed` was the **only** emitter of the `policy.deny` audit event; the live path (`is_allowed` → 403 in `_require_file_policy`, `files.py:405-440`) emits nothing.
- Delete `check_allowed` + `FilePolicyDenied`; remove any imports; delete/retarget the unit test.
- Add a `policy.deny` audit emit in `_require_file_policy` before raising 403, mirroring the table pattern at `tables.py:171-188` (`emit_audit` with action/location/path). Preserves auditing on the real path.

### A1 — Hard-delete sweeps solution file bytes
`delete_solution` (`solutions.py:1039-1159`) enumerates `file_entries` (each a real `s3_key`, `solution_files.py:43-71`) for the `files_swept` count but the S3 sweep (`:1147-1153`) only clears `_solutions/{id}/` — declared-location files at `{location}/{install_id}/{path}` are orphaned.
- In the post-commit sweep, loop the enumerated entries: `FileStorageService(ctx.db).delete_raw_from_s3(entry.s3_key)` for each non-null key (idempotent, `service.py:382-391`).
- The docstring at `:1055` wrongly claims `solutions/{id}/` covers it — fix the comment.
- Extend the E2E arc (`test_solution_files_e2e.py:620-693`) to assert the S3 objects are gone, not just FileMetadata cascade.

### S1 — Remove server-side policy-rule rename cascade
Intent: rename-and-rewrite belongs in the **local CLI** (surfacing "missing refs" pre-publish), not server-side rewriting live rows — least of all solution-managed ones via Core statements that bypass both guard layers.
- Delete `_cascade_rename` (`policy_rule_service.py:177-211`) and its call at `:132`; keep the rule-rename itself (`row.name = renamed`, `:133`) and the audit (`:145`, still reports `usages.total`).
- Delete `test_rename_cascades_via_core_update_under_guard` (`test_policy_rule_service.py:41-66`).
- Missing refs already fail closed at read (`resolve_policy_refs` → `PolicyRuleNotFound`, deny) and at write (`set_file_policy` validates refs, 422). No new safety net needed. **No local-CLI ref-checker exists today** — note as a possible follow-up, not in scope here.

### F6 — `is_provider_org` on the principal (fixes files AND tables + enables `{user: is_provider_org}`)
Mirror the existing `is_external` mint/parse path exactly.
- **Add field**: `is_provider_org: bool = False` on `UserPrincipal` (`core/principal.py`).
- **Helper**: `resolve_provider_org_claim(db, user)` (in `shared/external_access.py`) — one indexed `SELECT Organization.is_provider WHERE id = user.organization_id`; `False` for system/no-org.
- **Mint** the claim at every token site that already computes `is_external`: `auth.py:630,778,941,1688`, `oauth_sso.py:414`, `mcp_server/auth.py:430`. Embed/engine tokens → `False` (portals/system, no cross-org).
- **Parse** onto the principal at both construction sites: `core/auth.py:172,513` (`payload.get("is_provider_org", False)`).
- **Flip three gates** to `is_superuser OR is_provider_org`:
  - `resolve_target_org` (`core/org_filter.py:128-176`) — the file/table/knowledge/claims scope-hop resolver.
  - `_principal_matches_org` (`file_policy_service.py:429-439`).
  - `_file_org_and_scope` (`websocket.py:460-482`).
- **Type clarity**: add `is_provider_org: bool` to the `_PolicyUser`-adjacent principal typing if pyright complains.
- Tests: extend `test_scope_resolver.py`, `test_file_policy_service.py` (`_principal_matches_org` with a provider-org user), `test_cli_get_org_id.py` (scope-hop), and add `is_provider_org` to the `create_test_jwt` fixture.
- **Note**: existing tokens lack the claim → `get(...,False)` degrades safely; a re-login refreshes it (same as `is_external` rollout).
- **UI policy-builder docs (do in this PR)**: both reference slideouts document only `is_platform_admin` in their term glossary — `client/src/components/files/FilePolicyReferencePanel.tsx:30` and `client/src/components/tables/PolicyReferencePanel.tsx:29`. Add `is_provider_org` AND the already-supported-but-undocumented `is_external` (and audit whether `is_verified`/`organization_id` should be listed too — they resolve today via `{user: ...}` but aren't in the glossary). Both panels serve the same shared evaluator, so keep the two glossaries in sync. Without this, admins can't discover the predicates.

### H1 — `solution install` becomes an async job (full conversion — user-chosen)
Both `install` (`solutions.py:1813-1962`) and `install/from-repo` (`:1689-1810`) run the whole build (npm/vite) synchronously; the CLI waits on the default **30s** timeout (`client.py`; deploy uses `timeout=600`).
- Reuse the deploy-job machinery: `SolutionDeployJob` ORM + `GET /deploy-jobs/{job_id}` status endpoint + the startup orphan reconciler (`solutions.py:129-153`) all apply as-is.
- Add `_run_install_job()` background task wrapping `install_zip_path()` / `deploy_from_workspace()`, mirroring `_run_deploy_job()` (`:1162-1267`) — runs under the per-install write lock, records phases + result.
- **Keep validation synchronous** (fail-fast, before enqueue): config-values JSON parse, **password decrypt-check**, repo clone (from-repo). Only the build/deploy/finalize moves to the job.
- Endpoints return **202 + job_id** (`SolutionDeployEnqueued`). This changes `POST /install` from 200→202 — update both consumers:
  - CLI (`commands/solution.py` install cmd): submit-and-poll via `_poll_deploy_job` (`:1540-1577`); bump the submit call's timeout for the upload.
  - UI (`client/src/services/solutions.ts` + install flow): fire → job_id → poll to terminal, surfacing phase/error (also closes the audit's M4 "UI has no deploy-failure surface" for the install path).
- Also fixes the from-repo write-lock race (M7): the async job holds the lock across build+finalize.

### A3 — Solution file policies deploy (release blocker; ~80% pre-built)
`ManifestFilePolicy` (`manifest.py:1119-1181`) already has `solution_id` and supports `Destination.INSTALL`; ORM (`file_metadata.py:82-142`), solution-tier cascade (`load_policy`/`is_allowed`), and deploy seed (`file_locations.py:85-103`) are done. Wire the remaining pipeline, mirroring **tables** (`_upsert_tables` `deploy.py:840-977` + `_reconcile_one` `:1786-1808`) and **file locations**:
1. **Capture**: add `file_policies: list[UUID]` to `SolutionCaptureSelectors` (`capture.py:53-61`) + a `_file_policy_entries()` serializer (query `FilePolicy WHERE solution_id==sid`, `ManifestFilePolicy.from_row(...).view(INSTALL)`).
2. **Bundle**: add `file_policies: list[dict] = field(default_factory=list)` to `SolutionBundle` (`deploy.py:281`).
3. **Export**: write `.bifrost/file-policies.yaml` (`export.py`, after the `files.yaml` block; `MANIFEST_FILES` already maps `file_policies`).
4. **Zip parse**: add `_collect_file_policies(workspace)` (CLI collector, alongside `_collect_file_locations`) and wire into `_parse_workspace` / `_build_bundle` (`zip_install.py:196-234, 550`).
5. **Deploy reconcile**: add `_upsert_file_policies()` — validate policy docs (`FilePolicies.model_validate`), resolve `$ref`s via `resolve_policy_refs` with `solution_id`, **Core upsert** (never ORM, per the guard), keyed on `(solution_id, location, path)`; then `_reconcile_one`-style stale deletion scoped to `solution_id==sid`. Must **coexist with the seeded root `admin_bypass`** from `reconcile_solution_file_locations` — deploy file-policies after locations, and treat the seed as an upsert target (don't double-insert path="").
6. **CRUD (INCLUDED — closes A4)**: make `FilePolicyService.upsert/list/delete` and the REST/MCP endpoints honor the `solution` param they already accept but ignore (`mcp_server/tools/files.py` passes it; `files.py:700-790` drops it). Lets admins see/edit solution-tier rows (`list_policies` currently filters `solution_id.is_(None)` — add a solution-scoped variant). Guard: solution-tier writes are deploy-owned, so admin CRUD here must be consistent with the read-only guard — confirm whether admin edits to solution-tier policies are allowed (like table row data, instance-owned) or blocked (like the managed entity itself); resolve during implementation against `guard.py` semantics.
7. Round-trip unit test in `test_manifest.py` + E2E in `test_git_sync_local.py`-style deploy test; confirm a solution referencing a solution-scoped policy installs (no 409).

---

## Documentation task
- Add to `api/src/repositories/README.md` (or the file-policies doc): knowledge is not solution-scoped today → a solution needing knowledge must use **global access**; not a bug, working as designed.
- Codify the rule: **"global-deny applies only to entity types that CAN be solution-scoped"** — the signal for when to flip an entity to deny-global as Solutions expands.

---

## Sequencing rationale & execution
**One PR per fix, in order: F5 → A1 → S1 → F6 → H1 → A3.** F5/A1/S1 are small, isolated, independently shippable. F6 is medium and self-contained (mirrors the `is_external` path). H1 (UI/CLI async) and A3 (deploy pipeline + CRUD) are the large ones — done last, with a check-in before each. Each lands as its own PR with tests under the "ship inert/complete, never red" rule; F6 and H1 flip shared surfaces, so a full-suite run precedes their merge.

## Verification
- **Unit/type/lint**: `./test.sh quality api`; `./test.sh tests/unit/test_manifest.py test_scope_resolver.py test_file_policy_service.py test_policy_rule_service.py`; client `npm run tsc && npm run lint`.
- **F6 live**: boot debug stack; as a non-superuser member of a `provider`-flagged org, hit a file endpoint with `scope=<other-org>` and confirm read/write now resolve (previously denied/pinned); confirm a plain org user is still pinned. Exercise the `../bifrost-workspace` portal scope-switch against files.
- **A1 live**: install a solution with declared-location files, hard-delete it, confirm the `{location}/{install_id}/…` S3 objects are gone (not just the DB rows).
- **H1 live**: `bifrost solution install <big-app-zip>` from the CLI (previously ReadTimeout at 30s) now returns a job id and polls to success; a mid-install API restart marks the job failed via the reconciler rather than hanging.
- **A3 live**: author a solution with a customized file policy, `export` → fresh-env `install`, confirm the policy row deploys solution-scoped and governs access; redeploy removing it confirms stale deletion; uninstall/hard-delete cleans it up.
- **Full suite** before any merge: `./test.sh all` + `./test.sh client unit` (F6 flips a shared gate — watch sibling file/table/websocket tests).

## Decisions locked
- **H1**: full async-job conversion (not the timeout-bump shortcut).
- **A3**: deploy **+** CRUD together — one PR closes A3 and A4.
- **Execution**: one PR per fix, sequential F5→A1→S1→F6→H1→A3, check-in before F6/H1/A3.
