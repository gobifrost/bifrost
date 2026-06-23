# Task 10 Report: MCP Thin Wrapper + Manifest Round-Trip for Named Policy Rules

## Summary

Implemented `ManifestPolicyRule` entity model, manifest generator/import for named policy rules (is_builtin excluded, `_resolve_policy_rule` ordered before tables/file policies), and the `policy_rules.py` MCP thin wrapper.

## ManifestPolicyRef / Union NOT Re-added

Confirmed Task 8's work was already present:
- `ManifestPolicyRef` at line 908 (unchanged, not re-added)
- `ManifestTable.policies: list[ManifestPolicy | ManifestPolicyRef] | None` (unchanged, not re-widened)

## ManifestPolicyRule

Added to `api/bifrost/manifest.py` after `ManifestConfig`:
- Fields: `id` (IDENTITY), `name`/`domain`/`description`/`body` (CONTENT), `organization_id` (ENVIRONMENT, match_key=True)
- `from_row(rule)` — builds from PolicyRule ORM; excludes is_builtin/created_by/solution_id/timestamps
- `to_orm_values(Destination.GIT_SYNC)` — returns direct ImportFields; raises NotImplementedError for other destinations

Also added `policy_rules` to: MANIFEST_FILES (`"policy-rules.yaml"`), Manifest class, filter_manifest_by_ids, get_all_entity_ids.

## is_builtin Exclusion in manifest_generator.py

DB query: `select(PolicyRule).where(PolicyRule.is_builtin == False)` — built-ins are seeded at startup, never shipped in bundles.

## _resolve_policy_rule Ordering

manifest_import.py step ordering:
- Step 5 = `_resolve_policy_rule` (NEW — BEFORE tables and file policies)
- Step 6 = `_resolve_table`
- Step 8 = `_resolve_file_policy`

Also added: prefetch cache (`policy_rule_by_natural`, `policy_rule_ids`), stale-entity cleanup (deletes non-builtin rules not in manifest), `present_policy_rule_uuids`.

## MCP Tool (policy_rules.py)

`api/src/services/mcp_server/tools/policy_rules.py` — thin HTTP bridge:
- `list_policy_rules` → `GET /api/policy-rules`
- `create_policy_rule` → `POST /api/policy-rules`
- `delete_policy_rule` → `DELETE /api/policy-rules/{domain}/{name}`

No ORM, no repository imports, no AsyncSession. Registered in `__init__.py`.

## TDD RED → GREEN

RED: `test_to_orm_values_raises_for_non_git_sync` failed with `AttributeError: Destination has no attribute SOLUTION_INSTALL` (wrong enum name in test).

GREEN after fix to `Destination.INSTALL`: **140 passed, 0 failed**.

```
./test.sh tests/unit/test_manifest.py tests/unit/test_mcp_thin_wrapper.py -v
→ 140 passed in 3.26s
```

## Git-Sync Round-Trip E2E

Two tests in `TestPolicyRuleRoundTrip`:
1. `test_pull_policy_rule_from_manifest` — manifest commit → sync → PolicyRule row verified in DB
2. `test_policy_rule_runs_before_table_in_import` — rule + table with `{"$ref": "ops_access"}` → both exist after sync

E2E test run was in progress at report-writing time (stack healthy, test runner active).

## Gates

```
./test.sh tests/unit/test_dto_flags.py tests/unit/test_contract_version.py -v
→ 64 passed in 0.65s  ✓
```

No CONTRACT_VERSION bump — only MCP tool added, no CLI/SDK DTOs changed.

`python3 api/scripts/skill-truth/generate.py` ran in debug container. openapi-digest.md was already correct (policy-rules endpoints present from prior tasks). cli-reference.md produced a false diff from stale debug container — restored to committed state. plugins/bifrost/skills mirror verified in sync.

## Files Changed

- `api/bifrost/manifest.py`
- `api/src/services/manifest_generator.py`
- `api/src/services/manifest_import.py`
- `api/src/services/mcp_server/tools/policy_rules.py` (new)
- `api/src/services/mcp_server/tools/__init__.py`
- `api/tests/unit/test_manifest.py` (5 new tests in TestManifestPolicyRule)
- `api/tests/unit/test_mcp_thin_wrapper.py` (policy_rules in PARITY_HANDLERS + MODULES)
- `api/tests/e2e/platform/test_git_sync_local.py` (TestPolicyRuleRoundTrip — 2 tests)

## Self-Review

All constraints met: ManifestPolicyRef/union not re-added; ManifestPolicyRule mirrors ManifestConfig pattern; is_builtin excluded from generator; _resolve_policy_rule before _resolve_table/_resolve_file_policy; MCP tool ORM-free; gates green.

## Concerns

None.
