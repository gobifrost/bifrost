"""The request DB session must be committed before the response is sent.

FastAPI runs the teardown of a default-scoped (``scope="request"``) yield
dependency AFTER the response body has been sent. ``get_db`` commits in its
teardown, so a route relying on that implicit commit would return 2xx before
its write is durable, and a failed commit could no longer change the
response. ``scope="function"`` moves the teardown to when the endpoint returns.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI

from src.core.db_deps import DbSession

API_ROOT = Path(__file__).resolve().parents[2]
SCANNED_DIRS = ("src", "shared")
SESSION_DEPENDENCIES = {"get_db", "get_optional_db"}


def _unscoped_session_dependencies() -> list[str]:
    offenders: list[str] = []
    for directory in SCANNED_DIRS:
        for path in sorted((API_ROOT / directory).rglob("*.py")):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "Depends"
                    and node.args
                    and isinstance(node.args[0], ast.Name)
                    and node.args[0].id in SESSION_DEPENDENCIES
                ):
                    continue
                scoped = any(
                    kw.arg == "scope"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value == "function"
                    for kw in node.keywords
                )
                if not scoped:
                    offenders.append(f"{path.relative_to(API_ROOT)}:{node.lineno}")
    return offenders


def test_every_session_dependency_is_function_scoped() -> None:
    offenders = _unscoped_session_dependencies()
    assert not offenders, (
        "Depends(get_db) / Depends(get_optional_db) must use scope=\"function\" "
        "(or go through the DbSession / OptionalDbSession aliases). With the "
        "default request scope FastAPI commits the session AFTER the response "
        "is sent, so clients get a 2xx before the write is committed. "
        f"Offenders: {offenders}"
    )


class _RecordingSession:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def __aenter__(self) -> _RecordingSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._events.append("session closed")

    async def commit(self) -> None:
        self._events.append("session committed")

    async def rollback(self) -> None:
        self._events.append("session rolled back")


async def test_session_is_committed_before_response_body_is_sent() -> None:
    events: list[str] = []
    app = FastAPI()

    @app.post("/write")
    async def write(db: DbSession) -> dict[str, bool]:
        events.append("handler ran")
        return {"ok": True}

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        if message["type"] == "http.response.body":
            events.append("response body sent")

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/write",
        "raw_path": b"/write",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1),
        "server": ("testserver", 80),
    }

    with patch(
        "src.core.database.get_session_factory",
        return_value=lambda: _RecordingSession(events),
    ):
        await app(scope, receive, send)

    assert events == [
        "handler ran",
        "session committed",
        "session closed",
        "response body sent",
    ]
