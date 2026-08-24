"""Private-deploy suppression policy — the single decision point for what a
private Solution deploy is NOT allowed to do.

Security invariant 9 of the private-solution-builder spec: a builder user must
not be able to escalate through generated source. A private Solution's source is
authored by an untrusted-to-the-platform actor (a builder user, often via a
model), so the deploy that materializes it must cause **no shared control-plane
side effects**. Everything the deploy writes has to stay inside the install's own
``solution_id`` scope.

The deployer asks this module once (:func:`is_private_install`) and then consults
the suppression predicates instead of scattering ``visibility == "private"``
checks through the reconcile. :class:`PrivateDeployViolation` backs the
post-condition assertion the deployer runs at the end of a private deploy — the
"checked again" belt-and-braces the spec requires, so a future edit that
reintroduces a shared write fails loudly rather than silently escalating.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.solutions import Solution

PRIVATE_VISIBILITY = "private"


class PrivateDeployViolation(Exception):
    """A private deploy produced a shared control-plane side effect.

    Raised by the deployer's post-condition check (roles / entity-role junctions
    / event sources created under a private install). Never expected in normal
    operation — it means a suppression predicate was bypassed.
    """


async def is_private_install(db: AsyncSession, solution_id: UUID) -> bool:
    """True when the install's ``visibility`` is ``private``.

    Read from the DB rather than a caller-supplied flag so the deployer cannot be
    told "this is shared" by an in-memory Solution object that was never
    persisted with that visibility.
    """
    visibility = (
        await db.execute(
            select(Solution.visibility).where(Solution.id == solution_id)
        )
    ).scalar_one_or_none()
    return visibility == PRIVATE_VISIBILITY


@dataclass(frozen=True)
class PrivateDeployPolicy:
    """What a deploy may write, given the install's visibility.

    One instance is resolved per deploy. ``private=False`` leaves every predicate
    False, so a shared Solution deploy behaves exactly as it did before this
    policy existed.
    """

    private: bool
    promotion: bool = False

    @property
    def strict_private(self) -> bool:
        """True for ordinary owner-authored preview deploys only."""
        return self.private and not self.promotion

    @property
    def suppress_role_materialization(self) -> bool:
        """Spec: "deploy cannot create, rename, assign, or delete
        organization/global roles" — requested role names stay portable source
        declarations, so unknown names must not auto-create global Role rows."""
        return self.strict_private

    @property
    def suppress_entity_role_junctions(self) -> bool:
        """Spec: "entity-role junctions remain empty because the private-owner
        gate supplies runtime access"."""
        return self.strict_private

    @property
    def suppress_event_activation(self) -> bool:
        """Spec: "schedules, events, autonomous agents, and generated workflows
        cannot activate shared runtime side effects"."""
        return self.private

    @property
    def suppress_connection_resolution(self) -> bool:
        """Spec: "deploy cannot create or read organization integration mappings,
        OAuth mappings, credentials, or connection grants" — declared connection
        requirements remain unresolved while private."""
        return self.strict_private

    @property
    def suppress_shared_config_writes(self) -> bool:
        """Spec: "deploy cannot create or mutate organization/global config
        values" — only exact-Solution-scoped config storage is permitted."""
        return self.private
