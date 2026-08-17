"""Pure contract tests for private Solution access decisions."""

from uuid import uuid4

import pytest

from src.services.solutions.access import (
    SolutionAction,
    can_access_solution,
)


@pytest.mark.parametrize(
    "action",
    [
        SolutionAction.VIEW,
        SolutionAction.EDIT,
        SolutionAction.BUILD,
        SolutionAction.RUN,
        SolutionAction.MANAGE,
    ],
)
def test_private_owner_can_work_on_solution(action: SolutionAction) -> None:
    owner = uuid4()
    assert can_access_solution(
        action=action,
        visibility="private",
        owner_user_id=owner,
        actor_user_id=owner,
        is_platform_admin=False,
        is_external=False,
    )


def test_support_access_is_deliberate_and_external_users_never_receive_it() -> None:
    owner = uuid4()
    support_user = uuid4()
    common = dict(
        action=SolutionAction.EDIT,
        visibility="private",
        owner_user_id=owner,
        actor_user_id=support_user,
        is_platform_admin=False,
    )

    assert can_access_solution(**common, is_external=False, can_support=True)
    assert not can_access_solution(**common, is_external=True, can_support=True)


def test_view_collaborator_cannot_edit_but_editor_can() -> None:
    common = dict(
        visibility="private",
        owner_user_id=uuid4(),
        actor_user_id=uuid4(),
        is_platform_admin=False,
        is_external=False,
    )

    assert can_access_solution(
        **common, action=SolutionAction.VIEW, collaborator_access="view"
    )
    assert not can_access_solution(
        **common, action=SolutionAction.EDIT, collaborator_access="view"
    )
    assert can_access_solution(
        **common, action=SolutionAction.EDIT, collaborator_access="edit"
    )


def test_only_platform_admin_promotes_private_solution() -> None:
    owner = uuid4()
    common = dict(
        action=SolutionAction.PROMOTE,
        visibility="private",
        owner_user_id=owner,
        is_external=False,
    )

    assert not can_access_solution(
        **common, actor_user_id=owner, is_platform_admin=False
    )
    assert can_access_solution(
        **common, actor_user_id=uuid4(), is_platform_admin=True
    )
