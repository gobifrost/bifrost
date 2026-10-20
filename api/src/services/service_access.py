"""Current-caller visibility for supervised service definitions."""
from __future__ import annotations

from sqlalchemy import and_, true

from src.models.orm.services import ServiceDefinition
from src.services.authorization.enforce import (
    Caller,
    allows_permission,
    permitted_organizations,
    require_named_permission,
    user_reach,
)


def can_access_service(caller: Caller, service: ServiceDefinition, *, write: bool = False) -> bool:
    if not user_reach(caller).covers(service.organization_id):
        return False
    return allows_permission(
        caller, "platform.readwrite" if write else "platform.read", service.organization_id
    )


def require_service_access(caller: Caller, service: ServiceDefinition, *, write: bool = False) -> None:
    if not user_reach(caller).covers(service.organization_id):
        from src.services.authorization.enforce import PermissionDenied
        raise PermissionDenied("platform.readwrite" if write else "platform.read")
    require_named_permission(
        caller, "platform.readwrite" if write else "platform.read", service.organization_id
    )


def service_visibility_filter(caller: Caller):
    reach = user_reach(caller).where(ServiceDefinition.organization_id)
    permitted = permitted_organizations(caller, "platform.read").where(ServiceDefinition.organization_id)
    return and_(
        true() if reach is None else reach,
        true() if permitted is None else permitted,
    )
