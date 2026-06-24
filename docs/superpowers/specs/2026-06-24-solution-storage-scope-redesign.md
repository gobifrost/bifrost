# Solution Storage Scope — Corrected Model (Design)

**Status:** Approved (decided with Jack 2026-06-24; no open questions). Supersedes the `location=="solutions"` hardcode.
**Supersedes/corrects:** `2026-06-24-solution-storage-scope-BROKEN-findings.md`.
**Branch:** `codex/files-sdk-policies`.

## Problem
The solution-scoped-files feature only works through the literal `location=="solutions"` string, which no real workflow author would use, and the files SDK has no way to signal solution context at all. A solution writing to a normal location (`finance`) lands org-scoped (leaked) or, by default (`workspace`), in the cross-org `_repo/`. Tables have an analogous problem (implicit auto-create + ungated data cascade). The model below fixes both, unified.

## The model (final, both tables AND files)

1. **Solutions DECLARE their tables and file locations in the manifest. No implicit/auto-create inside a solution.** Declaration is the source of truth for what belongs to the solution, what gets exported, and what reattaches on reactivate. (Non-solution `_repo/` workspace KEEPS today's implicit auto-create-on-write — this rule is **solutions-only**.)

2. **Scope = `solution_id`, applied to ANY declared name/location.** A declared file location `finance` under a solution resolves to `finance/{solution_id}/{path}` (the S3 primitive `resolve_s3_key` already produces `{location}/{scope}/{path}` — only the router's `_resolve_effective_scope` hardcode blocks it). A declared table `invoices` keys by `solution_id`. The literal `location=="solutions"` special-case is REMOVED; solution context (`?solution=<install_id>` / `ctx.solution_id`) drives the scope for any location.

3. **`global_repo_access` gates the DATA cascade** (today it only gates code/module imports):
   - **ON** → cascade as normal: own-solution (`solution_id`) → org → global.
   - **OFF** → SEALED: resolution stops at the solution's own scope. No org/global visibility for tables or files. `global_repo_access` becomes a real data-isolation boundary.

4. **Undeclared reference inside a solution → not-found** (resolve empty / 404, NOT a hard error). Combined with #1, a solution sees only what it declared, plus the global cascade when access is on. Auto-create never fires inside a solution.

5. **Solution policies (file + table) apply ONLY to the solution scope — never cascade to global.** A solution's policy governs `{solution_id}` and nothing else; it is never consulted for org/global resolution.

## SDK (the missing reachability layer)
- **Tables already do it:** `bifrost/tables.py::_scope_query()` reads `ctx.solution_id` from the ExecutionContext and appends `?solution=<install_id>`. (`bifrost/_execution_context.py:113` carries `solution_id`.)
- **Files MUST do the same:** `bifrost/files.py` `read/write/list` append `?solution=<install_id>` from the same ExecutionContext. This is the single change that makes solution-scoped files reachable from a workflow at all.
- **Web SDK:** the same — file requests from a solution-mounted app carry the install context (the app already sends `X-Bifrost-App`; confirm files honor it, mirroring the worker `?solution=` path).

## What changes (concentrated, not a rebuild)
| Layer | Change |
|-------|--------|
| `bifrost/files.py` (+ web SDK) | Append `?solution=<install_id>` from ExecutionContext, mirroring `tables.py::_scope_query` |
| `routers/files.py::_resolve_effective_scope` | Drop `location=="solutions"` hardcode; when `ctx.solution_id` present, scope = `solution_id` for ANY location |
| `routers/files.py` read/list/metadata | Own-solution → org → global cascade for metadata/content, gated by `global_repo_access` (the policy cascade `file_policy_service.load_policy` ALREADY does this correctly — mirror it for metadata) |
| `routers/tables.py` + table resolution | Gate the org→global data cascade on `global_repo_access` (today ungated); no implicit create inside a solution (declared-only) |
| `global_repo_access` usage | Extend from code-only to ALSO gate data (tables + files) cascade |
| Manifest | Add a declared **file-locations** set (mirror `Manifest.tables: dict[str, ManifestTable]`) — the solution's owned locations; tables already declared via `Manifest.tables`. Enforce declared-only at deploy + runtime for solutions. |
| Deploy/install | Register the declared locations; runtime resolution checks declaration for solution context |
| Tests + capstone | Re-point off `location="solutions"` onto the real scope model (declared location `finance` → `finance/{solution_id}/`); add SDK-reachability tests (a solution WORKFLOW writes + reads its file) |

## What survives (scope-agnostic — keys on `solution_id`, not the literal location)
The DB columns (`solution_id` on FileMetadata/FilePolicy/Table/etc.), the file-policy cascade (Task 16 — already own-solution→org→global, just needs the `global_repo_access` gate added), Core writes, the entire inactive-lifecycle (L1-L10), the manifest round-trip keyed on solution_id, the status gate. These do not care whether the location is `"solutions"` or `"finance"`.

## Testing
- **SDK reachability (the gap that hid the defect):** a solution WORKFLOW (real `?solution=` path, not a raw `location="solutions"` REST call) writes a file to a declared location, reads it back, sees `{location}/{solution_id}/...`; a second solution can't see it; org can't see it when sealed.
- **`global_repo_access` gate:** sealed solution (access OFF) → undeclared/org/global table+file = not-found; access ON → cascades to global.
- **Declared-only:** a solution writing to an UNDECLARED location/table → not-found / refused (no auto-create); a `_repo/` workspace write still auto-creates (unchanged).
- **Policy-solution-only:** a solution file/table policy governs only `{solution_id}`; never consulted for org/global.
- **Lifecycle still holds:** uninstall freezes the declared solution-scoped files in place; reactivate reattaches; hard-delete cascades; export carries the declared locations + their files.
- Re-point the capstone + all `location="solutions"` tests onto the scope model.

## Note
This corrects tables too (no implicit create in solutions; gated data cascade) — so it touches the existing table surface, not only the new files work. The lifecycle/policy/manifest plumbing mostly stands; the scope-resolution + SDK-reachability + declaration + global-gate layer is the work.
