"""Resolve and manage the base role given to newly provisioned accounts.

The configured role is deliberately separate from role assignment: it is only
consulted when an account is created.  Existing accounts retain their current
base role when a default changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.builtin_roles import BASE_ROLE_IDS, BUILTIN_ROLE_IDS, USER_ROLE_ID
from src.services.authorization.privilege import is_privileged_principal


DEFAULT_BASE_ROLE_CATEGORY = "users"
DEFAULT_BASE_ROLE_KEY = "default_base_role"
ORGANIZATION_DEFAULT_BASE_ROLE_KEY = "default_base_role_id"


class BaseRoleDefaultError(Exception):
    """A configured or requested base role cannot be used."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class ResolvedBaseRole:
    """The base role selected for one new account."""

    id: UUID
    name: str
    source: Literal["global", "organization"]


async def resolve_base_role(
    session: AsyncSession,
    *,
    organization_id: UUID | None,
    explicit_base_role_id: UUID | None = None,
) -> ResolvedBaseRole:
    """Resolve an explicit role, then organization, global, and User.

    ``explicit_base_role_id`` is validated as a normal base role but may be
    privileged; it is an intentional provisioning choice. Configured defaults
    are separately constrained to non-privileged roles so changing a setting
    cannot silently grant a new account elevated access.
    """
    if explicit_base_role_id is not None:
        role = await _require_base_role(session, explicit_base_role_id)
        return ResolvedBaseRole(role.id, role.name, "organization" if organization_id else "global")

    if organization_id is not None:
        organization = await _require_organization(session, organization_id)
        configured_id = _organization_default_role_id(organization.settings)
        if configured_id is not None:
            role = await _require_base_role(session, configured_id)
            return ResolvedBaseRole(role.id, role.name, "organization")

    configured_id = await _global_default_role_id(session)
    if configured_id is not None:
        role = await _require_base_role(session, configured_id)
        return ResolvedBaseRole(role.id, role.name, "global")

    role = await _require_base_role(session, USER_ROLE_ID)
    return ResolvedBaseRole(role.id, role.name, "global")


async def get_default_base_role(
    session: AsyncSession, *, organization_id: UUID | None
) -> tuple[UUID | None, ResolvedBaseRole]:
    """Return the scope's stored value and the role a new account receives."""
    effective = await resolve_base_role(session, organization_id=organization_id)
    # The global response is always concrete, including the built-in fallback.
    if organization_id is None:
        return effective.id, effective
    organization = await _require_organization(session, organization_id)
    return _organization_default_role_id(organization.settings), effective


async def set_default_base_role(
    session: AsyncSession,
    *,
    organization_id: UUID | None,
    default_base_role_id: UUID | None,
    updated_by: str,
) -> tuple[UUID | None, ResolvedBaseRole]:
    """Store a global default or an organization override.

    A null global value removes the configuration and therefore resets to the
    built-in User role. A null organization value removes its override, making
    it inherit the global setting again.
    """
    if organization_id is None:
        await _lock_global_default_slot(session)
    if default_base_role_id is not None:
        await _require_configurable_default_role(session, default_base_role_id)

    if organization_id is None:
        await _set_global_default_role_id(session, default_base_role_id, updated_by)
    else:
        organization = await _require_organization(session, organization_id, lock=True)
        settings = dict(organization.settings or {})
        if default_base_role_id is None:
            settings.pop(ORGANIZATION_DEFAULT_BASE_ROLE_KEY, None)
        else:
            settings[ORGANIZATION_DEFAULT_BASE_ROLE_KEY] = str(default_base_role_id)
        organization.settings = settings
        await session.flush()

    configured, effective = await get_default_base_role(session, organization_id=organization_id)
    from src.services.audit import emit_audit

    await emit_audit(
        session,
        "base_role_defaults.update",
        resource_type="organization" if organization_id is not None else "platform",
        resource_id=organization_id,
        details={
            "organization_id": str(organization_id) if organization_id is not None else None,
            "default_base_role_id": str(configured) if configured is not None else None,
            "effective_base_role_id": str(effective.id),
            "source": effective.source,
        },
    )
    return configured, effective


async def base_role_is_privileged(session: AsyncSession, role_id: UUID) -> bool:
    """Whether an eligible base role grants any privileged permission."""
    await _require_base_role(session, role_id)
    from src.models import Role as RoleORM
    from src.models import RolePermission as RolePermissionORM

    # Permission replacement takes this same lock before changing a role. A
    # newly created account must not observe an ordinary role while another
    # transaction is elevating it.
    await session.execute(select(RoleORM.id).where(RoleORM.id == role_id).with_for_update())

    permissions = frozenset(
        (await session.execute(
            select(RolePermissionORM.permission).where(RolePermissionORM.role_id == role_id)
        )).scalars()
    )
    return is_privileged_principal(permissions)


async def refuse_referenced_default_base_role_deletion(
    session: AsyncSession, *, role_id: UUID
) -> None:
    """Refuse deleting a role selected by a global or organization default.

    Role deletion calls this while holding its role row lock. The settings
    writer takes that same lock before recording a role, so a configuration
    cannot be committed concurrently with the deletion decision.
    """
    if await default_base_role_reference_count(session, role_id=role_id):
        raise BaseRoleDefaultError(409, "Role is configured as a default base role")


async def default_base_role_reference_count(session: AsyncSession, *, role_id: UUID) -> int:
    """Count Global and organization defaults that reference ``role_id``."""
    from src.models import Organization as OrganizationORM
    from src.models import SystemConfig as SystemConfigORM

    encoded_id = str(role_id)
    global_count = len(
        (await session.execute(
            select(SystemConfigORM.id).where(
                SystemConfigORM.category == DEFAULT_BASE_ROLE_CATEGORY,
                SystemConfigORM.key == DEFAULT_BASE_ROLE_KEY,
                SystemConfigORM.organization_id.is_(None),
                SystemConfigORM.value_json[ORGANIZATION_DEFAULT_BASE_ROLE_KEY].astext == encoded_id,
            )
        )).scalars().all()
    )
    organization_count = len(
        (await session.execute(
            select(OrganizationORM.id).where(
                OrganizationORM.settings[ORGANIZATION_DEFAULT_BASE_ROLE_KEY].astext == encoded_id
            )
        )).scalars().all()
    )
    return global_count + organization_count


async def _require_base_role(session: AsyncSession, role_id: UUID):
    from src.models import Role as RoleORM

    if role_id in BUILTIN_ROLE_IDS - BASE_ROLE_IDS:
        raise BaseRoleDefaultError(422, "Only User or a custom role can be a base role")
    role = await session.get(RoleORM, role_id)
    if role is None:
        raise BaseRoleDefaultError(404, "Base role not found")
    if role.is_builtin and role_id not in BASE_ROLE_IDS:
        raise BaseRoleDefaultError(422, "Only User or a custom role can be a base role")
    return role


async def _require_configurable_default_role(session: AsyncSession, role_id: UUID):
    from src.models import Role as RoleORM

    # This locks the same row that role deletion and permission writes lock.
    # A default cannot race with removing or elevating its selected role.
    role = (await session.execute(
        select(RoleORM).where(RoleORM.id == role_id).with_for_update()
    )).scalar_one_or_none()
    if role is None:
        raise BaseRoleDefaultError(404, "Base role not found")
    if role_id in BUILTIN_ROLE_IDS - BASE_ROLE_IDS or (role.is_builtin and role_id not in BASE_ROLE_IDS):
        raise BaseRoleDefaultError(422, "Only User or a custom role can be a base role")
    if await base_role_is_privileged(session, role_id):
        raise BaseRoleDefaultError(
            422,
            "A default base role cannot grant privileged access",
        )
    return role


async def _require_organization(
    session: AsyncSession, organization_id: UUID, *, lock: bool = False
):
    from src.models import Organization as OrganizationORM

    if lock:
        organization = (
            await session.execute(
                select(OrganizationORM)
                .where(OrganizationORM.id == organization_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
    else:
        organization = await session.get(OrganizationORM, organization_id)
    if organization is None:
        raise BaseRoleDefaultError(404, "Organization not found")
    return organization


async def _lock_global_default_slot(session: AsyncSession) -> None:
    """Serialize writes to the one global setting, including first inserts."""
    from src.models import Role as RoleORM

    # The seeded User role is present in every supported database and is also
    # the fallback selected when no global configuration row exists. Lock it
    # before a selected custom role so concurrent writes select the same order.
    await session.execute(
        select(RoleORM.id).where(RoleORM.id == USER_ROLE_ID).with_for_update()
    )


def _organization_default_role_id(settings: dict | None) -> UUID | None:
    raw_id = (settings or {}).get(ORGANIZATION_DEFAULT_BASE_ROLE_KEY)
    if raw_id is None:
        return None
    try:
        return UUID(str(raw_id))
    except (TypeError, ValueError) as exc:
        raise BaseRoleDefaultError(409, "Organization default base role is invalid") from exc


async def _global_default_role_id(session: AsyncSession) -> UUID | None:
    from src.models import SystemConfig as SystemConfigORM

    config = (await session.execute(
        select(SystemConfigORM).where(
            SystemConfigORM.category == DEFAULT_BASE_ROLE_CATEGORY,
            SystemConfigORM.key == DEFAULT_BASE_ROLE_KEY,
            SystemConfigORM.organization_id.is_(None),
        )
    )).scalars().first()
    if config is None or not config.value_json:
        return None
    raw_id = config.value_json.get(ORGANIZATION_DEFAULT_BASE_ROLE_KEY)
    if raw_id is None:
        return None
    try:
        return UUID(str(raw_id))
    except (TypeError, ValueError) as exc:
        raise BaseRoleDefaultError(409, "Global default base role is invalid") from exc


async def _set_global_default_role_id(
    session: AsyncSession, role_id: UUID | None, updated_by: str
) -> None:
    from src.models import SystemConfig as SystemConfigORM

    config = (await session.execute(
        select(SystemConfigORM).where(
            SystemConfigORM.category == DEFAULT_BASE_ROLE_CATEGORY,
            SystemConfigORM.key == DEFAULT_BASE_ROLE_KEY,
            SystemConfigORM.organization_id.is_(None),
        ).with_for_update()
    )).scalars().first()
    if role_id is None:
        if config is not None:
            await session.delete(config)
            await session.flush()
        return
    value = {ORGANIZATION_DEFAULT_BASE_ROLE_KEY: str(role_id)}
    if config is None:
        session.add(
            SystemConfigORM(
                id=uuid4(),
                category=DEFAULT_BASE_ROLE_CATEGORY,
                key=DEFAULT_BASE_ROLE_KEY,
                value_json=value,
                value_bytes=None,
                organization_id=None,
                created_by=updated_by,
                updated_by=updated_by,
            )
        )
    else:
        config.value_json = value
        config.updated_by = updated_by
    await session.flush()
