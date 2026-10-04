"""Which organizations an operation reaches for a caller.

Kept free of the web framework so platform jobs, which run in the scheduler,
can store and apply a caller's reach.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import ColumnElement, and_, false, or_

from src.core.constants import PROVIDER_ORG_ID


@dataclass(frozen=True)
class OrgReach:
    """The organizations an operation reaches for a caller, for filtering
    lists (and the counts that go with them) before paging.

    ``everything``: every organization and Global (Platform Admin, or a
    superuser execution credential). Otherwise the union of explicit
    ``organization_ids`` (``organization`` boundaries, and the home org when
    the base role holds the permission), ``managed`` (every org except the
    provider org; never Global), and ``include_global`` (a ``platform``
    boundary, which covers Global rows only, not every organization).
    """

    everything: bool = False
    organization_ids: frozenset[UUID] = frozenset()
    managed: bool = False
    include_global: bool = False

    @property
    def is_empty(self) -> bool:
        return not (self.everything or self.organization_ids or self.managed or self.include_global)

    def covers(self, organization_id: UUID | None) -> bool:
        if self.everything:
            return True
        if organization_id is None:
            return self.include_global
        return organization_id in self.organization_ids or (
            self.managed and organization_id != PROVIDER_ORG_ID
        )

    def where(self, column) -> ColumnElement[bool] | None:
        """SQL predicate on an organization-id ``column``; None = no filter."""
        if self.everything:
            return None
        clauses: list[ColumnElement[bool]] = []
        if self.organization_ids:
            clauses.append(column.in_(self.organization_ids))
        if self.managed:
            clauses.append(and_(column.is_not(None), column != PROVIDER_ORG_ID))
        if self.include_global:
            clauses.append(column.is_(None))
        if not clauses:
            return false()
        return or_(*clauses)


EVERYTHING = OrgReach(everything=True)
NOWHERE = OrgReach()
