# Table Access Rules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let apps call `/api/tables/*` directly from the browser (REST + push), gated by per-table access rules and exposed via a TypeScript SDK that mirrors the Python workflow tables surface.

**Architecture:** Add a `Table.access` JSONB block (Everyone / Role / Creator scopes × CRUD). A pure-function checker enforces it on REST writes and websocket subscribes. Workflow SDK auto-resolves `created_by` from `context.user_id`. A new web SDK (`client/src/lib/app-sdk/tables.ts`) gives apps a typed `tables.*` API one-for-one with the Python surface, plus `tables.subscribe()` over the existing `/ws/connect` channel system.

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy / Alembic / Pydantic v2 (backend), TypeScript / React / Vite / openapi-react-query / Vitest / Playwright (frontend), Postgres JSONB, Redis pub/sub, FastAPI `WebSocket` router.

**Spec:** `docs/superpowers/specs/2026-04-29-table-access-rules-design.md`

---

## File map

| File | Purpose |
|---|---|
| `api/alembic/versions/20260429_add_table_access.py` | Add `access JSONB NULL` to `tables` |
| `api/src/models/orm/tables.py` | Add `access` column to `Table` ORM |
| `api/src/models/contracts/tables.py` | New Pydantic models; expose `access` on Table contracts |
| `api/shared/table_access.py` | Pure-function `TableAccessChecker` |
| `api/tests/unit/test_table_access.py` | Exhaustive checker unit tests |
| `api/src/routers/tables.py` | Relax document endpoints; call checker; populate created_by; publish |
| `api/src/core/pubsub.py` | `publish_document_change`, `publish_table_access_changed` |
| `api/src/routers/websocket.py` | `table:{id}` channel auth + Creator filter + revocation |
| `api/bifrost/tables.py` | SDK auto-resolves created_by/updated_by from execution context |
| `api/bifrost/manifest.py` | `ManifestTable.access` field |
| `api/src/services/manifest_generator.py` | Serialize `Table.access` |
| `api/src/services/manifest_import.py` | `_resolve_table` upserts `access` |
| `api/bifrost/portable.py` | Role-id ↔ role-name rewrite for tables |
| `api/bifrost/dto_flags.py` | `TableUpdate.access` parity |
| `api/tests/unit/test_manifest.py` | Round-trip Table.access |
| `api/tests/unit/test_pubsub_table_changes.py` | Envelope tests |
| `api/tests/unit/test_dto_flags.py` | Updated for new field |
| `api/tests/e2e/platform/test_table_access.py` | REST matrix |
| `api/tests/e2e/platform/test_tables.py` | Default-deny update |
| `api/tests/e2e/platform/test_table_subscriptions.py` | Websocket matrix |
| `client/src/lib/app-sdk/tables.ts` | Web SDK |
| `client/src/lib/app-sdk/use-table-subscription.ts` | React hook |
| `client/src/lib/app-sdk/ws-client.ts` | Thin ws client |
| `client/src/lib/app-sdk/tables.test.ts` | SDK unit tests |
| `client/src/lib/app-sdk/use-table-subscription.test.tsx` | Hook tests |
| `client/src/lib/app-code-runtime.ts` | Inject SDK into platform scope |
| `client/src/lib/app-code-platform.d.ts` | Type declarations |
| `client/src/components/tables/TableAccessEditor.tsx` | Admin editor |
| `client/src/components/tables/TableAccessEditor.test.tsx` | Editor unit |
| `client/e2e/tables-app-direct.spec.ts` | Playwright: SDK round-trip |
| `client/e2e/tables-subscription.spec.ts` | Playwright: subscribe push |

---

## Pre-flight

- [ ] **Boot the dev and test stacks for this worktree.**

```bash
./debug.sh
./test.sh stack up
```

Expected: dev stack URL printed; test stack reports `UP`. Required for type-gen and tests throughout.

- [ ] **Confirm migration head.**

```bash
docker compose -p $(./debug.sh status | awk '/Project/ {print $2}') exec api alembic heads
```

Expected output contains `20260428_webhook_rate_limit (head)`. If different, update Task 1's `down_revision` to match.

---

## Task 1: Migration — add `Table.access` column

**Files:**
- Create: `api/alembic/versions/20260429_add_table_access.py`

- [ ] **Step 1: Write the migration**

```python
"""add access JSONB to tables

Revision ID: 20260429_table_access
Revises: 20260428_webhook_rate_limit
Create Date: 2026-04-29 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260429_table_access"
down_revision = "20260428_webhook_rate_limit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tables",
        sa.Column("access", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tables", "access")
```

- [ ] **Step 2: Apply migration in dev stack**

```bash
docker restart $(docker ps --format '{{.Names}}' | grep bifrost-init)
docker logs $(docker ps -a --format '{{.Names}}' | grep bifrost-init) --tail 30
docker restart $(docker ps --format '{{.Names}}' | grep '^bifrost.*-api-1$')
```

Expected: alembic logs `Running upgrade 20260428_webhook_rate_limit -> 20260429_table_access`. API restart succeeds.

- [ ] **Step 3: Verify in Postgres**

```bash
docker compose -p $(./debug.sh status | awk '/Project/ {print $2}') exec postgres \
  psql -U postgres -d bifrost -c "\d tables" | grep -i access
```

Expected: `access | jsonb | | |` row.

- [ ] **Step 4: Commit**

```bash
git add api/alembic/versions/20260429_add_table_access.py
git commit -m "feat(tables): migration adds access JSONB column"
```

---

## Task 2: ORM — `Table.access` mapped column

**Files:**
- Modify: `api/src/models/orm/tables.py:23-72`

- [ ] **Step 1: Add column to `Table` ORM**

In `api/src/models/orm/tables.py`, after line 45 (the existing `schema` column), add:

```python
    access: Mapped[dict | None] = mapped_column(JSONB, default=None)
```

The full Table block should now read (showing context):

```python
    schema: Mapped[dict | None] = mapped_column(JSONB, default=None)
    access: Mapped[dict | None] = mapped_column(JSONB, default=None)
    description: Mapped[str | None] = mapped_column(Text, default=None)
```

- [ ] **Step 2: Restart api to pick up the model change**

Hot reload should pick this up; if not:

```bash
docker restart $(docker ps --format '{{.Names}}' | grep '^bifrost.*-api-1$')
```

- [ ] **Step 3: Verify ORM round-trips**

```bash
./test.sh tests/unit/test_tables_orm.py -v 2>/dev/null || echo "no file yet — proceed"
```

Acceptable: file doesn't exist yet (we add tests in Task 4). The ORM change is verified by the contracts task.

- [ ] **Step 4: Commit**

```bash
git add api/src/models/orm/tables.py
git commit -m "feat(tables): Table.access ORM column"
```

---

## Task 3: Pydantic contracts for `TableAccess`

**Files:**
- Modify: `api/src/models/contracts/tables.py`

- [ ] **Step 1: Add the access models**

Append to `api/src/models/contracts/tables.py` (above the existing `TablePublic` / `TableCreate` / `TableUpdate`):

```python
from uuid import UUID

from pydantic import BaseModel, Field


class TableAccessScopeCRUD(BaseModel):
    """CRUD flags for an access scope."""

    read: bool = False
    create: bool = False
    update: bool = False
    delete: bool = False


class TableAccessRoleScope(TableAccessScopeCRUD):
    """Role scope adds a list of role IDs."""

    roles: list[UUID] = Field(default_factory=list)


class TableAccess(BaseModel):
    """Table-level access rules. NULL on Table = workflow-only."""

    everyone: TableAccessScopeCRUD = Field(default_factory=TableAccessScopeCRUD)
    role: TableAccessRoleScope = Field(default_factory=TableAccessRoleScope)
    creator: TableAccessScopeCRUD = Field(default_factory=TableAccessScopeCRUD)
```

- [ ] **Step 2: Add `access` to `TableCreate`, `TableUpdate`, `TablePublic`**

In the same file, on each of those models, add (the field is optional on all three; `None` = no access rules):

```python
access: TableAccess | None = None
```

- [ ] **Step 3: Regenerate frontend types**

```bash
cd client && npm run generate:types && cd ..
```

Expected: `client/src/lib/v1.d.ts` updated; `git diff client/src/lib/v1.d.ts` shows new `TableAccess`, `TableAccessScopeCRUD`, `TableAccessRoleScope` schemas plus `access` on Table contracts.

- [ ] **Step 4: Type-check**

```bash
cd api && pyright src/models/contracts/tables.py && cd ..
cd client && npm run tsc && cd ..
```

Expected: 0 errors in both.

- [ ] **Step 5: Commit**

```bash
git add api/src/models/contracts/tables.py client/src/lib/v1.d.ts
git commit -m "feat(tables): TableAccess Pydantic contracts + regenerated types"
```

---

## Task 4: Pure checker — `TableAccessChecker`

**Files:**
- Create: `api/shared/table_access.py`
- Create: `api/tests/unit/test_table_access.py`

- [ ] **Step 1: Write the failing test file**

Create `api/tests/unit/test_table_access.py`:

```python
"""Unit tests for the table access checker."""
from __future__ import annotations

from uuid import uuid4

import pytest

from shared.table_access import (
    Action,
    Caller,
    CheckResult,
    WorkflowCaller,
    check_table_access,
)


def _table_access(**overrides):
    base = {
        "everyone": {"read": False, "create": False, "update": False, "delete": False},
        "role": {"roles": [], "read": False, "create": False, "update": False, "delete": False},
        "creator": {"read": False, "create": False, "update": False, "delete": False},
    }
    for scope, flags in overrides.items():
        base[scope] = {**base[scope], **flags}
    return base


def _user(user_id=None, role_ids=None, is_admin=False):
    return Caller(
        user_id=user_id or uuid4(),
        role_ids=set(role_ids or []),
        is_admin=is_admin,
    )


# ---- Admin and workflow always allow ----------------------------------------

@pytest.mark.parametrize("action", list(Action))
def test_admin_allowed_for_every_action_even_with_no_access(action):
    res = check_table_access(action=action, access=None, caller=_user(is_admin=True))
    assert res.allow is True


@pytest.mark.parametrize("action", list(Action))
def test_workflow_caller_allowed_even_with_no_access(action):
    res = check_table_access(action=action, access=None, caller=WorkflowCaller())
    assert res.allow is True


# ---- Default deny ------------------------------------------------------------

@pytest.mark.parametrize("action", list(Action))
def test_no_access_block_denies_non_admin(action):
    res = check_table_access(action=action, access=None, caller=_user())
    assert res.allow is False


@pytest.mark.parametrize("action", list(Action))
def test_empty_access_block_denies_non_admin(action):
    res = check_table_access(action=action, access=_table_access(), caller=_user())
    assert res.allow is False


# ---- Everyone scope ---------------------------------------------------------

@pytest.mark.parametrize("action", list(Action))
def test_everyone_grant_allows(action):
    access = _table_access(everyone={action.value: True})
    res = check_table_access(action=action, access=access, caller=_user())
    assert res.allow is True


# ---- Role scope -------------------------------------------------------------

def test_role_grant_requires_membership():
    role = uuid4()
    access = _table_access(role={"roles": [str(role)], "read": True})
    member = _user(role_ids=[role])
    non_member = _user(role_ids=[])
    assert check_table_access(action=Action.READ, access=access, caller=member).allow is True
    assert check_table_access(action=Action.READ, access=access, caller=non_member).allow is False


# ---- Creator scope ----------------------------------------------------------

def test_creator_grant_only_applies_to_owned_rows():
    user = uuid4()
    access = _table_access(creator={"read": True, "update": True, "delete": True})
    owner = _user(user_id=user)
    assert check_table_access(
        action=Action.READ, access=access, caller=owner, row_created_by=user
    ).allow is True
    assert check_table_access(
        action=Action.READ, access=access, caller=owner, row_created_by=uuid4()
    ).allow is False


def test_creator_create_grants_insert():
    access = _table_access(creator={"create": True})
    res = check_table_access(action=Action.CREATE, access=access, caller=_user(), row_created_by=None)
    assert res.allow is True


# ---- Additive resolution ----------------------------------------------------

def test_union_of_grants():
    role = uuid4()
    access = _table_access(
        everyone={"read": True},
        role={"roles": [str(role)], "update": True},
        creator={"delete": True},
    )
    user_id = uuid4()
    caller = _user(user_id=user_id, role_ids=[role])
    assert check_table_access(action=Action.READ, access=access, caller=caller).allow is True
    assert check_table_access(action=Action.UPDATE, access=access, caller=caller, row_created_by=uuid4()).allow is True
    assert check_table_access(
        action=Action.DELETE, access=access, caller=caller, row_created_by=user_id
    ).allow is True


# ---- List/query Creator filter signal ---------------------------------------

def test_list_filter_signal_creator_only():
    access = _table_access(creator={"read": True})
    res = check_table_access(action=Action.READ, access=access, caller=_user(), row_created_by=None)
    # No row supplied = list/query mode
    assert res.allow is True
    assert res.creator_filter_required is True


def test_list_filter_signal_everyone_overrides_creator():
    access = _table_access(everyone={"read": True}, creator={"read": True})
    res = check_table_access(action=Action.READ, access=access, caller=_user(), row_created_by=None)
    assert res.allow is True
    assert res.creator_filter_required is False
```

- [ ] **Step 2: Run the test, expect ImportError**

```bash
./test.sh tests/unit/test_table_access.py -v
```

Expected: collection error (`ModuleNotFoundError: shared.table_access`).

- [ ] **Step 3: Implement the checker**

Create `api/shared/table_access.py`:

```python
"""Pure-function table access checker.

Resolves access rules additively across three scopes (Everyone, Role, Creator).
The caller is responsible for loading Table.access, the user's role IDs, and
(for read/update/delete on a single row) the row's created_by before invoking.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID


class Action(str, enum.Enum):
    READ = "read"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


@dataclass(frozen=True)
class Caller:
    """A logged-in user invoking a tables endpoint."""

    user_id: UUID
    role_ids: frozenset[UUID] = field(default_factory=frozenset)
    is_admin: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.role_ids, frozenset):
            object.__setattr__(self, "role_ids", frozenset(self.role_ids))


@dataclass(frozen=True)
class WorkflowCaller:
    """Sentinel for SDK callers running inside a workflow execution."""


@dataclass(frozen=True)
class CheckResult:
    allow: bool
    # True iff the only scope granting read is Creator. The caller (list/query
    # endpoint) is expected to add `WHERE created_by = caller.user_id` to the SQL.
    creator_filter_required: bool = False


def _scope_flag(block: dict[str, Any] | None, action: Action) -> bool:
    if not block:
        return False
    return bool(block.get(action.value, False))


def check_table_access(
    *,
    action: Action,
    access: dict[str, Any] | None,
    caller: Caller | WorkflowCaller,
    row_created_by: UUID | None = None,
) -> CheckResult:
    """Resolve whether the caller can perform `action` on the table/row.

    `row_created_by` semantics:
      - `None` and action in {CREATE} → row doesn't exist yet; Creator.create applies.
      - `None` and action == READ     → list/query mode; Creator scope sets the
                                        `creator_filter_required` flag instead of
                                        gating on a specific row owner.
      - `UUID`                        → single-row check; Creator grants apply
                                        only if `row_created_by == caller.user_id`.
    """
    if isinstance(caller, WorkflowCaller):
        return CheckResult(allow=True)
    if caller.is_admin:
        return CheckResult(allow=True)
    if not access:
        return CheckResult(allow=False)

    everyone_grants = _scope_flag(access.get("everyone"), action)

    role_block = access.get("role") or {}
    role_grants_action = _scope_flag(role_block, action)
    role_ids_raw = role_block.get("roles") or []
    role_ids = {UUID(r) if isinstance(r, str) else r for r in role_ids_raw}
    user_in_role = bool(caller.role_ids & role_ids)
    role_grants = role_grants_action and user_in_role

    creator_grants_action = _scope_flag(access.get("creator"), action)
    if action == Action.CREATE:
        # Creator.create means "logged-in user can insert"; no row owner yet.
        creator_grants = creator_grants_action
    elif row_created_by is None:
        # List/query mode for READ; non-row-bound for UPDATE/DELETE means
        # we can't evaluate Creator (caller bug). Treat as not-granted; the
        # `creator_filter_required` flag handles the list case below.
        creator_grants = False
    else:
        creator_grants = creator_grants_action and row_created_by == caller.user_id

    allow = everyone_grants or role_grants or creator_grants

    creator_filter_required = (
        action == Action.READ
        and row_created_by is None
        and creator_grants_action
        and not everyone_grants
        and not role_grants
    )
    if creator_filter_required:
        allow = True

    return CheckResult(allow=allow, creator_filter_required=creator_filter_required)
```

- [ ] **Step 4: Run the tests, expect all pass**

```bash
./test.sh tests/unit/test_table_access.py -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add api/shared/table_access.py api/tests/unit/test_table_access.py
git commit -m "feat(tables): pure-function TableAccessChecker + tests"
```

---

## Task 5: REST endpoints — relax + enforce

**Files:**
- Modify: `api/src/routers/tables.py:504-823`

This task changes the document endpoints from `CurrentSuperuser` to `Context`, adds the access check, populates `created_by` from the session, and emits publish hooks (publish stub goes in via Task 7 — for this task we leave a `# TODO publish_document_change` line at each write site).

- [ ] **Step 1: Add helper for loading user role IDs**

In `api/src/routers/tables.py`, near the top of the file (after imports), add:

```python
from sqlalchemy import select as sa_select

from shared.table_access import Action, Caller, CheckResult, WorkflowCaller, check_table_access
from src.models.orm.users import UserRole as UserRoleORM


async def _load_caller(ctx, db) -> Caller:
    """Build a Caller from the request context."""
    role_q = sa_select(UserRoleORM.role_id).where(UserRoleORM.user_id == ctx.user.user_id)
    role_ids = {r for r in (await db.execute(role_q)).scalars().all()}
    return Caller(
        user_id=ctx.user.user_id,
        role_ids=frozenset(role_ids),
        is_admin=ctx.user.is_superuser,
    )
```

- [ ] **Step 2: Switch document endpoints from CurrentSuperuser to Context**

Replace each document-level endpoint signature in `api/src/routers/tables.py`. Concretely, find each of these functions and change:

| Function (line) | Old signature contains | New signature contains |
|---|---|---|
| `insert_document` (~647) | `user: CurrentSuperuser` | `ctx: Context` |
| `get_document` (~673) | `user: CurrentSuperuser` | `ctx: Context` |
| `update_document` (~704) | `user: CurrentSuperuser` | `ctx: Context` |
| `delete_document` (~736) | `user: CurrentSuperuser` | `ctx: Context` |
| `query_documents` (~765) | `user: CurrentSuperuser` | `ctx: Context` |
| `count_documents` (~799) | `user: CurrentSuperuser` | `ctx: Context` |

Keep `Context` imported from `src.core.auth` (already imported in the file as part of the existing imports).

The **table-level endpoints** (`POST/PATCH/DELETE /api/tables`, plus `GET /api/tables` and `GET /api/tables/{id}`) keep `CurrentSuperuser`. Don't touch them.

- [ ] **Step 3: Add the access check to `insert_document`**

Replace the body of `insert_document` (~lines 653-670) with:

```python
async def insert_document(
    table_id: UUID,
    body: DocumentCreate,
    ctx: Context,
    db: DbSession,
) -> DocumentPublic:
    table = await _get_table_or_404(db, table_id)
    caller = await _load_caller(ctx, db)
    res = check_table_access(action=Action.CREATE, access=table.access, caller=caller)
    if not res.allow:
        raise HTTPException(status_code=403, detail="Access denied")

    repo = DocumentRepository(db, table)
    doc = await repo.insert(body.data, created_by=str(ctx.user.user_id))
    await db.commit()
    # TODO Task 7: publish_document_change(table_id, "insert", doc)
    return DocumentPublic.model_validate(doc)
```

If `_get_table_or_404` doesn't already exist, add it to the file:

```python
async def _get_table_or_404(db, table_id: UUID):
    result = await db.execute(select(Table).where(Table.id == table_id))
    table = result.scalar_one_or_none()
    if table is None:
        raise HTTPException(status_code=404, detail="Table not found")
    return table
```

- [ ] **Step 4: Update `get_document`**

```python
async def get_document(
    table_id: UUID,
    doc_id: str,
    ctx: Context,
    db: DbSession,
) -> DocumentPublic:
    table = await _get_table_or_404(db, table_id)
    caller = await _load_caller(ctx, db)
    repo = DocumentRepository(db, table)
    doc = await repo.get(doc_id)
    if doc is None:
        # 403 instead of 404 to avoid leaking presence to unauthorized callers.
        raise HTTPException(status_code=403, detail="Access denied")
    row_creator = UUID(doc.created_by) if doc.created_by else None
    res = check_table_access(
        action=Action.READ, access=table.access, caller=caller, row_created_by=row_creator
    )
    if not res.allow:
        raise HTTPException(status_code=403, detail="Access denied")
    return DocumentPublic.model_validate(doc)
```

- [ ] **Step 5: Update `update_document`**

```python
async def update_document(
    table_id: UUID,
    doc_id: str,
    body: DocumentUpdate,
    ctx: Context,
    db: DbSession,
) -> DocumentPublic:
    table = await _get_table_or_404(db, table_id)
    caller = await _load_caller(ctx, db)
    repo = DocumentRepository(db, table)
    existing = await repo.get(doc_id)
    if existing is None:
        raise HTTPException(status_code=403, detail="Access denied")
    row_creator = UUID(existing.created_by) if existing.created_by else None
    res = check_table_access(
        action=Action.UPDATE, access=table.access, caller=caller, row_created_by=row_creator
    )
    if not res.allow:
        raise HTTPException(status_code=403, detail="Access denied")
    doc = await repo.update(doc_id, body.data, updated_by=str(ctx.user.user_id))
    await db.commit()
    # TODO Task 7: publish_document_change(table_id, "update", doc)
    assert doc is not None
    return DocumentPublic.model_validate(doc)
```

- [ ] **Step 6: Update `delete_document`**

```python
async def delete_document(
    table_id: UUID,
    doc_id: str,
    ctx: Context,
    db: DbSession,
) -> Response:
    table = await _get_table_or_404(db, table_id)
    caller = await _load_caller(ctx, db)
    repo = DocumentRepository(db, table)
    existing = await repo.get(doc_id)
    if existing is None:
        raise HTTPException(status_code=403, detail="Access denied")
    row_creator = UUID(existing.created_by) if existing.created_by else None
    res = check_table_access(
        action=Action.DELETE, access=table.access, caller=caller, row_created_by=row_creator
    )
    if not res.allow:
        raise HTTPException(status_code=403, detail="Access denied")
    deleted = await repo.delete(doc_id)
    await db.commit()
    # TODO Task 7: publish_document_change(table_id, "delete", existing)
    return Response(status_code=204)
```

- [ ] **Step 7: Update `query_documents` with Creator-filter support**

```python
async def query_documents(
    table_id: UUID,
    body: DocumentQuery,
    ctx: Context,
    db: DbSession,
) -> DocumentListResponse:
    table = await _get_table_or_404(db, table_id)
    caller = await _load_caller(ctx, db)
    res = check_table_access(action=Action.READ, access=table.access, caller=caller)
    if not res.allow:
        raise HTTPException(status_code=403, detail="Access denied")

    repo = DocumentRepository(db, table)
    if res.creator_filter_required:
        repo = repo.with_creator_filter(str(caller.user_id))
    documents, total = await repo.query(body)
    return DocumentListResponse(
        documents=[DocumentPublic.model_validate(d) for d in documents],
        total=total,
    )
```

- [ ] **Step 8: Update `count_documents` likewise**

```python
async def count_documents(
    table_id: UUID,
    ctx: Context,
    db: DbSession,
) -> DocumentCountResponse:
    table = await _get_table_or_404(db, table_id)
    caller = await _load_caller(ctx, db)
    res = check_table_access(action=Action.READ, access=table.access, caller=caller)
    if not res.allow:
        raise HTTPException(status_code=403, detail="Access denied")
    repo = DocumentRepository(db, table)
    if res.creator_filter_required:
        repo = repo.with_creator_filter(str(caller.user_id))
    return DocumentCountResponse(count=await repo.count())
```

- [ ] **Step 9: Add `with_creator_filter` to `DocumentRepository`**

In the `DocumentRepository` class (around line 300), add:

```python
def with_creator_filter(self, user_id: str) -> "DocumentRepository":
    """Return a clone that filters all reads by created_by = user_id."""
    clone = DocumentRepository(self.session, self.table)
    clone._creator_filter = user_id
    return clone
```

In `__init__`, add `self._creator_filter: str | None = None`. Then in `query()` and `count()`, after `base_query = select(Document).where(Document.table_id == self.table.id)`, append:

```python
if self._creator_filter is not None:
    base_query = base_query.where(Document.created_by == self._creator_filter)
```

- [ ] **Step 10: Type-check + lint**

```bash
cd api && pyright src/routers/tables.py shared/table_access.py && ruff check src/routers/tables.py shared/table_access.py && cd ..
```

Expected: 0 errors, 0 lint issues.

- [ ] **Step 11: Run unit tests**

```bash
./test.sh tests/unit/test_table_access.py -v
```

Expected: all green (no regression from earlier).

- [ ] **Step 12: Commit**

```bash
git add api/src/routers/tables.py
git commit -m "feat(tables): enforce TableAccess on document endpoints"
```

---

## Task 6: REST e2e — access matrix

**Files:**
- Modify: `api/tests/e2e/platform/test_tables.py` (default-deny update)
- Create: `api/tests/e2e/platform/test_table_access.py`

- [ ] **Step 1: Update `test_tables.py` to assert default-deny for non-superuser**

Find an existing test in `api/tests/e2e/platform/test_tables.py` that uses a non-admin client (or add one). Add a test:

```python
async def test_default_deny_non_superuser(non_admin_client, admin_client):
    # Admin creates a table with no access block
    table = await admin_client.post("/api/tables", json={"name": "t1"}).json()

    # Non-admin tries to insert -> 403
    r = await non_admin_client.post(
        f"/api/tables/{table['id']}/documents",
        json={"data": {"x": 1}},
    )
    assert r.status_code == 403

    # Non-admin tries to query -> 403
    r = await non_admin_client.post(f"/api/tables/{table['id']}/documents/query", json={})
    assert r.status_code == 403
```

If `non_admin_client` doesn't exist as a fixture, add one in the same file's conftest by mirroring the existing `admin_client` pattern but using a non-superuser.

- [ ] **Step 2: Create the access matrix test**

Create `api/tests/e2e/platform/test_table_access.py`:

```python
"""End-to-end tests for table access rules."""
from __future__ import annotations

import pytest


@pytest.fixture
async def role_a(admin_client):
    r = await admin_client.post("/api/roles", json={"name": "test-role-a"})
    return r.json()


async def _set_access(admin_client, table_id, access):
    r = await admin_client.patch(f"/api/tables/{table_id}", json={"access": access})
    assert r.status_code == 200, r.text


async def test_everyone_read_only(admin_client, non_admin_client):
    table = (await admin_client.post("/api/tables", json={"name": "t_everyone"})).json()
    await _set_access(admin_client, table["id"], {
        "everyone": {"read": True, "create": False, "update": False, "delete": False},
        "role": {"roles": [], "read": False, "create": False, "update": False, "delete": False},
        "creator": {"read": False, "create": False, "update": False, "delete": False},
    })
    # Admin inserts a row
    doc = (await admin_client.post(
        f"/api/tables/{table['id']}/documents", json={"data": {"k": "v"}})
    ).json()

    # Non-admin can read but not write
    r = await non_admin_client.get(f"/api/tables/{table['id']}/documents/{doc['id']}")
    assert r.status_code == 200

    r = await non_admin_client.post(
        f"/api/tables/{table['id']}/documents", json={"data": {"k": "v2"}}
    )
    assert r.status_code == 403


async def test_role_grant(admin_client, non_admin_client, role_a):
    # Assign role to non-admin
    await admin_client.post(
        f"/api/users/{non_admin_client.user_id}/roles", json={"role_id": role_a["id"]}
    )
    table = (await admin_client.post("/api/tables", json={"name": "t_role"})).json()
    await _set_access(admin_client, table["id"], {
        "everyone": {"read": False, "create": False, "update": False, "delete": False},
        "role": {
            "roles": [role_a["id"]],
            "read": True, "create": True, "update": True, "delete": False,
        },
        "creator": {"read": False, "create": False, "update": False, "delete": False},
    })
    r = await non_admin_client.post(
        f"/api/tables/{table['id']}/documents", json={"data": {"x": 1}}
    )
    assert r.status_code == 200


async def test_creator_filter_in_query(admin_client, alice_client, bob_client):
    """When only Creator grants read, list returns only the caller's rows."""
    table = (await admin_client.post("/api/tables", json={"name": "t_creator"})).json()
    await _set_access(admin_client, table["id"], {
        "everyone": {"read": False, "create": True, "update": False, "delete": False},
        "role": {"roles": [], "read": False, "create": False, "update": False, "delete": False},
        "creator": {"read": True, "create": True, "update": True, "delete": True},
    })
    # Alice and Bob each insert one row
    alice_doc = (await alice_client.post(
        f"/api/tables/{table['id']}/documents", json={"data": {"who": "alice"}})
    ).json()
    bob_doc = (await bob_client.post(
        f"/api/tables/{table['id']}/documents", json={"data": {"who": "bob"}})
    ).json()

    # Alice's query returns only her row
    r = await alice_client.post(f"/api/tables/{table['id']}/documents/query", json={})
    docs = r.json()["documents"]
    assert {d["id"] for d in docs} == {alice_doc["id"]}

    # Bob's query returns only his
    r = await bob_client.post(f"/api/tables/{table['id']}/documents/query", json={})
    docs = r.json()["documents"]
    assert {d["id"] for d in docs} == {bob_doc["id"]}


async def test_admin_bypass(admin_client):
    # No access block; admin still has full CRUD
    table = (await admin_client.post("/api/tables", json={"name": "t_admin"})).json()
    r = await admin_client.post(
        f"/api/tables/{table['id']}/documents", json={"data": {"x": 1}}
    )
    assert r.status_code == 200
```

If `alice_client` / `bob_client` fixtures don't exist, add them to the e2e conftest by mirroring `non_admin_client` with distinct user emails.

- [ ] **Step 3: Run the tests**

```bash
./test.sh tests/e2e/platform/test_table_access.py -v
./test.sh tests/e2e/platform/test_tables.py::test_default_deny_non_superuser -v
```

Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add api/tests/e2e/platform/test_table_access.py api/tests/e2e/platform/test_tables.py
git commit -m "test(tables): e2e access matrix + default-deny coverage"
```

---

## Task 7: Pub/sub helpers

**Files:**
- Modify: `api/src/core/pubsub.py`
- Create: `api/tests/unit/test_pubsub_table_changes.py`

- [ ] **Step 1: Write the failing test**

Create `api/tests/unit/test_pubsub_table_changes.py`:

```python
"""Tests that pubsub helpers emit the right envelopes."""
from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.core import pubsub


@pytest.fixture(autouse=True)
def patch_manager(monkeypatch):
    mgr = AsyncMock()
    monkeypatch.setattr(pubsub, "manager", mgr)
    return mgr


async def test_publish_document_change_envelope(patch_manager):
    table_id = uuid4()
    doc = {"id": "row-1", "data": {"k": "v"}, "created_by": "user-uuid"}
    await pubsub.publish_document_change(table_id, "insert", doc)

    patch_manager.broadcast.assert_called_once()
    channel, message = patch_manager.broadcast.call_args.args
    assert channel == f"table:{table_id}"
    assert message["type"] == "document_change"
    assert message["action"] == "insert"
    assert message["id"] == "row-1"
    assert message["created_by"] == "user-uuid"
    assert message["data"] == {"k": "v"}


async def test_publish_table_access_changed_envelope(patch_manager):
    table_id = uuid4()
    await pubsub.publish_table_access_changed(table_id)
    channel, message = patch_manager.broadcast.call_args.args
    assert channel == f"table:{table_id}"
    assert message["type"] == "table_access_changed"
```

- [ ] **Step 2: Run, expect AttributeError**

```bash
./test.sh tests/unit/test_pubsub_table_changes.py -v
```

Expected: `AttributeError: module 'src.core.pubsub' has no attribute 'publish_document_change'`.

- [ ] **Step 3: Implement helpers**

Append to `api/src/core/pubsub.py`:

```python
async def publish_document_change(
    table_id: UUID,
    action: str,
    doc: Any,
) -> None:
    """Broadcast a document change to the table's channel.

    `doc` may be a SQLAlchemy Document or a plain dict. Only id, data, and
    created_by are propagated (plus the action).
    """
    if hasattr(doc, "id"):
        payload_id = doc.id
        payload_data = doc.data
        created_by = doc.created_by
    else:
        payload_id = doc.get("id")
        payload_data = doc.get("data")
        created_by = doc.get("created_by")

    message = {
        "type": "document_change",
        "table_id": str(table_id),
        "action": action,
        "id": payload_id,
        "created_by": created_by,
        "data": payload_data,
    }
    await manager.broadcast(f"table:{table_id}", message)


async def publish_table_access_changed(table_id: UUID) -> None:
    """Notify subscribers that the table's access rules have changed."""
    message = {
        "type": "table_access_changed",
        "table_id": str(table_id),
    }
    await manager.broadcast(f"table:{table_id}", message)
```

(Add `from typing import Any` and `from uuid import UUID` to the imports if not present.)

- [ ] **Step 4: Wire publish calls in `tables.py` write paths**

In `api/src/routers/tables.py`, replace the three `# TODO Task 7: publish_document_change(...)` lines with actual calls:

```python
from src.core.pubsub import publish_document_change, publish_table_access_changed
```

And in `insert_document`:

```python
await publish_document_change(table_id, "insert", doc)
```

In `update_document`:

```python
await publish_document_change(table_id, "update", doc)
```

In `delete_document`:

```python
await publish_document_change(table_id, "delete", existing)
```

In the table-level `update_table` endpoint, after a successful update that changed `access`, call:

```python
if "access" in body.model_fields_set:
    await publish_table_access_changed(table_id)
```

- [ ] **Step 5: Run tests**

```bash
./test.sh tests/unit/test_pubsub_table_changes.py -v
```

Expected: green.

- [ ] **Step 6: Commit**

```bash
git add api/src/core/pubsub.py api/src/routers/tables.py api/tests/unit/test_pubsub_table_changes.py
git commit -m "feat(tables): publish_document_change + publish_table_access_changed"
```

---

## Task 8: Websocket — `table:{id}` channel auth + Creator filter + revocation

**Files:**
- Modify: `api/src/routers/websocket.py`

**Note on gating:** Subscription is gated at handshake by the same `TableAccessChecker` against `READ`. A user with no read grant from any scope is rejected at subscribe time; nothing reaches the per-message stage. The per-message Creator filter only runs when the subscriber's read grant came purely from the Creator scope (i.e. `creator_filter_required=True`); in that case the channel still pushes events but each event is filtered to those owned by the recipient. Subscribers with Everyone or Role read see every event unfiltered.

- [ ] **Step 1: Find the channel-validation block**

In `api/src/routers/websocket.py`, locate the block (lines ~194-289) that validates each requested channel against the authenticated user. Each channel kind has a stanza like:

```python
elif channel.startswith("execution:"):
    # validate execution access
    ...
```

- [ ] **Step 2: Add the `table:` validation stanza**

Add a new branch (alongside the others). Sketched:

```python
elif channel.startswith("table:"):
    table_id_str = channel.split(":", 1)[1]
    try:
        table_id = UUID(table_id_str)
    except ValueError:
        await websocket.close(code=4004, reason="bad table id")
        return
    table = (await db.execute(select(Table).where(Table.id == table_id))).scalar_one_or_none()
    if table is None:
        await websocket.close(code=4004, reason="table not found")
        return
    caller = await _load_caller_for_ws(user, db)
    res = check_table_access(action=Action.READ, access=table.access, caller=caller)
    if not res.allow:
        await websocket.close(code=4003, reason="access denied")
        return
    # Cache for per-message filtering
    table_subscriptions[channel] = {
        "table_id": table_id,
        "caller": caller,
        "creator_filter_required": res.creator_filter_required,
    }
```

`_load_caller_for_ws` mirrors `_load_caller` from `tables.py` but takes the ws-auth `user` directly. Add it near the top of `websocket.py`:

```python
async def _load_caller_for_ws(user, db):
    role_q = select(UserRoleORM.role_id).where(UserRoleORM.user_id == user.user_id)
    role_ids = {r for r in (await db.execute(role_q)).scalars().all()}
    return Caller(user_id=user.user_id, role_ids=frozenset(role_ids), is_admin=user.is_superuser)
```

`table_subscriptions` is a per-connection dict (or attribute on the WebSocket session struct — match the existing pattern in the file). If the file already uses a `subscription_state` map, attach to that.

- [ ] **Step 3: Add per-message Creator filter on outbound**

Find the per-connection send loop. For messages on a `table:*` channel, intercept and filter:

```python
async def _filtered_send(websocket, channel, message):
    state = table_subscriptions.get(channel)
    if state and state["creator_filter_required"]:
        if message.get("type") == "document_change":
            if message.get("created_by") != str(state["caller"].user_id):
                return  # drop
    await websocket.send_json(message)
```

Use this in place of the direct `send_json` for table-channel messages. (Match the existing dispatch style — if the file already wraps sends in a per-channel hook, plug in there.)

- [ ] **Step 4: Add revocation handling**

When a `table_access_changed` message arrives on a `table:*` channel, re-evaluate:

```python
if message.get("type") == "table_access_changed":
    state = table_subscriptions.get(channel)
    if state:
        table = (await db.execute(select(Table).where(Table.id == state["table_id"]))).scalar_one_or_none()
        res = check_table_access(action=Action.READ, access=(table.access if table else None), caller=state["caller"])
        if not res.allow:
            await websocket.send_json({"type": "subscription_revoked", "channel": channel})
            del table_subscriptions[channel]
            return
        state["creator_filter_required"] = res.creator_filter_required
```

- [ ] **Step 5: Type-check + lint**

```bash
cd api && pyright src/routers/websocket.py && ruff check src/routers/websocket.py && cd ..
```

- [ ] **Step 6: Commit**

```bash
git add api/src/routers/websocket.py
git commit -m "feat(tables): table:{id} ws channel + Creator filter + revocation"
```

---

## Task 9: Websocket e2e

**Files:**
- Create: `api/tests/e2e/platform/test_table_subscriptions.py`

- [ ] **Step 1: Write the test**

```python
"""Websocket subscription E2E for tables."""
from __future__ import annotations

import asyncio

import pytest


async def _connect_and_subscribe(client, channel: str):
    ws = await client.ws_connect("/ws/connect")
    await ws.send_json({"type": "subscribe", "channels": [channel]})
    ack = await asyncio.wait_for(ws.receive_json(), timeout=2.0)
    return ws, ack


async def test_subscribe_requires_read(admin_client, non_admin_ws_client):
    table = (await admin_client.post("/api/tables", json={"name": "t_ws_deny"})).json()
    # No access block → non-admin denied
    ws, ack = await _connect_and_subscribe(non_admin_ws_client, f"table:{table['id']}")
    assert ack.get("type") == "error" or ws.closed


async def test_receive_insert_event(admin_client, alice_ws_client, alice_client):
    table = (await admin_client.post("/api/tables", json={"name": "t_ws_insert"})).json()
    await admin_client.patch(f"/api/tables/{table['id']}", json={"access": {
        "everyone": {"read": True, "create": True, "update": False, "delete": False},
        "role": {"roles": [], "read": False, "create": False, "update": False, "delete": False},
        "creator": {"read": False, "create": False, "update": False, "delete": False},
    }})
    ws, _ = await _connect_and_subscribe(alice_ws_client, f"table:{table['id']}")

    # Insert a row over REST and expect a push
    await alice_client.post(f"/api/tables/{table['id']}/documents", json={"data": {"x": 1}})

    msg = await asyncio.wait_for(ws.receive_json(), timeout=3.0)
    assert msg["type"] == "document_change"
    assert msg["action"] == "insert"


async def test_creator_filter_drops_other_users_rows(admin_client, alice_ws_client, alice_client, bob_client):
    table = (await admin_client.post("/api/tables", json={"name": "t_ws_creator"})).json()
    await admin_client.patch(f"/api/tables/{table['id']}", json={"access": {
        "everyone": {"read": False, "create": True, "update": False, "delete": False},
        "role": {"roles": [], "read": False, "create": False, "update": False, "delete": False},
        "creator": {"read": True, "create": True, "update": True, "delete": True},
    }})
    ws, _ = await _connect_and_subscribe(alice_ws_client, f"table:{table['id']}")

    # Bob inserts -> Alice's ws should NOT see it
    await bob_client.post(f"/api/tables/{table['id']}/documents", json={"data": {"who": "bob"}})
    # Alice inserts -> Alice's ws SHOULD see it
    await alice_client.post(f"/api/tables/{table['id']}/documents", json={"data": {"who": "alice"}})

    msg = await asyncio.wait_for(ws.receive_json(), timeout=3.0)
    assert msg["data"]["who"] == "alice"

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(ws.receive_json(), timeout=0.5)


async def test_revocation_emits_subscription_revoked(admin_client, alice_ws_client):
    table = (await admin_client.post("/api/tables", json={"name": "t_ws_revoke"})).json()
    await admin_client.patch(f"/api/tables/{table['id']}", json={"access": {
        "everyone": {"read": True, "create": False, "update": False, "delete": False},
        "role": {"roles": [], "read": False, "create": False, "update": False, "delete": False},
        "creator": {"read": False, "create": False, "update": False, "delete": False},
    }})
    ws, _ = await _connect_and_subscribe(alice_ws_client, f"table:{table['id']}")

    # Admin removes the everyone.read grant
    await admin_client.patch(f"/api/tables/{table['id']}", json={"access": {
        "everyone": {"read": False, "create": False, "update": False, "delete": False},
        "role": {"roles": [], "read": False, "create": False, "update": False, "delete": False},
        "creator": {"read": False, "create": False, "update": False, "delete": False},
    }})

    # Alice's ws should receive subscription_revoked
    msg = await asyncio.wait_for(ws.receive_json(), timeout=3.0)
    assert msg["type"] == "subscription_revoked"
```

If `non_admin_ws_client` / `alice_ws_client` fixtures don't exist, add them to the e2e conftest by mirroring the existing http-client fixtures with a websocket-capable variant (the project already uses one for execution-stream tests — match that pattern).

- [ ] **Step 2: Run**

```bash
./test.sh tests/e2e/platform/test_table_subscriptions.py -v
```

Expected: all green.

- [ ] **Step 3: Commit**

```bash
git add api/tests/e2e/platform/test_table_subscriptions.py
git commit -m "test(tables): websocket subscription E2E (insert push, creator filter, revoke)"
```

---

## Task 10: Workflow SDK — auto-resolve `created_by` from execution context

**Files:**
- Modify: `api/bifrost/tables.py`
- Create: `api/tests/unit/test_workflow_sdk_attribution.py` (a small unit test)

- [ ] **Step 1: Write the failing test**

```python
"""SDK auto-resolves created_by/updated_by from context."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from bifrost import tables as tables_sdk


@pytest.fixture
def fake_context(monkeypatch):
    user_id = uuid4()
    ctx = MagicMock(user_id=user_id)
    monkeypatch.setattr(tables_sdk, "_current_context", lambda: ctx)
    return ctx


async def test_insert_attributes_to_context_user(fake_context, monkeypatch):
    fake_post = AsyncMock(return_value={"id": "row-1", "data": {"k": "v"}, "created_by": str(fake_context.user_id)})
    monkeypatch.setattr(tables_sdk, "_http_post", fake_post)

    await tables_sdk.insert("t1", {"k": "v"})
    payload = fake_post.call_args.kwargs.get("json") or fake_post.call_args.args[-1]
    assert payload["created_by"] == str(fake_context.user_id)


async def test_insert_explicit_override(fake_context, monkeypatch):
    fake_post = AsyncMock(return_value={"id": "row-1", "data": {}, "created_by": "other"})
    monkeypatch.setattr(tables_sdk, "_http_post", fake_post)

    await tables_sdk.insert("t1", {"k": "v"}, created_by="other-user-uuid")
    payload = fake_post.call_args.kwargs.get("json") or fake_post.call_args.args[-1]
    assert payload["created_by"] == "other-user-uuid"
```

- [ ] **Step 2: Run, expect failure**

```bash
./test.sh tests/unit/test_workflow_sdk_attribution.py -v
```

Expected: failure (signatures don't yet accept `created_by`, or auto-resolution missing).

- [ ] **Step 3: Update the SDK**

In `api/bifrost/tables.py`, on `insert`, `update`, `upsert`, `insert_batch`, `upsert_batch`:

- Add an optional `created_by: str | None = None` (and `updated_by: str | None = None` on update/upsert).
- If the caller didn't pass it, resolve from `_current_context().user_id` (use the existing context accessor pattern in the file — the SDK already imports the execution context).
- Pass through to the underlying API request body.

Sketch for `insert`:

```python
async def insert(table, data, id=None, scope=None, app=None, created_by=None):
    if created_by is None:
        ctx = _current_context()
        if ctx is not None and getattr(ctx, "user_id", None) is not None:
            created_by = str(ctx.user_id)
    body = {"data": data}
    if id is not None:
        body["id"] = id
    if created_by is not None:
        body["created_by"] = created_by
    return await _http_post(_doc_url(table, scope, app), json=body)
```

Mirror the same pattern on the other write methods. Add the `created_by` query param plumbing on the API side if the existing endpoint doesn't accept an explicit `created_by` in the body — if it doesn't, also extend `DocumentCreate` (`api/src/models/contracts/tables.py`) with an optional `created_by: str | None = None` and have the REST handler use it (only when the caller is workflow-trusted; otherwise force to `ctx.user.user_id`).

The "only when workflow-trusted" check: since workflow SDK calls go through a different auth surface than browser sessions, the simplest pattern is to *trust the body* `created_by` only when the calling user is superuser/system; for browser sessions the handler always overrides with `ctx.user.user_id`.

- [ ] **Step 4: Run tests**

```bash
./test.sh tests/unit/test_workflow_sdk_attribution.py -v
```

Expected: green.

- [ ] **Step 5: Commit**

```bash
git add api/bifrost/tables.py api/src/models/contracts/tables.py api/src/routers/tables.py api/tests/unit/test_workflow_sdk_attribution.py
git commit -m "feat(tables): SDK auto-resolves created_by/updated_by from execution context"
```

---

## Task 11: Manifest round-trip for `Table.access`

**Files:**
- Modify: `api/bifrost/manifest.py`
- Modify: `api/src/services/manifest_generator.py`
- Modify: `api/src/services/manifest_import.py`
- Modify: `api/bifrost/portable.py`
- Modify: `api/bifrost/dto_flags.py`
- Modify: `api/tests/unit/test_manifest.py`
- Modify: `api/tests/unit/test_dto_flags.py`

- [ ] **Step 1: Write the failing manifest round-trip test**

In `api/tests/unit/test_manifest.py`, add:

```python
def test_table_access_round_trips():
    from bifrost.manifest import ManifestTable
    role = str(uuid4())
    raw = {
        "id": str(uuid4()),
        "name": "t1",
        "description": None,
        "access": {
            "everyone": {"read": True, "create": False, "update": False, "delete": False},
            "role": {"roles": [role], "read": False, "create": True, "update": True, "delete": False},
            "creator": {"read": True, "create": True, "update": True, "delete": True},
        },
    }
    m = ManifestTable.model_validate(raw)
    assert m.access.role.roles == [UUID(role)]
    rt = m.model_dump(mode="json")
    assert rt["access"]["everyone"]["read"] is True
    assert rt["access"]["role"]["roles"] == [role]
```

- [ ] **Step 2: Add `access` to `ManifestTable`**

In `api/bifrost/manifest.py`, find `ManifestTable` (around lines 232-245). Add:

```python
from .table_access import ManifestTableAccess  # define alongside

class ManifestTable(BaseModel):
    ...
    access: ManifestTableAccess | None = None
```

Define `ManifestTableAccess` in the same file (or a sibling module) mirroring `TableAccess` from `api/src/models/contracts/tables.py` but using `str` UUIDs (manifest convention) which the validator parses to UUID.

- [ ] **Step 3: Update DB→manifest serialization**

In `api/src/services/manifest_generator.py`, find `serialize_table` (~lines 264-273). Add:

```python
def serialize_table(table: Table) -> ManifestTable:
    return ManifestTable(
        id=str(table.id),
        name=table.name,
        description=table.description,
        organization_id=str(table.organization_id) if table.organization_id else None,
        application_id=str(table.application_id) if table.application_id else None,
        access=table.access,  # JSONB → Pydantic
        **{"schema": table.schema},
    )
```

- [ ] **Step 4: Update manifest→DB resolution**

In `api/src/services/manifest_import.py`, find `_resolve_table` (~lines 2070-2143). Wherever the row is updated or inserted, include `access=mtable.access.model_dump(mode="json") if mtable.access else None`.

- [ ] **Step 5: Update portable role-name rewrite**

In `api/bifrost/portable.py`, find `_rewrite_role_ids_to_names` (~lines 202-243). Tables aren't in the rewrite list today. Add tables to the rewrite, but only the **`access.role.roles`** path:

```python
for tid, tentity in (manifest.get("tables") or {}).items():
    access = tentity.get("access")
    if not access:
        continue
    role_block = access.get("role") or {}
    role_ids = role_block.get("roles") or []
    if role_ids:
        names = [role_names_by_id.get(rid) for rid in role_ids if rid in role_names_by_id]
        role_block["role_names"] = names
        role_block.pop("roles", None)
```

Also add the inverse rewrite at import time (where forms/apps role-names are converted back).

- [ ] **Step 6: Update DTO flags parity test**

In `api/bifrost/dto_flags.py`, register `access` properly: since `TableUpdate.access` is exposed via the manifest and CLI, do **not** add it to `DTO_EXCLUDES`. The DTO-parity test
(`api/tests/unit/test_dto_flags.py`) should pass once the CLI surface includes `--access` (Task 12). For now, add `access` to `DTO_EXCLUDES` with the comment `"covered by Task 12 CLI"` and remove it then.

- [ ] **Step 7: Run tests**

```bash
./test.sh tests/unit/test_manifest.py -v
./test.sh tests/unit/test_dto_flags.py -v
```

Expected: green.

- [ ] **Step 8: Commit**

```bash
git add api/bifrost/manifest.py api/bifrost/portable.py api/bifrost/dto_flags.py api/src/services/manifest_generator.py api/src/services/manifest_import.py api/tests/unit/test_manifest.py
git commit -m "feat(tables): manifest round-trip for Table.access (role-name rewrite included)"
```

---

## Task 12: CLI — `bifrost tables create/update --access ...` and `get` shows access

**Files:**
- Modify: `api/bifrost/commands/tables.py` (existing CLI: list/get/create/update/delete at lines 62, 78, 97, 120, 169)
- Modify: `api/bifrost/dto_flags.py` (remove the temporary `DTO_EXCLUDES` entry from Task 11)

- [ ] **Step 1: Add `--access` to `tables create` (line 97)**

Add a `--access` option that accepts either a literal JSON string or `@path/to/file.json`. Decision per spec open question: JSON-only for v1; flag-style ergonomics is future work.

```python
import json
from pathlib import Path

@tables_group.command("create")
def create(
    name: str,
    description: str | None = None,
    organization_id: str | None = None,
    application_id: str | None = None,
    schema: str | None = typer.Option(None, "--schema", help="JSON or @file.json"),
    access: str | None = typer.Option(None, "--access", help="JSON or @file.json with TableAccess block"),
):
    body: dict = {"name": name}
    if description is not None: body["description"] = description
    if organization_id is not None: body["organization_id"] = organization_id
    if application_id is not None: body["application_id"] = application_id
    if schema is not None: body["schema"] = _parse_json_or_file(schema)
    if access is not None: body["access"] = _parse_json_or_file(access)
    return api.post("/api/tables", json=body)


def _parse_json_or_file(arg: str) -> dict:
    if arg.startswith("@"):
        return json.loads(Path(arg[1:]).read_text())
    return json.loads(arg)
```

(Match the file's existing argument names exactly. If the existing `create` already takes `--schema`, reuse the same `_parse_json_or_file` helper; otherwise add it once near the top of the module.)

- [ ] **Step 2: Add `--access` to `tables update` (line 120)**

```python
@tables_group.command("update")
def update(
    table_id: str,
    name: str | None = None,
    description: str | None = None,
    schema: str | None = typer.Option(None, "--schema"),
    access: str | None = typer.Option(None, "--access", help="JSON or @file.json with TableAccess block"),
):
    body: dict = {}
    if name is not None: body["name"] = name
    if description is not None: body["description"] = description
    if schema is not None: body["schema"] = _parse_json_or_file(schema)
    if access is not None: body["access"] = _parse_json_or_file(access)
    return api.patch(f"/api/tables/{table_id}", json=body)
```

Preserve any existing rename-safety logic (the docstring at line 22 mentions a name-change warning) — don't strip it.

- [ ] **Step 3: Show `access` in `tables get` (line 78)**

If `tables get` currently prints a structured row (table or JSON), include the `access` field in the output. Check the existing print path — if it's a table renderer, add an "Access" column or a compact summary like `everyone:r-c-u-d, role:r-c-u-d (N roles), creator:r-c-u-d` (`-` = false, letter = true). If it's JSON, the field already shows up; verify.

- [ ] **Step 4: Add a smoke test for `tables create --access`**

In `api/tests/unit/test_cli_tables.py` (create if missing, mirroring an existing CLI command test):

```python
from bifrost.commands.tables import tables_group
from typer.testing import CliRunner

def test_create_with_access_inline_json(monkeypatch):
    captured = {}
    def fake_post(path, json):
        captured["path"] = path
        captured["body"] = json
        return {"id": "t1", **json}
    monkeypatch.setattr("bifrost.commands.tables.api.post", fake_post)

    runner = CliRunner()
    access_json = '{"everyone":{"read":true,"create":false,"update":false,"delete":false},"role":{"roles":[],"read":false,"create":false,"update":false,"delete":false},"creator":{"read":false,"create":false,"update":false,"delete":false}}'
    result = runner.invoke(tables_group, ["create", "t1", "--access", access_json])
    assert result.exit_code == 0
    assert captured["body"]["access"]["everyone"]["read"] is True


def test_create_with_access_file(monkeypatch, tmp_path):
    f = tmp_path / "a.json"
    f.write_text('{"everyone":{"read":true,"create":false,"update":false,"delete":false},"role":{"roles":[],"read":false,"create":false,"update":false,"delete":false},"creator":{"read":false,"create":false,"update":false,"delete":false}}')
    captured = {}
    monkeypatch.setattr(
        "bifrost.commands.tables.api.post",
        lambda path, json: captured.update(body=json) or {"id": "t1"},
    )
    runner = CliRunner()
    result = runner.invoke(tables_group, ["create", "t1", "--access", f"@{f}"])
    assert result.exit_code == 0
    assert captured["body"]["access"]["everyone"]["read"] is True
```

- [ ] **Step 5: Remove `access` from `DTO_EXCLUDES`**

In `api/bifrost/dto_flags.py`, remove the temporary exclude entry added in Task 11.

- [ ] **Step 6: Run tests**

```bash
./test.sh tests/unit/test_cli_tables.py -v
./test.sh tests/unit/test_dto_flags.py -v
```

Expected: green.

- [ ] **Step 7: Commit**

```bash
git add api/bifrost/commands/tables.py api/bifrost/dto_flags.py api/tests/unit/test_cli_tables.py
git commit -m "feat(cli): bifrost tables create/update --access JSON flag"
```

---

## Task 13: Web SDK — `client/src/lib/app-sdk/tables.ts`

**Files:**
- Create: `client/src/lib/app-sdk/tables.ts`
- Create: `client/src/lib/app-sdk/tables.test.ts`
- Create: `client/src/lib/app-sdk/ws-client.ts`
- Create: `client/src/lib/app-sdk/use-table-subscription.ts`
- Create: `client/src/lib/app-sdk/use-table-subscription.test.tsx`

- [ ] **Step 1: Write the failing SDK unit tests**

Create `client/src/lib/app-sdk/tables.test.ts`:

```ts
import { describe, expect, it, vi } from "vitest";
import { tables } from "./tables";

describe("tables web SDK", () => {
  it("get returns null on 403", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 403 }));
    vi.stubGlobal("fetch", fetchMock);
    const result = await tables.get("t1", "row-1");
    expect(result).toBeNull();
  });

  it("insert posts to /api/tables/{name}/documents", async () => {
    const body = { id: "row-1", data: { k: "v" } };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } })
    );
    vi.stubGlobal("fetch", fetchMock);
    const result = await tables.insert("t1", { k: "v" });
    expect(result).toEqual(body);
    const url = fetchMock.mock.calls[0][0];
    expect(url).toMatch(/\/api\/tables\/t1\/documents$/);
  });

  it("update PATCHes /api/tables/{name}/documents/{id}", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: "row-1", data: {} }), {
        status: 200,
        headers: { "content-type": "application/json" },
      })
    );
    vi.stubGlobal("fetch", fetchMock);
    await tables.update("t1", "row-1", { k: "v2" });
    const opts = fetchMock.mock.calls[0][1];
    expect(opts.method).toBe("PATCH");
  });

  it("query POSTs to /api/tables/{name}/documents/query", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ documents: [], total: 0 }), {
        status: 200,
        headers: { "content-type": "application/json" },
      })
    );
    vi.stubGlobal("fetch", fetchMock);
    await tables.query("t1", { where: { x: { eq: 1 } } });
    const url = fetchMock.mock.calls[0][0];
    expect(url).toMatch(/\/api\/tables\/t1\/documents\/query$/);
  });

  it("delete returns true on 204", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));
    expect(await tables.delete("t1", "row-1")).toBe(true);
  });

  it("count returns the count", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ count: 42 }), { status: 200, headers: { "content-type": "application/json" } })
    ));
    expect(await tables.count("t1")).toBe(42);
  });

  it("upsert POSTs with id", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: "row-1", data: {} }), {
        status: 200,
        headers: { "content-type": "application/json" },
      })
    );
    vi.stubGlobal("fetch", fetchMock);
    await tables.upsert("t1", "row-1", { k: "v" });
    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body.id).toBe("row-1");
  });

  it("insert_batch posts the array", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ inserted: 2, errors: [] }), {
        status: 200,
        headers: { "content-type": "application/json" },
      })
    );
    vi.stubGlobal("fetch", fetchMock);
    const result = await tables.insert_batch("t1", [{ data: { x: 1 } }, { data: { x: 2 } }]);
    expect(result.inserted).toBe(2);
  });
});
```

- [ ] **Step 2: Run, expect import error**

```bash
cd client && npx vitest run src/lib/app-sdk/tables.test.ts && cd ..
```

Expected: failure (`tables` not exported).

- [ ] **Step 3: Implement the SDK**

Create `client/src/lib/app-sdk/tables.ts`:

```ts
import type { components } from "@/lib/v1";

type DocumentPublic = components["schemas"]["DocumentPublic"];
type DocumentQuery = components["schemas"]["DocumentQuery"];
type DocumentListResponse = components["schemas"]["DocumentListResponse"];
type DocumentCountResponse = components["schemas"]["DocumentCountResponse"];

const base = "/api/tables";

async function http<T>(
  path: string,
  init: RequestInit = {},
): Promise<T | null> {
  const r = await fetch(path, {
    ...init,
    credentials: "include",
    headers: { "content-type": "application/json", ...(init.headers || {}) },
  });
  if (r.status === 403) return null;
  if (r.status === 404) return null;
  if (r.status === 204) return true as unknown as T;
  if (!r.ok) throw new Error(`tables: ${r.status} ${await r.text()}`);
  return (await r.json()) as T;
}

export const tables = {
  async get(table: string, id: string): Promise<DocumentPublic | null> {
    return http<DocumentPublic>(`${base}/${encodeURIComponent(table)}/documents/${encodeURIComponent(id)}`);
  },

  async insert(
    table: string,
    data: Record<string, unknown>,
    options?: { id?: string },
  ): Promise<DocumentPublic> {
    const body: Record<string, unknown> = { data };
    if (options?.id) body.id = options.id;
    const r = await http<DocumentPublic>(`${base}/${encodeURIComponent(table)}/documents`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    if (!r) throw new Error("Access denied");
    return r;
  },

  async update(
    table: string,
    id: string,
    data: Record<string, unknown>,
  ): Promise<DocumentPublic | null> {
    return http<DocumentPublic>(`${base}/${encodeURIComponent(table)}/documents/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify({ data }),
    });
  },

  async upsert(
    table: string,
    id: string,
    data: Record<string, unknown>,
  ): Promise<DocumentPublic> {
    const r = await http<DocumentPublic>(`${base}/${encodeURIComponent(table)}/documents`, {
      method: "POST",
      body: JSON.stringify({ id, data, upsert: true }),
    });
    if (!r) throw new Error("Access denied");
    return r;
  },

  async delete(table: string, id: string): Promise<boolean> {
    const r = await http(`${base}/${encodeURIComponent(table)}/documents/${encodeURIComponent(id)}`, {
      method: "DELETE",
    });
    return r === true || r !== null;
  },

  async query(table: string, q: Partial<DocumentQuery> = {}): Promise<DocumentListResponse> {
    const r = await http<DocumentListResponse>(`${base}/${encodeURIComponent(table)}/documents/query`, {
      method: "POST",
      body: JSON.stringify(q),
    });
    if (!r) throw new Error("Access denied");
    return r;
  },

  async count(table: string): Promise<number> {
    const r = await http<DocumentCountResponse>(`${base}/${encodeURIComponent(table)}/documents/count`);
    if (!r) return 0;
    return r.count;
  },

  async insert_batch(
    table: string,
    rows: Array<{ id?: string; data: Record<string, unknown> }>,
  ): Promise<{ inserted: number; errors: unknown[] }> {
    const r = await http<{ inserted: number; errors: unknown[] }>(
      `${base}/${encodeURIComponent(table)}/documents/batch`,
      { method: "POST", body: JSON.stringify({ documents: rows }) },
    );
    if (!r) throw new Error("Access denied");
    return r;
  },

  async upsert_batch(
    table: string,
    rows: Array<{ id: string; data: Record<string, unknown> }>,
  ): Promise<{ upserted: number; errors: unknown[] }> {
    const r = await http<{ upserted: number; errors: unknown[] }>(
      `${base}/${encodeURIComponent(table)}/documents/batch`,
      { method: "POST", body: JSON.stringify({ documents: rows, upsert: true }) },
    );
    if (!r) throw new Error("Access denied");
    return r;
  },

  async delete_batch(
    table: string,
    ids: string[],
  ): Promise<{ deleted: number }> {
    const r = await http<{ deleted: number }>(
      `${base}/${encodeURIComponent(table)}/documents/batch-delete`,
      { method: "POST", body: JSON.stringify({ ids }) },
    );
    if (!r) throw new Error("Access denied");
    return r;
  },

  // Subscribe lives in a sibling module; re-export here for ergonomics.
  subscribe(
    table_id: string,
    onEvent: (evt: TableChangeEvent) => void,
  ): () => void {
    // Dynamic import to keep ws-client out of the synchronous chunk.
    let cleanup: (() => void) | null = null;
    import("./ws-client").then(({ subscribeToTable }) => {
      cleanup = subscribeToTable(table_id, onEvent);
    });
    return () => cleanup?.();
  },
};

export type TableChangeEvent =
  | {
      type: "document_change";
      action: "insert" | "update" | "delete";
      id: string;
      data: Record<string, unknown> | null;
      created_by: string | null;
    }
  | { type: "subscription_revoked"; channel: string };
```

If the REST surface for `insert_batch` / `upsert_batch` / `delete_batch` doesn't exist yet, expose them in `api/src/routers/tables.py` mirroring the existing single-row endpoints. Add a unit test for each new endpoint in `api/tests/unit/test_tables_routes.py` (a small smoke test confirming 200 + correct shape).

- [ ] **Step 4: Implement `ws-client.ts`**

```ts
export type TableChangeMessage = {
  type: "document_change" | "subscription_revoked" | "table_access_changed";
  table_id?: string;
  action?: string;
  id?: string;
  data?: Record<string, unknown> | null;
  created_by?: string | null;
  channel?: string;
};

export function subscribeToTable(
  tableId: string,
  onEvent: (evt: TableChangeMessage) => void,
): () => void {
  const url = new URL("/ws/connect", window.location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.searchParams.set("channels", `table:${tableId}`);
  const ws = new WebSocket(url);
  ws.addEventListener("message", (e) => {
    try {
      const msg = JSON.parse(e.data);
      onEvent(msg);
    } catch {
      // ignore
    }
  });
  return () => ws.close();
}
```

- [ ] **Step 5: Implement `useTableSubscription`**

```ts
import { useEffect, useRef } from "react";
import { tables, type TableChangeEvent } from "./tables";

export function useTableSubscription(
  tableId: string,
  onEvent: (evt: TableChangeEvent) => void,
) {
  const callbackRef = useRef(onEvent);
  callbackRef.current = onEvent;
  useEffect(() => {
    const off = tables.subscribe(tableId, (evt) => callbackRef.current(evt));
    return off;
  }, [tableId]);
}
```

Add a vitest spec at `client/src/lib/app-sdk/use-table-subscription.test.tsx` covering: hook subscribes on mount, unsubscribes on unmount, callback ref-stable across rerenders. Use a mock `tables.subscribe` via vi.spyOn.

- [ ] **Step 6: Run all client unit tests**

```bash
cd client && npx vitest run src/lib/app-sdk/ && cd ..
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add client/src/lib/app-sdk/
git commit -m "feat(tables): web SDK + ws subscribe + React hook"
```

---

## Task 14: Inject SDK into platform scope (apps can import `tables`)

**Files:**
- Modify: `client/src/lib/app-code-runtime.ts`
- Modify: `client/src/lib/app-code-platform.d.ts`

- [ ] **Step 1: Add `tables` and `useTableSubscription` to the runtime scope**

In `client/src/lib/app-code-runtime.ts`, find the `createPlatformScope` (or equivalent) function around lines 130-250. Add:

```ts
import { tables } from "./app-sdk/tables";
import { useTableSubscription } from "./app-sdk/use-table-subscription";

// inside the scope-builder:
$.tables = tables;
$.useTableSubscription = useTableSubscription;
```

Match the file's existing style (other apis are added similarly).

- [ ] **Step 2: Update the platform type declaration**

In `client/src/lib/app-code-platform.d.ts`, add the matching exports:

```ts
export const tables: typeof import("./app-sdk/tables").tables;
export const useTableSubscription: typeof import("./app-sdk/use-table-subscription").useTableSubscription;
```

- [ ] **Step 3: Type-check the client**

```bash
cd client && npm run tsc && cd ..
```

Expected: 0 errors.

- [ ] **Step 4: Commit**

```bash
git add client/src/lib/app-code-runtime.ts client/src/lib/app-code-platform.d.ts
git commit -m "feat(tables): expose tables + useTableSubscription to apps"
```

---

## Task 15: Admin access editor UI

**Files:**
- Create: `client/src/components/tables/TableAccessEditor.tsx`
- Create: `client/src/components/tables/TableAccessEditor.test.tsx`
- Modify: **both** the table-create dialog and the table-edit form to render the editor (recon: `client/src/components/tables/TableDialog.tsx` and the per-table edit page in `client/src/pages/tables/...`). Access must be settable on creation — apps shouldn't have to make two API calls (create then patch) to opt in.

- [ ] **Step 1: Write the failing test**

```tsx
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { TableAccessEditor } from "./TableAccessEditor";

describe("TableAccessEditor", () => {
  it("renders three scope cards with four checkboxes each", () => {
    render(<TableAccessEditor value={null} roles={[]} onChange={() => {}} />);
    expect(screen.getByText(/Everyone/)).toBeInTheDocument();
    expect(screen.getByText(/Role/)).toBeInTheDocument();
    expect(screen.getByText(/Creator/)).toBeInTheDocument();
    expect(screen.getAllByRole("checkbox").length).toBe(12);
  });

  it("emits onChange when a flag toggles", () => {
    const handler = vi.fn();
    render(<TableAccessEditor value={null} roles={[]} onChange={handler} />);
    const checkbox = screen.getByLabelText(/Everyone — Read/i);
    fireEvent.click(checkbox);
    expect(handler).toHaveBeenCalled();
    const arg = handler.mock.calls[0][0];
    expect(arg.everyone.read).toBe(true);
  });

  it("shows role multi-select", () => {
    render(
      <TableAccessEditor
        value={null}
        roles={[{ id: "r1", name: "Role A" }]}
        onChange={() => {}}
      />,
    );
    expect(screen.getByText("Role A")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run, expect failure**

```bash
cd client && npx vitest run src/components/tables/TableAccessEditor.test.tsx && cd ..
```

- [ ] **Step 3: Implement the editor**

Create `client/src/components/tables/TableAccessEditor.tsx`:

```tsx
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import type { components } from "@/lib/v1";

type TableAccess = components["schemas"]["TableAccess"];

const EMPTY_ACCESS: TableAccess = {
  everyone: { read: false, create: false, update: false, delete: false },
  role: { roles: [], read: false, create: false, update: false, delete: false },
  creator: { read: false, create: false, update: false, delete: false },
};

const ACTIONS: Array<keyof TableAccess["everyone"]> = ["read", "create", "update", "delete"];

export function TableAccessEditor({
  value,
  roles,
  onChange,
}: {
  value: TableAccess | null;
  roles: Array<{ id: string; name: string }>;
  onChange: (next: TableAccess) => void;
}) {
  const v = value ?? EMPTY_ACCESS;

  function update<K extends keyof TableAccess>(scope: K, patch: Partial<TableAccess[K]>) {
    onChange({ ...v, [scope]: { ...v[scope], ...patch } });
  }

  return (
    <div className="grid gap-4">
      <Card className="p-4">
        <h3>Everyone</h3>
        {ACTIONS.map((a) => (
          <Label key={a}>
            <Checkbox
              checked={v.everyone[a]}
              onCheckedChange={(c) => update("everyone", { [a]: !!c } as never)}
              aria-label={`Everyone — ${a[0].toUpperCase()}${a.slice(1)}`}
            />
            {a}
          </Label>
        ))}
      </Card>

      <Card className="p-4">
        <h3>Role</h3>
        <select
          multiple
          value={v.role.roles}
          onChange={(e) =>
            update("role", { roles: Array.from(e.target.selectedOptions, (o) => o.value) })
          }
        >
          {roles.map((r) => (
            <option key={r.id} value={r.id}>{r.name}</option>
          ))}
        </select>
        {ACTIONS.map((a) => (
          <Label key={a}>
            <Checkbox
              checked={v.role[a]}
              onCheckedChange={(c) => update("role", { [a]: !!c } as never)}
              aria-label={`Role — ${a[0].toUpperCase()}${a.slice(1)}`}
            />
            {a}
          </Label>
        ))}
      </Card>

      <Card className="p-4">
        <h3>Creator</h3>
        {ACTIONS.map((a) => (
          <Label key={a}>
            <Checkbox
              checked={v.creator[a]}
              onCheckedChange={(c) => update("creator", { [a]: !!c } as never)}
              aria-label={`Creator — ${a[0].toUpperCase()}${a.slice(1)}`}
            />
            {a}
          </Label>
        ))}
      </Card>
    </div>
  );
}
```

- [ ] **Step 4: Wire it into the Tables create dialog AND edit form**

Two integration points:

**(a) Create dialog** (`client/src/components/tables/TableDialog.tsx`). Add an "Access" section to the create form. The submit path POSTs to `/api/tables` with `access` included in the body. Default value when the dialog opens: `null` (workflow-only, the safe default). The form should make this opt-in obvious — e.g., a collapsed "Access (advanced)" panel that, when expanded, reveals the editor.

**(b) Edit form** (likely `client/src/pages/tables/<id>.tsx` or a sibling). Render `<TableAccessEditor value={table.access ?? null} roles={roles} onChange={...} />` and submit on save via the existing PATCH path.

Both surfaces fetch the available roles via `useRoles()` (existing hook) and pass the list into the editor's `roles` prop.

- [ ] **Step 5: Run client tests + tsc + lint**

```bash
cd client && npm run tsc && npm run lint && npx vitest run src/components/tables/TableAccessEditor.test.tsx && cd ..
```

- [ ] **Step 6: Commit**

```bash
git add client/src/components/tables/ client/src/pages/tables/
git commit -m "feat(tables): admin access editor (Everyone/Role/Creator × CRUD)"
```

---

## Task 16: Playwright e2e — apps use the SDK end-to-end (via real app fixture)

**Files:**
- Create: `client/e2e/tables-app-direct.admin.spec.ts`
- Create: `client/e2e/tables-app-subscription.admin.spec.ts`

These specs use the **existing app-fixture pattern** in `client/e2e/apps-preview.admin.spec.ts` — they create a real `Application` via `/api/applications`, seed `apps/{slug}/pages/index.tsx` via `/api/files/write`, then navigate to the preview URL. **No new test harness.** Reuse `client/e2e/fixtures/api-fixture.ts` for the authenticated `api` request context.

- [ ] **Step 1: Read the existing pattern**

Open `client/e2e/apps-preview.admin.spec.ts` and read it end-to-end. Pay attention to:
- `writeBody(path, content)` helper that builds the `/api/files/write` payload (base64 + `mode: "cloud"`).
- The `beforeAll` block that creates the app and seeds files.
- The `trackPageErrors(page)` helper — reuse it; pageerror/console.error during the test must fail.
- The preview URL pattern.

The new specs follow the same shape; only the seeded TSX content differs.

- [ ] **Step 2: Write the SDK round-trip spec**

Create `client/e2e/tables-app-direct.admin.spec.ts`:

```ts
import { test, expect } from "./fixtures/api-fixture";
import type { Page } from "@playwright/test";

const UNIQUE = `${Date.now()}-${Math.floor(Math.random() * 10000)}`;
const APP_SLUG = `e2e-tables-sdk-${UNIQUE}`;
const APP_NAME = `E2E Tables SDK ${UNIQUE}`;
const TABLE_NAME = `e2e_tables_sdk_${UNIQUE}`;

const LAYOUT_TSX = `import { Outlet } from "react-router-dom";
export default function Layout() { return <Outlet />; }
`;

const INDEX_TSX = `import { tables, useState } from "bifrost";

export default function Home() {
  const [last, setLast] = useState<string>("idle");
  const [rows, setRows] = useState<unknown[]>([]);

  async function onInsert() {
    try {
      const doc = await tables.insert("${TABLE_NAME}", { value: "from-app" });
      setLast(\`inserted:\${doc.id}\`);
    } catch (e) {
      setLast(\`error:\${(e as Error).message}\`);
    }
  }

  async function onQuery() {
    const r = await tables.query("${TABLE_NAME}");
    setRows(r.documents);
    setLast(\`queried:\${r.documents.length}\`);
  }

  return (
    <div>
      <button data-testid="insert" onClick={onInsert}>Insert</button>
      <button data-testid="query" onClick={onQuery}>Query</button>
      <div data-testid="last">{last}</div>
      <ul data-testid="rows">
        {rows.map((r: any) => <li key={r.id}>{r.data?.value}</li>)}
      </ul>
    </div>
  );
}
`;

function writeBody(path: string, content: string) {
  return {
    path,
    content: Buffer.from(content, "utf-8").toString("base64"),
    mode: "cloud",
    location: "workspace",
    binary: true,
  };
}

function trackPageErrors(page: Page): { errors: string[] } {
  const errors: string[] = [];
  page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`console.error: ${msg.text()}`);
  });
  return { errors };
}

test.describe("Tables web SDK in apps", () => {
  let appId: string;
  let tableId: string;

  test.beforeAll(async ({ api }) => {
    // Create the app
    const createApp = await api.post("/api/applications", {
      data: { name: APP_NAME, slug: APP_SLUG, access_level: "authenticated", role_ids: [] },
    });
    expect(createApp.ok(), await createApp.text()).toBe(true);
    appId = (await createApp.json()).id;

    // Create the table with everyone.read+create
    const createTable = await api.post("/api/tables", { data: { name: TABLE_NAME } });
    expect(createTable.ok(), await createTable.text()).toBe(true);
    tableId = (await createTable.json()).id;
    const setAccess = await api.patch(`/api/tables/${tableId}`, {
      data: {
        access: {
          everyone: { read: true, create: true, update: false, delete: false },
          role: { roles: [], read: false, create: false, update: false, delete: false },
          creator: { read: false, create: false, update: false, delete: false },
        },
      },
    });
    expect(setAccess.ok(), await setAccess.text()).toBe(true);

    // Seed app source
    for (const [relPath, source] of [
      [`apps/${APP_SLUG}/_layout.tsx`, LAYOUT_TSX],
      [`apps/${APP_SLUG}/pages/index.tsx`, INDEX_TSX],
    ] as const) {
      const r = await api.post("/api/files/write", { data: writeBody(relPath, source) });
      expect(r.ok(), await r.text()).toBe(true);
    }
  });

  test.afterAll(async ({ api }) => {
    await api.delete(`/api/applications/${appId}`);
    await api.delete(`/api/tables/${tableId}`);
  });

  test("app inserts a row, queries, and renders results — no workflow execution created", async ({ page, api }) => {
    const { errors } = trackPageErrors(page);

    // Capture the count of executions before the test
    const before = await (await api.get("/api/executions?limit=50")).json();
    const beforeCount = before.executions?.length ?? 0;

    await page.goto(`/apps/${APP_SLUG}/preview`);
    await page.click('[data-testid="insert"]');
    await expect(page.locator('[data-testid="last"]')).toContainText("inserted:");

    await page.click('[data-testid="query"]');
    await expect(page.locator('[data-testid="last"]')).toContainText("queried:1");
    await expect(page.locator('[data-testid="rows"]')).toContainText("from-app");

    // Confirm no new execution was created (app went over REST, not workflows)
    const after = await (await api.get("/api/executions?limit=50")).json();
    expect((after.executions?.length ?? 0)).toBe(beforeCount);

    expect(errors).toEqual([]);
  });
});
```

- [ ] **Step 3: Write the subscription spec**

Create `client/e2e/tables-app-subscription.admin.spec.ts`:

```ts
import { test, expect } from "./fixtures/api-fixture";
import type { Page } from "@playwright/test";

const UNIQUE = `${Date.now()}-${Math.floor(Math.random() * 10000)}`;
const APP_SLUG = `e2e-tables-sub-${UNIQUE}`;
const APP_NAME = `E2E Tables Sub ${UNIQUE}`;
const TABLE_NAME = `e2e_tables_sub_${UNIQUE}`;

const LAYOUT_TSX = `import { Outlet } from "react-router-dom";
export default function Layout() { return <Outlet />; }
`;

// The spec passes the table_id in via search params so the seeded source can
// stay parameter-free. The app reads it on mount.
const INDEX_TSX = `import { useTableSubscription, useState, useSearchParams } from "bifrost";

export default function Home() {
  const [params] = useSearchParams();
  const tableId = params.get("table") ?? "";
  const [events, setEvents] = useState<string[]>([]);

  useTableSubscription(tableId, (evt: any) => {
    setEvents((prev) => [...prev, \`\${evt.type}:\${evt.action ?? ""}\`]);
  });

  return (
    <div>
      <div data-testid="ready">ready</div>
      <ul data-testid="events">
        {events.map((e, i) => <li key={i}>{e}</li>)}
      </ul>
    </div>
  );
}
`;

function writeBody(path: string, content: string) {
  return {
    path,
    content: Buffer.from(content, "utf-8").toString("base64"),
    mode: "cloud",
    location: "workspace",
    binary: true,
  };
}

function trackPageErrors(page: Page): { errors: string[] } {
  const errors: string[] = [];
  page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`console.error: ${msg.text()}`);
  });
  return { errors };
}

test.describe("Tables subscription in apps", () => {
  let appId: string;
  let tableId: string;

  test.beforeAll(async ({ api }) => {
    const createApp = await api.post("/api/applications", {
      data: { name: APP_NAME, slug: APP_SLUG, access_level: "authenticated", role_ids: [] },
    });
    appId = (await createApp.json()).id;

    const createTable = await api.post("/api/tables", { data: { name: TABLE_NAME } });
    tableId = (await createTable.json()).id;
    await api.patch(`/api/tables/${tableId}`, {
      data: {
        access: {
          everyone: { read: true, create: true, update: false, delete: false },
          role: { roles: [], read: false, create: false, update: false, delete: false },
          creator: { read: false, create: false, update: false, delete: false },
        },
      },
    });

    for (const [relPath, source] of [
      [`apps/${APP_SLUG}/_layout.tsx`, LAYOUT_TSX],
      [`apps/${APP_SLUG}/pages/index.tsx`, INDEX_TSX],
    ] as const) {
      await api.post("/api/files/write", { data: writeBody(relPath, source) });
    }
  });

  test.afterAll(async ({ api }) => {
    await api.delete(`/api/applications/${appId}`);
    await api.delete(`/api/tables/${tableId}`);
  });

  test("app receives a push event when a row is inserted via REST", async ({ page, api }) => {
    const { errors } = trackPageErrors(page);

    await page.goto(`/apps/${APP_SLUG}/preview?table=${tableId}`);
    await expect(page.locator('[data-testid="ready"]')).toBeVisible();
    // Give the ws a moment to subscribe before the REST insert fires.
    await page.waitForTimeout(500);

    await api.post(`/api/tables/${tableId}/documents`, { data: { data: { x: 1 } } });

    await expect(page.locator('[data-testid="events"]')).toContainText("document_change:insert", { timeout: 5000 });

    expect(errors).toEqual([]);
  });
});
```

- [ ] **Step 4: Run**

```bash
./test.sh client e2e e2e/tables-app-direct.admin.spec.ts
./test.sh client e2e e2e/tables-app-subscription.admin.spec.ts
```

Expected: both green; no console errors; the SDK round-trip confirms zero workflow executions created during the test.

- [ ] **Step 5: Commit**

```bash
git add client/e2e/tables-app-direct.admin.spec.ts client/e2e/tables-app-subscription.admin.spec.ts
git commit -m "test(tables): playwright e2e — real app fixture exercises web SDK + subscription"
```

---

## Task 17: Update `bifrost-build` skill to teach the new SDK

**Files:**
- Modify: `.claude/skills/bifrost-build/platform-api.md` (add a Tables SDK section)
- Modify: `.claude/skills/bifrost-build/app-patterns.md` (add a "data-heavy app" pattern using the SDK + subscription)
- Modify: `.claude/skills/bifrost-build/SKILL.md` (cross-reference the new section)

The skill currently teaches Claude to build apps that proxy table reads through workflows. After this lands, the canonical pattern is `tables.*` directly. The skill needs updating so future builds use the SDK.

- [ ] **Step 1: Add the Tables SDK section to `platform-api.md`**

Append a new section. Show:

- The full `tables` surface: `get`, `insert`, `update`, `upsert`, `delete`, `query`, `count`, `insert_batch`, `upsert_batch`, `delete_batch`, `subscribe`. One-line description of each.
- One worked example: a small list-and-add component.
- The `useTableSubscription` hook with a worked example: a list that auto-updates on insert/delete.
- A "when to use this vs a workflow" guide:
  - **Use the SDK** for: reads/writes the user is allowed to make against a table that has access rules configured. Lower latency, no execution record.
  - **Use a workflow** for: complex multi-step logic, side-effects (calls to external APIs, sending email), or row-level access policies the table-level rules can't express.
- The access-rule prerequisites: a table must opt in via its `access` block before apps can call the SDK; the default is workflow-only.

- [ ] **Step 2: Add a "data-heavy app" pattern to `app-patterns.md`**

A new pattern entry showing a typical CRUD app (e.g. a tickets list with insert/edit/delete and live updates) built entirely with the SDK and `useTableSubscription`. Wire it to a table with `everyone.read=true, creator.create=true, creator.update=true, creator.delete=true` so each user manages their own rows.

- [ ] **Step 3: Cross-reference from `SKILL.md`**

Add a one-liner in the relevant section so the model lands on the new SDK guidance when it's about to write a table-reading app.

- [ ] **Step 4: Sanity check by reading the updated skill end-to-end**

```bash
cat .claude/skills/bifrost-build/SKILL.md
cat .claude/skills/bifrost-build/platform-api.md
cat .claude/skills/bifrost-build/app-patterns.md
```

Confirm the guidance is consistent: no remaining "wrap table reads in a workflow" advice that contradicts the new pattern. Update or delete contradictory examples.

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/bifrost-build/
git commit -m "docs(skill): teach bifrost-build to use the tables web SDK + subscriptions"
```

---

## Task 18: Update `docs/llm.txt` with the new tables surface

**Files:**
- Modify: `docs/llm.txt`

The "Tables" section at line 148 currently lists `list / get / create / update / delete`. After Task 12 it gains `--access` on create/update; the file's stated convention is "this file documents commands and non-obvious semantics only; it does not duplicate the flag surface" — so we don't list the new flag, but we DO need to add the access concept and the per-table opt-in semantics, since they are non-obvious.

Per the maintenance contract documented in `CLAUDE.md`:

> If the field changes a command or tool that Claude should know about, update `docs/llm.txt`.

- [ ] **Step 1: Replace the Tables section non-obvious semantics list**

In `docs/llm.txt`, replace the `Non-obvious semantics:` block under `## Tables` with:

```markdown
Non-obvious semantics:
- Tables are **workflow-only by default**. The REST endpoints under `/api/tables/{id}/documents/*` reject non-admin callers unless the table has an `access` block configured.
- `update --name` prints a warning to stderr about workflow SDK references (SDK calls like `sdk.tables.get("clients")` reference tables by name). It does not block — the author must grep the workspace before pushing.
- `--application` accepts app slug / UUID / name.
- `--access` accepts a JSON literal or `@path/to/file.json`. Shape:
  ```
  {
    "everyone": {"read": bool, "create": bool, "update": bool, "delete": bool},
    "role":     {"roles": [<role-uuid>...], "read": bool, "create": bool, "update": bool, "delete": bool},
    "creator":  {"read": bool, "create": bool, "update": bool, "delete": bool}
  }
  ```
  Resolution is additive (union of grants); `null` (the default) means workflow-only.
- The Creator scope filters reads to rows whose `created_by` matches the caller's user_id when no broader scope grants `read` — useful for "users see only their own submissions" patterns.
- Browser apps can call tables directly via the platform SDK (`import { tables, useTableSubscription } from "bifrost"`); see `bifrost-build` skill `platform-api.md` for examples. Apps using the SDK do not create execution records.
- Workflow SDK auto-attributes `created_by` / `updated_by` from `context.user_id`; pass `created_by=` / `updated_by=` to override.
```

- [ ] **Step 2: Sanity-check the file**

```bash
cat docs/llm.txt | grep -A 30 "^## Tables"
```

Confirm no contradictions with the rest of the file (e.g. the `## MCP tools (parity with CLI)` section). If the MCP section claims the tables MCP tool mirrors the CLI, leave the existing "drift" caveat in place per spec — the MCP tool is unchanged in this work.

- [ ] **Step 3: Commit**

```bash
git add docs/llm.txt
git commit -m "docs(llm.txt): document Table.access, default-deny, web SDK surface"
```

---

## Task 19: Pre-completion verification

- [ ] **Step 1: Backend**

```bash
cd api && pyright && ruff check . && cd ..
```

- [ ] **Step 2: Frontend**

```bash
cd client && npm run generate:types && npm run tsc && npm run lint && cd ..
```

- [ ] **Step 3: Run the full test suite**

```bash
./test.sh stack reset
./test.sh all
./test.sh client unit
./test.sh client e2e
```

Expected: all green.

- [ ] **Step 4: Final commit (if anything changed)**

```bash
git status
git diff --stat
# If nothing left, this step is a no-op.
```

---

## Open question deferred from spec

**CLI ergonomics** — Task 12 ships JSON-only (`--access <json>` or `@file.json`). Flag-style ergonomics (`--allow everyone.read`) are a future enhancement; do not add to this plan.
