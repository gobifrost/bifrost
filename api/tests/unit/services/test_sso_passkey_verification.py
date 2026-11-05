"""Passkey login enforces policy after credential proof, before token minting."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, Response

from src.models.contracts.passkeys import PasskeyAuthVerifyRequest
from src.routers.passkeys import verify_authentication


@pytest.mark.asyncio
@pytest.mark.parametrize('allowed', [False, True])
async def test_passkey_policy_is_checked_before_tokens(allowed):
    db = AsyncMock()
    user = SimpleNamespace(id='proved-user')
    request = PasskeyAuthVerifyRequest(challenge_id='challenge', credential={})
    with (
        patch('src.routers.passkeys.PasskeyService') as service,
        patch('src.routers.passkeys._passkey_allowed_for', AsyncMock(return_value=allowed)) as policy,
        patch('src.routers.passkeys._generate_login_tokens', AsyncMock(return_value='tokens')) as tokens,
    ):
        service.return_value.verify_authentication = AsyncMock(return_value=user)
        if allowed:
            assert await verify_authentication(request, Response(), db) == 'tokens'
            tokens.assert_awaited_once()
        else:
            with pytest.raises(HTTPException) as refused:
                await verify_authentication(request, Response(), db)
            assert refused.value.status_code == 403
            tokens.assert_not_awaited()
            db.commit.assert_not_awaited()
        policy.assert_awaited_once_with(db, user)
