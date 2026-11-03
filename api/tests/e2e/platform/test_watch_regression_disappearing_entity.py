"""Regression coverage for the disappearing-entity race (plan Task 11).

The original bug (plan lines 12-19): the UI creates an event source via
``POST /api/events/sources`` while a ``bifrost watch`` session in another
terminal picks up an unrelated file change.  The old watch batch also
re-pushed the local ``.bifrost/`` manifest, which did NOT contain the
freshly-created entity — so server-side manifest import with
``delete_removed_entities=True`` promptly deleted the entity the user had
just created.

Task 8 fixed this structurally by:

* Excluding ``.bifrost/`` from the watchdog observer (see
  ``_WatchChangeHandler`` in ``api/bifrost/cli.py`` — ``_spec`` layers a
  ``.bifrost/`` exclusion on top of the shared push/pull filter).
* Deleting the manifest-push branch of ``_process_watch_batch``. Watch now
  only calls per-file ``/api/files/write`` and ``/api/files/delete``; it
  never calls ``/api/files/manifest/import`` and never sends ``.bifrost/``
  content to the server.

This test asserts the race is structurally impossible by driving the watch
internals directly against the real API:

1. Create an event source via the API (the "UI side" of the race).
2. Stage both an unrelated ``workflows/foo.py`` change AND a ``.bifrost/
   events.yaml`` change in a temp workspace, and dispatch watchdog-shaped
   events through ``_WatchChangeHandler``. The handler must skip
   ``.bifrost/`` before anything reaches the queue.
3. Run ``_process_watch_batch`` against a ``BifrostClient`` wrapped so every
   HTTP call is captured.
4. Assert:
   * The event source still exists (``GET /api/events/sources/{id}`` → 200).
   * No recorded call is a DELETE against ``/api/events/sources/...``.
   * No recorded call targets ``/api/files/manifest/import`` (the old bulk
     push path is gone).
   * ``.bifrost/events.yaml`` was never queued as a push (structural check
     on the handler's filter).
"""

from __future__ import annotations

import asyncio
import pathlib
import sys
from uuid import uuid4

import httpx
import pytest

# Standalone bifrost package import (mirrors test_cli_orgs.py etc.).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from bifrost.cli import (  # noqa: E402
    _WatchChangeHandler,
    _WatchState,
    _process_watch_batch,
)
from bifrost.client import BifrostClient  # noqa: E402


class _RecordingClient(BifrostClient):
    """``BifrostClient`` that records every HTTP call it makes.

    Used so the regression test can assert — end-to-end and unambiguously —
    that watch never issues a DELETE against the event-source REST surface
    and never hits the manifest-import endpoint. If someone re-introduces
    the bulk manifest push path, one of those assertions will fail.
    """

    def __init__(self, api_url: str, access_token: str) -> None:
        super().__init__(api_url, access_token)
        self.calls: list[tuple[str, str]] = []

    async def _request_with_refresh(  # type: ignore[override]
        self, method: str, path: str, **kwargs: object
    ) -> httpx.Response:
        self.calls.append((method.upper(), path))
        return await super()._request_with_refresh(method, path, **kwargs)


class _FakeWatchdogEvent:
    """Duck-typed stand-in for ``watchdog.events.FileSystemEvent``.

    ``_WatchChangeHandler.dispatch`` only reads ``is_directory``,
    ``event_type``, and ``src_path`` (plus ``dest_path`` for moves), so a
    plain object is enough to exercise the filter without spinning up a
    real watchdog Observer thread inside a test.
    """

    def __init__(
        self,
        src_path: str,
        event_type: str = "modified",
        is_directory: bool = False,
        dest_path: str = "",
    ) -> None:
        self.src_path = src_path
        self.event_type = event_type
        self.is_directory = is_directory
        self.dest_path = dest_path


def _create_event_source(e2e_client, platform_admin) -> dict:
    """Create a webhook event source via the API and return its payload."""
    name = f"watch-regression-{uuid4().hex[:8]}"
    resp = e2e_client.post(
        "/api/events/sources",
        headers=platform_admin.headers,
        json={
            "name": name,
            "source_type": "webhook",
            "webhook": {
                "adapter_name": "generic",
                "config": {},
            },
        },
    )
    assert resp.status_code == 201, (
        f"Failed to create event source: {resp.status_code} {resp.text}"
    )
    return resp.json()


@pytest.mark.e2e
class TestWatchDisappearingEntityRegression:
    """Structural regression test for the disappearing-entity race."""

    def test_watch_does_not_delete_concurrently_created_entity(
        self,
        tmp_path: pathlib.Path,
        e2e_api_url: str,
        e2e_client: httpx.Client,
        platform_admin,
    ) -> None:
        """End-to-end: concurrent entity create + watch batch leaves the entity alive.

        Exercises the real watch handler + ``_process_watch_batch`` code
        path against the real API. The scenario re-creates the original
        bug: a manifest file on disk that DOES NOT reference the just-
        created entity. Under the old bulk-push behavior, watch would have
        shipped that manifest to ``/api/files/manifest/import`` with
        ``delete_removed_entities=True`` and killed the entity.  Today,
        ``.bifrost/`` is filtered out by the observer and there is no
        manifest import call in ``_process_watch_batch`` at all.
        """
        # --- Step 1: UI side of the race — create an event source via API.
        source = _create_event_source(e2e_client, platform_admin)
        source_id = source["id"]

        # --- Step 2: Workspace with BOTH an unrelated change AND a .bifrost/
        # manifest that would (under the old code path) have caused a
        # server-side delete of the just-created entity.
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "workflows").mkdir()
        foo_path = workspace / "workflows" / "foo.py"
        foo_path.write_text(
            "from src.sdk import workflow\n\n"
            "@workflow\n"
            "def foo() -> str:\n"
            "    return 'ok'\n",
            encoding="utf-8",
        )

        bifrost_dir = workspace / ".bifrost"
        bifrost_dir.mkdir()
        # A manifest with ZERO event sources. If this ever reached the
        # server with the old bulk-push semantics, the sync would treat
        # the freshly-created source as "removed" and delete it.
        events_yaml = bifrost_dir / "events.yaml"
        events_yaml.write_text("event_sources: {}\n", encoding="utf-8")

        # --- Step 3: Wire up watch state + handler exactly like
        # ``_watch_and_push`` does, minus the real Observer thread.
        state = _WatchState(workspace)
        handler = _WatchChangeHandler(state)

        # Dispatch BOTH events. ``.bifrost/events.yaml`` must be silently
        # filtered by ``_WatchChangeHandler._spec``; the unrelated change
        # must land in ``pending_changes``. This is the core structural
        # guarantee: the race is impossible because manifest events never
        # reach the queue in the first place.
        handler.dispatch(
            _FakeWatchdogEvent(str(events_yaml), event_type="modified")
        )
        handler.dispatch(
            _FakeWatchdogEvent(str(foo_path), event_type="modified")
        )

        changes, deletes = state.drain()

        # Manifest file MUST NOT have been queued.
        assert str(events_yaml) not in changes, (
            "Regression: .bifrost/events.yaml was queued for push. The "
            "observer filter is supposed to drop manifest events before "
            "they reach pending_changes. If this fires, the disappearing-"
            "entity race is back."
        )
        # The unrelated change must be present so the batch actually runs
        # through the network path (otherwise this test would vacuously
        # pass even if someone broke the filter in a different way).
        assert str(foo_path) in changes
        assert not deletes

        # --- Step 4: Run the real batch against the real API with a
        # recording client.
        client = _RecordingClient(e2e_api_url, platform_admin.access_token)

        async def _run_batch() -> None:
            try:
                await _process_watch_batch(
                    client,
                    changes,
                    deletes,
                    workspace,
                    repo_prefix="",
                    state=state,
                    watch_app=None,
                )
            finally:
                # Release the httpx transport bound to this loop.
                await client.close()

        asyncio.run(_run_batch())

        # --- Step 5: The event source must still exist.
        get_resp = e2e_client.get(
            f"/api/events/sources/{source_id}",
            headers=platform_admin.headers,
        )
        assert get_resp.status_code == 200, (
            f"Entity disappeared after watch batch: status={get_resp.status_code} "
            f"body={get_resp.text}"
        )
        assert get_resp.json()["id"] == source_id

        # --- Step 6: Inspect recorded REST traffic.
        # 6a: No DELETE against /api/events/sources/...  — this would be
        # the direct smoking gun if the race returned.
        assert not any(
            method == "DELETE" and path.startswith("/api/events/sources/")
            for method, path in client.calls
        ), (
            "Regression: watch issued a DELETE against /api/events/sources/. "
            f"Recorded calls: {client.calls}"
        )
        # 6b: No manifest-import call — the old bulk push path went
        # through ``/api/files/manifest/import`` with
        # ``delete_removed_entities=True``. Task 8 removed that branch
        # entirely; if it comes back, this assertion fails.
        assert not any(
            "/api/files/manifest/import" in path for _, path in client.calls
        ), (
            "Regression: watch called /api/files/manifest/import. The bulk "
            f"manifest push path is supposed to be gone. Calls: {client.calls}"
        )
        # 6c: Sanity — the batch DID run a network call (the unrelated
        # write) so the above negative assertions are meaningful.
        assert any(
            method == "POST" and path == "/api/files/write"
            for method, path in client.calls
        ), (
            "Expected at least one /api/files/write call for the unrelated "
            f"workflow change. Calls: {client.calls}"
        )

        # --- Cleanup: delete the event source so the test doesn't leak
        # state into other E2E tests.
        e2e_client.delete(
            f"/api/events/sources/{source_id}",
            headers=platform_admin.headers,
        )

    def test_watch_handler_filter_skips_bifrost_directory(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """Pure structural check: ``_WatchChangeHandler`` drops every event
        under ``.bifrost/`` regardless of the file name.

        This is the invariant that makes the disappearing-entity race
        impossible: even if a future change accidentally re-introduces a
        manifest-push code path in ``_process_watch_batch``, nothing under
        ``.bifrost/`` will ever be in the ``changes`` set passed to it.
        """
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        bifrost_dir = workspace / ".bifrost"
        bifrost_dir.mkdir()

        state = _WatchState(workspace)
        handler = _WatchChangeHandler(state)

        # Dispatch every manifest filename we actually serialize today.
        for filename in (
            "events.yaml",
            "workflows.yaml",
            "forms.yaml",
            "agents.yaml",
            "apps.yaml",
            "integrations.yaml",
            "organizations.yaml",
            "roles.yaml",
            "tables.yaml",
        ):
            manifest_file = bifrost_dir / filename
            manifest_file.write_text("{}\n", encoding="utf-8")
            handler.dispatch(
                _FakeWatchdogEvent(str(manifest_file), event_type="modified")
            )
            handler.dispatch(
                _FakeWatchdogEvent(str(manifest_file), event_type="deleted")
            )
            handler.dispatch(
                _FakeWatchdogEvent(str(manifest_file), event_type="created")
            )

        changes, deletes = state.drain()
        assert changes == set(), (
            f"Manifest files leaked into push queue: {changes}. The watch "
            "observer filter is supposed to exclude .bifrost/ entirely."
        )
        assert deletes == set(), (
            f"Manifest files leaked into delete queue: {deletes}. The "
            "watch observer filter is supposed to exclude .bifrost/ entirely."
        )
