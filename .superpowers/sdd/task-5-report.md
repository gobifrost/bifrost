# Task 5 Report: Single Resolving Loader + Wire All Table & File Eval Sites

## Summary

All table and file policy evaluation paths now route through the resolving loader. `PolicyRuleRef` entries are inlined before `preresolve_for_policies` / `compile_read_filter` / `evaluate_action` see the policy document.

## Files Changed

| File | Change |
|------|--------|
| `api/src/services/table_policy_loader.py` | **CREATED** — single resolving loader |
| `api/src/routers/tables.py` | Added import; replaced all 5 eval-path `_load_policies` calls |
| `api/src/routers/websocket.py` | Cache redesigned to raw-tuple; resolution per-read; new imports |
| `api/shared/claims/preresolve.py` | `_load_source_policies` made async + resolves refs; new imports |
| `api/src/services/file_policy_service.py` | Added ref resolution in `is_allowed` before `preresolve_for_policies` |
| `api/tests/e2e/test_table_ref_enforced.py` | **CREATED** — TDD regression guard |

## `_load_policies` Call-Site Inventory (tables.py)

The raw sync `_load_policies(table)` function definition is kept for Task 7's save-validation path. All 5 call sites classified:

| Line (approx) | Function | Classification | Action |
|---------------|----------|---------------|--------|
| 165 | `_check_action_or_403` | **EVAL** — per-row action gate | Replaced with `await load_resolved_table_policies(table, db)` |
| 1187 | `count_documents` | **EVAL** — count filtered by read policy | Replaced |
| 1320 | `query_documents` | **EVAL** — list filtered by read policy | Replaced |
| 1375 | `batch_documents` | **EVAL** — batch upsert policy pre-flight | Replaced |
| 1488 | `batch_delete_documents` | **EVAL** — batch delete policy pre-flight | Replaced |

No save-only `_load_policies` call sites exist currently. All 5 are eval. Task 7 will add save-time ref validation using `_load_policies` + `resolve_policy_refs` directly.

All other evaluation paths (`insert_document`, `upsert_document`, `update_document`, `delete_document`, `get_document`) go through `_check_action_or_403`, which routes through the resolving loader. No extra eval sites found beyond the brief.

The `validate_policies` endpoint does `TablePolicies.model_validate(body)` — this is **save-time** shape validation, not evaluation, and correctly stays raw.

## Websocket Cache Decision: Option (A) — Cache Raw, Resolve Per-Read

**Decision: (A)**

**Reasoning:** The cache previously stored resolved `TablePolicies`. A PolicyRule edit would make cached policies stale until `_invalidate_table_policy_cache` was triggered (only by `policy_changed` on the table's own `access` JSONB). With option (a), a rule edit is reflected on the next `document_change` without any extra bust mechanism.

**Implementation:** Changed `_table_policy_cache` to `dict[str, _RawTableEntry | None]` where `_RawTableEntry` is a `@dataclass` holding `(access: dict | None, org_id: UUID | None, solution_id: UUID | None)`. Resolution happens inside a fresh `get_db_context()` call after each cache read (hit or miss). The cache still amortizes the table's own `access` JSONB lookup.

**Trade-off:** Each `document_change` fanout event pays one async DB round-trip per distinct `$ref` name. This is a small constant overhead, not proportional to subscriber count. Future Task 6+ optimization: cache resolved docs + bust on rule edits.

## `_load_source_policies` — Made Async at the Function Level

Made `_load_source_policies` **async** and passed `db` through (smaller diff than call-site wrapping). The function is called from `_run_claim_query` which is already `async` with `db: AsyncSession` in scope. Call site changes from:
```python
source_policies = _load_source_policies(source)
```
to:
```python
source_policies = await _load_source_policies(source, db)
```

Resolution: `PolicyRuleRepository(db, org_id=source.organization_id, is_superuser=True)` + `await resolve_policy_refs(policies, repo=repo, action_domain="table")`. On unresolvable ref → returns empty `TablePolicies()` (fail-closed).

## TDD — Test Written as GREEN Guard

Test file: `api/tests/e2e/test_table_ref_enforced.py`

**RED state (without this task):** A table with `{"policies": [{"$ref": "rule_name"}]}` → `_load_policies(table)` → `TablePolicies.model_validate(table.access)` → `TablePolicies` containing a bare `PolicyRuleRef` (no `.when` attribute) → `preresolve_for_policies` iterates policies and calls `policy.when` → `AttributeError`. Query would 500.

**GREEN state:** `load_resolved_table_policies` inlines the ref before evaluation; admin sees 1 document.

**Test approach:** Hybrid `db_session` (inject `PolicyRule` with `await db_session.commit()`) + `e2e_client`/`platform_admin` (HTTP create/insert/query). The `finally` block deletes the global rule to avoid test pollution across the session-scoped `platform_admin` fixture.

## Quality Checks

- `pyright`: 0 errors, 0 warnings
- `ruff check .`: 1 pre-existing error in `tests/e2e/test_resolve_policy_refs.py` (unused `TablePolicies` import) — not introduced by this task (verified via git stash)

## Test Results

Run after implementation (GREEN state):
- `./test.sh e2e tests/e2e/test_table_ref_enforced.py -v` — results pending background run
- `./test.sh e2e tests/e2e -k "policy or table or file or websocket or claim" -v` — results pending background run

(Report written while tests run; commit SHA and final test output in commit message.)

## Extra Eval Sites Found

None beyond the brief. All 5 briefed `_load_policies` sites + the 3 explicitly mentioned paths (websocket, preresolve, file_policy_service) cover the full evaluation surface.

## Concerns

1. **Websocket double DB session:** Cache-hit path opens 1 DB session (resolution only). Cache-miss path opens 2 sequential sessions (raw fetch + resolution). This is unavoidable with `get_db_context()` closing on `async with` exit. A future optimization: combine both into one session on cache miss.

2. **`preresolve.py` note:** `_load_source_policies` does NOT pass `solution_id` to `resolve_policy_refs` — it uses `solution_id=None` (the default). The `source` table's `organization_id` is used for the repo scope, which gives org→global cascade. If a claim references a solution-scoped source table whose policy uses a solution-scoped rule, the ref may not resolve. This is a pre-existing design question about cross-solution claim resolution; flagged but not changed here.

---

## Review Fix: Remove Dead `_load_policies` Function

**Commit:** `3409e0020` — `refactor(policy-rules): remove dead _load_policies (all eval sites use the resolving loader)`

**Grep confirmation (no caller):**
- `grep -rn "_load_policies" api/src api/shared` returned exactly one hit: the definition itself at `api/src/routers/tables.py:71` (now deleted). The websocket hits are `_load_policies_for_table` — a different function, untouched. Two comments in `manifest_import.py` referenced it; line 2079 was updated to name `load_resolved_table_policies` in `table_policy_loader.py` instead.

**Imports:** `ValidationError` retained (still used at line 869); `Table` ORM import retained (used in 10+ other places). No imports removed.

**Ruff:** `All checks passed!` on `src/routers/tables.py`.

**Focused tests:**
- `tests/unit/test_policies_validate_endpoint.py` — **16/16 passed** (3.76 s)
- `tests/e2e/test_table_ref_enforced.py` — **1/1 passed** (5.00 s)
