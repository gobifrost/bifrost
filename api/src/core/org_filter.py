"""
Organization Filter Helper

Provides consistent organization filtering logic across endpoints.
Org scoping is selected via the `scope` query parameter.

Scope Parameter Values:
- Not sent / omitted → show all (no filter) - superusers only
- "global" → filter to organization_id IS NULL only
- "{uuid}" → filter to specific org + global records
"""

from enum import Enum
from uuid import UUID

from src.core.principal import UserPrincipal


class OrgFilterType(Enum):
    """Types of organization filtering."""

    ALL = "all"  # No filter, show everything (superuser only)
    GLOBAL_ONLY = "global"  # Only org_id IS NULL
    ORG_ONLY = "org_only"  # Only specific org, NO global fallback (platform admin selecting org)
    ORG_PLUS_GLOBAL = "org"  # Specific org + global records (org users only)


def resolve_org_filter(
    user: UserPrincipal,
    scope: str | None = None,
) -> tuple[OrgFilterType, UUID | None]:
    """
    Resolve organization filter for list queries.

    This helper provides consistent organization filtering logic:
    - Superusers can view all data (scope omitted), global only (scope=global),
      or ONLY a specific org's data (scope={uuid}) - no global fallback
    - Org users always see their org's data + global records (scope is ignored)

    Args:
        user: The authenticated user principal
        scope: Filter scope - None (all), "global", or org UUID string

    Returns:
        tuple of (filter_type, org_id):
        - filter_type: OrgFilterType indicating how to filter
        - org_id: The organization UUID to filter by (only for ORG_PLUS_GLOBAL)

    Examples:
        Superuser with scope omitted:
            -> (ALL, None) - show all records (no org filter at all)

        Superuser with scope="global":
            -> (GLOBAL_ONLY, None) - show only global records (org_id IS NULL)

        Superuser with scope="{uuid}":
            -> (ORG_ONLY, uuid) - show ONLY that org's records (no global)

        Org user (any scope value):
            -> (ORG_PLUS_GLOBAL, user.organization_id) - always show their org + global

        Org user with no org assigned:
            -> (GLOBAL_ONLY, None) - only global records visible (edge case)

    Raises:
        ValueError: If scope is not a valid UUID or "global"
    """
    if user.is_superuser:
        if scope is None or scope == "":
            # Superuser with no filter - show ALL records
            return (OrgFilterType.ALL, None)
        elif scope == "global":
            # Superuser filtering to global only
            return (OrgFilterType.GLOBAL_ONLY, None)
        else:
            # Superuser filtering by specific org - ONLY that org (no global)
            try:
                org_uuid = UUID(scope)
                return (OrgFilterType.ORG_ONLY, org_uuid)
            except ValueError:
                raise ValueError(f"Invalid scope value: {scope}")
    else:
        # Org users: always filter to their organization, ignore the scope parameter
        if user.organization_id is not None:
            return (OrgFilterType.ORG_PLUS_GLOBAL, user.organization_id)
        else:
            # Edge case: org user with no org assigned sees only global
            return (OrgFilterType.GLOBAL_ONLY, None)


def resolve_target_org(
    user: UserPrincipal,
    scope: str | None,
    default_org_id: UUID | None = None,
) -> UUID | None:
    """
    Resolve target organization ID for write operations.

    This helper provides consistent organization targeting for create/update/delete:
    - Superusers can target any org via scope parameter
    - Non-superusers always target their own org (scope is ignored)

    Args:
        user: The authenticated user principal
        scope: Target scope - None (use default), "global", or org UUID string
        default_org_id: Default org ID when scope is None (usually from context)

    Returns:
        UUID of target organization, or None for global scope

    Examples:
        Superuser with scope=None:
            -> default_org_id (usually their context org or None)

        Superuser with scope="global":
            -> None (targets global/platform-level resources)

        Superuser with scope="{uuid}":
            -> UUID (targets specific org)

        Non-superuser (any scope value):
            -> user.organization_id (always their own org, scope ignored)

    Raises:
        ValueError: If scope is not a valid UUID or "global"
    """
    if user.is_superuser:
        if scope is None:
            return default_org_id
        if scope == "global":
            return None
        try:
            return UUID(scope)
        except ValueError:
            raise ValueError(f"Invalid scope value: {scope}")
    else:
        # Non-superusers always use their own org, scope is ignored
        return user.organization_id
