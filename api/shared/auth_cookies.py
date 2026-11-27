"""Browser transport for the same signed access JWT used by bearer clients.

Large tokens are split into bounded HttpOnly cookies, never replaced by an
opaque authority lookup. The manifest binds the complete ordered bytes; the
normal signature, schema and epoch validators still authenticate the result.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Mapping
from urllib.parse import parse_qs, urlsplit

from fastapi import Request, Response, WebSocket

from shared.browser_impersonation import IMPERSONATION_COOKIE_NAME
from src.config import get_settings
from src.core.security import decode_token, generate_csrf_token

_CHUNK_BYTES = 3600
_CHUNK_PREFIX = "access_token."
_MANIFEST_PREFIX = "chunks."


_FRAME_ASSET_COOKIE = "__Secure-bifrost_frame_asset"
_FRAME_ASSET_PATH = "/api/applications/"
_FRAME_DOCUMENT_PATH = "/embedded/apps/"
_FRAME_SOCKET_COOKIE = "__Secure-bifrost_frame_socket"
_FRAME_SOCKET_PATH = "/ws/connect"


def read_frame_asset_cookie(request: Request) -> tuple[bool, str | None]:
    """Authenticate same-origin browser requests for native App imports only."""
    pathname = request.url.path
    if (
        request.method != "GET"
        or not pathname.startswith(_FRAME_ASSET_PATH)
        or pathname.split("/", 5)[4:5] not in (["dist"], ["bundle-asset"])
        or request.headers.get("sec-fetch-site") != "same-origin"
        or request.headers.get("sec-fetch-dest") not in {"script", "style", "font", "image"}
    ):
        return False, None
    try:
        referer = urlsplit(request.headers.get("referer", ""))
    except ValueError:
        return False, None
    token = _read_cookie(request.cookies, _FRAME_ASSET_COOKIE)
    if referer.path.startswith(_FRAME_DOCUMENT_PATH):
        # Partitioned cookies are shared by tabs on the same site. The initial
        # asset must belong to the exact session that authenticated this frame.
        claims = decode_token(token, expected_type="access") if token else None
        requested = parse_qs(referer.query).get("__bf_frame_session")
        return True, token if claims and [claims.get("sid")] == requested else None

    # Subsequent imports use the parent module or stylesheet as their referrer.
    # Only accept this credential for another asset of the same App.
    source_parts = referer.path.split("/", 5)
    target_parts = pathname.split("/", 5)
    if source_parts[1:4] == target_parts[1:4] and source_parts[4:5] in (["dist"], ["bundle-asset"]):
        return False, token
    return False, None


def has_same_origin_websocket_handshake(websocket: WebSocket) -> bool:
    """Match the browser Origin against the public Host preserved by the proxy."""
    try:
        origin = urlsplit(websocket.headers.get("origin", ""))
        host = urlsplit("//" + websocket.headers.get("host", ""))
        if (
            origin.scheme not in ("http", "https")
            or not origin.hostname or origin.username is not None or origin.password is not None
            or origin.path or origin.query or origin.fragment
            or not host.hostname or host.username is not None or host.password is not None
            or host.path or host.query or host.fragment
        ):
            return False
        default_port = 443 if origin.scheme == "https" else 80
        return (
            origin.hostname == host.hostname
            and (origin.port or default_port) == (host.port or default_port)
        )
    except ValueError:
        return False



def read_frame_socket_cookie(websocket: WebSocket) -> str | None:
    if websocket.url.path != _FRAME_SOCKET_PATH or not has_same_origin_websocket_handshake(websocket):
        return None
    token = _read_cookie(websocket.cookies, _FRAME_SOCKET_COOKIE)
    claims = decode_token(token, expected_type="access") if token else None
    if not claims or claims.get("sid") != websocket.query_params.get("frame"):
        return None
    return token


def set_frame_transport_cookies(response: Response, token: str) -> None:
    """Give browser-native transports the frame access credential."""
    _set_access_cookie(response, token, name=_FRAME_ASSET_COOKIE, partition_path=_FRAME_ASSET_PATH)
    _set_access_cookie(response, token, name=_FRAME_SOCKET_COOKIE, partition_path=_FRAME_SOCKET_PATH)


def clear_frame_transport_cookies(response: Response) -> None:
    for name, path in (
        (_FRAME_ASSET_COOKIE, _FRAME_ASSET_PATH),
        (_FRAME_SOCKET_COOKIE, _FRAME_SOCKET_PATH),
    ):
        response.headers.append(
            "Set-Cookie",
            f"{name}=; Path={path}; Max-Age=0; Secure; HttpOnly; SameSite=None; Partitioned",
        )


def read_access_cookie(cookies: Mapping[str, str]) -> str | None:
    """Reassemble a complete cookie token; malformed manifests fail closed."""
    return _read_cookie(cookies, "access_token")


def _read_cookie(cookies: Mapping[str, str], name: str) -> str | None:
    prefix = name + "."
    value = cookies.get(name)
    if value is None or not value.startswith(_MANIFEST_PREFIX):
        return value
    parts = value.split(".")
    if len(parts) != 3 or not parts[1].isdigit() or len(parts[1]) > 8:
        return None
    count = int(parts[1])
    if count < 2 or count > len(cookies) or len(parts[2]) != 64:
        return None
    chunks = [cookies.get(f"{prefix}{index}") for index in range(count)]
    if any(chunk is None or not chunk or len(chunk) > _CHUNK_BYTES for chunk in chunks):
        return None
    if any(len(chunk) != _CHUNK_BYTES for chunk in chunks[:-1] if chunk is not None):
        return None
    token = "".join(chunk for chunk in chunks if chunk is not None)
    if not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), parts[2]):
        return None
    return token


def _max_age(token: str, token_type: str) -> int:
    payload = decode_token(token, expected_type=token_type)
    if payload is None or not isinstance(payload.get("exp"), (int, float)):
        raise ValueError("Authentication cookies require a valid expiring token")
    return max(0, int(payload["exp"] - time.time()))


def set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    """Set byte-bounded access cookies and actual JWT expiry on every cookie."""
    secure = get_settings().is_production
    refresh_age = _max_age(refresh_token, "refresh")
    response.delete_cookie(IMPERSONATION_COOKIE_NAME, path="/")
    _set_access_cookie(response, access_token)
    response.set_cookie(
        "refresh_token", refresh_token, httponly=True, secure=secure,
        samesite="lax", max_age=refresh_age, path="/",
    )
    response.set_cookie(
        "csrf_token", generate_csrf_token(), httponly=False, secure=secure,
        samesite="strict", max_age=_max_age(access_token, "access"), path="/",
    )


def _set_access_cookie(
    response: Response, access_token: str, *, name: str = "access_token", partition_path: str | None = None,
) -> None:
    max_age = _max_age(access_token, "access")

    def set_part(part_name: str, value: str) -> None:
        if partition_path:
            # Starlette's partitioned keyword requires Python 3.14. Keep the
            # header explicit and only interpolate safe names and JWT bytes.
            response.headers.append(
                "Set-Cookie",
                f"{part_name}={value}; Path={partition_path}; Max-Age={max_age}; "
                "Secure; HttpOnly; SameSite=None; Partitioned",
            )
        else:
            response.set_cookie(
                part_name, value, httponly=True, secure=get_settings().is_production,
                samesite="lax", max_age=max_age, path="/",
            )

    if len(access_token) <= _CHUNK_BYTES:
        set_part(name, access_token)
    else:
        chunks = [access_token[start:start + _CHUNK_BYTES] for start in range(0, len(access_token), _CHUNK_BYTES)]
        digest = hashlib.sha256(access_token.encode()).hexdigest()
        set_part(name, f"{_MANIFEST_PREFIX}{len(chunks)}.{digest}")
        for index, chunk in enumerate(chunks):
            set_part(f"{name}.{index}", chunk)


def clear_auth_cookies(response: Response, cookies: Mapping[str, str] | None = None) -> None:
    """Remove the credential manifest and any received fragments on logout."""
    for name in ("access_token", "refresh_token", "csrf_token", IMPERSONATION_COOKIE_NAME):
        response.delete_cookie(name, path="/")
    # Only a complete manifest can name fragments. Full-size preceding chunks
    # bound deletion work by the incoming bytes; arbitrary cookie names cannot
    # amplify a small Cookie header into hundreds of deletion headers.
    if cookies is not None:
        manifest = cookies.get("access_token", "")
        if manifest.startswith(_MANIFEST_PREFIX) and read_access_cookie(cookies) is not None:
            for index in range(int(manifest.split(".")[1])):
                response.delete_cookie(f"{_CHUNK_PREFIX}{index}", path="/")
