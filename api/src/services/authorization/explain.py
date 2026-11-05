"""Explain and enforce original-user reach plus configured workflow powers.

Every run keeps its original user. Reach includes that user's home, placed
roles, and Global. A real Platform Admin reaches every organization. Full
workflows add every named power, including Secrets; Restricted workflows add
only their configured permission capabilities. Agents and inline executions
add no workflow powers. Acting identity remains separate.

Each check returns a Trace with its outcome and decision steps. Enforcement
and the Access Checks view share these rules and the native role evaluator.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from shared.access_checks import ALL_ORGS, NoteTarget
from shared.access_trace_storage import CANONICAL_TRACE_STEP_LABELS
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
    BoundaryKind,
    build_authorization_context,
)
from src.services.authorization.enforce import privileged_user_ids
from src.services.authorization.evaluator import HOME, GLOBAL, Target, cross_org, decide
from src.services.workflow_permissions import (
    get_default_workflow_permission_mode,
    load_workflow_permissions,
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
    # Only acting as another user asks: someone inactive can't act as anyone.
    is_active: bool

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
        label=CANONICAL_TRACE_STEP_LABELS["run_user"],
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
        label=CANONICAL_TRACE_STEP_LABELS["powers"],
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


def _evaluator_target(run_user: RunUser, target: UUID | None, entry: AccessEntry) -> Target:
    if target == run_user.home and entry.boundary != "platform":
        return HOME
    return GLOBAL if target is None else cross_org(target)


def permission_entry(permission: str) -> AccessEntry:
    """An organization-boundary check for a named power inside an operation."""
    return AccessEntry(
        method="GET",
        path="(named permission check)",
        access_class=AccessClass.PERMISSION,
        current_gate=CurrentGate.EVALUATOR,
        permission=permission,
        boundary="organization",
        reason="Named permission check",
    )


def _permission_step(
    run_user: RunUser, powers: Powers, target: UUID | None, entry: AccessEntry | None
) -> Step:
    if powers.mode is WorkflowPermissionMode.FULL:
        return Step("permission", CANONICAL_TRACE_STEP_LABELS["permission"], "passed", "full")
    if entry is None:
        return Step(
            "permission",
            CANONICAL_TRACE_STEP_LABELS["permission"],
            "not_applicable",
            "no_access_list_entry",
        )
    if entry.permission is not None and any(grant.permission == entry.permission for grant in powers.grants):
        return Step(
            "permission", CANONICAL_TRACE_STEP_LABELS["permission"], "passed", "workflow_capability",
            {"permission": entry.permission},
        )
    decision = decide(run_user.ctx, entry, _evaluator_target(run_user, target, entry))
    return Step(
        "permission",
        CANONICAL_TRACE_STEP_LABELS["permission"],
        "passed" if decision.allowed else "stopped",
        decision.rule,
        {"permission": entry.permission},
    )


def _direct_permission_step(run_user: RunUser, target: UUID | None, entry: AccessEntry) -> Step:
    """The permission held by the person's own roles at ``target``."""
    decision = decide(run_user.ctx, entry, _evaluator_target(run_user, target, entry))
    return Step(
        "permission",
        "Permission",
        "passed" if decision.allowed else "stopped",
        decision.rule,
        {"permission": entry.permission},
    )


_NO_WORKFLOW = Step("powers", CANONICAL_TRACE_STEP_LABELS["powers"], "not_applicable", "no_workflow")


def _target_step(run_user: RunUser, target: TargetOrg) -> Step:
    inside, via = in_reach(run_user, target)
    return Step(
        "target",
        CANONICAL_TRACE_STEP_LABELS["target"],
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
            Step("permission", CANONICAL_TRACE_STEP_LABELS["permission"], "not_applicable", "no_access_list_entry")
            if entry is None
            else _direct_permission_step(run_user, org, entry)
        )
    else:
        powers_step, permission = _powers_step(powers), _permission_step(run_user, powers, org, entry)
    return _trace([_run_user_step(run_user), powers_step, _target_step(run_user, target), permission])


def _workflow_access_step(run_user: RunUser, workflow_access: bool | None) -> Step:
    label = CANONICAL_TRACE_STEP_LABELS["workflow_access"]
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
    entry = permission_entry(permission)
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
        CANONICAL_TRACE_STEP_LABELS["run_as_user"],
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
    run_user_step = _run_user_step(run_user)
    if not run_user.is_active:
        run_user_step = replace(run_user_step, status="stopped", reason="inactive")
    steps = [
        run_user_step,
        _NO_WORKFLOW if powers is None else _powers_step(powers),
        run_as_user_step(target),
        _target_step(run_user, org),
        _held_step(run_user, powers, org, _IMPERSONATE_PERMISSION),
    ]
    if target.privileged:
        held = _held_step(run_user, powers, org, _PRIVILEGED_PERMISSION)
        steps.append(
            replace(
                held,
                key="privileged_target",
                label=CANONICAL_TRACE_STEP_LABELS["privileged_target"],
            )
        )
    trace = _trace(steps)
    # Each permission is named even when an earlier step stopped the trace.
    permissions = {"permission": _IMPERSONATE_PERMISSION, "privileged_target": _PRIVILEGED_PERMISSION}
    return replace(
        trace,
        steps=tuple(
            _named(step, permissions[step.key]) if step.key in permissions else step for step in trace.steps
        ),
    )


def check_run_as_outside_reach(run_user: RunUser) -> Trace:
    """A holder of Impersonate Users named a user they can't act as: one that
    doesn't exist, or one outside the organizations their grant covers. The
    trace is the same for both and records nothing about that user; the
    detailed decision is recorded in the user's own organization."""
    step = Step("target", CANONICAL_TRACE_STEP_LABELS["target"], "stopped", "outside_reach", {})
    return _trace([_run_user_step(run_user), _NO_WORKFLOW, step])


def check_entry(run_user: RunUser, *, allowed: bool, subject: str) -> Trace:
    """Starting ``subject`` (workflow, agent): the run user's own access to it."""
    step = Step(
        "entry",
        CANONICAL_TRACE_STEP_LABELS["entry"],
        "passed" if allowed else "stopped",
        "access" if allowed else "no_access",
        {"subject": subject},
    )
    return _trace([_run_user_step(run_user), step])


def check_policy(
    run_user: RunUser, powers: Powers | None, *, today: bool, model: bool, missing: list[str]
) -> Trace:
    """A table or file policy evaluated against the run user."""
    step = Step(
        "policy",
        CANONICAL_TRACE_STEP_LABELS["policy"],
        "passed" if model else "stopped",
        "allowed" if model else "denied",
        {"today": today, "missing": missing},
    )
    return _trace([_run_user_step(run_user), _NO_WORKFLOW if powers is None else _powers_step(powers), step])


def check_secret(run_user: RunUser, powers: Powers | None, target: UUID | None, *, kind: str, name: str) -> Trace:
    """Decrypt a secret only with its named power inside original-user reach."""
    trace = check_permission(run_user, powers, _SECRETS_PERMISSION, target)
    permission = trace.steps[-1]
    secret = replace(
        permission, label="Read secret", facts={**permission.facts, "kind": kind, "name": name},
    )
    return replace(trace, steps=(*trace.steps[:-1], secret))


async def load_run_user(db: AsyncSession, user_id: UUID) -> RunUser | None:
    """The run user's authorization context; None when the user no longer exists."""
    row = (
        await db.execute(select(User.identity_kind, User.is_active).where(User.id == user_id))
    ).one_or_none()
    if row is None:
        return None
    try:
        ctx = await build_authorization_context(db, user_id)
    except NoResultFound:
        # Deleted between the two reads.
        return None
    kind, is_active = row
    return RunUser(user_id=user_id, ctx=ctx, identity_kind=None if kind is None else str(kind), is_active=is_active)


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


async def load_powers_for_workflow(
    db: AsyncSession, workflow: Workflow | None
) -> Powers:
    """The supplied workflow's mode and grants; the platform default without one."""
    if workflow is None:
        return Powers(await get_default_workflow_permission_mode(db), ())
    mode, grants = await load_workflow_permissions(db, workflow)
    return Powers(mode, tuple(grants))


async def load_powers(db: AsyncSession, workflow_id: UUID | None) -> Powers:
    """The workflow's mode and grants; the platform default for no workflow."""
    workflow = (
        None
        if workflow_id is None
        else await db.get(Workflow, workflow_id, populate_existing=True)
    )
    return await load_powers_for_workflow(db, workflow)
