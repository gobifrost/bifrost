# Task 10 Report: MCP Thin Wrapper + Manifest Round-Trip for Named Policy Rules

## Summary

Implemented `ManifestPolicyRule` entity model, manifest generator/import for named policy rules (is_builtin excluded, `_resolve_policy_rule` ordered before tables/file policies), and the `policy_rules.py` MCP thin wrapper. All unit and e2e tests pass.

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

### Bug fixes during implementation

1. **`PolicyRule.created_at/updated_at` have Python-side defaults only** (no `server_default`). Core INSERT must supply them explicitly — added `created_at` and `updated_at` to the INSERT path in `_resolve_policy_rule`.

2. **`has_entities` check in `_import_all_entities` omitted `policy_rules`**. When the `.bifrost/` dir contained ONLY `policy-rules.yaml`, `has_entities` evaluated to `False` and import was skipped entirely. Fixed by adding `or manifest.policy_rules` to the check.

3. **Test used name `"admin_bypass"` that conflicts with a seeded builtin**. The natural-key lookup matched the builtin rule (with a different UUID), causing the manifest's UUID to point to the builtin row. Fixed by using a unique name `"ops_read_only"` in the test.

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
→ 140 passed in 3.09s
```

## Git-Sync Round-Trip E2E

Two tests in `TestPolicyRuleRoundTrip`:
1. `test_pull_policy_rule_from_manifest` — manifest commit → sync → PolicyRule row verified in DB
2. `test_policy_rule_runs_before_table_in_import` — rule + table with `{"$ref": "ops_access"}` → both exist after sync

```
./test.sh tests/e2e/platform/test_git_sync_local.py::TestPolicyRuleRoundTrip -v
→ 2 passed in 2.78s
```

## Gates

```
./test.sh tests/unit/test_dto_flags.py tests/unit/test_contract_version.py -v
→ 64 passed in 0.53s  ✓
```

No CONTRACT_VERSION bump — only MCP tool added, no CLI/SDK DTOs changed.

`python3 api/scripts/skill-truth/generate.py` ran in debug container. openapi-digest.md was already correct (policy-rules endpoints present from prior tasks). cli-reference.md produced a false diff from stale debug container — restored to committed state. plugins/bifrost/skills mirror verified in sync.

## Files Changed

- `api/bifrost/manifest.py` — ManifestPolicyRule class + MANIFEST_FILES + Manifest.policy_rules
- `api/src/services/manifest_generator.py` — serialize_policy_rule + is_builtin exclusion
- `api/src/services/manifest_import.py` — _resolve_policy_rule (step 5), prefetch cache, stale cleanup
- `api/src/services/github_sync.py` — has_entities check includes policy_rules
- `api/src/services/mcp_server/tools/policy_rules.py` (new) — 3 thin HTTP wrappers
- `api/src/services/mcp_server/tools/__init__.py` — policy_rules registered
- `api/tests/unit/test_manifest.py` — TestManifestPolicyRule (5 tests)
- `api/tests/unit/test_mcp_thin_wrapper.py` — policy_rules in PARITY_HANDLERS + MODULES
- `api/tests/e2e/platform/test_git_sync_local.py` — TestPolicyRuleRoundTrip (2 tests)

## Self-Review

All constraints met: ManifestPolicyRef/union not re-added; ManifestPolicyRule mirrors ManifestConfig pattern; is_builtin excluded from generator; _resolve_policy_rule before _resolve_table/_resolve_file_policy; MCP tool ORM-free; gates green.
