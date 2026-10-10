"""What the managed-identity model would decide, step by step (report-only).

Every run has a user: the person who started it, or the identity it runs as.
The model allows an action when the target is in that user's reach and the
power is held: the user's own roles plus what the workflow adds (Full: every
permission, secrets included; Restricted: the workflow's grants). A person
acting directly is judged on their own roles, with no workflow powers.

Reach is the user's home organization, every organization a role of theirs
is placed on, and Global, which is in everyone's reach. A Platform Admin
reaches every organization (the evaluator's rule 1). The global identity has
no home, so it reaches Global only.

Each check returns a ``Trace``: the outcome and the steps that led to it.
Phase 1 records traces (``enforced`` is False) and today's code still decides
every request, except a person's ``run_as``: ``check_run_as`` decides that
request too, and its row is written as enforced. The decision itself comes
from the evaluator (``decide``); nothing here re-implements a rule.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.access_checks import ALL_ORGS, NoteTarget
from shared.system_account_guard import is_system_account
from src.core.constants import PROVIDER_ORG_ID
from src.models.contracts.access_list import AccessClass, AccessEntry, CurrentGate
from src.models.contracts.permissions import permission_display_name
from src.models.contracts.workflow_permissions import WorkflowGrant, WorkflowPermissionMode
from src.models.orm.organizations import Organization
from src.models.orm.users import User
from src.models.orm.workflows import Workflow
from src.services.authorization.context import (
    AuthorizationContext,
    Boundary,
    BoundaryKind,
    RoleGrant,
    build_authorization_context,
)
from src.services.authorization.enforce import privileged_user_ids
from src.services.authorization.evaluator import HOME, GLOBAL, Target, cross_org, decide
from src.services.workflow_permissions import (
    get_default_workflow_permission_mode,
    effective_workflow_permission_mode,
    list_workflow_grants,
)

StepStatus = Literal["passed", "stopped", "not_applicable", "not_reached"]
Outcome = Literal["success", "failure"]

TargetOrg = NoteTarget

_SECRETS_PERMISSION = "secrets.read"
_IMPERSONATE_PERMISSION = "users.impersonate"
_PRIVILEGED_PERMISSION = "privilegedaccess.readwrite"


@dataclass(frozen=True)
class Step:
    key: str
    label: str
    status: StepStatus
    reason: str
    facts: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Trace:
    outcome: Outcome
    enforced: bool
    steps: tuple[Step, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "enforced": self.enforced,
            "steps": [asdict(step) for step in self.steps],
        }


@dataclass(frozen=True)
class RunUser:
    user_id: UUID
    ctx: AuthorizationContext
    identity_kind: str | None

    @property
    def home(self) -> UUID | None:
        return self.ctx.home_organization_id

    @property
    def is_platform_admin(self) -> bool:
        return self.ctx.is_platform_admin


@dataclass(frozen=True)
class Powers:
    mode: WorkflowPermissionMode
    grants: tuple[WorkflowGrant, ...]


@dataclass(frozen=True)
class RunAsTarget:
    """The user a ``run_as`` acts as."""

    user_id: UUID
    # Home organization; None is Global.
    organization_id: UUID | None
    is_active: bool
    is_system: bool
    identity_kind: str | None
    is_superuser: bool
    is_external: bool
    is_provider_org: bool
    email: str
    name: str | None
    # A Platform Admin or a holder of any privileged permission.
    privileged: bool


def in_reach(run_user: RunUser, target: TargetOrg) -> tuple[bool, str]:
    """Whether ``target`` is in the run user's reach, and through what."""
    if target is None:
        return True, "global"
    if run_user.is_platform_admin:
        return True, "platform_admin"
    if target == ALL_ORGS:
        return False, "outside"
    if target == run_user.home:
        return True, "home"
    for grant in run_user.ctx.role_grants:
        for boundary in grant.boundaries:
            if boundary.kind == BoundaryKind.ORGANIZATION and boundary.organization_id == target:
                return True, f"role:{grant.role_id}@organization"
            if boundary.kind == BoundaryKind.MANAGED_ORGANIZATIONS and target != PROVIDER_ORG_ID:
                return True, f"role:{grant.role_id}@managed_organizations"
    return False, "outside"


def _run_user_step(run_user: RunUser) -> Step:
    who = "identity" if run_user.identity_kind else "person"
    return Step(
        key="run_user",
        label="Run user",
        status="passed",
        reason=who,
        facts={
            "user_id": str(run_user.user_id),
            "identity_kind": run_user.identity_kind,
            "home_organization_id": str(run_user.home) if run_user.home else None,
            "is_platform_admin": run_user.is_platform_admin,
        },
    )


def _powers_step(powers: Powers) -> Step:
    return Step(
        key="powers",
        label="Workflow powers",
        status="passed",
        reason=powers.mode.value,
        facts={"grants": [grant.model_dump(mode="json") for grant in powers.grants]},
    )


def _trace(steps: list[Step]) -> Trace:
    stopped = False
    finished: list[Step] = []
    for step in steps:
        if stopped:
            finished.append(Step(step.key, step.label, "not_reached", "", {}))
            continue
        finished.append(step)
        stopped = step.status == "stopped"
    return Trace(outcome="failure" if stopped else "success", enforced=False, steps=tuple(finished))


def _restricted_context(run_user: RunUser, powers: Powers) -> AuthorizationContext:
    """The run user's context plus the workflow's grants as additional roles.

    A grant without a boundary applies at the run user's home organization
    (Global for the global identity).
    """
    home = (
        Boundary(BoundaryKind.PLATFORM)
        if run_user.home is None
        else Boundary(BoundaryKind.ORGANIZATION, run_user.home)
    )
    grants = tuple(
        RoleGrant(
            role_id=run_user.user_id,
            permissions=frozenset({grant.permission}),
            boundaries=(
                home
                if grant.boundary is None
                else Boundary(BoundaryKind(grant.boundary), grant.organization_id),
            ),
        )
        for grant in powers.grants
    )
    ctx = run_user.ctx
    return AuthorizationContext(
        user_id=ctx.user_id,
        home_organization_id=ctx.home_organization_id,
        base_role_id=ctx.base_role_id,
        is_external=ctx.is_external,
        base_permissions=ctx.base_permissions,
        role_grants=(*ctx.role_grants, *grants),
    )


def _evaluator_target(run_user: RunUser, target: UUID | None) -> Target:
    if target is not None and target == run_user.home:
        return HOME
    return GLOBAL if target is None else cross_org(target)


def _permission_entry(permission: str) -> AccessEntry:
    """An organization-boundary entry for a power the access list does not
    route (secret decryption and elevated branches happen inside many
    routes)."""
    return AccessEntry(
        method="GET",
        path="(report-only access check)",
        access_class=AccessClass.PERMISSION,
        current_gate=CurrentGate.EVALUATOR,
        permission=permission,
        boundary="organization",
        reason="Report-only access check",
    )


def _permission_step(
    run_user: RunUser, powers: Powers, target: UUID | None, entry: AccessEntry | None
) -> Step:
    if powers.mode is WorkflowPermissionMode.FULL:
        return Step("permission", "Permission", "passed", "full")
    if entry is None:
        return Step("permission", "Permission", "not_applicable", "no_access_list_entry")
    decision = decide(_restricted_context(run_user, powers), entry, _evaluator_target(run_user, target))
    return Step(
        "permission",
        "Permission",
        "passed" if decision.allowed else "stopped",
        decision.rule,
        {"permission": entry.permission},
    )


def _direct_permission_step(run_user: RunUser, target: UUID | None, entry: AccessEntry) -> Step:
    """The permission held by the person's own roles at ``target``."""
    decision = decide(run_user.ctx, entry, _evaluator_target(run_user, target))
    return Step(
        "permission",
        "Permission",
        "passed" if decision.allowed else "stopped",
        decision.rule,
        {"permission": entry.permission},
    )


_NO_WORKFLOW = Step("powers", "Workflow powers", "not_applicable", "no_workflow")


def _target_step(run_user: RunUser, target: TargetOrg) -> Step:
    inside, via = in_reach(run_user, target)
    return Step(
        "target",
        "Target in reach",
        "passed" if inside else "stopped",
        via,
        {"organization_id": None if target is None else str(target)},
    )


def check_target(
    run_user: RunUser, powers: Powers | None, target: TargetOrg, entry: AccessEntry | None
) -> Trace:
    """Acting in ``target``: in reach, and the power held there. ``powers``
    None is a person acting directly."""
    org = target if isinstance(target, UUID) else None
    if powers is None:
        powers_step = _NO_WORKFLOW
        permission = (
            Step("permission", "Permission", "not_applicable", "no_access_list_entry")
            if entry is None
            else _direct_permission_step(run_user, org, entry)
        )
    else:
        powers_step, permission = _powers_step(powers), _permission_step(run_user, powers, org, entry)
    return _trace([_run_user_step(run_user), powers_step, _target_step(run_user, target), permission])


def _workflow_access_step(run_user: RunUser, workflow_access: bool | None) -> Step:
    label = "Workflow access"
    if run_user.identity_kind:
        return Step("workflow_access", label, "not_applicable", "unattended")
    allowed = bool(workflow_access)
    return Step("workflow_access", label, "passed" if allowed else "stopped", "access" if allowed else "no_access")


def check_operation(
    run_user: RunUser,
    powers: Powers | None,
    target: UUID | None,
    entry: AccessEntry,
    *,
    workflow_access: bool | None,
) -> Trace:
    """What-if: ``run_user`` performing ``entry`` in ``target``, directly
    (``powers`` None) or through a workflow. ``workflow_access`` is read only
    for a person starting a workflow."""
    if powers is None:
        return _trace(
            [
                _run_user_step(run_user),
                _NO_WORKFLOW,
                _target_step(run_user, target),
                _direct_permission_step(run_user, target, entry),
            ]
        )
    return _trace(
        [
            _workflow_access_step(run_user, workflow_access),
            _run_user_step(run_user),
            _powers_step(powers),
            _target_step(run_user, target),
            _permission_step(run_user, powers, target, entry),
        ]
    )


def check_permission(
    run_user: RunUser, powers: Powers | None, permission: str, target: TargetOrg
) -> Trace:
    """Using a named permission in ``target``: in reach, and the permission
    held there. ``powers`` None is a person acting directly (their own roles
    decide); otherwise the run user's roles plus what the workflow adds. A
    Platform Admin holds every permission but ``secrets.read``."""
    org = target if isinstance(target, UUID) else None
    trace = _trace(
        [
            _run_user_step(run_user),
            _NO_WORKFLOW if powers is None else _powers_step(powers),
            _target_step(run_user, target),
            _held_step(run_user, powers, org, permission),
        ]
    )
    # The permission is named even when the target stopped the trace first.
    return replace(trace, steps=(*trace.steps[:-1], _named(trace.steps[-1], permission)))


def _held_step(run_user: RunUser, powers: Powers | None, target: UUID | None, permission: str) -> Step:
    """``permission`` held at ``target``: by the person's own roles
    (``powers`` None), or by the run user's roles plus the workflow's powers."""
    entry = _permission_entry(permission)
    if powers is None:
        return _direct_permission_step(run_user, target, entry)
    return _permission_step(run_user, powers, target, entry)


def _named(step: Step, permission: str) -> Step:
    return replace(
        step, facts={"permission": permission, "permission_display_name": permission_display_name(permission)}
    )


def run_as_user_step(target: RunAsTarget) -> Step:
    """Whether ``target`` can be acted as at all: a person who is active, not
    the system account and not a managed identity."""
    if not target.is_active:
        status, reason = "stopped", "inactive"
    elif target.is_system:
        status, reason = "stopped", "system_account"
    elif target.identity_kind:
        status, reason = "stopped", "managed_identity"
    else:
        status, reason = "passed", "person"
    return Step(
        "run_as_user",
        "Run As User",
        status,
        reason,
        {
            "run_as_user_id": str(target.user_id),
            "organization_id": None if target.organization_id is None else str(target.organization_id),
        },
    )


def check_run_as(run_user: RunUser, powers: Powers | None, target: RunAsTarget) -> Trace:
    """Acting as ``target``: a person (not inactive, the system account or an
    identity), in reach, and Impersonate Users held in the target's
    organization (a Global target at the platform boundary). A privileged
    target also needs Privileged Access there. ``powers`` None is a person
    acting directly; Full adds both permissions, Restricted needs a grant
    naming them."""
    org = target.organization_id
    steps = [
        _run_user_step(run_user),
        _NO_WORKFLOW if powers is None else _powers_step(powers),
        run_as_user_step(target),
        _target_step(run_user, org),
        _held_step(run_user, powers, org, _IMPERSONATE_PERMISSION),
    ]
    if target.privileged:
        held = _held_step(run_user, powers, org, _PRIVILEGED_PERMISSION)
        steps.append(replace(held, key="privileged_target", label="Privileged User"))
    trace = _trace(steps)
    # Each permission is named even when an earlier step stopped the trace.
    permissions = {"permission": _IMPERSONATE_PERMISSION, "privileged_target": _PRIVILEGED_PERMISSION}
    return replace(
        trace,
        steps=tuple(
            _named(step, permissions[step.key]) if step.key in permissions else step for step in trace.steps
        ),
    )


def check_entry(run_user: RunUser, *, allowed: bool, subject: str) -> Trace:
    """Starting ``subject`` (workflow, agent): the run user's own access to it."""
    step = Step("entry", "Entry", "passed" if allowed else "stopped", "access" if allowed else "no_access", {"subject": subject})
    return _trace([_run_user_step(run_user), step])


def check_policy(
    run_user: RunUser, powers: Powers, *, today: bool, model: bool, missing: list[str]
) -> Trace:
    """A table or file policy evaluated against the run user."""
    step = Step(
        "policy",
        "Policy",
        "passed" if model else "stopped",
        "allowed" if model else "denied",
        {"today": today, "missing": missing},
    )
    return _trace([_run_user_step(run_user), _powers_step(powers), step])


def check_secret(run_user: RunUser, powers: Powers, target: UUID | None, *, kind: str, name: str) -> Trace:
    """Decrypting a secret: Full adds it; otherwise the run user or a grant must hold it."""
    step = _permission_step(run_user, powers, target, _permission_entry(_SECRETS_PERMISSION))
    step = Step(step.key, "Read secret", step.status, step.reason, {**step.facts, "kind": kind, "name": name})
    return _trace([_run_user_step(run_user), _powers_step(powers), step])


async def load_run_user(db: AsyncSession, user_id: UUID) -> RunUser | None:
    """The run user's authorization context; None when the user no longer exists."""
    identity_kind = (
        await db.execute(select(User.identity_kind).where(User.id == user_id))
    ).one_or_none()
    if identity_kind is None:
        return None
    ctx = await build_authorization_context(db, user_id)
    kind = identity_kind[0]
    return RunUser(user_id=user_id, ctx=ctx, identity_kind=None if kind is None else str(kind))


async def load_run_as_target(db: AsyncSession, user_id: UUID) -> RunAsTarget | None:
    """The user a ``run_as`` names; None when no such user exists."""
    row = (
        await db.execute(
            select(
                User.organization_id,
                User.is_active,
                User.identity_kind,
                User.is_superuser,
                User.is_external,
                User.email,
                User.name,
                Organization.is_provider,
            )
            .outerjoin(Organization, Organization.id == User.organization_id)
            .where(User.id == user_id)
        )
    ).one_or_none()
    if row is None:
        return None
    return RunAsTarget(
        user_id=user_id,
        organization_id=row.organization_id,
        is_active=row.is_active,
        is_system=is_system_account(user_id),
        identity_kind=None if row.identity_kind is None else str(row.identity_kind),
        is_superuser=row.is_superuser,
        is_external=row.is_external,
        is_provider_org=bool(row.is_provider),
        email=row.email,
        name=row.name,
        privileged=user_id in await privileged_user_ids(db, [user_id]),
    )


async def load_powers(db: AsyncSession, workflow_id: UUID | None) -> Powers:
    """The workflow's mode and grants; the platform default for no workflow."""
    workflow = None if workflow_id is None else await db.get(Workflow, workflow_id)
    if workflow is None:
        return Powers(await get_default_workflow_permission_mode(db), ())
    mode = await effective_workflow_permission_mode(db, workflow)
    grants = await list_workflow_grants(db, workflow.id) if mode is WorkflowPermissionMode.RESTRICTED else []
    return Powers(mode, tuple(grants))
