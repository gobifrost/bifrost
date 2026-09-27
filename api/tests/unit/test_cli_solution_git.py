"""CLI-only managed Solution Git lifecycle contracts."""

from __future__ import annotations

from unittest import mock

from click.testing import CliRunner

from bifrost.commands.solution import solution_group

SOLUTION_ID = "11111111-1111-1111-1111-111111111111"


class _Response:
    def __init__(self, body: dict, status_code: int = 200):
        self.status_code = status_code
        self.text = str(body)
        self._body = body

    def json(self) -> dict:
        return self._body


def _client(calls: list[tuple[str, str, dict | None]]) -> mock.AsyncMock:
    client = mock.AsyncMock()

    async def get(path: str, **_kwargs):
        calls.append(("get", path, None))
        return _Response({"solutions": [{"id": SOLUTION_ID, "slug": "support"}]})

    async def post(path: str, json: dict | None = None, **_kwargs):
        calls.append(("post", path, json))
        if path == "/api/solutions/install/from-repo":
            return _Response({"deploy_job_id": "deploy-1"}, 202)
        return _Response({"solution_id": SOLUTION_ID, "status": "synced"}, 202)

    async def patch(path: str, json: dict | None = None, **_kwargs):
        calls.append(("patch", path, json))
        return _Response({"id": SOLUTION_ID, **(json or {})})

    client.get = get
    client.post = post
    client.patch = patch
    return client


def _invoke(monkeypatch, args: list[str], calls: list[tuple[str, str, dict | None]]):
    client = _client(calls)
    monkeypatch.setattr(
        "bifrost.client.BifrostClient.get_instance", lambda **_: client
    )
    monkeypatch.setattr(
        "bifrost.commands.solution._poll_deploy_job",
        mock.AsyncMock(return_value=0),
    )
    return CliRunner().invoke(solution_group, args)


def test_install_repo_posts_existing_endpoint(monkeypatch) -> None:
    calls: list[tuple[str, str, dict | None]] = []
    result = _invoke(
        monkeypatch,
        ["install-repo", "https://example.test/repo.git", "--ref", "main"],
        calls,
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        ("post", "/api/solutions/install/from-repo", {
            "repo_url": "https://example.test/repo.git",
            "repo_subpath": None,
            "git_ref": "main",
        }),
    ]


def test_solution_git_connect_patches_one_writer_fields(monkeypatch) -> None:
    calls: list[tuple[str, str, dict | None]] = []
    result = _invoke(
        monkeypatch,
        ["git", "connect", "support", "https://example.test/repo.git", "--ref", "main"],
        calls,
    )

    assert result.exit_code == 0, result.output
    assert calls[-1] == (
        "patch", f"/api/solutions/{SOLUTION_ID}", {
            "git_connected": True,
            "git_repo_url": "https://example.test/repo.git",
            "repo_subpath": None,
            "git_ref": "main",
        },
    )


def test_solution_sync_calls_update_endpoint_without_fake_poll(monkeypatch) -> None:
    calls: list[tuple[str, str, dict | None]] = []
    result = _invoke(monkeypatch, ["sync", "support"], calls)

    assert result.exit_code == 0, result.output
    assert calls[-1] == ("post", f"/api/solutions/{SOLUTION_ID}/sync", {})


def test_pull_manifests_is_canonical_name_with_compatibility_alias() -> None:
    result = CliRunner().invoke(solution_group, ["--help"])
    assert "pull-manifests" in result.output
    assert "pull" in result.output
