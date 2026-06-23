# Task 13 Report: Fix 7 sweep-caught test regressions

## Fix 1: `test_all_entity_subgroups_registered`
**Root cause**: `"policy-rule"` CLI group added by Task 9 was missing from the hardcoded expected set in the test.
**Fix**: Added `"policy-rule"` to the set literal in `api/tests/unit/cli/test_cli_base.py`.
**Result**: PASS

## Fix 2: `test_download_sdk_can_be_imported`
**Root cause**: Embedded test script asserted `len(ENTITY_GROUPS) == 13`; now 14 with `policy-rule`.
**Fix**: Updated literal `13` → `14` in `api/tests/e2e/api/test_cli.py` line 756.
**Result**: PASS

## Fix 3: `test_run_claim_query_returns_empty_when_source_table_denies_read`
**Root cause**: `_load_source_policies` was refactored to take 2 args `(source, db)` and made async; mock lambda only accepted 1 arg and wasn't async.
**Fix**: Replaced `lambda _s: TablePolicies()` with an `async def _fake_load_source_policies(_s, _db)` in `api/tests/unit/claims/test_preresolve.py`.
**Result**: PASS

## Fix 4: `test_every_field_is_classified[ManifestPolicyRef]`
**Root cause**: `ManifestPolicyRef.ref` field lacked `bifrost_field_class` in `json_schema_extra`. The field-class tripwire requires every manifest model field to carry this tag.
**Fix**: Added `**classify(FieldClass.CONTENT)` to `ManifestPolicyRef.ref = Field(...)` in `api/bifrost/manifest.py`.
**Result**: PASS

## Fix 5: `test_no_solution_id_is_repo_scoped`
**Classification**: NOT a real regression — the test was already PASSING before any changes. The error in the task description was referring to a failure that had been pre-resolved. Test passed as-is with no changes.
**Result**: PASS (no change needed)

## Fix 6: `test_load_policies_corruption_returns_empty`
**Root cause**: Test imported `_load_policies` from `src.routers.tables` which was deleted (Task 5 replaced all call sites with `load_resolved_table_policies`).
**Fix**: Rewrote test in `api/tests/unit/test_table_contract_policies.py` to use `load_resolved_table_policies` from `src.services.table_policy_loader`. Made it `@pytest.mark.asyncio`, added `AsyncMock` for db, updated logger name (`src.services.table_policy_loader`) and warning text to match new implementation.
**Result**: PASS

## Fix 7: `test_appendices_are_fresh_and_present`
**Root cause**: `cli-reference.md` was stale — the `policy-rule` CLI group and updated `claims` `--type [list|scalar]` enum were not reflected.
**Fix**: Generated updated `cli-reference.md` via `gen_cli_reference()` on host (bifrost-package-only, no src imports needed). Changes: `claims create/update --type TEXT` → `--type [list|scalar]`; `policy-rule create --domain TEXT` → `--domain [file|table]`; `policy-rule` group now appears in CLI reference.
**Codex mirrors**: `test_codex_mirror_sync` is SKIPPED outside CI — mirrors were NOT needed to unblock.
**Result**: PASS

## All 7 tests: GREEN
- `tests/unit/cli/test_cli_base.py` — PASS
- `tests/e2e/api/test_cli.py::TestCLIDownload::test_download_sdk_can_be_imported` — PASS
- `tests/unit/claims/test_preresolve.py` (all 4) — PASS
- `tests/unit/test_field_class_tripwire.py` (all parameterized + named) — PASS
- `tests/unit/test_manifest_scope_aware.py` (both) — PASS
- `tests/unit/test_table_contract_policies.py` (all 6) — PASS
- `tests/unit/test_skill_appendix_fresh.py` — PASS

`ruff check api/` — clean. 95 unit tests + 1 e2e test all green.
