# Table Policies Hardening Addendum

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. The companion plan is `2026-04-30-table-policies.md` — assume all 22 tasks of that plan are complete (commit `1c7cf3f9` on `feat/table-access`). This addendum closes gaps surfaced during execution.

**Goal:** Close the real gaps in the table-policies branch before merge. The 22-task plan delivered the core feature; review and verification surfaced six items that need to land before this is shippable.

**Working directory:** `/home/jack/GitHub/bifrost/.worktrees/table-access` — branch `feat/table-access`.

**Test stack:** `bifrost-test-e605f208` (per-worktree, already up).

---

## Task A: Write-through role cache (replaces per-request DB lookup)

**Files:**
- Create: `api/shared/role_cache.py`
- Create: `api/tests/unit/test_role_cache.py`
- Modify: `api/src/core/auth.py` — `get_execution_context` reads from cache, hydrates on miss, populates
- Modify: `api/src/routers/websocket.py` — `_populate_user_roles` uses the same cache
- Modify: `api/src/routers/roles.py` (or wherever role/user-role mutations live) — write-through invalidation

The Tasks 10/12 follow-ups added `select(Role.id, Role.name) JOIN UserRole WHERE user_id=...` per request and per WS connection. The plan said this was acceptable for now; it's not — it adds a DB round-trip to every authenticated REST call. Replace with a Redis-backed write-through cache.

### Design

- **Cache key:** `bifrost:role_cache:user:{user_id}` → JSON `{"role_ids": [...], "role_names": [...], "v": 1}` (the `v` is a schema version for future evolution).
- **Read path:** `get_user_roles(user_id) -> (role_ids, role_names)` — cache hit returns immediately; miss queries DB, populates cache, returns.
- **Write path on mutation:** any code that creates / updates / deletes a `UserRole` row, or renames / deletes a `Role`, calls `invalidate_user_role_cache(user_id)` (single user) or `invalidate_role_id_cache(role_id)` (all users with that role — needs a reverse lookup or a broad sweep). For role rename / role delete, broadcast invalidation to all users currently in cache (the cheapest correct approach is to delete the entire `bifrost:role_cache:user:*` keyspace; acceptable because the cache rebuilds on first read).
- **TTL:** 1 hour (cache entries are write-through, so TTL is defense-in-depth against missed invalidations, not the primary correctness mechanism).

### Steps

- [ ] **Step 1: Failing tests for the cache**

Create `api/tests/unit/test_role_cache.py` with:
- `test_get_user_roles_miss_hydrates_from_db` — cache empty → DB query fires → cache populated
- `test_get_user_roles_hit_returns_cache_no_db` — pre-seed cache → no DB query
- `test_invalidate_user_role_cache_clears_entry` — populate then invalidate → next read re-hydrates
- `test_role_id_invalidation_clears_all_users_with_role` — multiple users have the role → invalidating the role clears all of them
- `test_cache_handles_user_with_no_roles` — empty role list round-trips correctly (don't treat empty as a miss)

Use `redis.asyncio.Redis` mock or the test stack's real Redis (the project already has Redis in the test stack).

- [ ] **Step 2: Implement `shared/role_cache.py`**

Public API:
```python
async def get_user_roles(user_id: UUID, db: AsyncSession) -> tuple[list[UUID], list[str]]:
    """Returns (role_ids, role_names) for a user. Cache-first, DB on miss."""

async def invalidate_user(user_id: UUID) -> None:
    """Drop the cache entry for one user. Call after UserRole mutations for that user."""

async def invalidate_role(role_id: UUID) -> None:
    """Drop cache entries for every user who has this role.
    Call after Role rename / Role delete / UserRole mutations involving this role."""
```

Implementation notes:
- Use `cache_get_json` / `cache_set_json` helpers if they already exist in `api/shared/redis_cache.py` (check first); otherwise use `redis.asyncio` directly.
- For `invalidate_role`, scan `bifrost:role_cache:user:*` keys and check whether each cached entry contains the role_id. Acceptable at typical scale (<10k cached users); revisit if roles are mutated at high frequency.
- TTL = 3600 seconds.
- Empty result is a valid cache value: `{"role_ids": [], "role_names": [], "v": 1}`. Do not treat absence-of-keys as miss-due-to-no-roles.

- [ ] **Step 3: Wire into `get_execution_context`**

In `api/src/core/auth.py`, replace the existing `if not user.role_ids and not user.role_names: ... select(...) ...` block with:

```python
from shared.role_cache import get_user_roles

if not user.role_ids and not user.role_names:
    role_ids, role_names = await get_user_roles(user.user_id, db)
    user.role_ids = role_ids
    user.role_names = role_names
```

- [ ] **Step 4: Wire into `_populate_user_roles` (websocket)**

In `api/src/routers/websocket.py`, same pattern: replace the inline DB query with `get_user_roles(user.user_id, db)`.

- [ ] **Step 5: Add invalidation on role mutations**

Find every place that mutates `UserRole` or `Role`:
- `api/src/routers/roles.py` (or wherever the `POST /api/roles/{role_id}/users` endpoint lives) — when adding/removing users from a role: `await invalidate_user(user_id)` for each affected user
- Role rename / Role delete: `await invalidate_role(role_id)` (broad invalidation)
- Anywhere else the codebase touches `UserRole` rows directly (grep for it)

Add a docstring to each mutation site referencing the cache invariant.

- [ ] **Step 6: Verify**

```bash
./test.sh tests/unit/test_role_cache.py -v
./test.sh tests/e2e/platform/test_policies.py -v       # role-gated test must still pass
./test.sh tests/e2e/platform/test_subscriptions.py -v  # ws role hydration must still work
```

- [ ] **Step 7: Commit**

```
feat(policies): write-through role cache replaces per-request DB hydration

- shared/role_cache.py provides get_user_roles + invalidate_user / invalidate_role.
- auth.py and websocket.py now hit the cache; DB is fallback on miss.
- Role / UserRole mutation sites invalidate the cache write-through.
- TTL is defense-in-depth against missed invalidations.
```

---

## Task B: `eq` / `neq` validator tightening (closes evaluator/compiler divergence)

**Files:**
- Modify: `api/src/models/contracts/policies.py` — `_validate_op_node` rejects `None` literals in `eq`/`neq`
- Modify: `api/tests/unit/policies/test_validator.py` — regression test
- Modify: `api/tests/unit/policies/test_round_trip.py` — add `is_null`-via-`eq`-rejection case

### Background

The evaluator returns `False` for `{"eq": [{"row": "x"}, None]}` regardless of `x`'s value (NULL-as-false semantics). The SQL compiler emits `col IS NULL`, which is `True` when the column is null. Same policy → different answers between single-row REST handler (evaluator) and list/query handler (SQL pushdown).

Path-dependent behavior is a footgun. The intended idiom is `is_null` (both paths agree). Tighten the validator to reject `None` in `eq`/`neq` at write time.

### Steps

- [ ] **Step 1: Failing test**

Edit `api/tests/unit/policies/test_validator.py`. Add:

```python
def test_eq_rejects_none_literal():
    """eq/neq with a None literal is ambiguous between IS NULL and NULL-as-false.
    Use is_null instead."""
    with pytest.raises(ValidationError, match="use is_null"):
        _expr({"eq": [{"row": "x"}, None]})
    with pytest.raises(ValidationError, match="use is_null"):
        _expr({"neq": [{"row": "x"}, None]})
    # eq with None on left side is also rejected (symmetric)
    with pytest.raises(ValidationError, match="use is_null"):
        _expr({"eq": [None, {"row": "x"}]})
```

- [ ] **Step 2: Update validator**

In `_validate_op_node`, after the existing `eq`/`neq` arity check, add:

```python
if op in {"eq", "neq"}:
    for operand in value:
        if operand is None:
            raise ValueError(
                f"{op} does not accept null literals (NULL semantics differ "
                "between evaluator and SQL pushdown). Use is_null instead."
            )
```

- [ ] **Step 3: Run, expect green**

```bash
./test.sh tests/unit/policies/test_validator.py -v
./test.sh tests/unit/policies/ -v          # full policies suite, no regressions
```

- [ ] **Step 4: Commit**

```
fix(policies): reject None literals in eq/neq

The evaluator and SQL compiler diverge on `{"eq": [col, null]}` —
evaluator returns False (NULL-as-false), compiler emits `col IS NULL`
(true when null). The intended idiom is `is_null`, which both agree on.
Reject None literals in eq/neq at validation time so policies can't
encode the divergent shape.
```

---

## Task C: Audit logging on policy denial (writes to AuditLog table, surfaces in UI)

**Files:**
- Modify: `api/src/routers/tables.py` — `_check_action_or_403` writes an audit row before raising
- Modify: `api/src/models/orm/audit.py` (or wherever `AuditLog` ORM lives) — verify the schema accepts the policy-denial shape
- Possibly modify: `api/src/handlers/audit.py` (or similar) — if the audit-log query endpoint needs to filter for `event_type="policy_deny"`
- Modify: `api/tests/e2e/platform/test_policies.py` — assert an audit row is written on each denial

### Background

`_check_action_or_403` raises 403 with no logging. A policy author debugging "why can't user X read row Y?" has no trail. Audit log entries should be written and visible in the existing audit-log UI (whatever surfaces existing audit events for this org).

### Steps

- [ ] **Step 1: Find the audit log infrastructure**

```bash
grep -rn "class AuditLog\|audit_log\|AuditLogCreate" api/src/ | head -20
```

Read the existing audit ORM, the existing event_type values, and the existing handlers. The goal is to use the existing audit mechanism, not invent a new one.

If there's no existing audit table, **STOP and ask** — building one is out of scope.

- [ ] **Step 2: Failing test**

In `api/tests/e2e/platform/test_policies.py` add:

```python
async def test_denial_writes_audit_row(self, e2e_client, platform_admin, alice_user):
    """A 403 from policy denial writes an audit row that the admin can query."""
    table_id = _create_table(
        e2e_client, platform_admin.headers, f"audit_{uuid.uuid4().hex[:8]}",
    )  # seeded admin_bypass only — Alice will be denied

    # Alice tries to insert (denied)
    r = _insert(e2e_client, alice_user.headers, table_id, {"x": 1})
    assert r.status_code == 403

    # Admin queries the audit log
    audit = e2e_client.get(
        "/api/audit-log",  # or whatever the existing endpoint is
        headers=platform_admin.headers,
        params={"event_type": "policy_deny", "limit": 10},
    )
    assert audit.status_code == 200
    rows = audit.json()["events"]  # or whatever the existing shape is
    matching = [r for r in rows if r["actor_id"] == str(alice_user.user_id)]
    assert len(matching) >= 1
    entry = matching[0]
    assert entry["event_type"] == "policy_deny"
    assert entry["resource_type"] == "table_document"
    assert entry["details"]["action"] == "create"
    assert entry["details"]["table_id"] == table_id
```

If the existing audit endpoint shape differs, adapt — keep the assertion that a policy_deny row exists with the right actor and details.

- [ ] **Step 3: Wire denial to the audit log**

In `api/src/routers/tables.py`:

```python
def _check_action_or_403(
    action: str,
    table,
    row: dict,
    user_principal,
    *,
    db: AsyncSession,  # required for the audit write
) -> None:
    policies = _load_policies(table)
    if not evaluate_action(action, policies, row, user_principal):
        # Audit before raising
        await write_audit_log(
            db=db,
            event_type="policy_deny",
            actor_id=user_principal.user_id,
            resource_type="table_document",
            resource_id=row.get("id"),
            details={
                "action": action,
                "table_id": str(table.id),
                "table_name": table.name,
                # Do NOT include the row body or policy names — same generic
                # surface as the 403 response detail.
            },
        )
        raise HTTPException(status_code=403, detail="Access denied")
```

Use whatever existing helper writes audit log rows. If there's no helper, write the row directly via the ORM.

**Important:** Do NOT include the row body (could leak sensitive data the user wasn't supposed to see) or policy names (could leak the policy structure). Only metadata: actor, action, table id+name, doc id (if the action was on a specific row).

Update every call site of `_check_action_or_403` to pass `db=session`. Same for batch denial (write one audit row per denied index, or one summary row — pick whichever the existing audit pattern supports).

- [ ] **Step 4: Verify the UI surfaces the new event_type**

The existing audit-log UI may filter by event_type. Add `policy_deny` to whatever allow-list / dropdown / filter that UI uses. If the UI displays an arbitrary event_type list from the API, no UI change needed.

```bash
grep -rn "policy_deny\|event_type" client/src/ | head
```

- [ ] **Step 5: Run**

```bash
./test.sh tests/e2e/platform/test_policies.py::TestPoliciesMatrix::test_denial_writes_audit_row -v
./test.sh tests/e2e/platform/test_policies.py -v   # full matrix still green
```

- [ ] **Step 6: Commit**

```
feat(policies): audit denial events to the audit log

_check_action_or_403 writes a policy_deny audit row before raising 403.
Details carry actor, action, table id+name, and (when applicable) row id.
Body and policy names are intentionally excluded (no info leak via audit).
Visible in the existing audit-log UI.
```

---

## Task D: Malformed-JSONB defense in `_load_policies`

**Files:**
- Modify: `api/src/routers/tables.py` — `_load_policies` catches `ValidationError`
- Modify: `api/tests/unit/test_table_contract_policies.py` — corruption regression test

### Background

If `Table.access` JSONB is corrupt (manual SQL edit, schema drift from a partial migration, malformed manifest import), `TablePolicies.model_validate(table.access)` raises `ValidationError` and the request 500s. This makes one corrupt row take the entire table offline.

Per project rules ("no fallback without asking"), the right move is fail-closed (return empty `TablePolicies` → default deny) **with a warning log** so the corruption is visible.

### Steps

- [ ] **Step 1: Failing test**

In `api/tests/unit/test_table_contract_policies.py` add:

```python
def test_load_policies_corruption_returns_empty(caplog):
    """_load_policies fails closed (empty TablePolicies → default deny)
    when JSONB is corrupt, with a warning log so corruption is visible."""
    from src.routers.tables import _load_policies

    class FakeTable:
        access = {"policies": [{"name": "p", "actions": ["read"], "when": {"INVALID_OP": []}}]}
        id = "..."

    with caplog.at_level("WARNING"):
        result = _load_policies(FakeTable())

    assert result.policies == []  # default deny
    assert any("malformed policies" in rec.message for rec in caplog.records)
```

- [ ] **Step 2: Update `_load_policies`**

```python
def _load_policies(table) -> TablePolicies:
    if not table.access:
        return TablePolicies()
    try:
        return TablePolicies.model_validate(table.access)
    except ValidationError as e:
        logger.warning(
            "malformed policies on table %s; defaulting to empty (deny). "
            "Validation error: %s",
            table.id, e,
        )
        return TablePolicies()
```

- [ ] **Step 3: Run**

```bash
./test.sh tests/unit/test_table_contract_policies.py -v
```

- [ ] **Step 4: Commit**

```
fix(policies): _load_policies fails closed on malformed JSONB

A corrupt access JSONB blob raised 500 and took the whole table offline.
Now: catch ValidationError, log a warning, return empty TablePolicies
(default deny). Operators see the corruption in logs; users get a
predictable deny instead of a 500.
```

---

## Task E: Validator hardening (recursion limit + error path)

**Files:**
- Modify: `api/src/models/contracts/policies.py` — depth-limit + path-aware error messages
- Modify: `api/tests/unit/policies/test_validator.py` — depth-limit test + error-message-path test

### Background

Two reviewer items deferred from Task 3:
- **Recursion-depth DoS surface:** `_validate_operand` is unbounded; deeply-nested AST imports (e.g., `{"not": {"not": {"not": ...}}}`) hit Python's recursion limit and raise an unhelpful `RecursionError` → 500. Manifest imports could carry attacker-controlled deeply-nested expressions.
- **Error messages don't include node path:** "eq requires exactly two operands" is unhelpful when the policy has 5 nested `and` blocks. Surface `$.policies[2].when.and[1].eq` style paths.

### Steps

- [ ] **Step 1: Failing tests**

In `test_validator.py`:

```python
def test_validator_rejects_deeply_nested_expression():
    """Recursion is bounded at 64 levels; deeper raises a clear error."""
    expr_dict = {"not": True}
    for _ in range(70):
        expr_dict = {"not": expr_dict}
    with pytest.raises(ValidationError, match="nested too deeply"):
        Expr.model_validate(expr_dict)


def test_validator_error_includes_path():
    """Error message includes a JSON path to the bad node."""
    bad = {
        "and": [
            {"eq": [{"row": "x"}, 1]},
            {"eq": [{"row": "y"}]},  # missing operand at and[1]
        ]
    }
    with pytest.raises(ValidationError, match=r"\$\.and\[1\]\.eq"):
        Expr.model_validate(bad)
```

- [ ] **Step 2: Implement**

Add a `_DEPTH_LIMIT = 64` module constant. Thread `depth: int = 0, path: str = "$"` through `_validate_operand` / `_validate_op_node` / `_validate_call`. On every recursive call, append to `path` (e.g., `f"{path}.and[{i}]"`) and check `depth + 1 < _DEPTH_LIMIT`, raising `ValueError(f"expression nested too deeply (>{_DEPTH_LIMIT} levels)")` otherwise.

For path tracking:
- Logical/comparison/in operators with list operands: `f"{path}.{op}[{i}]"`
- `not` / `is_null` (single operand): `f"{path}.{op}"`
- Reference dicts (`{"row": ...}` / `{"user": ...}`): paths terminate
- Function call args: `f"{path}.args[{i}]"`

Surface paths in every `ValueError` raised inside the validator: `raise ValueError(f"{path}: {message}")`.

- [ ] **Step 3: Run**

```bash
./test.sh tests/unit/policies/test_validator.py -v
./test.sh tests/unit/policies/ -v
```

- [ ] **Step 4: Commit**

```
fix(policies): bound validator recursion + emit JSON paths in errors

- _DEPTH_LIMIT (64) on the AST validator prevents pathological imports
  (manifest from untrusted source) from hitting Python's recursion limit.
- Error messages now include a JSON-path pointer to the bad node
  ($.and[1].eq), making policy debugging actionable.
```

---

## Task F: PolicyEditor browser smoke + manifest e2e + visibility-gain Playwright

**Files:**
- Modify (or run manually): `./debug.sh` browser smoke for the PolicyEditor UI
- Create: `api/tests/e2e/platform/test_git_sync_local.py::test_table_policies_round_trip` (or wherever git-sync e2e lives)
- Modify: `client/e2e/policies-app-realtime.admin.spec.ts` — add the visibility-gain test that Task 19 skipped

### Steps

- [ ] **Step 1: PolicyEditor browser smoke**

Boot the dev stack:
```bash
./debug.sh
./debug.sh status   # note URL + login
```

Click through:
1. Tables → New table → enter name → Save → confirm seeded admin_bypass shows
2. Edit table → Policies section → click Add policy → Monaco loads → JSON Schema autocomplete fires (verify the suggestion list shows operators / row / user)
3. Click "Insert template" → own_row → row added with prefilled `when`
4. Click "Reference" panel → see USER fields, Row examples, Functions, Operators
5. Submit → confirm the table's new policies persist and a non-admin user is gated correctly
6. Capture a screenshot of each step at `~/Sync/Screenshots/policy-editor-step-N.png`
7. **If Monaco autocomplete does NOT fire** — the schema-binding via `setDiagnosticsOptions` may be wrong; investigate and fix before merge

- [ ] **Step 2: Manifest round-trip e2e**

Find the existing git-sync e2e test file:
```bash
grep -rn "test_git_sync\|test_export.*import" api/tests/e2e/ | head
```

Add a test that:
1. Creates a table with non-trivial policies (admin_bypass + own_row + role_gated_read)
2. Exports the manifest (`bifrost export --portable <dir>` or the API equivalent)
3. Wipes the table from the DB
4. Imports the manifest
5. Asserts the table is recreated with the same policies (after role-name → role-id resolution)
6. Asserts the role-name rewrite happened on export and reverse on import

- [ ] **Step 3: Visibility-gain Playwright spec**

In `client/e2e/policies-app-realtime.admin.spec.ts`, add a test using a denormalized `user_id` row field instead of the column-mapped `created_by`:

```ts
test("visibility-gain emits insert when row reassigned to user", async ({ page }) => {
  // Create a table with a policy keyed on row.user_id (NOT created_by — that's a column)
  // Bob inserts a row with user_id = bob.id (Alice can't see)
  // Alice subscribes via useTable
  // Admin reassigns: PATCH the row, set data.user_id = alice.id
  // Alice's UI should show the row appear (insert event from visibility-gain)
});
```

Use the same fixtures and structure as the other Playwright specs.

- [ ] **Step 4: Verify**

```bash
./test.sh tests/e2e/platform/test_git_sync_local.py::test_table_policies_round_trip -v
./test.sh client e2e e2e/policies-app-realtime.admin.spec.ts 2>&1 | tail -15
```

- [ ] **Step 5: Commit (one per sub-task or bundled)**

```
test(policies): manifest round-trip e2e + visibility-gain Playwright + browser smoke

- Manifest export → import preserves policies and round-trips role names.
- Playwright covers visibility-gain (row reassignment via denormalized user_id field).
- PolicyEditor smoke captured (screenshots in ~/Sync/Screenshots).
```

---

## Task G: Refresh end-user documentation

**Skill:** `bifrost-documentation`

The `bifrost-integrations-docs` site at `~/GitHub/bifrost-integrations-docs/` contains the public docs that need a Tables / Policies section now that the feature is shipping.

### Steps

- [ ] **Step 1: Run the bifrost-documentation skill**

Invoke `/bifrost-documentation` (or `Skill bifrost-documentation`). Use **diff mode** by default — it will detect what changed in this branch and prompt for re-capture / authoring.

Expected scope of changes:
- New page (or section): "Table policies" — covers the policy AST, default-deny seed, admin_bypass, additive-OR resolution, action scoping, pre-update semantics, the operator vocabulary, and the editor UI
- New page (or section): "Reading and writing tables from apps" — covers `tables.*` SDK + `useTable` hook + the SDK-vs-workflow guidance from `platform-api.md`
- Updated screenshots: PolicyEditor dialog (open with admin_bypass + a row policy visible), Table create dialog showing the Policies section, audit log showing a policy_deny entry (after Task C lands)

The skill knows how to:
- Re-capture screenshots against the dev stack
- Author missing pages (uses `docs/llm.txt` and the bifrost-build skill content as source-of-truth)
- Build the Astro site to verify no broken links / missing image references (per the `feedback_docs_vercel_build.md` memory — Vercel auto-deploys broken builds silently)

- [ ] **Step 2: Verify the Astro build**

```bash
cd ~/GitHub/bifrost-integrations-docs
npm run build 2>&1 | tail -20
```

The pre-push hook (per `reference_pre_push_hook.md` memory) catches missing image refs, but verify locally first.

- [ ] **Step 3: Push docs**

The docs repo deploys via Vercel on push. Push from the `bifrost-integrations-docs` worktree:

```bash
cd ~/GitHub/bifrost-integrations-docs
git push origin <docs-branch>
```

- [ ] **Step 4: Note the docs URL**

Capture the Vercel preview URL from the push output for the merge PR description.

---

## Final verification

After all seven tasks land:

```bash
cd /home/jack/GitHub/bifrost/.worktrees/table-access
./test.sh stack reset
./test.sh unit
./test.sh e2e
cd client && npx vitest run
cd /home/jack/GitHub/bifrost/.worktrees/table-access
./test.sh client e2e e2e/policies-app-direct.admin.spec.ts e2e/policies-app-realtime.admin.spec.ts
```

Expected: all green except the pre-existing `test_package_available_after_installation` flake.

---

# Session findings (2026-05-01) — extras landed + open blocker

## What landed beyond Tasks A–G

Three significant pieces of work landed in the same session as the original 7-task plan. They were prompted by user feedback during execution and ended up tightly coupled to the policy hardening, so they shipped on the same branch:

### Extra 1: Web SDK `scope` parity with Python SDK (commits `15147584`, `a9f187ac`, `8d24f467`)
- 8 document endpoints (`POST/GET/PATCH/DELETE` on `/api/tables/{id}/documents/*`) accept `?scope=<...>` query param via `_resolve_target_org_safe`. Provider admins can target other orgs; non-superusers' scope is silently ignored at this layer (matches Python SDK semantics — confirmed during implementation, NOT a 422).
- `DocumentListResponse.table_id: UUID` added — populated unconditionally in `query_documents`. Lets the web SDK subscribe by UUID after a name-based query.
- `client/src/lib/app-sdk/tables.ts`: every method gains `scope?: string` trailing arg. `withScope(path, scope)` helper handles URL encoding.
- `useTable(name, { scope })` plumbs scope to the snapshot, then subscribes by `snap.table_id` (UUID, sidesteps cross-org name ambiguity).
- 7 new e2e tests in `test_tables.py::TestDocumentScopeQueryParam` covering provider cross-org, name-collision disambiguation, `?scope=global`, and the `table_id` contract.

### Extra 2: Hard org gate at REST and WS surfaces (commits `4eda5a33`, `5361148d`)
**Discovered**: a non-superuser who learned another org's table UUID could reach `_check_action_or_403`, and any permissive policy (e.g. `everyone_read`) would let them read rows. The user's hard rule:

> "If you're not an admin and the org isn't your org or global, you can't access it in any way."

**Fix at REST**: `get_table_or_404` now routes both UUID and name lookups through `OrgScopedRepository.get(id=...)`, whose ID-lookup branch (lines 145–154 of `api/src/repositories/org_scoped.py`) already returns None for non-superuser cross-org access. Removed `is_superuser=True` override and the raw `select(Table).where(Table.id == ...)` bypass.

**Fix at WS**: `_resolve_table_id` in `websocket.py` now takes `user: UserPrincipal`, does the same gate (UUID lookup org-checked; name lookup filtered by `organization_id IN (user_org, NULL)`). Surfaces "Table not found" to avoid cross-org existence leaks.

**Performance fix bundled with the WS gate**: In-process `_table_policy_cache` for `_load_policies_for_table` in `websocket.py`. Was running a fresh DB load + Pydantic validate per subscriber per `document_change` event (O(subs × updates)). Now collapsed to one load per generation per process. Invalidation paths: `policy_changed` events drop entries on processes with active subs; subscribe-time also invalidates to close the staleness window between disconnect and re-subscribe.

**Tests**: `test_non_superuser_cannot_reach_other_org_table_by_uuid` (every CRUD verb × every scope variation = 404), `test_non_superuser_can_reach_global_tables`, `test_subscribe_to_other_org_table_rejected` (WS by name AND UUID).

### Extra 3: Manifest format flatten (commit `d0df4e5b`)
The `.bifrost/tables.yaml` had redundant `policies.policies` nesting (DB JSONB shape leaking into the YAML). Flattened to `policies: [...]` at the manifest level; serializer wraps to `{"policies": [...]}` when writing to `Table.access`, importer unwraps on export. DB JSONB shape unchanged (forward-compat). Removed `ManifestTablePolicies` model.

### Other notable changes
- Removed dead helpers `_get_table_or_404` and `get_or_create_table` from `tables.py` (zero callers).
- Route ordering bug fix: `GET /tables/{id}/documents/count` was being shadowed by `/{doc_id}` and silently 404'd. Reordered with an explicit comment noting the FastAPI binding hazard.

## Final test state at end of session

- Backend platform e2e: **283 passed, 3 skipped, 0 failed**
- Backend full e2e: **1135 passed, 1 known pre-existing flake** (`test_package_available_after_installation`)
- Backend unit: **3347 passed**
- Client unit: **passed** (32 in app-sdk alone)
- pyright: **0 errors / 0 warnings** on API
- ruff: **clean**
- Docs PR: **https://github.com/jackmusick/bifrost-integrations-docs/pull/3**

## OPEN BLOCKER — do NOT merge this branch yet

While building the demo POC at `/tmp/bifrost-poc/` (workflow + app + table), we discovered a fundamental architectural split that blocks shipping: **two parallel, divergent table-document API paths.**

### The split

| Path | Used by | Endpoint prefix | Publishes WS events? | Applies policies? | Audits denial? |
|---|---|---|---|---|---|
| REST | Web SDK / UI | `/api/tables/{id}/documents/*` | ✅ | ✅ | ✅ |
| CLI | Python SDK / workflows | `/api/cli/tables/documents/*` | ❌ | ❌ | ❌ |

The CLI path lives in `api/src/routers/cli.py` lines 2815–3346 (10 endpoints: insert, upsert, get, update, delete, insert/batch, upsert/batch, delete/batch, query, count). It's a parallel reimplementation that:

1. **Does NOT call `publish_document_change`** → WebSocket subscribers (incl. `useTable`) don't see workflow-driven mutations in real time. The visible symptom: clicking "Run workflow" in the demo app inserts rows into the DB, but the live UI doesn't update. Manual REST-driven mutations (UI clicks) work fine.
2. **Does NOT call `_check_action_or_403`** → workflows can read/write any table regardless of the table's policies. This is a security gap: a malicious or buggy workflow can exfiltrate or modify data the calling user wouldn't be authorized to touch via the UI.
3. **Does NOT emit `policy.deny` audit events** → no trail when a policy would have rejected the operation (because no policy is consulted).

This is the same class of drift CLAUDE.md warns about for MCP tools:

> **MCP vs REST routers (existing drift):** the MCP tools for `agents`, `forms`, `tables`, `apps`, `events` re-implement router logic and have diverged (different permission models, missing side effects, divergent validation). See `docs/plans/2026-04-18-mcp-router-reconciliation.md` for the catalog and reconciliation sequence. **New MCP tools must be thin HTTP wrappers that call the REST endpoints**.

The CLI path predates the policy hardening work, but the gap matters more now that policies are the security boundary.

### What the consolidation needs to address

This is a separate workstream — not a 30-line patch — because the auth model is fundamentally different between the two paths.

**Auth model questions:**
- The CLI path uses `CurrentUser` (the workflow's calling user), but workflows often run as system / scheduled / webhook-triggered context. What's "the user" for a scheduled workflow that fires at 3am with no human attached?
- Workflows can be triggered via a form by user A but execute as user A's session — should the policy check use A's identity? What about workflows that intentionally do administrative work that A couldn't do directly (a typical pattern)?
- The Python SDK has a "system trust" model where workflows are admin-curated code that's expected to do whatever its author intended. Is forcing per-row policy checks on workflows the right answer, or do we need a "system context" that bypasses policies (with audit) when the workflow is running as a system actor?
- `tables.set_scope("provider-org")` exists on the Python SDK for provider admins; how does that interact with row-level policies?

**Implementation paths:**

#### Option A: Thin HTTP wrappers (the CLAUDE.md-blessed pattern)
Each CLI handler becomes a forward to the REST endpoint, preserving the auth context. Roughly:

```python
@router.post("/tables/documents/insert")
async def cli_insert_document(request, current_user, db):
    response = await http_bridge.post(
        f"/api/tables/{request.table}/documents",
        body={"data": request.data, "id": request.id},
        params={"scope": request.scope} if request.scope else {},
        user=current_user,
    )
    return response
```

Pros: zero divergence by construction; both paths share policy + WS publish + audit.
Cons: forces a "user" context on workflows even when they run as system. Need a clean way to mark workflow execution as a privileged caller (e.g. `X-Bifrost-Workflow-Execution: <id>` header that the policy layer recognizes and either bypasses or applies a workflow-specific policy to).

#### Option B: Shared service module
Extract document mutation logic into `api/shared/document_service.py`. Both REST router and CLI router call into it. Service handles policy checks, WS publish, and audit; routers just translate request/response shapes.

Pros: keeps performance characteristics in-process (no internal HTTP hop); easier to thread workflow-execution context as a typed param.
Cons: more upfront refactor; two callers means policy/audit drift can recur if a future contributor only touches one router.

**Recommended: A.** Matches the project's existing direction (MCP reconciliation plan) and makes drift structurally impossible.

### Sub-tasks to plan in the new session

1. **Inventory** — confirm exact list of CLI table endpoints + which the Python SDK actually calls (some batch variants may be CLI-only). Confirm whether MCP tools (`api/src/services/mcp_server/tools/tables.py`) is a third path that needs the same treatment.
2. **Auth design decision** — system-context vs. forced-user semantics for workflow execution. Brainstorm.
3. **HTTP bridge or service module** — pick A vs. B.
4. **Implement** — replace each CLI handler with the chosen pattern. Each replacement is testable in isolation.
5. **Policy enforcement on workflows** — write the test matrix: a workflow that tries to read/write a table whose policies don't allow its calling user. Decide enforcement vs. bypass-with-audit.
6. **WS publish on workflow mutations** — verify the demo POC's Run-workflow button now drives live updates in the UI.
7. **Audit on workflow-driven denial** — verify `policy.deny` rows appear with correct actor (the workflow's calling user, not the platform admin).
8. **MCP tools** — fold into the same consolidation if the same gap exists there.
9. **Performance check** — internal HTTP hops add latency; if it's a problem in the workflow-write hot path, fall back to Option B for the in-process call.
10. **Tests** — every test that uses `bifrost.tables.*` from a workflow path needs a sibling assertion that the policy was checked AND the WS event fired. The demo POC at `/tmp/bifrost-poc/` is a good integration check.

### Demo POC artifacts (sanity-check substrate for the new branch)

Files at `/tmp/bifrost-poc/`:
- `apps/progress-demo/_layout.tsx` and `pages/index.tsx` — uses `useTable` + `useWorkflowMutation`
- `workflows/progress_demo.py` — workflow that inserts 5 rows over 5 seconds via `bifrost.tables.insert`
- `policies.yaml`, `schema.yaml`, `create_table.json`

In the current debug stack the demo:
- ✅ Renders correctly
- ✅ Run-workflow button triggers the workflow and writes 5 rows
- ❌ Rows do NOT appear live (the bug — CLI path doesn't publish)
- ❌ Workflow can write to any table regardless of policy (the security gap — CLI path doesn't enforce)

Once the CLI/REST consolidation lands, the same POC should show all rows arriving live as the workflow runs, AND a workflow attempting to write to a denying table should fail with 403 + audit row.

### What to do with this branch in the meantime

The hardening on this branch (Tasks A–G + Extras 1–3) is correct for the REST/UI surface. None of it regresses anything. But it can't ship to main until the CLI path also enforces — otherwise we'd be in a state where the policy engine is rigorously enforced for one code path and silently bypassed for another, which is worse than uniformly enforced everywhere or uniformly absent everywhere.

Branch state: 21 commits on `feat/table-access`, all tests green, ready for the user to either:
- Hold the branch and start the consolidation work in a fresh session, OR
- Cherry-pick the manifest format flatten + e2e test additions onto a parallel branch if those are wanted independently of the CLI consolidation.

Then proceed to `superpowers:finishing-a-development-branch`.

---

# Direction for finishing this branch (added 2026-05-02)

After tracing the CLI/REST split end-to-end and walking through several rejected design directions, the path forward for this branch is documented in **`docs/org-scoping-and-table-endpoints.md`** at the repo root. That doc is the source of truth for what changes; this plan is preserved as historical record of how the branch got here.

Highlights of what landed on the framing:

- **The engine identity stays as-is.** Nothing about `authenticate_engine`, the engine-superuser token, or the SDK's bearer credentials changes. The user's framing: workflow execution is a controlled environment, an admin wrote the workflow, the engine has authority to do what workflows need.
- **The consolidation is the only refactor.** Ten CLI table-document handlers get deleted; SDK is repointed at REST. Two small REST additions (engine/superuser-gated `created_by`/`updated_by` body fields, explicit upsert verb), one removal (`Table.application_id` column), one validation fix (`_get_cli_org_id` UUID validation).
- **Eight known scoping divergences are catalogued in the doc** as refactor candidates for future sessions. None are addressed here. The doc names them with file:line so a future session can pick any subset without re-tracing.
- **Approaches considered and rejected** are also captured in the doc — engine-token rescoping, on-behalf-of header, per-workflow capability sets, per-workflow `bypass_caller_auth` flag, four-marker FastAPI dependency scheme. A future session that proposes any of these has a place to read why they were ruled out.

**Plan stack for this branch** (from `docs/org-scoping-and-table-endpoints.md`):

1. Add `created_by` / `updated_by` to `DocumentCreate` / `DocumentUpdate`. 403 if present and caller isn't engine or superuser.
2. Add `POST /api/tables/{id}/documents/upsert` to REST.
3. Move auto-create-on-insert into the Python SDK (404 → POST `/api/tables` → retry).
4. Repoint Python SDK's `tables.documents.*` methods at REST URLs.
5. Delete CLI table-document handlers (cli.py:2818-3370) + helpers `_find_or_create_table_for_sdk`, `_find_table_for_sdk`.
6. Drop `Table.application_id` column (separate migration).
7. Validate `scope` as UUID/`"global"`/null in `_get_cli_org_id`.

Each step is independently reviewable. Use `superpowers:writing-plans` to expand any step into a task list when ready to implement. After step 5 lands, web UI and SDK share one path; policy/WS/audit happen uniformly. The branch is shippable.
