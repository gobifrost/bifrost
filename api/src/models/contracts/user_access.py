"""Contracts for the access map: what a person can do, and where."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel

PlaceKind = Literal["home", "organization", "managed_organizations", "platform"]
GrantVia = Literal["base", "additional"]

# A grant's scope is its permission domain's catalog scope.
GrantScope = Literal["per_organization", "platform_wide", "varies"]


class OrganizationRef(BaseModel):
    id: UUID
    name: str


class Place(BaseModel):
    """Somewhere a person's roles apply, with the label the screens show."""

    kind: PlaceKind
    organization_id: UUID | None = None
    organization_name: str | None = None
    label: str


class AccessGrantSource(BaseModel):
    """The role a permission is held through."""

    role_id: UUID
    role_name: str
    via: GrantVia


class AccessGrant(BaseModel):
    permission: str
    domain: str
    action: str
    scope: GrantScope
    sources: list[AccessGrantSource]


class AccessRow(BaseModel):
    place: Place
    grants: list[AccessGrant]


class UserAccessMap(BaseModel):
    user_id: UUID
    name: str | None = None
    email: str
    home_organization: OrganizationRef | None = None
    is_platform_admin: bool
    is_protected: bool
    # The held permissions that make the person protected.
    privileged_permissions: list[str]
    # Every place the person can act, home first and Global last.
    reach: list[Place]
    # Reach order; only places where something is held.
    rows: list[AccessRow]
