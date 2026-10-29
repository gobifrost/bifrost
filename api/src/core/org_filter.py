"""Organization query predicates for resolved scopes."""

from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, false, or_

from shared.access_checks import ALL_ORGS, NoteTarget


class OrgFilterType(Enum):
    """Types of organization filtering."""

    ALL = "all"
    GLOBAL_ONLY = "global"
    ORG_ONLY = "org_only"
    ORG_PLUS_GLOBAL = "org"


def org_filter_clause(
    org_column: Any,
    filter_type: OrgFilterType,
    filter_org_id: UUID | None,
) -> ColumnElement[bool] | None:
    """Build a SQL predicate for an already-authorized organization filter."""
    if filter_type is OrgFilterType.ALL:
        return None
    if filter_type is OrgFilterType.GLOBAL_ONLY:
        return org_column.is_(None)
    if filter_type is OrgFilterType.ORG_ONLY:
        if filter_org_id is None:
            return false()
        return org_column == filter_org_id
    if filter_org_id is None:
        return org_column.is_(None)
    return or_(org_column == filter_org_id, org_column.is_(None))


def filter_target(filter_type: OrgFilterType, filter_org_id: UUID | None) -> NoteTarget:
    """Return the access-check target represented by a resolved list filter."""
    if filter_type is OrgFilterType.ALL:
        return ALL_ORGS
    if filter_type is OrgFilterType.GLOBAL_ONLY:
        return None
    return filter_org_id
