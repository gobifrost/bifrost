# File Policies Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship file policies (#170) — RLS-style rules that let apps read and write files directly from the browser without proxying through workflows. Backed by a new `FilePolicy` ORM table, a `file_index` sidecar extension carrying `created_by`/`created_at`, a `FileResolver` plugged into the shared policy engine, REST-relax of `/api/files/*` from `CurrentSuperuser` to `Context`, a Web SDK (`client/src/lib/app-sdk/files.ts`), CLI/MCP/manifest plumbing, admin UI (file browser + rule editor + effective-access tester), and websocket subscriptions for prefix-based live listings.

**Architecture:** This plan plugs into the refactored engine from `2026-05-19-policy-engine-extraction.md` (PR A). A new `api/shared/file_policies.py` provides a `FileResolver` (namespace=`file`, no `Binding` since files have no SQL pushdown), a file-specific `make_seed_admin_bypass` with the `[read,write,delete,list]` action vocab, and a `compile_action_filter(action, policies, user) -> Callable[[FileMetadata], bool]` helper that produces a Python predicate the list endpoint uses to filter S3 results in-memory. Policy resolution is longest-prefix-wins on `(location, path)` — different from tables' additive-across-rules. A new sidecar in `file_index` carries `created_by`/`created_at` populated by every write path; pre-sidecar files are admin-only by intentional design.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2.x, Pydantic v2, Alembic, pytest. React + TypeScript with shadcn/ui + Monaco editor for the admin UI. Playwright for E2E client tests.

**Hard precondition:** PR A (`2026-05-19-policy-engine-extraction.md`) must be merged before starting Task 2 of this plan. Task 1 verifies that.

---

## Scope and sub-project decomposition

This plan is one cohesive build. The work breaks into seven sub-projects:

| Sub-project | Tasks | Owner role | Can parallelize with |
|---|---|---|---|
| **A. Engine wiring** | 2–4 | Backend | — (foundational) |
| **B. Storage + migration** | 5–7 | Backend | A (after Task 2) |
| **C. REST relax + batch signed-URL** | 8–11 | Backend | D (after Task 7) |
| **D. CLI / MCP / Manifest** | 12–14 | Backend | C |
| **E. Web SDK + useFiles hook** | 15–17 | Frontend | C (after Task 8) |
| **F. Admin UI (browser, editor, tester)** | 18–21 | Frontend | E (after Task 15) |
| **G. Subscriptions** | 22–24 | Backend + Frontend | F |

For agent-team execution: spin up 3 agents after Task 7 lands — one on C, one on D, one on E. After Task 15, F can start. G is last.

---

## Public API contracts (cross-task)

These signatures are referenced by multiple tasks. They are the contract; do not deviate.

### `api/shared/file_policies.py`

```python
class FileMetadata(BaseModel):
    """Domain context shape for `{file: ...}` references."""
    location: str
    path: str  # user-meaningful, no scope segment
    created_by: UUID | None
    created_at: datetime | None

class FileResolver:
    namespace: ClassVar[str] = "file"
    def resolve(self, path: str, ctx: FileMetadata | dict | None) -> Any: ...

class FileAction(str, Enum):
    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    LIST = "list"

def make_seed_admin_bypass() -> dict: ...  # action vocab = read/write/delete/list

def find_governing_policy(
    location: str,
    path: str,
    db: AsyncSession,
    organization_id: UUID | None,
) -> "FilePolicy | None":
    """Longest-prefix match against file_policies for (location, path)."""

async def evaluate_file_action(
    action: FileAction,
    location: str,
    path: str,
    file_meta: FileMetadata | None,
    user: UserPrincipal,
    db: AsyncSession,
    organization_id: UUID | None,
) -> bool:
    """End-to-end: lookup governing policy → load metadata if needed → evaluator."""

async def filter_listing(
    location: str,
    prefix: str,
    items: list[FileMetadata],
    user: UserPrincipal,
    db: AsyncSession,
    organization_id: UUID | None,
) -> list[FileMetadata]:
    """Per-row read check for a listing. Uses governing policy for each item."""
```

### `api/shared/models.py` additions

```python
class FilePolicyDocument(BaseModel):
    """List of rules. Action vocab is file-specific."""
    policies: list["FilePolicyRule"]

class FilePolicyRule(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    actions: list[Literal["read", "write", "delete", "list"]] = Field(min_length=1)
    when: Expr | None = None

class FilePolicyCreate(BaseModel):
    location: str
    path: str
    policies: FilePolicyDocument
    organization_id: UUID | None = None  # null = global

class FilePolicyUpdate(BaseModel):
    policies: FilePolicyDocument

class FilePolicyResponse(BaseModel):
    id: UUID
    organization_id: UUID | None
    location: str
    path: str
    policies: FilePolicyDocument
    created_by: UUID
    created_at: datetime
    updated_at: datetime

class FileSignedUrlsRequest(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=500)
    method: Literal["GET", "PUT"] = "GET"
    expires_in: int = Field(default=300, ge=30, le=3600)

class FileSignedUrlsResponse(BaseModel):
    allowed: list["FileSignedUrlItem"]
    denied: list["FileSignedUrlDenial"]

class FileSignedUrlItem(BaseModel):
    path: str
    url: str
    expires_in: int

class FileSignedUrlDenial(BaseModel):
    path: str
    error: Literal["not_found", "denied"]
```

### `api/src/models/orm/file_policy.py`

```python
class FilePolicy(Base):
    __tablename__ = "file_policies"
    id: Mapped[UUID] = mapped_column(PG_UUID, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[UUID | None] = mapped_column(PG_UUID, ForeignKey("organizations.id"), nullable=True)
    location: Mapped[str] = mapped_column(String(64), nullable=False)
    path: Mapped[str] = mapped_column(String(1000), nullable=False)
    policies: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[UUID] = mapped_column(PG_UUID, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("organization_id", "location", "path", name="uq_file_policies_org_loc_path"),
        Index("ix_file_policies_lookup", "organization_id", "location", "path"),
    )
```

### `api/src/models/orm/file_index.py` (extended)

```python
class FileIndex(Base):
    __tablename__ = "file_index"

    path: Mapped[str] = mapped_column(String(1000), primary_key=True)  # existing
    content: Mapped[str | None]  # existing
    content_hash: Mapped[str | None]  # existing
    updated_at: Mapped[datetime]  # existing
    updated_by: Mapped[str | None]  # existing

    # NEW columns (Task 5 migration):
    location: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scope: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)  # user-meaningful path (no scope)
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

### `client/src/lib/app-sdk/files.ts`

```typescript
export interface FilesSdk {
  read(path: string): Promise<string>;
  write(path: string, content: string | ArrayBuffer): Promise<void>;
  delete(path: string): Promise<void>;
  list(prefix: string): Promise<FileMetadata[]>;
  exists(path: string): Promise<boolean>;
  signedUrl(path: string, method?: "GET" | "PUT"): Promise<SignedUrl>;
  signedUrls(paths: string[], method?: "GET" | "PUT"): Promise<SignedUrlsResult>;
  upload(path: string, blob: Blob): Promise<void>;
  download(path: string): Promise<Blob>;
}

export interface FileMetadata { path: string; size: number; updated_at: string; created_by: string | null; }
export interface SignedUrl { url: string; expires_in: number; }
export interface SignedUrlsResult { allowed: { path: string; url: string; expires_in: number }[]; denied: { path: string; error: "not_found" | "denied" }[]; }
```

---

## Pre-flight

- [ ] **Step 1: Confirm PR A merged**

Run: `git log main --oneline | grep -i "domain-agnostic engine"`
Expected: One match (the merge commit). If absent, PR A is not merged — block and resolve.

- [ ] **Step 2: Confirm engine modules in place**

Run: `test -f api/shared/policies/ast.py && test -f api/shared/policies/resolver.py && test -f api/shared/table_policies.py && echo OK`
Expected: `OK`.

- [ ] **Step 3: Boot test stack**

Run: `./test.sh stack up`
Expected: Stack boots.

- [ ] **Step 4: Boot dev stack**

Run: `./debug.sh status | grep -q "Status:   UP" || ./debug.sh`
Expected: Dev stack running (needed for type regen in later tasks).

---

## Sub-project A: Engine wiring

### Task 2: Create `FileResolver` and `FilePolicyDocument` Pydantic models

**Files:**
- Create: `api/shared/file_policies.py`
- Modify: `api/shared/models.py`
- Test: `api/tests/unit/test_file_resolver.py`

- [ ] **Step 1: Write the failing test for FileResolver**

Create: `api/tests/unit/test_file_resolver.py`

```python
"""FileResolver: resolves `{file: ...}` references against FileMetadata."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from shared.file_policies import FileMetadata, FileResolver


def test_file_resolver_namespace():
    assert FileResolver().namespace == "file"


def test_file_resolver_resolves_known_fields():
    uid = uuid4()
    ts = datetime.now(timezone.utc)
    meta = FileMetadata(
        location="shared",
        path="finance/q1.pdf",
        created_by=uid,
        created_at=ts,
    )
    r = FileResolver()
    assert r.resolve("created_by", meta) == str(uid)
    assert r.resolve("created_at", meta) == ts.isoformat()
    assert r.resolve("path", meta) == "finance/q1.pdf"
    assert r.resolve("location", meta) == "shared"


def test_file_resolver_handles_none_ctx():
    r = FileResolver()
    assert r.resolve("created_by", None) is None


def test_file_resolver_rejects_unknown_field():
    meta = FileMetadata(location="shared", path="x", created_by=None, created_at=None)
    r = FileResolver()
    # Unknown fields return None (consistent with RowResolver — Resolver is
    # responsible for validation at the field-name level, but a missing field
    # on a known shape returns None to match the SQL NULL-as-false semantics).
    assert r.resolve("nonexistent", meta) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/test_file_resolver.py -v`
Expected: FAIL — `ModuleNotFoundError: shared.file_policies`.

- [ ] **Step 3: Implement `shared.file_policies` (initial cut)**

Create: `api/shared/file_policies.py`

```python
"""File-domain binding to the shared policy engine.

Mirrors `shared.table_policies` for the file domain. Action vocab is
file-specific (read/write/delete/list). No Binding — files have no SQL
pushdown (lists are S3-prefix-bound, filtered per-row in Python).
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, ClassVar
from uuid import UUID

from pydantic import BaseModel

from shared.policies.ast import PolicyDocument

# Known fields on `{file: ...}` references. Resolver returns None for anything else.
_KNOWN_FILE_FIELDS: frozenset[str] = frozenset({
    "location",
    "path",
    "created_by",
    "created_at",
})


class FileMetadata(BaseModel):
    """Context shape for file policy evaluation."""

    location: str
    path: str  # user-meaningful (no scope segment)
    created_by: UUID | None = None
    created_at: datetime | None = None


class FileAction(str, Enum):
    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    LIST = "list"


class FileResolver:
    """Resolves `{file: path}` references against FileMetadata."""

    namespace: ClassVar[str] = "file"

    def resolve(self, path: str, ctx: Any) -> Any:
        if ctx is None:
            return None
        if path not in _KNOWN_FILE_FIELDS:
            return None
        if isinstance(ctx, FileMetadata):
            val = getattr(ctx, path, None)
        elif isinstance(ctx, dict):
            val = ctx.get(path)
        else:
            return None
        if isinstance(val, UUID):
            return str(val)
        if isinstance(val, datetime):
            return val.isoformat()
        return val


def make_seed_admin_bypass() -> dict:
    """Seeded policy for a freshly-created file_policies row. File action vocab."""
    return {
        "policies": [
            {
                "name": "admin_bypass",
                "description": "Platform admins bypass all checks.",
                "actions": ["read", "write", "delete", "list"],
                "when": {"user": "is_platform_admin"},
            },
        ],
    }


__all__ = [
    "FileMetadata",
    "FileAction",
    "FileResolver",
    "PolicyDocument",  # re-export for handler ergonomics
    "make_seed_admin_bypass",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./test.sh tests/unit/test_file_resolver.py -v`
Expected: PASS — all four tests green.

- [ ] **Step 5: Add `FilePolicyDocument` / `FilePolicyRule` to shared models**

Modify: `api/shared/models.py`

Add at an appropriate location (near other policy-related models):

```python
from typing import Literal as _Literal
from shared.policies.ast import Expr as _PolicyExpr


class FilePolicyRule(BaseModel):
    """One rule in a file policy. File-specific action vocab."""

    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    actions: list[_Literal["read", "write", "delete", "list"]] = Field(min_length=1)
    when: _PolicyExpr | None = None

    @field_validator("actions")
    @classmethod
    def _no_dup_actions(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("actions must not contain duplicates")
        return v


class FilePolicyDocument(BaseModel):
    """List of rules for a single (location, path) policy."""

    policies: list[FilePolicyRule] = Field(default_factory=list)
```

If `BaseModel` / `Field` / `field_validator` are not already imported in `models.py`, add them.

- [ ] **Step 6: Run unit tests to confirm nothing broke**

Run: `./test.sh unit -v 2>&1 | tail -10`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add api/shared/file_policies.py api/shared/models.py api/tests/unit/test_file_resolver.py
git commit -m "feat(file-policies): add FileResolver + FilePolicyDocument

FileResolver plugs into the shared engine for {file: ...} references.
Known fields: location, path, created_by, created_at. Action vocab
is read/write/delete/list. Mirrors shared.table_policies shape."
```

---

### Task 3: Add `evaluate_file_action` and `find_governing_policy` (longest-prefix lookup)

**Files:**
- Modify: `api/shared/file_policies.py`
- Test: `api/tests/unit/test_file_policy_lookup.py`

The longest-prefix-wins resolution is the key behavioral difference from tables. This task implements + tests it without DB access yet (synthetic FilePolicy rows passed in as a list).

- [ ] **Step 1: Write the failing test for longest-prefix matching**

Create: `api/tests/unit/test_file_policy_lookup.py`

```python
"""Longest-prefix lookup for governing FilePolicy."""
from __future__ import annotations

import pytest

from shared.file_policies import _select_longest_prefix


@pytest.fixture
def policies():
    return [
        {"id": "p-root", "location": "shared", "path": ""},
        {"id": "p-finance", "location": "shared", "path": "finance"},
        {"id": "p-finance-q1", "location": "shared", "path": "finance/q1"},
        {"id": "p-other-loc", "location": "private", "path": ""},
    ]


def test_exact_match(policies):
    assert _select_longest_prefix(policies, "shared", "finance")["id"] == "p-finance"


def test_subpath_uses_longest_match(policies):
    assert _select_longest_prefix(policies, "shared", "finance/q1/jan.pdf")["id"] == "p-finance-q1"
    assert _select_longest_prefix(policies, "shared", "finance/q2/feb.pdf")["id"] == "p-finance"
    assert _select_longest_prefix(policies, "shared", "hr/handbook.pdf")["id"] == "p-root"


def test_location_isolated(policies):
    assert _select_longest_prefix(policies, "private", "anything")["id"] == "p-other-loc"
    assert _select_longest_prefix(policies, "shared", "finance")["id"] == "p-finance"


def test_no_match_returns_none(policies):
    assert _select_longest_prefix(policies, "nonexistent", "x") is None


def test_prefix_boundary_must_be_path_separator():
    """`finance` should NOT match `financialreports/...` — path boundaries matter."""
    pol = [{"id": "p-finance", "location": "shared", "path": "finance"}]
    assert _select_longest_prefix(pol, "shared", "financialreports/x") is None
    assert _select_longest_prefix(pol, "shared", "finance/x")["id"] == "p-finance"
    assert _select_longest_prefix(pol, "shared", "finance")["id"] == "p-finance"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/test_file_policy_lookup.py -v`
Expected: FAIL — `_select_longest_prefix` not exported.

- [ ] **Step 3: Implement `_select_longest_prefix` in `file_policies.py`**

Append to: `api/shared/file_policies.py`

```python
from typing import TypeVar

_Policy = TypeVar("_Policy", bound=dict)


def _select_longest_prefix(
    policies: list[_Policy],
    location: str,
    path: str,
) -> _Policy | None:
    """Find the policy with the longest `(location, path)` prefix matching the request.

    Prefix boundaries must align to path separators: a policy at `path="finance"`
    matches `"finance"` and `"finance/anything"` but NOT `"financialreports"`.
    """
    best: _Policy | None = None
    best_len = -1
    for p in policies:
        if p["location"] != location:
            continue
        pp = p["path"]
        if pp == "":
            if best_len < 0:
                best, best_len = p, 0
            continue
        if path == pp or path.startswith(pp + "/"):
            if len(pp) > best_len:
                best, best_len = p, len(pp)
    return best


__all__.append("_select_longest_prefix")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./test.sh tests/unit/test_file_policy_lookup.py -v`
Expected: PASS — all five tests green.

- [ ] **Step 5: Add an integration test for `evaluate_file_action` semantics with default-deny**

Append to: `api/tests/unit/test_file_policy_lookup.py`

```python
from shared.file_policies import FileMetadata
from shared.policies.ast import PolicyDocument
from shared.policies.evaluate import evaluate
from shared.policies.probe import evaluate_action
from shared.file_policies import FileResolver


class _User:
    user_id = "u-1"
    email = "u@x"
    organization_id = None
    is_platform_admin = False
    role_ids = []
    role_names = ["finance"]


def test_default_deny_no_policy():
    """No policy at any prefix → denied for everyone but admin (and admin path is via admin_bypass rule)."""
    doc = PolicyDocument()
    assert evaluate_action(
        "read", doc, ctx=None, user=_User(), resolver=FileResolver(),
    ) is False


def test_creator_rule_allows_owner_read():
    doc = PolicyDocument.model_validate({
        "policies": [{
            "name": "own_uploads",
            "actions": ["read", "write", "delete"],
            "when": {"eq": [{"file": "created_by"}, {"user": "user_id"}]},
        }],
    })
    from uuid import UUID
    owner = FileMetadata(location="shared", path="x.pdf", created_by=UUID("00000000-0000-0000-0000-000000000001"), created_at=None)
    # User u-1 is not UUID-1
    assert evaluate_action(
        "read", doc, ctx=owner, user=_User(), resolver=FileResolver(),
    ) is False


def test_has_role_rule():
    doc = PolicyDocument.model_validate({
        "policies": [{
            "name": "finance_team",
            "actions": ["read", "write", "list"],
            "when": {"call": "has_role", "args": ["finance"]},
        }],
    })
    assert evaluate_action(
        "read", doc, ctx=None, user=_User(), resolver=FileResolver(),
    ) is True
    assert evaluate_action(
        "delete", doc, ctx=None, user=_User(), resolver=FileResolver(),
    ) is False
```

- [ ] **Step 6: Run the suite**

Run: `./test.sh tests/unit/test_file_policy_lookup.py tests/unit/test_file_resolver.py -v`
Expected: PASS — all tests green.

- [ ] **Step 7: Commit**

```bash
git add api/shared/file_policies.py api/tests/unit/test_file_policy_lookup.py
git commit -m "feat(file-policies): longest-prefix policy selector + integration tests

_select_longest_prefix implements path-separator-aware prefix matching
(finance does not match financialreports). evaluate_file_action wired
via shared.policies.probe.evaluate_action with FileResolver."
```

---

### Task 4: Wire `find_governing_policy` and `evaluate_file_action` to the DB

**Files:**
- Modify: `api/shared/file_policies.py`
- Test: `api/tests/e2e/platform/test_file_policy_evaluator.py`

- [ ] **Step 1: Write the failing E2E test against a real DB**

Create: `api/tests/e2e/platform/test_file_policy_evaluator.py`

```python
"""E2E: governing policy lookup against the file_policies table."""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from shared.file_policies import (
    FileAction,
    FileMetadata,
    evaluate_file_action,
    find_governing_policy,
)
from src.models.orm.file_policy import FilePolicy


async def _insert_policy(db, *, location, path, policies, org_id=None, user_id=None):
    row = FilePolicy(
        organization_id=org_id,
        location=location,
        path=path,
        policies=policies,
        created_by=user_id or uuid4(),
    )
    db.add(row)
    await db.flush()
    return row


@pytest.mark.asyncio
async def test_find_governing_policy_longest_prefix(db: AsyncSession):
    org_id = uuid4()
    user_id = uuid4()
    await _insert_policy(db, location="shared", path="", policies={"policies": []}, org_id=org_id, user_id=user_id)
    await _insert_policy(db, location="shared", path="finance", policies={"policies": []}, org_id=org_id, user_id=user_id)
    await _insert_policy(db, location="shared", path="finance/q1", policies={"policies": []}, org_id=org_id, user_id=user_id)

    p = await find_governing_policy("shared", "finance/q1/jan.pdf", db, org_id)
    assert p is not None and p.path == "finance/q1"

    p = await find_governing_policy("shared", "finance/q2/feb.pdf", db, org_id)
    assert p is not None and p.path == "finance"

    p = await find_governing_policy("shared", "hr/handbook.pdf", db, org_id)
    assert p is not None and p.path == ""

    p = await find_governing_policy("nonexistent", "x", db, org_id)
    assert p is None


@pytest.mark.asyncio
async def test_evaluate_file_action_admin_bypass(db: AsyncSession, platform_admin_user):
    org_id = uuid4()
    user_id = uuid4()
    await _insert_policy(db, location="shared", path="", policies={
        "policies": [{
            "name": "admin_bypass",
            "actions": ["read", "write", "delete", "list"],
            "when": {"user": "is_platform_admin"},
        }],
    }, org_id=org_id, user_id=user_id)

    allowed = await evaluate_file_action(
        FileAction.READ,
        location="shared",
        path="anything.pdf",
        file_meta=None,
        user=platform_admin_user,
        db=db,
        organization_id=org_id,
    )
    assert allowed is True


@pytest.mark.asyncio
async def test_evaluate_file_action_default_deny(db: AsyncSession, regular_user):
    # No policies at all → default deny
    allowed = await evaluate_file_action(
        FileAction.READ,
        location="shared",
        path="anything.pdf",
        file_meta=None,
        user=regular_user,
        db=db,
        organization_id=uuid4(),
    )
    assert allowed is False
```

Note on fixtures `platform_admin_user` and `regular_user`: these likely already exist in `api/tests/conftest.py` for table policy tests. If they don't, add them next to the existing user fixtures.

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/e2e/platform/test_file_policy_evaluator.py -v`
Expected: FAIL — `find_governing_policy` and `evaluate_file_action` not yet exported; also `FilePolicy` ORM doesn't yet exist (Task 5 creates it).

- [ ] **Step 3: Defer step 4-Z until Task 5 lands**

Stop here. Task 5 creates the `FilePolicy` ORM and the migration. Task 6 returns to finish the function implementations.

- [ ] **Step 4: Commit the failing test (skipped)**

Pytest skip the new tests until ORM lands:

Wrap the file content with `pytestmark = pytest.mark.skip(reason="awaits Task 5 FilePolicy ORM")` at the top.

```bash
git add api/tests/e2e/platform/test_file_policy_evaluator.py
git commit -m "test(file-policies): WIP evaluator E2E (skipped — awaits FilePolicy ORM)"
```

---

## Sub-project B: Storage + migration

### Task 5: Create `FilePolicy` ORM + Alembic migration + sidecar columns on `file_index`

**Files:**
- Create: `api/src/models/orm/file_policy.py`
- Modify: `api/src/models/orm/__init__.py`
- Modify: `api/src/models/orm/file_index.py`
- Create: `api/alembic/versions/<YYYYMMDDHHMM>_file_policies_and_file_index_sidecar.py` (Alembic generates the filename)
- Test: `api/tests/unit/test_file_policy_orm.py`

- [ ] **Step 1: Create the FilePolicy ORM**

Create: `api/src/models/orm/file_policy.py`

```python
"""FilePolicy ORM model.

Stores RLS-style rules keyed by (organization_id, location, path) for direct
file reads/writes from apps. Resolution is longest-prefix-wins on
(location, path); within a policy, rules are additive OR per action.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class FilePolicy(Base):
    """RLS-style policy for a (location, path) prefix."""

    __tablename__ = "file_policies"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,  # null = global
    )
    location: Mapped[str] = mapped_column(String(64), nullable=False)
    path: Mapped[str] = mapped_column(String(1000), nullable=False)
    policies: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id", "location", "path",
            name="uq_file_policies_org_loc_path",
        ),
        Index("ix_file_policies_lookup", "organization_id", "location", "path"),
    )
```

- [ ] **Step 2: Register the ORM in `__init__.py`**

Modify: `api/src/models/orm/__init__.py`

Add the import and __all__ entry:

```python
from src.models.orm.file_policy import FilePolicy

__all__ = [
    # ... existing entries ...
    "FilePolicy",
]
```

- [ ] **Step 3: Extend `FileIndex` with sidecar columns**

Modify: `api/src/models/orm/file_index.py`

Replace the class with:

```python
"""FileIndex ORM model.

Search index for text content in _repo/ AND sidecar metadata for file
policies (created_by, created_at, location/scope/user_path decomposition).
Populated via dual-write whenever files are written to S3.
"""
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import DateTime, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class FileIndex(Base):
    """Search index + policy sidecar for workspace files in _repo/."""

    __tablename__ = "file_index"

    path: Mapped[str] = mapped_column(String(1000), primary_key=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    updated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Sidecar fields for file policies (#170). Populated by every write path.
    # NULL on pre-existing rows means the file is admin-only until rewritten.
    location: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scope: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True,
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
```

- [ ] **Step 4: Generate the Alembic migration**

Run: `cd api && alembic revision -m "file_policies and file_index sidecar"`
Expected: A new file under `api/alembic/versions/`.

- [ ] **Step 5: Edit the migration**

Open the new file and replace `upgrade()`/`downgrade()`:

```python
"""file_policies and file_index sidecar.

Adds the file_policies table for RLS-style file access rules and extends
file_index with sidecar columns (location, scope, user_path, created_by,
created_at) so policies can filter listings by `{file: created_by}`.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "<auto>"
down_revision = "<auto>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "file_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("location", sa.String(length=64), nullable=False),
        sa.Column("path", sa.String(length=1000), nullable=False),
        sa.Column("policies", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "location", "path", name="uq_file_policies_org_loc_path"),
    )
    op.create_index(
        "ix_file_policies_lookup", "file_policies",
        ["organization_id", "location", "path"],
    )

    op.add_column("file_index", sa.Column("location", sa.String(length=64), nullable=True))
    op.add_column("file_index", sa.Column("scope", sa.String(length=64), nullable=True))
    op.add_column("file_index", sa.Column("user_path", sa.String(length=1000), nullable=True))
    op.add_column("file_index", sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("file_index", sa.Column("created_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("file_index", "created_at")
    op.drop_column("file_index", "created_by")
    op.drop_column("file_index", "user_path")
    op.drop_column("file_index", "scope")
    op.drop_column("file_index", "location")
    op.drop_index("ix_file_policies_lookup", table_name="file_policies")
    op.drop_table("file_policies")
```

Update `revision` and `down_revision` with the values Alembic generated.

- [ ] **Step 6: Apply the migration in the test stack**

Run: `./test.sh stack reset && ./test.sh stack up`
Expected: Both tables present.

- [ ] **Step 7: Smoke-test the ORM**

Create: `api/tests/unit/test_file_policy_orm.py`

```python
"""FilePolicy ORM smoke test — confirm the table exists and round-trips."""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.file_policy import FilePolicy


@pytest.mark.asyncio
async def test_file_policy_round_trip(db: AsyncSession, regular_user):
    fp = FilePolicy(
        organization_id=None,
        location="shared",
        path="finance",
        policies={"policies": []},
        created_by=regular_user.user_id,
    )
    db.add(fp)
    await db.commit()
    await db.refresh(fp)

    result = await db.execute(
        select(FilePolicy).where(FilePolicy.id == fp.id)
    )
    loaded = result.scalar_one()
    assert loaded.location == "shared"
    assert loaded.path == "finance"
    assert loaded.policies == {"policies": []}
```

Run: `./test.sh tests/unit/test_file_policy_orm.py -v`
Expected: PASS.

- [ ] **Step 8: Apply migration in the dev stack**

Run: `docker compose restart bifrost-init && docker compose restart api`
(Or equivalent for the worktree's debug stack — check container names with `./debug.sh status`.)

- [ ] **Step 9: Commit**

```bash
git add api/src/models/orm/file_policy.py api/src/models/orm/__init__.py api/src/models/orm/file_index.py api/alembic/versions/*file_policies*.py api/tests/unit/test_file_policy_orm.py
git commit -m "feat(file-policies): FilePolicy ORM + file_index sidecar columns

New file_policies table keyed by (org_id, location, path). file_index
gains location/scope/user_path/created_by/created_at sidecar columns
populated by every write path. Existing rows leave them NULL —
pre-sidecar files are admin-only until rewritten (per design spec)."
```

---

### Task 6: Finish `evaluate_file_action` and `find_governing_policy` against the DB

**Files:**
- Modify: `api/shared/file_policies.py`
- Modify: `api/tests/e2e/platform/test_file_policy_evaluator.py` (un-skip)

- [ ] **Step 1: Un-skip the E2E test**

Modify: `api/tests/e2e/platform/test_file_policy_evaluator.py`

Remove the `pytestmark = pytest.mark.skip(...)` line.

- [ ] **Step 2: Run test to verify it fails on missing functions**

Run: `./test.sh tests/e2e/platform/test_file_policy_evaluator.py -v`
Expected: FAIL — `find_governing_policy`/`evaluate_file_action` not yet defined.

- [ ] **Step 3: Implement the DB-backed helpers**

Append to: `api/shared/file_policies.py`

```python
from typing import TYPE_CHECKING
from uuid import UUID as _UUID

from sqlalchemy import select as _select
from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession

from shared.policies.ast import PolicyDocument as _PolicyDocument
from shared.policies.probe import evaluate_action as _evaluate_action

if TYPE_CHECKING:
    from src.models.orm.file_policy import FilePolicy as _FilePolicy


async def find_governing_policy(
    location: str,
    path: str,
    db: "_AsyncSession",
    organization_id: "_UUID | None",
):
    """Longest-prefix lookup against file_policies for (org, location, path)."""
    from src.models.orm.file_policy import FilePolicy as _FP

    stmt = _select(_FP).where(
        _FP.location == location,
        (_FP.organization_id == organization_id) | (_FP.organization_id.is_(None)),
    )
    result = await db.execute(stmt)
    candidates = result.scalars().all()

    rows = [{"id": p.id, "location": p.location, "path": p.path, "row": p} for p in candidates]
    best = _select_longest_prefix(rows, location, path)
    return best["row"] if best else None


async def evaluate_file_action(
    action: "FileAction",
    location: str,
    path: str,
    file_meta: "FileMetadata | None",
    user,
    db: "_AsyncSession",
    organization_id: "_UUID | None",
) -> bool:
    """Resolve the governing policy and evaluate `action` against it."""
    governing = await find_governing_policy(location, path, db, organization_id)
    if governing is None:
        return False
    doc = _PolicyDocument.model_validate(governing.policies)
    return _evaluate_action(
        action.value,
        doc,
        ctx=file_meta,
        user=user,
        resolver=FileResolver(),
    )


__all__.extend(["find_governing_policy", "evaluate_file_action"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./test.sh tests/e2e/platform/test_file_policy_evaluator.py -v`
Expected: PASS — all three E2E tests green.

- [ ] **Step 5: Commit**

```bash
git add api/shared/file_policies.py api/tests/e2e/platform/test_file_policy_evaluator.py
git commit -m "feat(file-policies): DB-backed find_governing_policy + evaluate_file_action

Longest-prefix lookup against file_policies table; runs the shared
engine evaluator with FileResolver. Default deny when no governing
policy. Org-scoped lookup (org policies + global policies)."
```

---

### Task 7: Sidecar dual-write on every file write path

**Files:**
- Modify: `api/src/services/file_backend.py` (or wherever the actual S3-write logic lives)
- Modify: `api/src/routers/files.py` (write endpoint sidecar update)
- Modify: `api/src/routers/forms.py` (form upload path)
- Modify: any other write path identified via grep
- Test: `api/tests/e2e/platform/test_file_index_sidecar.py`

The sidecar is the source of truth for `{file: created_by}` and `{file: created_at}`. Every write must populate it or Creator-scope policies misbehave.

- [ ] **Step 1: Inventory every write path**

Run: `grep -rn "file_index\|FileIndex" api/src/ api/shared/ --include="*.py" | grep -iE "insert|update|add\b|merge" | head -20`
Record the file paths into a comment block in this task — every one needs to set the five new fields.

- [ ] **Step 2: Write the failing E2E test**

Create: `api/tests/e2e/platform/test_file_index_sidecar.py`

```python
"""Every file write path must populate the file_index sidecar columns."""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.file_index import FileIndex


@pytest.mark.asyncio
async def test_files_write_populates_sidecar(
    db: AsyncSession,
    api_client_admin,  # existing fixture, authenticated as platform admin
    platform_admin_user,
):
    resp = await api_client_admin.post("/api/files/write", json={
        "location": "shared",
        "scope": "org-x",
        "path": "test/sidecar.txt",
        "content": "hello",
    })
    assert resp.status_code == 204

    result = await db.execute(
        select(FileIndex).where(FileIndex.user_path == "test/sidecar.txt")
    )
    row = result.scalar_one()
    assert row.location == "shared"
    assert row.scope == "org-x"
    assert row.user_path == "test/sidecar.txt"
    assert row.created_by == platform_admin_user.user_id
    assert row.created_at is not None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `./test.sh tests/e2e/platform/test_file_index_sidecar.py -v`
Expected: FAIL — sidecar columns are NULL.

- [ ] **Step 4: Add a sidecar-write helper**

Append to: `api/shared/file_policies.py`

```python
async def upsert_sidecar(
    db: "_AsyncSession",
    *,
    s3_key: str,
    location: str,
    scope: "str | None",
    user_path: str,
    user_id: "_UUID",
) -> None:
    """Upsert the file_index sidecar fields for a freshly-written file.

    Idempotent on (s3_key) since file_index has path as its primary key.
    On insert: populates created_by/created_at to (user_id, now()).
    On update: leaves created_by/created_at untouched (preserves original creator).
    """
    from datetime import datetime, timezone
    from src.models.orm.file_index import FileIndex

    stmt = _select(FileIndex).where(FileIndex.path == s3_key)
    existing = (await db.execute(stmt)).scalar_one_or_none()

    now = datetime.now(timezone.utc)
    if existing is None:
        db.add(FileIndex(
            path=s3_key,
            location=location,
            scope=scope,
            user_path=user_path,
            created_by=user_id,
            created_at=now,
            updated_at=now,
            updated_by=str(user_id),
        ))
    else:
        existing.location = location
        existing.scope = scope
        existing.user_path = user_path
        existing.updated_at = now
        existing.updated_by = str(user_id)
        # created_by/created_at intentionally preserved


__all__.append("upsert_sidecar")
```

- [ ] **Step 5: Call `upsert_sidecar` from every write path**

For each write path identified in Step 1, add a call to `upsert_sidecar` immediately after the S3 write succeeds and before the request returns. Each call site has a different way to resolve `(location, scope, user_path)`:

- `POST /api/files/write` (`routers/files.py`): values are in the request body.
- Form uploads (`routers/forms.py`): scope = `<form-id>`, location = `"uploads"`, user_path = the user-supplied filename.
- Workflow SDK writes (`sdk/files.py` if any direct-write helpers): pass through to the existing S3-write internal — add `upsert_sidecar` next to the dual-write that maintains FileIndex content/hash.

**Important:** make these changes in the same commit; partial coverage means some files are missing sidecar and policy evaluator denies them unexpectedly.

- [ ] **Step 6: Run the sidecar E2E**

Run: `./test.sh tests/e2e/platform/test_file_index_sidecar.py -v`
Expected: PASS.

- [ ] **Step 7: Run the full file-related test suite**

Run: `./test.sh tests/e2e/platform/ -k "file or upload" -v`
Expected: PASS — existing file tests unchanged.

- [ ] **Step 8: Add a sidecar-required check to verify nothing was missed**

Append to: `api/tests/e2e/platform/test_file_index_sidecar.py`

```python
@pytest.mark.asyncio
async def test_form_upload_populates_sidecar(
    db: AsyncSession,
    api_client_admin,
    test_form_id: str,
):
    """Form uploads write a file_index row with location='uploads', scope=form_id."""
    files = {"file": ("test.txt", b"hi", "text/plain")}
    resp = await api_client_admin.post(
        f"/api/forms/{test_form_id}/upload",
        files=files,
    )
    assert resp.status_code in (200, 201)

    result = await db.execute(
        select(FileIndex)
        .where(FileIndex.location == "uploads")
        .where(FileIndex.scope == test_form_id)
    )
    rows = result.scalars().all()
    assert len(rows) >= 1
    row = rows[0]
    assert row.created_by is not None
    assert row.created_at is not None
```

Run: `./test.sh tests/e2e/platform/test_file_index_sidecar.py -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add api/shared/file_policies.py api/src/routers/files.py api/src/routers/forms.py api/tests/e2e/platform/test_file_index_sidecar.py
# add any other write-path files you modified
git commit -m "feat(file-policies): dual-write file_index sidecar on every write path

Every file write now populates file_index.location/scope/user_path/
created_by/created_at. upsert_sidecar() is the shared helper; called
from POST /api/files/write, form uploads, and any SDK direct-write
paths. created_by/created_at are preserved on update (only the
original writer is credited as creator)."
```

---

## Sub-project C: REST relax + batch signed-URL

### Task 8: Relax `/api/files/*` from `CurrentSuperuser` to `Context` with policy enforcement

**Files:**
- Modify: `api/src/routers/files.py`
- Test: `api/tests/e2e/platform/test_file_policies_rest.py`

- [ ] **Step 1: Write the failing REST matrix test**

Create: `api/tests/e2e/platform/test_file_policies_rest.py`

```python
"""REST matrix: admin vs role-holder vs plain user × read/write/delete/list."""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.file_policy import FilePolicy


@pytest.fixture
async def finance_policy(db: AsyncSession, admin_user):
    fp = FilePolicy(
        organization_id=None,  # global for test simplicity
        location="shared",
        path="finance",
        policies={
            "policies": [
                {
                    "name": "admin_bypass",
                    "actions": ["read", "write", "delete", "list"],
                    "when": {"user": "is_platform_admin"},
                },
                {
                    "name": "finance_team",
                    "actions": ["read", "write", "delete", "list"],
                    "when": {"call": "has_role", "args": ["finance"]},
                },
            ],
        },
        created_by=admin_user.user_id,
    )
    db.add(fp)
    await db.commit()
    return fp


@pytest.mark.asyncio
async def test_admin_can_read(api_client_admin, finance_policy):
    resp = await api_client_admin.post("/api/files/read", json={
        "location": "shared",
        "scope": "org-1",
        "path": "finance/q1.pdf",
    })
    # 200 if file exists, 404 if not — but NEVER 403 for admin
    assert resp.status_code != 403


@pytest.mark.asyncio
async def test_finance_role_can_read(api_client_finance, finance_policy):
    resp = await api_client_finance.post("/api/files/read", json={
        "location": "shared",
        "scope": "org-1",
        "path": "finance/q1.pdf",
    })
    assert resp.status_code != 403


@pytest.mark.asyncio
async def test_plain_user_denied(api_client_plain, finance_policy):
    resp = await api_client_plain.post("/api/files/read", json={
        "location": "shared",
        "scope": "org-1",
        "path": "finance/q1.pdf",
    })
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_default_deny_no_policy(api_client_plain):
    """A request for a path with no governing policy → 403 (non-admin)."""
    resp = await api_client_plain.post("/api/files/read", json={
        "location": "shared",
        "scope": "org-1",
        "path": "ungoverned/x.pdf",
    })
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_exists_does_not_leak(api_client_plain, finance_policy):
    """Deny and not-found both return 403 (existence-non-leak)."""
    resp = await api_client_plain.post("/api/files/exists", json={
        "location": "shared",
        "scope": "org-1",
        "path": "finance/q1.pdf",
    })
    assert resp.status_code == 403
```

Fixtures needed: `api_client_finance` (auth as user with role `finance`) and `api_client_plain` (no relevant roles). Add to `api/tests/conftest.py` next to existing API-client fixtures.

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/e2e/platform/test_file_policies_rest.py -v`
Expected: FAIL — endpoints still require `CurrentSuperuser` (401 instead of 403).

- [ ] **Step 3: Relax `read` endpoint**

Modify: `api/src/routers/files.py`

Replace the `read` endpoint:

```python
from shared.file_policies import (
    FileAction,
    FileMetadata,
    evaluate_file_action,
    find_governing_policy,
)
from src.core.auth import Context, UserPrincipal
# remove CurrentSuperuser import if no longer used


@router.post("/read", response_model=FileReadResponse)
async def read_file(
    request: FileReadRequest,
    ctx: Context,
    user: UserPrincipal,  # was CurrentSuperuser
) -> FileReadResponse:
    # Load sidecar metadata (if any) for FileMetadata
    s3_key = resolve_s3_key(request.location, request.scope, request.path)
    fi = await _load_file_index(ctx.db, s3_key)
    file_meta = FileMetadata(
        location=request.location,
        path=request.path,
        created_by=fi.created_by if fi else None,
        created_at=fi.created_at if fi else None,
    )

    allowed = await evaluate_file_action(
        FileAction.READ,
        location=request.location,
        path=request.path,
        file_meta=file_meta,
        user=user,
        db=ctx.db,
        organization_id=ctx.organization_id,
    )
    if not allowed:
        raise HTTPException(status_code=403, detail="forbidden")

    # ... existing read logic ...
```

Add helper:

```python
async def _load_file_index(db: AsyncSession, s3_key: str):
    from src.models.orm.file_index import FileIndex
    result = await db.execute(select(FileIndex).where(FileIndex.path == s3_key))
    return result.scalar_one_or_none()
```

- [ ] **Step 4: Relax write, delete, list, exists, signed-url**

Apply the same pattern to each endpoint. The action mapping:

- `POST /api/files/read` → `FileAction.READ`
- `POST /api/files/write` → `FileAction.WRITE`
- `POST /api/files/delete` → `FileAction.DELETE`
- `POST /api/files/list` → `FileAction.LIST` (and use `filter_listing` from Task 9 to filter results)
- `POST /api/files/exists` → `FileAction.READ` BUT respond 403 for both deny AND not-found (existence-non-leak)
- `POST /api/files/signed-url` → `FileAction.READ` (GET) / `FileAction.WRITE` (PUT)

The other endpoints (`/pull`, `/manifest`, `/watch`, `/watchers`, the admin-mode browse endpoints starting around line 586) remain `CurrentSuperuser`-gated.

- [ ] **Step 5: Run the matrix test**

Run: `./test.sh tests/e2e/platform/test_file_policies_rest.py -v`
Expected: PASS — all five tests green.

- [ ] **Step 6: Regression check — admin must still work**

Run: `./test.sh tests/e2e/platform/test_files.py -v`
Expected: PASS — existing tests still pass (admin can read/write everything via the seeded admin_bypass).

- [ ] **Step 7: Commit**

```bash
git add api/src/routers/files.py api/tests/e2e/platform/test_file_policies_rest.py api/tests/conftest.py
git commit -m "feat(file-policies): relax /api/files/* to Context + policy enforcement

read/write/delete/list/exists/signed-url now check file policies via
evaluate_file_action. Admin keeps full access via the seeded
admin_bypass rule (which lands automatically on first policy create).
exists uses existence-non-leak (403 for both deny and not-found)."
```

---

### Task 9: List endpoint — per-row filtering

**Files:**
- Modify: `api/src/routers/files.py`
- Modify: `api/shared/file_policies.py`
- Test: `api/tests/e2e/platform/test_file_policies_rest.py` (extend)

- [ ] **Step 1: Write the failing test for Creator-only listing**

Append to: `api/tests/e2e/platform/test_file_policies_rest.py`

```python
@pytest.fixture
async def own_uploads_policy(db: AsyncSession, admin_user):
    fp = FilePolicy(
        organization_id=None,
        location="shared",
        path="user-uploads",
        policies={
            "policies": [
                {
                    "name": "admin_bypass",
                    "actions": ["read", "write", "delete", "list"],
                    "when": {"user": "is_platform_admin"},
                },
                {
                    "name": "own_uploads",
                    "actions": ["read", "write", "delete", "list"],
                    "when": {"eq": [{"file": "created_by"}, {"user": "user_id"}]},
                },
            ],
        },
        created_by=admin_user.user_id,
    )
    db.add(fp)
    await db.commit()
    return fp


@pytest.mark.asyncio
async def test_list_filters_to_own_files(
    db: AsyncSession,
    api_client_plain,
    api_client_other_plain,  # different non-admin user
    own_uploads_policy,
):
    """User A's list returns only User A's files."""
    # Two users upload one file each
    await api_client_plain.post("/api/files/write", json={
        "location": "shared", "scope": "x", "path": "user-uploads/a.txt", "content": "a",
    })
    await api_client_other_plain.post("/api/files/write", json={
        "location": "shared", "scope": "x", "path": "user-uploads/b.txt", "content": "b",
    })

    resp = await api_client_plain.post("/api/files/list", json={
        "location": "shared",
        "scope": "x",
        "prefix": "user-uploads/",
    })
    assert resp.status_code == 200
    items = resp.json()["items"]
    paths = [it["path"] for it in items]
    assert "user-uploads/a.txt" in paths
    assert "user-uploads/b.txt" not in paths
```

Add `api_client_other_plain` to conftest fixtures (a second non-admin user — already needed for table-policies tests, likely exists).

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/e2e/platform/test_file_policies_rest.py::test_list_filters_to_own_files -v`
Expected: FAIL — list returns both files (no filter applied yet).

- [ ] **Step 3: Add `filter_listing` to `file_policies.py`**

Append to: `api/shared/file_policies.py`

```python
async def filter_listing(
    location: str,
    items: list[FileMetadata],
    user,
    db: "_AsyncSession",
    organization_id: "_UUID | None",
) -> list[FileMetadata]:
    """Filter a listing by per-item read permission.

    For each item, looks up the governing policy and runs the read evaluator.
    Items the user cannot read are silently omitted (no count leak).
    """
    out: list[FileMetadata] = []
    # Cache governing-policy lookups by (location, path-prefix) to avoid
    # N queries for a flat folder.
    cache: dict[tuple[str, str], object] = {}
    for item in items:
        cache_key = (location, item.path)
        if cache_key in cache:
            governing = cache[cache_key]
        else:
            governing = await find_governing_policy(location, item.path, db, organization_id)
            cache[cache_key] = governing

        if governing is None:
            continue
        doc = _PolicyDocument.model_validate(governing.policies)
        if _evaluate_action(
            FileAction.READ.value,
            doc,
            ctx=item,
            user=user,
            resolver=FileResolver(),
        ):
            out.append(item)
    return out


__all__.append("filter_listing")
```

- [ ] **Step 4: Update the list endpoint**

Modify: `api/src/routers/files.py`

In the `list` endpoint, after the S3-prefix list returns items, build `FileMetadata` for each (looking up file_index for `created_by`/`created_at` per item — single query, `IN (...)` on `path`), then pass through `filter_listing`.

```python
@router.post("/list", response_model=FileListResponse)
async def list_files(
    request: FileListRequest,
    ctx: Context,
    user: UserPrincipal,
) -> FileListResponse:
    # First check action=list against the listing prefix (cheap fail-fast).
    list_allowed = await evaluate_file_action(
        FileAction.LIST,
        location=request.location,
        path=request.prefix,
        file_meta=None,
        user=user,
        db=ctx.db,
        organization_id=ctx.organization_id,
    )
    if not list_allowed:
        raise HTTPException(status_code=403, detail="forbidden")

    # Existing S3 list logic returns paths + sizes.
    raw_items = await _s3_list(request.location, request.scope, request.prefix)

    # Bulk-load sidecar rows for these paths in one query.
    s3_keys = [resolve_s3_key(request.location, request.scope, it.path) for it in raw_items]
    from src.models.orm.file_index import FileIndex
    fi_rows = (await ctx.db.execute(
        select(FileIndex).where(FileIndex.path.in_(s3_keys))
    )).scalars().all()
    by_path = {fi.user_path: fi for fi in fi_rows}

    metas = [
        FileMetadata(
            location=request.location,
            path=it.path,
            created_by=(by_path.get(it.path) and by_path[it.path].created_by),
            created_at=(by_path.get(it.path) and by_path[it.path].created_at),
        )
        for it in raw_items
    ]

    visible = await filter_listing(request.location, metas, user, ctx.db, ctx.organization_id)

    return FileListResponse(items=[
        FileListMetadataItem(
            path=m.path,
            size=next(it.size for it in raw_items if it.path == m.path),
            created_by=str(m.created_by) if m.created_by else None,
            updated_at=...,
        )
        for m in visible
    ])
```

Adjust the response model `FileListMetadataItem` to include `created_by: str | None`.

- [ ] **Step 5: Run the test**

Run: `./test.sh tests/e2e/platform/test_file_policies_rest.py::test_list_filters_to_own_files -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add api/shared/file_policies.py api/src/routers/files.py api/tests/e2e/platform/test_file_policies_rest.py
git commit -m "feat(file-policies): per-item read filter on list endpoint

list now bulk-loads sidecar metadata for the listing, then runs the
per-item read evaluator. Items the user can't read are silently omitted
(no count leak)."
```

---

### Task 10: Batch signed-URL endpoint

**Files:**
- Modify: `api/src/routers/files.py`
- Modify: `api/shared/models.py` (signed-urls request/response)
- Test: `api/tests/e2e/platform/test_file_signed_urls_batch.py`

- [ ] **Step 1: Write the failing test**

Create: `api/tests/e2e/platform/test_file_signed_urls_batch.py`

```python
"""POST /api/files/signed-urls — batch signing with mixed allow/deny."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_batch_signed_urls_mixed(
    api_client_finance,
    finance_policy,  # from test_file_policies_rest
):
    resp = await api_client_finance.post("/api/files/signed-urls", json={
        "location": "shared",
        "scope": "x",
        "paths": [
            "finance/q1.pdf",
            "finance/q2.pdf",
            "hr/handbook.pdf",  # no policy → deny
        ],
        "method": "GET",
        "expires_in": 300,
    })
    assert resp.status_code == 200
    body = resp.json()
    allowed_paths = [item["path"] for item in body["allowed"]]
    denied_paths = [item["path"] for item in body["denied"]]
    assert set(allowed_paths) == {"finance/q1.pdf", "finance/q2.pdf"}
    assert "hr/handbook.pdf" in denied_paths
    for item in body["allowed"]:
        assert item["url"].startswith("http")
        assert item["expires_in"] == 300
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/e2e/platform/test_file_signed_urls_batch.py -v`
Expected: FAIL — endpoint doesn't exist.

- [ ] **Step 3: Add the request/response models to `shared/models.py`**

Use the contract shapes from the "Public API contracts" section at the top of this plan.

- [ ] **Step 4: Add the endpoint to `routers/files.py`**

```python
class FileSignedUrlsRequest(BaseModel):
    location: str
    scope: str | None = None
    paths: list[str] = Field(min_length=1, max_length=500)
    method: Literal["GET", "PUT"] = "GET"
    expires_in: int = Field(default=300, ge=30, le=3600)


@router.post("/signed-urls")
async def signed_urls_batch(
    request: FileSignedUrlsRequest,
    ctx: Context,
    user: UserPrincipal,
) -> FileSignedUrlsResponse:
    action = FileAction.READ if request.method == "GET" else FileAction.WRITE

    allowed: list[FileSignedUrlItem] = []
    denied: list[FileSignedUrlDenial] = []
    for path in request.paths:
        s3_key = resolve_s3_key(request.location, request.scope, path)
        fi = await _load_file_index(ctx.db, s3_key)
        file_meta = FileMetadata(
            location=request.location, path=path,
            created_by=fi.created_by if fi else None,
            created_at=fi.created_at if fi else None,
        )
        if await evaluate_file_action(
            action,
            location=request.location, path=path, file_meta=file_meta,
            user=user, db=ctx.db, organization_id=ctx.organization_id,
        ):
            url = await _sign_url(s3_key, request.method, request.expires_in)
            allowed.append(FileSignedUrlItem(path=path, url=url, expires_in=request.expires_in))
        else:
            denied.append(FileSignedUrlDenial(path=path, error="denied"))

    return FileSignedUrlsResponse(allowed=allowed, denied=denied)
```

- [ ] **Step 5: Run test**

Run: `./test.sh tests/e2e/platform/test_file_signed_urls_batch.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add api/src/routers/files.py api/shared/models.py api/tests/e2e/platform/test_file_signed_urls_batch.py
git commit -m "feat(file-policies): batch signed-URL endpoint

POST /api/files/signed-urls accepts a list of paths and a method;
returns split allowed/denied lists. One auth check, N HMACs.
Unblocks gallery use cases (per design spec §10.3 fallback —
useful standalone even if other surfaces slip)."
```

---

### Task 11: Validate endpoint for file policies

**Files:**
- Modify: `api/src/routers/files.py`
- Test: `api/tests/e2e/platform/test_file_policy_validate.py`

A `POST /api/files/policies/validate` mirrors `/api/tables/policies/validate`. Same `PolicyValidationResponse` shape; structured errors via the AST validator's `ValueError`s.

- [ ] **Step 1: Test + endpoint**

Create: `api/tests/e2e/platform/test_file_policy_validate.py`

```python
import pytest


@pytest.mark.asyncio
async def test_validate_clean(api_client_admin):
    resp = await api_client_admin.post("/api/files/policies/validate", json={
        "policies": [{"name": "admin", "actions": ["read"], "when": {"user": "is_platform_admin"}}],
    })
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


@pytest.mark.asyncio
async def test_validate_unknown_user_field(api_client_admin):
    resp = await api_client_admin.post("/api/files/policies/validate", json={
        "policies": [{"name": "x", "actions": ["read"], "when": {"user": "nonexistent"}}],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert any("unknown user field" in e["message"] for e in body["errors"])
```

Add the endpoint:

```python
@router.post("/policies/validate")
async def validate_file_policy(
    document: dict,
    user: UserPrincipal,
) -> PolicyValidationResponse:
    from shared.models import FilePolicyDocument
    errors: list[PolicyValidationError] = []
    try:
        FilePolicyDocument.model_validate(document)
    except ValidationError as e:
        for err in e.errors():
            errors.append(PolicyValidationError(
                path=".".join(str(p) for p in err["loc"]),
                message=err["msg"],
            ))
    return PolicyValidationResponse(ok=not errors, errors=errors)
```

Run: `./test.sh tests/e2e/platform/test_file_policy_validate.py -v`
Expected: PASS.

- [ ] **Step 2: Commit**

```bash
git add api/src/routers/files.py api/tests/e2e/platform/test_file_policy_validate.py
git commit -m "feat(file-policies): policy validate endpoint

POST /api/files/policies/validate returns structured per-field errors,
mirroring /api/tables/policies/validate."
```

---

## Sub-project D: CLI / MCP / Manifest

### Task 12: CRUD endpoints for FilePolicy

**Files:**
- Modify: `api/src/routers/files.py`
- Test: `api/tests/e2e/platform/test_file_policy_crud.py`

- [ ] **Step 1: Test + endpoints**

Create: `api/tests/e2e/platform/test_file_policy_crud.py`

```python
"""CRUD for FilePolicy via /api/files/policies."""
import pytest


@pytest.mark.asyncio
async def test_create_get_update_delete(api_client_admin):
    create = await api_client_admin.post("/api/files/policies", json={
        "location": "shared",
        "path": "test/x",
        "policies": {"policies": [{
            "name": "admin", "actions": ["read"], "when": {"user": "is_platform_admin"},
        }]},
    })
    assert create.status_code == 201
    pid = create.json()["id"]

    get = await api_client_admin.get(f"/api/files/policies/{pid}")
    assert get.status_code == 200
    assert get.json()["path"] == "test/x"

    update = await api_client_admin.put(f"/api/files/policies/{pid}", json={
        "policies": {"policies": [
            {"name": "admin", "actions": ["read", "write"], "when": {"user": "is_platform_admin"}},
        ]},
    })
    assert update.status_code == 200

    listr = await api_client_admin.get("/api/files/policies?location=shared")
    assert listr.status_code == 200
    assert any(p["id"] == pid for p in listr.json())

    delete = await api_client_admin.delete(f"/api/files/policies/{pid}")
    assert delete.status_code == 204
```

Add the endpoints (CREATE, GET, UPDATE, LIST, DELETE). These are admin-only (`CurrentSuperuser`) — only the data-plane endpoints relaxed in Task 8.

```python
@router.post("/policies", response_model=FilePolicyResponse, status_code=201)
async def create_file_policy(req: FilePolicyCreate, ctx: Context, user: CurrentSuperuser):
    from src.models.orm.file_policy import FilePolicy as FPOrm
    # Validate policy document
    from shared.models import FilePolicyDocument
    FilePolicyDocument.model_validate(req.policies.model_dump() if hasattr(req.policies, "model_dump") else req.policies)

    row = FPOrm(
        organization_id=req.organization_id,
        location=req.location,
        path=req.path,
        policies=req.policies.model_dump() if hasattr(req.policies, "model_dump") else req.policies,
        created_by=user.user_id,
    )
    ctx.db.add(row)
    await ctx.db.commit()
    await ctx.db.refresh(row)
    return FilePolicyResponse.model_validate(row, from_attributes=True)


@router.get("/policies/{policy_id}", response_model=FilePolicyResponse)
async def get_file_policy(policy_id: UUID, ctx: Context, user: CurrentSuperuser):
    from src.models.orm.file_policy import FilePolicy as FPOrm
    row = (await ctx.db.execute(select(FPOrm).where(FPOrm.id == policy_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(404)
    return FilePolicyResponse.model_validate(row, from_attributes=True)


@router.put("/policies/{policy_id}", response_model=FilePolicyResponse)
async def update_file_policy(policy_id: UUID, req: FilePolicyUpdate, ctx: Context, user: CurrentSuperuser):
    from src.models.orm.file_policy import FilePolicy as FPOrm
    row = (await ctx.db.execute(select(FPOrm).where(FPOrm.id == policy_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(404)
    row.policies = req.policies.model_dump() if hasattr(req.policies, "model_dump") else req.policies
    await ctx.db.commit()
    await ctx.db.refresh(row)
    return FilePolicyResponse.model_validate(row, from_attributes=True)


@router.get("/policies", response_model=list[FilePolicyResponse])
async def list_file_policies(
    ctx: Context, user: CurrentSuperuser,
    location: str | None = None,
    organization_id: UUID | None = None,
):
    from src.models.orm.file_policy import FilePolicy as FPOrm
    stmt = select(FPOrm)
    if location is not None:
        stmt = stmt.where(FPOrm.location == location)
    if organization_id is not None:
        stmt = stmt.where(FPOrm.organization_id == organization_id)
    rows = (await ctx.db.execute(stmt)).scalars().all()
    return [FilePolicyResponse.model_validate(r, from_attributes=True) for r in rows]


@router.delete("/policies/{policy_id}", status_code=204)
async def delete_file_policy(policy_id: UUID, ctx: Context, user: CurrentSuperuser):
    from src.models.orm.file_policy import FilePolicy as FPOrm
    await ctx.db.execute(FPOrm.__table__.delete().where(FPOrm.id == policy_id))
    await ctx.db.commit()
```

Run: `./test.sh tests/e2e/platform/test_file_policy_crud.py -v`
Expected: PASS.

- [ ] **Step 2: Commit**

```bash
git add api/src/routers/files.py api/tests/e2e/platform/test_file_policy_crud.py
git commit -m "feat(file-policies): CRUD endpoints under /api/files/policies"
```

---

### Task 13: CLI `bifrost files policies <verb>`

**Files:**
- Modify: `api/bifrost/files_policies_cli.py` (new submodule)
- Modify: `api/bifrost/__main__.py` or wherever the CLI command tree is wired
- Modify: `api/bifrost/dto_flags.py` — register `FilePolicyCreate` / `FilePolicyUpdate`
- Test: `api/tests/unit/test_dto_flags.py` (will fail until DTOs registered)

- [ ] **Step 1: Confirm baseline DTO parity**

Run: `./test.sh tests/unit/test_dto_flags.py -v`
Expected: PASS (baseline).

- [ ] **Step 2: Add CLI commands**

Look at `api/bifrost/tables_policies_cli.py` (table policies CLI from PR #178) for the canonical shape. Mirror it.

Create: `api/bifrost/files_policies_cli.py`

```python
"""bifrost files policies <verb>"""
import json
from pathlib import Path

import click

from bifrost._client import client


@click.group(name="policies")
def policies_group():
    """Manage file policies."""


@policies_group.command(name="set")
@click.option("--location", required=True)
@click.option("--path", required=True)
@click.option("--policies-file", type=click.Path(exists=True))
@click.option("--policies", "policies_inline")
@click.option("--organization-id", default=None)
def set_policy(location, path, policies_file, policies_inline, organization_id):
    """Create or update a file policy at (location, path)."""
    if policies_file:
        doc = json.loads(Path(policies_file).read_text())
    elif policies_inline:
        doc = json.loads(policies_inline)
    else:
        raise click.UsageError("provide --policies-file or --policies")

    # Find-or-create
    existing = client.get("/api/files/policies", params={"location": location}).json()
    match = next((p for p in existing if p["path"] == path), None)
    if match:
        client.put(f"/api/files/policies/{match['id']}", json={"policies": doc})
    else:
        client.post("/api/files/policies", json={
            "location": location, "path": path, "policies": doc,
            "organization_id": organization_id,
        })


@policies_group.command(name="get")
@click.option("--location", required=True)
@click.option("--path", required=True)
def get_policy(location, path):
    rows = client.get("/api/files/policies", params={"location": location}).json()
    match = next((p for p in rows if p["path"] == path), None)
    if not match:
        raise click.ClickException(f"no policy at ({location}, {path})")
    click.echo(json.dumps(match, indent=2))


@policies_group.command(name="list")
@click.option("--location", default=None)
def list_policies(location):
    params = {"location": location} if location else {}
    rows = client.get("/api/files/policies", params=params).json()
    click.echo(json.dumps(rows, indent=2))


@policies_group.command(name="delete")
@click.option("--location", required=True)
@click.option("--path", required=True)
def delete_policy(location, path):
    rows = client.get("/api/files/policies", params={"location": location}).json()
    match = next((p for p in rows if p["path"] == path), None)
    if not match:
        return
    client.delete(f"/api/files/policies/{match['id']}")
```

Wire it into the `files` group in `__main__.py`:

```python
from bifrost.files_policies_cli import policies_group as _files_policies_group
files_group.add_command(_files_policies_group)
```

- [ ] **Step 3: Register DTOs**

Modify: `api/bifrost/dto_flags.py`

Find the `DTO_REGISTRY` (or equivalent) and add:

```python
"FilePolicyCreate": FilePolicyCreate,
"FilePolicyUpdate": FilePolicyUpdate,
```

Confirm:

Run: `./test.sh tests/unit/test_dto_flags.py -v`
Expected: PASS.

- [ ] **Step 4: CLI smoke test**

Add: `api/tests/unit/test_files_policies_cli.py` — invokes Click via `CliRunner` and asserts the help text + a roundtrip against a mocked client.

Run: `./test.sh tests/unit/test_files_policies_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/bifrost/files_policies_cli.py api/bifrost/__main__.py api/bifrost/dto_flags.py api/tests/unit/test_files_policies_cli.py
git commit -m "feat(file-policies): bifrost files policies set/get/list/delete"
```

---

### Task 14: Manifest serialization

**Files:**
- Modify: `api/bifrost/manifest.py`
- Modify: `api/src/services/manifest_generator.py`
- Modify: `api/src/services/github_sync.py`
- Modify: `api/bifrost/portable.py`
- Test: `api/tests/unit/test_manifest.py` (extend)
- Test: `api/tests/e2e/platform/test_git_sync_local.py` (extend)

- [ ] **Step 1: Add `ManifestFilePolicy` to `manifest.py`**

Modify: `api/bifrost/manifest.py`

```python
class ManifestFilePolicy(BaseModel):
    """Portable serialization of a FilePolicy row."""
    id: UUID | None = None  # absent for portable export; assigned on import
    organization_id: UUID | None = None  # null for global policies
    location: str
    path: str
    policies: dict  # JSON document, validated as FilePolicyDocument
```

If there's a top-level Manifest container that lists `tables`, `agents`, `forms`, etc., add `file_policies: dict[str, ManifestFilePolicy] = Field(default_factory=dict)`.

- [ ] **Step 2: Generator (DB → manifest)**

Modify: `api/src/services/manifest_generator.py`

Find the existing table-policy generator (likely a function that iterates `Table` rows and emits a section). Add an analogous loop over `FilePolicy` rows:

```python
async def _generate_file_policies(db: AsyncSession, organization_id: UUID | None) -> dict[str, dict]:
    from src.models.orm.file_policy import FilePolicy
    stmt = select(FilePolicy)
    if organization_id is not None:
        stmt = stmt.where(FilePolicy.organization_id == organization_id)
    rows = (await db.execute(stmt)).scalars().all()
    return {
        str(r.id): {
            "id": str(r.id),
            "organization_id": str(r.organization_id) if r.organization_id else None,
            "location": r.location,
            "path": r.path,
            "policies": r.policies,
        }
        for r in rows
    }
```

Hook it into the top-level generator that produces the manifest file.

- [ ] **Step 3: Importer (manifest → DB)**

Modify: `api/src/services/github_sync.py`

Add `_resolve_file_policy` following the non-destructive upsert pattern documented in CLAUDE.md ("Critical: non-destructive upsert pattern"):

```python
async def _resolve_file_policy(
    db: AsyncSession,
    entry: ManifestFilePolicy,
    organization_id: UUID | None,
    user_id: UUID,
) -> None:
    from src.models.orm.file_policy import FilePolicy
    existing = (await db.execute(
        select(FilePolicy)
        .where(FilePolicy.organization_id == organization_id)
        .where(FilePolicy.location == entry.location)
        .where(FilePolicy.path == entry.path)
    )).scalar_one_or_none()
    if existing:
        existing.policies = entry.policies
    else:
        db.add(FilePolicy(
            organization_id=organization_id,
            location=entry.location,
            path=entry.path,
            policies=entry.policies,
            created_by=user_id,
        ))
```

Add stale-entity cleanup at the section level: delete any `FilePolicy` rows not in the manifest.

- [ ] **Step 4: Portable scrub**

Modify: `api/bifrost/portable.py`

On portable export, scrub `organization_id` (set to None — only global policies round-trip). On import, re-stamp with the target org id.

- [ ] **Step 5: Round-trip unit test**

Append to: `api/tests/unit/test_manifest.py`

```python
def test_file_policy_round_trip():
    """Manifest FilePolicy serializes and re-parses identically."""
    src = ManifestFilePolicy(
        organization_id=None,
        location="shared",
        path="finance",
        policies={"policies": [{
            "name": "admin", "actions": ["read"], "when": {"user": "is_platform_admin"},
        }]},
    )
    blob = src.model_dump_json()
    reparsed = ManifestFilePolicy.model_validate_json(blob)
    assert reparsed.location == "shared"
    assert reparsed.path == "finance"
    assert reparsed.policies == src.policies
```

Run: `./test.sh tests/unit/test_manifest.py -v`
Expected: PASS.

- [ ] **Step 6: E2E git sync test**

Append to: `api/tests/e2e/platform/test_git_sync_local.py`

Add a test that creates a `FilePolicy`, exports to a local manifest dir, imports back into an empty target DB, and asserts the row matches.

- [ ] **Step 7: Commit**

```bash
git add api/bifrost/manifest.py api/src/services/manifest_generator.py api/src/services/github_sync.py api/bifrost/portable.py api/tests/unit/test_manifest.py api/tests/e2e/platform/test_git_sync_local.py
git commit -m "feat(file-policies): manifest round-trip for FilePolicy

ManifestFilePolicy serializes (location, path, policies). Generator
emits, github_sync upserts non-destructively, portable export scrubs
organization_id. Round-trip unit + e2e tests cover the lifecycle."
```

---

## Sub-project E: Web SDK

### Task 15: `client/src/lib/app-sdk/files.ts` — TypeScript SDK

**Files:**
- Create: `client/src/lib/app-sdk/files.ts`
- Test: `client/src/lib/app-sdk/files.test.ts`

- [ ] **Step 1: Regenerate types**

Run: `cd client && npm run generate:types`
Expected: `client/src/lib/v1.d.ts` updated with new file-policy endpoints.

- [ ] **Step 2: Write the failing vitest**

Create: `client/src/lib/app-sdk/files.test.ts`

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { createFilesSdk } from "./files";

const mockFetch = vi.fn();
beforeEach(() => {
  mockFetch.mockReset();
  global.fetch = mockFetch;
});

describe("files SDK", () => {
  it("read calls POST /api/files/read with the path", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ content: "hello" }),
    });
    const sdk = createFilesSdk({ location: "shared", scope: "x" });
    const content = await sdk.read("a.txt");
    expect(content).toBe("hello");
    expect(mockFetch).toHaveBeenCalledWith(
      "/api/files/read",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("signedUrls returns allowed and denied lists", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        allowed: [{ path: "a.txt", url: "https://x", expires_in: 300 }],
        denied: [{ path: "b.txt", error: "denied" }],
      }),
    });
    const sdk = createFilesSdk({ location: "shared", scope: "x" });
    const result = await sdk.signedUrls(["a.txt", "b.txt"]);
    expect(result.allowed).toHaveLength(1);
    expect(result.denied).toHaveLength(1);
    expect(result.denied[0].error).toBe("denied");
  });

  it("upload uses a signed PUT URL", async () => {
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ url: "https://put-here", expires_in: 300 }),
      })
      .mockResolvedValueOnce({ ok: true });
    const sdk = createFilesSdk({ location: "shared", scope: "x" });
    const blob = new Blob(["hi"], { type: "text/plain" });
    await sdk.upload("a.txt", blob);
    expect(mockFetch).toHaveBeenNthCalledWith(
      2,
      "https://put-here",
      expect.objectContaining({ method: "PUT", body: blob }),
    );
  });

  it("read raises on 403", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 403,
      json: async () => ({ detail: "forbidden" }),
    });
    const sdk = createFilesSdk({ location: "shared", scope: "x" });
    await expect(sdk.read("a.txt")).rejects.toThrow(/403|forbidden/i);
  });
});
```

Run: `cd client && npm run test files`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement the SDK**

Create: `client/src/lib/app-sdk/files.ts`

```typescript
import type { components } from "@/lib/v1";

export type FileMetadata = {
  path: string;
  size: number;
  updated_at: string;
  created_by: string | null;
};
export type SignedUrl = { url: string; expires_in: number };
export type SignedUrlsResult = {
  allowed: { path: string; url: string; expires_in: number }[];
  denied: { path: string; error: "not_found" | "denied" }[];
};

export interface FilesSdk {
  read(path: string): Promise<string>;
  write(path: string, content: string | ArrayBuffer): Promise<void>;
  delete(path: string): Promise<void>;
  list(prefix: string): Promise<FileMetadata[]>;
  exists(path: string): Promise<boolean>;
  signedUrl(path: string, method?: "GET" | "PUT"): Promise<SignedUrl>;
  signedUrls(paths: string[], method?: "GET" | "PUT"): Promise<SignedUrlsResult>;
  upload(path: string, blob: Blob): Promise<void>;
  download(path: string): Promise<Blob>;
}

export type FilesSdkConfig = {
  location: string;
  scope?: string;
  baseUrl?: string;
};

async function call(url: string, init: RequestInit): Promise<Response> {
  const resp = await fetch(url, init);
  if (!resp.ok) {
    let detail = `${resp.status}`;
    try {
      const body = await resp.json();
      if (body.detail) detail += `: ${body.detail}`;
    } catch {}
    throw new Error(detail);
  }
  return resp;
}

export function createFilesSdk(config: FilesSdkConfig): FilesSdk {
  const { location, scope, baseUrl = "" } = config;

  return {
    async read(path) {
      const resp = await call(`${baseUrl}/api/files/read`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ location, scope, path }),
      });
      return (await resp.json()).content;
    },

    async write(path, content) {
      await call(`${baseUrl}/api/files/write`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ location, scope, path, content }),
      });
    },

    async delete(path) {
      await call(`${baseUrl}/api/files/delete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ location, scope, path }),
      });
    },

    async list(prefix) {
      const resp = await call(`${baseUrl}/api/files/list`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ location, scope, prefix }),
      });
      return (await resp.json()).items;
    },

    async exists(path) {
      const resp = await fetch(`${baseUrl}/api/files/exists`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ location, scope, path }),
      });
      if (resp.status === 403) return false; // existence-non-leak: deny == not-found
      return (await resp.json()).exists;
    },

    async signedUrl(path, method = "GET") {
      const resp = await call(`${baseUrl}/api/files/signed-url`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ location, scope, path, method }),
      });
      return resp.json();
    },

    async signedUrls(paths, method = "GET") {
      const resp = await call(`${baseUrl}/api/files/signed-urls`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ location, scope, paths, method }),
      });
      return resp.json();
    },

    async upload(path, blob) {
      const { url } = await this.signedUrl(path, "PUT");
      await call(url, { method: "PUT", body: blob });
    },

    async download(path) {
      const { url } = await this.signedUrl(path, "GET");
      const resp = await call(url, { method: "GET" });
      return resp.blob();
    },
  };
}
```

- [ ] **Step 4: Run vitest**

Run: `cd client && npm run test files`
Expected: PASS — all four tests green.

- [ ] **Step 5: Type-check + lint**

Run: `cd client && npm run tsc && npm run lint`
Expected: 0 errors.

- [ ] **Step 6: Commit**

```bash
git add client/src/lib/app-sdk/files.ts client/src/lib/app-sdk/files.test.ts client/src/lib/v1.d.ts
git commit -m "feat(app-sdk): Files SDK for browser apps

createFilesSdk({location, scope}) exposes read/write/delete/list/
exists/signedUrl/signedUrls/upload/download. All calls go through
/api/files/* and are subject to file-policies enforcement.
exists silently maps 403 to false (existence-non-leak)."
```

---

### Task 16: `useFiles(prefix)` React hook

**Files:**
- Create: `client/src/lib/app-sdk/use-files.tsx`
- Test: `client/src/lib/app-sdk/use-files.test.tsx`

The hook returns a live listing — REST list seed + websocket invalidation. For this task, ship the REST-seed half only; the websocket half lands in Task 22.

- [ ] **Step 1: Write the failing test**

Create: `client/src/lib/app-sdk/use-files.test.tsx`

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { useFiles } from "./use-files";

const mockFetch = vi.fn();
beforeEach(() => {
  mockFetch.mockReset();
  global.fetch = mockFetch;
});

describe("useFiles", () => {
  it("returns items from /api/files/list", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ items: [{ path: "a.txt", size: 1, updated_at: "", created_by: null }] }),
    });
    const { result } = renderHook(() =>
      useFiles({ location: "shared", scope: "x", prefix: "" }),
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.items).toHaveLength(1);
    expect(result.current.error).toBeNull();
  });

  it("surfaces 403 as an access-denied error", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 403,
      json: async () => ({ detail: "forbidden" }),
    });
    const { result } = renderHook(() =>
      useFiles({ location: "shared", scope: "x", prefix: "" }),
    );
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("access_denied");
    expect(result.current.items).toEqual([]);
  });
});
```

- [ ] **Step 2: Implement the hook**

Create: `client/src/lib/app-sdk/use-files.tsx`

```typescript
import { useEffect, useState } from "react";
import { createFilesSdk, type FileMetadata } from "./files";

export type UseFilesOptions = {
  location: string;
  scope?: string;
  prefix: string;
};

export type UseFilesResult = {
  items: FileMetadata[];
  loading: boolean;
  error: "access_denied" | "other" | null;
  refresh: () => Promise<void>;
};

export function useFiles({ location, scope, prefix }: UseFilesOptions): UseFilesResult {
  const [items, setItems] = useState<FileMetadata[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<"access_denied" | "other" | null>(null);

  const refresh = async () => {
    const sdk = createFilesSdk({ location, scope });
    setLoading(true);
    setError(null);
    try {
      const result = await sdk.list(prefix);
      setItems(result);
    } catch (e: any) {
      if (String(e.message).startsWith("403")) setError("access_denied");
      else setError("other");
      setItems([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
    // Live-listing via websocket lands in Task 22 (subscriptions).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location, scope, prefix]);

  return { items, loading, error, refresh };
}
```

- [ ] **Step 3: Run tests + type-check**

Run: `cd client && npm run test use-files && npm run tsc`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add client/src/lib/app-sdk/use-files.tsx client/src/lib/app-sdk/use-files.test.tsx
git commit -m "feat(app-sdk): useFiles hook (REST-seed half)

Returns {items, loading, error, refresh}. Maps 403 to error='access_denied'.
Websocket invalidation lands in the subscriptions task."
```

---

### Task 17: Playwright e2e — embedded app uses Files SDK

**Files:**
- Create: `client/e2e/files-app-direct.spec.ts`

- [ ] **Step 1: Write the spec**

Create: `client/e2e/files-app-direct.spec.ts`

```typescript
import { test, expect } from "@playwright/test";

test("embedded app reads + writes files via SDK without executing a workflow", async ({ page, request }) => {
  // Pre-seed an admin-bypass policy at the test location
  // (use the admin API; assumes a fixture login as superuser)
  await request.post("/api/files/policies", {
    data: {
      location: "shared",
      path: "test/sdk-direct",
      policies: { policies: [
        { name: "everyone_for_test", actions: ["read", "write", "list"], when: null },
      ]},
    },
  });

  // Navigate to a built-in test app that exercises the Files SDK
  // (the app needs to exist — see step 2)
  await page.goto("/apps/files-sdk-smoke");

  // Trigger a write
  await page.getByRole("button", { name: "Write file" }).click();
  await expect(page.getByText("Wrote successfully")).toBeVisible();

  // Trigger a read
  await page.getByRole("button", { name: "Read file" }).click();
  await expect(page.getByText("hello world")).toBeVisible();

  // Assert no workflow execution was created
  const execResp = await request.get("/api/agent-runs?limit=10");
  const recent = (await execResp.json()).items;
  expect(recent.find((r: any) => r.app_slug === "files-sdk-smoke")).toBeFalsy();
});
```

- [ ] **Step 2: Build the smoke app**

Create `apps/files-sdk-smoke/index.tsx` (built-in app for E2E) that uses `createFilesSdk` to write then read a file when buttons are clicked. Mirror the smallest existing test app for setup boilerplate.

- [ ] **Step 3: Run**

Run: `./test.sh client e2e e2e/files-app-direct.spec.ts`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add client/e2e/files-app-direct.spec.ts apps/files-sdk-smoke/
git commit -m "test(file-policies): e2e — app uses Files SDK direct, no workflow"
```

---

## Sub-project F: Admin UI

### Task 18: Shared tree-view component

**Files:**
- Create: `client/src/components/files/FileTree.tsx`
- Test: `client/src/components/files/FileTree.test.tsx`

The file browser, the rule editor's path picker, and the renamer all use the same tree component. Build it once, parameterize the right-click menu via props.

- [ ] **Step 1: Test**

Create: `client/src/components/files/FileTree.test.tsx`

```typescript
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { FileTree } from "./FileTree";

describe("FileTree", () => {
  it("renders nested paths", () => {
    render(
      <FileTree
        items={[
          { path: "a/b.txt", isFile: true },
          { path: "a/c/d.txt", isFile: true },
          { path: "e.txt", isFile: true },
        ]}
      />,
    );
    expect(screen.getByText("a")).toBeInTheDocument();
    expect(screen.getByText("b.txt")).toBeInTheDocument();
    expect(screen.getByText("e.txt")).toBeInTheDocument();
  });

  it("hides scope segment by default", () => {
    render(
      <FileTree
        items={[{ path: "a/b.txt", isFile: true, scope: "org-x" }]}
      />,
    );
    expect(screen.queryByText("org-x")).not.toBeInTheDocument();
  });

  it("shows scope when admin-mode is on", () => {
    render(
      <FileTree
        items={[{ path: "a/b.txt", isFile: true, scope: "org-x" }]}
        showRawScopes
      />,
    );
    expect(screen.getByText("org-x")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Implement**

Create: `client/src/components/files/FileTree.tsx`

A standard recursive tree built on shadcn's `Collapsible` + `ContextMenu`. Props:
- `items: { path: string; isFile: boolean; scope?: string; created_by?: string }[]`
- `showRawScopes?: boolean`
- `onFileContextMenu?: (path) => MenuItem[]`
- `onFolderContextMenu?: (path) => MenuItem[]`

Group items by their first path segment; recurse on subpaths. The implementation is ~150 LoC of normal tree rendering — follow `apps/file-browser-demo/...` if such a precedent exists; otherwise build fresh on Radix Collapsible.

- [ ] **Step 3: Run tests**

Run: `cd client && npm run test FileTree`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add client/src/components/files/FileTree.tsx client/src/components/files/FileTree.test.tsx
git commit -m "feat(file-policies): shared FileTree component with scope-hiding"
```

---

### Task 19: File browser page

**Files:**
- Create: `client/src/pages/files/FileBrowser.tsx`
- Test: `client/src/pages/files/FileBrowser.test.tsx`
- Modify: `client/src/router.tsx` (add route `/files`)

- [ ] **Step 1: Test**

Create: `client/src/pages/files/FileBrowser.test.tsx`

```typescript
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { FileBrowser } from "./FileBrowser";

vi.mock("@/lib/app-sdk/use-files", () => ({
  useFiles: () => ({
    items: [{ path: "shared/finance/q1.pdf", size: 1, updated_at: "", created_by: null }],
    loading: false,
    error: null,
    refresh: vi.fn(),
  }),
}));

describe("FileBrowser", () => {
  it("renders the tree under the current location", () => {
    render(<FileBrowser />);
    expect(screen.getByText("q1.pdf")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Implement the page**

Create: `client/src/pages/files/FileBrowser.tsx`

Compose:
- Location picker (top-left): dropdown of known locations
- OrgSelect: filters server-side
- "Show raw scopes" admin toggle
- `<FileTree>` with right-click menus that route to the rule editor + the tester
- Action buttons: "Upload file", "Add policy at…"

- [ ] **Step 3: Register the route**

Modify: `client/src/router.tsx`

```typescript
{ path: "/files", element: <FileBrowser />, requiresAuth: true }
```

- [ ] **Step 4: Run + smoke-test in browser**

Run: `cd client && npm run test FileBrowser`
Expected: PASS.

Then navigate to the dev URL and open `/files`. Verify the tree renders and right-click works.

- [ ] **Step 5: Commit**

```bash
git add client/src/pages/files/FileBrowser.tsx client/src/pages/files/FileBrowser.test.tsx client/src/router.tsx
git commit -m "feat(file-policies): file browser page"
```

---

### Task 20: Rule editor (Monaco + templates + reference panel)

**Files:**
- Create: `client/src/components/files/FilePolicyEditor.tsx`
- Test: `client/src/components/files/FilePolicyEditor.test.tsx`

The shape is **identical to** `TablePolicyEditor` (built in PR #178). The differences:
- Action vocab: `read/write/delete/list` (vs tables: `read/create/update/delete`)
- Reference panel: `{file: created_by | created_at | path | location}` (vs `{row: ...}`)
- Templates: "Everyone reads, role X writes" / "Only the file's creator" / "Admin bypass" — adapted action vocab

- [ ] **Step 1: Test**

Mirror the structure of `client/src/components/tables/TablePolicyEditor.test.tsx` — at minimum:
- Renders without crashing
- Loads a template into the editor
- Calls the validate endpoint on change with debounce
- Surfaces structured errors from the validator
- Shows the `{file: ...}` reference panel

- [ ] **Step 2: Implement**

Mirror `client/src/components/tables/TablePolicyEditor.tsx`. Where it imports `compile_read_filter`-shaped helpers or the table validator, swap for the file-policies analogues. The Monaco JSON schema validation can reuse the same `Expr` JSON schema since the AST is shared.

- [ ] **Step 3: Run + smoke-test**

Run: `cd client && npm run test FilePolicyEditor`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add client/src/components/files/FilePolicyEditor.tsx client/src/components/files/FilePolicyEditor.test.tsx
git commit -m "feat(file-policies): FilePolicyEditor with Monaco JSON + templates"
```

---

### Task 21: Effective-access tester

**Files:**
- Create: `client/src/components/files/EffectiveAccessTester.tsx`
- Create: `api/src/routers/files.py` — add `/api/files/policies/test` endpoint
- Test (client): `client/src/components/files/EffectiveAccessTester.test.tsx`
- Test (backend): `api/tests/e2e/platform/test_file_policy_test_endpoint.py`

This is the safety-net surface from the spec §"Effective access tester". Backend endpoint runs the real evaluator with a (possibly synthesized) user and returns the resolution trail.

- [ ] **Step 1: Backend endpoint test**

Create: `api/tests/e2e/platform/test_file_policy_test_endpoint.py`

```python
import pytest


@pytest.mark.asyncio
async def test_resolution_trail(api_client_admin, finance_policy):
    resp = await api_client_admin.post("/api/files/policies/test", json={
        "user_id": "<some-user-uuid>",
        "extra_roles": ["finance"],  # hypothetical
        "location": "shared",
        "path": "finance/q1.pdf",
        "action": "read",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["allowed"] is True
    assert body["matched_policy"]["path"] == "finance"
    assert body["matched_rule"] == "finance_team"
    assert "trace" in body
```

- [ ] **Step 2: Implement the endpoint**

```python
@router.post("/policies/test")
async def test_file_policy_access(
    request: "FilePolicyTestRequest",
    ctx: Context,
    user: CurrentSuperuser,  # tester is admin-only
) -> "FilePolicyTestResponse":
    # Synthesize a hypothetical user
    real = await _load_user(ctx.db, request.user_id)
    hypo = real.with_extra_roles(request.extra_roles)

    governing = await find_governing_policy(
        request.location, request.path, ctx.db, ctx.organization_id,
    )
    if not governing:
        return FilePolicyTestResponse(allowed=False, matched_policy=None, matched_rule=None, trace=[])

    # Walk rules manually to emit a trail
    doc = FilePolicyDocument.model_validate(governing.policies)
    trace = []
    for rule in doc.policies:
        if request.action not in rule.actions:
            continue
        rule_result = (
            rule.when is None
            or evaluate(rule.when, ctx=None, user=hypo, resolver=FileResolver())
        )
        trace.append({"rule": rule.name, "result": rule_result})
        if rule_result:
            return FilePolicyTestResponse(
                allowed=True,
                matched_policy={"id": str(governing.id), "path": governing.path},
                matched_rule=rule.name,
                trace=trace,
            )
    return FilePolicyTestResponse(allowed=False, matched_policy={"id": str(governing.id), "path": governing.path}, matched_rule=None, trace=trace)
```

Define `FilePolicyTestRequest`/`FilePolicyTestResponse` in `shared/models.py`.

- [ ] **Step 3: Client component**

Create: `client/src/components/files/EffectiveAccessTester.tsx`

Form: user-picker, optional "extra roles" multi-select, path input, action select. Submit calls `/api/files/policies/test`. Render the trail as a numbered list with green/red dots.

- [ ] **Step 4: Add to FileBrowser as "Test access here…" right-click**

Modify: `FileBrowser.tsx`

Right-click on file or folder → "Test access here…" → opens the tester in a Drawer with `path` pre-filled.

Standalone page: `/files/access-tester` for support cases.

- [ ] **Step 5: Run tests**

Run: `./test.sh tests/e2e/platform/test_file_policy_test_endpoint.py -v`
Then: `cd client && npm run test EffectiveAccessTester`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add api/src/routers/files.py api/shared/models.py api/tests/e2e/platform/test_file_policy_test_endpoint.py client/src/components/files/EffectiveAccessTester.tsx client/src/components/files/EffectiveAccessTester.test.tsx client/src/pages/files/FileBrowser.tsx
git commit -m "feat(file-policies): effective-access tester + /api/files/policies/test

Renders the resolution trail (which policy matched, which rule fired,
per-rule pass/fail). Supports hypothetical-user testing: 'as Alice +
Role Y, even though Alice doesn't have Role Y'. Available from the
file browser right-click ('Test access here…') and a standalone page."
```

---

## Sub-project G: Subscriptions

### Task 22: Backend — files websocket channel

**Files:**
- Modify: `api/src/routers/websocket.py`
- Modify: `api/src/core/pubsub.py` (new `publish_file_change` / `publish_file_policy_changed`)
- Modify: `api/shared/file_policies.py` (call publish on write/delete from Tasks 7-8 — wire-up now)
- Test: `api/tests/e2e/platform/test_file_subscriptions.py`

- [ ] **Step 1: Test the websocket flow**

Create: `api/tests/e2e/platform/test_file_subscriptions.py`

```python
import pytest


@pytest.mark.asyncio
async def test_subscribe_accepts_when_user_can_list(ws_client_finance, finance_policy):
    async with ws_client_finance.subscribe("files:shared:finance") as ws:
        # subscribe handshake succeeded — connection stays open
        assert await ws.recv() is None  # no immediate message


@pytest.mark.asyncio
async def test_subscribe_rejects_when_user_cannot_list(ws_client_plain, finance_policy):
    with pytest.raises(Exception, match="subscription_denied"):
        async with ws_client_plain.subscribe("files:shared:finance"):
            pass


@pytest.mark.asyncio
async def test_creator_only_filtering(ws_client_plain, ws_client_other_plain, own_uploads_policy, api_client_plain):
    """When two users subscribe to the same prefix, each only sees their own writes."""
    async with ws_client_plain.subscribe("files:shared:user-uploads") as ws_a:
        async with ws_client_other_plain.subscribe("files:shared:user-uploads") as ws_b:
            await api_client_plain.post("/api/files/write", json={
                "location": "shared", "scope": "x",
                "path": "user-uploads/a.txt", "content": "a",
            })
            msg_a = await ws_a.recv(timeout=2)
            assert msg_a["action"] == "insert"
            msg_b = await ws_b.recv_or_none(timeout=2)
            assert msg_b is None  # filtered out
```

- [ ] **Step 2: Implement the channel handler**

In `routers/websocket.py`, add the `files:` channel kind. Handshake: parse `(location, prefix)`, call `evaluate_file_action(FileAction.LIST, ...)` — if false, reply `subscription_denied` and close.

- [ ] **Step 3: Implement `publish_file_change` / `publish_file_policy_changed`**

In `core/pubsub.py`:

```python
async def publish_file_change(location: str, path: str, action: str, file_meta: dict) -> None:
    # Pub to all "files:{location}:{prefix}" channels where prefix is a prefix of `path`.
    # Server-side per-recipient filter: if subscriber's effective read is Creator-only,
    # drop events whose created_by != subscriber.user_id.
    ...

async def publish_file_policy_changed(location: str, path: str) -> None:
    # All subscriptions under (location, path) re-probe authorization;
    # those whose probe is now False receive `subscription_revoked` and close.
    ...
```

- [ ] **Step 4: Wire publish calls into write/delete paths**

In `routers/files.py`, after each successful write/delete (post-sidecar-update), call `publish_file_change(...)`.

In the CRUD endpoints for FilePolicy, after each create/update/delete, call `publish_file_policy_changed(...)`.

- [ ] **Step 5: Run**

Run: `./test.sh tests/e2e/platform/test_file_subscriptions.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add api/src/routers/websocket.py api/src/core/pubsub.py api/src/routers/files.py api/tests/e2e/platform/test_file_subscriptions.py
git commit -m "feat(file-policies): websocket subscriptions for prefix change events

files:{location}:{prefix} channel. Subscribe handshake runs the list
evaluator. publish_file_change broadcasts on write/delete with
per-recipient Creator filter. publish_file_policy_changed forces
re-probe on subscribed clients (subscription_revoked if denied)."
```

---

### Task 23: Frontend — wire websocket into `useFiles`

**Files:**
- Modify: `client/src/lib/app-sdk/use-files.tsx`
- Test: `client/src/lib/app-sdk/use-files.test.tsx`

- [ ] **Step 1: Extend `useFiles` to open a websocket**

Subscribe to `files:{location}:{prefix}`. On `insert`/`update`/`delete`, mutate the `items` array locally without a refetch. On `subscription_revoked`, set `error="access_denied"` and clear `items`.

- [ ] **Step 2: Extend tests**

Cover: insert event appends, delete removes, update mutates, `subscription_revoked` resets state.

- [ ] **Step 3: Run + commit**

Run: `cd client && npm run test use-files`
Expected: PASS.

```bash
git add client/src/lib/app-sdk/use-files.tsx client/src/lib/app-sdk/use-files.test.tsx
git commit -m "feat(app-sdk): useFiles subscribes to file change events

Live listing via websocket. Handles insert/update/delete events and
subscription_revoked. No refetch on each event — mutates local state."
```

---

### Task 24: Folder rename as a UI primitive

**Files:**
- Modify: `api/src/routers/files.py` (rename endpoint)
- Modify: `client/src/pages/files/FileBrowser.tsx` (rename dialog)
- Test: `client/e2e/files-rename.spec.ts`

S3 doesn't rename — the UI does S3 copy + delete + sidecar update + FilePolicy path update in one user action.

- [ ] **Step 1: Backend rename endpoint test + impl**

Endpoint `POST /api/files/rename` accepts `(location, scope, old_prefix, new_prefix)` and:
1. Updates every `FilePolicy` row whose `(location, path)` falls under the old prefix (one transaction).
2. Lists S3 keys under the old prefix.
3. For each key: copy to new prefix; update `file_index` sidecar (`user_path`, plus the S3 key in the path PK — which means a delete-old + insert-new in the sidecar table); delete old S3 key.
4. Return `{moved_files: N, updated_policies: M}`.

Test: end-to-end rename with both files and a policy under the old prefix, assert all three side effects.

- [ ] **Step 2: UI dialog**

In `FileBrowser.tsx`, "Rename folder" right-click → dialog. Pre-flight: call `POST /api/files/rename/preview` (counts only) to surface "this will move X files and update Y policies".

- [ ] **Step 3: Playwright spec**

```typescript
test("rename folder updates files, sidecar, and policies", async ({ page, request }) => {
  // Seed: a policy + two files under "test/old/"
  // ... seed setup ...
  await page.goto("/files");
  await page.getByText("old").click({ button: "right" });
  await page.getByRole("menuitem", { name: "Rename" }).click();
  await page.getByPlaceholder("New name").fill("new");
  await expect(page.getByText("This will move 2 files and update 1 policy")).toBeVisible();
  await page.getByRole("button", { name: "Rename" }).click();
  await expect(page.getByText("new")).toBeVisible();
  await expect(page.getByText("old")).not.toBeVisible();
});
```

- [ ] **Step 4: Run + commit**

Run: `./test.sh client e2e e2e/files-rename.spec.ts`
Expected: PASS.

```bash
git add api/src/routers/files.py client/src/pages/files/FileBrowser.tsx client/e2e/files-rename.spec.ts
git commit -m "feat(file-policies): folder rename as a UI primitive

UI dialog surfaces the secondary effects ('this will move 47 files and
update 3 policies') before the rename. Backend updates policies first
(one tx) then S3-copies + sidecar-updates + deletes — worst-case
interleaving is 'new prefix briefly empty', never 'access mismatched'."
```

---

## Final verification

### Task 25: Pre-completion sweep

- [ ] **Step 1: Pyright + ruff (backend)**

Run: `cd api && pyright && ruff check .`
Expected: 0 errors.

- [ ] **Step 2: Frontend type-check + lint + types regen**

Run: `cd client && npm run generate:types && npm run tsc && npm run lint`
Expected: 0 errors. No type drift.

- [ ] **Step 3: Backend full test suite**

Run: `./test.sh all`
Expected: PASS.

- [ ] **Step 4: Frontend unit suite**

Run: `./test.sh client unit`
Expected: PASS.

- [ ] **Step 5: Frontend e2e suite**

Run: `./test.sh client e2e`
Expected: PASS.

- [ ] **Step 6: DTO parity test**

Run: `./test.sh tests/unit/test_dto_flags.py -v`
Expected: PASS.

- [ ] **Step 7: Manifest round-trip e2e**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py -v`
Expected: PASS.

- [ ] **Step 8: Open PR**

```bash
git push -u origin 170-file-policies
gh pr create --title "feat(files): file policies (#170)" --body "$(cat <<'EOF'
## Summary

- `FilePolicy` ORM keyed by (org_id, location, path) with longest-prefix-wins resolution
- `file_index` sidecar extension (created_by, created_at, scope decomposition) populated by every write path
- `/api/files/*` data-plane endpoints relaxed from CurrentSuperuser to Context with policy enforcement
- Batch signed-URL endpoint (`/api/files/signed-urls`) — gallery unblocker
- Web SDK at `client/src/lib/app-sdk/files.ts` with `useFiles` hook
- Admin UI: file browser, rule editor (Monaco), effective-access tester, folder rename primitive
- CLI: `bifrost files policies set/get/list/delete`
- Manifest round-trip via `ManifestFilePolicy`
- Websocket subscriptions on `files:{location}:{prefix}` with per-recipient Creator filtering

Plugs into the domain-agnostic engine from #<PR-A-number>. Action vocab is `read/write/delete/list`. Pre-sidecar files are admin-only by design (no retroactive backfill of created_by).

Spec: `docs/superpowers/specs/2026-05-01-file-policies-design.md`
Plan: `docs/superpowers/plans/2026-05-19-file-policies.md`

Closes #170.

## Test plan

- [ ] `./test.sh all` green
- [ ] `./test.sh client unit` green
- [ ] `./test.sh client e2e` green
- [ ] DTO parity + manifest round-trip green
- [ ] Manual: file browser loads, tree renders, scope-hide toggle works
- [ ] Manual: rule editor renders, templates load, validate endpoint surfaces errors
- [ ] Manual: effective-access tester shows resolution trail
- [ ] Manual: subscribe in two tabs as different users, write a file, assert per-recipient Creator filter
- [ ] Manual: rename a folder with files + a policy, assert pre-flight count + post-rename consistency

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Parallelism playbook for agent teams

| Phase | Tasks | Concurrency |
|---|---|---|
| 1 — Foundation | 1, 2, 3 | Serial. One agent. |
| 2 — Storage | 4, 5, 6 | Serial. One agent. |
| 3 — Sidecar | 7 | Serial. One agent (touches many write paths). |
| 4 — REST + CLI + SDK | 8, 9, 10, 11, 12, 13, 14, 15, 16, 17 | **3 agents in parallel** after Task 7: backend agent on 8→11; backend agent on 12→14; frontend agent on 15→17. |
| 5 — Admin UI | 18, 19, 20, 21 | Serial within the frontend lane. One agent. |
| 6 — Subscriptions | 22, 23, 24 | 22 first (backend), then 23 and 24 in parallel. |
| 7 — Verification | 25 | One agent, serial. |

**Coordination notes:**
- Task 8 and Task 12 both edit `api/src/routers/files.py`. They touch different sections (8 = data-plane endpoints; 12 = CRUD endpoints) but agents must rebase carefully or run sequentially.
- Task 15 (SDK) does not need backend Task 11 or 12 to be complete — it only needs Task 8 (the relaxed data-plane endpoints) and the generated types.
- The Files SDK e2e (Task 17) needs Task 15 + Task 8 (no admin UI required).
