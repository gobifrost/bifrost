# Reusable Named Policy Rules — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let file and table policies reference reusable named rules by `{"$ref": name}` so a rule (e.g. `admin_bypass`) is defined once and applied across many policies.

**Architecture:** A shared org-scoped `PolicyRule` entity (cascade org→global via `OrgScopedRepository`, with an explicit `domain` discriminator) holds a single `{actions, when}` body. Both policy documents' rule lists become a `Rule | PolicyRuleRef` union. A **single resolving loader per domain** validates the document and inlines refs **before** the pure evaluator / SQL compiler / claim pre-resolution runs — every evaluation site routes through it, so a missed site is impossible by construction. Integrity via hard-fail-on-missing, Core-write override-aware rename cascade, and a delete-while-referenced guard.

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy (async) / Pydantic v2 / Alembic / PostgreSQL JSONB; Click CLI; React/TypeScript client.

**Spec:** `docs/superpowers/specs/2026-06-22-named-policy-rules-design.md` (incl. the "Codex pre-implementation review — corrections" section, which this plan implements).

## Global Constraints

- **Worktree only.** All work in `/home/jack/GitHub/bifrost/.claude/worktrees/files-sdk-policies` (branch `codex/files-sdk-policies`). Never touch the primary checkout.
- **Org scoping is canonical.** `PolicyRule` resolves through `OrgScopedRepository` — never hand-roll `WHERE organization_id == x OR IS NULL`. Read `api/src/repositories/README.md` first.
- **Single choke point.** Every policy load *for evaluation* goes through the domain's resolving loader (`load_resolved_table_policies` / the file `is_allowed` path). A raw `TablePolicies.model_validate` / `FilePolicies.model_validate` used for evaluation is a bug. Refs MUST be inlined before `evaluate_*` / `compile_read_filter` / `preresolve_for_policies`.
- **Resolve before claim pre-resolution.** A `PolicyRuleRef` has no `.when`; `preresolve_for_policies` would `AttributeError`. The resolving loader runs resolution first.
- **Hard-fail on missing/mismatched ref** — raise, never silently drop. At enforcement sites a raise is caught → deny + log; at save/import/deploy a raise → structured 422 / import failure.
- **Two `admin_bypass` built-ins** — one per domain (file vs table action sets). Read-only, seeded idempotently before any `{"$ref":"admin_bypass"}` can resolve.
- **Core writes for the rename cascade** — solution-managed rows trip the `before_flush` guard under ORM mutation. Use Core `update()`. Install the guard in cascade tests to be prod-faithful.
- **Three parallel surfaces** for entity mutations: REST + CLI + MCP, fed from one `PolicyRuleCreate`/`PolicyRuleUpdate` DTO pair. MCP tools are thin HTTP bridges (no ORM).
- **No dead code / no unrequested fallbacks.**
- **Tests use `./test.sh`** (Dockerized). JUnit at `/tmp/bifrost-<project>/test-results.xml`.
- **No migration of existing inline `admin_bypass` rows.** All rule writes require admin (the bypass check `is_platform_admin OR is_provider_org`); a global rule is a bypass-gated write.
- **`PolicyRule` is solution-scopable (Codex R2/C1).** `PolicyRule` carries `solution_id` from the start so a solution can ship its own named rules. Resolution is **own-solution → org → global** (mirroring workflows/tables). The resolver, where-used, rename, delete-guard, and manifest import are all solution-scope-aware. Built-ins stay global (`solution_id` NULL). See Task 1 (column) + Task 4/5 (resolver threading). The actual *wiring of solution context into file/table policy evaluation* for solution-managed policies is owned by the **solution-scoped-files plan** (`2026-06-22-solution-scoped-files.md`), which runs after this one — but the column + resolver signature + repo support land HERE so that plan has them.

---

## File Structure

| File | Responsibility | New/Mod |
|------|----------------|---------|
| `api/src/models/orm/policy_rule.py` | `PolicyRule` ORM (+ `domain`, partial unique indexes) | New |
| `api/alembic/versions/<rev>_policy_rule.py` | table + partial unique indexes + expression GIN on JSONB policy arrays | New |
| `api/src/models/contracts/policy_rule.py` | `PolicyRuleCreate/Update/Public` (domain, validated body) | New |
| `api/src/models/contracts/policies.py` | `PolicyRuleRef` (+`extra=forbid`); widen unions; `extra=forbid` + mixed-field validator on inline rules | Mod |
| `api/src/repositories/policy_rule.py` | `PolicyRuleRepository(OrgScopedRepository)` | New |
| `api/shared/policy_rules.py` | `resolve_policy_refs`, exceptions, override-aware where-used, Core-update rename cascade helper | New |
| `api/src/services/table_policy_loader.py` | `load_resolved_table_policies(table, db)` choke point | New |
| `api/shared/file_policies_seed.py` | seed as `{"$ref":"admin_bypass"}` | Mod |
| `api/src/services/policy_rule_service.py` | CRUD + write-time body validation + rename cascade + delete-guard + seed two built-ins + audit | New |
| `api/src/services/file_policy_service.py` | resolve refs in `is_allowed` (before preresolve) | Mod |
| `api/src/routers/tables.py` | route `_load_policies` + save-validate through resolver | Mod |
| `api/src/routers/websocket.py` | route its `TablePolicies` load through resolver + invalidate cache on rule edit | Mod |
| `api/shared/claims/preresolve.py` | `_load_source_policies` resolves refs before compile | Mod |
| `api/src/routers/policy_rules.py` | REST CRUD + `/usages` (structured validation errors) | New |
| `api/src/routers/files.py` | file-policy save ref-validation (structured) | Mod |
| `api/src/services/solutions/deploy.py` | table-policy deploy validation resolves refs | Mod |
| `api/src/services/manifest_import.py` | file + table policy import ref-validation; rule-before-policy | Mod |
| `api/bifrost/commands/policy_rules.py` | `bifrost policy-rule` group | New |
| `api/bifrost/commands/tables.py` | `tables policies {get,set}` subgroup | Mod |
| `api/bifrost/commands/__init__.py` | register groups | Mod |
| `api/src/services/mcp_server/tools/policy_rules.py` | thin MCP wrapper | New |
| `api/bifrost/manifest.py` | `ManifestPolicyRule` + table `ManifestPolicy` inline-or-ref union | Mod |
| `api/src/services/manifest_generator.py` | serialize rules; preserve `$ref` in policies | Mod |
| `client/src/services/policyRules.ts` | API wrapper | New |
| `client/src/components/{files,tables}/` policy editors | reference mode + rules manager | Mod |

---

## Task 1: `PolicyRule` ORM model + migration (domain + partial unique + expression GIN)

**Files:**
- Create: `api/src/models/orm/policy_rule.py`
- Create: `api/alembic/versions/<rev>_policy_rule.py`
- Test: `api/tests/unit/test_policy_rule_model.py`

**Interfaces:**
- Produces: `PolicyRule` with `id, organization_id|NULL, solution_id|NULL, name, domain('file'|'table'), description|NULL, body:JSONB({actions,when}), is_builtin:bool, created_by|NULL, created_at, updated_at`; partial unique indexes per scope tier (global / org / solution — see step 3).
- **`solution_id` (Codex R2/C1):** a solution can ship its own named rules; resolution is own-solution→org→global. Built-ins are global (`solution_id` NULL).

> **Why `domain` (correction #7):** `read`/`delete` are valid in both file and table vocabularies, so the domain can't be inferred from actions. An explicit column lets write-validation and `resolve_policy_refs` reject cross-domain use, and lets the two `admin_bypass` built-ins coexist by name.

- [ ] **Step 1: Write the failing test**

```python
# api/tests/unit/test_policy_rule_model.py
from src.models.orm.policy_rule import PolicyRule

def test_columns_and_defaults():
    r = PolicyRule(name="ops", domain="file", body={"actions": ["write"], "when": None})
    assert r.organization_id is None and r.is_builtin is False
    for col in ("id","name","domain","description","body","is_builtin","created_by","created_at","updated_at"):
        assert hasattr(r, col)

def test_partial_unique_indexes_declared():
    idx = {i.name: i for i in PolicyRule.__table__.indexes}
    assert "uq_policy_rules_global_name_domain" in idx
    assert "uq_policy_rules_org_name_domain" in idx
    assert idx["uq_policy_rules_global_name_domain"].unique
    assert idx["uq_policy_rules_org_name_domain"].unique
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/test_policy_rule_model.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write the ORM model**

```python
# api/src/models/orm/policy_rule.py
"""Reusable named policy rule, referenced by {"$ref": name} from file/table policies."""
from __future__ import annotations
from datetime import datetime, timezone
from uuid import UUID, uuid4
from sqlalchemy import Boolean, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from src.models.orm.base import Base

class PolicyRule(Base):
    """A named, reusable policy rule body ({actions, when}). Cascade org→global; per-domain."""
    __tablename__ = "policy_rules"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID | None] = mapped_column(ForeignKey("organizations.id"), default=None)
    solution_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("solutions.id", ondelete="CASCADE"), default=None)  # Codex R2/C1: solution-shippable rules
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    domain: Mapped[str] = mapped_column(String(8), nullable=False)  # 'file' | 'table'
    description: Mapped[str | None] = mapped_column(Text, default=None)
    body: Mapped[dict] = mapped_column(JSONB, nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        # Partial unique indexes per scope tier — NULLs don't compare equal, so a plain
        # UNIQUE would allow duplicate globals and break scalar_one_or_none() (correction #4).
        # Three mutually-exclusive tiers: global (both NULL), org (org set, sol NULL),
        # solution (sol set). Codex R2/C1 adds the solution tier.
        Index("uq_policy_rules_global_name_domain", "name", "domain", unique=True,
              postgresql_where=text("organization_id IS NULL AND solution_id IS NULL")),
        Index("uq_policy_rules_org_name_domain", "organization_id", "name", "domain", unique=True,
              postgresql_where=text("organization_id IS NOT NULL AND solution_id IS NULL")),
        Index("uq_policy_rules_solution_name_domain", "solution_id", "name", "domain", unique=True,
              postgresql_where=text("solution_id IS NOT NULL")),
    )
```

Register in the ORM model import location (grep where `from src.models.orm.config import Config` is collected; add `PolicyRule`).

- [ ] **Step 4: Run model test to verify it passes**

Run: `./test.sh tests/unit/test_policy_rule_model.py -v`
Expected: PASS.

- [ ] **Step 5: Create the migration**

```bash
cd api && alembic revision -m "policy_rules + partial unique + expression GIN on policy arrays"
```

```python
def upgrade() -> None:
    op.create_table(
        "policy_rules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("domain", sa.String(length=8), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("uq_policy_rules_global_name_domain", "policy_rules", ["name", "domain"],
                    unique=True, postgresql_where=sa.text("organization_id IS NULL"))
    op.create_index("uq_policy_rules_org_name_domain", "policy_rules",
                    ["organization_id", "name", "domain"],
                    unique=True, postgresql_where=sa.text("organization_id IS NOT NULL"))
    # Expression GIN matching the where-used query shape `(col -> 'policies') @> [...]`
    # (correction #8 — a GIN on the whole column would NOT serve the extracted-array query).
    op.create_index("ix_file_policies_rules_gin", "file_policies",
                    [sa.text("(policies -> 'policies') jsonb_path_ops")], postgresql_using="gin")
    op.create_index("ix_tables_access_rules_gin", "tables",
                    [sa.text("(access -> 'policies') jsonb_path_ops")], postgresql_using="gin")

def downgrade() -> None:
    op.drop_index("ix_tables_access_rules_gin", table_name="tables")
    op.drop_index("ix_file_policies_rules_gin", table_name="file_policies")
    op.drop_index("uq_policy_rules_org_name_domain", table_name="policy_rules")
    op.drop_index("uq_policy_rules_global_name_domain", table_name="policy_rules")
    op.drop_table("policy_rules")
```

Add `from alembic import op` / `import sqlalchemy as sa` / `from sqlalchemy.dialects import postgresql` if the template lacks them.

- [ ] **Step 6: Apply migration + verify**

Run: `./test.sh stack reset && ./test.sh tests/unit/test_policy_rule_model.py -v`
Expected: PASS, no migration error.

- [ ] **Step 7: Commit**

```bash
git add api/src/models/orm/policy_rule.py api/alembic/versions/*policy_rule*.py api/tests/unit/test_policy_rule_model.py
git commit -m "feat(policy-rules): PolicyRule ORM (domain, partial-unique) + expression GIN migration"
```

---

## Task 2: Contracts — `PolicyRuleRef` (forbid-extra) + hardened unions + validated DTOs

**Files:**
- Modify: `api/src/models/contracts/policies.py` (add `PolicyRuleRef`; widen unions; `extra="forbid"` + mixed-field validator on `FilePolicyRule`/`Policy`)
- Create: `api/src/models/contracts/policy_rule.py`
- Test: `api/tests/unit/test_policy_rule_contracts.py`

**Interfaces:**
- Produces: `PolicyRuleRef(ref)` (alias `$ref`, `extra="forbid"`); hardened `FilePolicies`/`TablePolicies` unions; `PolicyRuleCreate(name, domain, description?, body)`, `PolicyRuleUpdate(name?, description?, body?)`, `PolicyRulePublic`, with **body validated** (actions non-empty/unique, parseable `when`, domain-consistent).

- [ ] **Step 1: Write the failing test (incl. the F7 drop bug)**

```python
# api/tests/unit/test_policy_rule_contracts.py
import pytest
from pydantic import ValidationError
from src.models.contracts.policies import FilePolicies, TablePolicies, PolicyRuleRef

def test_ref_parses_via_dollar_alias():
    assert PolicyRuleRef.model_validate({"$ref": "ab"}).ref == "ab"

def test_mixed_ref_plus_inline_is_rejected():
    # F7: an entry carrying BOTH $ref and inline fields must NOT silently parse as inline.
    with pytest.raises(ValidationError):
        FilePolicies.model_validate({"policies": [
            {"$ref": "ab", "name": "r", "actions": ["read"], "when": None}]})

def test_ref_with_extra_key_is_rejected():
    with pytest.raises(ValidationError):
        FilePolicies.model_validate({"policies": [{"$ref": "ab", "actions": ["read"]}]})

def test_clean_mixed_list_ok():
    doc = FilePolicies.model_validate({"policies": [
        {"$ref": "ab"}, {"name": "r", "actions": ["read"], "when": None}]})
    assert isinstance(doc.policies[0], PolicyRuleRef)
    assert doc.policies[1].name == "r"

def test_table_ref_ok():
    assert isinstance(TablePolicies.model_validate({"policies": [{"$ref": "x"}]}).policies[0], PolicyRuleRef)
```

- [ ] **Step 2: Run to verify it fails**

Run: `./test.sh tests/unit/test_policy_rule_contracts.py -v`
Expected: FAIL.

- [ ] **Step 3: Add `PolicyRuleRef` and harden the inline models in `policies.py`**

```python
from pydantic import ConfigDict, model_validator

class PolicyRuleRef(BaseModel):
    """A reference to a named PolicyRule, spliced inline at resolution time."""
    ref: str = Field(alias="$ref", min_length=1, max_length=100)
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
```

On BOTH `FilePolicyRule` and `Policy`, add `extra="forbid"` and a guard so an inline rule can't carry `$ref`:

```python
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _reject_ref_field(cls, data):
        if isinstance(data, dict) and "$ref" in data:
            raise ValueError("an inline rule must not carry $ref; use a bare {\"$ref\": name} entry")
        return data
```

Widen the unions (ref **last** so the structurally-richer inline rule is tried first, and `extra=forbid` rejects the ambiguous overlap):

```python
class FilePolicies(BaseModel):
    policies: list[FilePolicyRule | PolicyRuleRef] = Field(default_factory=list)

class TablePolicies(BaseModel):
    policies: list[Policy | PolicyRuleRef] = Field(default_factory=list)
```

> Verify no existing stored policy has unknown extra keys that `extra="forbid"` would now reject. Grep tests/fixtures for policy docs; if any carry stray keys, that's a pre-existing data issue to surface, not silently allow.

- [ ] **Step 4: Create `policy_rule.py` DTOs with validated body**

```python
# api/src/models/contracts/policy_rule.py
from __future__ import annotations
from datetime import datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator
from src.models.contracts.policies import FilePolicyRule, Policy

Domain = Literal["file", "table"]

def _validate_body(body: dict, domain: str) -> dict:
    """Validate a rule body against its domain by round-tripping through the inline model."""
    probe = {"name": "_probe", "actions": body.get("actions"), "when": body.get("when")}
    (FilePolicyRule if domain == "file" else Policy).model_validate(probe)  # raises on bad actions/when
    return body

class PolicyRuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    domain: Domain
    description: str | None = None
    body: dict
    organization_id: UUID | None = None
    @model_validator(mode="after")
    def _check_body(self):
        _validate_body(self.body, self.domain); return self

class PolicyRuleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    body: dict | None = None
    # domain is immutable; body re-validated against the stored domain in the service.

class PolicyRulePublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    organization_id: UUID | None
    name: str
    domain: Domain
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

Run (dev stack up): `cd client && npm run generate:types`

```bash
git add api/src/models/contracts/policies.py api/src/models/contracts/policy_rule.py api/tests/unit/test_policy_rule_contracts.py client/src/lib/v1.d.ts
git commit -m "feat(policy-rules): forbid-extra ref union (no \$ref drop) + domain-validated DTOs"
```

---

## Task 3: `PolicyRuleRepository` + override-aware where-used

**Files:**
- Create: `api/src/repositories/policy_rule.py`
- Create part of: `api/shared/policy_rules.py` (where-used only)
- Test: `api/tests/e2e/test_policy_rule_repo.py`

**Interfaces:**
- Produces: `PolicyRuleRepository(session, org_id, ...)` with inherited cascade `get(name=..., domain=...)`; `find_policy_rule_usages(db, name, domain, *, org_id) -> PolicyRuleUsages` — **override-aware** (a global rule's scan excludes orgs that define their own `(name, domain)`).

- [ ] **Step 1: Write failing tests (cascade + override-aware where-used)**

```python
# api/tests/e2e/test_policy_rule_repo.py
import pytest
from src.models.orm.policy_rule import PolicyRule
from src.models.orm.file_metadata import FilePolicy
from src.repositories.policy_rule import PolicyRuleRepository
from api.shared.policy_rules import find_policy_rule_usages

@pytest.mark.asyncio
async def test_get_cascades_org_over_global(db_session, seed_org):
    db_session.add(PolicyRule(name="r", domain="file", body={"actions": ["read"], "when": None}))
    db_session.add(PolicyRule(name="r", domain="file", organization_id=seed_org, body={"actions": ["write"], "when": None}))
    await db_session.flush()
    repo = PolicyRuleRepository(db_session, org_id=seed_org, is_superuser=True)
    assert (await repo.get(name="r", domain="file")).body["actions"] == ["write"]

@pytest.mark.asyncio
async def test_where_used_for_global_skips_org_with_override(db_session, seed_org, other_org):
    # global rule "ops" + an org override of "ops" in seed_org.
    db_session.add(PolicyRule(name="ops", domain="file", body={"actions": ["read"], "when": None}))
    db_session.add(PolicyRule(name="ops", domain="file", organization_id=seed_org, body={"actions": ["read"], "when": None}))
    # seed_org policy references "ops" → resolves to the OVERRIDE, not the global.
    db_session.add(FilePolicy(organization_id=seed_org, location="shared", path="a/", policies={"policies": [{"$ref": "ops"}]}))
    # other_org policy references "ops" → resolves to the GLOBAL.
    db_session.add(FilePolicy(organization_id=other_org, location="shared", path="b/", policies={"policies": [{"$ref": "ops"}]}))
    await db_session.flush()
    u = await find_policy_rule_usages(db_session, "ops", "file", org_id=None)  # global rule
    locs = {f["location"]+f["path"] for f in u.file_policies}
    assert "shareda/" not in locs            # seed_org overrides → NOT a usage of the global
    assert "sharedb/" in locs                # other_org → genuine usage of the global
```

- [ ] **Step 2: Run to verify it fails**

Run: `./test.sh e2e tests/e2e/test_policy_rule_repo.py -v`
Expected: FAIL (`ImportError`).

- [ ] **Step 3: Repository**

```python
# api/src/repositories/policy_rule.py
from src.models.orm.policy_rule import PolicyRule
from src.repositories.org_scoped import OrgScopedRepository

class PolicyRuleRepository(OrgScopedRepository[PolicyRule]):
    """Cascade org→global resolution for named policy rules."""
    model = PolicyRule
    role_table = None
```

- [ ] **Step 4: Override-aware where-used in `api/shared/policy_rules.py`**

```python
# api/shared/policy_rules.py  (where-used portion)
from __future__ import annotations
from dataclasses import dataclass, field
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.models.orm.file_metadata import FilePolicy
from src.models.orm.tables import Table
from src.models.orm.policy_rule import PolicyRule

@dataclass
class PolicyRuleUsages:
    file_policies: list[dict] = field(default_factory=list)
    tables: list[dict] = field(default_factory=list)
    @property
    def total(self) -> int:
        return len(self.file_policies) + len(self.tables)

async def find_policy_rule_usages(db: AsyncSession, name: str, domain: str, *, org_id: UUID | None) -> PolicyRuleUsages:
    """Find file/table policies that reference {"$ref": name} and resolve to THIS rule.

    org-scoped rule (org_id set): exact scan within that org.
    global rule (org_id None): scan all orgs, BUT exclude any org that defines its own
      (name, domain) override — there a {"$ref": name} resolves to the override, not this
      global (correction #6/#5).
    """
    ref_json = [{"$ref": name}]
    fp = select(FilePolicy.id, FilePolicy.organization_id, FilePolicy.location, FilePolicy.path).where(
        FilePolicy.policies["policies"].contains(ref_json))
    tb = select(Table.id, Table.organization_id, Table.name).where(
        Table.access["policies"].contains(ref_json))
    if domain == "file":
        tb = tb.where(False)  # a file rule can only be referenced by file policies
    if domain == "table":
        fp = fp.where(False)
    if org_id is not None:
        fp = fp.where(FilePolicy.organization_id == org_id)
        tb = tb.where(Table.organization_id == org_id)
        override_orgs: set = set()
    else:
        override_orgs = {
            o for (o,) in (await db.execute(
                select(PolicyRule.organization_id).where(
                    PolicyRule.name == name, PolicyRule.domain == domain,
                    PolicyRule.organization_id.isnot(None))
            )).all()
        }
    usages = PolicyRuleUsages()
    for r in (await db.execute(fp)).all():
        if r.organization_id in override_orgs:  # global rule shadowed here
            continue
        usages.file_policies.append({"id": str(r.id), "location": r.location, "path": r.path,
                                     "organization_id": str(r.organization_id) if r.organization_id else None})
    for r in (await db.execute(tb)).all():
        if r.organization_id in override_orgs:
            continue
        usages.tables.append({"id": str(r.id), "name": r.name,
                              "organization_id": str(r.organization_id) if r.organization_id else None})
    return usages
```

- [ ] **Step 5: Run to verify it passes**

Run: `./test.sh e2e tests/e2e/test_policy_rule_repo.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add api/src/repositories/policy_rule.py api/shared/policy_rules.py api/tests/e2e/test_policy_rule_repo.py
git commit -m "feat(policy-rules): cascade repo + override-aware where-used"
```

---

## Task 4: `resolve_policy_refs` (domain-checked, hard-fail) + exceptions

**Files:**
- Modify: `api/shared/policy_rules.py` (resolver + exceptions)
- Test: `api/tests/e2e/test_resolve_policy_refs.py`

**Interfaces:**
- Produces: `async resolve_policy_refs(policies, *, repo, action_domain, solution_id=None) -> None` (mutates in place; replaces each `PolicyRuleRef` with the resolved inline rule of the domain's type); raises `PolicyRuleNotFound`, `PolicyRuleDomainMismatch`. Requires the resolved rule's `domain == action_domain`.
- **`solution_id` (Codex R2/C1):** when set, `repo.get` resolves **own-solution → org → global**; when `None`, the existing org→global cascade. The repo's `get(name=, domain=, solution_id=None)` adds the solution arm: query `solution_id == solution_id` first, then fall through to `OrgScopedRepository`'s org→global. The named-rules plan lands the parameter + own-first arm; the solution-files plan passes a real `solution_id` from solution-policy evaluation.

- [ ] **Step 1: Write the failing tests**

```python
# api/tests/e2e/test_resolve_policy_refs.py
import pytest
from src.models.orm.policy_rule import PolicyRule
from src.models.contracts.policies import FilePolicies, FilePolicyRule, TablePolicies
from src.repositories.policy_rule import PolicyRuleRepository
from api.shared.policy_rules import resolve_policy_refs, PolicyRuleNotFound, PolicyRuleDomainMismatch

@pytest.mark.asyncio
async def test_resolves_inline(db_session, seed_org):
    db_session.add(PolicyRule(name="ab", domain="file", organization_id=seed_org,
        body={"actions": ["read","write","delete","list"], "when": {"user": "is_platform_admin"}}))
    await db_session.flush()
    repo = PolicyRuleRepository(db_session, org_id=seed_org, is_superuser=True)
    doc = FilePolicies.model_validate({"policies": [{"$ref": "ab"}, {"name":"x","actions":["read"],"when":None}]})
    await resolve_policy_refs(doc, repo=repo, action_domain="file")
    assert all(isinstance(p, FilePolicyRule) for p in doc.policies)
    assert doc.policies[0].name == "ab" and doc.policies[1].name == "x"  # order preserved

@pytest.mark.asyncio
async def test_missing_raises(db_session, seed_org):
    repo = PolicyRuleRepository(db_session, org_id=seed_org, is_superuser=True)
    doc = FilePolicies.model_validate({"policies": [{"$ref": "nope"}]})
    with pytest.raises(PolicyRuleNotFound):
        await resolve_policy_refs(doc, repo=repo, action_domain="file")

@pytest.mark.asyncio
async def test_cross_domain_raises(db_session, seed_org):
    db_session.add(PolicyRule(name="t", domain="table", organization_id=seed_org,
        body={"actions": ["create"], "when": None}))
    await db_session.flush()
    repo = PolicyRuleRepository(db_session, org_id=seed_org, is_superuser=True)
    doc = FilePolicies.model_validate({"policies": [{"$ref": "t"}]})  # table rule in a file policy
    with pytest.raises(PolicyRuleDomainMismatch):
        await resolve_policy_refs(doc, repo=repo, action_domain="file")
```

- [ ] **Step 2: Run to verify it fails**

Run: `./test.sh e2e tests/e2e/test_resolve_policy_refs.py -v`
Expected: FAIL (`ImportError`).

- [ ] **Step 3: Implement the resolver**

```python
# append to api/shared/policy_rules.py
from typing import Literal
from src.models.contracts.policies import FilePolicies, FilePolicyRule, Policy, PolicyRuleRef, TablePolicies

class PolicyRuleNotFound(Exception): ...
class PolicyRuleDomainMismatch(Exception): ...

async def resolve_policy_refs(policies: FilePolicies | TablePolicies, *, repo,
                              action_domain: Literal["file", "table"]) -> None:
    """Replace each PolicyRuleRef with the resolved inline rule. Mutates in place.

    Resolution is by (name, domain=action_domain) so a {"$ref": "admin_bypass"} in a file
    policy picks the FILE admin_bypass and in a table policy the TABLE one (correction #3).
    Raises on missing or domain-mismatched ref. Runs BEFORE evaluation/compilation/preresolve.
    """
    rule_cls = FilePolicyRule if action_domain == "file" else Policy
    resolved: list = []
    for entry in policies.policies:
        if not isinstance(entry, PolicyRuleRef):
            resolved.append(entry); continue
        row = await repo.get(name=entry.ref, domain=action_domain)
        if row is None:
            raise PolicyRuleNotFound(entry.ref)
        if row.domain != action_domain:
            raise PolicyRuleDomainMismatch(f"{entry.ref!r} is a {row.domain} rule, not {action_domain}")
        body = row.body or {}
        try:
            resolved.append(rule_cls.model_validate({
                "name": row.name, "description": row.description,
                "actions": body.get("actions"), "when": body.get("when")}))
        except Exception as exc:  # foreign when-namespace etc. → domain mismatch
            raise PolicyRuleDomainMismatch(f"rule {entry.ref!r} body invalid for {action_domain}: {exc}") from exc
    policies.policies = resolved
```

- [ ] **Step 4: Run to verify it passes**

Run: `./test.sh e2e tests/e2e/test_resolve_policy_refs.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/shared/policy_rules.py api/tests/e2e/test_resolve_policy_refs.py
git commit -m "feat(policy-rules): resolve_policy_refs (domain-checked, hard-fail)"
```

---

## Task 5: Single resolving loader (choke point) + wire ALL table eval sites

**Files:**
- Create: `api/src/services/table_policy_loader.py` (`load_resolved_table_policies`)
- Modify: `api/src/routers/tables.py` (`_load_policies` callers → resolving loader)
- Modify: `api/src/routers/websocket.py` (its `TablePolicies` load → resolving loader + cache note)
- Modify: `api/shared/claims/preresolve.py` (`_load_source_policies` resolves before compile)
- Test: `api/tests/e2e/test_table_ref_enforced.py`

**Interfaces:**
- Produces: `async load_resolved_table_policies(table, db) -> TablePolicies` — validates `table.access`, resolves refs (`action_domain="table"`), returns inlined `TablePolicies`; on unresolvable ref returns an **empty** doc (default-deny for evaluation) and logs. **This is the only function eval paths call.**

> **Order matters (correction #2):** the loader resolves refs *before* the caller hands the doc to `preresolve_for_policies` / `compile_read_filter`. A `PolicyRuleRef` reaching `preresolve` would `AttributeError`.

- [ ] **Step 1: Write the failing test (table list enforced via ref, across eval paths)**

```python
# api/tests/e2e/test_table_ref_enforced.py
import pytest
from src.models.orm.policy_rule import PolicyRule

@pytest.mark.asyncio
async def test_table_list_allowed_via_referenced_rule(admin_client, db_session, seed_org):
    db_session.add(PolicyRule(name="ab", domain="table", organization_id=seed_org,
        body={"actions": ["read","create","update","delete"], "when": {"user": "is_platform_admin"}}))
    await db_session.flush()
    # create a table whose access references the rule, then list rows as admin → allowed.
    # (use the existing table-create + document-list e2e fixtures; assert 200 + rows visible.)
```

(Grep `tests/e2e` for the table list/documents fixture; model the assertion on an existing table-policy e2e.)

- [ ] **Step 2: Run to verify it fails**

Run: `./test.sh e2e tests/e2e/test_table_ref_enforced.py -v`
Expected: FAIL — the `{"$ref"}` entry isn't resolved on the list path yet.

- [ ] **Step 3: Write the resolving loader**

```python
# api/src/services/table_policy_loader.py
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from src.models.orm.tables import Table
from src.models.contracts.policies import TablePolicies
from src.repositories.policy_rule import PolicyRuleRepository
from api.shared.policy_rules import resolve_policy_refs, PolicyRuleNotFound, PolicyRuleDomainMismatch

logger = logging.getLogger(__name__)

async def load_resolved_table_policies(table: Table, db: AsyncSession) -> TablePolicies:
    """THE table-policy load path for evaluation. Validates + inlines refs (before preresolve/compile)."""
    if table.access is None:
        return TablePolicies()
    try:
        policies = TablePolicies.model_validate(table.access)
    except Exception as exc:
        logger.warning("malformed table policies for %s; denying: %s", table.id, exc)
        return TablePolicies()
    repo = PolicyRuleRepository(db, org_id=table.organization_id, is_superuser=True)
    try:
        await resolve_policy_refs(policies, repo=repo, action_domain="table")
    except (PolicyRuleNotFound, PolicyRuleDomainMismatch) as exc:
        logger.warning("unresolvable policy ref on table %s; denying: %s", table.id, exc)
        return TablePolicies()
    return policies
```

- [ ] **Step 4: Route `tables.py::_load_policies` callers through it**

`_load_policies` is sync and used at 5 sites (`tables.py:163,1155,1288,1343,1456`). Replace the sync `_load_policies(table)` calls in the **evaluation** paths with `await load_resolved_table_policies(table, ctx.db)`. Keep the raw sync `_load_policies` ONLY for the save-validation path (Task 7 uses `resolve_policy_refs` directly there). Confirm every eval site (count, list, read-one, subscribe gate) now uses the resolving loader.

- [ ] **Step 5: Route websocket + preresolve through resolution**

`websocket.py:199`: replace the inline `TablePolicies.model_validate(row[0])` with `await load_resolved_table_policies(table, db)` (fetch the `Table` it already has, or adapt the loader to take `access`+`org_id`+`id`). Note the `_table_policy_cache`: a policy-rule **edit** must invalidate cached entries — add a cache-bust hook called from `PolicyRuleService.update` (Task 6) or document the cache TTL makes it eventually consistent (decide and record; do not leave stale-forever).

`preresolve.py:_load_source_policies` (line 204): after building the `TablePolicies`, resolve refs before the `compile_read_filter` at line 148:

```python
    repo = PolicyRuleRepository(db, org_id=source.organization_id, is_superuser=True)
    try:
        await resolve_policy_refs(source_policies, repo=repo, action_domain="table")
    except (PolicyRuleNotFound, PolicyRuleDomainMismatch):
        return TablePolicies()  # source unreadable → claim resolves to [] (fail-closed)
```

(`_load_source_policies` becomes async, or the resolution happens at its call site at line 143 which is already in an async function — pick the smaller diff and keep it consistent.)

- [ ] **Step 6: Wire file enforcement (`file_policy_service.is_allowed`)**

In `file_policy_service.py::is_allowed`, after `FilePolicies.model_validate(...)` and **before** `preresolve_for_policies(...)`:

```python
        from api.shared.policy_rules import resolve_policy_refs, PolicyRuleNotFound, PolicyRuleDomainMismatch
        from src.repositories.policy_rule import PolicyRuleRepository
        rule_repo = PolicyRuleRepository(self.db, org_id=organization_id, is_superuser=True)
        try:
            await resolve_policy_refs(policies, repo=rule_repo, action_domain="file")
        except (PolicyRuleNotFound, PolicyRuleDomainMismatch) as exc:
            logger.warning("unresolvable file policy ref %s/%s; denying: %s", organization_id, location, exc)
            return False
```

- [ ] **Step 7: Run + regression sweep**

Run: `./test.sh e2e tests/e2e/test_table_ref_enforced.py -v`
Then: `./test.sh e2e tests/e2e -k "policy or table or file or websocket or claim" -v`
Expected: PASS (existing inline policies untouched; refs now resolve on every path).

- [ ] **Step 8: Commit**

```bash
git add api/src/services/table_policy_loader.py api/src/routers/tables.py api/src/routers/websocket.py api/shared/claims/preresolve.py api/src/services/file_policy_service.py api/tests/e2e/test_table_ref_enforced.py
git commit -m "feat(policy-rules): single resolving loader; wire all table+file eval sites (ws, preresolve, claims)"
```

---

## Task 6: `PolicyRuleService` — CRUD + body validation + Core-update rename cascade + delete guard + two built-ins + audit

**Files:**
- Create: `api/src/services/policy_rule_service.py`
- Modify: `api/shared/file_policies_seed.py`
- Test: `api/tests/e2e/test_policy_rule_service.py`

**Interfaces:**
- Produces: `PolicyRuleService(db)` with `create`, `update` (Core-update rename cascade, override-aware), `delete` (guarded), `seed_builtin_admin_bypass()` (seeds BOTH domains), `usages(name, domain, org_id)`.
- Built-ins: `PolicyRule(name="admin_bypass", domain="file", is_builtin=True, body={actions:[read,write,delete,list], when:{user:is_platform_admin}})` AND the same with `domain="table", actions:[read,create,update,delete]`.

- [ ] **Step 1: Write failing tests (incl. Core-update under the guard)**

```python
# api/tests/e2e/test_policy_rule_service.py
import pytest
from sqlalchemy import select
from src.models.orm.policy_rule import PolicyRule
from src.models.orm.file_metadata import FilePolicy
from src.models.contracts.policy_rule import PolicyRuleCreate, PolicyRuleUpdate
from src.services.policy_rule_service import PolicyRuleService, PolicyRuleInUse, PolicyRuleReadOnly
from src.services.solutions.guard import install_solution_write_guard

@pytest.mark.asyncio
async def test_rename_cascades_via_core_update_under_guard(db_session, seed_org, admin_actor):
    install_solution_write_guard()  # prod-faithful: guard active
    svc = PolicyRuleService(db_session)
    await svc.create(PolicyRuleCreate(name="ops", domain="file", organization_id=seed_org,
                     body={"actions": ["read"], "when": None}), actor=admin_actor)
    db_session.add(FilePolicy(organization_id=seed_org, location="shared", path="d/",
                              policies={"policies": [{"$ref": "ops"}]}))
    await db_session.flush()
    await svc.update("ops", "file", PolicyRuleUpdate(name="operations"), org_id=seed_org, actor=admin_actor)
    fp = (await db_session.execute(select(FilePolicy))).scalar_one()
    assert fp.policies["policies"] == [{"$ref": "operations"}]

@pytest.mark.asyncio
async def test_delete_blocked_while_referenced(db_session, seed_org, admin_actor):
    svc = PolicyRuleService(db_session)
    await svc.create(PolicyRuleCreate(name="ops", domain="file", organization_id=seed_org,
                     body={"actions": ["read"], "when": None}), actor=admin_actor)
    db_session.add(FilePolicy(organization_id=seed_org, location="shared", path="d/",
                              policies={"policies": [{"$ref": "ops"}]}))
    await db_session.flush()
    with pytest.raises(PolicyRuleInUse):
        await svc.delete("ops", "file", org_id=seed_org, actor=admin_actor)

@pytest.mark.asyncio
async def test_seeds_both_domains_idempotent_and_readonly(db_session, admin_actor):
    svc = PolicyRuleService(db_session)
    await svc.seed_builtin_admin_bypass(); await svc.seed_builtin_admin_bypass()
    rows = (await db_session.execute(select(PolicyRule).where(PolicyRule.name == "admin_bypass"))).scalars().all()
    assert {r.domain for r in rows} == {"file", "table"} and len(rows) == 2
    with pytest.raises(PolicyRuleReadOnly):
        await svc.update("admin_bypass", "file", PolicyRuleUpdate(description="x"), org_id=None, actor=admin_actor)
```

- [ ] **Step 2: Run to verify it fails**

Run: `./test.sh e2e tests/e2e/test_policy_rule_service.py -v`
Expected: FAIL (`ImportError`).

- [ ] **Step 3: Implement the service (Core-update cascade)**

```python
# api/src/services/policy_rule_service.py
from __future__ import annotations
from typing import Any
from uuid import UUID
from sqlalchemy import select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession
from src.models.orm.policy_rule import PolicyRule
from src.models.orm.file_metadata import FilePolicy
from src.models.orm.tables import Table
from src.models.contracts.policy_rule import PolicyRuleCreate, PolicyRuleUpdate, _validate_body
from src.repositories.policy_rule import PolicyRuleRepository
from api.shared.policy_rules import find_policy_rule_usages
from src.services.audit import emit_audit

class PolicyRuleInUse(Exception): ...
class PolicyRuleReadOnly(Exception): ...
class PolicyRuleNotFoundError(Exception): ...

_BUILTINS = [
    {"name": "admin_bypass", "domain": "file",
     "description": "Platform admins bypass all file checks. Built-in, read-only.",
     "body": {"actions": ["read","write","delete","list"], "when": {"user": "is_platform_admin"}}},
    {"name": "admin_bypass", "domain": "table",
     "description": "Platform admins bypass all table checks. Built-in, read-only.",
     "body": {"actions": ["read","create","update","delete"], "when": {"user": "is_platform_admin"}}},
]

class PolicyRuleService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def seed_builtin_admin_bypass(self) -> None:
        for b in _BUILTINS:
            exists = (await self.db.execute(select(PolicyRule).where(
                PolicyRule.name == b["name"], PolicyRule.domain == b["domain"],
                PolicyRule.organization_id.is_(None)))).scalar_one_or_none()
            if exists is None:
                self.db.add(PolicyRule(organization_id=None, is_builtin=True, **b))
        await self.db.flush()

    async def create(self, data: PolicyRuleCreate, *, actor: Any) -> PolicyRule:
        row = PolicyRule(organization_id=data.organization_id, name=data.name,
                         domain=data.domain, description=data.description, body=data.body)
        self.db.add(row); await self.db.flush()
        await emit_audit(self.db, "policy_rule.create", resource_type="policy_rule",
                         resource_id=row.id, details={"name": row.name, "domain": row.domain}, actor_override=actor)
        return row

    async def _get(self, name: str, domain: str, org_id: UUID | None) -> PolicyRule:
        repo = PolicyRuleRepository(self.db, org_id=org_id, is_superuser=True)
        row = await repo.get(name=name, domain=domain)
        if row is None:
            raise PolicyRuleNotFoundError(name)
        return row

    async def update(self, name: str, domain: str, data: PolicyRuleUpdate, *, org_id: UUID | None, actor: Any) -> PolicyRule:
        row = await self._get(name, domain, org_id)
        if row.is_builtin:
            raise PolicyRuleReadOnly(name)
        usages = await find_policy_rule_usages(self.db, row.name, row.domain, org_id=row.organization_id)
        renamed = data.name if data.name and data.name != row.name else None
        if renamed:
            await self._cascade_rename(row.name, renamed, row.domain, row.organization_id, usages)
            row.name = renamed
        if data.description is not None:
            row.description = data.description
        if data.body is not None:
            _validate_body(data.body, row.domain); row.body = data.body
        await self.db.flush()
        await emit_audit(self.db, "policy_rule.update", resource_type="policy_rule", resource_id=row.id,
                         details={"name": row.name, "domain": row.domain, "renamed_to": renamed, "usages": usages.total},
                         actor_override=actor)
        return row

    async def delete(self, name: str, domain: str, *, org_id: UUID | None, actor: Any) -> None:
        row = await self._get(name, domain, org_id)
        if row.is_builtin:
            raise PolicyRuleReadOnly(name)
        usages = await find_policy_rule_usages(self.db, row.name, row.domain, org_id=row.organization_id)
        if usages.total > 0:
            raise PolicyRuleInUse(name)
        await self.db.delete(row); await self.db.flush()
        await emit_audit(self.db, "policy_rule.delete", resource_type="policy_rule", resource_id=row.id,
                         details={"name": row.name, "domain": row.domain}, actor_override=actor)

    async def usages(self, name: str, domain: str, *, org_id: UUID | None):
        row = await self._get(name, domain, org_id)
        return await find_policy_rule_usages(self.db, row.name, row.domain, org_id=row.organization_id)

    async def _cascade_rename(self, old: str, new: str, domain: str, org_id: UUID | None, usages) -> None:
        """Rewrite {"$ref": old}→{"$ref": new} via CORE updates (not ORM) so the solution
        read-only guard does not reject solution-managed targets (correction #8). Only the
        override-aware usage set is touched (correction #6)."""
        for fp in usages.file_policies:  # already override-filtered
            row = (await self.db.execute(select(FilePolicy).where(FilePolicy.id == fp["id"]))).scalar_one()
            await self.db.execute(sa_update(FilePolicy).where(FilePolicy.id == fp["id"])
                                  .values(policies=_rewrite_ref(row.policies, old, new)))
        for tb in usages.tables:
            row = (await self.db.execute(select(Table).where(Table.id == tb["id"]))).scalar_one()
            await self.db.execute(sa_update(Table).where(Table.id == tb["id"])
                                  .values(access=_rewrite_ref(row.access, old, new)))
        await self.db.flush()

def _rewrite_ref(doc: dict, old: str, new: str) -> dict:
    rules = [{"$ref": new} if (isinstance(r, dict) and r.get("$ref") == old) else r
             for r in (doc or {}).get("policies", [])]
    return {**(doc or {}), "policies": rules}
```

> Core `update()` writes bypass the ORM unit-of-work, so the `before_flush` guard never sees a dirty solution-managed object — matching how `deploy.py` writes. Reading the row first (`select`) does not make it dirty.

- [ ] **Step 4: Seed file prefixes with the ref**

```python
# api/shared/file_policies_seed.py
def make_seed_admin_bypass_file() -> dict:
    """New file prefixes reference the built-in admin_bypass (file domain)."""
    return {"policies": [{"$ref": "admin_bypass"}]}
```

Wire `PolicyRuleService(db).seed_builtin_admin_bypass()` into the startup seed hook (grep for the existing idempotent boot seeds, e.g. where roles/configs seed at startup) so BOTH built-ins exist before any file prefix is created. If no boot hook exists, seed lazily in `FilePolicyService.upsert_policy`'s create branch *before* inserting the seed doc. (Document which; the ref must resolve at create time or file creation hard-fails.)

- [ ] **Step 5: Run + file-create regression**

Run: `./test.sh e2e tests/e2e/test_policy_rule_service.py -v && ./test.sh e2e tests/e2e -k "file_polic" -v`
Expected: PASS — seeded prefixes resolve `{"$ref":"admin_bypass"}`.

- [ ] **Step 6: Commit**

```bash
git add api/src/services/policy_rule_service.py api/shared/file_policies_seed.py api/tests/e2e/test_policy_rule_service.py
git commit -m "feat(policy-rules): service (validated body, Core-update override-aware rename, delete guard, 2 built-ins, audit)"
```

---

## Task 7: REST router — CRUD + `/usages` + structured save-time validation (file + table)

**Files:**
- Create: `api/src/routers/policy_rules.py`
- Modify: file-policy save (`api/src/routers/files.py`) + table-policy save (`tables.py:863`) → structured ref validation
- Register router in the app
- Test: `api/tests/e2e/test_policy_rules_api.py`

**Interfaces:**
- Produces: `POST/GET/PUT/DELETE /api/policy-rules` (admin-gated), `GET /api/policy-rules/{domain}/{name}/usages`. Save failures return `PolicyValidationResponse` (structured, with `path`).

- [ ] **Step 1: Write the failing API test**

```python
# api/tests/e2e/test_policy_rules_api.py
import pytest

@pytest.mark.asyncio
async def test_crud_and_usages(admin_client):
    r = await admin_client.post("/api/policy-rules", json={
        "name": "ops", "domain": "file", "body": {"actions": ["read"], "when": None}})
    assert r.status_code == 201
    u = await admin_client.get("/api/policy-rules/file/ops/usages")
    assert u.status_code == 200 and u.json()["total"] == 0
    assert (await admin_client.delete("/api/policy-rules/file/ops")).status_code == 204

@pytest.mark.asyncio
async def test_file_policy_missing_ref_is_structured_422(admin_client):
    r = await admin_client.put("/api/files/policies/docs%2F", params={"location": "shared"},
        json={"policies": [{"$ref": "nope"}]})
    assert r.status_code == 422
    body = r.json()
    assert "errors" in body or "detail" in body  # structured PolicyValidationResponse shape

@pytest.mark.asyncio
async def test_non_admin_cannot_create(user_client):
    r = await user_client.post("/api/policy-rules", json={"name": "x", "domain": "file", "body": {"actions": ["read"], "when": None}})
    assert r.status_code in (401, 403)
```

- [ ] **Step 2: Run to verify it fails**

Run: `./test.sh e2e tests/e2e/test_policy_rules_api.py -v`
Expected: FAIL (404 — not mounted).

- [ ] **Step 3: Router** (thin; mirror `config.py` deps + admin gate)

Build `POST/GET/PUT/DELETE /api/policy-rules` and `GET /api/policy-rules/{domain}/{name}/usages` calling `PolicyRuleService`. Map `PolicyRuleReadOnly`→409, `PolicyRuleNotFoundError`→404, `PolicyRuleInUse`→409 with the usages payload. Use the same `Context`/admin dependency `config.py` uses (the bypass gate). Register in `main.py` next to the other `include_router` calls.

- [ ] **Step 4: Structured save-time validation (both domains)**

> **VERIFIED shapes (don't guess):** `PolicyValidationResponse` is `{ok: bool, errors: list[PolicyValidationError(path, message)]}` (`policies.py:331-353`). The table **validate** endpoint deliberately returns **HTTP 200 with `ok=false`** on failure (its docstring: "Endpoint always returns 200 — callers parse this body"), NOT a 422. So ref-validation must surface the SAME way: `ok=false` + an error entry, not a raised 422. (The *file-policy `set`* path may differ — match whatever that handler already does for a malformed doc; if it raises 422, ref failures raise 422 there too. Read each handler and mirror its existing failure mode rather than imposing one.)

For the table validate path (`tables.py:863`), extend the existing try/except so an unresolvable ref becomes an `ok=false` error rather than escaping:

```python
    try:
        parsed = TablePolicies.model_validate(body)
        # NEW: resolve refs as part of validation (same 200/ok=false contract).
        from api.shared.policy_rules import resolve_policy_refs, PolicyRuleNotFound, PolicyRuleDomainMismatch
        from src.repositories.policy_rule import PolicyRuleRepository
        repo = PolicyRuleRepository(ctx.db, org_id=<target_org>, is_superuser=True)
        await resolve_policy_refs(parsed.model_copy(deep=True), repo=repo, action_domain="table")
        return PolicyValidationResponse(ok=True)
    except (PolicyRuleNotFound, PolicyRuleDomainMismatch) as exc:
        return PolicyValidationResponse(ok=False, errors=[PolicyValidationError(path="$.policies", message=str(exc))])
    except ValidationError as e:
        ...  # existing AST-error handling unchanged
```

For the file-policy `set` handler, add the same `resolve_policy_refs(..., action_domain="file")` validation against a deep copy and convert a raise into that handler's existing failure shape (read it first; do not assume 422 vs 200).

- [ ] **Step 5: Run + regen types + commit**

Run: `./test.sh e2e tests/e2e/test_policy_rules_api.py -v && cd client && npm run generate:types`

```bash
git add api/src/routers/policy_rules.py api/src/routers/files.py api/src/routers/tables.py api/src/main.py api/tests/e2e/test_policy_rules_api.py client/src/lib/v1.d.ts
git commit -m "feat(policy-rules): REST CRUD + /usages + structured save-time ref validation"
```

---

## Task 8: Solution deploy + manifest import ref validation

**Files:**
- Modify: `api/src/services/solutions/deploy.py` (table-policy validation at :875 resolves refs)
- Modify: `api/src/services/manifest_import.py` (table import :2084 + file import :2172 validate refs; rule-before-policy ordering)
- Test: `api/tests/e2e/platform/test_git_sync_local.py`, `api/tests/e2e/test_solution_deploy_policy_ref.py`

**Interfaces:** consumes `resolve_policy_refs`. Ensures a bundle/manifest with a `{"$ref"}` policy fails closed if the rule is absent, and that rules import before the policies that reference them.

- [ ] **Step 1: Failing tests**

```python
# api/tests/e2e/test_solution_deploy_policy_ref.py
# Deploy a bundle whose table access references {"$ref":"x"} with NO such rule → deploy fails (422/error),
# does NOT silently install an unresolvable policy.
# Then deploy a bundle that DOES carry the rule → succeeds and the table lists rows under the rule.
```

- [ ] **Step 2: Run to verify it fails** — `./test.sh e2e tests/e2e/test_solution_deploy_policy_ref.py -v` → FAIL.

- [ ] **Step 3:** In `deploy.py` (~:875) `TablePolicies.model_validate(access)` is **already followed by** `_validate_table_policy_claim_refs` (an existing claim-ref validator) — add the `resolve_policy_refs(..., action_domain="table")` call **right alongside it** (same try-block, scoped to the install's org via `PolicyRuleRepository`); a raise aborts that entity's deploy with a clear error. In `manifest_import.py` apply the same to table (:2084) and file (:2172) policy writes. Ensure `_resolve_policy_rule` (Task 10) runs **before** policy/table resolution in the import ordering. The `domain` for the rule lookup is `"table"` for `Table.access` / `"file"` for `FilePolicy.policies`.

- [ ] **Step 4: Run + commit** — `./test.sh e2e tests/e2e/test_solution_deploy_policy_ref.py tests/e2e/platform/test_git_sync_local.py -v`

```bash
git add api/src/services/solutions/deploy.py api/src/services/manifest_import.py api/tests/e2e/test_solution_deploy_policy_ref.py
git commit -m "feat(policy-rules): fail-closed ref validation in solution deploy + manifest import"
```

---

## Task 9: CLI — `policy-rule` group + `tables policies {get,set}`

**Files:** Create `api/bifrost/commands/policy_rules.py`; modify `tables.py`, `commands/__init__.py`; test `api/tests/e2e/test_cli_policy_rules.py`.

(Same as the prior draft — mirror `configs.py`; `policy-rule create/list/get/update/delete/usages` with a `--domain` flag; `tables policies get/set` mirroring `files policies`. `files policies set/get` already round-trip refs unchanged.)

- [ ] **Step 1–5:** Write failing CLI e2e (create rule with `--domain file`; `tables policies set` round-trips a `$ref`), implement the group + subgroup, register, run.
- [ ] **Step 6: DTO parity + contract tripwire + skill-truth**

Run: `./test.sh tests/unit/test_dto_flags.py tests/unit/test_contract_version.py -v`
If the fingerprint test fails: additive change → refresh `EXPECTED_CONTRACT_FINGERPRINT` only (no `CONTRACT_VERSION` bump). Then `python api/scripts/skill-truth/generate.py`.

- [ ] **Step 7: Commit**

```bash
git add api/bifrost/commands/policy_rules.py api/bifrost/commands/tables.py api/bifrost/commands/__init__.py api/tests/e2e/test_cli_policy_rules.py api/.../generated
git commit -m "feat(policy-rules): CLI policy-rule group (--domain) + tables policies get/set"
```

---

## Task 10: MCP thin wrapper + Manifest round-trip (rules + table inline-or-ref union)

**Files:**
- Create: `api/src/services/mcp_server/tools/policy_rules.py` (+ register)
- Modify: `api/bifrost/manifest.py` (`ManifestPolicyRule`; **widen table `ManifestPolicy` to inline-or-ref**), `manifest_generator.py` (serialize rules; preserve `$ref`), `manifest_import.py` (`_resolve_policy_rule` before policies)
- Test: `api/tests/unit/test_manifest.py`, `api/tests/unit/test_mcp_thin_wrapper.py`, `api/tests/e2e/platform/test_git_sync_local.py`

> **Correction #11:** table policies serialize through `ManifestPolicy` (`manifest.py:889`) which requires inline `name/actions/when`. A `{"$ref"}` entry won't round-trip until that model becomes an inline-or-ref union (file policies are loose dicts and already pass refs through).

- [ ] **Step 1: Failing tests** — `ManifestPolicyRule` round-trip; a table manifest policy list containing `{"$ref":"ops"}` round-trips (currently rejected); MCP thin-wrapper passes.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3:** Add `ManifestPolicyRule` (mirror `ManifestConfig` `classify`/`from_row`/`to_orm_values`, including `domain`). Widen the table `ManifestPolicy` rule entries to `inline | {"$ref"}`. MCP tool mirrors `tools/configs.py` (thin `call_rest` bridge, no ORM): `list/create/delete_policy_rule`.
- [ ] **Step 4:** Serialize rules in `manifest_generator.py` (exclude `is_builtin` rows from export — built-ins are seeded, not shipped). Import `_resolve_policy_rule` upsert by `(organization_id, name, domain)`, ordered **before** policy/table resolution.
- [ ] **Step 5: Run + commit** — `./test.sh tests/unit/test_manifest.py tests/unit/test_mcp_thin_wrapper.py -v && ./test.sh e2e tests/e2e/platform/test_git_sync_local.py -v`

```bash
git add api/src/services/mcp_server/tools/policy_rules.py api/bifrost/manifest.py api/src/services/manifest_generator.py api/src/services/manifest_import.py api/tests/unit/test_manifest.py
git commit -m "feat(policy-rules): MCP tool + manifest round-trip (rules + table inline-or-ref policy union)"
```

---

## Task 11: Frontend — reference mode in Files + Tables policy editors

**Files:** Create `client/src/services/policyRules.ts` (+ test); modify Files `PolicyEditorModal` + Tables policy editor; test vitest + Playwright.

(Same as the prior draft. Service `listPolicyRules()` / `policyRuleUsages(domain,name)`; "Insert reference…" option in both editors sourced from `/api/policy-rules` filtered by `domain`, inserting `{"$ref": name}`; surface the structured `422` inline.)

- [ ] **Steps:** failing service test → service → editor wiring → vitest + Playwright (insert ref in Files editor; insert ref in Tables editor) → tsc/lint → commit.

```bash
git commit -m "feat(policy-rules): reference mode in Files + Tables policy editors"
```

---

## Task 12: Frontend — in-context policy-rules manager (list/edit/where-used)

**Files:** Create `client/src/components/policy-rules/PolicyRulesManager.tsx` (+ test); wire "Manage rules…" into both editors.

(Same as the prior draft. List/create/edit/delete; blast-radius from `/usages` before save; 409 delete shows usages; built-in `admin_bypass` (both domains) read-only.)

- [ ] **Steps:** failing component test → manager → wire into editors → vitest + Playwright → tsc/lint → commit.

```bash
git commit -m "feat(policy-rules): in-context policy-rules manager with blast-radius"
```

---

## Task 13: Full verification sweep

- [ ] **Backend:** `cd api && pyright && ruff check .` → 0 errors.
- [ ] **Types + frontend:** `cd client && npm run generate:types && npm run tsc && npm run lint` → PASS.
- [ ] **Full backend suite:** `./test.sh all` → green (parse `/tmp/bifrost-<project>/test-results.xml`). Confirm websocket, claims, deploy, manifest, and file/table policy suites all pass with refs in play.
- [ ] **Client:** `./test.sh client unit && ./test.sh client e2e files-explorer.admin.spec.ts` → PASS.
- [ ] **Lint smell check (choke point):** grep for any `TablePolicies.model_validate` / `FilePolicies.model_validate` used in an evaluation path that does NOT go through the resolving loader — there should be none outside the loader + the explicit save/import/deploy validators.

```bash
git add -A && git commit -m "chore(policy-rules): verification fixups"
```

---

## Notes for the implementer

- **Import root:** match neighboring imports (`from shared.…` vs `from api.shared.…`) per the file you edit; `file_policy_service.py` uses `from shared.claims.preresolve import …`.
- **The choke point is the whole point.** If you find yourself adding `resolve_policy_refs` at a new eval call site by hand, prefer routing that site through `load_resolved_table_policies` (table) or the `is_allowed` resolution (file). New eval paths must go through the loader.
- **Order:** refs resolve BEFORE `preresolve_for_policies` and BEFORE `compile_read_filter`/`evaluate_*`. A ref reaching preresolve is an `AttributeError`.
- **Core writes** for the rename cascade (and any solution-managed policy mutation). Read the row with `select`, write with Core `update()`. Install the guard in cascade tests.
- **Two built-ins, seeded before first use.** Confirm the seed hook runs at boot (or lazily before the first file-prefix create) so `{"$ref":"admin_bypass"}` always resolves; otherwise file creation hard-fails.
- **JSONB dirty tracking:** always reassign the whole dict (`_rewrite_ref` returns fresh) — though the cascade uses Core `update().values(...)` which sidesteps ORM tracking entirely.
- **Match `PolicyValidationResponse` exactly** (`policies.py:343`) — read it before constructing one.
