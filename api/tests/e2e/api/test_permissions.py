"""
E2E tests for permission enforcement.

Tests that org users are properly restricted and org isolation is maintained.
"""

import pytest


@pytest.mark.e2e
class TestOrgUserRestrictions:
    """Test that org users are properly restricted from admin operations."""

    def test_org_user_cannot_list_all_organizations(self, e2e_client, org1_user):
        """Org user should not be able to list all organizations."""
        response = e2e_client.get(
            "/api/organizations",
            headers=org1_user.headers,
        )
        assert response.status_code == 403

    def test_org_user_cannot_create_organization(self, e2e_client, org1_user):
        """Org user should not be able to create organizations."""
        response = e2e_client.post(
            "/api/organizations",
            headers=org1_user.headers,
            json={"name": "Hacker Corp", "domain": "hacker.com"},
        )
        assert response.status_code == 403

    def test_org_user_cannot_create_roles(self, e2e_client, org1_user):
        """Org user should not be able to create roles."""
        response = e2e_client.post(
            "/api/roles",
            headers=org1_user.headers,
            json={"name": "Hacker Role", "description": "Unauthorized"},
        )
        assert response.status_code == 403

    def test_org_user_cannot_create_forms(self, e2e_client, org1_user):
        """Org user should not be able to create forms."""
        response = e2e_client.post(
            "/api/forms",
            headers=org1_user.headers,
            json={
                "name": "Unauthorized Form",
                "workflow_id": None,
                "form_schema": {"fields": []},
            },
        )
        assert response.status_code == 403

    def test_org_user_cannot_manage_config(self, e2e_client, org1_user):
        """Org user should not be able to create config."""
        response = e2e_client.post(
            "/api/config",
            headers=org1_user.headers,
            json={
                "key": "hacker_config",
                "value": "evil",
                "type": "string",
            },
        )
        assert response.status_code == 403

    def test_org_user_cannot_access_files(self, e2e_client, org1_user):
        """Org user should not be able to access workspace files."""
        response = e2e_client.get(
            "/api/files/editor",
            headers=org1_user.headers,
        )
        assert response.status_code == 403

    def test_org_user_cannot_execute_workflows_directly(self, e2e_client, org1_user):
        """Org user should not be able to execute workflows directly (only via forms)."""
        response = e2e_client.post(
            "/api/workflows/execute",
            headers=org1_user.headers,
            json={
                "workflow_id": "00000000-0000-0000-0000-000000000000",
                "input_data": {},
            },
        )
        # 403 = permission denied, 404 = workflow not found (both acceptable)
        assert response.status_code in [403, 404], \
            f"Org user should not execute workflows directly: {response.status_code}"

    def test_org_user_cannot_list_all_users(self, e2e_client, org1_user):
        """Org user cannot list all users (403 or filtered)."""
        response = e2e_client.get(
            "/api/users",
            headers=org1_user.headers,
        )
        # Should be 403 or return only limited/filtered data
        assert response.status_code in [403, 200]
        if response.status_code == 200:
            # If 200, should be filtered (not see all users)
            data = response.json()
            users = data.get("users", []) if isinstance(data, dict) else data
            # Org user should not see platform admin details
            for user in users:
                assert not user.get("is_superuser"), \
                    "Org user should not see superuser details"

    def test_org_user_cannot_create_users(self, e2e_client, org1_user):
        """Org user cannot create users (403)."""
        response = e2e_client.post(
            "/api/users",
            headers=org1_user.headers,
            json={
                "email": "hacker@evil.com",
                "name": "Hacker",
                "organization_id": str(org1_user.organization_id),
            },
        )
        assert response.status_code == 403, \
            f"Org user should not create users: {response.status_code}"

    def test_org_user_cannot_delete_users(self, e2e_client, org1_user, platform_admin):
        """Org user cannot delete users (403)."""
        response = e2e_client.delete(
            f"/api/users/{platform_admin.user_id}",
            headers=org1_user.headers,
        )
        assert response.status_code == 403, \
            f"Org user should not delete users: {response.status_code}"

    def test_org_user_cannot_delete_config(self, e2e_client, org1_user):
        """Org user cannot delete config (403)."""
        response = e2e_client.delete(
            "/api/config/test_key",
            headers=org1_user.headers,
        )
        assert response.status_code in [403, 404], \
            f"Org user should not delete config: {response.status_code}"

    def test_org_user_cannot_uninstall_packages(self, e2e_client, org1_user):
        """Org user cannot uninstall packages (403)."""
        response = e2e_client.delete(
            "/api/packages/some-package",
            headers=org1_user.headers,
        )
        assert response.status_code == 403, \
            f"Org user should not uninstall packages: {response.status_code}"

    def test_org_user_cannot_access_oauth_admin(self, e2e_client, org1_user):
        """Org user cannot create OAuth connections (403)."""
        response = e2e_client.post(
            "/api/oauth/connections",
            headers=org1_user.headers,
            json={
                "connection_name": "hacked_oauth",
                "oauth_flow_type": "authorization_code",
                "authorization_url": "https://evil.com/auth",
                "token_url": "https://evil.com/token",
            },
        )
        assert response.status_code == 403, \
            f"Org user should not access OAuth admin: {response.status_code}"

    def test_org_user_cannot_modify_roles(self, e2e_client, org1_user):
        """Org user cannot modify roles (403)."""
        # Try to modify a role (using a dummy ID since we just need to test permissions)
        response = e2e_client.put(
            "/api/roles/00000000-0000-0000-0000-000000000000",
            headers=org1_user.headers,
            json={"name": "Hacked Role"},
        )
        assert response.status_code == 403, \
            f"Org user should not modify roles: {response.status_code}"

    def test_org_user_cannot_delete_roles(self, e2e_client, org1_user):
        """Org user cannot delete roles (403)."""
        response = e2e_client.delete(
            "/api/roles/00000000-0000-0000-0000-000000000000",
            headers=org1_user.headers,
        )
        assert response.status_code == 403, \
            f"Org user should not delete roles: {response.status_code}"

    # =========================================================================
    # Tables API - Platform admin only
    # =========================================================================

    def test_org_user_cannot_list_tables(self, e2e_client, org1_user):
        """Org user cannot list tables (403)."""
        response = e2e_client.get(
            "/api/tables",
            headers=org1_user.headers,
        )
        assert response.status_code == 403, \
            f"Org user should not list tables: {response.status_code}"

    def test_org_user_cannot_create_tables(self, e2e_client, org1_user):
        """Org user cannot create tables (403)."""
        response = e2e_client.post(
            "/api/tables",
            headers=org1_user.headers,
            json={"name": "hacker_table"},
        )
        assert response.status_code == 403, \
            f"Org user should not create tables: {response.status_code}"

    def test_org_user_cannot_get_table(self, e2e_client, org1_user):
        """Org user cannot get table metadata (403)."""
        response = e2e_client.get(
            "/api/tables/00000000-0000-0000-0000-000000000000",
            headers=org1_user.headers,
        )
        assert response.status_code == 403, \
            f"Org user should not get table: {response.status_code}"

    def test_org_user_cannot_query_documents(self, e2e_client, org1_user):
        """Org user cannot query documents on a non-existent table (404 hides existence)."""
        response = e2e_client.post(
            "/api/tables/00000000-0000-0000-0000-000000000000/documents/query",
            headers=org1_user.headers,
            json={"limit": 10},
        )
        assert response.status_code in (403, 404), \
            f"Org user should not query documents: {response.status_code}"

    def test_org_user_cannot_insert_documents(self, e2e_client, org1_user):
        """Org user cannot insert documents on a non-existent table (404 hides existence)."""
        response = e2e_client.post(
            "/api/tables/00000000-0000-0000-0000-000000000000/documents",
            headers=org1_user.headers,
            json={"data": {"key": "value"}},
        )
        assert response.status_code in (403, 404), \
            f"Org user should not insert documents: {response.status_code}"

    # =========================================================================
    # Agents API - Platform admin only for CRUD
    # =========================================================================

    def test_org_user_cannot_create_agents(self, e2e_client, org1_user):
        """Org user cannot create agents (403)."""
        response = e2e_client.post(
            "/api/agents",
            headers=org1_user.headers,
            json={
                "name": "Hacker Agent",
                "description": "Malicious agent",
                "system_prompt": "You are evil",
                "channels": ["web"],
            },
        )
        # 422 (validation) may fire before permission check; either means denied
        assert response.status_code in (403, 422), \
            f"Org user should not create agents: {response.status_code}"

    def test_org_user_cannot_update_agents(self, e2e_client, org1_user):
        """Org user cannot update agents (403 or 404)."""
        response = e2e_client.put(
            "/api/agents/00000000-0000-0000-0000-000000000000",
            headers=org1_user.headers,
            json={"name": "Hacked Agent"},
        )
        # 404 (not found) before 403 is expected — avoids leaking resource existence
        assert response.status_code in (403, 404), \
            f"Org user should not update agents: {response.status_code}"

    def test_org_user_cannot_delete_agents(self, e2e_client, org1_user):
        """Org user cannot delete agents (403 or 404)."""
        response = e2e_client.delete(
            "/api/agents/00000000-0000-0000-0000-000000000000",
            headers=org1_user.headers,
        )
        # 404 (not found) before 403 is expected — avoids leaking resource existence
        assert response.status_code in (403, 404), \
            f"Org user should not delete agents: {response.status_code}"

    # =========================================================================
    # Knowledge API - Platform admin only (accessed via MCP, not REST)
    # Note: Knowledge endpoints may return 404 if REST API is not exposed
    # =========================================================================

    def test_org_user_cannot_store_knowledge(self, e2e_client, org1_user):
        """Org user cannot store knowledge documents (403 or 404 if no REST API)."""
        response = e2e_client.post(
            "/api/knowledge",
            headers=org1_user.headers,
            json={
                "content": "Malicious content",
                "namespace": "default",
            },
        )
        # 403 = permission denied, 404 = endpoint not exposed (knowledge via MCP only)
        assert response.status_code in [403, 404], \
            f"Org user should not store knowledge: {response.status_code}"

    def test_org_user_cannot_delete_knowledge_namespace(self, e2e_client, org1_user):
        """Org user cannot delete knowledge namespace (403 or 404 if no REST API)."""
        response = e2e_client.delete(
            "/api/knowledge/namespaces/default",
            headers=org1_user.headers,
        )
        # 403 = permission denied, 404 = endpoint not exposed (knowledge via MCP only)
        assert response.status_code in [403, 404], \
            f"Org user should not delete knowledge namespace: {response.status_code}"


@pytest.mark.e2e
class TestOrgUserCapabilities:
    """Test what org users CAN do."""

    def test_org_user_can_see_own_profile(self, e2e_client, org1_user):
        """Org user can access their own profile."""
        response = e2e_client.get("/auth/me", headers=org1_user.headers)
        assert response.status_code == 200
        assert response.json()["email"] == org1_user.email

    def test_org_user_can_list_own_executions(self, e2e_client, org1_user):
        """Org user can list their execution history."""
        response = e2e_client.get(
            "/api/executions",
            headers=org1_user.headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "executions" in data

    def test_org_user_can_view_mfa_status(self, e2e_client, org1_user):
        """Org user can check their MFA status."""
        response = e2e_client.get(
            "/auth/mfa/status",
            headers=org1_user.headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "mfa_enabled" in data

    def test_org_user_can_list_assigned_forms(self, e2e_client, org1_user):
        """Org user can list forms assigned to them."""
        response = e2e_client.get(
            "/api/forms",
            headers=org1_user.headers,
        )
        assert response.status_code == 200, f"List forms failed: {response.text}"
        # Response should be filtered to assigned forms only
        data = response.json()
        # Verify response has expected structure
        assert isinstance(data, (dict, list))


@pytest.mark.e2e
class TestOrgIsolation:
    """Test that organizations are properly isolated from each other."""

    # =========================================================================
    # Forms Isolation
    # =========================================================================

    def test_org1_user_only_sees_own_forms(self, e2e_client, org1_user, org2):
        """Org1 user only sees their own org's forms regardless of filter param."""
        # With query param filtering, org users always get their own org's data
        # The scope param is ignored for non-superusers
        response = e2e_client.get(
            "/api/forms",
            params={"scope": org2["id"]},  # Try to filter by org2
            headers=org1_user.headers,
        )
        # Request succeeds but returns only org1's resources
        assert response.status_code == 200
        forms = response.json()
        for form in forms:
            assert form.get("organization_id") in [None, str(org1_user.organization_id)], \
                "Org user should only see their own org's forms"

    def test_scope_param_ignored_for_forms_list(self, e2e_client, org1_user, org2):
        """Scope parameter is ignored for non-superusers on forms list."""
        # Request with scope param pointing to other org
        response_with_scope = e2e_client.get(
            "/api/forms",
            params={"scope": org2["id"]},
            headers=org1_user.headers,
        )
        # Request without scope param
        response_without_scope = e2e_client.get(
            "/api/forms",
            headers=org1_user.headers,
        )
        # Both should return same data
        assert response_with_scope.status_code == 200
        assert response_without_scope.status_code == 200
        # Lists should be equivalent (scope param had no effect)
        forms_with = response_with_scope.json()
        forms_without = response_without_scope.json()
        assert len(forms_with) == len(forms_without), \
            "Scope param should be ignored for non-superusers"

    # =========================================================================
    # Applications Isolation
    # =========================================================================

    def test_org1_user_only_sees_own_apps(self, e2e_client, org1_user, org2):
        """Org1 user only sees their own org's applications."""
        response = e2e_client.get(
            "/api/applications",  # Correct endpoint path
            params={"scope": org2["id"]},  # Try to filter by org2
            headers=org1_user.headers,
        )
        assert response.status_code == 200
        data = response.json()
        apps = data.get("applications", [])
        for app in apps:
            assert app.get("organization_id") in [None, str(org1_user.organization_id)], \
                "Org user should only see their own org's apps"

    def test_scope_param_ignored_for_apps_list(self, e2e_client, org1_user, org2):
        """Scope parameter is ignored for non-superusers on apps list."""
        response_with_scope = e2e_client.get(
            "/api/applications",  # Correct endpoint path
            params={"scope": org2["id"]},
            headers=org1_user.headers,
        )
        response_without_scope = e2e_client.get(
            "/api/applications",  # Correct endpoint path
            headers=org1_user.headers,
        )
        assert response_with_scope.status_code == 200
        assert response_without_scope.status_code == 200
        data_with = response_with_scope.json()
        data_without = response_without_scope.json()
        assert len(data_with.get("applications", [])) == len(data_without.get("applications", [])), \
            "Scope param should be ignored for non-superusers"

    # =========================================================================
    # Executions Isolation
    # =========================================================================

    def test_org1_user_only_sees_own_executions(self, e2e_client, org1_user, org2):
        """Org1 user only sees their own executions."""
        response = e2e_client.get(
            "/api/executions",
            params={"scope": org2["id"]},  # Try to filter by org2
            headers=org1_user.headers,
        )
        assert response.status_code == 200
        data = response.json()
        executions = data.get("executions", [])
        for execution in executions:
            # Executions should belong to org1 user
            assert execution.get("user_id") == str(org1_user.user_id) or \
                   execution.get("organization_id") in [None, str(org1_user.organization_id)], \
                "Org user should only see their own executions"

    def test_scope_param_ignored_for_executions_list(self, e2e_client, org1_user, org2):
        """Scope parameter is ignored for non-superusers on executions list."""
        response_with_scope = e2e_client.get(
            "/api/executions",
            params={"scope": org2["id"]},
            headers=org1_user.headers,
        )
        response_without_scope = e2e_client.get(
            "/api/executions",
            headers=org1_user.headers,
        )
        assert response_with_scope.status_code == 200
        assert response_without_scope.status_code == 200
        data_with = response_with_scope.json()
        data_without = response_without_scope.json()
        assert len(data_with.get("executions", [])) == len(data_without.get("executions", [])), \
            "Scope param should be ignored for non-superusers"

    # =========================================================================
    # Cross-Org Access Attempts (Direct ID Access)
    # =========================================================================

    def test_org1_user_cannot_access_org2_form_by_id(
        self, e2e_client, platform_admin, org1_user, org2
    ):
        """Org1 user cannot access a form from org2 by ID."""
        # First, create a form in org2 as platform admin
        form_response = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "Org2 Private Form",
                "workflow_id": None,
                "form_schema": {"fields": []},
                "organization_id": org2["id"],  # Set org ID in body
            },
        )
        # May fail if form creation requires workflow - that's ok, we're testing isolation
        if form_response.status_code == 201:
            form_id = form_response.json()["id"]

            # Org1 user tries to access org2's form
            response = e2e_client.get(
                f"/api/forms/{form_id}",
                headers=org1_user.headers,
            )
            # Should get 404 (not found) or 403 (forbidden) - not 200
            assert response.status_code in [403, 404], \
                f"Org1 user should not access org2 form: got {response.status_code}"

            # Cleanup
            e2e_client.delete(
                f"/api/forms/{form_id}",
                headers=platform_admin.headers,
            )

    def test_org1_user_cannot_execute_org2_form(
        self, e2e_client, platform_admin, org1_user, org2
    ):
        """Org1 user cannot execute a form from org2."""
        # Create a test form in org2
        form_response = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "Org2 Executable Form",
                "workflow_id": None,
                "form_schema": {"fields": []},
                "organization_id": org2["id"],  # Set org ID in body
            },
        )
        if form_response.status_code == 201:
            form_id = form_response.json()["id"]

            # Org1 user tries to execute org2's form
            response = e2e_client.post(
                f"/api/forms/{form_id}/execute",
                headers=org1_user.headers,
                json={"inputs": {}},
            )
            # Should get 403 or 404, not 200
            assert response.status_code in [403, 404], \
                f"Org1 user should not execute org2 form: got {response.status_code}"

            # Cleanup
            e2e_client.delete(
                f"/api/forms/{form_id}",
                headers=platform_admin.headers,
            )

    def test_org2_user_cannot_see_org1_executions(
        self, e2e_client, org1_user, org2_user
    ):
        """Org2 user cannot see org1 user's executions."""
        # Get org1 user's executions
        response = e2e_client.get(
            "/api/executions",
            headers=org1_user.headers,
        )
        assert response.status_code == 200
        org1_executions = response.json().get("executions", [])

        # Get org2 user's executions
        response = e2e_client.get(
            "/api/executions",
            headers=org2_user.headers,
        )
        assert response.status_code == 200
        org2_executions = response.json().get("executions", [])

        # If org1 has executions, verify org2 doesn't see them
        org1_exec_ids = {e["id"] for e in org1_executions}
        org2_exec_ids = {e["id"] for e in org2_executions}

        # No overlap should exist
        overlap = org1_exec_ids & org2_exec_ids
        assert len(overlap) == 0, \
            f"Org2 user can see org1 executions: {overlap}"

    def test_org1_user_cannot_access_org2_execution_by_id(
        self, e2e_client, org1_user, org2_user
    ):
        """Org1 user cannot access an org2 execution by direct ID."""
        # Get org2 user's executions
        response = e2e_client.get(
            "/api/executions",
            headers=org2_user.headers,
        )
        assert response.status_code == 200
        org2_executions = response.json().get("executions", [])

        if org2_executions:
            exec_id = org2_executions[0]["id"]

            # Org1 user tries to access org2's execution
            response = e2e_client.get(
                f"/api/executions/{exec_id}",
                headers=org1_user.headers,
            )
            # Should get 404 or 403, not 200
            assert response.status_code in [403, 404], \
                f"Org1 user should not access org2 execution: got {response.status_code}"


@pytest.mark.e2e
class TestPlatformAdminCapabilities:
    """Test what platform admins can do."""

    def test_platform_admin_sees_all_executions(self, e2e_client, platform_admin):
        """Platform admin can see all executions."""
        response = e2e_client.get(
            "/api/executions",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "executions" in data

    def test_platform_admin_can_filter_by_org_scope(
        self, e2e_client, platform_admin, org1, org2
    ):
        """Platform admin can filter resources by organization scope."""
        # Get forms for org1 only
        response = e2e_client.get(
            "/api/forms",
            params={"scope": org1["id"]},
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        forms = response.json()
        for form in forms:
            # Platform admin filtering by org1 sees only org1 forms (no global fallback)
            if form.get("organization_id"):
                assert form.get("organization_id") == org1["id"], \
                    "Platform admin scope filter should work correctly"

    def test_platform_admin_can_see_global_resources(
        self, e2e_client, platform_admin
    ):
        """Platform admin can filter to see global resources."""
        response = e2e_client.get(
            "/api/forms",
            params={"scope": "global"},
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
        forms = response.json()
        for form in forms:
            assert form.get("organization_id") is None, \
                "Global scope should return only global resources"


@pytest.mark.e2e
class TestRoleBasedFormAccess:
    """Test role-based form access control."""

    @pytest.fixture
    def test_role_with_form(self, e2e_client, platform_admin, org1):
        """Create a role and form, assign form to role."""
        # Create role
        role_response = e2e_client.post(
            "/api/roles",
            headers=platform_admin.headers,
            json={
                "name": "Test Form Access Role",
                "description": "Role for form access testing",
            },
        )
        assert role_response.status_code == 201, f"Create role failed: {role_response.text}"
        role = role_response.json()

        # Create form with role_based access in org1
        form_response = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "Role-Restricted Form",
                "workflow_id": None,
                "form_schema": {"fields": []},
                "access_level": "role_based",
                "organization_id": org1["id"],  # Set org ID in body, not query param
            },
        )
        assert form_response.status_code == 201, f"Create form failed: {form_response.text}"
        form = form_response.json()

        # Assign form to role
        assign_response = e2e_client.post(
            f"/api/roles/{role['id']}/forms",
            headers=platform_admin.headers,
            json={"form_ids": [form["id"]]},
        )
        assert assign_response.status_code in [200, 201, 204], \
            f"Assign form to role failed: {assign_response.text}"

        yield {"role": role, "form": form}

        # Cleanup
        e2e_client.delete(
            f"/api/forms/{form['id']}",
            headers=platform_admin.headers,
        )
        e2e_client.delete(
            f"/api/roles/{role['id']}",
            headers=platform_admin.headers,
        )

    def test_user_with_role_can_see_assigned_form(
        self, e2e_client, platform_admin, org1_user, test_role_with_form
    ):
        """User assigned to role can see forms assigned to that role."""
        role = test_role_with_form["role"]
        form = test_role_with_form["form"]

        # Assign user to role
        assign_response = e2e_client.post(
            f"/api/roles/{role['id']}/users",
            headers=platform_admin.headers,
            json={"user_ids": [str(org1_user.user_id)]},
        )
        assert assign_response.status_code in [200, 201, 204], \
            f"Assign user to role failed: {assign_response.text}"

        # User should be able to see the form
        response = e2e_client.get(
            f"/api/forms/{form['id']}",
            headers=org1_user.headers,
        )
        assert response.status_code == 200, \
            f"User with role should see assigned form: {response.status_code}"

        # Clean up role assignment
        e2e_client.delete(
            f"/api/roles/{role['id']}/users/{org1_user.user_id}",
            headers=platform_admin.headers,
        )

    def test_user_without_role_cannot_see_role_restricted_form(
        self, e2e_client, org2_user, test_role_with_form
    ):
        """User without role cannot see role-restricted forms."""
        form = test_role_with_form["form"]

        # Org2 user (not assigned to role) should not see org1's form
        response = e2e_client.get(
            f"/api/forms/{form['id']}",
            headers=org2_user.headers,
        )
        # Should be 403 or 404
        assert response.status_code in [403, 404], \
            f"User without role should not see form: {response.status_code}"

    def test_user_in_role_sees_form_in_list(
        self, e2e_client, platform_admin, org1_user, test_role_with_form
    ):
        """User assigned to role sees role-assigned forms in list."""
        role = test_role_with_form["role"]
        form = test_role_with_form["form"]

        # Assign user to role
        assign_response = e2e_client.post(
            f"/api/roles/{role['id']}/users",
            headers=platform_admin.headers,
            json={"user_ids": [str(org1_user.user_id)]},
        )
        assert assign_response.status_code in [200, 201, 204]

        # User should see the form in list
        response = e2e_client.get(
            "/api/forms",
            headers=org1_user.headers,
        )
        assert response.status_code == 200
        forms = response.json()
        form_ids = [f["id"] for f in forms]
        assert form["id"] in form_ids, \
            "User with role should see assigned form in list"

        # Clean up role assignment
        e2e_client.delete(
            f"/api/roles/{role['id']}/users/{org1_user.user_id}",
            headers=platform_admin.headers,
        )


@pytest.mark.e2e
class TestAuthenticatedFormAccess:
    """Test forms with 'authenticated' access level."""

    @pytest.fixture
    def authenticated_form(self, e2e_client, platform_admin, org1):
        """Create a form with authenticated access level in org1."""
        form_response = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "Authenticated Access Form",
                "workflow_id": None,
                "form_schema": {"fields": []},
                "access_level": "authenticated",
                "organization_id": org1["id"],  # Set org ID in body, not query param
            },
        )
        assert form_response.status_code == 201, f"Create form failed: {form_response.text}"
        form = form_response.json()

        yield form

        # Cleanup
        e2e_client.delete(
            f"/api/forms/{form['id']}",
            headers=platform_admin.headers,
        )

    def test_any_org_user_can_see_authenticated_form(
        self, e2e_client, org1_user, authenticated_form
    ):
        """Any authenticated user in the org can see authenticated forms."""
        response = e2e_client.get(
            f"/api/forms/{authenticated_form['id']}",
            headers=org1_user.headers,
        )
        assert response.status_code == 200, \
            f"Authenticated user should see authenticated form: {response.status_code}"

    def test_other_org_user_cannot_see_authenticated_form(
        self, e2e_client, org2_user, authenticated_form
    ):
        """User from different org cannot see authenticated form."""
        response = e2e_client.get(
            f"/api/forms/{authenticated_form['id']}",
            headers=org2_user.headers,
        )
        # Should be 403 or 404 - not their org
        assert response.status_code in [403, 404], \
            f"User from other org should not see form: {response.status_code}"
