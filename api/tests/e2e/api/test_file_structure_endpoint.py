from tests.e2e.file_policy_helpers import grant_file_policy


def _write(e2e_client, headers, *, path, location, scope, content="x"):
    return e2e_client.post(
        "/api/files/write",
        headers=headers,
        json={"path": path, "content": content, "location": location, "scope": scope},
    )


class TestFileStructureEndpoint:
    def test_structure_shares_then_prefix(self, e2e_client, platform_admin):
        grant_file_policy(
            e2e_client, platform_admin.headers,
            location="gallery", scope="global", prefix="",
        )
        w = _write(
            e2e_client, platform_admin.headers,
            path="a.png", location="gallery", scope="global",
        )
        assert w.status_code in (200, 204), w.text

        # Discover shares (no location).
        r = e2e_client.post(
            "/api/files/structure", headers=platform_admin.headers,
            json={"scope": "global"},
        )
        assert r.status_code == 200, r.text
        assert "gallery" in {s["location"] for s in r.json()["shares"]}

        # List a prefix.
        r2 = e2e_client.post(
            "/api/files/structure", headers=platform_admin.headers,
            json={"location": "gallery", "prefix": "", "scope": "global"},
        )
        assert "a.png" in {e["name"] for e in r2.json()["entries"]}

    def test_structure_forbidden_for_non_admin(self, e2e_client, org1_user):
        r = e2e_client.post(
            "/api/files/structure", headers=org1_user.headers,
            json={"scope": "global"},
        )
        assert r.status_code == 403
