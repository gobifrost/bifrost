"""Release allocator arenas after large table API requests complete."""
from __future__ import annotations

import gc
from collections.abc import Awaitable, Callable
from typing import Any

from src.core.malloc import trim_malloc

ASGIApp = Callable[
    [dict[str, Any], Callable[[], Awaitable[dict[str, Any]]], Callable[[dict[str, Any]], Awaitable[None]]],
    Awaitable[None],
]

_TRIM_THRESHOLD_BYTES = 64 * 1024


class AllocationTrimMiddleware:
    """Trim glibc arenas after a large tables request and its response are done.

    Table batches and queries can transiently hold multiple JSON representations
    at once. Running after the wrapped ASGI app returns ensures the response has
    been sent and its request-scoped objects are no longer retained by FastAPI.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        if scope["type"] != "http" or not _is_table_document_path(scope["path"]):
            await self.app(scope, receive, send)
            return

        large_request = _header_content_length(scope.get("headers", [])) >= _TRIM_THRESHOLD_BYTES
        large_response = False

        async def observing_send(message: dict[str, Any]) -> None:
            nonlocal large_response
            if message["type"] == "http.response.start":
                large_response = (
                    _header_content_length(message.get("headers", []))
                    >= _TRIM_THRESHOLD_BYTES
                )
            await send(message)

        try:
            await self.app(scope, receive, observing_send)
        finally:
            if large_request or large_response:
                gc.collect()
                trim_malloc()


def _is_table_document_path(path: str) -> bool:
    return path.startswith("/api/tables/") and "/documents" in path


def _header_content_length(headers: list[tuple[bytes, bytes]]) -> int:
    for name, value in headers:
        if name.lower() == b"content-length":
            try:
                return int(value)
            except ValueError:
                return 0
    return 0
