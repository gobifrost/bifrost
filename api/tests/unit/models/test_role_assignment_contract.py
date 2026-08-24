"""Contract tests for the boundary-aware role assignment models."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.models.contracts import (
    RoleAssignmentPublic,
    RoleAssignmentBoundaryBase,
    RoleAssignmentBoundaryInput,
    RoleAssignmentBoundaryPublic,
    RoleAssignmentCreate,
)


def _boundary(kind: str, *, organization_id=None, organization_group_id=None):
    return RoleAssignmentBoundaryInput(
        boundary_kind=kind,
        organization_id=organization_id,
        organization_group_id=organization_group_id,
    )


class TestRoleAssignmentBoundaryShape:
    def test_allows_multiple_orgs_groups_managed_and_platform(self) -> None:
        RoleAssignmentCreate(
            user_id=uuid4(),
            role_id=uuid4(),
            boundaries=[
                _boundary("organization", organization_id=uuid4()),
                _boundary("organization", organization_id=uuid4()),
                _boundary("organization_group", organization_group_id=uuid4()),
                _boundary("managed_organizations"),
                _boundary("platform"),
            ],
        )

    @pytest.mark.parametrize(
        ("kind", "organization_id", "organization_group_id", "message"),
        [
            ("organization", None, None, "organization boundaries require organization_id"),
            (
                "organization",
                uuid4(),
                uuid4(),
                "organization boundaries require organization_id",
            ),
            (
                "organization_group",
                None,
                None,
                "organization_group boundaries require organization_group_id",
            ),
            (
                "organization_group",
                uuid4(),
                uuid4(),
                "organization_group boundaries require organization_group_id",
            ),
            (
                "managed_organizations",
                uuid4(),
                None,
                "managed_organizations boundaries do not take organization identifiers",
            ),
            (
                "platform",
                None,
                uuid4(),
                "platform boundaries do not take organization identifiers",
            ),
        ],
    )
    def test_rejects_invalid_boundary_shapes(
        self,
        kind: str,
        organization_id,
        organization_group_id,
        message: str,
    ) -> None:
        with pytest.raises(ValidationError, match=message):
            RoleAssignmentBoundaryInput(
                boundary_kind=kind,
                organization_id=organization_id,
                organization_group_id=organization_group_id,
            )

    def test_rejects_duplicate_exact_organization_selection(self) -> None:
        org_id = uuid4()

        with pytest.raises(ValidationError, match="duplicate boundary selections"):
            RoleAssignmentCreate(
                user_id=uuid4(),
                role_id=uuid4(),
                boundaries=[
                    _boundary("organization", organization_id=org_id),
                    _boundary("organization", organization_id=org_id),
                ],
            )

    def test_rejects_duplicate_exact_group_selection(self) -> None:
        group_id = uuid4()

        with pytest.raises(ValidationError, match="duplicate boundary selections"):
            RoleAssignmentCreate(
                user_id=uuid4(),
                role_id=uuid4(),
                boundaries=[
                    _boundary("organization_group", organization_group_id=group_id),
                    _boundary("organization_group", organization_group_id=group_id),
                ],
            )

    def test_boundary_identity_includes_kind_and_target(self) -> None:
        boundary = RoleAssignmentBoundaryBase(
            boundary_kind="organization_group",
            organization_group_id=uuid4(),
        )

        assert boundary.identity()[0] == "organization_group"
        assert boundary.identity()[1] is None
        assert boundary.identity()[2] is not None


def test_role_assignment_public_round_trip() -> None:
    assignment = RoleAssignmentPublic(
        id=uuid4(),
        user_id=uuid4(),
        role_id=uuid4(),
        assigned_by_user_id=None,
        assigned_at=datetime.now(UTC),
        boundaries=[
            RoleAssignmentBoundaryPublic(
                id=uuid4(),
                boundary_kind="platform",
                organization_id=None,
                organization_group_id=None,
            )
        ],
    )

    data = assignment.model_dump()

    assert data["boundaries"][0]["boundary_kind"] == "platform"
    assert "assigned_by" not in data
