"""GET /api/applications/{app_id}/bundle-manifest must not trigger a
_repo build/write for a non-bypass caller — it serves the existing
manifest as-is (even if stale-schema) or 404s if none exists yet."""

import uuid

import pytest


@pytest.mark.e2e
class TestBundleManifestNonBypassNeverBuilds:
    @pytest.fixture
    def inline_app(self, e2e_client, platform_admin, org1, org1_user):
        """Created by platform_admin scoped into org1 (application creation
        is bypass-only), so org1_user's own read access against it is
        unambiguous."""
        slug = f"bundle-manifest-rv-{uuid.uuid4().hex[:8]}"
        resp = e2e_client.post(
            "/api/applications",
            headers=platform_admin.headers,
            json={
                "name": slug,
                "slug": slug,
                "app_model": "inline_v1",
                "organization_id": org1["id"],
            },
        )
        assert resp.status_code == 201, resp.text
        app = resp.json()
        yield app
        e2e_client.delete(f"/api/applications/{app['id']}", headers=platform_admin.headers)

    def test_non_bypass_caller_gets_404_when_no_manifest_exists_yet(
        self, e2e_client, org1_user, inline_app
    ):
        """No manifest built yet (inline_v1 apps auto-scaffold+build on
        creation, so remove the auto-created one to reach this state): a
        regular user must 404, never trigger the build themselves."""
        import asyncio

        from src.services.app_storage import AppStorageService

        asyncio.run(
            AppStorageService().delete_preview_file(
                inline_app["id"], "manifest.json"
            )
        )

        resp = e2e_client.get(
            f"/api/applications/{inline_app['id']}/bundle-manifest",
            headers=org1_user.headers,
            params={"mode": "draft"},
        )
        assert resp.status_code == 404, resp.text

    def test_non_bypass_caller_gets_stale_manifest_as_is(
        self, e2e_client, org1_user, inline_app
    ):
        """A stale-schema manifest already on disk is served unchanged —
        no rebuild triggered by the regular caller's GET."""
        import asyncio
        import json

        from src.services.app_storage import AppStorageService

        stale_manifest = {
            "schema_version": 1,  # older than current SCHEMA_VERSION
            "entry": "STALE_SENTINEL.js",
            "css": None,
            "dependencies": {},
        }

        async def _write_stale():
            await AppStorageService().write_preview_file(
                inline_app["id"],
                "manifest.json",
                json.dumps(stale_manifest).encode(),
            )

        asyncio.run(_write_stale())

        resp = e2e_client.get(
            f"/api/applications/{inline_app['id']}/bundle-manifest",
            headers=org1_user.headers,
            params={"mode": "draft"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["entry"] == "STALE_SENTINEL.js"
