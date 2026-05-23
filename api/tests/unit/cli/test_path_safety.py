"""Security regressions for CLI local path handling."""

from __future__ import annotations

import base64
import pathlib
from datetime import datetime, timezone
from typing import Any

import pytest

from bifrost import cli


class _Response:
    def __init__(self, status_code: int, body: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._body = body or {}
        self.text = ""

    def json(self) -> dict[str, Any]:
        return self._body


class _PullClient:
    def __init__(self, server_path: str, content: bytes) -> None:
        self.server_path = server_path
        self.content = content

    async def post(self, endpoint: str, json: dict[str, Any]) -> _Response:
        if endpoint == "/api/files/list":
            return _Response(
                200,
                {
                    "files_metadata": [
                        {
                            "path": self.server_path,
                            "etag": "server-md5",
                            "last_modified": datetime.now(timezone.utc).isoformat(),
                            "updated_by": "attacker",
                        }
                    ]
                },
            )
        if endpoint == "/api/files/read":
            assert json["path"] == self.server_path
            return _Response(
                200,
                {"content": base64.b64encode(self.content).decode("ascii")},
            )
        raise AssertionError(f"unexpected endpoint: {endpoint}")


@pytest.mark.asyncio
async def test_pull_rejects_server_path_traversal(tmp_path: pathlib.Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.unlink(missing_ok=True)

    rc = await cli._sync_files(
        str(tmp_path),
        force=True,
        client=_PullClient("../outside.txt", b"stolen"),
    )

    assert rc == 1
    assert not outside.exists()


@pytest.mark.asyncio
async def test_pull_rejects_absolute_server_path(tmp_path: pathlib.Path) -> None:
    absolute = tmp_path / "absolute-server-path.txt"

    rc = await cli._sync_files(
        str(tmp_path),
        force=True,
        client=_PullClient(absolute.as_posix(), b"stolen"),
    )

    assert rc == 1
    assert not absolute.exists()
