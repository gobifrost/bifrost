"""Override-aware where-used for named policy rules + ref resolver."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from sqlalchemy import false, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.policies import FilePolicies, FilePolicyRule, Policy, PolicyRuleRef, TablePolicies
from src.models.orm.file_metadata import FilePolicy
from src.models.orm.policy_rule import PolicyRule
from src.models.orm.tables import Table

if TYPE_CHECKING:
    from src.repositories.policy_rule import PolicyRuleRepository


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
        tb = tb.where(false())  # a file rule can only be referenced by file policies
    if domain == "table":
        fp = fp.where(false())
    if org_id is not None:
        fp = fp.where(FilePolicy.organization_id == org_id)
        tb = tb.where(Table.organization_id == org_id)
        override_orgs: set[UUID] = set()
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


# ---------------------------------------------------------------------------
# Named-rule ref resolver
# ---------------------------------------------------------------------------


class PolicyRuleNotFound(Exception):
    """Raised when a {"$ref": name} rule cannot be found in any scope tier."""


class PolicyRuleDomainMismatch(Exception):
    """Raised when a resolved rule's domain differs from the policy's action_domain."""


async def resolve_policy_refs(
    policies: FilePolicies | TablePolicies,
    *,
    repo: PolicyRuleRepository,
    action_domain: Literal["file", "table"],
    solution_id: UUID | None = None,
) -> None:
    """Replace each PolicyRuleRef with the resolved inline rule. Mutates in place.

    Resolution is by (name, domain=action_domain): a {"$ref": "admin_bypass"} in a
    file policy picks the FILE admin_bypass and in a table policy the TABLE one.

    When solution_id is set, resolution is own-solution → org → global (Codex R2/C1).
    When solution_id is None, the inherited org→global cascade applies.

    Raises:
        PolicyRuleNotFound: ref name not found in any scope tier.
        PolicyRuleDomainMismatch: resolved rule domain != action_domain, or rule body
            is invalid for the action_domain's rule class.
    """
    rule_cls = FilePolicyRule if action_domain == "file" else Policy
    resolved: list = []
    for entry in policies.policies:
        if not isinstance(entry, PolicyRuleRef):
            resolved.append(entry)
            continue
        row = await repo.get_for_ref(name=entry.ref, domain=action_domain, solution_id=solution_id)
        if row is None:
            raise PolicyRuleNotFound(entry.ref)
        # Defense-in-depth: load-bearing check.  get_for_ref's step-3 can surface a
        # cross-domain row (a ref that exists but in the wrong domain); reject it here
        # so a misconfigured cross-domain ref can never silently apply wrong-domain rules.
        if row.domain != action_domain:
            raise PolicyRuleDomainMismatch(
                f"{entry.ref!r} is a {row.domain} rule, not {action_domain}"
            )
        body = row.body or {}
        try:
            resolved.append(rule_cls.model_validate({
                "name": row.name,
                "description": row.description,
                "actions": body.get("actions"),
                "when": body.get("when"),
            }))
        except Exception as exc:  # body invalid for this domain (e.g. FileAction vs Action)
            raise PolicyRuleDomainMismatch(
                f"rule {entry.ref!r} body invalid for {action_domain}: {exc}"
            ) from exc
    policies.policies = resolved
