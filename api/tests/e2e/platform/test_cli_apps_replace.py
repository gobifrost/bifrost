"""E2E tests for ``bifrost apps replace`` CLI command.

Exercises the full CLI → REST → DB chain for repointing an application's
``repo_path``:

* Happy path — source files exist at the new prefix, ``replace`` succeeds
  and the DB row's ``repo_path`` is updated (verified via the regenerated
  manifest, which is the only surface that exposes ``repo_path`` today).
* Missing-source guard — replacing to a prefix with no files in
  ``file_index`` fails with exit code 1 and surfaces the server's
  "no files" message on stderr.
* Duplicate guard — replacing to a ``repo_path`` already claimed by
  another app fails with exit code 1 and surfaces the server's
  "already claimed" message on stderr.
* ``--force`` bypass — forcing lets the replace succeed even when no
  files exist at the destination (the repoint-ahead-of-push use case).
* Manifest round-trip — the regenerated ``.bifrost/apps.yaml`` carries
  the expected ``path:`` entry, confirming the export captures
  ``repo_path`` correctly.

The commands are invoked via :class:`click.testing.CliRunner` against the
real API stack (the same mechanism used by :mod:`test_cli_apps` and
:mod:`test_cli_workflows` — see ``conftest.py`` for ``cli_client`` /
``invoke_cli`` fixture plumbing).
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
import yaml

from bifrost.commands.apps import apps_group


@pytest.fixture
def _invoke(invoke_cli):
    """Per-file binding: ``_invoke(args)`` → ``invoke_cli(apps_group, args)``."""
    return lambda args: invoke_cli(apps_group, args)


def _create_app(e2e_client, platform_admin, slug: str) -> str:
    """Create an app via REST; returns the new application UUID."""
    resp = e2e_client.post(
        "/api/applications",
        headers=platform_admin.headers,
        json={"name": slug, "slug": slug, "app_model": "inline_v1"},
    )
    assert resp.status_code == 201, (
        f"Create app {slug!r} failed: {resp.status_code} {resp.text}"
    )
    return resp.json()["id"]


def _seed_file(e2e_client, platform_admin, path: str) -> None:
    """Write a throw-away file so ``file_index`` has a row under ``path``'s prefix."""
    resp = e2e_client.put(
        "/api/files/editor/content",
        headers=platform_admin.headers,
        json={
            "path": path,
            "content": "// seeded by test_cli_apps_replace\nexport default null;\n",
            "encoding": "utf-8",
        },
    )
    assert resp.status_code in (200, 201), (
        f"Seed file {path!r} failed: {resp.status_code} {resp.text}"
    )


def _get_repo_path_from_manifest(
    e2e_client, platform_admin, app_id: str
) -> str | None:
    """Look up an application's current ``repo_path`` via the manifest export.

    ``ApplicationPublic`` doesn't expose ``repo_path``; the regenerated manifest
    (``GET /api/files/manifest``) is the supported read surface for that field.
    Returns ``None`` if the app isn't in the manifest.
    """
    resp = e2e_client.get(
        "/api/files/manifest", headers=platform_admin.headers
    )
    assert resp.status_code == 200, f"Manifest fetch failed: {resp.text}"
    files = resp.json()
    apps_yaml = files.get(".bifrost/apps.yaml") or files.get("apps.yaml")
    if not apps_yaml:
        return None
    data = yaml.safe_load(apps_yaml) or {}
    apps = data.get("apps", {}) if isinstance(data, dict) else {}
    entry = apps.get(app_id)
    if not isinstance(entry, dict):
        return None
    return entry.get("path")


@pytest.mark.e2e
class TestCliAppsReplace:
    """End-to-end coverage for ``bifrost apps replace``."""

    def test_replace_happy_path_updates_repo_path(
        self, cli_client, _invoke, e2e_client, platform_admin
    ) -> None:
        """Source files at the new prefix → ``replace`` succeeds; DB updated."""
        slug = f"cli-app-rep-{uuid4().hex[:8]}"
        app_id = _create_app(e2e_client, platform_admin, slug)

        new_path = f"apps/cli-rep-target-{uuid4().hex[:8]}"
        _seed_file(e2e_client, platform_admin, f"{new_path}/src/App.tsx")

        try:
            result = _invoke(
                ["--json", "replace", slug, "--repo-path", new_path]
            )
            assert result.exit_code == 0, (
                f"stdout={result.output!r} stderr={result.stderr!r}"
            )
            payload = json.loads(result.output)
            assert str(payload["id"]) == app_id

            # DB-side verification: regenerated manifest reflects the new path.
            current = _get_repo_path_from_manifest(
                e2e_client, platform_admin, app_id
            )
            assert current == new_path, (
                f"expected repo_path={new_path!r}, manifest says {current!r}"
            )
        finally:
            _invoke(["--json", "delete", app_id])

    def test_replace_rejects_missing_source_without_force(
        self, cli_client, _invoke, e2e_client, platform_admin
    ) -> None:
        """No files under the new prefix → exit 1 with server's "no files" message."""
        slug = f"cli-app-empty-{uuid4().hex[:8]}"
        app_id = _create_app(e2e_client, platform_admin, slug)
        missing_path = f"apps/cli-empty-dst-{uuid4().hex[:8]}"

        try:
            result = _invoke(
                ["--json", "replace", slug, "--repo-path", missing_path]
            )
            assert result.exit_code == 1, (
                f"stdout={result.output!r} stderr={result.stderr!r}"
            )
            # Server error body is echoed on stderr by ``_print_http_error``.
            assert "no files" in result.stderr, result.stderr

            # Nothing changed on the DB side.
            current = _get_repo_path_from_manifest(
                e2e_client, platform_admin, app_id
            )
            assert current == f"apps/{slug}", (
                f"repo_path should be unchanged, manifest says {current!r}"
            )
        finally:
            _invoke(["--json", "delete", app_id])

    def test_replace_rejects_duplicate_without_force(
        self, cli_client, _invoke, e2e_client, platform_admin
    ) -> None:
        """Repointing app A to app B's path → exit 1 with "already claimed"."""
        slug_a = f"cli-app-dup-a-{uuid4().hex[:8]}"
        slug_b = f"cli-app-dup-b-{uuid4().hex[:8]}"
        app_a = _create_app(e2e_client, platform_admin, slug_a)
        app_b = _create_app(e2e_client, platform_admin, slug_b)

        try:
            # App B's auto-assigned repo_path is ``apps/{slug_b}``; try
            # to claim it for A without --force.
            result = _invoke(
                ["--json", "replace", slug_a, "--repo-path", f"apps/{slug_b}"]
            )
            assert result.exit_code == 1, (
                f"stdout={result.output!r} stderr={result.stderr!r}"
            )
            assert "already claimed" in result.stderr, result.stderr

            # A's path should be unchanged.
            current = _get_repo_path_from_manifest(
                e2e_client, platform_admin, app_a
            )
            assert current == f"apps/{slug_a}", (
                f"app A's repo_path should be unchanged, manifest says {current!r}"
            )
        finally:
            _invoke(["--json", "delete", app_a])
            _invoke(["--json", "delete", app_b])

    def test_replace_force_bypasses_source_exists_check(
        self, cli_client, _invoke, e2e_client, platform_admin
    ) -> None:
        """``--force`` lets replace succeed even with no files at destination."""
        slug = f"cli-app-force-{uuid4().hex[:8]}"
        app_id = _create_app(e2e_client, platform_admin, slug)
        new_path = f"apps/cli-force-dst-{uuid4().hex[:8]}"

        try:
            result = _invoke(
                [
                    "--json",
                    "replace",
                    slug,
                    "--repo-path",
                    new_path,
                    "--force",
                ]
            )
            assert result.exit_code == 0, (
                f"stdout={result.output!r} stderr={result.stderr!r}"
            )
            payload = json.loads(result.output)
            assert str(payload["id"]) == app_id

            current = _get_repo_path_from_manifest(
                e2e_client, platform_admin, app_id
            )
            assert current == new_path, (
                f"expected repo_path={new_path!r}, manifest says {current!r}"
            )
        finally:
            _invoke(["--json", "delete", app_id])

    def test_manifest_round_trip_includes_repo_path(
        self, cli_client, _invoke, e2e_client, platform_admin
    ) -> None:
        """The regenerated ``apps.yaml`` carries ``path: apps/<slug>``.

        Simplified round-trip check: confirm the export surface captures
        ``repo_path`` as the ``path`` field in ``ManifestApp``, which is the
        handoff point between DB state and portable bundles.
        """
        slug = f"mr-app-{uuid4().hex[:8]}"
        app_id = _create_app(e2e_client, platform_admin, slug)

        try:
            resp = e2e_client.get(
                "/api/files/manifest", headers=platform_admin.headers
            )
            assert resp.status_code == 200, resp.text
            files = resp.json()
            apps_yaml = files.get(".bifrost/apps.yaml") or files.get("apps.yaml")
            assert apps_yaml, (
                f"expected apps.yaml in manifest files, got keys={list(files)}"
            )
            data = yaml.safe_load(apps_yaml) or {}
            apps = data.get("apps", {})
            assert app_id in apps, (
                f"expected app {app_id} in apps.yaml, got keys={list(apps)}"
            )
            entry = apps[app_id]
            assert entry.get("path") == f"apps/{slug}", (
                f"expected path=apps/{slug}, got entry={entry}"
            )
        finally:
            _invoke(["--json", "delete", app_id])
