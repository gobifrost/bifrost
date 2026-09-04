from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.user_invites import InviteStatus
from src.models.orm import User
from src.services.user_invite_service import UserInviteService


@pytest.mark.asyncio
async def test_statuses_for_resolves_a_page_with_two_queries() -> None:
    registered_id = uuid4()
    oauth_id = uuid4()
    pending_id = uuid4()
    expired_id = uuid4()
    never_invited_id = uuid4()
    users = cast(
        list[User],
        [
            SimpleNamespace(id=registered_id, is_registered=True),
            SimpleNamespace(id=oauth_id, is_registered=False),
            SimpleNamespace(id=pending_id, is_registered=False),
            SimpleNamespace(id=expired_id, is_registered=False),
            SimpleNamespace(id=never_invited_id, is_registered=False),
        ],
    )

    oauth_result = MagicMock()
    oauth_result.scalars.return_value.all.return_value = [oauth_id]
    invite_result = MagicMock()
    invite_result.scalars.return_value.all.return_value = [
        SimpleNamespace(
            user_id=pending_id,
            revoked_at=None,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ),
        SimpleNamespace(
            user_id=expired_id,
            revoked_at=None,
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        ),
    ]
    session = AsyncMock(spec=AsyncSession)
    session.execute.side_effect = [oauth_result, invite_result]

    statuses = await UserInviteService(session).statuses_for(users)

    assert statuses == {
        registered_id: InviteStatus.ACTIVE,
        oauth_id: InviteStatus.ACTIVE,
        pending_id: InviteStatus.PENDING,
        expired_id: InviteStatus.EXPIRED,
        never_invited_id: InviteStatus.NEVER_INVITED,
    }
    assert session.execute.await_count == 2
