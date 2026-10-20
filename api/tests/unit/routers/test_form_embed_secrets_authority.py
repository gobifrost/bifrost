"""Embed-secret metadata is configuration, never raw secret material."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch
from uuid import uuid4

import pytest

from src.routers.form_embed_secrets import list_form_embed_secrets


@pytest.mark.asyncio
async def test_settings_authority_can_list_embed_secret_metadata() -> None:
    """Listing metadata must not require the separate raw-secret permission."""
    form_id = uuid4()
    user = SimpleNamespace(user_id=uuid4())
    record = SimpleNamespace(
        id=uuid4(),
        name="Contoso webhook",
        is_active=True,
        hmac_scheme="shopify",
        created_at=datetime.now(timezone.utc),
    )
    query_result = MagicMock()
    query_result.scalars.return_value.all.return_value = [record]
    ctx = SimpleNamespace(db=SimpleNamespace(execute=AsyncMock(return_value=query_result)))

    with patch(
        "src.routers.form_embed_secrets._require_embed_secret_permission",
        new=AsyncMock(return_value=SimpleNamespace()),
    ) as require_permission:
        result = await list_form_embed_secrets(ctx, user, form_id)

    assert require_permission.await_args_list == [
        call(ctx, user, form_id, "settings.readwrite"),
    ]
    assert result[0].model_dump(exclude={"id", "created_at"}) == {
        "name": "Contoso webhook",
        "is_active": True,
        "hmac_scheme": "shopify",
    }
