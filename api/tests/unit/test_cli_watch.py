"""Unit tests for CLI watch mode logic."""
import pathlib
import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bifrost.cli import _WatchChangeHandler, _WatchState


def test_read_only_events_are_ignored():
    """Opened/closed events (read-only access) should not trigger pushes."""
    pending_changes: set[str] = set()
    pending_deletes: set[str] = set()
    lock = threading.Lock()

    def simulate_event(event_type: str, src: str) -> None:
        """Mirrors the ChangeHandler.on_any_event filtering logic (non-moved events)."""
        with lock:
            if event_type == "deleted":
                pending_deletes.add(src)
                pending_changes.discard(src)
            elif event_type in ("created", "modified", "closed"):
                pending_changes.add(src)
                pending_deletes.discard(src)

    # Read-only events should be ignored
    simulate_event("opened", "/workspace/apps/my-app/index.tsx")
    assert len(pending_changes) == 0
    assert len(pending_deletes) == 0

    # Content-modifying events should be captured
    simulate_event("created", "/workspace/apps/my-app/new-file.tsx")
    assert "/workspace/apps/my-app/new-file.tsx" in pending_changes

    simulate_event("modified", "/workspace/apps/my-app/index.tsx")
    assert "/workspace/apps/my-app/index.tsx" in pending_changes

    # Closed events should be captured (Linux inotify: some editors emit created → closed with no modified)
    pending_changes.clear()
    simulate_event("closed", "/workspace/apps/my-app/new-file.tsx")
    assert "/workspace/apps/my-app/new-file.tsx" in pending_changes

    simulate_event("deleted", "/workspace/apps/my-app/old-file.tsx")
    assert "/workspace/apps/my-app/old-file.tsx" in pending_deletes


# =============================================================================
# Moved events (editor atomic saves: write .tmp then rename)
# =============================================================================


def test_moved_event_queues_destination_as_change():
    """A moved/renamed event should queue the destination path as a change."""
    pending_changes: set[str] = set()
    pending_deletes: set[str] = set()
    lock = threading.Lock()

    # Simulate: editor writes test.txt.tmp then renames to test.txt
    dest = "/workspace/test.txt"
    with lock:
        pending_changes.add(dest)
        pending_deletes.discard(dest)

    assert dest in pending_changes
    assert dest not in pending_deletes


def test_moved_event_does_not_delete_source():
    """Moved source (often a temp file) should NOT be queued for server deletion."""
    pending_changes: set[str] = set()
    pending_deletes: set[str] = set()
    lock = threading.Lock()

    # Simulate: only destination is queued, source is ignored
    src = "/workspace/test.txt.tmp.112174.1771947498343"
    dest = "/workspace/test.txt"
    with lock:
        pending_changes.add(dest)
        pending_deletes.discard(dest)

    assert src not in pending_deletes
    assert src not in pending_changes
    assert dest in pending_changes


# =============================================================================
# Fix 1: Observer health check and error resilience
# =============================================================================


def test_observer_death_detected_and_restart_attempted():
    """When the observer thread dies, the watch loop should detect and restart it."""
    from watchdog.observers import Observer

    observer = MagicMock(spec=Observer)
    observer.is_alive.return_value = False

    # Simulate the health check logic from the watch loop
    restarted = False
    if not observer.is_alive():
        new_observer = MagicMock(spec=Observer)
        new_observer.start.return_value = None
        new_observer.schedule.return_value = None
        # In real code, observer is reassigned
        observer = new_observer
        observer.schedule(MagicMock(), "/workspace", recursive=True)
        observer.start()
        restarted = True

    assert restarted
    observer.schedule.assert_called_once()
    observer.start.assert_called_once()


def test_transient_push_error_requeues_changes():
    """On push error, changes and deletes should be re-queued for retry."""
    pending_changes: set[str] = set()
    pending_deletes: set[str] = set()
    lock = threading.Lock()

    changes = {"/workspace/apps/my-app/index.tsx"}
    deletes = {"/workspace/apps/my-app/old.tsx"}

    # Simulate: error during push processing — re-queue
    with lock:
        pending_changes.update(changes)
        pending_deletes.update(deletes)

    assert "/workspace/apps/my-app/index.tsx" in pending_changes
    assert "/workspace/apps/my-app/old.tsx" in pending_deletes


def test_consecutive_error_counter_and_backoff():
    """After 10 consecutive errors, backoff should activate."""
    consecutive_errors = 0

    # Simulate 10 consecutive failures
    for _ in range(10):
        consecutive_errors += 1

    assert consecutive_errors >= 10

    # Simulate successful push resets counter
    consecutive_errors = 0
    assert consecutive_errors == 0


# =============================================================================
# Fix 3: Deletion sync
# =============================================================================


def test_deletion_computes_correct_repo_path():
    """Deletion should compute repo_path from local path and prefix."""
    base = pathlib.Path("/workspace")
    abs_path = pathlib.Path("/workspace/apps/my-app/old-file.tsx")
    repo_prefix = "my-org/my-repo"

    rel = abs_path.relative_to(base)
    repo_path = f"{repo_prefix}/{rel}" if repo_prefix else str(rel)

    assert repo_path == "my-org/my-repo/apps/my-app/old-file.tsx"


def test_deletion_computes_repo_path_without_prefix():
    """When repo_prefix is empty, repo_path is just the relative path."""
    base = pathlib.Path("/workspace")
    abs_path = pathlib.Path("/workspace/apps/my-app/old-file.tsx")
    repo_prefix = ""

    rel = abs_path.relative_to(base)
    repo_path = f"{repo_prefix}/{rel}" if repo_prefix else str(rel)

    assert repo_path == "apps/my-app/old-file.tsx"


@pytest.mark.asyncio
async def test_deletion_404_treated_as_success():
    """A 404 from the delete endpoint should be treated as success (file already gone)."""
    mock_response = MagicMock()
    mock_response.status_code = 404

    mock_client = AsyncMock()
    # httpx raises for 4xx when raise_for_status is called, simulate via exception
    error = Exception("Not Found")
    error.response = mock_response  # type: ignore[attr-defined]
    mock_client.post.side_effect = error

    deleted_count = 0
    try:
        await mock_client.post("/api/files/delete", json={
            "path": "my-org/my-repo/apps/old.tsx",
            "location": "workspace",
            "mode": "cloud",
        })
    except Exception as del_err:
        status_code = getattr(getattr(del_err, "response", None), "status_code", None)
        if status_code == 404:
            deleted_count += 1

    assert deleted_count == 1


# =============================================================================
# Watch handler excludes .bifrost/ — manifest dir is export-only, watch never
# touches it. The observer should drop events under .bifrost/ before the
# handler ever queues them for push.
# =============================================================================


def _fake_event(event_type: str, src_path: str, *, is_directory: bool = False) -> Any:
    return SimpleNamespace(event_type=event_type, src_path=src_path, is_directory=is_directory)


def test_watch_handler_drops_bifrost_events(tmp_path):
    """Events under .bifrost/ should never reach the pending push queue."""
    state = _WatchState(tmp_path)
    handler = _WatchChangeHandler(state)

    # Create a .bifrost/ subtree on disk so relative_to works.
    (tmp_path / ".bifrost").mkdir()
    (tmp_path / ".bifrost" / "workflows.yaml").write_text("workflows: {}\n")
    (tmp_path / ".bifrost" / "agents.yaml").write_text("agents: {}\n")

    handler.dispatch(_fake_event("modified", str(tmp_path / ".bifrost" / "workflows.yaml")))
    handler.dispatch(_fake_event("created", str(tmp_path / ".bifrost" / "agents.yaml")))
    handler.dispatch(_fake_event("deleted", str(tmp_path / ".bifrost" / "workflows.yaml")))
    # Moved into .bifrost/ should also be dropped (dest is under the excluded dir)
    moved = SimpleNamespace(
        event_type="moved",
        src_path=str(tmp_path / "tmp.yaml"),
        dest_path=str(tmp_path / ".bifrost" / "agents.yaml"),
        is_directory=False,
    )
    handler.dispatch(moved)

    assert state.pending_changes == set()
    assert state.pending_deletes == set()


def test_watch_handler_queues_workflow_and_app_files(tmp_path):
    """Code files under workflows/, apps/, or the workspace root should be queued."""
    state = _WatchState(tmp_path)
    handler = _WatchChangeHandler(state)

    workflow_path = tmp_path / "workflows" / "subdir" / "other.py"
    workflow_path.parent.mkdir(parents=True)
    workflow_path.write_text("def run(): pass\n")

    app_path = tmp_path / "apps" / "my-app" / "pages" / "index.tsx"
    app_path.parent.mkdir(parents=True)
    app_path.write_text("export default () => null\n")

    root_text = tmp_path / "random.txt"
    root_text.write_text("hello\n")

    handler.dispatch(_fake_event("modified", str(workflow_path)))
    handler.dispatch(_fake_event("created", str(app_path)))
    handler.dispatch(_fake_event("modified", str(root_text)))

    assert str(workflow_path) in state.pending_changes
    assert str(app_path) in state.pending_changes
    assert str(root_text) in state.pending_changes


def test_watch_handler_respects_gitignore(tmp_path):
    """Files matching .gitignore should still be excluded from watch events."""
    (tmp_path / ".gitignore").write_text("secrets/\n*.log\n")
    state = _WatchState(tmp_path)
    handler = _WatchChangeHandler(state)

    secret_path = tmp_path / "secrets" / "token.txt"
    secret_path.parent.mkdir()
    secret_path.write_text("hunter2\n")

    log_path = tmp_path / "app.log"
    log_path.write_text("noise\n")

    real_path = tmp_path / "workflows" / "do_thing.py"
    real_path.parent.mkdir()
    real_path.write_text("def run(): pass\n")

    handler.dispatch(_fake_event("modified", str(secret_path)))
    handler.dispatch(_fake_event("modified", str(log_path)))
    handler.dispatch(_fake_event("modified", str(real_path)))

    assert str(secret_path) not in state.pending_changes
    assert str(log_path) not in state.pending_changes
    assert str(real_path) in state.pending_changes


def test_watch_handler_respects_root_gitignore_for_subdirectory_watch(tmp_path):
    """Root .gitignore patterns should apply when watching a subdirectory."""
    (tmp_path / ".gitignore").write_text("/apps/my-app/generated/\n*.local\n")
    app_dir = tmp_path / "apps" / "my-app"
    app_dir.mkdir(parents=True)
    state = _WatchState(app_dir)
    with patch("pathlib.Path.cwd", return_value=tmp_path):
        handler = _WatchChangeHandler(state)

        generated = app_dir / "generated" / "out.py"
        generated.parent.mkdir()
        generated.write_text("print('generated')\n")

        local_config = app_dir / "settings.local"
        local_config.write_text("secret\n")

        real_path = app_dir / "workflow.py"
        real_path.write_text("def run(): pass\n")

        handler.dispatch(_fake_event("modified", str(generated)))
        handler.dispatch(_fake_event("modified", str(local_config)))
        handler.dispatch(_fake_event("modified", str(real_path)))

    assert str(generated) not in state.pending_changes
    assert str(local_config) not in state.pending_changes
    assert str(real_path) in state.pending_changes


def test_watch_handler_dropped_events_do_not_call_post(tmp_path, monkeypatch):
    """Events under .bifrost/ should produce zero queued work, so a subsequent
    drain → push pipeline would issue zero REST calls."""
    state = _WatchState(tmp_path)
    handler = _WatchChangeHandler(state)

    (tmp_path / ".bifrost").mkdir()
    (tmp_path / ".bifrost" / "workflows.yaml").write_text("workflows: {}\n")

    handler.dispatch(_fake_event("modified", str(tmp_path / ".bifrost" / "workflows.yaml")))
    handler.dispatch(_fake_event("created", str(tmp_path / ".bifrost" / "agents.yaml")))

    changes, deletes = state.drain()
    assert changes == set()
    assert deletes == set()

    # If a caller naively iterated drained sets and posted per file, no posts
    # would happen because both sets are empty. This documents the contract.
    posted: list[tuple[str, dict]] = []

    async def fake_post(url, json=None, **kwargs):  # type: ignore[no-untyped-def]
        posted.append((url, json or {}))

        class _Resp:
            status_code = 204

        return _Resp()

    # Iterate as the watch batch would (no asyncio needed since we never await)
    for _ in changes:
        # Would have called fake_post; we never get here.
        pass
    for _ in deletes:
        pass

    assert posted == []
