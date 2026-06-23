"""Override-aware where-used for named policy rules."""
from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.file_metadata import FilePolicy
from src.models.orm.policy_rule import PolicyRule
from src.models.orm.tables import Table


@dataclass
class PolicyRuleUsages:
    file_policies: list[dict] = field(default_factory=list)
    tables: list[dict] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.file_policies) + len(self.tables)


async def find_policy_rule_usages(
    db: AsyncSession, name: str, domain: str, *, org_id: UUID | None
) -> PolicyRuleUsages:
    """Find file/table policies that reference {"$ref": name} and resolve to THIS rule.

    org-scoped rule (org_id set): exact scan within that org.
    global rule (org_id None): scan all orgs, BUT exclude any org that defines its own
      (name, domain) override — there a {"$ref": name} resolves to the override, not this
      global.
    """
    ref_json = [{"$ref": name}]
    fp = select(FilePolicy.id, FilePolicy.organization_id, FilePolicy.location, FilePolicy.path).where(
        FilePolicy.policies["policies"].contains(ref_json)
    )
    tb = select(Table.id, Table.organization_id, Table.name).where(
        Table.access["policies"].contains(ref_json)
    )
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
            o
            for (o,) in (
                await db.execute(
                    select(PolicyRule.organization_id).where(
                        PolicyRule.name == name,
                        PolicyRule.domain == domain,
                        PolicyRule.organization_id.isnot(None),
                    )
                )
            ).all()
        }
    usages = PolicyRuleUsages()
    for r in (await db.execute(fp)).all():
        if r.organization_id in override_orgs:  # global rule shadowed here
            continue
        usages.file_policies.append(
            {
                "id": str(r.id),
                "location": r.location,
                "path": r.path,
                "organization_id": str(r.organization_id) if r.organization_id else None,
            }
        )
    for r in (await db.execute(tb)).all():
        if r.organization_id in override_orgs:
            continue
        usages.tables.append(
            {
                "id": str(r.id),
                "name": r.name,
                "organization_id": str(r.organization_id) if r.organization_id else None,
            }
        )
    return usages
