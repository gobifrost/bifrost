"""Home uses the common resource visibility resolver as its access authority."""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, or_, select, true
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

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
from src.models.orm.agents import Agent
from src.models.orm.forms import Form
from src.models.orm.applications import Application
from shared.resource_access import ResourceAccessResolver, ResourceKind
from src.services.agent_write_policy import agent_query_in_reach, caller_user_id
from src.services.authorization.enforce import Caller, allows_permission, load_caller, permitted_organizations, require_permission, user_reach


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


def can_edit_collection(collection: HomeCollection, caller: Caller) -> bool:
    if not user_reach(caller).covers(collection.organization_id):
        return False
    own = collection.owner_id == caller_user_id(caller)
    if not collection.shared and own:
        return True
    permission = "home.readwrite" if own else "home.readwrite.all"
    return allows_permission(caller, permission, collection.organization_id)


def can_read_collection(collection: HomeCollection, caller: Caller) -> bool:
    if caller.ctx is None or not user_reach(caller).covers(collection.organization_id):
        return False
    if collection.owner_id == caller.ctx.user_id:
        return True
    if collection.shared and (
        collection.organization_id == caller.ctx.home_organization_id
        or (collection.organization_id is None and not caller.ctx.is_external)
    ):
        return True
    return allows_permission(caller, "home.read.all", collection.organization_id)


def collection_audience(
    shared: bool, organization_id: UUID | None
) -> tuple[bool, UUID | None]:
    return shared, organization_id


async def catalog(db: AsyncSession, caller: Caller) -> list[HomeResource]:
    """Reuse each resource's read gate; collections confer no resource access."""
    if caller.ctx is None:
        raise HTTPException(403, "Home requires a current user")
    reach = user_reach(caller)
    app_query = select(Application).options(defer(Application.logo_data), defer(Application.logo_thumbnail_data))
    form_query = select(Form).options(defer(Form.logo_data), defer(Form.logo_thumbnail_data)).where(Form.is_active.is_(True))
    app_reach = reach.where(Application.organization_id)
    form_reach = reach.where(Form.organization_id)
    if app_reach is not None:
        app_query = app_query.where(app_reach)
    if form_reach is not None:
        form_query = form_query.where(form_reach)
    access = ResourceAccessResolver(db, caller)
    apps = await access.filter_readable(
        ResourceKind.APP, list((await db.scalars(app_query)).all())
    )
    forms = await access.filter_readable(
        ResourceKind.FORM, list((await db.scalars(form_query)).all())
    )
    agents = await access.filter_readable(
        ResourceKind.AGENT,
        list((await db.scalars(
            agent_query_in_reach(caller).options(
                defer(Agent.logo_data), defer(Agent.logo_thumbnail_data)
            ).where(Agent.is_active.is_(True))
        )).all()),
    )
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
                HomeResourcePreference.user_id == caller.ctx.user_id
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
    db: AsyncSession, row: HomeCollection, caller: Caller, allowed: set[str]
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
        can_edit=can_edit_collection(row, caller),
    )


async def get_home(db: AsyncSession, user: UserPrincipal) -> HomeResponse:
    caller = await load_caller(db, user)
    if caller.ctx is None:
        raise HTTPException(403, "Home requires a current user")
    resources = await catalog(db, caller)
    shared_scope = or_(
        HomeCollection.organization_id == caller.ctx.home_organization_id,
        HomeCollection.organization_id.is_(None) if not caller.ctx.is_external else False,
    )
    read_all = permitted_organizations(caller, "home.read.all").where(HomeCollection.organization_id)
    reach = user_reach(caller).where(HomeCollection.organization_id)
    query = select(HomeCollection).where(
        or_(
            HomeCollection.owner_id == caller.ctx.user_id,
            and_(HomeCollection.shared.is_(True), shared_scope),
            true() if read_all is None else read_all,
        ),
        true() if reach is None else reach,
    ).order_by(HomeCollection.name, HomeCollection.id)
    rows = (await db.scalars(query)).all()
    allowed = {resource.key for resource in resources}
    return HomeResponse(
        resources=resources,
        collections=[await public_collection(db, row, caller, allowed) for row in rows],
    )


async def get_collection(
    db: AsyncSession,
    user: UserPrincipal,
    collection_id: UUID,
    *,
    require_edit: bool = False,
) -> HomeCollection:
    caller = await load_caller(db, user)
    row = await db.get(HomeCollection, collection_id)
    if row is None or not (
        can_read_collection(row, caller)
        or (require_edit and can_edit_collection(row, caller))
    ):
        raise HTTPException(404, "Collection not found")
    if require_edit and not can_edit_collection(row, caller):
        raise HTTPException(403, "You cannot edit this collection")
    return row


async def save_collection(
    db: AsyncSession,
    user: UserPrincipal,
    data: HomeCollectionWrite,
    collection_id: UUID | None = None,
) -> HomeCollectionPublic:
    caller = await load_caller(db, user)
    if caller.ctx is None:
        raise HTTPException(403, "Home requires a current user")
    row = (
        await get_collection(db, user, collection_id, require_edit=True)
        if collection_id
        else None
    )
    if data.shared and (row is None or not row.shared or row.organization_id != data.organization_id):
        require_permission(caller, "home.readwrite", data.organization_id)
        if not user_reach(caller).covers(data.organization_id):
            raise HTTPException(403, "Access denied")
    if not data.shared and data.organization_id is not None:
        raise HTTPException(
            422, "Personal collections do not have an organization scope"
        )
    if data.organization_id is not None and not await db.get(
        Organization, data.organization_id
    ):
        raise HTTPException(422, "Organization not found")
    resources = await catalog(db, caller)
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
        row = HomeCollection(owner_id=caller.ctx.user_id)
        db.add(row)
    elif row.shared and not data.shared:
        row.owner_id = caller.ctx.user_id
    for key, value in data.model_dump().items():
        setattr(row, key, value)
    await db.flush()
    return await public_collection(db, row, caller, allowed)


async def save_preference(
    db: AsyncSession, user: UserPrincipal, resource_key: str, data: HomePreferenceWrite
) -> None:
    caller = await load_caller(db, user)
    if caller.ctx is None:
        raise HTTPException(403, "Home requires a current user")
    if resource_key not in {resource.key for resource in await catalog(db, caller)}:
        raise HTTPException(404, "Resource not found")
    values: dict = {}
    if data.pinned is not None:
        values["pinned"] = data.pinned
    if data.opened:
        values["last_opened_at"] = datetime.now(timezone.utc)
    if values:
        statement = insert(HomeResourcePreference).values(
            user_id=caller.ctx.user_id, resource_key=resource_key, **values
        )
        await db.execute(
            statement.on_conflict_do_update(
                index_elements=["user_id", "resource_key"], set_=values
            )
        )
