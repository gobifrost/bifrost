import pytest

from bifrost import cli


def test_push_accepts_single_file(tmp_path, monkeypatch):
    f = tmp_path / "mod.py"
    f.write_text("x = 1\n")
    captured = {}

    async def fake_sync(local_path, *, mirror, validate, force, client, single_file=None, one_way=False):
        captured["single_file"] = single_file
        captured["local_path"] = local_path
        captured["one_way"] = one_way
        return 0

    monkeypatch.setattr(cli, "_sync_files", fake_sync)
    monkeypatch.setattr(cli.BifrostClient, "get_instance", lambda **k: object())
    rc = cli.handle_push([str(f)])
    assert rc == 0
    assert captured["single_file"] == str(f)


def test_push_rejects_missing_path(tmp_path):
    rc = cli.handle_push([str(tmp_path / "does-not-exist.py")])
    assert rc == 1


def test_collect_push_files_single_file_root(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text("x = 1\n")
    files, skipped = cli._collect_push_files(tmp_path, "", single_file=str(f))
    assert skipped == 0
    assert set(files.keys()) == {"mod.py"}


def test_collect_push_files_single_file_nested_with_prefix(tmp_path):
    sub = tmp_path / "apps" / "x"
    sub.mkdir(parents=True)
    f = sub / "main.tsx"
    f.write_text("const a = 1;\n")
    files, skipped = cli._collect_push_files(tmp_path, "repo", single_file=str(f))
    assert skipped == 0
    assert set(files.keys()) == {"repo/apps/x/main.tsx"}


def test_collect_push_files_single_file_honors_ignore_filter(tmp_path):
    f = tmp_path / ".env"
    f.write_text("BIFROST_ACCESS_TOKEN=secret\n")

    files, skipped = cli._collect_push_files(tmp_path, "", single_file=str(f))

    assert files == {}
    assert skipped == 1


@pytest.mark.asyncio
async def test_plain_push_with_tty_does_not_open_tui(tmp_path, monkeypatch):
    f = tmp_path / "mod.py"
    f.write_text("x = 1\n")

    # Pretend we are on an interactive TTY.
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: True)
    monkeypatch.delenv("BIFROST_NONINTERACTIVE", raising=False)

    def _boom(*a, **k):
        raise AssertionError("plain push must not open the interactive TUI")

    import bifrost.tui.sync_app as sync_app

    monkeypatch.setattr(sync_app, "interactive_sync", _boom)

    class FakeResp:
        def __init__(self, status_code, payload=None):
            self.status_code = status_code
            self._payload = payload or {}

        def json(self):
            return self._payload

    class FakeClient:
        async def post(self, url, *a, **k):
            if url == "/api/files/list":
                return FakeResp(200, {"files_metadata": []})
            if url == "/api/files/write":
                return FakeResp(204)
            return FakeResp(200, {})

    rc = await cli._sync_files(
        str(tmp_path),
        mirror=False,
        force=False,
        client=FakeClient(),
        single_file=str(f),
    )
    assert rc == 0


@pytest.mark.asyncio
async def test_push_only_drops_pull_items_before_auto_accept(tmp_path, monkeypatch, capsys):
    local = tmp_path / "local.py"
    local.write_text("x = 1\n")

    monkeypatch.setattr(cli, "_resolve_is_tty", lambda: False)

    class FakeResp:
        def __init__(self, status_code, payload=None):
            self.status_code = status_code
            self._payload = payload or {}

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self):
            self.calls = []

        async def post(self, url, *a, **k):
            self.calls.append((url, k.get("json")))
            if url == "/api/files/list":
                return FakeResp(200, {
                    "files_metadata": [
                        {
                            "path": "remote.py",
                            "etag": "server-md5",
                            "last_modified": "2026-01-01T00:00:00+00:00",
                            "updated_by": "server",
                        }
                    ]
                })
            if url == "/api/files/write":
                return FakeResp(204)
            if url == "/api/files/read":
                raise AssertionError("push-only sync must not pull server files")
            return FakeResp(200, {})

    client = FakeClient()
    rc = await cli._sync_files(
        str(tmp_path),
        mirror=False,
        force=False,
        client=client,
        one_way=True,
    )

    assert rc == 0
    assert [payload["path"] for url, payload in client.calls if url == "/api/files/write"] == ["local.py"]
    assert not (tmp_path / "remote.py").exists()
    out = capsys.readouterr().out
    assert "to pull" not in out
