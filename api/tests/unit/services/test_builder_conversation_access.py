from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.services.builder.conversation_access import can_access_conversation


def _principal(user_id, *, scopes: set[str] | None = None):
    granted = scopes or set()
    return SimpleNamespace(
        user_id=user_id,
        is_platform_admin=False,
        is_external=False,
        is_provider_org=False,
        has_scope=lambda scope: scope in granted,
    )


@pytest.mark.asyncio
async def test_ordinary_chat_remains_owner_only() -> None:
    owner_id = uuid4()
    conversation = SimpleNamespace(
        id=uuid4(),
        user_id=owner_id,
        channel="web",
    )
    db = AsyncMock()

    assert await can_access_conversation(
        db,
        conversation=conversation,
        principal=_principal(owner_id),
        action="view",
    )
    assert not await can_access_conversation(
        db,
        conversation=conversation,
        principal=_principal(uuid4()),
        action="view",
    )
    db.scalar.assert_not_awaited()


@pytest.mark.asyncio
async def test_builder_collaborator_access_controls_read_and_edit() -> None:
    collaborator_id = uuid4()
    solution_id = uuid4()
    conversation = SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        channel="builder",
    )
    solution = SimpleNamespace(
        id=solution_id,
        visibility="private",
        owner_user_id=uuid4(),
    )
    db = AsyncMock()
    db.scalar.side_effect = [
        SimpleNamespace(solution_id=solution_id),
        "view",
        SimpleNamespace(solution_id=solution_id),
        "view",
    ]
    db.get.return_value = solution
    principal = _principal(collaborator_id)

    assert await can_access_conversation(
        db,
        conversation=conversation,
        principal=principal,
        action="view",
    )
    assert not await can_access_conversation(
        db,
        conversation=conversation,
        principal=principal,
        action="edit",
    )
