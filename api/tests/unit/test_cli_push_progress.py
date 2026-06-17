"""Non-TTY push prints line-oriented progress so large pushes don't look hung."""
import asyncio

from bifrost import cli


class _FakeResp:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    """Stub BifrostClient: server has no files, writes succeed."""

    async def post(self, url: str, json: dict | None = None):  # noqa: A002
        if url == "/api/files/list":
            return _FakeResp(200, {"files_metadata": []})
        if url == "/api/files/write":
            return _FakeResp(204)
        return _FakeResp(200, {})

    async def get(self, url: str):
        return _FakeResp(404)


def test_non_tty_push_prints_progress(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "_is_tty", False)

    # Three local files to push; server has none (FakeClient returns empty list).
    def _fake_collect(path, repo_prefix, single_file=None):
        import base64

        files = {
            f"app/file{i}.txt": base64.b64encode(f"content {i}".encode()).decode()
            for i in range(1, 4)
        }
        return files, 0

    monkeypatch.setattr(cli, "_collect_push_files", _fake_collect)

    rc = asyncio.run(
        cli._sync_files(
            str(tmp_path),
            repo_prefix="app",
            client=_FakeClient(),
            one_way=True,
        )
    )

    assert rc == 0
    out = capsys.readouterr().out
    assert "Scanning files..." in out
    assert "Uploading 1/3" in out
    assert out.strip().endswith("Done: 3 pushed, 0 unchanged, 0 skipped, 0 failed")
