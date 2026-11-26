"""Keep non-origin input out of the framing policy."""

import pytest
from pydantic import ValidationError

from shared.models import AppFrameSigninSettings


@pytest.mark.parametrize("origin", [
    "http://halo.example.com",
    "https://*.example.com",
    "https://halo.example.com/path",
    "https://halo.example.com https://unrelated.example.com",
    "https://halo.example.com;frame-src",
    "https://user@halo.example.com",
])
def test_signin_settings_require_exact_https_origins(origin):
    with pytest.raises(ValidationError):
        AppFrameSigninSettings(allowed_origins=[origin])


def test_signin_settings_canonicalize_and_deduplicate_origins():
    settings = AppFrameSigninSettings(allowed_origins=[
        "https://Halo.Example.com:443",
        "https://halo.example.com",
    ])
    assert settings.allowed_origins == ["https://halo.example.com"]


@pytest.mark.asyncio
async def test_frame_signin_rejects_an_unverified_approval(monkeypatch):
    from unittest.mock import AsyncMock

    from shared import app_frame_signin

    redis = AsyncMock()
    redis.getdel.return_value = "{}"
    monkeypatch.setattr(app_frame_signin, "get_shared_redis", AsyncMock(return_value=redis))

    assert await app_frame_signin.consume_app_frame_signin_code("synthetic-verifier") is None
