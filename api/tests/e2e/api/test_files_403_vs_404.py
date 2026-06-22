from urllib.parse import quote


def _seed_admin_bypass(e2e_client, headers, *, location, scope):
    # Empty doc → backend seeds admin_bypass on create.
    return e2e_client.put(
        f"/api/files/policies/{quote('/', safe='')}",
        headers=headers,
        params={"location": location, "scope": scope},
        json={"policies": {"policies": []}},
    )


class TestFiles403vs404:
    def test_read_denied_is_403(self, e2e_client, org1_user):
        # No policy grants this non-admin user → 403, not 404.
        r = e2e_client.post(
            "/api/files/read", headers=org1_user.headers,
            json={"path": "nope.txt", "location": "gallery", "scope": None},
        )
        assert r.status_code == 403

    def test_read_allowed_but_missing_is_404(self, e2e_client, platform_admin):
        _seed_admin_bypass(
            e2e_client, platform_admin.headers, location="gallery", scope="global"
        )
        r = e2e_client.post(
            "/api/files/read", headers=platform_admin.headers,
            json={"path": "absent.txt", "location": "gallery", "scope": "global"},
        )
        assert r.status_code == 404

    def test_list_allowed_but_empty_is_200_empty(self, e2e_client, platform_admin):
        _seed_admin_bypass(
            e2e_client, platform_admin.headers, location="gallery", scope="global"
        )
        r = e2e_client.post(
            "/api/files/list", headers=platform_admin.headers,
            json={"directory": "emptydir", "location": "gallery", "scope": "global"},
        )
        assert r.status_code == 200
        assert r.json()["files"] == []
