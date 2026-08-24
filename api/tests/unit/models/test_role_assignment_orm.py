"""ORM contract tests for the new role-assignment persistence tables."""

from __future__ import annotations

from src.models.orm import (
    OrganizationGroup,
    OrganizationGroupMembership,
    RoleAssignment,
    RoleAssignmentBoundary,
    SolutionRoleGrant,
)


def _constraint_names(table) -> set[str]:
    return {constraint.name for constraint in table.constraints if constraint.name}


def _index_names(table) -> set[str]:
    return {index.name for index in table.indexes if index.name}


def test_role_assignment_table_has_clean_parent_key() -> None:
    assert RoleAssignment.__tablename__ == "role_assignments"

    constraint_names = _constraint_names(RoleAssignment.__table__)
    index_names = _index_names(RoleAssignment.__table__)

    assert "uq_role_assignments_user_role" in constraint_names
    assert "ix_role_assignments_user" in index_names
    assert "ix_role_assignments_role" in index_names


def test_role_assignment_boundary_table_models_all_boundary_shapes() -> None:
    constraint_names = _constraint_names(RoleAssignmentBoundary.__table__)
    index_names = _index_names(RoleAssignmentBoundary.__table__)

    assert "ck_role_assignment_boundaries_kind" in constraint_names
    assert "ck_role_assignment_boundaries_shape" in constraint_names
    assert "uq_role_assignment_boundaries_organization" in index_names
    assert "uq_role_assignment_boundaries_organization_group" in index_names
    assert "uq_role_assignment_boundaries_managed_organizations" in index_names
    assert "uq_role_assignment_boundaries_platform" in index_names


def test_organization_group_membership_has_composite_key() -> None:
    assert OrganizationGroup.__tablename__ == "organization_groups"
    assert OrganizationGroupMembership.__tablename__ == "organization_group_members"

    constraint_names = _constraint_names(OrganizationGroupMembership.__table__)
    index_names = _index_names(OrganizationGroupMembership.__table__)

    assert "uq_organization_group_members_group_org" in constraint_names
    assert "ix_organization_group_members_organization" in index_names


def test_solution_role_grant_table_has_role_grant_guardrails() -> None:
    constraint_names = _constraint_names(SolutionRoleGrant.__table__)
    index_names = _index_names(SolutionRoleGrant.__table__)

    assert "uq_solution_role_grants_solution_role" in constraint_names
    assert "ck_solution_role_grants_access" in constraint_names
    assert "ix_solution_role_grants_solution" in index_names
    assert "ix_solution_role_grants_role" in index_names
