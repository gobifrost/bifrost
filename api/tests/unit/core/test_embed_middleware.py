"""Unit tests for embed token route scoping."""

from datetime import timedelta
from uuid import uuid4

import pytest
from starlette.requests import Request
from starlette.responses import Response

from src.core.embed_middleware import EmbedScopeMiddleware
from src.core.security import create_access_token


def _embed_token(**claims: str) -> str:
    token_claims = {
        "sub": str(uuid4()),
        "jti": str(uuid4()),
        "email": "embed@internal.gobifrost.com",
        "is_superuser": False,
        "embed": True,
        "roles": ["EmbedUser"],
        **claims,
    }
    return create_access_token(token_claims, expires_delta=timedelta(hours=1))


def _request(method: str, path: str, token: str, *, token_source: str = "bearer") -> Request:
    headers = []
    if token_source == "bearer":
        headers.append((b"authorization", f"Bearer {token}".encode()))
    elif token_source == "cookie":
        headers.append((b"cookie", f"embed_token={token}".encode()))

    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": headers,
            "query_string": b"",
            "server": ("testserver", 80),
            "scheme": "http",
            "client": ("testclient", 50000),
        }
    )


async def _allowed_response(_: Request) -> Response:
    return Response(status_code=204)


async def _dispatch(
    method: str,
    path: str,
    token: str,
    *,
    token_source: str = "bearer",
) -> Response:
    middleware = EmbedScopeMiddleware(app=None)
    return await middleware.dispatch(
        _request(method, path, token, token_source=token_source), _allowed_response
    )


@pytest.mark.asyncio
async def test_embed_token_cannot_mutate_allowlisted_app_route():
    token = _embed_token(app_id=str(uuid4()))

    response = await _dispatch("PATCH", "/api/applications/some-app", token)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_form_embed_token_cannot_replay_against_another_form():
    form_id = str(uuid4())
    other_form_id = str(uuid4())
    token = _embed_token(form_id=form_id)

    response = await _dispatch("GET", f"/api/forms/{other_form_id}", token)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_app_embed_token_cannot_replay_against_another_app_render_route():
    app_id = str(uuid4())
    other_app_id = str(uuid4())
    token = _embed_token(app_id=app_id)

    response = await _dispatch("GET", f"/api/applications/{other_app_id}/render", token)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_embed_token_can_access_its_scoped_form_route():
    form_id = str(uuid4())
    token = _embed_token(form_id=form_id)

    response = await _dispatch("GET", f"/api/forms/{form_id}", token)

    assert response.status_code == 204


@pytest.mark.asyncio
async def test_cookie_embed_token_is_scoped_like_bearer_token():
    form_id = str(uuid4())
    other_form_id = str(uuid4())
    token = _embed_token(form_id=form_id)

    response = await _dispatch(
        "GET",
        f"/api/forms/{other_form_id}",
        token,
        token_source="cookie",
    )

    assert response.status_code == 403
