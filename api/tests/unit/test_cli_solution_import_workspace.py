"""Safety checks for the two-phase workspace-import CLI command."""
from __future__ import annotations

import pytest
from click.testing import CliRunner
from unittest import mock


def test_noninteractive_conflicts_require_explicit_decisions(monkeypatch) -> None:
    from bifrost.commands.solution import _workspace_import_decisions

    class Stdin:
        def isatty(self) -> bool:
            return False

    monkeypatch.setattr("click.get_text_stream", lambda _name: Stdin())
    with pytest.raises(Exception, match="Use --keep-all, --replace-all, or --decisions"):
        _workspace_import_decisions(
            {"items": [{"id": "file:a.py", "classification": "conflict"}]},
            keep_all=False, replace_all=False, decisions_path=None, json_output=False,
        )


def test_replace_all_covers_each_conflict() -> None:
    from bifrost.commands.solution import _workspace_import_decisions

    assert _workspace_import_decisions(
        {"items": [
            {"id": "file:a.py", "classification": "conflict"},
            {"id": "entity:workflow:w", "classification": "conflict"},
        ]},
        keep_all=False, replace_all=True, decisions_path=None, json_output=True,
    ) == [
        {"item_id": "file:a.py", "action": "replace"},
        {"item_id": "entity:workflow:w", "action": "replace"},
    ]


def test_keep_all_covers_each_conflict() -> None:
    from bifrost.commands.solution import _workspace_import_decisions

    assert _workspace_import_decisions(
        {"items": [{"id": "file:a.py", "classification": "conflict"}]},
        keep_all=True, replace_all=False, decisions_path=None, json_output=True,
    ) == [{"item_id": "file:a.py", "action": "keep"}]


def test_command_streams_upload_and_prints_preview_json(tmp_path) -> None:
    from bifrost.commands.solution import solution_group

    archive = tmp_path / "bundle.zip"
    archive.write_bytes(b"bundle")
    preview = {"preview_token": "preview", "package_sha256": "a" * 64, "items": []}
    calls: list[tuple[str, dict]] = []

    def response(body):
        result = mock.MagicMock(status_code=200, text=str(body))
        result.json.return_value = body
        return result

    class Client:
        async def post(self, path, **kwargs):
            calls.append((path, kwargs))
            return response(preview)

    with mock.patch("bifrost.client.BifrostClient.get_instance", return_value=Client()):
        result = CliRunner().invoke(
            solution_group, ["import-workspace", str(archive), "--preview", "--json"],
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    assert result.output.strip() == '{"preview": {"preview_token": "preview", "package_sha256": "' + "a" * 64 + '", "items": []}}'
    path, kwargs = calls[0]
    assert path.endswith("/preview")
    assert kwargs["files"]["file"][1].closed is True


def test_human_preview_prints_compatibility_warning_and_conflicts(tmp_path) -> None:
    from bifrost.commands.solution import solution_group

    archive = tmp_path / "bundle.zip"
    archive.write_bytes(b"bundle")
    preview = {
        "preview_token": "preview",
        "package_sha256": "a" * 64,
        "items": [
            {
                "id": "entity:workflow:w",
                "classification": "conflict",
                "kind": "workflow",
                "name": "daily_sync",
                "match_key": "workflows/daily_sync.py::daily_sync",
                "target_id": "destination-workflow-id",
            },
            {
                "id": "file:workflows/new.py",
                "classification": "create",
                "kind": "file",
                "name": "workflows/new.py",
            },
        ],
        "warnings": ["Imported entities are unattached global workspace content."],
    }

    class Client:
        async def post(self, _path, **_kwargs):
            result = mock.MagicMock(status_code=200, text=str(preview))
            result.json.return_value = preview
            return result

    with mock.patch("bifrost.client.BifrostClient.get_instance", return_value=Client()):
        result = CliRunner().invoke(
            solution_group,
            ["import-workspace", str(archive), "--preview"],
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    assert "Review compatibility" in result.output
    assert "1 conflict" in result.output
    assert "conflict  workflow" in result.output
    assert "daily_sync" in result.output
    assert "destination-workflow-id" in result.output
    assert "create    file" in result.output
    assert "Imported entities are unattached global workspace content." in result.output
    assert "unattached workspace content and creates uncommitted workspace Git changes" in result.output


def test_org_and_global_are_mutually_exclusive(tmp_path) -> None:
    from bifrost.commands.solution import solution_group

    archive = tmp_path / "bundle.zip"
    archive.write_bytes(b"bundle")

    class Client:
        pass

    with mock.patch("bifrost.client.BifrostClient.get_instance", return_value=Client()):
        result = CliRunner().invoke(
            solution_group,
            ["import-workspace", str(archive), "--org", "acme", "--global", "--preview"],
            catch_exceptions=False,
        )
    assert result.exit_code != 0


def test_org_name_resolves_to_uuid_for_repo_preview() -> None:
    from bifrost.commands.solution import solution_group

    preview = {
        "preview_token": "preview",
        "package_sha256": "a" * 64,
        "source_kind": "repo",
        "repo_url": "https://example.com/repo.git",
        "organization_id": "org-uuid-1",
        "items": [],
        "warnings": [],
    }
    calls: list[tuple[str, dict]] = []

    def response(body):
        result = mock.MagicMock(status_code=200, text=str(body))
        result.json.return_value = body
        return result

    class Client:
        async def post(self, path, **kwargs):
            calls.append((path, kwargs))
            return response(preview)

    class Resolver:
        def __init__(self, _client) -> None:
            pass

        async def resolve(self, _kind, value):
            assert value == "acme"
            return "org-uuid-1"

    with (
        mock.patch("bifrost.client.BifrostClient.get_instance", return_value=Client()),
        mock.patch("bifrost.commands.solution.RefResolver", Resolver),
    ):
        result = CliRunner().invoke(
            solution_group,
            ["import-workspace", "--repo", "https://example.com/repo.git",
             "--org", "acme", "--preview", "--json"],
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    assert calls[0][1]["json"]["organization_id"] == "org-uuid-1"
    assert '"organization_id": "org-uuid-1"' in result.output


def test_global_is_default_for_archive_preview(tmp_path) -> None:
    from bifrost.commands.solution import solution_group

    archive = tmp_path / "bundle.zip"
    archive.write_bytes(b"bundle")
    preview = {
        "preview_token": "preview",
        "package_sha256": "a" * 64,
        "organization_id": None,
        "items": [],
        "warnings": [],
    }
    calls: list[tuple[str, dict]] = []

    def response(body):
        result = mock.MagicMock(status_code=200, text=str(body))
        result.json.return_value = body
        return result

    class Client:
        async def post(self, path, **kwargs):
            calls.append((path, kwargs))
            return response(preview)

    with mock.patch("bifrost.client.BifrostClient.get_instance", return_value=Client()):
        result = CliRunner().invoke(
            solution_group, ["import-workspace", str(archive), "--preview"],
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    assert "Scope: global workspace content" in result.output


def test_archive_and_repo_are_mutually_exclusive(tmp_path) -> None:
    from bifrost.commands.solution import solution_group

    archive = tmp_path / "bundle.zip"
    archive.write_bytes(b"bundle")
    result = CliRunner().invoke(
        solution_group,
        ["import-workspace", str(archive), "--repo", "https://example.com/repo.git", "--preview"],
        catch_exceptions=False,
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_repo_and_archive_both_missing_fails(tmp_path) -> None:
    from bifrost.commands.solution import solution_group

    result = CliRunner().invoke(
        solution_group, ["import-workspace", "--preview"], catch_exceptions=False,
    )
    assert result.exit_code != 0
    assert "ARCHIVE or --repo" in result.output


def test_ref_without_repo_fails(tmp_path) -> None:
    from bifrost.commands.solution import solution_group

    archive = tmp_path / "bundle.zip"
    archive.write_bytes(b"bundle")
    result = CliRunner().invoke(
        solution_group,
        ["import-workspace", str(archive), "--ref", "main", "--preview"],
        catch_exceptions=False,
    )
    assert result.exit_code != 0
    assert "--ref and --path require --repo" in result.output


def test_repo_preview_posts_coordinates_and_prints_source() -> None:
    from bifrost.commands.solution import solution_group

    preview = {
        "preview_token": "preview",
        "package_sha256": "a" * 64,
        "source_kind": "repo",
        "repo_url": "https://example.com/repo.git",
        "git_ref": "main",
        "repo_subpath": "packages/demo",
        "resolved_commit": "abc123",
        "items": [],
        "warnings": [],
    }
    calls: list[tuple[str, dict]] = []

    def response(body):
        result = mock.MagicMock(status_code=200, text=str(body))
        result.json.return_value = body
        return result

    class Client:
        async def post(self, path, **kwargs):
            calls.append((path, kwargs))
            return response(preview)

    with mock.patch("bifrost.client.BifrostClient.get_instance", return_value=Client()):
        result = CliRunner().invoke(
            solution_group,
            ["import-workspace", "--repo", "https://example.com/repo.git",
             "--ref", "main", "--path", "packages/demo", "--preview"],
            catch_exceptions=False,
        )

    assert result.exit_code == 0, result.output
    assert calls[0][0].endswith("/preview-repo")
    assert calls[0][1]["json"]["repo_url"] == "https://example.com/repo.git"
    assert calls[0][1]["json"]["git_ref"] == "main"
    assert calls[0][1]["json"]["repo_subpath"] == "packages/demo"
    assert "https://example.com/repo.git" in result.output
    assert "abc123" in result.output
    assert "one-time snapshot" in result.output
    assert "unattached workspace content and creates uncommitted workspace Git changes" in result.output


def test_command_warns_before_interactive_conflict_prompt(monkeypatch) -> None:
    from bifrost.commands.solution import _workspace_import_decisions

    class Tty:
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr("click.get_text_stream", lambda _name: Tty())
    transcript: list[str] = []
    monkeypatch.setattr("click.echo", lambda message, **_kwargs: transcript.append(message))
    monkeypatch.setattr("click.prompt", lambda *_args, **_kwargs: "k")

    decisions = _workspace_import_decisions(
        {"items": [{"id": "file:a.py", "kind": "file", "name": "a.py", "classification": "conflict"}]},
        keep_all=False, replace_all=False, decisions_path=None, json_output=False,
    )

    assert decisions == [{"item_id": "file:a.py", "action": "keep"}]
    assert transcript[0].startswith("Warning:")
