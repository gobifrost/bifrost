"""SCIM 2.0 service provider endpoint for identity provider connections.

Base URL (the "Tenant URL" an identity provider is configured with):
``/api/scim/v2/{identity_provider_id}``. Requests authenticate with the
connection's bearer token. Translation lives in
``src.services.identity_provisioning.scim``; the rules live in the
protocol-independent core.
"""

from __future__ import annotations

import json
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from src.core.db_deps import DbSession
from src.core.rate_limit import RateLimiter
from src.models.orm.identity_providers import (
    IdentityProvider,
    IdentityProviderGroup,
    IdentityProviderGroupMember,
    IdentityProviderUser,
)
from src.services.identity_provisioning import scim, tokens
from src.services.identity_provisioning.core import ProvisioningError, Provisioner
from src.services.identity_provisioning.scim import ScimError

router = APIRouter(
    prefix="/api/scim/v2/{identity_provider_id}",
    tags=["SCIM"],
    include_in_schema=False,
)

# Generous: a large Entra or Okta cycle sends bursts of user and membership
# changes. Applied per connection, before any database work.
_RATE_LIMIT = RateLimiter(max_requests=1200, window_seconds=60)
_MAX_BODY_BYTES = 1024 * 1024


def scim_response(body: Any, status_code: int = 200, *, location: str | None = None) -> Response:
    headers = {"Location": location} if location else None
    return JSONResponse(
        body, status_code=status_code, media_type=scim.SCIM_MEDIA_TYPE, headers=headers
    )


def scim_error_response(exc: ScimError) -> JSONResponse:
    headers = {"WWW-Authenticate": 'Bearer realm="SCIM"'} if exc.status == 401 else None
    return JSONResponse(
        exc.body(), status_code=exc.status, media_type=scim.SCIM_MEDIA_TYPE, headers=headers
    )


async def scim_connection(
    identity_provider_id: str, request: Request, db: DbSession
) -> IdentityProvider:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise ScimError(401, "A bearer token is required")
    try:
        connection_id = UUID(identity_provider_id)
    except ValueError:
        raise ScimError(401, "Invalid or expired token") from None
    try:
        await _RATE_LIMIT.check("scim", str(connection_id))
    except HTTPException as exc:
        if exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            raise ScimError(429, "Too many requests") from None
        raise
    connection = await tokens.authenticate(db, connection_id, token.strip())
    if connection is None or connection.source_type != "scim":
        raise ScimError(401, "Invalid or expired token")
    return connection


Connection = Annotated[IdentityProvider, Depends(scim_connection)]


def _base_url(connection: IdentityProvider) -> str:
    from src.config import get_settings

    return f"{get_settings().public_url.rstrip('/')}/api/scim/v2/{connection.id}"


async def _json_body(request: Request) -> Any:
    raw = await request.body()
    if len(raw) > _MAX_BODY_BYTES:
        raise ScimError(413, "The request body is too large", "tooLarge")
    try:
        return json.loads(raw)
    except ValueError:
        raise ScimError(400, "The request body is not valid JSON", "invalidSyntax") from None


def _paging(start_index: int, count: int | None) -> tuple[int, int]:
    start = max(start_index, 1)
    limit = scim.MAX_RESULTS if count is None else max(0, min(count, scim.MAX_RESULTS))
    return start, limit


async def _commit_and_emit(db: DbSession, provisioner: Provisioner) -> None:
    await db.commit()
    await provisioner.after_commit()


def _parse_id(resource_id: str) -> UUID:
    try:
        return UUID(resource_id)
    except ValueError:
        raise ScimError(404, "Resource not found") from None


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


@router.get("/ServiceProviderConfig")
async def service_provider_config(connection: Connection) -> Response:
    return scim_response(scim.service_provider_config(_base_url(connection)))


@router.get("/ResourceTypes")
async def resource_types(connection: Connection) -> Response:
    types = scim.resource_types(_base_url(connection))
    return scim_response(scim.list_response(types, len(types), 1))


@router.get("/ResourceTypes/{type_id}")
async def resource_type(type_id: str, connection: Connection) -> Response:
    for item in scim.resource_types(_base_url(connection)):
        if item["id"] == type_id:
            return scim_response(item)
    raise ScimError(404, "Resource type not found")


@router.get("/Schemas")
async def schemas(connection: Connection) -> Response:
    items = scim.schemas(_base_url(connection))
    return scim_response(scim.list_response(items, len(items), 1))


@router.get("/Schemas/{schema_id}")
async def schema(schema_id: str, connection: Connection) -> Response:
    for item in scim.schemas(_base_url(connection)):
        if item["id"] == schema_id:
            return scim_response(item)
    raise ScimError(404, "Schema not found")


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


async def _user_link(provisioner: Provisioner, user_id: str) -> IdentityProviderUser:
    link = await provisioner.get_user_link(_parse_id(user_id))
    if link is None:
        raise ScimError(404, "User not found")
    return link


async def _locked_user_link(provisioner: Provisioner, user_id: str) -> IdentityProviderUser:
    """The link, locked for a write: a change computed from it can't
    overwrite a concurrent one."""
    link = await provisioner.lock_user_link(_parse_id(user_id))
    if link is None:
        raise ScimError(404, "User not found")
    return link


@router.get("/Users")
async def list_users(
    connection: Connection,
    db: DbSession,
    filter_: Annotated[str | None, Query(alias="filter")] = None,
    start_index: Annotated[int, Query(alias="startIndex")] = 1,
    count: int | None = None,
    attributes: str | None = None,
    excluded_attributes: Annotated[str | None, Query(alias="excludedAttributes")] = None,
) -> Response:
    criteria = scim.parse_filter(filter_, scim.USER_FILTER_ATTRIBUTES)
    stmt = select(IdentityProviderUser).where(
        IdentityProviderUser.identity_provider_id == connection.id
    )
    if criteria is not None:
        if criteria.attribute == "userName":
            # userName is case-insensitive (RFC 7643 §4.1.1).
            stmt = stmt.where(
                func.lower(IdentityProviderUser.user_name) == criteria.value.lower()
            )
        elif criteria.attribute == "externalId":
            stmt = stmt.where(IdentityProviderUser.external_id == criteria.value)
        else:
            try:
                stmt = stmt.where(IdentityProviderUser.id == UUID(criteria.value))
            except ValueError:
                return scim_response(scim.list_response([], 0, 1))
    start, limit = _paging(start_index, count)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    links = (
        await db.scalars(
            stmt.order_by(IdentityProviderUser.created_at, IdentityProviderUser.id)
            .offset(start - 1)
            .limit(limit)
        )
    ).all()
    base_url = _base_url(connection)
    resources = [
        scim.project(scim.user_response(link, base_url), attributes, excluded_attributes)
        for link in links
    ]
    return scim_response(scim.list_response(resources, total, start))


@router.post("/Users")
async def create_user(request: Request, connection: Connection, db: DbSession) -> Response:
    resource, unknown = scim.parse_user(await _json_body(request))
    provisioner = Provisioner(db, connection)
    try:
        link = await provisioner.create_user(scim.user_record(resource, unknown))
    except ProvisioningError as exc:
        raise scim.from_provisioning_error(exc) from None
    await db.refresh(link)
    base_url = _base_url(connection)
    body = scim.user_response(link, base_url)
    await _commit_and_emit(db, provisioner)
    return scim_response(body, 201, location=body["meta"]["location"])


@router.get("/Users/{user_id}")
async def get_user(
    user_id: str,
    connection: Connection,
    db: DbSession,
    attributes: str | None = None,
    excluded_attributes: Annotated[str | None, Query(alias="excludedAttributes")] = None,
) -> Response:
    link = await _user_link(Provisioner(db, connection), user_id)
    body = scim.user_response(link, _base_url(connection))
    return scim_response(scim.project(body, attributes, excluded_attributes))


@router.put("/Users/{user_id}")
async def replace_user(
    user_id: str, request: Request, connection: Connection, db: DbSession
) -> Response:
    resource, unknown = scim.parse_user(await _json_body(request))
    provisioner = Provisioner(db, connection)
    link = await _locked_user_link(provisioner, user_id)
    try:
        link = await provisioner.replace_user(link, scim.user_record(resource, unknown))
    except ProvisioningError as exc:
        raise scim.from_provisioning_error(exc) from None
    await db.refresh(link)
    body = scim.user_response(link, _base_url(connection))
    await _commit_and_emit(db, provisioner)
    return scim_response(body)


@router.patch("/Users/{user_id}")
async def patch_user(
    user_id: str, request: Request, connection: Connection, db: DbSession
) -> Response:
    patch = await _json_body(request)
    provisioner = Provisioner(db, connection)
    link = await _locked_user_link(provisioner, user_id)
    resource, unknown = scim.apply_user_patch(link, patch)
    try:
        link = await provisioner.replace_user(link, scim.user_record(resource, unknown))
    except ProvisioningError as exc:
        raise scim.from_provisioning_error(exc) from None
    await db.refresh(link)
    body = scim.user_response(link, _base_url(connection))
    await _commit_and_emit(db, provisioner)
    return scim_response(body)


@router.delete("/Users/{user_id}")
async def delete_user(user_id: str, connection: Connection, db: DbSession) -> Response:
    provisioner = Provisioner(db, connection)
    link = await _locked_user_link(provisioner, user_id)
    try:
        await provisioner.delete_user(link)
    except ProvisioningError as exc:
        raise scim.from_provisioning_error(exc) from None
    await _commit_and_emit(db, provisioner)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Groups
# ---------------------------------------------------------------------------


async def _group(provisioner: Provisioner, group_id: str) -> IdentityProviderGroup:
    group = await provisioner.get_group(_parse_id(group_id))
    if group is None:
        raise ScimError(404, "Group not found")
    return group


async def _locked_group(
    provisioner: Provisioner, group_id: str, *, exclusive_policy: bool = False
) -> IdentityProviderGroup:
    """The group, locked for a write: a membership change computed from it
    can't overwrite a concurrent one."""
    group = await provisioner.lock_group(_parse_id(group_id), exclusive_policy=exclusive_policy)
    if group is None:
        raise ScimError(404, "Group not found")
    return group


def _wants_members(attributes: str | None, excluded_attributes: str | None) -> bool:
    excluded = {a.strip().lower() for a in (excluded_attributes or "").split(",")}
    included = {a.strip().split(".")[0].lower() for a in (attributes or "").split(",") if a.strip()}
    if "members" in excluded:
        return False
    return not included or "members" in included


async def _members(db: DbSession, group_ids: list[UUID]) -> dict[UUID, list[tuple[UUID, str]]]:
    members: dict[UUID, list[tuple[UUID, str]]] = {group_id: [] for group_id in group_ids}
    if not group_ids:
        return members
    rows = await db.execute(
        select(
            IdentityProviderGroupMember.group_id,
            IdentityProviderUser.id,
            IdentityProviderUser.user_name,
        )
        .join(
            IdentityProviderUser,
            IdentityProviderUser.id == IdentityProviderGroupMember.identity_provider_user_id,
        )
        .where(IdentityProviderGroupMember.group_id.in_(group_ids))
        .order_by(IdentityProviderUser.user_name)
    )
    for group_id, link_id, user_name in rows.all():
        members[group_id].append((link_id, user_name))
    return members


async def _group_body(
    db: DbSession,
    group: IdentityProviderGroup,
    base_url: str,
    attributes: str | None = None,
    excluded_attributes: str | None = None,
) -> dict[str, Any]:
    members = None
    if _wants_members(attributes, excluded_attributes):
        members = (await _members(db, [group.id]))[group.id]
    body = scim.group_response(group, members, base_url)
    return scim.project(body, attributes, excluded_attributes)


@router.get("/Groups")
async def list_groups(
    connection: Connection,
    db: DbSession,
    filter_: Annotated[str | None, Query(alias="filter")] = None,
    start_index: Annotated[int, Query(alias="startIndex")] = 1,
    count: int | None = None,
    attributes: str | None = None,
    excluded_attributes: Annotated[str | None, Query(alias="excludedAttributes")] = None,
) -> Response:
    criteria = scim.parse_filter(filter_, scim.GROUP_FILTER_ATTRIBUTES)
    stmt = select(IdentityProviderGroup).where(
        IdentityProviderGroup.identity_provider_id == connection.id
    )
    if criteria is not None:
        if criteria.attribute == "displayName":
            stmt = stmt.where(IdentityProviderGroup.display_name == criteria.value)
        elif criteria.attribute == "externalId":
            stmt = stmt.where(IdentityProviderGroup.external_id == criteria.value)
        else:
            try:
                stmt = stmt.where(IdentityProviderGroup.id == UUID(criteria.value))
            except ValueError:
                return scim_response(scim.list_response([], 0, 1))
    start, limit = _paging(start_index, count)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    groups = (
        await db.scalars(
            stmt.order_by(IdentityProviderGroup.created_at, IdentityProviderGroup.id)
            .offset(start - 1)
            .limit(limit)
        )
    ).all()
    base_url = _base_url(connection)
    members: dict[UUID, list[tuple[UUID, str]]] = {}
    if _wants_members(attributes, excluded_attributes):
        members = await _members(db, [group.id for group in groups])
    resources = [
        scim.project(
            scim.group_response(group, members.get(group.id) if members else None, base_url),
            attributes,
            excluded_attributes,
        )
        for group in groups
    ]
    return scim_response(scim.list_response(resources, total, start))


@router.post("/Groups")
async def create_group(request: Request, connection: Connection, db: DbSession) -> Response:
    resource = scim.parse_group(await _json_body(request))
    provisioner = Provisioner(db, connection)
    try:
        group = await provisioner.create_group(
            scim.group_record(resource), scim.group_member_ids(resource)
        )
    except ProvisioningError as exc:
        raise scim.from_provisioning_error(exc) from None
    await db.refresh(group)
    body = await _group_body(db, group, _base_url(connection))
    await _commit_and_emit(db, provisioner)
    return scim_response(body, 201, location=body["meta"]["location"])


@router.get("/Groups/{group_id}")
async def get_group(
    group_id: str,
    connection: Connection,
    db: DbSession,
    attributes: str | None = None,
    excluded_attributes: Annotated[str | None, Query(alias="excludedAttributes")] = None,
) -> Response:
    group = await _group(Provisioner(db, connection), group_id)
    body = await _group_body(
        db, group, _base_url(connection), attributes, excluded_attributes
    )
    return scim_response(body)


@router.put("/Groups/{group_id}")
async def replace_group(
    group_id: str, request: Request, connection: Connection, db: DbSession
) -> Response:
    resource = scim.parse_group(await _json_body(request))
    provisioner = Provisioner(db, connection)
    group = await _locked_group(provisioner, group_id)
    try:
        await provisioner.replace_group(group, scim.group_record(resource))
        await provisioner.set_group_members(group, scim.group_member_ids(resource))
    except ProvisioningError as exc:
        raise scim.from_provisioning_error(exc) from None
    await db.refresh(group)
    body = await _group_body(db, group, _base_url(connection))
    await _commit_and_emit(db, provisioner)
    return scim_response(body)


@router.patch("/Groups/{group_id}")
async def patch_group(
    group_id: str, request: Request, connection: Connection, db: DbSession
) -> Response:
    patch = await _json_body(request)
    provisioner = Provisioner(db, connection)
    group = await _locked_group(provisioner, group_id)
    current_members = await provisioner.group_member_ids(group.id)
    resource = scim.apply_group_patch(group, current_members, patch)
    try:
        await provisioner.replace_group(group, scim.group_record(resource))
        await provisioner.set_group_members(group, scim.group_member_ids(resource))
    except ProvisioningError as exc:
        raise scim.from_provisioning_error(exc) from None
    await _commit_and_emit(db, provisioner)
    # Entra and Okta ignore a PATCH body; 204 avoids serializing large groups.
    return Response(status_code=204)


@router.delete("/Groups/{group_id}")
async def delete_group(group_id: str, connection: Connection, db: DbSession) -> Response:
    provisioner = Provisioner(db, connection)
    group = await _locked_group(provisioner, group_id, exclusive_policy=True)
    try:
        await provisioner.delete_group(group)
    except ProvisioningError as exc:
        raise scim.from_provisioning_error(exc) from None
    await _commit_and_emit(db, provisioner)
    return Response(status_code=204)
