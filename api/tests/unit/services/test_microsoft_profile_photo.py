"""Entra profile photos are stored only when Graph returns a real image."""

import asyncio
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from PIL import Image

from src.services.oauth_sso import (
    MICROSOFT_GRAPH_PHOTO_URL,
    OAuthService,
    _microsoft_photo_content_type,
    fetch_microsoft_profile_photo,
)
from src.services import oauth_sso


def _image(fmt: str) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (1, 1), (12, 34, 56)).save(buffer, format=fmt)
    return buffer.getvalue()


def _route(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(oauth_sso.httpx, "AsyncClient", factory)


def test_png_and_jpeg_photos_are_accepted() -> None:
    assert _microsoft_photo_content_type(_image("PNG")) == "image/png"
    assert _microsoft_photo_content_type(_image("JPEG")) == "image/jpeg"


def test_gif_photos_are_rejected() -> None:
    assert _microsoft_photo_content_type(_image("GIF")) is None


def test_non_image_bodies_are_rejected() -> None:
    assert _microsoft_photo_content_type(b'{"error":"ImageNotFound"}') is None


def test_oversized_photos_are_rejected() -> None:
    assert _microsoft_photo_content_type(b"x" * (2 * 1024 * 1024 + 1)) is None


def test_pixel_cap_rejects_a_decoded_image(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oauth_sso, "_MICROSOFT_PHOTO_MAX_PIXELS", 0)

    assert _microsoft_photo_content_type(_image("PNG")) is None


@pytest.mark.asyncio
async def test_blank_token_does_not_call_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    def factory(*_args, **_kwargs):
        raise AssertionError("Graph should not be called")

    monkeypatch.setattr(oauth_sso.httpx, "AsyncClient", factory)

    assert await fetch_microsoft_profile_photo("") is None


@pytest.mark.asyncio
async def test_graph_404_is_a_missing_photo(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == MICROSOFT_GRAPH_PHOTO_URL
        assert request.headers["authorization"] == "Bearer graph-token"
        return httpx.Response(404, json={"error": {"code": "ImageNotFound"}})

    _route(monkeypatch, handler)

    assert await fetch_microsoft_profile_photo("graph-token") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 500])
async def test_graph_errors_do_not_raise(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    _route(monkeypatch, lambda _request: httpx.Response(status))

    assert await fetch_microsoft_profile_photo("graph-token") is None


@pytest.mark.asyncio
async def test_transport_errors_do_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    _route(monkeypatch, handler)

    assert await fetch_microsoft_profile_photo("graph-token") is None


@pytest.mark.asyncio
async def test_photo_download_drops_the_token_outside_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    png = _image("PNG")
    seen: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, request.headers.get("authorization")))
        if request.url.host == "graph.microsoft.com":
            return httpx.Response(302, headers={"Location": "https://cdn.example.test/photo.png"})
        return httpx.Response(200, content=png)

    _route(monkeypatch, handler)

    assert await fetch_microsoft_profile_photo("secret-token") == (png, "image/png")
    assert seen == [
        ("graph.microsoft.com", "Bearer secret-token"),
        ("cdn.example.test", None),
    ]


@pytest.mark.asyncio
async def test_non_https_photo_response_is_discarded(monkeypatch: pytest.MonkeyPatch) -> None:
    png = _image("PNG")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "graph.microsoft.com":
            return httpx.Response(302, headers={"Location": "http://cdn.example.test/photo.png"})
        return httpx.Response(200, content=png)

    _route(monkeypatch, handler)

    assert await fetch_microsoft_profile_photo("secret-token") is None


@pytest.mark.asyncio
async def test_apply_stores_a_returned_photo(monkeypatch: pytest.MonkeyPatch) -> None:
    png = _image("PNG")
    user = SimpleNamespace(avatar_data=None, avatar_content_type=None)
    fetch = AsyncMock(return_value=(png, "image/png"))
    monkeypatch.setattr(oauth_sso, "fetch_microsoft_profile_photo", fetch)

    await OAuthService(MagicMock()).apply_microsoft_profile_photo(user, "graph-token")

    assert user.avatar_data == png
    assert user.avatar_content_type == "image/png"
    fetch.assert_awaited_once_with("graph-token")


@pytest.mark.asyncio
async def test_apply_keeps_an_existing_avatar_when_graph_has_no_photo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = SimpleNamespace(avatar_data=b"custom", avatar_content_type="image/png")
    monkeypatch.setattr(oauth_sso, "fetch_microsoft_profile_photo", AsyncMock(return_value=None))

    await OAuthService(MagicMock()).apply_microsoft_profile_photo(user, "graph-token")

    assert user.avatar_data == b"custom"
    assert user.avatar_content_type == "image/png"


@pytest.mark.asyncio
async def test_download_stops_at_byte_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    consumed = []

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            for index in range(4):
                consumed.append(index)
                yield b"x" * (1024 * 1024)

    _route(monkeypatch, lambda _request: httpx.Response(200, stream=Body()))

    assert await fetch_microsoft_profile_photo("graph-token") is None
    assert consumed == [0, 1, 2]


@pytest.mark.asyncio
async def test_total_deadline_stops_a_slow_download(monkeypatch: pytest.MonkeyPatch) -> None:
    closed = False

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"x"
            await asyncio.Event().wait()

        async def aclose(self):
            nonlocal closed
            closed = True

    monkeypatch.setattr(oauth_sso, "_MICROSOFT_PHOTO_TIMEOUT_SECONDS", 0.01)
    _route(monkeypatch, lambda _request: httpx.Response(200, stream=Body()))

    assert await fetch_microsoft_profile_photo("graph-token") is None
    assert closed
