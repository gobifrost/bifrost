"""GET /api/applications/{app_id}/bundle-manifest must not trigger a
_repo build/write for a non-bypass caller — it serves the existing
manifest as-is (even if stale-schema) or 404s if none exists yet."""

import uuid

import pytest
import pytest_asyncio


async def _set_role_permissions(
    session_factory, role_id: str, permissions: frozenset[str]
) -> None:
    from src.services.role_permissions import set_role_permissions

    async with session_factory() as session:
        await set_role_permissions(
            session,
            role_id=uuid.UUID(role_id),
            permissions=permissions,
        )
        await session.commit()


@pytest.mark.e2e
class TestBundleManifestNonBypassNeverBuilds:
    @pytest_asyncio.fixture
    async def inline_app(
        self, e2e_client, platform_admin, org1, org1_user, async_session_factory
    ):
        """Created by platform_admin scoped into org1 (application creation
        is bypass-only).  Grant the reader only ``apps.read`` at org1 so
        draft-manifest GET may read but can never trigger a rebuild."""
        slug = f"bundle-manifest-rv-{uuid.uuid4().hex[:8]}"
        role_resp = e2e_client.post(
            "/api/roles",
            headers=platform_admin.headers,
            json={"name": f"Contoso Bundle Manifest Reader {slug}"},
        )
        assert role_resp.status_code == 201, role_resp.text
        role = role_resp.json()
        await _set_role_permissions(
            async_session_factory,
            role["id"],
            frozenset({"apps.read"}),
        )
        assignment_resp = e2e_client.post(
            f"/api/roles/{role['id']}/users",
            headers=platform_admin.headers,
            json={"user_ids": [str(org1_user.user_id)]},
        )
        assert assignment_resp.status_code == 204, assignment_resp.text
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
        e2e_client.delete(f"/api/roles/{role['id']}", headers=platform_admin.headers)

    async def test_non_bypass_caller_gets_404_when_no_manifest_exists_yet(
        self, e2e_client, org1_user, inline_app
    ):
        """No manifest built yet (inline_v1 apps auto-scaffold+build on
        creation, so remove the auto-created one to reach this state): a
        regular user must 404, never trigger the build themselves."""
        from src.services.app_storage import AppStorageService

        await AppStorageService().delete_preview_file(
            inline_app["id"], "manifest.json"
        )

        resp = e2e_client.get(
            f"/api/applications/{inline_app['id']}/bundle-manifest",
            headers=org1_user.headers,
            params={"mode": "draft"},
        )
        assert resp.status_code == 404, resp.text

    async def test_non_bypass_caller_gets_stale_manifest_as_is(
        self, e2e_client, org1_user, inline_app
    ):
        """A stale-schema manifest already on disk is served unchanged —
        no rebuild triggered by the regular caller's GET."""
        import json

        from src.services.app_storage import AppStorageService

        stale_manifest = {
            "schema_version": 1,  # older than current SCHEMA_VERSION
            "entry": "STALE_SENTINEL.js",
            "css": None,
            "dependencies": {},
        }

        await AppStorageService().write_preview_file(
            inline_app["id"],
            "manifest.json",
            json.dumps(stale_manifest).encode(),
        )

        resp = e2e_client.get(
            f"/api/applications/{inline_app['id']}/bundle-manifest",
            headers=org1_user.headers,
            params={"mode": "draft"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["entry"] == "STALE_SENTINEL.js"
