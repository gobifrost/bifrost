"""Unattended sync requires consent without broadening its file action policy."""

import base64
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from bifrost import cli


@pytest.fixture(autouse=True)
def headless(monkeypatch):
    monkeypatch.setattr(cli, "_is_tty", False)
    monkeypatch.delenv("BIFROST_NONINTERACTIVE", raising=False)


class FileClient:
    def __init__(self):
        self.calls = []

    async def post(self, url, *, json):
        self.calls.append((url, json))
        if url == "/api/files/list":
            return SimpleNamespace(status_code=200, json=lambda: {"files_metadata": [
                {"path": name, "etag": "different", "last_modified": "2030-01-01T00:00:00Z"}
                for name in ("edited.txt", "remote-only.txt")
            ]})
        if url == "/api/files/read":
            return SimpleNamespace(status_code=200, json=lambda: {
                "content": base64.b64encode(b"remote contents\r\n").decode(),
            })
        if url == "/api/files/write":
            return SimpleNamespace(status_code=204)
        raise AssertionError(f"Unexpected mutation: {url}")


@pytest.mark.asyncio
@pytest.mark.parametrize("mirror", [False, True])
async def test_unapproved_sync_preserves_local_edits_and_makes_no_requests(tmp_path, mirror, capsys):
    edited = tmp_path / "edited.txt"
    edited.write_bytes(b"local edits\r\n")
    sibling = tmp_path / "local-only.txt"
    sibling.write_bytes(b"keep me")
    client = FileClient()

    result = await cli._sync_files(str(tmp_path), client=client, mirror=mirror)

    assert result == 1
    assert client.calls == []
    assert edited.read_bytes() == b"local edits\r\n"
    assert sibling.read_bytes() == b"keep me"
    assert "--force" in capsys.readouterr().err


@pytest.mark.asyncio
@pytest.mark.parametrize("use_environment", [False, True])
async def test_explicit_consent_keeps_existing_mirror_defaults(tmp_path, monkeypatch, use_environment):
    edited = tmp_path / "edited.txt"
    edited.write_bytes(b"local edits")
    os.utime(edited, (1, 1))
    (tmp_path / "local-only.txt").write_bytes(b"local only")
    if use_environment:
        monkeypatch.setenv("BIFROST_NONINTERACTIVE", "1")
    client = FileClient()

    result = await cli._sync_files(
        str(tmp_path), client=client, mirror=True, force=not use_environment,
    )

    assert result == 0
    assert edited.read_bytes() == b"remote contents\r\n"
    assert (tmp_path / "remote-only.txt").read_bytes() == b"remote contents\r\n"
    assert (tmp_path / "local-only.txt").read_bytes() == b"local only"
    uploads = [payload["path"] for url, payload in client.calls if url == "/api/files/write"]
    assert uploads == ["local-only.txt"]


@pytest.mark.asyncio
@pytest.mark.parametrize("single_file", [False, True])
async def test_plain_and_single_file_push_preserve_newer_remote_and_siblings(tmp_path, single_file):
    edited = tmp_path / "edited.txt"
    edited.write_bytes(b"older local")
    os.utime(edited, (1, 1))
    sibling = tmp_path / "sibling.txt"
    sibling.write_bytes(b"keep sibling")
    client = FileClient()

    result = await cli._sync_files(
        str(tmp_path), client=client,
        single_file=str(edited) if single_file else None,
        one_way=not single_file, mirror=single_file,
    )

    assert result == 0
    assert edited.read_bytes() == b"older local"
    assert sibling.read_bytes() == b"keep sibling"
    assert not (tmp_path / "remote-only.txt").exists()
    assert not any(url == "/api/files/read" for url, _ in client.calls)
    assert not any(
        url == "/api/files/write" and payload["path"] == "edited.txt"
        for url, payload in client.calls
    )


@pytest.mark.asyncio
async def test_terminal_loss_cancels_selection_without_overwrite(tmp_path, monkeypatch):
    edited = tmp_path / "edited.txt"
    edited.write_bytes(b"keep local")
    os.utime(edited, (1, 1))
    monkeypatch.setattr(cli, "_is_tty", True)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: False)
    client = FileClient()

    await cli._sync_files(str(tmp_path), client=client)

    assert edited.read_bytes() == b"keep local"
    assert [url for url, _ in client.calls] == ["/api/files/list"]


@pytest.mark.asyncio
@pytest.mark.parametrize("force", [False, True])
async def test_watch_dispatch_preserves_force(tmp_path, monkeypatch, force):
    watch = AsyncMock(return_value=1)
    monkeypatch.setattr(cli, "_watch_and_push", watch)

    await cli._push_with_precheck(str(tmp_path), watch=True, force=force, client=Mock())

    assert watch.await_args.kwargs["force"] is force


@pytest.mark.asyncio
async def test_unapproved_watch_initial_sync_cannot_start_observer(tmp_path, monkeypatch):
    observer = Mock(side_effect=AssertionError("Refused sync must not start watching"))
    monkeypatch.setattr("watchdog.observers.Observer", observer)
    (tmp_path / "edited.txt").write_bytes(b"keep local")
    client = AsyncMock()

    result = await cli._watch_and_push(
        str(tmp_path), repo_prefix="", mirror=False, validate=False, client=client,
    )

    assert result == 1
    observer.assert_not_called()
    assert (tmp_path / "edited.txt").read_bytes() == b"keep local"
    assert [call.args[0] for call in client.post.await_args_list] == ["/api/files/watch"]
