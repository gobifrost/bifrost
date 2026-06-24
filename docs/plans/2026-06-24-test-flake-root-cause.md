# Intermittent test flake — root cause & fix (2026-06-24)

Two backend tests passed in isolation but failed intermittently under `./test.sh all`:
- `tests/unit/test_manifest_scope_aware.py::TestScopeAwareManifest::test_no_solution_id_is_repo_scoped`
- `tests/e2e/platform/test_solution_connection_refs_e2e.py::test_export_scrubs_connection_template_and_carries_no_secret`

Investigated via two parallel subagents (ref-resolution trace + isolation audit). Both converged.

## Root cause (two layers)

### 1. Real production data-integrity bug (the underlying defect)
`api/src/repositories/tables.py` (create line ~85, update line ~124) serialized
`TablePolicies` with `model_dump(mode="json")` **without `by_alias=True`**.
`PolicyRuleRef.ref` carries `alias="$ref"` (`src/models/contracts/policies.py:299`),
so a `{"$ref": "rule"}` policy was **persisted to the DB as `{"ref": "rule"}`** —
the alias silently dropped. Every table created/updated via the API with a `$ref`
policy stored a corrupted shape. **Pre-existing on main** (the lines were unchanged
on this branch); the branch's new manifest/test interactions merely exposed it.

`bifrost/manifest.py::ManifestTable.from_row` checked the literal key `"$ref"`,
didn't find it in `{"ref": ...}`, fell through to `ManifestPolicy.model_validate`,
which requires `name`+`actions` → `ValidationError` (the flake symptom).

### 2. Order-dependent test leak (why it was intermittent)
`tests/e2e/test_table_ref_enforced.py::test_table_list_allowed_via_referenced_rule`
created a **global** (`organization_id=None`, `solution_id=None`) table via the API
with a `$ref` policy, but its `finally` only deleted the seeded `PolicyRule` — the
table (committed in the running API's own session, never reached by the test's
`db_session` rollback) leaked. A later same-process test calling
`generate_manifest(db)` with no solution scope picked up that orphaned global table
and exploded on its corrupted `{"ref": ...}` policy. In isolation the orphan didn't
exist → passed.

## Fix (source-level, 3 layers — no risky shared-infra change)
1. `tables.py`: `model_dump(mode="json", by_alias=True)` on create + update. **Canonical fix.**
2. `manifest.py`: `from_row` accepts both `"$ref"` and legacy `"ref"`, recovering
   already-corrupted production rows as a ref instead of crashing. **Defense-in-depth** (prod DBs already contain `{"ref": ...}` rows).
3. `test_table_ref_enforced.py`: delete the created table in teardown. **Stops the leak at source.**

Regression tests in `tests/unit/test_table_contract_policies.py`:
- `test_policy_ref_serializes_with_dollar_ref_alias`
- `test_manifest_table_round_trips_ref_policy`
- `test_manifest_table_tolerates_legacy_unaliased_ref_policy`

## Rejected: broadening the conftest sweep
First attempt extended `isolate_file_policies` to also `DELETE` global `Table` +
`PolicyRule` rows and run for unit tests. **This broke `test_arc_1_deploy_through_uninstall`**:
the built-in `admin_bypass` PolicyRule is a *seeded global row every test depends on*
(`src/main.py: seed_builtin_*()`), not test litter — the blanket global delete wiped it.
Backed out. Lesson: global rows are a mix of seed state and leaks; you cannot blanket-delete
them. Fix leaks at their source instead. A comment in `conftest.py` records this so it
isn't re-attempted.

## Residual general gap (NOT fixed — documented for a future, deliberate pass)
The isolation audit found a broader class, independent of this specific flake:
- `db_session` rolls back, but that's a **no-op for any test that commits** (e2e tests
  commit through the API's own session). No truncation safety net.
- `./test.sh all` runs **e2e then unit in one pytest process sharing one DB**, so a
  committed e2e row can pollute a later unit test.
- `isolate_file_policies` skips unit tests and only sweeps `FilePolicy`/`FileMetadata`.

Other committing-without-cleanup tests the audit named (potential future leakers):
`tests/unit/jobs/schedulers/test_deferred_execution_promoter.py`,
`tests/unit/jobs/schedulers/test_cron_scheduler.py`, `tests/unit/test_pending_captures.py`.

A safe systemic fix (e.g. a per-test transaction-savepoint wrapper, or splitting unit
and e2e into separate DBs/processes) is a larger infra change and out of scope for this
flake. Recommended as a separate, carefully-tested effort — do NOT blanket-delete global
rows (see rejected approach above).
