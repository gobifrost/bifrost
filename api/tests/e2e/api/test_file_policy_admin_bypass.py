from urllib.parse import quote


def _put_policy(e2e_client, headers, *, location, scope, policies):
    return e2e_client.put(
        f"/api/files/policies/{quote('/', safe='')}",
        headers=headers,
        params={"location": location, "scope": scope},
        json={"policies": {"policies": policies}},
    )


def _test_access(e2e_client, headers, *, path, location, scope, action="read"):
    return e2e_client.post(
        "/api/files/policies/test",
        headers=headers,
        json={"path": path, "location": location, "action": action, "scope": scope},
    )


class TestAdminBypassSeed:
    def test_admin_allowed_via_seeded_then_denied_when_revoked(
        self, e2e_client, platform_admin
    ):
        # 1. Create a policy on a fresh share → seeds admin_bypass.
        r = _put_policy(
            e2e_client, platform_admin.headers,
            location="gallery", scope="global", policies=[],
        )
        assert r.status_code == 200, r.text
        assert any(
            p["name"] == "admin_bypass" for p in r.json()["policies"]["policies"]
        )

        # 2. Admin is allowed to read under it.
        t = _test_access(
            e2e_client, platform_admin.headers,
            path="pic.png", location="gallery", scope="global",
        )
        assert t.json()["allowed"] is True

        # 3. Revoke admin_bypass (update with empty doc — seed NOT re-added).
        r2 = _put_policy(
            e2e_client, platform_admin.headers,
            location="gallery", scope="global", policies=[],
        )
        assert not any(
            p["name"] == "admin_bypass" for p in r2.json()["policies"]["policies"]
        )

        # 4. Admin now denied.
        t2 = _test_access(
            e2e_client, platform_admin.headers,
            path="pic.png", location="gallery", scope="global",
        )
        assert t2.json()["allowed"] is False
