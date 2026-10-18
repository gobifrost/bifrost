"""Explain recorded requirements that a new User-base identity would lose.

Uses the same recorded evidence as Recommended Access. It never grants reach
or role membership, and recorded absence cannot prove a workflow needs none.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from shared.access_checks import ALL_ORGS
from src.models.orm.workflows import Workflow
from src.services.recommended_access import CheckedAction, _recorded_actions


def new_identity_requirements(
    actions: list[CheckedAction], organization_id: UUID | None
) -> list[str]:
    requirements: set[str] = set()
    for action in actions:
        if action.kind in {"scope_switch", "child_run"}:
            if action.target == ALL_ORGS:
                requirements.add(
                    "Observed access spans all organizations; a new User-base identity does not have that reach"
                )
            elif isinstance(action.target, UUID) and action.target != organization_id:
                requirements.add(
                    f"Observed access reaches organization {action.target}; a new identity in this workflow's organization does not"
                )
        elif action.kind == "policy":
            for missing in action.missing:
                if missing.startswith("role:"):
                    requirements.add(
                        f"A recorded resource policy requires {missing}; a new identity has no additional roles"
                    )
                elif missing.startswith("claim:"):
                    requirements.add(
                        f"A recorded resource policy requires {missing}; identity permissions do not supply personal claim data"
                    )
    return sorted(requirements)


async def recorded_identity_requirements(
    db: AsyncSession,
    workflow: Workflow,
    *,
    window_days: int,
) -> list[str]:
    actions, _runs = await _recorded_actions(
        db,
        workflow.id,
        datetime.now(timezone.utc) - timedelta(days=window_days),
    )
    return new_identity_requirements(actions, workflow.organization_id)
