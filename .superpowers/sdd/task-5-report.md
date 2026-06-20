# Task 5 Report: Workflow Serialization Unification (Slice 4)

## Phase A: Parity Tests — Both Green

Both parity tests passed before AND after Phase B swaps:
- `test_workflow_git_sync_parity` — PASSED
- `test_workflow_install_parity` — PASSED

## Phase B: Call Site Swaps

### 1. `manifest_generator.py` serialize_workflow
**Before:** 15-line hand-built `ManifestWorkflow(...)` constructor call.
**After:** `return ManifestWorkflow.from_row(wf, roles=roles)` — one line.

### 2. `capture.py` _workflow_entries
**Before:** Hand-built `_drop_none({...})` dict per workflow row.
**After:** `ManifestWorkflow.from_row(w, roles=role_ids).view(Destination.INSTALL, extras={"roles": role_ids, "role_names": role_names})`. Role IDs + names computation kept as-is.

### 3. `manifest_import.py` _resolve_workflow
**Before:** 10-field hand-built `wf_values` dict + 3 conditional field additions.
**After:** `direct = mwf.to_orm_values(Destination.GIT_SYNC).direct` + override `name` with `manifest_name` and convert `organization_id` to UUID. Natural-key upsert, id-realign, SyncRoles orchestration kept unchanged.

### 4. `deploy.py` _upsert_workflows
**Before:** 13-field hand-built `values` dict + conditional `access_level`.
**After:** `ManifestWorkflow(**mwf).to_orm_values(Destination.INSTALL).direct` + stamp `organization_id`/`solution_id` from solution. Guard/conflict check, Upsert, _sync_entity_roles orchestration kept unchanged.

## Install View Key Set (verified against capture._workflow_entries)

Allowlist (14 keys):
```
id, name, function_name, path, type, description,
endpoint_enabled, public_endpoint, timeout_seconds, category,
tags (always [], never dropped), access_level, roles, role_names
```

`organization_id` is ABSENT (scope-inherited from install).

## Tests Green

- Phase A parity tests: 2/2 green
- All 25 roundtrip tests green (13 repo + 12 solution, including both Workflow solution tests)

## Quality Checks

- `pyright`: 0 errors, 0 warnings
- `ruff check`: All checks passed

## Files Changed

- `api/bifrost/manifest.py` — ManifestWorkflow: added EntityCodec mixin, from_row, _install_view, to_orm_values
- `api/src/services/manifest_generator.py` — serialize_workflow: 15 lines → 1 line
- `api/src/services/manifest_import.py` — _resolve_workflow: sourced from to_orm_values(GIT_SYNC).direct
- `api/src/services/solutions/capture.py` — _workflow_entries: sourced from from_row + view(INSTALL)
- `api/src/services/solutions/deploy.py` — _upsert_workflows: sourced from ManifestWorkflow(**mwf).to_orm_values(INSTALL).direct
- `api/tests/unit/test_manifest_codec.py` — added test_workflow_git_sync_parity + test_workflow_install_parity

## Self-Review

- The `is not None` guard for `timeout_seconds` (vs `or 1800`) is correctly implemented — `0` meaning "no timeout" is preserved.
- `tags` is forced to `[]` in `_install_view` (never dropped), matching capture's `w.tags or []`.
- `organization_id` absent from install view (scope-inherited), present in git_sync view (as string).
- `to_orm_values(GIT_SYNC)` keeps `description`/`tool_description` absent when None (resolver only sets them when manifest explicitly provides them).
- `to_orm_values(INSTALL)` always includes `description`/`tool_description` (full-replace semantics — clearing on redeploy is intentional).
- `access_level` always in GIT_SYNC direct (model default "authenticated" is never None); present-only in INSTALL direct (matches deploy.py's existing present-only pattern).
- `name` in `to_orm_values(GIT_SYNC)` is `self.name`; resolver overrides with `manifest_name` — pattern matches the organization resolver's same approach.
- `organization_id` string→UUID conversion kept in `_resolve_workflow` (resolver logic, not model logic).

## Concerns

None. The pattern is clean, all tests pass, and the roundtrip detector remains green.
