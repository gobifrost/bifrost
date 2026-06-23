"""E2E tests for /api/policy-rules CRUD + usages + structured save-time ref validation."""
import pytest

pytestmark = pytest.mark.e2e


class TestPolicyRulesCRUD:
    def test_crud_and_usages(self, e2e_client, platform_admin):
        """Create → usages (0) → delete round-trip."""
        # Create
        r = e2e_client.post(
            "/api/policy-rules",
            headers=platform_admin.headers,
            json={"name": "ops_e2e", "domain": "file", "body": {"actions": ["read"], "when": None}},
        )
        assert r.status_code == 201, r.text
        data = r.json()
        assert data["name"] == "ops_e2e"
        assert data["domain"] == "file"
        assert data["is_builtin"] is False

        # Usages — should be empty on a freshly created rule
        u = e2e_client.get("/api/policy-rules/file/ops_e2e/usages", headers=platform_admin.headers)
        assert u.status_code == 200, u.text
        assert u.json()["total"] == 0

        # Delete
        d = e2e_client.delete("/api/policy-rules/file/ops_e2e", headers=platform_admin.headers)
        assert d.status_code == 204, d.text

        # Gone after delete
        g = e2e_client.get("/api/policy-rules/file/ops_e2e/usages", headers=platform_admin.headers)
        assert g.status_code == 404, g.text

    def test_list_returns_created_rule(self, e2e_client, platform_admin):
        """Created rule appears in the list response."""
        e2e_client.post(
            "/api/policy-rules",
            headers=platform_admin.headers,
            json={"name": "list_target_e2e", "domain": "table", "body": {"actions": ["read"], "when": None}},
        )
        r = e2e_client.get(
            "/api/policy-rules",
            headers=platform_admin.headers,
            params={"domain": "table"},
        )
        assert r.status_code == 200, r.text
        names = [x["name"] for x in r.json()]
        assert "list_target_e2e" in names

        # Cleanup
        e2e_client.delete("/api/policy-rules/table/list_target_e2e", headers=platform_admin.headers)

    def test_readonly_builtin_cannot_be_deleted(self, e2e_client, platform_admin):
        """admin_bypass built-in returns 409 on delete."""
        r = e2e_client.delete("/api/policy-rules/file/admin_bypass", headers=platform_admin.headers)
        assert r.status_code == 409, r.text

    def test_delete_unknown_returns_404(self, e2e_client, platform_admin):
        """Deleting a non-existent rule returns 404."""
        r = e2e_client.delete("/api/policy-rules/file/definitely_does_not_exist_xyz", headers=platform_admin.headers)
        assert r.status_code == 404, r.text

    def test_delete_in_use_returns_409_with_usages(self, e2e_client, platform_admin):
        """Deleting a rule referenced by a file policy returns 409 with usages payload."""
        rule_name = "ops_in_use_e2e"

        # Create the policy rule
        r = e2e_client.post(
            "/api/policy-rules",
            headers=platform_admin.headers,
            json={"name": rule_name, "domain": "file", "body": {"actions": ["read"], "when": None}},
        )
        assert r.status_code == 201, r.text

        # Create a file policy that references it via {"$ref": rule_name}
        # This exercises the by_alias fix — without by_alias=True the ref is stored as
        # {"ref": rule_name} (no $) and find_policy_rule_usages misses it entirely.
        fp = e2e_client.put(
            "/api/files/policies/ops_test%2F",
            headers=platform_admin.headers,
            params={"location": "shared"},
            json={"policies": {"policies": [{"$ref": rule_name}]}},
        )
        assert fp.status_code == 200, fp.text

        # GET /usages must show the file policy (proves by_alias fix on storage side)
        u = e2e_client.get(
            f"/api/policy-rules/file/{rule_name}/usages",
            headers=platform_admin.headers,
        )
        assert u.status_code == 200, u.text
        usages_body = u.json()
        assert usages_body["total"] >= 1, f"Expected >= 1 usage, got: {usages_body}"
        assert len(usages_body["file_policies"]) >= 1

        # DELETE must return 409 with usages payload
        d = e2e_client.delete(
            f"/api/policy-rules/file/{rule_name}",
            headers=platform_admin.headers,
        )
        assert d.status_code == 409, d.text
        body = d.json()
        assert "detail" in body
        detail = body["detail"]
        assert "usages" in detail, f"409 detail missing 'usages': {detail}"
        assert detail["usages"]["total"] >= 1

        # Cleanup — remove file policy first, then the rule
        e2e_client.delete(
            "/api/files/policies/ops_test%2F",
            headers=platform_admin.headers,
            params={"location": "shared"},
        )
        e2e_client.delete(
            f"/api/policy-rules/file/{rule_name}",
            headers=platform_admin.headers,
        )


class TestFilePolicyMissingRef:
    def test_file_policy_missing_ref_is_structured_422(self, e2e_client, platform_admin):
        """Setting a file policy with an unresolvable $ref returns 422 with structured errors."""
        r = e2e_client.put(
            "/api/files/policies/docs%2F",
            headers=platform_admin.headers,
            params={"location": "shared"},
            json={"policies": {"policies": [{"$ref": "nonexistent_rule_xyz"}]}},
        )
        assert r.status_code == 422, r.text
        body = r.json()
        # Must carry structured error info — either top-level "errors" or "detail"
        assert "errors" in body or "detail" in body, f"Missing structured error in: {body}"


class TestNonAdminCannotCreate:
    def test_non_admin_cannot_create(self, e2e_client, org1_user):
        """Regular org user cannot create policy rules — must be 401 or 403."""
        r = e2e_client.post(
            "/api/policy-rules",
            headers=org1_user.headers,
            json={"name": "x", "domain": "file", "body": {"actions": ["read"], "when": None}},
        )
        assert r.status_code in (401, 403), f"Expected 401/403, got {r.status_code}: {r.text}"
