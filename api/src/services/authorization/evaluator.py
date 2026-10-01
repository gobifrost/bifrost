"""The authorization decision function (R2c). Not enforced.

Nothing in request handling calls ``decide`` yet; R3 wires it in, domain by
domain, after the persona x operation matrix
(``tests/unit/authorization/test_decision_matrix.py``) proved it decides what
today's code decides, apart from the differences the access list marks as
intended (``intended_change``: the provider-org non-admin paths that go away).

The decision combines the person's base role, their additional roles at the
target's boundary, the object's own access setting, and the External flag.
This module implements the operation level: the first two. The object level
(``OrgScopedRepository``, ``access_level``, role grants) stays in existing
code.

1. A Platform Admin base role is allowed, except on an entry whose permission
   the wildcard does not satisfy (``WILDCARD_EXCLUDED_PERMISSIONS``: secret
   decryption). There an admin is decided like anyone else, by explicit
   role grants.
2. ``permission`` class: the entry's permission must be held by the base role
   (home organization only, never for a ``platform``-boundary entry) or by an
   additional role whose boundary covers the target:
   ``organization`` covers exactly that org, ``managed_organizations`` covers
   every customer org (not the provider org, not Global), ``platform`` covers
   Global / platform-level targets. A ``platform``-boundary entry is only
   satisfied by a ``platform`` boundary.
3. Other classes: ``public`` allows anyone (anonymous included); ``personal``,
   ``execute``, ``own_private_agent`` and ``table_policy`` allow any signed-in
   user acting in their home org (the object check decides the rest);
   ``embed`` is never a user decision.
4. External users decide the same as Users here: External only changes the
   object-level meaning of "authenticated".

Targets are the scope the caller acts in, as ``resolve_effective_scope``
resolves it, not the owning org of a cascaded object: ``HOME`` is an unset
scope or the caller's own org; ``CROSS`` is another org or explicit Global.
Beyond one's own org only two things allow: a Platform Admin on an operation
that acts beyond one's org at all (see ``acts_beyond_own_org``), or a
``permission``-class operation whose permission a role with a covering
boundary grants.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from src.core.constants import PROVIDER_ORG_ID
from src.models.contracts.access_list import (
    AccessClass,
    AccessEntry,
    CurrentGate,
    InlineEffect,
)
from src.models.contracts.permissions import WILDCARD_EXCLUDED_PERMISSIONS
from src.services.authorization.context import AuthorizationContext, Boundary, BoundaryKind


class TargetKind(StrEnum):
    HOME = "home"
    CROSS = "cross"


@dataclass(frozen=True)
class Target:
    kind: TargetKind
    # CROSS only: the organization acted in; None is explicit Global.
    organization_id: UUID | None = None


HOME = Target(TargetKind.HOME)
GLOBAL = Target(TargetKind.CROSS)


def cross_org(organization_id: UUID) -> Target:
    return Target(TargetKind.CROSS, organization_id)


@dataclass(frozen=True)
class Decision:
    allowed: bool
    # Short machine-readable reason: platform_admin | public | signed_in |
    # base_role:<perm> | role:<role_id>:<perm>@<boundary> | denied:<why>.
    rule: str


_CROSS_ORG_EFFECTS = frozenset(
    {
        InlineEffect.DENY_UNLESS_SUPERUSER,
        InlineEffect.DENY_UNLESS_BYPASS,
        InlineEffect.WIDENS_FOR_SUPERUSER,
        InlineEffect.WIDENS_FOR_BYPASS,
    }
)


def acts_beyond_own_org(entry: AccessEntry) -> bool:
    """Whether the operation lets a caller act outside their own org at all.

    Read from the access-list data: a superuser or scope-bypass gate, or an
    inline check that admits or widens for a superuser/bypass caller. An
    operation with neither is confined to the caller's own org for everyone.
    """
    return (
        entry.current_gate in (CurrentGate.SUPERUSER, CurrentGate.ENGINE_OR_BYPASS)
        or entry.inline_effect in _CROSS_ORG_EFFECTS
    )


def _covers(boundary: Boundary, entry: AccessEntry, ctx: AuthorizationContext, target: Target) -> bool:
    if entry.boundary == "platform":
        return boundary.kind == BoundaryKind.PLATFORM
    org = ctx.home_organization_id if target.kind == TargetKind.HOME else target.organization_id
    if boundary.kind == BoundaryKind.ORGANIZATION:
        return org is not None and boundary.organization_id == org
    if boundary.kind == BoundaryKind.MANAGED_ORGANIZATIONS:
        return org is not None and org != PROVIDER_ORG_ID
    return target.kind == TargetKind.CROSS and target.organization_id is None


def decide(ctx: AuthorizationContext | None, entry: AccessEntry, target: Target) -> Decision:
    """Decide whether ``ctx`` (None = anonymous) may perform ``entry`` at ``target``."""
    if entry.access_class == AccessClass.EMBED:
        return Decision(False, "denied:embed_session_only")

    if ctx is None:
        if entry.access_class == AccessClass.PUBLIC and target.kind == TargetKind.HOME:
            return Decision(True, "public")
        return Decision(False, "denied:anonymous")

    if (
        target.kind == TargetKind.CROSS
        and target.organization_id is not None
        and target.organization_id == ctx.home_organization_id
    ):
        target = HOME

    if ctx.is_platform_admin and entry.permission not in WILDCARD_EXCLUDED_PERMISSIONS:
        if target.kind == TargetKind.HOME or acts_beyond_own_org(entry):
            return Decision(True, "platform_admin")
        return Decision(False, "denied:operation_confined_to_own_org")

    if entry.access_class != AccessClass.PERMISSION:
        if target.kind == TargetKind.CROSS:
            return Decision(False, "denied:beyond_own_org_requires_role")
        return Decision(True, "public" if entry.access_class == AccessClass.PUBLIC else "signed_in")

    permission = entry.permission
    assert permission is not None
    if target.kind == TargetKind.HOME and entry.boundary != "platform" and permission in ctx.base_permissions:
        return Decision(True, f"base_role:{permission}")
    for grant in ctx.role_grants:
        if permission not in grant.permissions:
            continue
        for boundary in grant.boundaries:
            if _covers(boundary, entry, ctx, target):
                return Decision(True, f"role:{grant.role_id}:{permission}@{boundary.kind.value}")
    return Decision(False, f"denied:missing:{permission}")
