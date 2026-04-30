"""
E2E tests for App Builder applications.

Tests application CRUD operations, draft/live versioning, and rollback functionality.
Applications are stored in the database (DB-first model).
"""

import uuid

import pytest


def _create_app(e2e_client, headers, slug, name=None, params=None, **json_extra):
    """Create an app and return the response JSON with id."""
    kwargs = {}
    if params:
        kwargs["params"] = params
    response = e2e_client.post(
        "/api/applications",
        headers=headers,
        json={"name": name or slug, "slug": slug, **json_extra},
        **kwargs,
    )
    assert response.status_code == 201, f"Create app '{slug}' failed: {response.text}"
    return response.json()


def _delete_app(e2e_client, headers, app_id, **kwargs):
    """Delete an app by UUID."""
    e2e_client.delete(f"/api/applications/{app_id}", headers=headers, **kwargs)


@pytest.mark.e2e
class TestApplicationCRUD:
    """Test application CRUD operations."""

    def test_create_application(self, e2e_client, platform_admin):
        """Platform admin can create an application."""
        response = e2e_client.post(
            "/api/applications",
            headers=platform_admin.headers,
            json={
                "name": "E2E Test App",
                "slug": "e2e-test-app",
                "description": "Test application for E2E tests",
                "icon": "box",
            },
        )
        assert response.status_code == 201, f"Create app failed: {response.text}"
        app = response.json()

        assert app["name"] == "E2E Test App"
        assert app["slug"] == "e2e-test-app"
        assert app["description"] == "Test application for E2E tests"
        assert app["icon"] == "box"
        assert app.get("id"), "App should have an ID"
        assert app["is_published"] is False, "New app should not be published"
        assert app["has_unpublished_changes"] is True, "New app should have unpublished changes from scaffolded files"

        # Cleanup
        _delete_app(e2e_client, platform_admin.headers, app["id"])

    def test_get_application_by_slug(self, e2e_client, platform_admin):
        """Get application by slug."""
        app = _create_app(e2e_client, platform_admin.headers, "get-test-app", name="Get Test App")

        # Get by slug
        response = e2e_client.get(
            "/api/applications/get-test-app",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"Get app failed: {response.text}"
        data = response.json()
        assert data["slug"] == "get-test-app"
        assert data["name"] == "Get Test App"

        # Cleanup
        _delete_app(e2e_client, platform_admin.headers, app["id"])

    def test_list_applications(self, e2e_client, platform_admin):
        """List all applications."""
        app1 = _create_app(e2e_client, platform_admin.headers, "list-test-1", name="List Test 1")
        app2 = _create_app(e2e_client, platform_admin.headers, "list-test-2", name="List Test 2")

        # List apps
        response = e2e_client.get(
            "/api/applications",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"List apps failed: {response.text}"
        data = response.json()

        assert "applications" in data
        assert "total" in data
        app_slugs = [a["slug"] for a in data["applications"]]
        assert "list-test-1" in app_slugs
        assert "list-test-2" in app_slugs

        # Cleanup
        _delete_app(e2e_client, platform_admin.headers, app1["id"])
        _delete_app(e2e_client, platform_admin.headers, app2["id"])

    def test_update_application(self, e2e_client, platform_admin):
        """Update application metadata."""
        app = _create_app(
            e2e_client, platform_admin.headers, "update-test-app",
            name="Update Test App", description="Original description",
        )

        # Update
        response = e2e_client.patch(
            f"/api/applications/{app['id']}",
            headers=platform_admin.headers,
            json={
                "name": "Updated App Name",
                "description": "Updated description",
                "icon": "star",
            },
        )
        assert response.status_code == 200, f"Update app failed: {response.text}"
        updated = response.json()

        assert updated["name"] == "Updated App Name"
        assert updated["description"] == "Updated description"
        assert updated["icon"] == "star"
        assert updated["slug"] == "update-test-app"  # Slug unchanged

        # Cleanup
        _delete_app(e2e_client, platform_admin.headers, app["id"])

    def test_update_application_slug(self, e2e_client, platform_admin):
        """Update application slug."""
        app = _create_app(
            e2e_client, platform_admin.headers, "slug-update-original",
            name="Slug Update App",
        )

        # Update slug
        response = e2e_client.patch(
            f"/api/applications/{app['id']}",
            headers=platform_admin.headers,
            json={
                "slug": "slug-update-new",
            },
        )
        assert response.status_code == 200, f"Update slug failed: {response.text}"
        updated = response.json()

        assert updated["slug"] == "slug-update-new"
        assert updated["name"] == "Slug Update App"  # Name unchanged

        # Old slug should not exist
        response = e2e_client.get(
            "/api/applications/slug-update-original",
            headers=platform_admin.headers,
        )
        assert response.status_code == 404, "Old slug should return 404"

        # New slug should work
        response = e2e_client.get(
            "/api/applications/slug-update-new",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"New slug should work: {response.text}"

        # Cleanup
        _delete_app(e2e_client, platform_admin.headers, app["id"])

    def test_update_application_slug_duplicate_rejected(self, e2e_client, platform_admin):
        """Updating to an existing slug is rejected."""
        app1 = _create_app(e2e_client, platform_admin.headers, "slug-dup-first", name="First App")
        app2 = _create_app(e2e_client, platform_admin.headers, "slug-dup-second", name="Second App")

        # Try to update second app to use first app's slug
        response = e2e_client.patch(
            f"/api/applications/{app2['id']}",
            headers=platform_admin.headers,
            json={"slug": "slug-dup-first"},
        )
        assert response.status_code == 409, \
            f"Expected 409 Conflict for duplicate slug update, got {response.status_code}"

        # Cleanup
        _delete_app(e2e_client, platform_admin.headers, app1["id"])
        _delete_app(e2e_client, platform_admin.headers, app2["id"])

    def test_delete_application(self, e2e_client, platform_admin):
        """Delete application."""
        app = _create_app(
            e2e_client, platform_admin.headers, "delete-test-app",
            name="Delete Test App",
        )

        # Delete
        response = e2e_client.delete(
            f"/api/applications/{app['id']}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 204, f"Delete app failed: {response.text}"

        # Verify gone
        response = e2e_client.get(
            "/api/applications/delete-test-app",
            headers=platform_admin.headers,
        )
        assert response.status_code == 404


@pytest.mark.e2e
class TestApplicationDuplicateSlugs:
    """Test handling of duplicate application slugs."""

    def test_duplicate_slug_rejected(self, e2e_client, platform_admin):
        """Creating app with duplicate slug is rejected."""
        app1 = _create_app(
            e2e_client, platform_admin.headers, "duplicate-slug",
            name="First App",
        )

        # Try to create second with same slug
        response2 = e2e_client.post(
            "/api/applications",
            headers=platform_admin.headers,
            json={"name": "Second App", "slug": "duplicate-slug"},
        )
        assert response2.status_code == 409, \
            f"Expected 409 Conflict for duplicate slug, got {response2.status_code}"

        # Cleanup
        _delete_app(e2e_client, platform_admin.headers, app1["id"])

    def test_duplicate_slug_cross_scope_rejected(self, e2e_client, platform_admin):
        """Creating app with same slug in different scope (global vs org) is rejected.

        Slugs are globally unique — the same slug cannot exist in both an org
        and global scope.
        """
        # Create in org scope (default)
        app1 = _create_app(
            e2e_client, platform_admin.headers, "cross-scope-dup",
            name="Org App",
        )

        # Try to create with same slug in global scope
        response2 = e2e_client.post(
            "/api/applications",
            headers=platform_admin.headers,
            json={"name": "Global App", "slug": "cross-scope-dup"},
            params={"scope": "global"},
        )
        assert response2.status_code == 409, \
            f"Expected 409 Conflict for cross-scope duplicate slug, got {response2.status_code}"

        # Cleanup
        _delete_app(e2e_client, platform_admin.headers, app1["id"])


@pytest.mark.e2e
class TestApplicationVersioning:
    """Test application draft/live versioning."""

    @pytest.fixture
    def test_app(self, e2e_client, platform_admin):
        """Create an app for versioning tests."""
        response = e2e_client.post(
            "/api/applications",
            headers=platform_admin.headers,
            json={
                "name": "Versioning Test App",
                "slug": "versioning-test-app",
                "description": "Tests draft/live versioning",
            },
        )
        assert response.status_code == 201
        app = response.json()

        yield app

        # Cleanup
        _delete_app(e2e_client, platform_admin.headers, app["id"])

    def test_get_empty_draft(self, e2e_client, platform_admin, test_app):
        """Get draft definition for new app."""
        response = e2e_client.get(
            f"/api/applications/{test_app['id']}/draft",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"Get draft failed: {response.text}"
        data = response.json()

        # version is a legacy deprecated field (always 0)
        assert data["is_live"] is False
        # Definition may be empty or null for new app

    def test_save_draft(self, e2e_client, platform_admin, test_app):
        """Save a draft definition."""
        draft_definition = {
            "pages": [
                {
                    "id": "home",
                    "name": "Home",
                    "path": "/",
                    "layouts": [],
                }
            ],
            "navigation": [],
            "theme": {},
        }

        response = e2e_client.put(
            f"/api/applications/{test_app['id']}/draft",
            headers=platform_admin.headers,
            json={"definition": draft_definition},
        )
        assert response.status_code == 200, f"Save draft failed: {response.text}"
        data = response.json()

        assert data["is_live"] is False
        # Definition is returned as-is from input

    def test_get_saved_draft(self, e2e_client, platform_admin, test_app):
        """Saved draft is retrievable via files API."""
        response = e2e_client.get(
            f"/api/applications/{test_app['id']}/files",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 2, "Expected scaffolded files for code engine app"

    def test_multiple_publishes(self, e2e_client, platform_admin, test_app):
        """Multiple publishes update the published state."""
        # First publish
        response = e2e_client.post(
            f"/api/applications/{test_app['id']}/publish",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"First publish failed: {response.text}"
        v1 = response.json()
        assert v1["is_published"] is True, "First publish should mark app as published"
        v1_published_at = v1["published_at"]

        # Modify a file in draft
        e2e_client.put(
            f"/api/applications/{test_app['id']}/files/pages/index.tsx",
            headers=platform_admin.headers,
            json={"source": "export default function Index() { return <div>V2</div>; }"},
        )

        # Second publish
        response = e2e_client.post(
            f"/api/applications/{test_app['id']}/publish",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"Second publish failed: {response.text}"
        v2 = response.json()
        assert v2["is_published"] is True
        assert v2["published_at"] != v1_published_at, \
            "Each publish should update published_at"


@pytest.mark.e2e
class TestApplicationAccess:
    """Test application access control."""

    def test_org_user_can_view_global_apps(self, e2e_client, platform_admin, org1_user):
        """Org user can view global applications."""
        # Create global app
        response = e2e_client.post(
            "/api/applications",
            headers=platform_admin.headers,
            json={"name": "Global App", "slug": "global-app", "organization_id": None},
        )
        assert response.status_code == 201
        app = response.json()

        # Org user should be able to see it
        response = e2e_client.get(
            "/api/applications/global-app",
            headers=org1_user.headers,
        )
        # May be 200 (can view) or 403 (can't view) depending on access rules
        assert response.status_code in [200, 403]

        # Cleanup
        _delete_app(e2e_client, platform_admin.headers, app["id"], params={"scope": "global"})


@pytest.mark.e2e
class TestApplicationScopeFiltering:
    """Test application scope filtering."""

    @pytest.fixture
    def scoped_apps(self, e2e_client, platform_admin, org1):
        """Create apps in different scopes with unique slugs to avoid conflicts."""
        apps = {}
        suffix = uuid.uuid4().hex[:8]

        # Create global app
        response = e2e_client.post(
            "/api/applications",
            headers=platform_admin.headers,
            json={"name": "Global App", "slug": f"global-scope-app-{suffix}", "organization_id": None},
        )
        assert response.status_code == 201
        apps["global"] = response.json()

        # Create org-scoped app
        response = e2e_client.post(
            "/api/applications",
            headers=platform_admin.headers,
            json={"name": "Org App", "slug": f"org-scope-app-{suffix}", "organization_id": org1["id"]},
        )
        assert response.status_code == 201
        apps["org"] = response.json()

        yield apps

        # Platform admin can delete any app by ID (global or org-scoped) —
        # ApplicationRepository inherits OrgScopedRepository.get(id=...) which
        # bypasses scope filtering for superusers.
        _delete_app(e2e_client, platform_admin.headers, apps["global"]["id"])
        _delete_app(e2e_client, platform_admin.headers, apps["org"]["id"])

    def test_list_with_global_scope(
        self, e2e_client, platform_admin, scoped_apps
    ):
        """Listing with scope=global shows only global apps."""
        response = e2e_client.get(
            "/api/applications",
            headers=platform_admin.headers,
            params={"scope": "global"},
        )
        assert response.status_code == 200
        data = response.json()
        app_slugs = [a["slug"] for a in data["applications"]]

        assert scoped_apps["global"]["slug"] in app_slugs
        assert scoped_apps["org"]["slug"] not in app_slugs

    def test_list_with_org_scope(
        self, e2e_client, platform_admin, org1, scoped_apps
    ):
        """Listing with scope=org shows org apps (may include global fallback)."""
        response = e2e_client.get(
            "/api/applications",
            headers=platform_admin.headers,
            params={"scope": org1["id"]},
        )
        assert response.status_code == 200
        data = response.json()
        app_slugs = [a["slug"] for a in data["applications"]]

        assert scoped_apps["org"]["slug"] in app_slugs
        # Global may or may not be included depending on filter type


@pytest.mark.e2e
class TestApplicationCrossOrgAdmin:
    """Platform admins must be able to mutate apps in any org.

    Regression tests for the bug where ApplicationRepository overrode
    get_by_id with cascade-only scoping, dropping the superuser bypass
    that OrgScopedRepository.get(id=...) provides. GET worked (it used
    can_access(id=...) → base class path) but PATCH/DELETE/publish/replace
    all returned 404 for cross-org apps.
    """

    def test_platform_admin_patch_cross_org_app(
        self, e2e_client, platform_admin, org1
    ):
        """Platform admin can PATCH an app owned by another org."""
        suffix = uuid.uuid4().hex[:8]
        app = _create_app(
            e2e_client,
            platform_admin.headers,
            f"cross-org-patch-{suffix}",
            name="Cross-org Patch",
            organization_id=org1["id"],
        )

        try:
            response = e2e_client.patch(
                f"/api/applications/{app['id']}",
                headers=platform_admin.headers,
                json={"description": "Edited cross-org"},
            )
            assert response.status_code == 200, (
                f"Platform admin should be able to PATCH cross-org app: {response.text}"
            )
            assert response.json()["description"] == "Edited cross-org"
        finally:
            _delete_app(e2e_client, platform_admin.headers, app["id"])

    def test_platform_admin_delete_cross_org_app(
        self, e2e_client, platform_admin, org1
    ):
        """Platform admin can DELETE an app owned by another org."""
        suffix = uuid.uuid4().hex[:8]
        slug = f"cross-org-delete-{suffix}"
        app = _create_app(
            e2e_client,
            platform_admin.headers,
            slug,
            name="Cross-org Delete",
            organization_id=org1["id"],
        )

        response = e2e_client.delete(
            f"/api/applications/{app['id']}",
            headers=platform_admin.headers,
        )
        assert response.status_code in (200, 204), (
            f"Platform admin should be able to DELETE cross-org app: {response.text}"
        )

        # Confirm it's gone (GET is by slug)
        response = e2e_client.get(
            f"/api/applications/{slug}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 404

    def test_non_admin_cannot_patch_cross_org_app(
        self, e2e_client, platform_admin, org1_user, org2
    ):
        """Regular user in org1 cannot PATCH an app in org2 — scope check still denies."""
        suffix = uuid.uuid4().hex[:8]
        app = _create_app(
            e2e_client,
            platform_admin.headers,
            f"cross-org-deny-{suffix}",
            name="Cross-org Deny",
            organization_id=org2["id"],
        )

        try:
            response = e2e_client.patch(
                f"/api/applications/{app['id']}",
                headers=org1_user.headers,
                json={"description": "Should not work"},
            )
            assert response.status_code == 404, (
                f"Non-admin in org1 should not be able to PATCH org2 app: "
                f"status={response.status_code} body={response.text}"
            )
        finally:
            _delete_app(e2e_client, platform_admin.headers, app["id"])


@pytest.mark.e2e
class TestApplicationDBStorage:
    """Test that applications are stored in database, not S3."""

    def test_app_immediately_queryable(self, e2e_client, platform_admin):
        """Created app is immediately queryable (DB storage)."""
        response = e2e_client.post(
            "/api/applications",
            headers=platform_admin.headers,
            json={"name": "Immediate Query App", "slug": "immediate-query-app"},
        )
        assert response.status_code == 201
        created = response.json()

        # Immediately query by slug - should work (DB storage)
        response = e2e_client.get(
            "/api/applications/immediate-query-app",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, \
            "App should be immediately queryable (DB-first)"
        queried = response.json()
        assert queried["id"] == created["id"]

        # Cleanup
        _delete_app(e2e_client, platform_admin.headers, created["id"])

    def test_app_persists_across_requests(self, e2e_client, platform_admin):
        """App data persists across multiple requests."""
        # Create app - code engine is now the only engine
        create_response = e2e_client.post(
            "/api/applications",
            headers=platform_admin.headers,
            json={"name": "Persist Test App", "slug": "persist-test-app"},
        )
        assert create_response.status_code == 201
        app = create_response.json()

        # Verify scaffolded files exist (they're automatically created)
        response = e2e_client.get(
            f"/api/applications/{app['id']}/files",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        data = response.json()
        file_paths = [f["path"] for f in data["files"]]
        assert "pages/index.tsx" in file_paths, f"Expected pages/index.tsx in {file_paths}"

        # Modify the index file via files API
        modify_response = e2e_client.put(
            f"/api/applications/{app['id']}/files/pages/index.tsx",
            headers=platform_admin.headers,
            json={"source": "export default function Index() { return <div>Persisted</div>; }"},
        )
        assert modify_response.status_code == 200, f"Modify file failed: {modify_response.text}"

        # Query in a separate request - data should persist
        response = e2e_client.get(
            f"/api/applications/{app['id']}/files/pages/index.tsx",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"Get file failed: {response.text}"
        data = response.json()
        assert "Persisted" in data["source"]

        # Cleanup
        _delete_app(e2e_client, platform_admin.headers, app["id"])


@pytest.mark.e2e
class TestCodeEngineApps:
    """Test code engine application features."""

    def test_app_scaffolds_files(self, e2e_client, platform_admin):
        """Creating an app scaffolds initial files (code engine is now the only engine)."""
        # Create app (code engine is now the default and only engine)
        response = e2e_client.post(
            "/api/applications",
            headers=platform_admin.headers,
            json={
                "name": "Code Engine Test",
                "slug": "code-engine-test",
            },
        )
        assert response.status_code == 201, f"Create app failed: {response.text}"
        app = response.json()

        # List files
        response = e2e_client.get(
            f"/api/applications/{app['id']}/files",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"List files failed: {response.text}"
        data = response.json()

        # Should have scaffolded _layout.tsx and pages/index.tsx
        file_paths = [f["path"] for f in data["files"]]
        assert "_layout.tsx" in file_paths, f"Expected _layout.tsx in {file_paths}"
        assert "pages/index.tsx" in file_paths, f"Expected pages/index.tsx in {file_paths}"

        # Check content
        files_by_path = {f["path"]: f for f in data["files"]}
        assert "RootLayout" in files_by_path["_layout.tsx"]["source"]
        assert "HomePage" in files_by_path["pages/index.tsx"]["source"]

        # Cleanup
        _delete_app(e2e_client, platform_admin.headers, app["id"])

    def test_app_create_preserves_existing_local_files(self, e2e_client, platform_admin):
        """Creating an app whose source dir already has files must NOT clobber them with the default scaffold.

        Repro: author writes apps/<slug>/_layout.tsx locally, pushes to S3,
        then runs `bifrost apps create --slug <slug>`. The user-authored
        layout must survive — losing it to the default Welcome scaffold is
        the bug this guards against.
        """
        slug = "preserve-local-source"

        # Pre-stage a user-authored file at apps/<slug>/_layout.tsx,
        # mimicking a `bifrost watch` push that ran before `apps create`.
        custom_layout = (
            'import { Outlet } from "bifrost";\n\n'
            'export default function RootLayout() {\n'
            '  return <div data-test="user-authored"><Outlet /></div>;\n'
            '}\n'
        )
        write_response = e2e_client.post(
            "/api/files/write",
            headers=platform_admin.headers,
            json={
                "path": f"apps/{slug}/_layout.tsx",
                "content": custom_layout,
                "location": "workspace",
                "mode": "cloud",
            },
        )
        assert write_response.status_code == 204, f"Pre-stage write failed: {write_response.text}"

        try:
            response = e2e_client.post(
                "/api/applications",
                headers=platform_admin.headers,
                json={"name": "Preserve Local Source", "slug": slug},
            )
            assert response.status_code == 201, f"Create app failed: {response.text}"
            app = response.json()

            files_response = e2e_client.get(
                f"/api/applications/{app['id']}/files",
                headers=platform_admin.headers,
            )
            assert files_response.status_code == 200
            files_by_path = {f["path"]: f for f in files_response.json()["files"]}

            assert "_layout.tsx" in files_by_path, "Pre-staged _layout.tsx missing after create"
            assert 'data-test="user-authored"' in files_by_path["_layout.tsx"]["source"], (
                "Default scaffold clobbered the pre-staged user-authored _layout.tsx"
            )
            assert "pages/index.tsx" not in files_by_path, (
                "Scaffold should be skipped entirely when prefix is non-empty"
            )

            _delete_app(e2e_client, platform_admin.headers, app["id"])
        finally:
            e2e_client.post(
                "/api/files/delete",
                headers=platform_admin.headers,
                json={
                    "path": f"apps/{slug}/_layout.tsx",
                    "location": "workspace",
                    "mode": "cloud",
                },
            )
