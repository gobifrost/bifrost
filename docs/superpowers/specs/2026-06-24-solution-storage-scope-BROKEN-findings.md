# Solution Storage Scope — Architecture Defect Findings (2026-06-24)

**Status:** BLOCKER. The solution-scoped-files feature as built does not work the way a real solution author would use it. Found during pre-drive review with Jack.

## Jack's intended model (the correct one)
A solution is a **SCOPE**, exactly like tables/configs — NOT a location. Any normal location + a solution context resolves to `{location}/{solution_id}/{path}` (e.g. `finance/{solution_id}/invoices.pdf`). When a solution does NOT declare/specify storage, the default should be **global if allowed**, never `_repo/` and never silently the org.

## What was actually built (the defect)
1. **`resolve_s3_key` (api/shared/file_paths.py) is CORRECT** — generic `{location}/{scope}/{path}`. It will happily produce `finance/{solution_id}/...`. The storage primitive is scope-agnostic. NOT the problem.

2. **`_resolve_effective_scope` (api/src/routers/files.py:275) is WRONG** — it only uses `ctx.solution_id` as the scope when `location == "solutions"` (a hardcoded string). For ANY other location it discards `ctx.solution_id` and falls back to `_file_org_id` (the ORG). So solution-scoping is artificially restricted to one literal location name `"solutions"`, producing a single flat `solutions/{install}/` bucket instead of `{any_location}/{install}/`.

3. **The SDK has NO solution-scope path (api/bifrost/files.py + _context.py::resolve_scope).** `read/write/list` take `(location, scope)`; `resolve_scope(None)` defaults to the **org** (provider-orgs may override to another org). There is NO way for a workflow to express "this is my solution's storage." A solution workflow calling `sdk.files.write("x.pdf", location="finance")` → `finance/{org}/...` (org-scoped, LEAKED + collides with other solutions in the org). Default `location="workspace"` → `_repo/...` (cross-org shared codebase — worst case).

4. **No storage DECLARATION in the manifest.** `ManifestSolutionFile` is an export INDEX of existing files, not a declaration. The Solution model/manifest has no "this solution uses location X, scoped Y" concept. So there is no place to say "invoices is solution-scoped" or "this defaults to global."

## Why the tests passed anyway (the trap)
Every solution-file test drives `"location": "solutions"` (the one hardcoded path). The capstone passes because it uses that literal location. NO test — and critically NO SDK code path — exercises a solution writing to a custom location the way a real author would. The feature is green but unreachable from a workflow/SDK.

## Blast radius — what survives vs what's wrong
- **SURVIVES (scope-agnostic, keys on `solution_id` not the literal location):** the `solution_id` DB columns (Task 14), the policy cascade own-solution→org→global (Task 16), Core-write service (Task 17), uninstall/reactivate/hard-delete lifecycle (L1-L10), manifest round-trip keyed on solution_id (Task 22), the status gate. These don't care whether the location is "solutions" or "finance".
- **WRONG / needs redesign:** `_resolve_effective_scope` (the location=="solutions" hardcode), the SDK `resolve_scope`/files methods (no solution concept + wrong default), the default-scope policy (workspace/_repo instead of global-if-allowed), and the MISSING manifest storage-declaration concept. Tests + capstone that hardcode `location="solutions"` need re-pointing at the scope model.
- **OPEN DESIGN QUESTIONS:** (a) does a solution context auto-scope ALL locations, or only declared ones? (b) how does a solution reach ORG/global shared files (escape hatch)? (c) the "default to global if allowed when undeclared" rule — what does "allowed" mean (bypass flags)? (d) declaration mechanism: manifest section listing location→scope. (e) reactivate/orphan implications (the lifecycle keys on solution_id, so scope-as-`{loc}/{solution_id}` reattaches the same way — verify).

## Required next step
A real design pass (brainstorm → revised spec → plan) for the scope model BEFORE driving. This is not a patch — it changes the core resolver, the SDK surface, the default-scope policy, and adds a manifest declaration. The lifecycle/policy/manifest plumbing mostly stands; the *scope resolution + SDK reachability + declaration* layer is the rebuild.
