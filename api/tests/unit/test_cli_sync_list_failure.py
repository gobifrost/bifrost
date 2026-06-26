"""A failed server file-listing must ABORT the sync, never mass-push.

Regression guard: `_sync_files` used to swallow any non-200 (or exception) from
`/api/files/list` and proceed with an empty server view — so every local file
looked "new locally" and got pushed. On an auth/permission failure (403/401)
that silently overwrites the server with the entire local tree. The fetch is
now fatal: bail with rc=1 and write NOTHING.
"""
import asyncio
import base64

import pytest

from bifrost import cli


class _Resp:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> dict:
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


class _FailingListClient:
    """Server rejects the listing; records any write attempts."""

    def __init__(self, status_code: int, payload: dict | None = None):
        self._status = status_code
        self._payload = payload
        self.writes: list[dict] = []

    async def post(self, url: str, json: dict | None = None):  # noqa: A002
        if url == "/api/files/list":
            return _Resp(self._status, self._payload)
        if url == "/api/files/write":
            self.writes.append(json or {})
            return _Resp(204)
        return _Resp(200, {})

    async def get(self, url: str):
        return _Resp(404)


def _fake_collect(path, repo_prefix, single_file=None):
    files = {
        f"app/file{i}.txt": base64.b64encode(f"content {i}".encode()).decode()
        for i in range(1, 4)
    }
    return files, 0


@pytest.mark.parametrize("status_code", [403, 401, 500])
def test_failed_list_aborts_without_pushing(monkeypatch, capsys, tmp_path, status_code):
    monkeypatch.setattr(cli, "_is_tty", False)
    monkeypatch.setattr(cli, "_collect_push_files", _fake_collect)
    client = _FailingListClient(status_code, {"detail": "Forbidden"})

    rc = asyncio.run(
        cli._sync_files(str(tmp_path), repo_prefix="app", client=client, one_way=True)
    )

    assert rc == 1, "a failed listing must abort with a non-zero exit code"
    assert client.writes == [], "no files may be pushed when the listing failed"
    err = capsys.readouterr().err
    assert "Aborting" in err
    assert str(status_code) in err


def test_list_network_error_aborts(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "_is_tty", False)
    monkeypatch.setattr(cli, "_collect_push_files", _fake_collect)

    class _BoomClient:
        def __init__(self):
            self.writes: list[dict] = []

        async def post(self, url: str, json: dict | None = None):  # noqa: A002
            if url == "/api/files/list":
                raise ConnectionError("connection refused")
            if url == "/api/files/write":
                self.writes.append(json or {})
                return _Resp(204)
            return _Resp(200, {})

    client = _BoomClient()
    rc = asyncio.run(
        cli._sync_files(str(tmp_path), repo_prefix="app", client=client, one_way=True)
    )

    assert rc == 1
    assert client.writes == []
    assert "Aborting" in capsys.readouterr().err
