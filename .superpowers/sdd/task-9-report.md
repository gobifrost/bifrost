# Task 9 Report: CLI policy-rule group + tables policies get/set

## Idiom Mirrored

Mirrored `configs.py` for the `policy-rule` group and `files.py` for `tables policies`.

**configs.py idiom followed:**
- `build_cli_flags(PolicyRuleCreate, exclude=DTO_EXCLUDES.get("PolicyRuleCreate", set()), verb_ref_lookups=...)`
- `assemble_body(PolicyRuleCreate, fields, resolver=resolver)` for the create body
- `org_option` + `resolve_org_target` for `create` (unified `--org/--global` standard)
- `pass_resolver`, `run_async`, `output_result`, `_apply_flags` from `.base`

**files.py idiom followed for tables policies:**
- `click.Group("policies", ...)` subgroup added to `tables_group` via `tables_group.add_command(table_policies_group)`
- `policies get <ref>` → GET `/api/tables/{id}`, returns `data.get("policies")`
- `policies set <ref> --file` → loads YAML/JSON file, wraps as `{"policies": <list>}` (matching the `TablePolicies` wire shape), sends PATCH `/api/tables/{id}`

**`create` body assembly:** Uses `assemble_body(PolicyRuleCreate, fields, resolver=resolver)` which calls `load_dict_value` for the `body` dict field (handles `@path` files and JSON literals). The `domain` field is a `Literal["file", "table"]` — `build_cli_flags` emits it as a required `--domain TEXT` flag.

## DTO_EXCLUDES additions

Added to `api/bifrost/dto_flags.py`:

```python
"PolicyRuleCreate": set(_ORG_TARGET_EXCLUDE),  # organization_id via --org/--global
"PolicyRuleUpdate": set(),  # all fields exposed
```

Rationale: `organization_id` is excluded from create (handled via unified org_option); update has no `organization_id` field, so empty exclusion set (explicit entry added to DTO_REF_LOOKUPS for completeness).

## Contract fingerprint refresh

Fingerprint refreshed from `9f33dba1...` → `4b9e5c87...`. **No CONTRACT_VERSION bump** — this is purely additive (new PolicyRule DTOs added to the fingerprint models, no existing DTOs changed, no field renames/removals). Old CLIs that lack the `policy-rule` group still work fine against new servers.

## Skill-truth files regenerated

- `.claude/skills/bifrost-build/generated/cli-reference.md` — added `policy-rule` group (create/delete/get/list/update/usages) + `tables policies` subgroup (get/set) + updated tables group commands list
- `.claude/skills/bifrost-build/generated/openapi-digest.md` — added 5 new `/api/policy-rules` routes + `POST /api/files/structure` (pre-existing gap from a prior task)
- Mirrors synced: `plugins/bifrost/skills/bifrost-build/generated/{cli-reference,openapi-digest}.md`

The `generate.py` script requires the node `dump-app-sdk-surface.mjs` pointing at `/client/src/lib/app-sdk/index.v2.ts` which isn't available in the API container, so the `web-sdk-surface.md` and `python-sdk-signatures.md` were not regenerated (they are unchanged; verified by `test_skill_appendix_fresh.py` passing).

## No src.* imports in bifrost/

```
grep -rn "from src.\|import src." api/bifrost/commands/policy_rules.py \
  api/bifrost/commands/tables.py api/bifrost/contracts/policy_rules.py
# → no output (clean)
```

## TDD — RED then GREEN

**RED:** First e2e run showed 2 failures:
- `test_tables_policies_set_round_trips_ref` → 422 `policies: Input should be a valid dictionary`
- `test_tables_policies_set_plain_inline_policy` → same

**Root cause:** `TableUpdate.policies` is `TablePolicies` (a Pydantic wrapper with `{"policies": [...]}`), not a bare list. Tests and CLI were sending the list directly.

**Fix:**
- Tests: changed `json={"policies": [...]}` to `json={"policies": {"policies": [...]}}`
- CLI `set_table_policy`: loads the file and always wraps `{"policies": loaded_list}` before sending `PATCH /api/tables/{id}`

**GREEN:** All 5 tests pass:
```
tests/e2e/test_cli_policy_rules.py::TestCliPolicyRuleGroup::test_create_list_get_usages_delete_file_rule PASSED
tests/e2e/test_cli_policy_rules.py::TestCliPolicyRuleGroup::test_update_policy_rule PASSED
tests/e2e/test_cli_policy_rules.py::TestTablesPolicisCLI::test_tables_policies_get_returns_policies_field PASSED
tests/e2e/test_cli_policy_rules.py::TestTablesPolicisCLI::test_tables_policies_set_round_trips_ref PASSED
tests/e2e/test_cli_policy_rules.py::TestTablesPolicisCLI::test_tables_policies_set_plain_inline_policy PASSED
```

## Files changed

- `api/bifrost/commands/policy_rules.py` — new (create/list/get/update/delete/usages)
- `api/bifrost/contracts/policy_rules.py` — new (PolicyRuleCreate/Update mirrors)
- `api/tests/e2e/test_cli_policy_rules.py` — new (5 e2e tests)
- `api/bifrost/commands/tables.py` — added `table_policies_group` (get/set)
- `api/bifrost/commands/__init__.py` — registered `policy-rule` group
- `api/bifrost/contracts/__init__.py` — exported PolicyRuleCreate/Update
- `api/bifrost/dto_flags.py` — DTO_EXCLUDES + DTO_REF_LOOKUPS entries
- `api/tests/unit/test_dto_flags.py` — added PolicyRule DTOs to COVERED_DTOS
- `api/tests/unit/test_contract_version.py` — added to _COMMAND_DTOS; fingerprint refreshed
- `.claude/skills/bifrost-build/generated/{cli-reference,openapi-digest}.md` — regenerated
- `plugins/bifrost/skills/bifrost-build/generated/{cli-reference,openapi-digest}.md` — mirrors

## Self-review

- `domain` on `PolicyRuleCreate` is surfaced as `--domain TEXT` (not `click.Choice`), since `build_cli_flags` sees `Literal["file", "table"]` as a plain string after `_unwrap_optional`. For `get/update/delete/usages`, domain is a positional argument with `click.Choice(["file", "table"])`.
- The `tables policies set` imports `pathlib.Path` and `yaml` inline within the async function. This is slightly non-idiomatic but avoids adding module-level imports that weren't there before.
- Pre-existing test failures in `test_policies_validate_endpoint.py` (13 tests) and `test_table_contract_policies.py` (1 test) were confirmed pre-existing and not related to this task.

## Concerns

None blocking. The inline import in `set_table_policy` could be moved to top-of-file in a follow-up.
