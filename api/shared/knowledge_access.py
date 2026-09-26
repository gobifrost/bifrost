"""Namespace-role visibility for direct (non-engine) knowledge reads.

The knowledge store has no per-document grant axis (no roles, no
access_level, no row policies) — see ``knowledge_sources.py``'s
``_deny_external``. ``KnowledgeNamespaceRole`` exists to gate which
namespaces a *human* caller may browse/search directly, mirroring what
``GET /api/agents/accessible-knowledge`` already computes for "which
namespaces can I assign to my agents." This module is the one place that
computes the equivalent set for direct read surfaces (REST
``knowledge_sources.py`` and the SDK ``/api/sdk/knowledge/*`` routes in
``cli.py``) so the two don't drift.

Engine/service callers and other bypass principals (platform admin,
provider org) are unrestricted — the engine sentinel always resolves with
the full cascade (see ``api/src/repositories/README.md``). Only a direct,
non-bypass human caller is filtered down to namespaces their roles grant.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.scope_resolver import has_scope_bypass
from src.core.principal import UserPrincipal


async def accessible_namespaces_for_user(
    db: AsyncSession, user: UserPrincipal
) -> set[str] | None:
    """Return the namespaces ``user`` may read directly.

    Returns ``None`` for a bypass caller (platform admin / provider org),
    meaning "unrestricted." Returns a (possibly empty) set of namespace
    names for a regular caller, derived from their roles'
    ``KnowledgeNamespaceRole`` grants.
    """
    if has_scope_bypass(
        is_platform_admin=user.is_superuser,
        is_provider_org=user.is_provider_org,
    ):
        return None

    from src.models.orm.knowledge_sources import KnowledgeNamespaceRole
    from src.models.orm.users import UserRole

    role_ids_result = await db.execute(
        select(UserRole.role_id).where(UserRole.user_id == user.user_id)
    )
    role_ids = list(role_ids_result.scalars().all())
    if not role_ids:
        return set()

    namespaces_result = await db.execute(
        select(KnowledgeNamespaceRole.namespace)
        .where(KnowledgeNamespaceRole.role_id.in_(role_ids))
        .distinct()
    )
    return set(namespaces_result.scalars().all())


__all__ = ["accessible_namespaces_for_user"]
