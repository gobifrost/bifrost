# Reusable Named Policy Rules — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let file and table policies reference reusable named rules by `{"$ref": name}` so a rule (e.g. `admin_bypass`) is defined once and applied across many policies.

**Architecture:** A shared org-scoped `PolicyRule` entity (cascade org→global via `OrgScopedRepository`) holds a single `{actions, when}` body. Both policy documents' rule lists become a `Rule | PolicyRuleRef` union; a `resolve_policy_refs` pre-pass inlines refs **before** the existing pure evaluator / SQL compiler runs (called right next to the existing `preresolve_for_policies`). Integrity via hard-fail-on-missing, server-side rename cascade, and delete-while-referenced guard.

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy (async) / Pydantic v2 / Alembic / PostgreSQL JSONB; Click CLI; React/TypeScript client.

**Spec:** `docs/superpowers/specs/2026-06-22-named-policy-rules-design.md`

## Global Constraints

- **Worktree only.** All work in `/home/jack/GitHub/bifrost/.claude/worktrees/files-sdk-policies` (branch `codex/files-sdk-policies`). Never touch the primary checkout.
- **Org scoping is canonical.** `PolicyRule` resolves through `OrgScopedRepository` — never hand-roll `WHERE organization_id == x OR IS NULL` (lint test catches it). Read `api/src/repositories/README.md` first.
- **Resolution before evaluation.** Refs MUST be inlined before `evaluate_file_action` / `compile_read_filter`. The pure evaluator and SQL compiler stay unchanged.
- **Hard-fail on missing ref** — raise, consistent with the evaluator's `unknown operator`. Never silently drop.
- **Three parallel surfaces** for entity mutations: REST + CLI + MCP, fed from one `PolicyRuleCreate`/`PolicyRuleUpdate` DTO pair. MCP tools are thin HTTP bridges (no ORM) — `test_mcp_thin_wrapper.py` enforces.
- **No dead code / no unrequested fallbacks.**
- **Tests use `./test.sh`** (Dockerized). Backend logic → `api/tests/unit/`; endpoints → `api/tests/e2e/`. JUnit at `/tmp/bifrost-<project>/test-results.xml`.
- **`admin_bypass` built-in is read-only; do NOT migrate existing inline rows.**
- **All rule writes require admin** (the bypass check `is_platform_admin OR is_provider_org`); a global rule (`organization_id=NULL`) is a bypass-gated write.

---

## File Structure

| File | Responsibility | New/Mod |
|------|----------------|---------|
| `api/src/models/orm/policy_rule.py` | `PolicyRule` ORM | New |
| `api/alembic/versions/<rev>_policy_rule.py` | table + GIN indexes on JSONB policy columns | New |
| `api/src/models/contracts/policy_rule.py` | `PolicyRuleCreate/Update/Public` | New |
| `api/src/models/contracts/policies.py` | add `PolicyRuleRef`; widen both policy unions | Mod |
| `api/src/repositories/policy_rule.py` | `PolicyRuleRepository(OrgScopedRepository)` | New |
| `api/shared/policy_rules.py` | `resolve_policy_refs`, exceptions, where-used query | New |
| `api/shared/file_policies_seed.py` | seed as `{"$ref":"admin_bypass"}` | Mod |
| `api/src/services/policy_rule_service.py` | CRUD + rename-cascade + delete-guard + seed built-in + audit | New |
| `api/src/services/file_policy_service.py` | call `resolve_policy_refs` in `is_allowed` | Mod |
| `api/src/routers/tables.py` | call `resolve_policy_refs` before compile/eval | Mod |
| `api/src/routers/policy_rules.py` | REST CRUD + `/usages` | New |
| `api/src/routers/files.py` / table policy save | ref save-validation | Mod |
| `api/bifrost/commands/policy_rules.py` | `bifrost policy-rule` group | New |
| `api/bifrost/commands/tables.py` | `tables policies {get,set}` subgroup | Mod |
| `api/bifrost/commands/__init__.py` | register groups | Mod |
| `api/src/services/mcp_server/tools/policy_rules.py` | thin MCP wrapper | New |
| `api/bifrost/manifest.py` | `ManifestPolicyRule` | Mod |
| `api/src/services/manifest_generator.py` | serialize rules | Mod |
| `api/src/services/manifest_import.py` | `_resolve_policy_rule` (before policies) | Mod |
| `client/src/services/policyRules.ts` | API wrapper | New |
| `client/src/components/{files,tables}/` policy editors | reference mode + rules manager | Mod |

---

## Task 1: `PolicyRule` ORM model + migration

**Files:**
- Create: `api/src/models/orm/policy_rule.py`
- Create: `api/alembic/versions/<rev>_policy_rule.py`
- Test: `api/tests/unit/test_policy_rule_model.py`

**Interfaces:**
- Produces: `PolicyRule` ORM with columns `id, organization_id|NULL, name, description|NULL, body:JSONB({actions,when}), is_builtin:bool, created_by|NULL, created_at, updated_at`; `UNIQUE(organization_id, name)`.

- [ ] **Step 1: Write the failing test**

```python
# api/tests/unit/test_policy_rule_model.py
from uuid import uuid4
from src.models.orm.policy_rule import PolicyRule

def test_policy_rule_columns_and_defaults():
    r = PolicyRule(name="ops_write", body={"actions": ["write"], "when": {"user": "is_platform_admin"}})
    assert r.organization_id is None          # global by default
    assert r.is_builtin is False
    # columns exist
    for col in ("id", "name", "description", "body", "is_builtin", "created_by", "created_at", "updated_at"):
        assert hasattr(r, col)

def test_unique_org_name_constraint_declared():
    cols = {c.name for c in PolicyRule.__table__.indexes for c in c.columns} if PolicyRule.__table__.indexes else set()
    uniques = [c for c in PolicyRule.__table__.constraints if c.__class__.__name__ == "UniqueConstraint"]
    names = {tuple(col.name for col in u.columns) for u in uniques} | {tuple(i.columns.keys()) for i in PolicyRule.__table__.indexes if i.unique}
    assert ("organization_id", "name") in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/test_policy_rule_model.py -v`
Expected: FAIL with `ModuleNotFoundError: src.models.orm.policy_rule`.

- [ ] **Step 3: Write the ORM model**

```python
# api/src/models/orm/policy_rule.py
"""Reusable named policy rule, referenced by {"$ref": name} from file/table policies."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Boolean, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class PolicyRule(Base):
    """A named, reusable policy rule body ({actions, when}). Cascade org→global."""

    __tablename__ = "policy_rules"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id"), default=None
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    body: Mapped[dict] = mapped_column(JSONB, nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_policy_rules_org_name", "organization_id", "name", unique=True),
    )
```

Then register the model where ORM models are imported (find the orm `__init__.py` or model registry and add `from src.models.orm.policy_rule import PolicyRule`).

- [ ] **Step 4: Run model test to verify it passes**

Run: `./test.sh tests/unit/test_policy_rule_model.py -v`
Expected: PASS.

- [ ] **Step 5: Create the migration**

```bash
cd api && alembic revision -m "policy_rules table + GIN on policy JSONB"
```

Edit the new file's `upgrade()`/`downgrade()`:

```python
def upgrade() -> None:
    op.create_table(
        "policy_rules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_policy_rules_org_name", "policy_rules", ["organization_id", "name"], unique=True)
    # GIN for the where-used @> containment scans (rename/delete/blast-radius)
    op.create_index("ix_file_policies_policies_gin", "file_policies", ["policies"], postgresql_using="gin")
    op.create_index("ix_tables_access_gin", "tables", ["access"], postgresql_using="gin")

def downgrade() -> None:
    op.drop_index("ix_tables_access_gin", table_name="tables")
    op.drop_index("ix_file_policies_policies_gin", table_name="file_policies")
    op.drop_index("ix_policy_rules_org_name", table_name="policy_rules")
    op.drop_table("policy_rules")
```

Add `from alembic import op` / `import sqlalchemy as sa` / `from sqlalchemy.dialects import postgresql` imports if the template lacks them.

- [ ] **Step 6: Apply migration to the test stack and verify**

Run: `./test.sh stack reset && ./test.sh tests/unit/test_policy_rule_model.py -v`
(The test stack's init applies alembic on reset.) Expected: PASS, no migration error.

- [ ] **Step 7: Commit**

```bash
git add api/src/models/orm/policy_rule.py api/alembic/versions/*policy_rule*.py api/tests/unit/test_policy_rule_model.py
git commit -m "feat(policy-rules): PolicyRule ORM + migration + GIN on policy JSONB"
```

---

## Task 2: Contracts — `PolicyRuleRef` + widened policy unions + DTOs

**Files:**
- Modify: `api/src/models/contracts/policies.py` (add `PolicyRuleRef`, widen `FilePolicies.policies` and `TablePolicies.policies`)
- Create: `api/src/models/contracts/policy_rule.py` (`PolicyRuleCreate/Update/Public`)
- Test: `api/tests/unit/test_policy_rule_contracts.py`

**Interfaces:**
- Produces: `PolicyRuleRef(ref: str)` (alias `$ref`); `FilePolicies.policies: list[FilePolicyRule | PolicyRuleRef]`; `TablePolicies.policies: list[Policy | PolicyRuleRef]`; `PolicyRuleCreate(name, description, body)`, `PolicyRuleUpdate(name?, description?, body?)`, `PolicyRulePublic(...)`.

- [ ] **Step 1: Write the failing test**

```python
# api/tests/unit/test_policy_rule_contracts.py
from src.models.contracts.policies import FilePolicies, TablePolicies, PolicyRuleRef
from src.models.contracts.policy_rule import PolicyRuleCreate

def test_ref_parses_via_dollar_alias():
    ref = PolicyRuleRef.model_validate({"$ref": "admin_bypass"})
    assert ref.ref == "admin_bypass"
    assert ref.model_dump(by_alias=True) == {"$ref": "admin_bypass"}

def test_file_policies_accepts_mixed_inline_and_ref():
    doc = FilePolicies.model_validate({"policies": [
        {"$ref": "admin_bypass"},
        {"name": "r", "actions": ["read"], "when": None},
    ]})
    assert isinstance(doc.policies[0], PolicyRuleRef)
    assert doc.policies[1].name == "r"

def test_table_policies_accepts_ref():
    doc = TablePolicies.model_validate({"policies": [{"$ref": "shared"}]})
    assert isinstance(doc.policies[0], PolicyRuleRef)

def test_policy_rule_create_body_shape():
    c = PolicyRuleCreate(name="ops", body={"actions": ["write"], "when": {"user": "is_platform_admin"}})
    assert c.body["actions"] == ["write"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `./test.sh tests/unit/test_policy_rule_contracts.py -v`
Expected: FAIL (`ImportError: PolicyRuleRef`).

- [ ] **Step 3: Add `PolicyRuleRef` and widen the unions in `policies.py`**

In `api/src/models/contracts/policies.py`, add near `FilePolicies`/`TablePolicies`:

```python
from pydantic import ConfigDict

class PolicyRuleRef(BaseModel):
    """A reference to a named PolicyRule, spliced inline at resolution time."""
    ref: str = Field(alias="$ref", min_length=1, max_length=100)
    model_config = ConfigDict(populate_by_name=True)
```

Change the two list types (keep `PolicyRuleRef` **last** so Pydantic tries the structurally-richer inline rule first):

```python
class FilePolicies(BaseModel):
    policies: list[FilePolicyRule | PolicyRuleRef] = Field(default_factory=list)

class TablePolicies(BaseModel):
    policies: list[Policy | PolicyRuleRef] = Field(default_factory=list)
```

- [ ] **Step 4: Create `policy_rule.py` DTOs**

```python
# api/src/models/contracts/policy_rule.py
from __future__ import annotations
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_serializer

class PolicyRuleBody(BaseModel):
    """The portable rule body — actions + when AST. Domain-validated at resolve time."""
    actions: list[str] = Field(min_length=1)
    when: dict | None = None

class PolicyRuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None
    body: dict
    organization_id: UUID | None = None

class PolicyRuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    body: dict | None = None

class PolicyRulePublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    organization_id: UUID | None
    name: str
    description: str | None
    body: dict
    is_builtin: bool
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def _dt(self, dt: datetime | None) -> str | None:
        return dt.isoformat() if dt else None
```

- [ ] **Step 5: Run to verify it passes**

Run: `./test.sh tests/unit/test_policy_rule_contracts.py -v`
Expected: PASS.

- [ ] **Step 6: Regenerate client types + commit**

Run (dev stack must be up): `cd client && npm run generate:types`

```bash
git add api/src/models/contracts/policies.py api/src/models/contracts/policy_rule.py api/tests/unit/test_policy_rule_contracts.py client/src/lib/v1.d.ts
git commit -m "feat(policy-rules): PolicyRuleRef union member + Create/Update/Public DTOs"
```

---

## Task 3: `PolicyRuleRepository` (cascade) + where-used query

**Files:**
- Create: `api/src/repositories/policy_rule.py`
- Create part of: `api/shared/policy_rules.py` (the where-used query only)
- Test: `api/tests/e2e/test_policy_rule_repo.py`

**Interfaces:**
- Consumes: `PolicyRule` (Task 1), `OrgScopedRepository`.
- Produces: `PolicyRuleRepository(session, org_id, user_id=None, is_superuser=False)` with inherited `get(name=...)` (cascade) and `list()`; `find_policy_rule_usages(db, name, *, org_id) -> PolicyRuleUsages` (counts + targets across `file_policies` and `tables`).

- [ ] **Step 1: Write the failing e2e test**

```python
# api/tests/e2e/test_policy_rule_repo.py
import pytest
from uuid import uuid4
from src.models.orm.policy_rule import PolicyRule
from src.repositories.policy_rule import PolicyRuleRepository

@pytest.mark.asyncio
async def test_get_name_cascades_org_over_global(db_session, seed_org):
    db_session.add(PolicyRule(name="r", body={"actions": ["read"], "when": None}))  # global
    db_session.add(PolicyRule(name="r", organization_id=seed_org, body={"actions": ["write"], "when": None}))
    await db_session.flush()
    repo = PolicyRuleRepository(db_session, org_id=seed_org, is_superuser=True)
    got = await repo.get(name="r")
    assert got.body["actions"] == ["write"]   # org overrides global

@pytest.mark.asyncio
async def test_get_name_falls_back_to_global(db_session, seed_org):
    db_session.add(PolicyRule(name="g", body={"actions": ["read"], "when": None}))
    await db_session.flush()
    repo = PolicyRuleRepository(db_session, org_id=seed_org, is_superuser=True)
    assert (await repo.get(name="g")).body["actions"] == ["read"]
```

(Use the project's existing e2e fixtures for `db_session`/`seed_org`; mirror an existing repo e2e test's fixtures — grep `tests/e2e` for `OrgScopedRepository` usage.)

- [ ] **Step 2: Run to verify it fails**

Run: `./test.sh e2e tests/e2e/test_policy_rule_repo.py -v`
Expected: FAIL (`ImportError: PolicyRuleRepository`).

- [ ] **Step 3: Write the repository**

```python
# api/src/repositories/policy_rule.py
from src.models.orm.policy_rule import PolicyRule
from src.repositories.org_scoped import OrgScopedRepository

class PolicyRuleRepository(OrgScopedRepository[PolicyRule]):
    """Cascade org→global resolution for named policy rules."""
    model = PolicyRule
    role_table = None
```

- [ ] **Step 4: Add the where-used query to `api/shared/policy_rules.py`**

```python
# api/shared/policy_rules.py  (where-used portion; resolver added in Task 4)
from __future__ import annotations
from dataclasses import dataclass, field
from uuid import UUID
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from src.models.orm.file_metadata import FilePolicy
from src.models.orm.tables import Table

@dataclass
class PolicyRuleUsages:
    file_policies: list[dict] = field(default_factory=list)   # {id, location, path}
    tables: list[dict] = field(default_factory=list)          # {id, name}
    @property
    def total(self) -> int:
        return len(self.file_policies) + len(self.tables)

async def find_policy_rule_usages(
    db: AsyncSession, name: str, *, org_id: UUID | None
) -> PolicyRuleUsages:
    """Find every file/table policy whose `policies` list contains {"$ref": name}.

    org_id=None (a global rule) scans all orgs; an org-scoped rule scans that org.
    Uses the JSONB @> containment operator (GIN-indexed).
    """
    ref_json = [{"$ref": name}]
    fp = select(FilePolicy.id, FilePolicy.location, FilePolicy.path).where(
        FilePolicy.policies["policies"].contains(ref_json)
    )
    tb = select(Table.id, Table.name).where(Table.access["policies"].contains(ref_json))
    if org_id is not None:
        fp = fp.where(FilePolicy.organization_id == org_id)
        tb = tb.where(Table.organization_id == org_id)
    usages = PolicyRuleUsages()
    for row in (await db.execute(fp)).all():
        usages.file_policies.append({"id": str(row.id), "location": row.location, "path": row.path})
    for row in (await db.execute(tb)).all():
        usages.tables.append({"id": str(row.id), "name": row.name})
    return usages
```

- [ ] **Step 5: Run to verify repo test passes**

Run: `./test.sh e2e tests/e2e/test_policy_rule_repo.py -v`
Expected: PASS.

- [ ] **Step 6: Add a where-used e2e test + run**

```python
# append to api/tests/e2e/test_policy_rule_repo.py
@pytest.mark.asyncio
async def test_find_usages_scans_file_policies(db_session, seed_org):
    from src.models.orm.file_metadata import FilePolicy
    from api.shared.policy_rules import find_policy_rule_usages  # adjust import path
    db_session.add(FilePolicy(organization_id=seed_org, location="shared", path="x/",
                              policies={"policies": [{"$ref": "ops"}]}))
    await db_session.flush()
    u = await find_policy_rule_usages(db_session, "ops", org_id=seed_org)
    assert u.total == 1 and u.file_policies[0]["location"] == "shared"
```

Run: `./test.sh e2e tests/e2e/test_policy_rule_repo.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add api/src/repositories/policy_rule.py api/shared/policy_rules.py api/tests/e2e/test_policy_rule_repo.py
git commit -m "feat(policy-rules): cascade repository + JSONB where-used query"
```

---

## Task 4: `resolve_policy_refs` pre-pass + exceptions

**Files:**
- Modify: `api/shared/policy_rules.py` (add resolver + exceptions)
- Test: `api/tests/e2e/test_resolve_policy_refs.py`

**Interfaces:**
- Consumes: `PolicyRuleRepository`, `PolicyRuleRef`, `FilePolicies`/`TablePolicies`.
- Produces: `async resolve_policy_refs(policies, *, repo, action_domain) -> None` (mutates `policies.policies` in place, replacing each `PolicyRuleRef` with the resolved inline rule of the domain's type); raises `PolicyRuleNotFound`, `PolicyRuleDomainMismatch`.
- Domain action sets: file = `{read, write, delete, list}`; table = `{read, create, update, delete}`.

- [ ] **Step 1: Write the failing test**

```python
# api/tests/e2e/test_resolve_policy_refs.py
import pytest
from src.models.orm.policy_rule import PolicyRule
from src.models.contracts.policies import FilePolicies, FilePolicyRule, PolicyRuleRef
from src.repositories.policy_rule import PolicyRuleRepository
from api.shared.policy_rules import resolve_policy_refs, PolicyRuleNotFound, PolicyRuleDomainMismatch

@pytest.mark.asyncio
async def test_resolves_ref_to_inline_rule(db_session, seed_org):
    db_session.add(PolicyRule(name="ab", organization_id=seed_org,
                              body={"actions": ["read", "write", "delete", "list"], "when": {"user": "is_platform_admin"}}))
    await db_session.flush()
    repo = PolicyRuleRepository(db_session, org_id=seed_org, is_superuser=True)
    doc = FilePolicies.model_validate({"policies": [{"$ref": "ab"}, {"name": "x", "actions": ["read"], "when": None}]})
    await resolve_policy_refs(doc, repo=repo, action_domain="file")
    assert all(isinstance(p, FilePolicyRule) for p in doc.policies)   # no refs left
    assert doc.policies[0].name == "ab" and "write" in doc.policies[0].actions
    assert doc.policies[1].name == "x"                                # order preserved

@pytest.mark.asyncio
async def test_missing_ref_raises(db_session, seed_org):
    repo = PolicyRuleRepository(db_session, org_id=seed_org, is_superuser=True)
    doc = FilePolicies.model_validate({"policies": [{"$ref": "nope"}]})
    with pytest.raises(PolicyRuleNotFound):
        await resolve_policy_refs(doc, repo=repo, action_domain="file")

@pytest.mark.asyncio
async def test_cross_domain_ref_raises(db_session, seed_org):
    db_session.add(PolicyRule(name="filey", organization_id=seed_org,
                              body={"actions": ["list"], "when": None}))   # 'list' invalid for table
    await db_session.flush()
    from src.models.contracts.policies import TablePolicies
    repo = PolicyRuleRepository(db_session, org_id=seed_org, is_superuser=True)
    doc = TablePolicies.model_validate({"policies": [{"$ref": "filey"}]})
    with pytest.raises(PolicyRuleDomainMismatch):
        await resolve_policy_refs(doc, repo=repo, action_domain="table")
```

- [ ] **Step 2: Run to verify it fails**

Run: `./test.sh e2e tests/e2e/test_resolve_policy_refs.py -v`
Expected: FAIL (`ImportError: resolve_policy_refs`).

- [ ] **Step 3: Implement the resolver**

Append to `api/shared/policy_rules.py`:

```python
from typing import Literal
from src.models.contracts.policies import (
    FilePolicies, FilePolicyRule, Policy, PolicyRuleRef, TablePolicies,
)

class PolicyRuleNotFound(Exception):
    """A {"$ref"} pointed at a rule that does not resolve in (org → global)."""

class PolicyRuleDomainMismatch(Exception):
    """A referenced rule's actions are not valid for the referencing domain."""

_DOMAIN_ACTIONS = {
    "file": {"read", "write", "delete", "list"},
    "table": {"read", "create", "update", "delete"},
}

async def resolve_policy_refs(
    policies: FilePolicies | TablePolicies,
    *,
    repo,                                   # PolicyRuleRepository
    action_domain: Literal["file", "table"],
) -> None:
    """Replace each PolicyRuleRef in `policies.policies` with the resolved inline rule.

    Mutates in place. Raises PolicyRuleNotFound / PolicyRuleDomainMismatch.
    Runs BEFORE evaluation/compilation so the evaluator only ever sees inline rules.
    """
    valid = _DOMAIN_ACTIONS[action_domain]
    rule_cls = FilePolicyRule if action_domain == "file" else Policy
    resolved: list = []
    for entry in policies.policies:
        if not isinstance(entry, PolicyRuleRef):
            resolved.append(entry)
            continue
        row = await repo.get(name=entry.ref)
        if row is None:
            raise PolicyRuleNotFound(entry.ref)
        body = row.body or {}
        actions = body.get("actions", [])
        if not set(actions) <= valid:
            raise PolicyRuleDomainMismatch(
                f"rule {entry.ref!r} actions {actions} invalid for {action_domain}"
            )
        try:
            resolved.append(rule_cls.model_validate({
                "name": row.name,
                "description": row.description,
                "actions": actions,
                "when": body.get("when"),
            }))
        except Exception as exc:
            # The target domain's `when` validator rejects a foreign namespace
            # (e.g. a {file:...} expr validated into a table Policy whose Expr
            # forbids the file namespace). Normalize to the domain-mismatch error.
            raise PolicyRuleDomainMismatch(
                f"rule {entry.ref!r} body invalid for {action_domain}: {exc}"
            ) from exc
    policies.policies = resolved
```

- [ ] **Step 4: Run to verify it passes**

Run: `./test.sh e2e tests/e2e/test_resolve_policy_refs.py -v`
Expected: PASS (all three).

- [ ] **Step 5: Commit**

```bash
git add api/shared/policy_rules.py api/tests/e2e/test_resolve_policy_refs.py
git commit -m "feat(policy-rules): resolve_policy_refs pre-pass (hard-fail, domain-validated)"
```

---

## Task 5: Wire resolver into file + table evaluation paths

**Files:**
- Modify: `api/src/services/file_policy_service.py` (`is_allowed`, after `model_validate`)
- Modify: `api/src/routers/tables.py` (before `compile_read_filter` / eval, next to `preresolve_for_policies`)
- Test: `api/tests/e2e/test_policy_ref_enforced.py`

**Interfaces:**
- Consumes: `resolve_policy_refs`, `PolicyRuleRepository`.

- [ ] **Step 1: Write the failing e2e test (file path enforcement via ref)**

```python
# api/tests/e2e/test_policy_ref_enforced.py
import pytest
from src.models.orm.policy_rule import PolicyRule
from src.models.orm.file_metadata import FilePolicy
from src.services.file_policy_service import FilePolicyService

@pytest.mark.asyncio
async def test_file_read_allowed_via_referenced_rule(db_session, seed_org, admin_user):
    db_session.add(PolicyRule(name="ab", organization_id=seed_org,
        body={"actions": ["read", "write", "delete", "list"], "when": {"user": "is_platform_admin"}}))
    db_session.add(FilePolicy(organization_id=seed_org, location="shared", path="docs/",
        policies={"policies": [{"$ref": "ab"}]}))
    await db_session.flush()
    svc = FilePolicyService(db_session)
    allowed = await svc.is_allowed("read", organization_id=seed_org,
                                   location="shared", path="docs/file.txt", user=admin_user)
    assert allowed is True
```

(Reuse existing file-policy e2e fixtures for `admin_user`; grep `tests/e2e` for `FilePolicyService` usage to copy the setup.)

- [ ] **Step 2: Run to verify it fails**

Run: `./test.sh e2e tests/e2e/test_policy_ref_enforced.py -v`
Expected: FAIL — the `{"$ref":"ab"}` entry is a `PolicyRuleRef`, not yet resolved, so `evaluate_file_action` sees no matching inline rule → `False`.

- [ ] **Step 3: Wire into `file_policy_service.is_allowed`**

In `api/src/services/file_policy_service.py`, immediately after the `FilePolicies.model_validate(...)` try/except block and before `preresolve_for_policies(...)`, add:

```python
        from api.shared.policy_rules import resolve_policy_refs, PolicyRuleNotFound, PolicyRuleDomainMismatch
        from src.repositories.policy_rule import PolicyRuleRepository
        rule_repo = PolicyRuleRepository(self.db, org_id=organization_id, is_superuser=True)
        try:
            await resolve_policy_refs(policies, repo=rule_repo, action_domain="file")
        except (PolicyRuleNotFound, PolicyRuleDomainMismatch) as exc:
            logger.warning("unresolvable policy ref for %s/%s; denying: %s",
                           organization_id, location, exc)
            return False
```

> Note: at the **enforcement** call site a missing ref denies (fail-closed *for that request*) and logs — the loud hard-fail surfaces at **save** time (Task 7) where the user can act. This matches the existing `malformed policies → deny` handling two lines up.

- [ ] **Step 4: Wire into the table list/eval path**

In `api/src/routers/tables.py`, right after the existing `await preresolve_for_policies(...)` call (and before `compile_read_filter` / per-row eval), add the same resolver call with `action_domain="table"`:

```python
    from api.shared.policy_rules import resolve_policy_refs, PolicyRuleNotFound, PolicyRuleDomainMismatch
    from src.repositories.policy_rule import PolicyRuleRepository
    if policies is not None:
        rule_repo = PolicyRuleRepository(ctx.db, org_id=table.organization_id, is_superuser=True)
        try:
            await resolve_policy_refs(policies, repo=rule_repo, action_domain="table")
        except (PolicyRuleNotFound, PolicyRuleDomainMismatch):
            policies.policies = []   # unresolvable → default-deny for this request
```

Apply the same two lines at any **other** table-policy eval site (grep `tables.py` for every `preresolve_for_policies` call and add the resolver after each).

- [ ] **Step 5: Run to verify it passes**

Run: `./test.sh e2e tests/e2e/test_policy_ref_enforced.py -v`
Expected: PASS.

- [ ] **Step 6: Run the existing policy suites to confirm no regression**

Run: `./test.sh e2e tests/e2e -k "policy or table or file" -v`
Expected: PASS (existing inline-rule policies untouched).

- [ ] **Step 7: Commit**

```bash
git add api/src/services/file_policy_service.py api/src/routers/tables.py api/tests/e2e/test_policy_ref_enforced.py
git commit -m "feat(policy-rules): resolve refs before file + table policy evaluation"
```

---

## Task 6: `PolicyRuleService` — CRUD + rename cascade + delete guard + built-in seed + audit

**Files:**
- Create: `api/src/services/policy_rule_service.py`
- Modify: `api/shared/file_policies_seed.py` (seed as ref)
- Test: `api/tests/e2e/test_policy_rule_service.py`

**Interfaces:**
- Consumes: `PolicyRuleRepository`, `find_policy_rule_usages`, `emit_audit`, `PolicyRuleCreate/Update`.
- Produces: `PolicyRuleService(db)` with `create`, `update` (renames cascade), `delete` (guarded), `seed_builtin_admin_bypass()`, `usages(name, org_id)`.
- Built-in: a global `PolicyRule(name="admin_bypass", is_builtin=True, body={actions:[read,write,delete,list], when:{user:is_platform_admin}})`.

- [ ] **Step 1: Write the failing tests**

```python
# api/tests/e2e/test_policy_rule_service.py
import pytest
from sqlalchemy import select
from src.models.orm.policy_rule import PolicyRule
from src.models.orm.file_metadata import FilePolicy
from src.models.contracts.policy_rule import PolicyRuleCreate, PolicyRuleUpdate
from src.services.policy_rule_service import PolicyRuleService, PolicyRuleInUse, PolicyRuleReadOnly

@pytest.mark.asyncio
async def test_rename_cascades_to_referencing_file_policies(db_session, seed_org, admin_actor):
    svc = PolicyRuleService(db_session)
    await svc.create(PolicyRuleCreate(name="ops", organization_id=seed_org,
                     body={"actions": ["read"], "when": None}), actor=admin_actor)
    db_session.add(FilePolicy(organization_id=seed_org, location="shared", path="d/",
                              policies={"policies": [{"$ref": "ops"}]}))
    await db_session.flush()
    await svc.update("ops", PolicyRuleUpdate(name="operations"), org_id=seed_org, actor=admin_actor)
    fp = (await db_session.execute(select(FilePolicy))).scalar_one()
    assert fp.policies["policies"] == [{"$ref": "operations"}]   # ref rewritten

@pytest.mark.asyncio
async def test_delete_blocked_while_referenced(db_session, seed_org, admin_actor):
    svc = PolicyRuleService(db_session)
    await svc.create(PolicyRuleCreate(name="ops", organization_id=seed_org,
                     body={"actions": ["read"], "when": None}), actor=admin_actor)
    db_session.add(FilePolicy(organization_id=seed_org, location="shared", path="d/",
                              policies={"policies": [{"$ref": "ops"}]}))
    await db_session.flush()
    with pytest.raises(PolicyRuleInUse):
        await svc.delete("ops", org_id=seed_org, actor=admin_actor)

@pytest.mark.asyncio
async def test_builtin_admin_bypass_is_readonly(db_session, admin_actor):
    svc = PolicyRuleService(db_session)
    await svc.seed_builtin_admin_bypass()
    with pytest.raises(PolicyRuleReadOnly):
        await svc.update("admin_bypass", PolicyRuleUpdate(description="x"), org_id=None, actor=admin_actor)

@pytest.mark.asyncio
async def test_seed_is_idempotent(db_session):
    svc = PolicyRuleService(db_session)
    await svc.seed_builtin_admin_bypass()
    await svc.seed_builtin_admin_bypass()
    rows = (await db_session.execute(select(PolicyRule).where(PolicyRule.name == "admin_bypass"))).scalars().all()
    assert len(rows) == 1
```

(Use the project's e2e actor fixture for `admin_actor`; grep for `actor_override` / `ActorContext` in tests.)

- [ ] **Step 2: Run to verify it fails**

Run: `./test.sh e2e tests/e2e/test_policy_rule_service.py -v`
Expected: FAIL (`ImportError: PolicyRuleService`).

- [ ] **Step 3: Implement the service**

```python
# api/src/services/policy_rule_service.py
from __future__ import annotations
from typing import Any
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.models.orm.policy_rule import PolicyRule
from src.models.orm.file_metadata import FilePolicy
from src.models.orm.tables import Table
from src.models.contracts.policy_rule import PolicyRuleCreate, PolicyRuleUpdate
from src.repositories.policy_rule import PolicyRuleRepository
from api.shared.policy_rules import find_policy_rule_usages
from src.services.audit import emit_audit

class PolicyRuleInUse(Exception): ...
class PolicyRuleReadOnly(Exception): ...
class PolicyRuleNotFoundError(Exception): ...

_ADMIN_BYPASS = {
    "name": "admin_bypass",
    "description": "Platform admins bypass all checks. Built-in, read-only.",
    "body": {"actions": ["read", "write", "delete", "list"], "when": {"user": "is_platform_admin"}},
}

class PolicyRuleService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def seed_builtin_admin_bypass(self) -> None:
        existing = (await self.db.execute(
            select(PolicyRule).where(PolicyRule.name == "admin_bypass",
                                     PolicyRule.organization_id.is_(None))
        )).scalar_one_or_none()
        if existing is not None:
            return
        self.db.add(PolicyRule(organization_id=None, is_builtin=True, **_ADMIN_BYPASS))
        await self.db.flush()

    async def create(self, data: PolicyRuleCreate, *, actor: Any) -> PolicyRule:
        row = PolicyRule(organization_id=data.organization_id, name=data.name,
                         description=data.description, body=data.body)
        self.db.add(row)
        await self.db.flush()
        await emit_audit(self.db, "policy_rule.create", resource_type="policy_rule",
                         resource_id=row.id, details={"name": row.name}, actor_override=actor)
        return row

    async def _get(self, name: str, org_id: UUID | None) -> PolicyRule:
        repo = PolicyRuleRepository(self.db, org_id=org_id, is_superuser=True)
        row = await repo.get(name=name)
        if row is None:
            raise PolicyRuleNotFoundError(name)
        return row

    async def update(self, name: str, data: PolicyRuleUpdate, *, org_id: UUID | None, actor: Any) -> PolicyRule:
        row = await self._get(name, org_id)
        if row.is_builtin:
            raise PolicyRuleReadOnly(name)
        renamed_to = data.name if data.name and data.name != row.name else None
        usages = await find_policy_rule_usages(self.db, row.name, org_id=row.organization_id)
        if renamed_to:
            await self._cascade_rename(row.name, renamed_to, row.organization_id)
            row.name = renamed_to
        if data.description is not None:
            row.description = data.description
        if data.body is not None:
            row.body = data.body
        await self.db.flush()
        await emit_audit(self.db, "policy_rule.update", resource_type="policy_rule",
                         resource_id=row.id,
                         details={"name": row.name, "renamed_to": renamed_to, "usages": usages.total},
                         actor_override=actor)
        return row

    async def delete(self, name: str, *, org_id: UUID | None, actor: Any) -> None:
        row = await self._get(name, org_id)
        if row.is_builtin:
            raise PolicyRuleReadOnly(name)
        usages = await find_policy_rule_usages(self.db, row.name, org_id=row.organization_id)
        if usages.total > 0:
            raise PolicyRuleInUse(name)
        await self.db.delete(row)
        await self.db.flush()
        await emit_audit(self.db, "policy_rule.delete", resource_type="policy_rule",
                         resource_id=row.id, details={"name": row.name}, actor_override=actor)

    async def usages(self, name: str, *, org_id: UUID | None):
        row = await self._get(name, org_id)
        return await find_policy_rule_usages(self.db, row.name, org_id=row.organization_id)

    async def _cascade_rename(self, old: str, new: str, org_id: UUID | None) -> None:
        """Rewrite {"$ref": old} → {"$ref": new} in every referencing file/table policy."""
        ref_json = [{"$ref": old}]
        fp_q = select(FilePolicy).where(FilePolicy.policies["policies"].contains(ref_json))
        tb_q = select(Table).where(Table.access["policies"].contains(ref_json))
        if org_id is not None:
            fp_q = fp_q.where(FilePolicy.organization_id == org_id)
            tb_q = tb_q.where(Table.organization_id == org_id)
        for fp in (await self.db.execute(fp_q)).scalars().all():
            fp.policies = _rewrite_ref(fp.policies, old, new)
        for tb in (await self.db.execute(tb_q)).scalars().all():
            tb.access = _rewrite_ref(tb.access, old, new)
        await self.db.flush()

def _rewrite_ref(doc: dict, old: str, new: str) -> dict:
    rules = [{"$ref": new} if r.get("$ref") == old else r for r in doc.get("policies", [])]
    return {**doc, "policies": rules}
```

> **JSONB mutation note:** reassign `fp.policies = ...` / `tb.access = ...` to a NEW dict so SQLAlchemy marks the column dirty (in-place mutation of a JSONB dict is not tracked). `_rewrite_ref` returns a fresh dict for this reason.

- [ ] **Step 4: Update the file seed to reference the built-in**

In `api/shared/file_policies_seed.py`, change `make_seed_admin_bypass_file` to return a ref:

```python
def make_seed_admin_bypass_file() -> dict:
    """New file prefixes reference the built-in admin_bypass rule."""
    return {"policies": [{"$ref": "admin_bypass"}]}
```

> Existing inline rows are NOT migrated (spec decision). Ensure `seed_builtin_admin_bypass()` runs at app startup / first-policy-create so the ref resolves — wire a call into the existing startup seed path (grep for where other built-ins seed, e.g. a `seed_*` startup hook) OR call it lazily in `FilePolicyService.upsert_policy`'s create branch before inserting the seed.

- [ ] **Step 5: Run to verify it passes**

Run: `./test.sh e2e tests/e2e/test_policy_rule_service.py -v`
Expected: PASS (all four).

- [ ] **Step 6: Confirm the file-policy create flow still works end to end**

Run: `./test.sh e2e tests/e2e -k "file_polic" -v`
Expected: PASS — newly seeded prefixes resolve `{"$ref":"admin_bypass"}` against the seeded built-in.

- [ ] **Step 7: Commit**

```bash
git add api/src/services/policy_rule_service.py api/shared/file_policies_seed.py api/tests/e2e/test_policy_rule_service.py
git commit -m "feat(policy-rules): service (CRUD, rename cascade, delete guard, built-in seed, audit)"
```

---

## Task 7: REST router — CRUD + `/usages` + save-time ref validation

**Files:**
- Create: `api/src/routers/policy_rules.py`
- Modify: the file-policy save handler (`api/src/routers/files.py` policies `set`) and table-policy save handler to validate refs on write
- Register the router in the app (find `main.py` / router registry)
- Test: `api/tests/e2e/test_policy_rules_api.py`

**Interfaces:**
- Consumes: `PolicyRuleService`, `PolicyRulePublic`.
- Produces: `POST/GET/PUT/DELETE /api/policy-rules`, `GET /api/policy-rules/{name}/usages`. All admin-gated (`CurrentSuperuser`-equivalent).

- [ ] **Step 1: Write the failing API e2e test**

```python
# api/tests/e2e/test_policy_rules_api.py
import pytest

@pytest.mark.asyncio
async def test_crud_and_usages(admin_client):
    r = await admin_client.post("/api/policy-rules", json={
        "name": "ops", "body": {"actions": ["read"], "when": None}})
    assert r.status_code == 201
    assert (await admin_client.get("/api/policy-rules")).status_code == 200
    u = await admin_client.get("/api/policy-rules/ops/usages")
    assert u.status_code == 200 and u.json()["total"] == 0
    assert (await admin_client.delete("/api/policy-rules/ops")).status_code == 204

@pytest.mark.asyncio
async def test_saving_file_policy_with_missing_ref_is_422(admin_client):
    r = await admin_client.put("/api/files/policies/docs%2F",
        params={"location": "shared"},
        json={"policies": [{"$ref": "does_not_exist"}]})
    assert r.status_code == 422

@pytest.mark.asyncio
async def test_non_admin_cannot_create_rule(user_client):
    r = await user_client.post("/api/policy-rules", json={
        "name": "x", "body": {"actions": ["read"], "when": None}})
    assert r.status_code in (401, 403)
```

(Use existing `admin_client` / `user_client` e2e fixtures; grep `tests/e2e` for them.)

- [ ] **Step 2: Run to verify it fails**

Run: `./test.sh e2e tests/e2e/test_policy_rules_api.py -v`
Expected: FAIL (404 — router not mounted).

- [ ] **Step 3: Write the router (thin)**

```python
# api/src/routers/policy_rules.py
from uuid import UUID
from fastapi import APIRouter, HTTPException, status
from src.models.contracts.policy_rule import PolicyRuleCreate, PolicyRulePublic, PolicyRuleUpdate
from src.services.policy_rule_service import (
    PolicyRuleService, PolicyRuleInUse, PolicyRuleReadOnly, PolicyRuleNotFoundError,
)
# Context / CurrentSuperuser deps: import the same ones config.py uses.
from src.routers.config import Context, CurrentSuperuser  # or wherever these live

router = APIRouter()

@router.get("/api/policy-rules", response_model=list[PolicyRulePublic])
async def list_rules(ctx: Context, user: CurrentSuperuser):
    from src.repositories.policy_rule import PolicyRuleRepository
    repo = PolicyRuleRepository(ctx.db, org_id=ctx.org_id, is_superuser=True)
    return await repo.list()

@router.post("/api/policy-rules", response_model=PolicyRulePublic, status_code=status.HTTP_201_CREATED)
async def create_rule(data: PolicyRuleCreate, ctx: Context, user: CurrentSuperuser):
    return await PolicyRuleService(ctx.db).create(data, actor=user)

@router.put("/api/policy-rules/{name}", response_model=PolicyRulePublic)
async def update_rule(name: str, data: PolicyRuleUpdate, ctx: Context, user: CurrentSuperuser):
    try:
        return await PolicyRuleService(ctx.db).update(name, data, org_id=ctx.org_id, actor=user)
    except PolicyRuleReadOnly:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{name} is a read-only built-in rule")
    except PolicyRuleNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, name)

@router.delete("/api/policy-rules/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(name: str, ctx: Context, user: CurrentSuperuser):
    try:
        await PolicyRuleService(ctx.db).delete(name, org_id=ctx.org_id, actor=user)
    except PolicyRuleInUse:
        svc = PolicyRuleService(ctx.db)
        u = await svc.usages(name, org_id=ctx.org_id)
        raise HTTPException(status.HTTP_409_CONFLICT,
                            {"message": f"{name} is referenced", "usages": u.__dict__})
    except (PolicyRuleReadOnly, PolicyRuleNotFoundError) as exc:
        code = status.HTTP_409_CONFLICT if isinstance(exc, PolicyRuleReadOnly) else status.HTTP_404_NOT_FOUND
        raise HTTPException(code, name)

@router.get("/api/policy-rules/{name}/usages")
async def rule_usages(name: str, ctx: Context, user: CurrentSuperuser):
    u = await PolicyRuleService(ctx.db).usages(name, org_id=ctx.org_id)
    return {"file_policies": u.file_policies, "tables": u.tables, "total": u.total}
```

Register it where other routers mount (grep `main.py` for `include_router(config`): add `app.include_router(policy_rules.router)`.

- [ ] **Step 4: Add save-time ref validation to the policy save handlers**

In the file-policy `set` handler (`api/src/routers/files.py`) and the table-policy save path, after parsing the incoming `FilePolicies`/`TablePolicies` and before persisting, resolve refs against a repo to validate (discard the result — this call only validates):

```python
    from api.shared.policy_rules import resolve_policy_refs, PolicyRuleNotFound, PolicyRuleDomainMismatch
    from src.repositories.policy_rule import PolicyRuleRepository
    rule_repo = PolicyRuleRepository(ctx.db, org_id=<target_org>, is_superuser=True)
    try:
        # validate against a COPY so the stored doc keeps its {$ref} entries
        await resolve_policy_refs(parsed.model_copy(deep=True), repo=rule_repo, action_domain="file")  # "table" in tables.py
    except (PolicyRuleNotFound, PolicyRuleDomainMismatch) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
```

- [ ] **Step 5: Run to verify it passes**

Run: `./test.sh e2e tests/e2e/test_policy_rules_api.py -v`
Expected: PASS (all three).

- [ ] **Step 6: Regenerate client types + commit**

Run: `cd client && npm run generate:types`

```bash
git add api/src/routers/policy_rules.py api/src/routers/files.py api/src/main.py api/tests/e2e/test_policy_rules_api.py client/src/lib/v1.d.ts
git commit -m "feat(policy-rules): REST CRUD + /usages + save-time ref validation (422)"
```

---

## Task 8: CLI — `policy-rule` group + `tables policies {get,set}`

**Files:**
- Create: `api/bifrost/commands/policy_rules.py`
- Modify: `api/bifrost/commands/tables.py` (add `policies` subgroup)
- Modify: `api/bifrost/commands/__init__.py` (register `policy-rule`)
- Test: `api/tests/e2e/test_cli_policy_rules.py`

**Interfaces:**
- Consumes: REST endpoints (Task 7). `files policies set/get` already exist and round-trip refs unchanged.

- [ ] **Step 1: Write the failing CLI e2e test**

```python
# api/tests/e2e/test_cli_policy_rules.py
import pytest, json

@pytest.mark.asyncio
async def test_policy_rule_create_list_via_cli(run_cli):   # run_cli: existing CLI harness fixture
    out = await run_cli(["policy-rule", "create", "--name", "ops",
                         "--body", json.dumps({"actions": ["read"], "when": None})])
    assert "ops" in out
    listed = await run_cli(["policy-rule", "list"])
    assert "ops" in listed

@pytest.mark.asyncio
async def test_tables_policies_set_get_roundtrips_ref(run_cli, seed_table):
    doc = {"policies": [{"$ref": "ops"}]}
    await run_cli(["policy-rule", "create", "--name", "ops",
                   "--body", json.dumps({"actions": ["read"], "when": None})])
    await run_cli(["tables", "policies", "set", seed_table, "--policies", json.dumps(doc)])
    got = await run_cli(["tables", "policies", "get", seed_table])
    assert "$ref" in got and "ops" in got
```

(Grep `tests/e2e` for the existing CLI invocation fixture — likely `test_cli_files.py` shows the pattern.)

- [ ] **Step 2: Run to verify it fails**

Run: `./test.sh e2e tests/e2e/test_cli_policy_rules.py -v`
Expected: FAIL (`Unknown entity subgroup: policy-rule`).

- [ ] **Step 3: Write the `policy-rule` CLI group**

Mirror `api/bifrost/commands/configs.py` exactly (entity_group, build_cli_flags from `PolicyRuleCreate`/`PolicyRuleUpdate`, `pass_resolver`/`run_async`/`output_result`):

```python
# api/bifrost/commands/policy_rules.py
import click
from bifrost.contracts import PolicyRuleCreate, PolicyRuleUpdate
from bifrost.dto_flags import DTO_EXCLUDES, build_cli_flags
from .base import entity_group, output_result, pass_resolver, run_async

policy_rules_group = entity_group("policy-rule", "Manage reusable named policy rules.")

@policy_rules_group.command("list")
@click.pass_context
@pass_resolver
@run_async
async def list_rules(ctx, *, client, resolver):  # noqa: ARG001
    resp = await client.get("/api/policy-rules"); resp.raise_for_status()
    output_result(resp.json(), ctx=ctx)

@policy_rules_group.command("get")
@click.argument("name")
@click.pass_context
@pass_resolver
@run_async
async def get_rule(ctx, name, *, client, resolver):  # noqa: ARG001
    resp = await client.get("/api/policy-rules"); resp.raise_for_status()
    match = next((r for r in resp.json() if r["name"] == name), None)
    output_result(match or {"error": f"{name} not found"}, ctx=ctx)

@policy_rules_group.command("usages")
@click.argument("name")
@click.pass_context
@pass_resolver
@run_async
async def usages(ctx, name, *, client, resolver):  # noqa: ARG001
    resp = await client.get(f"/api/policy-rules/{name}/usages"); resp.raise_for_status()
    output_result(resp.json(), ctx=ctx)
```

Add `create` / `update` / `delete` commands following the `configs.py` create/update pattern (flags via `build_cli_flags`, POST/PUT/DELETE to `/api/policy-rules[/{name}]`). Register in `__init__.py`: `from .policy_rules import policy_rules_group` and `"policy-rule": policy_rules_group` in `ENTITY_GROUPS`. Export `PolicyRuleCreate/Update` from `bifrost.contracts` (mirror how `ConfigCreate` is exported).

- [ ] **Step 4: Add `tables policies {get,set}` mirroring `files policies`**

In `api/bifrost/commands/tables.py`, add a `policies` subgroup (copy the shape of `policies_group` in `files.py`):

```python
tables_policies = click.Group("policies", help="Get/set a table's access policy document.")

@tables_policies.command("get")
@click.argument("name")
@click.pass_context
@pass_resolver
@run_async
async def get_table_policies(ctx, name, *, client, resolver):
    table_id = await resolver.resolve("table", name)
    resp = await client.get(f"/api/tables/{table_id}"); resp.raise_for_status()
    output_result(resp.json().get("policies"), ctx=ctx)

@tables_policies.command("set")
@click.argument("name")
@click.option("--policies", required=True, help="JSON literal or @file path.")
@click.pass_context
@pass_resolver
@run_async
async def set_table_policies(ctx, name, policies, *, client, resolver):
    table_id = await resolver.resolve("table", name)
    doc = _load_policy_document_like_files(policies)   # reuse files.py loader shape
    resp = await client.put(f"/api/tables/{table_id}", json={"policies": doc})
    resp.raise_for_status()
    output_result(resp.json().get("policies"), ctx=ctx)

tables_group.add_command(tables_policies)
```

- [ ] **Step 5: Run to verify it passes**

Run: `./test.sh e2e tests/e2e/test_cli_policy_rules.py -v`
Expected: PASS.

- [ ] **Step 6: Run DTO parity + contract tripwire**

Run: `./test.sh tests/unit/test_dto_flags.py tests/unit/test_contract_version.py -v`
Expected: PASS — if the contract fingerprint test fails, refresh `EXPECTED_CONTRACT_FINGERPRINT` (additive change → fingerprint refresh only, no `CONTRACT_VERSION` bump). Regenerate skill appendices: `python api/scripts/skill-truth/generate.py`.

- [ ] **Step 7: Commit**

```bash
git add api/bifrost/commands/policy_rules.py api/bifrost/commands/tables.py api/bifrost/commands/__init__.py api/tests/e2e/test_cli_policy_rules.py api/.../contract_fingerprint* api/.../generated
git commit -m "feat(policy-rules): CLI policy-rule group + tables policies get/set"
```

---

## Task 9: MCP thin wrapper

**Files:**
- Create: `api/src/services/mcp_server/tools/policy_rules.py`
- Register it in the MCP tool registry (mirror `configs.py` registration)
- Test: `api/tests/unit/test_mcp_thin_wrapper.py` already enforces the no-ORM rule; add a tool-level test if the entity has a dedicated MCP test pattern.

**Interfaces:**
- Consumes: REST endpoints via `call_rest` / `rest_client` (no ORM).

- [ ] **Step 1: Write the tool (mirror `tools/configs.py`)**

```python
# api/src/services/mcp_server/tools/policy_rules.py
from typing import Any
from ._http_bridge import call_rest
from .base import ToolResult, error_result, success_result  # match configs.py imports

async def list_policy_rules(context: Any) -> ToolResult:
    status_code, body = await call_rest(context, "GET", "/api/policy-rules")
    if status_code != 200:
        return error_result(f"list_policy_rules failed: HTTP {status_code}", {"body": body})
    items = body if isinstance(body, list) else []
    return success_result(f"Found {len(items)} policy rule(s)", {"policy_rules": items, "count": len(items)})

async def create_policy_rule(context: Any, name: str, body: dict,
                             description: str | None = None,
                             organization_id: str | None = None) -> ToolResult:
    payload = {"name": name, "body": body, "description": description, "organization_id": organization_id}
    status_code, resp = await call_rest(context, "POST", "/api/policy-rules", json=payload)
    if status_code != 201:
        return error_result(f"create_policy_rule failed: HTTP {status_code}", {"body": resp})
    return success_result(f"Created policy rule {name}", resp)

async def delete_policy_rule(context: Any, name: str) -> ToolResult:
    status_code, resp = await call_rest(context, "DELETE", f"/api/policy-rules/{name}")
    if status_code != 204:
        return error_result(f"delete_policy_rule failed: HTTP {status_code}", {"body": resp})
    return success_result(f"Deleted policy rule {name}", {})
```

Register in the MCP tool list exactly as `configs` tools are registered (grep the registry for `list_configs`).

- [ ] **Step 2: Run the thin-wrapper enforcement test**

Run: `./test.sh tests/unit/test_mcp_thin_wrapper.py -v`
Expected: PASS (no ORM imports in the new tool).

- [ ] **Step 3: Commit**

```bash
git add api/src/services/mcp_server/tools/policy_rules.py api/src/services/mcp_server/tools/__init__.py
git commit -m "feat(policy-rules): MCP thin HTTP-bridge tool"
```

---

## Task 10: Manifest round-trip (`ManifestPolicyRule`, rule-before-policy import)

**Files:**
- Modify: `api/bifrost/manifest.py` (add `ManifestPolicyRule`)
- Modify: `api/src/services/manifest_generator.py` (serialize)
- Modify: `api/src/services/manifest_import.py` (`_resolve_policy_rule`, ordered before policies)
- Test: `api/tests/unit/test_manifest.py` (round-trip), `api/tests/e2e/platform/test_git_sync_local.py` (ordering)

**Interfaces:**
- Consumes: `PolicyRule`, the `EntityCodec`/`classify` manifest patterns.
- Produces: `ManifestPolicyRule(name, description, body, organization_id?)`; serializer; `_resolve_policy_rule` upsert by `(organization_id, name)`.

- [ ] **Step 1: Write the failing round-trip test**

```python
# add to api/tests/unit/test_manifest.py
def test_policy_rule_manifest_roundtrip():
    from api.bifrost.manifest import ManifestPolicyRule
    m = ManifestPolicyRule(name="ops", description="d",
                           body={"actions": ["read"], "when": None}, organization_id=None)
    again = ManifestPolicyRule.model_validate(m.model_dump())
    assert again.name == "ops" and again.body["actions"] == ["read"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `./test.sh tests/unit/test_manifest.py -k policy_rule -v`
Expected: FAIL (`ImportError: ManifestPolicyRule`).

- [ ] **Step 3: Add `ManifestPolicyRule`** (mirror `ManifestConfig`'s `classify`/`from_row`/`to_orm_values`)

```python
# api/bifrost/manifest.py
class ManifestPolicyRule(EntityCodec, BaseModel):
    name: str = Field(**classify(FieldClass.CONTENT, match_key=True))
    description: str | None = Field(default=None, **classify(FieldClass.CONTENT))
    body: dict = Field(**classify(FieldClass.CONTENT))
    organization_id: str | None = Field(default=None, **classify(FieldClass.ENVIRONMENT, match_key=True))

    @classmethod
    def from_row(cls, r) -> "ManifestPolicyRule":
        return cls(name=r.name, description=r.description, body=r.body,
                   organization_id=str(r.organization_id) if r.organization_id else None)

    def to_orm_values(self, dest) -> "ImportFields":
        return ImportFields(direct={
            "name": self.name, "description": self.description, "body": self.body,
            "organization_id": self.organization_id,
        }, indexer_content={}, restamp={})
```

- [ ] **Step 4: Serialize in `manifest_generator.py`** (add `policy_rules` collection to the manifest, sourced from `PolicyRule` rows where `is_builtin == False`).

- [ ] **Step 5: Import in `manifest_import.py`** — add `_resolve_policy_rule` upserting by `(organization_id, name)`, and ensure it runs **before** policy/table resolution in the import ordering (so a `{$ref}` resolves at validation). Builtin `admin_bypass` is excluded from export and seeded separately, so it is never imported.

- [ ] **Step 6: Add the git-sync ordering e2e test** in `test_git_sync_local.py`: export a rule + a file policy referencing it, re-import into a clean DB, assert both land and the ref resolves.

- [ ] **Step 7: Run + commit**

Run: `./test.sh tests/unit/test_manifest.py -k policy_rule -v && ./test.sh e2e tests/e2e/platform/test_git_sync_local.py -k policy_rule -v`
Expected: PASS.

```bash
git add api/bifrost/manifest.py api/src/services/manifest_generator.py api/src/services/manifest_import.py api/tests/unit/test_manifest.py api/tests/e2e/platform/test_git_sync_local.py
git commit -m "feat(policy-rules): ManifestPolicyRule round-trip + rule-before-policy import"
```

---

## Task 11: Frontend — reference mode in Files + Tables policy editors

**Files:**
- Create: `client/src/services/policyRules.ts` (+ `policyRules.test.ts`)
- Modify: `client/src/components/files/` policy editor (`PolicyEditorModal`) — reference-mode insert
- Modify: `client/src/components/tables/` policy editor — reference-mode insert
- Test: vitest siblings + `client/e2e/files-explorer.admin.spec.ts` (or new `policy-rules.admin.spec.ts`)

**Interfaces:**
- Consumes: `/api/policy-rules` (typed via generated `v1.d.ts`).
- Produces: `listPolicyRules()`, `policyRuleUsages(name)` service fns; an "Insert reference…" action in both editors.

- [ ] **Step 1: Write the failing service test**

```ts
// client/src/services/policyRules.test.ts
import { describe, it, expect, vi } from "vitest";
import { listPolicyRules } from "./policyRules";

describe("policyRules service", () => {
  it("GETs /api/policy-rules", async () => {
    const spy = vi.fn().mockResolvedValue({ data: [{ name: "ops" }] });
    // mock apiClient.get per the project's existing service test pattern
    expect((await listPolicyRules(spy as any))[0].name).toBe("ops");
  });
});
```

(Match the existing `client/src/services/*.test.ts` mocking pattern — grep for one, e.g. `filePolicies.test.ts`.)

- [ ] **Step 2: Run to verify it fails**

Run: `./test.sh client unit policyRules`
Expected: FAIL (module missing).

- [ ] **Step 3: Write the service** (mirror `client/src/services/filePolicies.ts`)

```ts
// client/src/services/policyRules.ts
import { apiClient } from "@/lib/api-client";
import type { components } from "@/lib/v1";
export type PolicyRule = components["schemas"]["PolicyRulePublic"];

export async function listPolicyRules() {
  return apiClient.get<PolicyRule[]>("/api/policy-rules");
}
export async function policyRuleUsages(name: string) {
  return apiClient.get(`/api/policy-rules/${name}/usages`);
}
```

- [ ] **Step 4: Add "Insert reference…" to both editors**

In the Files `PolicyEditorModal` and the Tables policy editor, extend the existing "Insert template…" dropdown with a **reference** option sourced from `listPolicyRules()` (filtered to the domain by each rule's `body.actions`), inserting `{"$ref": name}` into the `JsonYamlEditor` buffer instead of a deep copy. Surface the server `422` (unresolvable ref) inline at the offending index using the editors' existing `PolicyValidationError` channel.

- [ ] **Step 5: Run vitest + add a Playwright assertion**

Run: `./test.sh client unit policyRules`
Expected: PASS.

Add to `client/e2e/files-explorer.admin.spec.ts` (and a tables policy spec): open the policy editor, choose "Insert reference… → admin_bypass", save, assert the document contains `$ref`.

- [ ] **Step 6: tsc + lint + commit**

Run: `cd client && npm run tsc && npm run lint`

```bash
git add client/src/services/policyRules.ts client/src/services/policyRules.test.ts client/src/components/files client/src/components/tables client/e2e
git commit -m "feat(policy-rules): reference mode in Files + Tables policy editors"
```

---

## Task 12: Frontend — in-context policy-rules manager (list/edit/where-used)

**Files:**
- Create: `client/src/components/policy-rules/PolicyRulesManager.tsx` (+ test)
- Modify: both policy editors to add a "Manage rules…" affordance opening the manager
- Test: vitest sibling + Playwright

**Interfaces:**
- Consumes: `policyRules.ts` service.

- [ ] **Step 1: Write the failing component test**

```tsx
// client/src/components/policy-rules/PolicyRulesManager.test.tsx
import { render, screen } from "@testing-library/react";
import { PolicyRulesManager } from "./PolicyRulesManager";
// mock listPolicyRules to return [{name:"ops", body:{actions:["read"]}}]
it("lists rules", async () => {
  render(<PolicyRulesManager />);
  expect(await screen.findByText("ops")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `./test.sh client unit PolicyRulesManager`
Expected: FAIL.

- [ ] **Step 3: Build the manager** — list rules, create/edit (the `JsonYamlEditor` body), delete. Before saving an edit, fetch `policyRuleUsages(name)` and show "used by N file prefixes and M tables". On a blocked delete (409), render the returned usages list. Built-in `admin_bypass` shows read-only (no edit/delete).

- [ ] **Step 4: Wire "Manage rules…" into both editors** (a button beside the reference dropdown opening the manager as a slideout/modal — reachable in-context from Files and Tables policy surfaces).

- [ ] **Step 5: Run vitest + Playwright; tsc + lint**

Run: `./test.sh client unit PolicyRulesManager && cd client && npm run tsc && npm run lint`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add client/src/components/policy-rules client/src/components/files client/src/components/tables
git commit -m "feat(policy-rules): in-context policy-rules manager with blast-radius"
```

---

## Task 13: Full verification sweep

- [ ] **Step 1: Backend**

Run: `cd api && pyright && ruff check .`
Expected: 0 errors.

- [ ] **Step 2: Regenerate types + frontend checks**

Run: `cd client && npm run generate:types && npm run tsc && npm run lint`
Expected: PASS.

- [ ] **Step 3: Full backend suite**

Run: `./test.sh all`
Expected: green (parse `/tmp/bifrost-<project>/test-results.xml`). Confirm existing policy/table/file e2e unaffected and the new policy-rule tests pass.

- [ ] **Step 4: Client suites**

Run: `./test.sh client unit && ./test.sh client e2e files-explorer.admin.spec.ts`
Expected: PASS.

- [ ] **Step 5: Final commit if any fixups**

```bash
git add -A && git commit -m "chore(policy-rules): verification fixups"
```

---

## Notes for the implementer

- **Import paths:** the codebase imports shared utilities as `from api.shared.policy_rules import ...` in some places and `from shared.policy_rules import ...` in others depending on the package root. Match the style of the file you're editing (e.g. `file_policy_service.py` uses `from shared.claims.preresolve import ...`). Use the same root as neighboring imports.
- **`ctx.org_id` vs target org:** for the policy-rules router, rules are org-scoped to the caller's org by default; a superuser/provider creating a **global** rule passes `organization_id=None` in the body. Honor `resolve_target_org`/`resolve_effective_scope` if the existing entity routers do — mirror `config.py`'s scope handling rather than inventing one.
- **Where the built-in gets seeded:** find the existing startup seed hook (grep for other idempotent seeds run at boot) and add `PolicyRuleService(db).seed_builtin_admin_bypass()` there, so `{"$ref":"admin_bypass"}` always resolves. If no global seed hook exists, seed lazily in the file-policy create branch before inserting the seed doc.
- **JSONB dirty tracking:** always reassign the whole `policies`/`access` dict on mutation (Task 6 `_rewrite_ref`), never mutate in place.
