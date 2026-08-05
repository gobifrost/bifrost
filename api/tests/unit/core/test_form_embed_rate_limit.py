from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.routers import forms


class RejectingLimiter:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    async def check(self, endpoint: str, identifier: str) -> None:
        self.calls.append((endpoint, identifier))
        raise HTTPException(status_code=429, detail="Too many requests")


@pytest.mark.asyncio
async def test_embed_action_limit_uses_session_and_client_ip(monkeypatch):
    limiter = RejectingLimiter()
    monkeypatch.setitem(forms._FORM_EMBED_LIMITERS, "provider", limiter)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [(b"x-forwarded-for", b"203.0.113.4")],
            "client": ("127.0.0.1", 1234),
        }
    )
    ctx = SimpleNamespace(
        user=SimpleNamespace(embed=True, jti="session-1")
    )

    with pytest.raises(HTTPException) as exc_info:
        await forms._limit_embed_action(request, ctx, "provider")

    assert exc_info.value.status_code == 429
    assert limiter.calls == [
        ("form_embed_provider", "session-1:203.0.113.4")
    ]
