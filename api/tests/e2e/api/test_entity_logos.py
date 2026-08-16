"""E2E tests for app and agent logo upload/fetch/delete."""

import base64
import io
import uuid

import pytest
from PIL import Image


_png_buffer = io.BytesIO()
Image.new("RGBA", (1, 1), (255, 0, 0, 255)).save(_png_buffer, "PNG")
CLEAN_PNG = _png_buffer.getvalue()


def _create_app(e2e_client, headers, slug, name=None):
    resp = e2e_client.post(
        "/api/applications",
        headers=headers,
        json={"name": name or slug, "slug": slug, "app_model": "inline_v1"},
    )
    assert resp.status_code == 201, f"create app failed: {resp.text}"
    return resp.json()


def _delete_app(e2e_client, headers, app_id):
    e2e_client.delete(f"/api/applications/{app_id}", headers=headers)


def _upload_headers(headers):
    """Strip Content-Type so httpx sets it for multipart."""
    return {k: v for k, v in headers.items() if k.lower() != "content-type"}


@pytest.mark.e2e
class TestApplicationLogo:
    def test_upload_fetch_delete_png(self, e2e_client, platform_admin):
        slug = f"logo-app-{uuid.uuid4().hex[:8]}"
        app = _create_app(e2e_client, platform_admin.headers, slug)
        try:
            app_id = app["id"]

            # No logo → 404
            miss = e2e_client.get(
                f"/api/applications/{app_id}/logo",
                headers=platform_admin.headers,
            )
            assert miss.status_code == 404

            # Upload PNG
            upload = e2e_client.post(
                f"/api/applications/{app_id}/logo",
                headers=_upload_headers(platform_admin.headers),
                files={"file": ("logo.png", CLEAN_PNG, "image/png")},
            )
            assert upload.status_code == 200, f"upload failed: {upload.text}"

            # Fetch
            got = e2e_client.get(
                f"/api/applications/{app_id}/logo",
                headers=platform_admin.headers,
            )
            assert got.status_code == 200
            assert got.headers["content-type"].startswith("image/webp")
            assert len(got.content) <= 20 * 1024
            assert got.headers["cache-control"] == "private, max-age=31536000, immutable"

            detail = e2e_client.get(
                f"/api/applications/{slug}",
                headers=platform_admin.headers,
            )
            assert detail.status_code == 200
            detail_body = detail.json()
            assert detail_body["logo"].startswith("data:image/webp;base64,")
            assert len(
                base64.b64decode(detail_body["logo"].split(",", 1)[1])
            ) <= 20 * 1024

            listed = e2e_client.get(
                "/api/applications", headers=platform_admin.headers
            )
            listed_app = next(
                row for row in listed.json()["applications"] if row["id"] == app_id
            )
            assert listed_app["logo"] is None
            assert listed_app["logo_url"].startswith(
                f"/api/applications/{app_id}/logo?v="
            )

            # Delete
            deleted = e2e_client.delete(
                f"/api/applications/{app_id}/logo",
                headers=platform_admin.headers,
            )
            assert deleted.status_code == 204

            # 404 again
            miss2 = e2e_client.get(
                f"/api/applications/{app_id}/logo",
                headers=platform_admin.headers,
            )
            assert miss2.status_code == 404
        finally:
            _delete_app(e2e_client, platform_admin.headers, app["id"])

    def test_rejects_unknown_content_type(self, e2e_client, platform_admin):
        slug = f"logo-app-bad-{uuid.uuid4().hex[:8]}"
        app = _create_app(e2e_client, platform_admin.headers, slug)
        try:
            resp = e2e_client.post(
                f"/api/applications/{app['id']}/logo",
                headers=_upload_headers(platform_admin.headers),
                files={"file": ("logo.gif", b"GIF89a", "image/gif")},
            )
            assert resp.status_code == 400
        finally:
            _delete_app(e2e_client, platform_admin.headers, app["id"])

    def test_svg_sanitized(self, e2e_client, platform_admin):
        slug = f"logo-app-svg-{uuid.uuid4().hex[:8]}"
        app = _create_app(e2e_client, platform_admin.headers, slug)
        try:
            payload = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script><circle r="1"/></svg>'
            upload = e2e_client.post(
                f"/api/applications/{app['id']}/logo",
                headers=_upload_headers(platform_admin.headers),
                files={"file": ("logo.svg", payload, "image/svg+xml")},
            )
            assert upload.status_code == 200, f"upload failed: {upload.text}"
            got = e2e_client.get(
                f"/api/applications/{app['id']}/logo",
                headers=platform_admin.headers,
            )
            assert got.status_code == 200
            assert got.headers["content-type"].startswith("image/webp")
        finally:
            _delete_app(e2e_client, platform_admin.headers, app["id"])


def _create_agent(e2e_client, headers, name):
    resp = e2e_client.post(
        "/api/agents",
        headers=headers,
        json={
            "name": name,
            "system_prompt": "You are a helper.",
            "channels": ["chat"],
            "access_level": "authenticated",
        },
    )
    assert resp.status_code == 201, f"create agent failed: {resp.text}"
    return resp.json()


def _delete_agent(e2e_client, headers, agent_id):
    e2e_client.delete(f"/api/agents/{agent_id}", headers=headers)


@pytest.mark.e2e
class TestAgentLogo:
    def test_upload_fetch_delete_png(self, e2e_client, platform_admin):
        agent = _create_agent(e2e_client, platform_admin.headers, f"logo-bot-{uuid.uuid4().hex[:8]}")
        try:
            agent_id = agent["id"]

            miss = e2e_client.get(
                f"/api/agents/{agent_id}/logo",
                headers=platform_admin.headers,
            )
            assert miss.status_code == 404

            upload = e2e_client.post(
                f"/api/agents/{agent_id}/logo",
                headers=_upload_headers(platform_admin.headers),
                files={"file": ("logo.png", CLEAN_PNG, "image/png")},
            )
            assert upload.status_code == 200, f"upload failed: {upload.text}"

            got = e2e_client.get(
                f"/api/agents/{agent_id}/logo",
                headers=platform_admin.headers,
            )
            assert got.status_code == 200
            assert got.headers["content-type"].startswith("image/webp")
            assert len(got.content) <= 20 * 1024
            assert got.headers["cache-control"] == "private, max-age=31536000, immutable"

            listed = e2e_client.get(
                "/api/agents?include_stats=true",
                headers=platform_admin.headers,
            )
            listed_agent = next(
                row for row in listed.json() if row["id"] == agent_id
            )
            assert listed_agent["logo"] is None
            assert listed_agent["logo_url"].startswith(
                f"/api/agents/{agent_id}/logo?v="
            )
            assert listed_agent["stats"]["runs_7d"] == 0

            deleted = e2e_client.delete(
                f"/api/agents/{agent_id}/logo",
                headers=platform_admin.headers,
            )
            assert deleted.status_code == 204
        finally:
            _delete_agent(e2e_client, platform_admin.headers, agent["id"])

    def test_rejects_unknown_content_type(self, e2e_client, platform_admin):
        agent = _create_agent(e2e_client, platform_admin.headers, f"logo-bot-bad-{uuid.uuid4().hex[:8]}")
        try:
            resp = e2e_client.post(
                f"/api/agents/{agent['id']}/logo",
                headers=_upload_headers(platform_admin.headers),
                files={"file": ("logo.gif", b"GIF89a", "image/gif")},
            )
            assert resp.status_code == 400
        finally:
            _delete_agent(e2e_client, platform_admin.headers, agent["id"])

    def test_svg_sanitized(self, e2e_client, platform_admin):
        agent = _create_agent(e2e_client, platform_admin.headers, f"logo-bot-svg-{uuid.uuid4().hex[:8]}")
        try:
            payload = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script><circle r="1"/></svg>'
            upload = e2e_client.post(
                f"/api/agents/{agent['id']}/logo",
                headers=_upload_headers(platform_admin.headers),
                files={"file": ("logo.svg", payload, "image/svg+xml")},
            )
            assert upload.status_code == 200, f"upload failed: {upload.text}"
            got = e2e_client.get(
                f"/api/agents/{agent['id']}/logo",
                headers=platform_admin.headers,
            )
            assert got.status_code == 200
            assert got.headers["content-type"].startswith("image/webp")
        finally:
            _delete_agent(e2e_client, platform_admin.headers, agent["id"])
