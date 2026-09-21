"""CLI contracts for workspace Git connection workflows."""

from __future__ import annotations

from unittest import mock

import pytest

from bifrost import cli


class _Response:
    status_code = 202
    text = ""

    def __init__(self, body: dict):
        self._body = body

    def json(self) -> dict:
        return self._body


def test_git_sync_enqueues_platform_job_with_delete_confirmation(monkeypatch) -> None:
    """``git sync`` is the accurately named workspace reconciliation command."""
    client = mock.MagicMock()
    monkeypatch.setattr(
        "bifrost.client.BifrostClient.get_instance", lambda **_: client
    )
    called: dict[str, object] = {}

    def run(client_arg, endpoint, *, label, body):  # type: ignore[no-untyped-def]
        called.update(client=client_arg, endpoint=endpoint, label=label, body=body)
        return {"status": "succeeded", "result": {"status": "success", "pulled": 1}}

    monkeypatch.setattr("bifrost.git_commands._post_platform_job", run)

    assert cli.handle_git(["sync", "--confirm-deletes"]) == 0
    assert called == {
        "client": client,
        "endpoint": "/api/github/sync",
        "label": "Syncing",
        "body": {"confirm_deletes": True},
    }


def test_git_abort_merge_enqueues_platform_job(monkeypatch) -> None:
    """``git abort-merge`` has an explicit recovery path."""
    client = mock.MagicMock()
    monkeypatch.setattr(
        "bifrost.client.BifrostClient.get_instance", lambda **_: client
    )
    called: dict[str, object] = {}

    def run(client_arg, endpoint, *, label, body):  # type: ignore[no-untyped-def]
        called.update(client=client_arg, endpoint=endpoint, label=label, body=body)
        return {"status": "succeeded", "result": {"status": "success", "aborted": True}}

    monkeypatch.setattr("bifrost.git_commands._post_platform_job", run)

    assert cli.handle_git(["abort-merge"]) == 0
    assert called == {
        "client": client,
        "endpoint": "/api/github/abort-merge",
        "label": "Aborting merge",
        "body": {},
    }


@pytest.mark.parametrize(
    ("args", "endpoint", "label", "body", "result", "expected_output"),
    [
        (
            ["fetch"],
            "/api/github/fetch",
            "Fetching",
            {},
            {
                "success": True,
                "commits_ahead": 1,
                "changed_files": [{"path": "README.md", "change_type": "added"}],
            },
            "README.md",
        ),
        (
            ["status"],
            "/api/github/changes",
            "Checking status",
            {},
            {
                "success": True,
                "commits_behind": 1,
                "changed_files": [{"path": "workflows/run.py", "change_type": "modified"}],
                "conflicts": [{"path": "workflows/run.py"}],
            },
            "merge conflict",
        ),
        (
            ["commit", "-m", "save workspace"],
            "/api/github/commit",
            "Committing",
            {"message": "save workspace"},
            {"success": True, "commit_sha": "abcdef012345", "files_committed": 1},
            "Committed abcdef0",
        ),
        (
            ["resolve", "workflows/run.py=keep_local"],
            "/api/github/resolve",
            "Resolving",
            {"resolutions": {"workflows/run.py": "ours"}},
            {"success": True, "pulled": 1},
            "Sync complete: pulled 1 change",
        ),
        (
            ["diff", "workflows/run.py"],
            "/api/github/diff",
            "Diffing",
            {"path": "workflows/run.py"},
            {"success": True, "path": "workflows/run.py", "head_content": "old", "working_content": "new"},
            "+ new",
        ),
        (
            ["discard", "workflows/run.py"],
            "/api/github/discard",
            "Discarding",
            {"paths": ["workflows/run.py"]},
            {"success": True, "discarded": ["workflows/run.py"]},
            "Discarded changes to 1 file",
        ),
    ],
)
def test_git_operations_enqueue_platform_jobs_and_use_terminal_results(
    monkeypatch,
    capsys,
    args: list[str],
    endpoint: str,
    label: str,
    body: dict[str, object],
    result: dict[str, object],
    expected_output: str,
) -> None:
    """Every workspace Git operation uses the shared PlatformJob transport."""
    client = mock.MagicMock()
    monkeypatch.setattr(
        "bifrost.client.BifrostClient.get_instance", lambda **_: client
    )
    called: dict[str, object] = {}

    def run(client_arg, operation_endpoint, *, label: str, body: dict[str, object]):  # type: ignore[no-untyped-def]
        called.update(client=client_arg, endpoint=operation_endpoint, label=label, body=body)
        return {"status": "succeeded", "result": result}

    monkeypatch.setattr("bifrost.git_commands._post_platform_job", run)

    assert cli.handle_git(args) == 0
    assert called == {
        "client": client,
        "endpoint": endpoint,
        "label": label,
        "body": body,
    }
    assert expected_output in capsys.readouterr().out


def test_git_sync_prints_pending_deletions_from_action_result(monkeypatch, capsys) -> None:
    """A delete-confirmation action remains structured after job polling."""
    client = mock.MagicMock()
    monkeypatch.setattr(
        "bifrost.client.BifrostClient.get_instance", lambda **_: client
    )
    monkeypatch.setattr(
        "bifrost.git_commands._post_platform_job",
        lambda *_args, **_kwargs: {
            "status": "requires_action",
            "result": {
                "success": False,
                "requires_action": "confirm_deletes",
                "pending_deletes": [{"path": ".bifrost/agents.yaml"}],
            },
        },
    )

    assert cli.handle_git(["sync"]) == 2
    output = capsys.readouterr().out
    assert ".bifrost/agents.yaml" in output
    assert "--confirm-deletes" in output


def test_git_connect_requires_explicit_strategy_when_not_interactive(monkeypatch, capsys) -> None:
    """A redirected shell never guesses how to reconcile divergent content."""
    client = mock.MagicMock()
    monkeypatch.setattr(
        "bifrost.client.BifrostClient.get_instance", lambda **_: client
    )
    monkeypatch.setattr("bifrost.git_commands.sys.stdin.isatty", lambda: False)
    monkeypatch.setattr(
        "bifrost.git_commands._preview_connect",
        lambda *_: {
            "token": "preview-token",
            "repository_url": "https://example.test/repo.git",
            "branch": "main",
            "state": "requires_reconciliation",
            "items": [{"path": "workflows/shared.py", "classification": "conflict"}],
        },
    )

    assert cli.handle_git(["connect", "https://example.test/repo.git"]) == 2
    output = capsys.readouterr()
    assert "Conflict" in output.out
    assert "--strategy" in output.err


def test_git_connect_posts_preview_token_and_reconcile_decision(monkeypatch) -> None:
    """The apply request is bound to the preview token and explicit decision."""
    client = mock.MagicMock()
    monkeypatch.setattr(
        "bifrost.client.BifrostClient.get_instance", lambda **_: client
    )
    monkeypatch.setattr(
        "bifrost.git_commands._preview_connect",
        lambda *_: {
            "token": "preview-token",
            "repository_url": "https://example.test/repo.git",
            "branch": "main",
            "state": "requires_reconciliation",
            "items": [{"path": "workflows/shared.py", "classification": "conflict"}],
        },
    )
    called: dict[str, object] = {}

    def run(_client, endpoint, *, label, body):  # type: ignore[no-untyped-def]
        called.update(endpoint=endpoint, label=label, body=body)
        return {"status": "succeeded", "result": {"status": "success", "connected": True}}

    monkeypatch.setattr("bifrost.git_commands._post_platform_job", run)

    assert cli.handle_git([
        "connect", "https://example.test/repo.git", "--strategy", "reconcile",
        "--decision", "workflows/shared.py=local",
    ]) == 0
    assert called == {
        "endpoint": "/api/github/connect",
        "label": "Connecting",
        "body": {
            "preview_token": "preview-token",
            "strategy": "reconcile",
            "decisions": {"workflows/shared.py": "local"},
        },
    }


def test_git_connect_start_from_remote_requires_explicit_destructive_confirmation(
    monkeypatch, capsys
) -> None:
    """The CLI must fail closed instead of silently discarding reviewed local content."""
    client = mock.MagicMock()
    monkeypatch.setattr(
        "bifrost.client.BifrostClient.get_instance", lambda **_: client
    )
    monkeypatch.setattr(
        "bifrost.git_commands._preview_connect",
        lambda *_: {
            "token": "preview-token",
            "repository_url": "https://example.test/repo.git",
            "branch": "main",
            "state": "requires_reconciliation",
            "items": [{"path": "workflows/local.py", "classification": "local_only"}],
        },
    )
    enqueue = mock.MagicMock()
    monkeypatch.setattr("bifrost.git_commands._post_platform_job", enqueue)

    assert cli.handle_git([
        "connect", "https://example.test/repo.git", "--strategy", "start-from-remote",
    ]) == 2

    assert "--confirm-destructive" in capsys.readouterr().err
    enqueue.assert_not_called()


def test_git_connect_start_from_remote_sends_explicit_destructive_confirmation(
    monkeypatch
) -> None:
    """An explicitly approved remote start reaches the server with its confirmation."""
    client = mock.MagicMock()
    monkeypatch.setattr(
        "bifrost.client.BifrostClient.get_instance", lambda **_: client
    )
    monkeypatch.setattr(
        "bifrost.git_commands._preview_connect",
        lambda *_: {
            "token": "preview-token",
            "repository_url": "https://example.test/repo.git",
            "branch": "main",
            "state": "requires_reconciliation",
            "items": [{"path": "workflows/local.py", "classification": "local_only"}],
        },
    )
    called: dict[str, object] = {}

    def run(_client, endpoint, *, label, body):  # type: ignore[no-untyped-def]
        called.update(endpoint=endpoint, label=label, body=body)
        return {"status": "succeeded", "result": {"status": "success"}}

    monkeypatch.setattr("bifrost.git_commands._post_platform_job", run)

    assert cli.handle_git([
        "connect", "https://example.test/repo.git", "--strategy", "start-from-remote",
        "--confirm-destructive",
    ]) == 0
    assert called["body"] == {
        "preview_token": "preview-token",
        "strategy": "start_from_remote",
        "decisions": {},
        "confirm_destructive": True,
    }
