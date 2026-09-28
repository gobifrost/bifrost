"""``shared.workflow_access.user_can_access_workflow`` — the one predicate
shared by save-time tool validation (``_validate_user_tool_access``) and the
``/api/tools`` list filter (``WorkflowRepository.list_tools_for_filter``).
"""

from uuid import uuid4

from shared.workflow_access import user_can_access_workflow


def test_everyone_grants_regardless_of_roles_or_external():
    assert user_can_access_workflow(
        access_level="everyone",
        is_external=True,
        user_role_ids=set(),
        workflow_role_ids=set(),
    )


def test_authenticated_grants_non_external():
    assert user_can_access_workflow(
        access_level="authenticated",
        is_external=False,
        user_role_ids=set(),
        workflow_role_ids=set(),
    )


def test_authenticated_denies_external():
    assert not user_can_access_workflow(
        access_level="authenticated",
        is_external=True,
        user_role_ids=set(),
        workflow_role_ids=set(),
    )


def test_role_based_grants_on_role_intersection():
    shared_role = uuid4()
    assert user_can_access_workflow(
        access_level="role_based",
        is_external=False,
        user_role_ids={shared_role, uuid4()},
        workflow_role_ids={shared_role},
    )


def test_role_based_denies_without_intersection():
    assert not user_can_access_workflow(
        access_level="role_based",
        is_external=False,
        user_role_ids={uuid4()},
        workflow_role_ids={uuid4()},
    )


def test_role_based_denies_when_workflow_has_no_roles():
    assert not user_can_access_workflow(
        access_level="role_based",
        is_external=False,
        user_role_ids={uuid4()},
        workflow_role_ids=set(),
    )


def test_unknown_access_level_denies():
    assert not user_can_access_workflow(
        access_level="private",
        is_external=False,
        user_role_ids={uuid4()},
        workflow_role_ids=set(),
    )
