"""Unit tests for the central Solution access gate (private visibility rules)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.services.solutions.access import (
    VISIBILITY_PRIVATE,
    VISIBILITY_SHARED,
    SolutionAction,
    can_access_solution,
    visible_solutions_criterion,
)

OWNER = uuid4()
OTHER = uuid4()

CONTENT_ACTIONS = [
    SolutionAction.VIEW,
    SolutionAction.EDIT,
    SolutionAction.BUILD,
    SolutionAction.RUN,
]


def _decide(action, *, visibility, owner=OWNER, actor, admin=False, external=False):
    return can_access_solution(
        action=action,
        visibility=visibility,
        owner_user_id=owner,
        actor_user_id=actor,
        is_platform_admin=admin,
        is_external=external,
    )


@pytest.mark.parametrize("action", CONTENT_ACTIONS)
def test_private_owner_allowed(action):
    assert _decide(action, visibility=VISIBILITY_PRIVATE, actor=OWNER)


@pytest.mark.parametrize("action", CONTENT_ACTIONS)
def test_private_other_user_denied(action):
    assert not _decide(action, visibility=VISIBILITY_PRIVATE, actor=OTHER)


@pytest.mark.parametrize("action", CONTENT_ACTIONS)
def test_private_platform_admin_denied_content_access(action):
    """Admins do not see or run private content; their surface is promotion."""
    assert not _decide(action, visibility=VISIBILITY_PRIVATE, actor=OTHER, admin=True)


@pytest.mark.parametrize("action", CONTENT_ACTIONS)
def test_private_external_denied_even_as_owner(action):
    assert not _decide(
        action, visibility=VISIBILITY_PRIVATE, actor=OWNER, external=True
    )


@pytest.mark.parametrize("action", CONTENT_ACTIONS)
def test_shared_defers_to_downstream_authz(action):
    assert _decide(action, visibility=VISIBILITY_SHARED, actor=OTHER)


def test_private_orphaned_owner_admits_nobody():
    """Owner deleted (SET NULL): nobody gets content access until break-glass."""
    for action in CONTENT_ACTIONS:
        assert not _decide(
            action, visibility=VISIBILITY_PRIVATE, owner=None, actor=OWNER
        )


def test_promote_is_admin_only_and_private_only():
    assert _decide(
        SolutionAction.PROMOTE, visibility=VISIBILITY_PRIVATE, actor=OTHER, admin=True
    )
    # The owner may request promotion but may not perform it.
    assert not _decide(SolutionAction.PROMOTE, visibility=VISIBILITY_PRIVATE, actor=OWNER)
    # Promote is meaningless on an already-shared Solution.
    assert not _decide(
        SolutionAction.PROMOTE, visibility=VISIBILITY_SHARED, actor=OTHER, admin=True
    )
    # An external principal never promotes, admin flag or not.
    assert not _decide(
        SolutionAction.PROMOTE,
        visibility=VISIBILITY_PRIVATE,
        actor=OTHER,
        admin=True,
        external=True,
    )


def test_list_criterion_hides_foreign_private_rows():
    crit = visible_solutions_criterion(actor_user_id=OWNER, is_external=False)
    sql = str(crit)
    assert "visibility" in sql and "owner_user_id" in sql


def test_list_criterion_external_sees_only_shared():
    crit = visible_solutions_criterion(actor_user_id=OWNER, is_external=True)
    sql = str(crit)
    assert "visibility" in sql and "owner_user_id" not in sql


def test_list_criterion_anonymous_sees_only_shared():
    crit = visible_solutions_criterion(actor_user_id=None, is_external=False)
    assert "owner_user_id" not in str(crit)
