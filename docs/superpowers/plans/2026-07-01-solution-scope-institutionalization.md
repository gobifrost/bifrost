# Solution Runtime-Scope Institutionalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Solution install-scope derivation a single canonical machine (like org scoping's `OrgScopedRepository` + enforcement test), so no surface can quietly invent a new way to say "which install is calling."

**Architecture:** Auth (`api/src/core/auth.py`) stays the ONLY parser of raw transport signals (`?solution=`, `X-Bifrost-App`). `api/src/services/solution_scope.py` becomes the ONLY consumer API for turning a request context (plus deprecated body-field compat inputs) into an install scope. A mechanical enforcement test (mirroring `test_org_scoping_enforcement.py`) fails the build on any new derivation site. The worker/engine path (scope from the workflow's own DB row) is already correct and gets documented, not changed. A golden Chromium e2e pins the deployed-app contract end-to-end.

**Tech Stack:** FastAPI, SQLAlchemy async, pytest (`./test.sh`), Playwright (`./test.sh client e2e`), React app SDK.

## Global Constraints

- All backend tests run via `./test.sh` from the worktree root (never bare pytest on host).
- TDD: every behavior change lands with a failing test first (pinning tests for already-correct behavior are labeled as such).
- No new fallbacks or dead code; body-field compat inputs are kept ONLY because live SDKs send them, and are documented as deprecated.
- The parked product decisions are OUT OF SCOPE (see "Decision points" at the end): config solution tier, data-fallback `global_repo_access` gating, body-field removal timing.

## Context: current derivation sites (verified 2026-07-01)

| Site | Signal read | Status |
|---|---|---|
| `core/auth.py:317-375` | raw `?solution=` + `X-Bifrost-App` → `ctx.solution_id`/`ctx.app_id` | canonical raw parser — keep |
| `services/solution_scope.py::solution_context_id` | `ctx.solution_id`, `ctx.app_id` fallback | canonical consumer — keep, extend |
| `routers/workflows.py::_derive_solution_scope` (~:700) | ctx + body `solution_id`/`form_id`/`app_id` | re-implementation → fold into service (Task 1) |
| `routers/files.py::_ctx_solution_id` (:298) | `ctx.solution_id` direct parse | duplicate parse → delegate to service (Task 2) |
| `routers/cli.py::cli_create_table` (:2776-2797) | raw `?solution=` + raw header + app→solution query | re-implemented raw parsing → use ctx + service (Task 3) |
| `routers/forms.py:879,:1084` | form's OWN stored `solution_id` | correct (owned scope, not request-derived) — document only |
| worker: `jobs/consumers/workflow_execution.py:570` | workflow row's `solution_id` | correct (execution identity from DB row) — document only |

---

### Task 1: Move workflow-execution scope derivation into `solution_scope.py`

**Files:**
- Modify: `api/src/services/solution_scope.py` (add `derive_execution_solution_scope`)
- Modify: `api/src/routers/workflows.py:700-758` (delete `_derive_solution_scope`, call the service)
- Test: `api/tests/unit/test_execute_solution_scope.py` (repoint imports, keep all cases)
- Test: `api/tests/e2e/platform/test_execute_solution_scope_e2e.py` (repoint imports)

**Interfaces:**
- Produces: `async def derive_execution_solution_scope(db, ctx, *, solution_id: str | None, form_id: str | None, app_id: str | None) -> UUID | None` in `api/src/services/solution_scope.py`. Precedence: `ctx` (via `solution_context_id`) > body `solution_id` > `form_id` > `app_id`.

- [ ] **Step 1: Repoint the unit tests to the new function (failing first)**

In `api/tests/unit/test_execute_solution_scope.py`, replace the import and every call:

```python
from src.services.solution_scope import derive_execution_solution_scope
```

Every existing call `_derive_solution_scope(db, ...)` becomes `derive_execution_solution_scope(db, ctx, solution_id=..., form_id=..., app_id=...)` where `ctx` is `SimpleNamespace(solution_id=None, app_id=None)` for body-only cases. The three ctx tests pass `SimpleNamespace(solution_id=str(sid), app_id=None)` instead of the `ctx_solution_id=` kwarg. Same edit in `test_execute_solution_scope_e2e.py` (its two calls pass `SimpleNamespace(solution_id=None, app_id=None)`).

- [ ] **Step 2: Run to verify failure**

Run: `./test.sh tests/unit/test_execute_solution_scope.py -q`
Expected: FAIL — `ImportError: cannot import name 'derive_execution_solution_scope'`.

- [ ] **Step 3: Implement the service function**

Append to `api/src/services/solution_scope.py` (the `Form` import goes at the top of the function to match the file's lazy-import style; `Application` is already imported at module top):

```python
async def derive_execution_solution_scope(
    db: AsyncSession,
    ctx,
    *,
    solution_id: str | None,
    form_id: str | None,
    app_id: str | None,
) -> UUID | None:
    """Resolve the calling install's scope for workflow execution.

    THE canonical derivation for /api/workflows/execute. Precedence:
    request context (auth already resolved ?solution= / X-Bifrost-App —
    the same signal tables/files scope by) > body solution_id (a Solution
    form/agent that knows its own install) > form_id (Form.solution_id)
    > app_id (Application.solution_id). The body fields are DEPRECATED
    compatibility inputs — live SDKs still send them; removal requires a
    CONTRACT_VERSION bump. A bad/foreign/missing reference yields None →
    no narrowing (the path ref resolves the _repo/ row, or 404s for a
    scoped caller). Each source is client-supplied; the resolver's own
    org gate (cascade scope) prevents a foreign scope from reaching
    another org's workflow.
    """
    from src.models.orm.forms import Form

    ctx_scope = await solution_context_id(db, ctx)
    if ctx_scope is not None:
        return ctx_scope
    if solution_id:
        try:
            return UUID(solution_id)
        except ValueError:
            return None
    if form_id:
        try:
            form_uuid = UUID(form_id)
        except ValueError:
            return None
        return (
            await db.execute(select(Form.solution_id).where(Form.id == form_uuid))
        ).scalar_one_or_none()
    if app_id:
        try:
            app_uuid = UUID(app_id)
        except ValueError:
            return None
        return (
            await db.execute(
                select(Application.solution_id).where(Application.id == app_uuid)
            )
        ).scalar_one_or_none()
    return None
```

Note the ctx branch now goes through `solution_context_id`, which adds the `ctx.app_id` fallback the old router code lacked and drops the old invalid-ctx-falls-through-to-body path (`solution_context_id` returns None on unparseable ctx.solution_id, then body tiers run — same observable behavior; keep the `test_invalid_ctx_solution_id_falls_through_to_body` case, it must still pass).

- [ ] **Step 4: Point the router at it and delete the private copy**

In `api/src/routers/workflows.py`: delete the whole `_derive_solution_scope` function (:700-758) and change the call site (~:813):

```python
    from src.services.solution_scope import derive_execution_solution_scope

    solution_scope = await derive_execution_solution_scope(
        db,
        ctx,
        solution_id=request.solution_id,
        form_id=request.form_id,
        app_id=request.app_id,
    )
```

(Import at the top of the file with the other `src.services` imports if the file's style prefers module-top imports — follow the file.)

- [ ] **Step 5: Run tests**

Run: `./test.sh tests/unit/test_execute_solution_scope.py tests/e2e/platform/test_execute_solution_scope_e2e.py tests/e2e/platform/test_solution_deploy_execution.py -q`
Expected: PASS (all previous cases + ctx precedence + the header-only e2e).

- [ ] **Step 6: Commit**

```bash
git add api/src/services/solution_scope.py api/src/routers/workflows.py api/tests/unit/test_execute_solution_scope.py api/tests/e2e/platform/test_execute_solution_scope_e2e.py
git commit -m "refactor: fold workflow scope derivation into solution_scope service"
```

---

### Task 2: One ctx-parse primitive, files router delegates

**Files:**
- Modify: `api/src/services/solution_scope.py` (add `parse_ctx_solution_id`, use it in `solution_context_id` and `file_read_tiers`)
- Modify: `api/src/routers/files.py:298-307` (`_ctx_solution_id` delegates)
- Test: `api/tests/unit/test_execute_solution_scope.py` (add parse cases)

**Interfaces:**
- Produces: `def parse_ctx_solution_id(ctx) -> UUID | None` in `api/src/services/solution_scope.py` — sync, no DB; returns the parsed `ctx.solution_id` or None.

- [ ] **Step 1: Failing test**

```python
class TestParseCtxSolutionId:
    def test_parses_valid_uuid(self):
        from src.services.solution_scope import parse_ctx_solution_id
        sid = uuid4()
        assert parse_ctx_solution_id(SimpleNamespace(solution_id=str(sid))) == sid

    def test_none_and_garbage_yield_none(self):
        from src.services.solution_scope import parse_ctx_solution_id
        assert parse_ctx_solution_id(SimpleNamespace(solution_id=None)) is None
        assert parse_ctx_solution_id(SimpleNamespace(solution_id="nope")) is None
```

Run: `./test.sh tests/unit/test_execute_solution_scope.py -q` → FAIL (ImportError).

- [ ] **Step 2: Implement + delegate**

In `solution_scope.py`:

```python
def parse_ctx_solution_id(ctx) -> UUID | None:
    """Parse ``ctx.solution_id`` (set by auth) into a UUID, or None.

    THE single parse point — routers must not re-implement this
    (test_solution_scope_enforcement.py)."""
    raw = getattr(ctx, "solution_id", None)
    if raw is None:
        return None
    try:
        return UUID(str(raw))
    except (ValueError, AttributeError, TypeError):
        return None
```

Use it inside `solution_context_id` (replace its inline try/except block) and inside `file_read_tiers` (replace `UUID(str(ctx.solution_id))` at the solution-tier branch — note `file_read_tiers` checks `ctx.solution_id is None` first; keep that flow, just parse via the primitive and return `[]` if parse fails).

In `files.py`, `_ctx_solution_id` becomes:

```python
def _ctx_solution_id(ctx: Context, location: str) -> UUID | None:
    """Install UUID from context (canonical parse in solution_scope)."""
    return parse_ctx_solution_id(ctx)
```

(Keep the `location` parameter only if call sites pass it — they do; do not change 10 call sites in this task.)

- [ ] **Step 3: Run files + scope tests**

Run: `./test.sh tests/unit/test_execute_solution_scope.py tests/unit/test_files_sdk_solution_scope.py tests/e2e/platform/test_solution_file_scope.py -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add api/src/services/solution_scope.py api/src/routers/files.py api/tests/unit/test_execute_solution_scope.py
git commit -m "refactor: single parse primitive for ctx.solution_id"
```

---

### Task 3: cli_create_table guard uses the canonical helper

**Files:**
- Modify: `api/src/routers/cli.py:2776-2797`
- Test: `api/tests/unit/test_cli_sdk_table_create_guard.py` (new; or extend the existing test file covering cli_create_table — search `grep -rln "cli_create_table\|Tables must be declared" api/tests/` first and extend that file if one exists)

- [ ] **Step 1: Find/write the failing test**

The guard's behavior: creating a table from a solution execution context (`?solution=` or app header) is refused 404. Pin it through the canonical path — construct the request with `?solution=<active install id>`; assert 404 `"Tables must be declared by the solution manifest"`. If an existing e2e covers this (`grep -rn "must be declared" api/tests/`), extend it with an `X-Bifrost-App` variant; else add an e2e in `tests/e2e/platform/test_cli_solution_files.py`'s style. The new assertion to add BEFORE refactoring (should already pass — pinning): both signals refuse. Then refactor and re-run.

- [ ] **Step 2: Refactor the guard**

Replace the raw parsing in `cli_create_table` (`raw_request.query_params.get("solution")` + `raw_request.headers.get("X-Bifrost-App")` + inline `Application.solution_id` query) with the context-derived check:

```python
async def cli_create_table(
    request: SDKTableCreateRequest,
    ctx: Context,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> SDKTableInfo:
    """Create a new table via SDK."""
    from src.services.solution_scope import solution_context_id
    from src.models.orm.tables import Table

    if await solution_context_id(db, ctx) is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tables must be declared by the solution manifest",
        )
    ...  # rest unchanged
```

Behavior delta to verify intentionally: `get_execution_context` 409s an INACTIVE install's `?solution=`/header before the guard runs (previously the raw parse let an inactive-install call through to the 404 guard). 409 vs 404 for an inactive install is acceptable — both refuse; note it in the commit message. Check `Context` is imported in cli.py (it is used elsewhere in the file — verify with grep, add import if not).

- [ ] **Step 3: Run**

Run: `./test.sh tests/e2e/platform/test_cli_solution_files.py <the-guard-test-file> -q` → PASS.

- [ ] **Step 4: Commit**

```bash
git add api/src/routers/cli.py api/tests/
git commit -m "refactor: cli table-create solution guard via canonical scope helper"
```

---

### Task 4: Mechanical enforcement test

**Files:**
- Create: `api/tests/unit/test_solution_scope_enforcement.py` (mirror `api/tests/unit/test_org_scoping_enforcement.py`'s structure: regex scan + content-keyed allow-list)

- [ ] **Step 1: Write the test (fails if any rule is violated — write it, run it, fix any stragglers it finds)**

```python
"""Mechanical enforcement of the Solution runtime-scope pattern.

Three rules (see api/src/repositories/README.md, "How the install id
reaches the resolver"):

1. Only core/auth.py parses the raw transport signals: the literal
   header name "X-Bifrost-App" and the ?solution= query param must not
   be read anywhere else under api/src (comments/docstrings exempt via
   allow-list).
2. Only services/solution_scope.py and core/auth.py may map
   Application.solution_id (app -> install) for scope purposes.
3. Routers must not re-implement ctx.solution_id parsing: the pattern
   `UUID(str(ctx.solution_id))` / `UUID(str(<x>.solution_id))` is owned
   by solution_scope.parse_ctx_solution_id.

Allow-lists are content-keyed with a justification comment each.
"""
from __future__ import annotations

import re
from pathlib import Path

API_SRC = Path(__file__).resolve().parents[2] / "src"

_RAW_HEADER_RE = re.compile(r"""headers\.get\(\s*['"]X-Bifrost-App['"]""")
_RAW_QUERY_RE = re.compile(r"""query_params\.get\(\s*['"]solution['"]""")
_APP_TO_SOLUTION_RE = re.compile(r"select\(\s*Application\.solution_id\s*\)")
_CTX_PARSE_RE = re.compile(r"UUID\(\s*str\(\s*ctx\.solution_id\s*\)\s*\)")

_ALLOWED_FILES_RAW = {
    Path("core/auth.py"),  # THE raw-signal parser
}
_ALLOWED_FILES_APP_MAP = {
    Path("core/auth.py"),  # header gate maps app -> solution for the active-install check
    Path("services/solution_scope.py"),  # canonical consumer API
}


def _scan(pattern: re.Pattern, allowed: set[Path]) -> list[str]:
    violations = []
    for py in API_SRC.rglob("*.py"):
        rel = py.relative_to(API_SRC)
        if rel in allowed:
            continue
        for i, line in enumerate(py.read_text().splitlines(), 1):
            if pattern.search(line):
                violations.append(f"{rel}:{i}: {line.strip()}")
    return violations


def test_only_auth_reads_raw_solution_signals():
    v = _scan(_RAW_HEADER_RE, _ALLOWED_FILES_RAW) + _scan(_RAW_QUERY_RE, _ALLOWED_FILES_RAW)
    assert not v, (
        "Raw ?solution= / X-Bifrost-App parsing outside core/auth.py — "
        "use ctx.solution_id via services/solution_scope.py:\n" + "\n".join(v)
    )


def test_app_to_solution_mapping_is_canonical():
    v = _scan(_APP_TO_SOLUTION_RE, _ALLOWED_FILES_APP_MAP)
    assert not v, (
        "Application.solution_id scope mapping outside the canonical sites — "
        "use solution_context_id / derive_execution_solution_scope:\n" + "\n".join(v)
    )


def test_ctx_solution_id_parse_is_canonical():
    allowed = {Path("services/solution_scope.py")}  # parse_ctx_solution_id lives here
    v = _scan(_CTX_PARSE_RE, allowed)
    assert not v, (
        "Inline UUID(str(ctx.solution_id)) parse — use "
        "solution_scope.parse_ctx_solution_id:\n" + "\n".join(v)
    )
```

- [ ] **Step 2: Run it; fix any real violations it finds**

Run: `./test.sh tests/unit/test_solution_scope_enforcement.py -q`
Expected after Tasks 1–3: PASS. If it finds a site the earlier tasks missed, fix that site the same way (delegate to the service) — do NOT allow-list working code without a structural reason.

- [ ] **Step 3: Commit**

```bash
git add api/tests/unit/test_solution_scope_enforcement.py
git commit -m "test: enforce canonical solution-scope derivation"
```

---

### Task 5: README contract section

**Files:**
- Modify: `api/src/repositories/README.md` (inside the "Solutions: first-stop resolution" section, after "### 2. Entity reads (the data side)")

- [ ] **Step 1: Add the derivation contract subsection**

```markdown
### How the install id is DERIVED (the request side)

Symmetric to gate 1's `resolve_effective_scope` for org scope, install
scope has exactly one derivation chain — enforced by
`tests/unit/test_solution_scope_enforcement.py`:

- **`core/auth.py` is the ONLY parser of raw transport signals.** It reads
  `?solution=` and `X-Bifrost-App`, validates them (UUID shape, active
  install, header/param agreement), and sets `ctx.solution_id` /
  `ctx.app_id` on the request's ExecutionContext.
- **`services/solution_scope.py` is the ONLY consumer API.**
  `parse_ctx_solution_id(ctx)` parses the context value;
  `solution_context_id(db, ctx)` adds the app→install fallback;
  `derive_execution_solution_scope(db, ctx, ...)` adds workflow-execute's
  deprecated body-field compat tiers (body `solution_id` > `form_id` >
  `app_id`). Routers call these; they never parse signals themselves.
- **Body fields on /api/workflows/execute are DEPRECATED compat.** Live
  SDKs still send them; removing them is a CONTRACT_VERSION bump.
- **The worker path derives scope from the workflow's own DB row**
  (`jobs/consumers/workflow_execution.py` → `workflow_data["solution_id"]`),
  NOT from request signals — execution identity is the row's, by design.
  Forms likewise use their own stored `form.solution_id` (owned scope).
```

- [ ] **Step 2: Commit**

```bash
git add api/src/repositories/README.md
git commit -m "docs: solution scope derivation contract"
```

---

### Task 6: Negative e2e — foreign header cannot cross org scope

**Files:**
- Test: `api/tests/e2e/platform/test_solution_deploy_execution.py` (append)

This is a PINNING test (expected to pass — the resolver's org gate already blocks it). It exists so the ctx-first change can never silently relax the org boundary.

- [ ] **Step 1: Write the test**

```python
def test_foreign_app_header_cannot_reach_other_orgs_workflow(
    e2e_client, platform_admin, org_user_factory
):
    """A user from another org smuggling org A's X-Bifrost-App must NOT
    resolve org A's install workflow (the resolver's org gate holds under
    ctx-first scoping)."""
    headers = platform_admin.headers
    app_a = _deploy_install_with_app(e2e_client, headers, "xorg")

    outsider = org_user_factory()  # regular user in a different org
    resp = e2e_client.post(
        "/api/workflows/execute",
        headers={**outsider.headers, "X-Bifrost-App": app_a},
        json={"workflow_id": "workflows/main.py::main", "sync": True},
    )
    assert resp.status_code in (403, 404), resp.text
```

Check the actual fixture name for "regular user in another org" first: `grep -rn "def org_user_factory\|def regular_user\|def second_org" api/tests/e2e/ | head`. Use whatever exists (e.g. a `create_user_in_org` helper); if the platform's global-scope solutions make org gating vacuous for `scope: "global"` installs, deploy the fixture solution with an org-bound scope instead (`"scope": "organization"` + the org id) so the boundary is real.

- [ ] **Step 2: Run**

Run: `./test.sh tests/e2e/platform/test_solution_deploy_execution.py -q` → PASS. If it FAILS (cross-org leak), STOP — that is a security finding; report before continuing.

- [ ] **Step 3: Commit**

```bash
git add api/tests/e2e/platform/test_solution_deploy_execution.py
git commit -m "test: pin org gate under header-derived solution scope"
```

---

### Task 7: Golden Chromium e2e — deployed app contract

**Files:**
- Create: `client/e2e/solution-runtime-contract.admin.spec.ts`

The fixture app is a hand-written ES module (no Vite build): the shell reads the `<script type="module" src>` from dist `index.html` (`app_code_files.py::get_bundle_manifest`, standalone_v2 branch), imports it, and the module reads `window.__BIFROST_APP__` (set before import). The module exercises the THREE data planes with the header contract only — no body `app_id`, no UUIDs.

- [ ] **Step 1: Write the spec**

```typescript
/**
 * Golden deployed-app runtime-scope contract (Chromium, real deploy).
 *
 * Deploys a Solution (workflow + table + file location) with a
 * standalone_v2 app whose entry module uses ONLY the transport contract
 * (Authorization + X-Bifrost-App; portable path::fn ref; no UUIDs, no
 * body app_id) and asserts workflow, table, and file access all resolve
 * the install's own resources in a real browser.
 */
import { test, expect } from "@playwright/test";

const SLUG = `runtime-contract-${Date.now().toString(36)}`;

// The app entry: reads the bootstrap, exercises workflow/table/file with
// header-only scoping, renders one marker per plane.
const ENTRY_JS = `
const boot = window.__BIFROST_APP__;
const el = boot.mountEl;
const h = { Authorization: "Bearer " + boot.token, "X-Bifrost-App": boot.appId, "Content-Type": "application/json" };
const mark = (id, txt) => { const d = document.createElement("div"); d.dataset.testid = id; d.textContent = txt; el.appendChild(d); };
(async () => {
  try {
    const wf = await fetch(boot.baseUrl + "/api/workflows/execute", { method: "POST", headers: h, body: JSON.stringify({ workflow_id: "workflows/runtime.py::main", sync: true }) }).then(r => r.json());
    mark("workflow-result", wf.result && wf.result.marker || "FAIL:" + JSON.stringify(wf).slice(0, 200));
    const rows = await fetch(boot.baseUrl + "/api/tables/runtime_items/documents", { headers: h }).then(r => r.json());
    mark("table-result", Array.isArray(rows.documents) ? "rows:" + rows.documents.length : "FAIL:" + JSON.stringify(rows).slice(0, 200));
    await fetch(boot.baseUrl + "/api/files/write", { method: "POST", headers: h, body: JSON.stringify({ path: "probe.txt", content: btoa("ok"), location: "docs" }) });
    const rd = await fetch(boot.baseUrl + "/api/files/read", { method: "POST", headers: h, body: JSON.stringify({ path: "probe.txt", location: "docs" }) });
    mark("file-result", rd.ok ? "ok" : "FAIL:" + rd.status);
  } catch (e) { mark("fatal", String(e)); }
})();
`;

test.describe("deployed solution runtime contract", () => {
  test("workflow/table/file resolve via header contract in the browser", async ({ page, request }) => {
    // No UUID workaround: the fixture must not embed any UUID.
    expect(ENTRY_JS).not.toMatch(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i);

    // --- deploy via API (same JSON-bundle path as the backend e2e) ---
    const sol = await request.post("/api/solutions", { data: { slug: SLUG, name: SLUG, scope: "global", global_repo_access: false } });
    expect(sol.ok()).toBeTruthy();
    const sid = (await sol.json()).id;

    const dep = await request.post(`/api/solutions/${sid}/deploy`, { data: {
      python_files: { "workflows/runtime.py": [
        "from bifrost import workflow, tables",
        "", "@workflow", "async def main():",
        "    await tables.insert('runtime_items', {'k': 'v'})",
        "    return {'marker': 'golden'}", ""].join("\n") },
      workflows: [{ id: crypto.randomUUID(), name: `runtime_${SLUG}`, function_name: "main", path: "workflows/runtime.py", type: "workflow" }],
      tables: [{ id: crypto.randomUUID(), name: "runtime_items", definition: { columns: [{ name: "k" }] } }],
      file_locations: ["docs"],
      apps: [{ id: crypto.randomUUID(), slug: `app-${SLUG}`, name: "RC", app_model: "standalone_v2", dependencies: {}, access_level: "authenticated",
        dist_files: { "index.html": `<!doctype html><div id="root"></div><script type="module" src="/dist/main.js"></script>`, "main.js": ENTRY_JS } }],
    }});
    expect([200, 201, 202]).toContain(dep.status());
    if (dep.status() === 202) {
      const jobId = (await dep.json()).deploy_job_id;
      await expect.poll(async () => (await (await request.get(`/api/solutions/deploy-jobs/${jobId}`)).json()).status, { timeout: 30000 }).toBe("succeeded");
    }

    // --- drive the deployed app ---
    await page.goto(`/apps/app-${SLUG}`);
    await expect(page.getByTestId("workflow-result")).toHaveText("golden", { timeout: 20000 });
    await expect(page.getByTestId("table-result")).toHaveText(/rows:[1-9]/);
    await expect(page.getByTestId("file-result")).toHaveText("ok");
  });
});
```

Adjust to reality while implementing (the spec above is the shape, verify each endpoint contract as you go): (a) the deploy bundle key names for tables/file locations — mirror `api/tests/unit/test_solution_table_deploy.py::_table_entry` and `test_solution_file_locations.py`; (b) the table-documents read route + response shape (`client/src/lib/app-sdk/tables.ts` uses base `/api/tables`); (c) the files write/read request shape (`client/src/lib/app-sdk/files.ts:250,:214` — copy the exact body fields it sends, including how content is encoded); (d) how `request` inherits admin auth from the Playwright admin fixture (see `client/e2e/fixtures/auth-fixture.ts` and how sibling `.admin.spec.ts` files make API calls); (e) file write authorization relies on the #427 seeded solution-scoped `admin_bypass` policy — the admin fixture user is a platform admin so this passes. If sdk table insert route differs for the workflow body, check `api/bifrost/tables.py` for the SDK call shape (`tables.insert` may be `sdk.tables.get(...).insert(...)` — copy a working workflow from an existing e2e fixture, e.g. `test_solution_table_e2e.py`).

- [ ] **Step 2: Run it**

Run: `./test.sh client e2e e2e/solution-runtime-contract.admin.spec.ts`
Expected: PASS with all three markers.

- [ ] **Step 3: Commit**

```bash
git add client/e2e/solution-runtime-contract.admin.spec.ts
git commit -m "test: golden deployed-app runtime-scope contract in Chromium"
```

---

### Task 8: Full verification + drive matrix (manual, evidence required)

**Files:** none (verification). Debug stack is up at the URL from `./debug.sh status`; scratch CLI venv at `/tmp/bifrost-cli-runtime-scope`.

- [ ] **Step 1: Full suites**

```bash
./test.sh all              # expect: 0 failures
./test.sh quality api      # expect: 0 errors
(cd client && npm run tsc && npm run lint)
./test.sh client unit
./test.sh client e2e
```

- [ ] **Step 2: Drive matrix against the live debug stack** (each row needs observed evidence, not inference)

| Angle | How | Expect |
|---|---|---|
| Deployed app, in-platform | Deploy the Task-7-style solution via CLI (`bifrost solution deploy` from a scratch workspace); open `/apps/<slug>` in Chrome (claude-in-chrome); check all three markers | workflow marker `golden`, table rows, file `ok` |
| Local dev, out-of-platform | Same workspace, `bifrost solution start`; open the local URL in Chrome | same three markers via the local proxy (also proves accept-encoding fix live) |
| CLI portable ref | `bifrost run "workflows/runtime.py::main"` bound to the install | executes install's own workflow |
| Form-triggered (engine path) | Create a form bound to the solution workflow; submit in the platform UI | run succeeds; workflow's `tables.insert` hits the install's own table |
| Negative | From a second non-admin user in another org, curl `/api/workflows/execute` with the app's `X-Bifrost-App` | 403/404, never another org's data |

- [ ] **Step 3: Record results** — append a "Drive evidence" section to this plan file with per-row outcomes, then commit.

---

## Drive evidence (2026-07-02, debug stack + live browser)

Fixture: solution `drive-rc` (global install `7cee7e28`), workflow
`functions/hello.py::main` (upserts into own table `drive_items`, returns a
marker), table + `docs` file location + `Drive Form`, standalone_v2 app
`drive-app` (scaffolded via CLI, real React SDK: `useWorkflowQuery` portable
ref + `tables.query` + `files.write/read`).

| Angle | How | Result |
|---|---|---|
| Deployed app, in-platform | `bifrost solution deploy` → `/apps/drive-app` in the user's Chrome | PASS: `golden-drive` / `rows:1` / `ok` |
| Local dev, out-of-platform | `bifrost solution start` → headless Chromium on the proxy origin | FAIL before fix (table 404, file 403, workflow silently ran the DEPLOYED copy) → **fixed** (`_vite_child_env` routes the bundle through the proxy) → PASS: workflow runs in-process, `rows:1`, file `ok` |
| CLI portable ref | `bifrost workflows execute functions/hello.py::main` from the bound workspace | PASS: resolved the install's own workflow (`solution_id=7cee7e28`) |
| Form-triggered (engine) | `Drive Form` submitted in the platform UI | PASS: Completed, result marker `golden-drive`, 162ms |
| Negative | e2e `test_foreign_app_header_cannot_reach_other_orgs_workflow` (org2 user + org1 app header) + live unauthenticated curl with header | PASS: e2e green in full suite; live 401 |

Drive findings fixed during the drive:
- **`solution start` bundle bypassed the proxy** (BIFROST_API_URL pointed at
  the upstream) — install tables 404'd, file writes 403'd, and local
  workflow edits silently executed the deployed copy. Fixed +
  `test_solution_start_env.py`.

Drive findings recorded, NOT yet fixed:
- `scaffold-app` bakes `http://localhost:8000` into `package.json` when
  `BIFROST_API_URL` is absent from the workspace env at scaffold time — it
  should fall back to the authenticated client's URL.
- Local dev sends the MANIFEST app id (`VITE_BIFROST_APP_ID`, proxy
  `X-Bifrost-App`); the deployed row id is uuid5-remapped, so the id never
  resolves server-side. Harmless for scope now (the proxy's `?solution=` +
  ctx-first execution carry it), but cosmetically wrong (logo 404s) and a
  trap if anything ever trusts it. Consider resolving the deployed id via
  `/api/solutions/{id}/entities` at start.
- Unscoped path-ref courtesy fallback (no `_repo` row + exactly one visible
  solution row → resolves it) let the pre-fix local drive "work" for
  workflows while tables/files failed — deliberate and documented in the
  resolver, but it masks scope-loss bugs; the diagnostics task (plan Task 5
  of the original Codex plan) would make such degradation visible.

## Completeness audit (2026-07-02): which surfaces actually have an install tier

The work above institutionalized request-side derivation for the surfaces
that HAD install tiers. A full audit of every entity type a Solution ships
or touches found four data planes with NO install tier at all — the values
live in a shared org/global namespace and the resolvers never narrow:

| Surface | Ships in bundle? | Runtime install-scoped? | Gap |
|---|---|---|---|
| Workflow bodies (tables/files SDK) | yes | **yes** — worker re-derives from the workflow row (`execution/service.py` → consumer → engine ctx) for ALL callers (forms, agents' tools, events, schedules) | none |
| Events → solution workflow | yes | **yes** (via workflow row) | none |
| Forms | yes | **yes** (`form.solution_id` as resolution scope) | none |
| MCP/CLI authoring | n/a | deliberately `solution_id IS NULL`-only | by design |
| **Config VALUES** | schema only (`SolutionConfigSchema`) | **NO** — `Config` has no `solution_id`; SDK client (`api/bifrost/config.py`) never sends `?solution=`; `cli_get_config` → `merged_for_sdk` org/global only; Setup matches by `(org, key)` | two installs (or two solutions) sharing a key in one org silently share/clobber ONE value row |
| **Knowledge** | no | **NO** — no `solution_id` on `KnowledgeStore`; org+global fallback; agent search runs at org scope | namespaces collide across installs; solutions can't own a corpus |
| **Connections/Integrations** | declaration only (`SolutionConnectionSchema`) | **NO** — `integrations.get(name)` resolves by NAME at org/global; the install id is used ONLY for the declared-but-unset 424 | two solutions expecting different "CRM" bindings collide on the name |
| **OAuth tokens** | no | **NO** — provider-keyed org→global cascade | all installs share the provider token |
| **Agents (agent-LEVEL access)** | yes (`Agent.solution_id`) | **partial** — tool workflows scope via their own rows, but `agent.solution_id` never enters the run context (`agent_runs.py` enqueue, `consumers/agent_run.py`); agent-level SDK access (knowledge search) runs org-scoped; asymmetric with forms | a solution agent's own-surface reads are unscoped; a mis-bound foreign tool workflow runs at the foreign scope |

Follow-on work (largest first): config value install tier (schema migration
`Config.solution_id` + own-first tier in `merged_for_sdk`/`cli_get_config` +
SDK client sends `?solution=` + Setup stamps the install + uninstall
lifecycle + enforcement-test extension), connection per-install binding,
agent-level scope propagation, knowledge install namespace (product call).

## Decision points for Jack (explicitly NOT decided by this plan)

1. **Config solution tier** — `Config` has no `solution_id` column; a solution workflow reading config today gets the plain org/global cascade. Giving configs the own-first tier tables/files have = schema migration + product semantics. Related to the parked data-fallback question.
2. **Data-fallback gating** — `global_repo_access` gates code only; a "sealed" install can still read `_repo/` tables/configs by name (README "Open question"). Options documented in `docs/superpowers/specs/2026-06-08-solution-workflow-resolution-chokepoint-design.md`.
3. **Body-field removal** — `solution_id`/`form_id`/`app_id` on `/api/workflows/execute` stay until forms/agents carry context signals; removal is a CONTRACT_VERSION bump.
