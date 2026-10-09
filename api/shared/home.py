"""Home organization uses existing resource repositories as its access authority."""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from shared import access_checks
from shared.access_checks import ALL_ORGS
from src.core.principal import UserPrincipal
from src.models.contracts.home import (
    HomeCollectionPublic,
    HomeCollectionWrite,
    HomePreferenceWrite,
    HomeResource,
    HomeResponse,
)
from shared.logo_processing import is_logo_thumbnail_version
from src.models.orm.home import HomeCollection, HomeResourcePreference
from src.models.orm.organizations import Organization
from src.repositories.agents import AgentRepository
from src.repositories.applications import ApplicationRepository
from src.repositories.forms import FormRepository


def _logo_url(prefix: str, entity) -> str | None:
    version = getattr(entity, "logo_thumbnail_version", None)
    if is_logo_thumbnail_version(version):
        return f"{prefix}/{entity.id}/logo?v={version}"
    if getattr(entity, "logo_content_type", None):
        return f"{prefix}/{entity.id}/logo"
    return None


def _logo_version(entity) -> str | None:
    version = getattr(entity, "logo_thumbnail_version", None)
    return version if is_logo_thumbnail_version(version) else None


def can_edit_collection(collection: HomeCollection, user: UserPrincipal) -> bool:
    if not collection.shared:
        return collection.owner_id == user.user_id
    if not user.is_platform_admin:
        return False
    access_checks.note_power(
        "home.readwrite" if collection.owner_id == user.user_id else "home.readwrite.all",
        collection.organization_id,
        subject=f"home_collection:{collection.id}",
    )
    return True


def can_read_collection(collection: HomeCollection, user: UserPrincipal) -> bool:
    if not collection.shared:
        return collection.owner_id == user.user_id
    if collection.organization_id == user.organization_id or (
        collection.organization_id is None and not user.is_external
    ):
        return True
    if not user.is_platform_admin:
        return False
    access_checks.note_power("home.read.all", collection.organization_id, subject=f"home_collection:{collection.id}")
    return True


def collection_audience(
    shared: bool, organization_id: UUID | None
) -> tuple[bool, UUID | None]:
    return shared, organization_id


async def catalog(db: AsyncSession, user: UserPrincipal) -> list[HomeResource]:
    """Only launchable resources; shared collection membership does not widen this set."""
    apps_repo = ApplicationRepository(
        db,
        user.organization_id,
        user_id=user.user_id,
        is_superuser=user.is_platform_admin,
        is_external=user.is_external,
    )
    forms_repo = FormRepository(
        db,
        user.organization_id,
        user_id=user.user_id,
        is_superuser=user.is_platform_admin,
        is_external=user.is_external,
    )
    agents_repo = AgentRepository(
        db,
        user.organization_id,
        user_id=user.user_id,
        is_superuser=user.is_platform_admin,
        is_external=user.is_external,
    )
    if user.is_platform_admin:
        apps = await apps_repo.list_all_in_scope()
        forms = await forms_repo.list_all_in_scope(active_only=True)
        agents = await agents_repo.list_all_in_scope(active_only=True)
    else:
        apps = await apps_repo.list_applications()
        forms = await forms_repo.list_forms()
        agents = await agents_repo.list_agents()
    org_ids = {
        entity.organization_id
        for entity in [*apps, *forms, *agents]
        if entity.organization_id
    }
    orgs = dict(
        (
            await db.execute(
                select(Organization.id, Organization.name).where(
                    Organization.id.in_(org_ids)
                )
            )
        ).all()
    )
    resources: list[HomeResource] = []
    for app in apps:
        if app.is_published:
            resources.append(
                HomeResource(
                    key=f"app:{app.id}",
                    id=app.id,
                    kind="app",
                    name=app.name,
                    description=app.description,
                    icon=app.icon or "app-window",
                    logo_url=_logo_url("/api/applications", app),
                    logo_version=_logo_version(app),
                    organization_id=app.organization_id,
                    organization_name=orgs.get(app.organization_id, "Global"),
                    href=f"/apps/{app.slug}",
                )
            )
    for form in forms:
        resources.append(
            HomeResource(
                key=f"form:{form.id}",
                id=form.id,
                kind="form",
                name=form.name,
                description=form.description,
                icon="file-input",
                logo_url=_logo_url("/api/forms", form),
                logo_version=_logo_version(form),
                organization_id=form.organization_id,
                organization_name=orgs.get(form.organization_id, "Global"),
                href=f"/execute/{form.id}",
            )
        )
    for agent in agents:
        if "chat" in (agent.channels or []):
            resources.append(
                HomeResource(
                    key=f"agent:{agent.id}",
                    id=agent.id,
                    kind="agent",
                    name=agent.name,
                    description=agent.description,
                    icon="bot",
                    logo_url=_logo_url("/api/agents", agent),
                    logo_version=_logo_version(agent),
                    organization_id=agent.organization_id,
                    organization_name=orgs.get(agent.organization_id, "Global"),
                    href="/chat",
                )
            )
    preferences = (
        await db.scalars(
            select(HomeResourcePreference).where(
                HomeResourcePreference.user_id == user.user_id
            )
        )
    ).all()
    by_key = {pref.resource_key: pref for pref in preferences}
    for resource in resources:
        pref = by_key.get(resource.key)
        if pref:
            resource.pinned = pref.pinned
            resource.last_opened_at = pref.last_opened_at
    return sorted(
        resources, key=lambda resource: (resource.name.casefold(), resource.key)
    )


async def public_collection(
    db: AsyncSession, row: HomeCollection, user: UserPrincipal, allowed: set[str]
) -> HomeCollectionPublic:
    org_name = (
        await db.scalar(
            select(Organization.name).where(Organization.id == row.organization_id)
        )
        if row.organization_id
        else None
    )
    return HomeCollectionPublic(
        id=row.id,
        name=row.name,
        description=row.description,
        icon=row.icon,
        shared=row.shared,
        organization_id=row.organization_id,
        organization_name=org_name,
        resource_keys=[key for key in row.resource_keys if key in allowed],
        can_edit=can_edit_collection(row, user),
    )


async def get_home(db: AsyncSession, user: UserPrincipal) -> HomeResponse:
    resources = await catalog(db, user)
    shared_scope = or_(
        HomeCollection.organization_id == user.organization_id,
        HomeCollection.organization_id.is_(None) if not user.is_external else False,
    )
    query = (
        select(HomeCollection)
        .where(
            or_(
                and_(
                    HomeCollection.shared.is_(False),
                    HomeCollection.owner_id == user.user_id,
                ),
                and_(
                    HomeCollection.shared.is_(True),
                    True if user.is_platform_admin else shared_scope,
                ),
            )
        )
        .order_by(HomeCollection.name, HomeCollection.id)
    )
    rows = (await db.scalars(query)).all()
    if user.is_platform_admin:
        access_checks.note_power("home.readwrite", ALL_ORGS, subject="home_collections")
    allowed = {resource.key for resource in resources}
    return HomeResponse(
        resources=resources,
        collections=[await public_collection(db, row, user, allowed) for row in rows],
    )


async def get_collection(
    db: AsyncSession,
    user: UserPrincipal,
    collection_id: UUID,
    *,
    require_edit: bool = False,
) -> HomeCollection:
    row = await db.get(HomeCollection, collection_id)
    if row is None or not can_read_collection(row, user):
        raise HTTPException(404, "Collection not found")
    if require_edit and not can_edit_collection(row, user):
        raise HTTPException(403, "You cannot edit this collection")
    return row


async def save_collection(
    db: AsyncSession,
    user: UserPrincipal,
    data: HomeCollectionWrite,
    collection_id: UUID | None = None,
) -> HomeCollectionPublic:
    row = (
        await get_collection(db, user, collection_id, require_edit=True)
        if collection_id
        else None
    )
    if data.shared:
        if not user.is_platform_admin:
            raise HTTPException(
                403, "Only platform administrators can publish shared collections"
            )
        access_checks.note_power("home.readwrite", data.organization_id, subject="home_collections")
    if not data.shared and data.organization_id is not None:
        raise HTTPException(
            422, "Personal collections do not have an organization scope"
        )
    if data.organization_id is not None and not await db.get(
        Organization, data.organization_id
    ):
        raise HTTPException(422, "Organization not found")
    resources = await catalog(db, user)
    allowed = {resource.key for resource in resources}

    existing_keys = row.resource_keys if row is not None else []
    existing_hidden_keys = [key for key in existing_keys if key not in allowed]
    existing_key_set = set(existing_keys)
    unknown_keys = [
        key
        for key in data.resource_keys
        if key not in allowed and key not in existing_key_set
    ]
    if unknown_keys:
        raise HTTPException(
            422, "A selected resource is unavailable. Refresh Home and try again."
        )
    if (
        existing_hidden_keys
        and row is not None
        and collection_audience(row.shared, row.organization_id)
        != collection_audience(data.shared, data.organization_id)
    ):
        raise HTTPException(
            409,
            "This collection contains unavailable resources. Refresh Home and remove unavailable items before changing its audience.",
        )

    visible_resource_keys = [key for key in data.resource_keys if key in allowed]
    data.resource_keys = [
        *visible_resource_keys,
        *(key for key in existing_hidden_keys if key not in visible_resource_keys),
    ]
    if data.shared and data.organization_id:
        resource_map = {resource.key: resource for resource in resources}
        if any(
            resource_map[key].organization_id not in (None, data.organization_id)
            for key in visible_resource_keys
        ):
            raise HTTPException(
                422,
                "Organization collections can contain only that organization's or global resources",
            )
    if row is None:
        row = HomeCollection(owner_id=user.user_id)
        db.add(row)
    elif row.shared and not data.shared:
        row.owner_id = user.user_id
    for key, value in data.model_dump().items():
        setattr(row, key, value)
    await db.flush()
    return await public_collection(db, row, user, allowed)


async def save_preference(
    db: AsyncSession, user: UserPrincipal, resource_key: str, data: HomePreferenceWrite
) -> None:
    if resource_key not in {resource.key for resource in await catalog(db, user)}:
        raise HTTPException(404, "Resource not found")
    values: dict = {}
    if data.pinned is not None:
        values["pinned"] = data.pinned
    if data.opened:
        values["last_opened_at"] = datetime.now(timezone.utc)
    if values:
        statement = insert(HomeResourcePreference).values(
            user_id=user.user_id, resource_key=resource_key, **values
        )
        await db.execute(
            statement.on_conflict_do_update(
                index_elements=["user_id", "resource_key"], set_=values
            )
        )
