"""Embed-secret raw material requires both settings and secrets authority."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.models.contracts.applications import EmbedSecretCreate
from src.routers.app_embed_secrets import create_embed_secret


@pytest.mark.asyncio
async def test_settings_only_cannot_create_or_receive_raw_embed_secret() -> None:
    """The raw-secret gate runs before generation or any database mutation."""
    app_id = uuid4()
    ctx = SimpleNamespace(db=MagicMock())
    current_user = SimpleNamespace(user_id=uuid4())
    settings_allowed_app = SimpleNamespace(organization_id=uuid4())
    secrets_denied = HTTPException(status_code=403, detail="Permission denied")

    with (
        patch(
            "src.routers.app_embed_secrets._require_embed_secret_permission",
            new=AsyncMock(side_effect=[settings_allowed_app, secrets_denied]),
        ) as require_permission,
        patch("src.routers.app_embed_secrets.secrets.token_urlsafe") as generate_secret,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await create_embed_secret(
                EmbedSecretCreate(name="Settings-only"),
                ctx,
                current_user,
                app_id,
            )

    assert exc_info.value.status_code == 403
    assert require_permission.await_args_list == [
        call(ctx, app_id, "settings.readwrite"),
        call(ctx, app_id, "secrets.read"),
    ]
    generate_secret.assert_not_called()
    ctx.db.add.assert_not_called()
