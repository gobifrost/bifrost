"""Large browser credentials retain exactly the bearer JWT and cookie policy."""

from datetime import timedelta
from http.cookies import SimpleCookie
from uuid import uuid4

import pytest
from fastapi import Response

from shared.auth_cookies import clear_auth_cookies, read_access_cookie, set_auth_cookies
from src.core.security import create_access_token, create_refresh_token, decode_token


def _cookies(response: Response) -> dict[str, str]:
    jar: SimpleCookie = SimpleCookie()
    for header in response.headers.getlist("set-cookie"):
        jar.load(header)
    return {name: morsel.value for name, morsel in jar.items() if morsel.value}


@pytest.mark.parametrize("size", [50, 3900, 9000])
def test_cookie_transport_round_trips_exact_token_with_bounded_headers(size: int) -> None:
    token = create_access_token({"sub": str(uuid4()), "padding": "x" * size}, timedelta(seconds=4900))
    refresh, _ = create_refresh_token({"sub": str(uuid4())}, timedelta(days=2))
    response = Response()
    set_auth_cookies(response, token, refresh)
    cookies = _cookies(response)

    assert read_access_cookie(cookies) == token
    for header in response.headers.getlist("set-cookie"):
        assert len(header.encode()) < 4096
        if header.startswith("access_token"):
            assert "HttpOnly" in header and "SameSite=lax" in header
    access: SimpleCookie = SimpleCookie()
    access.load(response.headers.getlist("set-cookie")[1])
    lifetime = int(access["access_token"]["max-age"])
    payload = decode_token(token)
    assert payload is not None
    assert 4897 <= lifetime <= 4900


def test_missing_changed_or_mixed_fragments_never_resolve() -> None:
    token = create_access_token({"padding": "a" * 9000})
    refresh, _ = create_refresh_token({"sub": str(uuid4())})
    response = Response()
    set_auth_cookies(response, token, refresh)
    cookies = _cookies(response)
    assert read_access_cookie(cookies) == token
    missing = dict(cookies)
    missing.pop("access_token.1")
    assert read_access_cookie(missing) is None
    changed = {**cookies, "access_token.0": "changed"}
    assert read_access_cookie(changed) is None
    assert read_access_cookie({"access_token": "chunks.99999999." + "0" * 64}) is None
    # Orphaned fragments cannot authenticate after logout clears the manifest.
    assert read_access_cookie({name: value for name, value in cookies.items() if name != "access_token"}) is None


def test_logout_removes_received_cookie_fragments_and_manifest() -> None:
    refresh, _ = create_refresh_token({"sub": str(uuid4())})
    issued = Response()
    set_auth_cookies(issued, create_access_token({"padding": "a" * 4500}), refresh)
    response = Response()
    clear_auth_cookies(response, _cookies(issued))
    headers = response.headers.getlist("set-cookie")
    for name in ("access_token", "access_token.0", "access_token.1", "refresh_token", "csrf_token"):
        assert any(header.startswith(name + "=") and "Max-Age=0" in header for header in headers)


def test_arbitrary_fragment_cookie_names_cannot_amplify_logout_headers() -> None:
    response = Response()
    clear_auth_cookies(response, {f"access_token.{index}": "x" for index in range(500)})
    assert len(response.headers.getlist("set-cookie")) == 4


def test_rotation_to_fewer_chunks_never_reads_old_unreferenced_fragments() -> None:
    refresh, _ = create_refresh_token({"sub": str(uuid4())})
    old = Response()
    set_auth_cookies(old, create_access_token({"padding": "a" * 15000}), refresh)
    cookies = _cookies(old)
    fresh_token = create_access_token({"padding": "b" * 4500})
    fresh = Response()
    set_auth_cookies(fresh, fresh_token, refresh)
    cookies.update(_cookies(fresh))
    assert "access_token.3" in cookies  # Leftover cookie from the older generation.
    assert read_access_cookie(cookies) == fresh_token
    short = create_access_token({"sub": str(uuid4())})
    response = Response()
    set_auth_cookies(response, short, refresh)
    cookies.update(_cookies(response))
    assert read_access_cookie(cookies) == short
