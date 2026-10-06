"""What a workflow's identity lacks, from the access checks its runs recorded.

A run's access checks are written to the audit log (``access.check``, see
``src.services.access_check_writer``) with the workflow they came from. Read
back for the identity a workflow runs as (or is about to), they say what the
workflow does that the identity could not:

- Reach: organizations the workflow switches into or starts child runs in,
  outside the identity's reach (``explain.in_reach``).
- Policy roles: roles a table or file policy looked for that the identity
  does not hold now. Claims are not requirements: they resolve from the
  person's own data, and no role assignment supplies them.
- Workflow roles: whether the identity may open the workflow itself, which
  matters when it starts it through the API (``run_user_may_open``, the same
  rule the workflow's own access check applies).

A role named by a policy is a requirement only as far as the recorded check
saw it: a check lists what its run user was missing, so a role that run user
held does not appear. Nothing is listed until the workflow has recorded a
check. Nothing here decides access; it reads what was recorded and what the
identity holds now.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.access_checks import ALL_ORGS, NoteTarget
from shared.role_cache import get_user_roles
from src.models.contracts.role_assignments import RoleBoundaryInput
from src.models.contracts.workflow_requirements import (
    RequirementGrant,
    WorkflowRequirement,
    WorkflowRequirements,
)
from src.models.orm.audit import AuditLog
from src.models.orm.organizations import Organization
from src.models.orm.users import Role
from src.models.orm.workflow_roles import WorkflowRole
from src.models.orm.workflows import Workflow
from src.repositories.workflows import WorkflowRepository
from src.services.access_check_entry import run_user_may_open
from src.services.audit_retention.settings import AuditRetentionSettingsService
from src.services.authorization.explain import RunUser, in_reach, load_run_user

_ACTS_IN = {"scope_switch": "switches into", "child_run": "starts child runs in"}
_ROLE_PREFIX = "role:"


@dataclass(frozen=True)
class CheckedAction:
    """One recorded access check: its kind (``scope_switch``, ``child_run``,
    ``policy``, ...), the organization it acted in, and, for a policy, what
    its run user was missing (``role:<name or id>`` / ``claim:<name>``)."""

    kind: str
    target: NoteTarget
    missing: tuple[str, ...]


@dataclass(frozen=True)
class RoleRef:
    id: UUID
    name: str
    builtin: bool


@dataclass(frozen=True)
class Holder:
    """The identity the requirements are for, and the roles it holds now:
    its role memberships, by name and by id, as a policy's ``has_role`` sees
    them."""

    run_user: RunUser
    held_roles: frozenset[str]


def _place(holder: Holder, target: NoteTarget) -> RoleBoundaryInput:
    """Where a role a policy asked for at ``target`` is placed: there, or for
    Global at the identity's home."""
    organization_id = target if isinstance(target, UUID) else holder.run_user.home
    if organization_id is None:
        return RoleBoundaryInput(kind="platform")
    return RoleBoundaryInput(kind="organization", organization_id=organization_id)


def _reach_requirements(
    actions: Sequence[CheckedAction], holder: Holder, organization_names: Mapping[UUID, str]
) -> list[WorkflowRequirement]:
    acts: dict[NoteTarget, set[str]] = {}
    for action in actions:
        if action.kind in _ACTS_IN and not in_reach(holder.run_user, action.target)[0]:
            acts.setdefault(action.target, set()).add(action.kind)
    items = []
    for target, kinds in acts.items():
        if target == ALL_ORGS:
            label, detail = "All Organizations", "The workflow reads across every organization, which only a Platform Admin reaches"
        else:
            assert isinstance(target, UUID)
            label = organization_names.get(target, str(target))
            verbs = " and ".join(_ACTS_IN[kind] for kind in sorted(kinds))
            detail = f"The workflow {verbs} this organization, outside the identity's reach"
        items.append(WorkflowRequirement(kind="reach", label=label, detail=detail, grant=None))
    return sorted(items, key=lambda item: item.label)


def _policy_requirements(
    actions: Sequence[CheckedAction], holder: Holder, roles_by_ref: Mapping[str, RoleRef]
) -> list[WorkflowRequirement]:
    held = holder.held_roles
    seen: set[tuple[str, str]] = set()
    items = []
    for action in actions:
        if action.kind != "policy":
            continue
        for entry in action.missing:
            if not entry.startswith(_ROLE_PREFIX) or entry[len(_ROLE_PREFIX):] in held:
                continue
            ref = entry[len(_ROLE_PREFIX):]
            role = roles_by_ref.get(ref)
            place = _place(holder, action.target)
            key = (str(role.id) if role else ref, f"{place.kind}:{place.organization_id}")
            if key in seen:
                continue
            seen.add(key)
            items.append(
                WorkflowRequirement(
                    kind="policy_role",
                    label=role.name if role else ref,
                    detail="A table or file policy the workflow uses checks for this role",
                    grant=(
                        RequirementGrant(role_id=role.id, boundaries=[place])
                        if role is not None and not role.builtin
                        else None
                    ),
                )
            )
    return sorted(items, key=lambda item: item.label)


def _workflow_role_requirement(
    workflow_roles: Mapping[UUID, str], may_open_workflow: bool
) -> list[WorkflowRequirement]:
    if not workflow_roles or may_open_workflow:
        return []
    return [
        WorkflowRequirement(
            kind="workflow_role",
            label=", ".join(sorted(workflow_roles.values())),
            detail="Needed when the identity starts this workflow through the API: it must hold one of these roles",
            grant=None,
        )
    ]


def build_requirements(
    actions: Sequence[CheckedAction],
    *,
    holder: Holder,
    roles_by_ref: Mapping[str, RoleRef],
    organization_names: Mapping[UUID, str],
    workflow_roles: Mapping[UUID, str],
    may_open_workflow: bool,
) -> list[WorkflowRequirement]:
    """The requirements ``actions`` leave unmet by ``holder``: reach, then
    policy roles, then workflow roles. ``roles_by_ref`` finds a role by name
    or id; ``workflow_roles`` is the role-based workflow's roles (empty
    otherwise) and ``may_open_workflow`` whether the identity may open it.
    No recorded actions, no requirements."""
    if not actions:
        return []
    return [
        *_reach_requirements(actions, holder, organization_names),
        *_policy_requirements(actions, holder, roles_by_ref),
        *_workflow_role_requirement(workflow_roles, may_open_workflow),
    ]


def _target(raw: str | None) -> NoteTarget:
    if raw is None:
        return None
    return ALL_ORGS if raw == ALL_ORGS else UUID(raw)


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


async def _recorded_actions(db: AsyncSession, workflow_id: UUID, since: datetime) -> tuple[list[CheckedAction], int]:
    """The workflow's distinct recorded checks since ``since``, and how many
    runs recorded them."""
    recorded = (
        AuditLog.action == "access.check",
        AuditLog.details["workflow_id"].astext == str(workflow_id),
        AuditLog.created_at >= since,
    )
    inputs = AuditLog.details["inputs"]
    rows = (
        await db.execute(
            select(AuditLog.resource_type, inputs["target"].astext, inputs["missing"]).where(*recorded).distinct()
        )
    ).all()
    runs = await db.scalar(select(func.count(distinct(AuditLog.execution_id))).where(*recorded)) or 0
    actions = [
        CheckedAction(kind=kind or "", target=_target(target), missing=tuple(missing or ()))
        for kind, target, missing in rows
    ]
    return actions, runs


async def workflow_requirements(db: AsyncSession, workflow: Workflow, identity_id: UUID) -> WorkflowRequirements:
    """``workflow``'s requirements for the identity ``identity_id`` (which
    must exist), from the checks recorded in the audit log's hot window."""
    window_days = (await AuditRetentionSettingsService(db).get_settings()).hot_days
    actions, runs = await _recorded_actions(db, workflow.id, datetime.now(timezone.utc) - timedelta(days=window_days))
    run_user = await load_run_user(db, identity_id)
    assert run_user is not None
    # The same membership the policy observer and workflow access checks read.
    held_ids, held_names = await get_user_roles(identity_id, db)
    referenced = {entry[len(_ROLE_PREFIX):] for action in actions for entry in action.missing if entry.startswith(_ROLE_PREFIX)}
    referenced_ids = {UUID(ref) for ref in referenced if _is_uuid(ref)}
    roles = (
        await db.execute(
            select(Role.id, Role.name, Role.is_builtin)
            .where(Role.id.in_(referenced_ids) | Role.name.in_(referenced))
            .order_by(Role.name, Role.id)
        )
    ).all()
    roles_by_ref: dict[str, RoleRef] = {}
    for role_id, name, builtin in roles:
        ref = RoleRef(id=role_id, name=name, builtin=builtin)
        roles_by_ref.setdefault(name, ref)
        roles_by_ref[str(role_id)] = ref

    # A role-based workflow of another organization is out of the identity's
    # scope however many roles it holds (``run_identity_allowed`` pairs
    # scopes, e.g. a global identity with a provider-organization workflow),
    # so its roles are no requirement.
    workflow_roles = (
        dict(
            (
                await db.execute(
                    select(Role.id, Role.name)
                    .join(WorkflowRole, WorkflowRole.role_id == Role.id)
                    .where(WorkflowRole.workflow_id == workflow.id)
                )
            ).all()
        )
        if workflow.access_level == "role_based" and workflow.organization_id in (None, run_user.home)
        else {}
    )
    target_ids = {action.target for action in actions if isinstance(action.target, UUID)}
    organization_names = dict(
        (await db.execute(select(Organization.id, Organization.name).where(Organization.id.in_(target_ids)))).all()
    )
    items = build_requirements(
        actions,
        holder=Holder(run_user=run_user, held_roles=frozenset(held_names) | {str(role_id) for role_id in held_ids}),
        roles_by_ref=roles_by_ref,
        organization_names=organization_names,
        workflow_roles=workflow_roles,
        may_open_workflow=await run_user_may_open(db, WorkflowRepository, identity_id, workflow.id),
    )
    return WorkflowRequirements(identity_id=identity_id, observed_runs=runs, window_days=window_days, items=items)
