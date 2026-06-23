"""PolicyRuleService — CRUD, body validation, Core-update rename cascade, delete guard, built-ins."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.policy_rules import PolicyRuleUsages, find_policy_rule_usages
from src.models.contracts.policy_rule import PolicyRuleCreate, PolicyRuleUpdate, _validate_body
from src.models.orm.file_metadata import FilePolicy
from src.models.orm.policy_rule import PolicyRule
from src.models.orm.tables import Table
from src.repositories.policy_rule import PolicyRuleRepository
from src.services.audit import emit_audit
from src.services.solutions.guard import assert_not_solution_managed


class PolicyRuleInUse(Exception):
    """Raised when a deletion is attempted on a rule that has active references."""

    def __init__(self, name: str, usages: "PolicyRuleUsages") -> None:
        super().__init__(name)
        self.usages = usages


class PolicyRuleReadOnly(Exception):
    """Raised when a mutation is attempted on a built-in rule."""


class PolicyRuleNotFoundError(Exception):
    """Raised when a named policy rule cannot be resolved."""


_BUILTINS = [
    {
        "name": "admin_bypass",
        "domain": "file",
        "description": "Platform admins bypass all file checks. Built-in, read-only.",
        "body": {
            "actions": ["read", "write", "delete", "list"],
            "when": {"user": "is_platform_admin"},
        },
    },
    {
        "name": "admin_bypass",
        "domain": "table",
        "description": "Platform admins bypass all table checks. Built-in, read-only.",
        "body": {
            "actions": ["read", "create", "update", "delete"],
            "when": {"user": "is_platform_admin"},
        },
    },
]


class PolicyRuleService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def seed_builtin_admin_bypass(self) -> None:
        """Idempotently insert both admin_bypass built-ins (file + table domains)."""
        for b in _BUILTINS:
            exists = (
                await self.db.execute(
                    select(PolicyRule).where(
                        PolicyRule.name == b["name"],
                        PolicyRule.domain == b["domain"],
                        PolicyRule.organization_id.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if exists is None:
                self.db.add(PolicyRule(organization_id=None, is_builtin=True, **b))
        await self.db.flush()

    async def create(self, data: PolicyRuleCreate, *, actor: Any) -> PolicyRule:
        row = PolicyRule(
            organization_id=data.organization_id,
            name=data.name,
            domain=data.domain,
            description=data.description,
            body=data.body,
        )
        self.db.add(row)
        await self.db.flush()
        await emit_audit(
            self.db,
            "policy_rule.create",
            resource_type="policy_rule",
            resource_id=row.id,
            details={"name": row.name, "domain": row.domain},
            actor_override=actor,
        )
        return row

    async def _get(self, name: str, domain: str, org_id: UUID | None) -> PolicyRule:
        repo = PolicyRuleRepository(self.db, org_id=org_id, is_superuser=True)
        row = await repo.get(name=name, domain=domain)
        if row is None:
            raise PolicyRuleNotFoundError(name)
        return row

    async def update(
        self,
        name: str,
        domain: str,
        data: PolicyRuleUpdate,
        *,
        org_id: UUID | None,
        actor: Any,
    ) -> PolicyRule:
        row = await self._get(name, domain, org_id)
        assert_not_solution_managed(row)
        if row.is_builtin:
            raise PolicyRuleReadOnly(name)
        usages = await find_policy_rule_usages(
            self.db, row.name, row.domain, org_id=row.organization_id
        )
        renamed: str | None = None
        if data.name and data.name != row.name:
            renamed = data.name
            await self._cascade_rename(row.name, renamed, row.domain, row.organization_id, usages)
            row.name = renamed
        if data.description is not None:
            row.description = data.description
        if data.body is not None:
            _validate_body(data.body, row.domain)
            row.body = data.body
        await self.db.flush()
        await emit_audit(
            self.db,
            "policy_rule.update",
            resource_type="policy_rule",
            resource_id=row.id,
            details={"name": row.name, "domain": row.domain, "renamed_to": renamed, "usages": usages.total},
            actor_override=actor,
        )
        return row

    async def delete(self, name: str, domain: str, *, org_id: UUID | None, actor: Any) -> None:
        row = await self._get(name, domain, org_id)
        assert_not_solution_managed(row)
        if row.is_builtin:
            raise PolicyRuleReadOnly(name)
        usages = await find_policy_rule_usages(
            self.db, row.name, row.domain, org_id=row.organization_id
        )
        if usages.total > 0:
            raise PolicyRuleInUse(name, usages)
        await self.db.delete(row)
        await self.db.flush()
        await emit_audit(
            self.db,
            "policy_rule.delete",
            resource_type="policy_rule",
            resource_id=row.id,
            details={"name": row.name, "domain": row.domain},
            actor_override=actor,
        )

    async def usages(self, name: str, domain: str, *, org_id: UUID | None) -> PolicyRuleUsages:
        row = await self._get(name, domain, org_id)
        return await find_policy_rule_usages(
            self.db, row.name, row.domain, org_id=row.organization_id
        )

    async def _cascade_rename(
        self,
        old: str,
        new: str,
        domain: str,
        org_id: UUID | None,
        usages: PolicyRuleUsages,
    ) -> None:
        """Rewrite {"$ref": old} → {"$ref": new} via Core UPDATE statements.

        Core writes bypass the ORM unit-of-work so the solution read-only
        before_flush guard never sees a dirty solution-managed object — matching
        how deploy.py writes. Reading the row first (select) does not make it dirty.
        """
        for fp in usages.file_policies:
            row = (
                await self.db.execute(
                    select(FilePolicy).where(FilePolicy.id == fp["id"])
                )
            ).scalar_one()
            await self.db.execute(
                sa_update(FilePolicy)
                .where(FilePolicy.id == fp["id"])
                .values(policies=_rewrite_ref(row.policies, old, new))
            )
        for tb in usages.tables:
            row = (
                await self.db.execute(select(Table).where(Table.id == tb["id"]))
            ).scalar_one()
            await self.db.execute(
                sa_update(Table)
                .where(Table.id == tb["id"])
                .values(access=_rewrite_ref(row.access, old, new))
            )
        await self.db.flush()


def _rewrite_ref(doc: dict, old: str, new: str) -> dict:
    """Return a copy of doc with every {"$ref": old} replaced by {"$ref": new}."""
    rules = [
        {"$ref": new} if (isinstance(r, dict) and r.get("$ref") == old) else r
        for r in (doc or {}).get("policies", [])
    ]
    return {**(doc or {}), "policies": rules}
