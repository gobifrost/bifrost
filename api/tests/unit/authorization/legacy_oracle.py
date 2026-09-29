"""What today's code decides, restated from the access-list data.

Test-only. ``legacy_decide`` encodes the semantics the platform enforces today
(dependency-tree gate, then the inline effect the handler layers on top, and
scope bypass for acting beyond one's own org) so the R2c evaluator can be
compared against it cell by cell.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import NAMESPACE_OID, UUID, uuid5

from shared.builtin_roles import PLATFORM_ADMIN_ROLE_ID, USER_BASE_PERMISSIONS, USER_ROLE_ID
from src.core.constants import PROVIDER_ORG_ID
from src.models.contracts.access_list import AccessEntry, CurrentGate, InlineEffect
from src.services.authorization.context import AuthorizationContext

CUSTOMER_ORG_ID = UUID("00000000-0000-0000-0000-00000000c001")
OTHER_CUSTOMER_ORG_ID = UUID("00000000-0000-0000-0000-00000000c002")


@dataclass(frozen=True)
class Persona:
    name: str
    ctx: AuthorizationContext | None
    is_superuser: bool = False
    is_provider_org: bool = False

    @property
    def signed_in(self) -> bool:
        return self.ctx is not None

    @property
    def has_bypass(self) -> bool:
        return self.is_superuser or self.is_provider_org


def _ctx(name: str, base_role_id: UUID, home: UUID, *, external: bool = False) -> AuthorizationContext:
    return AuthorizationContext(
        user_id=uuid5(NAMESPACE_OID, name),
        home_organization_id=home,
        base_role_id=base_role_id,
        is_external=external,
        base_permissions=frozenset()
        if base_role_id == PLATFORM_ADMIN_ROLE_ID
        else USER_BASE_PERMISSIONS,
    )


PERSONAS: tuple[Persona, ...] = (
    Persona("platform_admin", _ctx("platform_admin", PLATFORM_ADMIN_ROLE_ID, PROVIDER_ORG_ID), is_superuser=True, is_provider_org=True),
    Persona("provider_member", _ctx("provider_member", USER_ROLE_ID, PROVIDER_ORG_ID), is_provider_org=True),
    Persona("regular", _ctx("regular", USER_ROLE_ID, CUSTOMER_ORG_ID)),
    Persona("external", _ctx("external", USER_ROLE_ID, CUSTOMER_ORG_ID, external=True)),
    Persona("anonymous", None),
)

_SUPERUSER_ACTS_BEYOND = frozenset(
    {
        InlineEffect.DENY_UNLESS_SUPERUSER,
        InlineEffect.DENY_UNLESS_BYPASS,
        InlineEffect.WIDENS_FOR_SUPERUSER,
        InlineEffect.WIDENS_FOR_BYPASS,
    }
)
_PROVIDER_ACTS_BEYOND = frozenset({InlineEffect.DENY_UNLESS_BYPASS, InlineEffect.WIDENS_FOR_BYPASS})


def _acts_beyond_own_org(persona: Persona, entry: AccessEntry) -> bool:
    gate = entry.current_gate
    if persona.is_superuser:
        return gate in (CurrentGate.SUPERUSER, CurrentGate.ENGINE_OR_BYPASS) or entry.inline_effect in _SUPERUSER_ACTS_BEYOND
    if persona.is_provider_org:
        return gate == CurrentGate.ENGINE_OR_BYPASS or entry.inline_effect in _PROVIDER_ACTS_BEYOND
    return False


def legacy_decide(persona: Persona, entry: AccessEntry, cross: bool) -> bool:
    """Today's decision for ``persona`` on ``entry``; ``cross`` = acting in
    another org or explicit Global (bypass-only scope)."""
    if cross:
        return _acts_beyond_own_org(persona, entry)
    gate = entry.current_gate
    if gate == CurrentGate.NONE:
        return True
    if not persona.signed_in:
        return False
    if gate == CurrentGate.SUPERUSER:
        return persona.is_superuser
    if gate in (CurrentGate.ENGINE, CurrentGate.EMBED):
        return False
    if gate == CurrentGate.ENGINE_OR_BYPASS:
        return persona.has_bypass
    if entry.inline_effect == InlineEffect.DENY_UNLESS_SUPERUSER:
        return persona.is_superuser
    if entry.inline_effect == InlineEffect.DENY_UNLESS_BYPASS:
        return persona.has_bypass
    return True
