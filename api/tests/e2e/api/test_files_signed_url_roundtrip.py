"""
E2E round-trip tests for /api/files/signed-url across all locations.

The bug this issue fixed: signed URLs always pointed at `uploads/{scope}/{path}`
regardless of where the file actually lived. Now signed URLs share the same
path resolver as `read`/`write`, so a file written via
`/api/files/write` with `location=workspace` is reachable via
`/api/files/signed-url` with `location=workspace`.
"""

import base64
import secrets
import uuid

import httpx
import pytest


@pytest.mark.e2e
class TestSignedUrlRoundTrip:
    """For every location, write-then-sign-then-fetch must return the same bytes."""

    def _write(self, e2e_client, headers, path: str, location: str, content: bytes, scope: str | None = None):
        body = {
            "path": path,
            "content": base64.b64encode(content).decode("ascii"),
            "location": location,
            "binary": True,
            "scope": scope,
        }
        return e2e_client.post("/api/files/write", headers=headers, json=body)

    def _sign(self, e2e_client, headers, path: str, location: str, scope: str | None = None):
        body = {
            "path": path,
            "method": "GET",
            "location": location,
            "content_type": "application/octet-stream",
            "scope": scope,
        }
        return e2e_client.post("/api/files/signed-url", headers=headers, json=body)

    def test_workspace_roundtrip(self, e2e_client, platform_admin):
        path = f"e2e/{uuid.uuid4().hex}.bin"
        content = secrets.token_bytes(64)

        write = self._write(e2e_client, platform_admin.headers, path, "workspace", content)
        assert write.status_code == 204, f"write failed: {write.text}"

        sign = self._sign(e2e_client, platform_admin.headers, path, "workspace")
        assert sign.status_code == 200, f"sign failed: {sign.text}"
        signed = sign.json()
        assert signed["path"] == f"_repo/{path}", (
            f"workspace sign should target _repo/, got {signed['path']}"
        )

        with httpx.Client(timeout=30.0) as s3:
            r = s3.get(signed["url"])
        assert r.status_code == 200, f"download via signed URL failed: {r.status_code} {r.text[:200]}"
        assert r.content == content, "downloaded bytes don't match what was written"

        # Cleanup
        e2e_client.post(
            "/api/files/delete",
            headers=platform_admin.headers,
            json={"path": path, "location": "workspace"},
        )

    def test_temp_roundtrip(self, e2e_client, platform_admin):
        path = f"e2e/{uuid.uuid4().hex}.bin"
        content = secrets.token_bytes(64)
        scope = "e2e-org"

        write = self._write(e2e_client, platform_admin.headers, path, "temp", content, scope=scope)
        assert write.status_code == 204, f"write failed: {write.text}"

        sign = self._sign(e2e_client, platform_admin.headers, path, "temp", scope=scope)
        assert sign.status_code == 200, f"sign failed: {sign.text}"
        signed = sign.json()
        assert signed["path"] == f"_tmp/{scope}/{path}", (
            f"temp sign should target _tmp/{scope}/{path}, got {signed['path']}"
        )

        with httpx.Client(timeout=30.0) as s3:
            r = s3.get(signed["url"])
        assert r.status_code == 200, f"download failed: {r.status_code} {r.text[:200]}"
        assert r.content == content

    def test_uploads_roundtrip(self, e2e_client, platform_admin):
        path = f"e2e-{uuid.uuid4().hex}/upload.bin"
        content = secrets.token_bytes(64)
        scope = "e2e-org"

        write = self._write(e2e_client, platform_admin.headers, path, "uploads", content, scope=scope)
        assert write.status_code == 204, f"write failed: {write.text}"

        sign = self._sign(e2e_client, platform_admin.headers, path, "uploads", scope=scope)
        assert sign.status_code == 200, f"sign failed: {sign.text}"
        signed = sign.json()
        assert signed["path"] == f"uploads/{scope}/{path}", (
            f"uploads sign should target uploads/{scope}/{path}, got {signed['path']}"
        )

        with httpx.Client(timeout=30.0) as s3:
            r = s3.get(signed["url"])
        assert r.status_code == 200, f"download failed: {r.status_code} {r.text[:200]}"
        assert r.content == content

    def test_freeform_location_roundtrip(self, e2e_client, platform_admin):
        path = f"e2e-{uuid.uuid4().hex}/q1.bin"
        content = secrets.token_bytes(64)
        location = "reports"
        scope = "e2e-org"

        write = self._write(e2e_client, platform_admin.headers, path, location, content, scope=scope)
        assert write.status_code == 204, f"write failed: {write.text}"

        sign = self._sign(e2e_client, platform_admin.headers, path, location, scope=scope)
        assert sign.status_code == 200, f"sign failed: {sign.text}"
        signed = sign.json()
        assert signed["path"] == f"{location}/{scope}/{path}", (
            f"freeform sign should target {location}/{scope}/{path}, got {signed['path']}"
        )

        with httpx.Client(timeout=30.0) as s3:
            r = s3.get(signed["url"])
        assert r.status_code == 200, f"download failed: {r.status_code} {r.text[:200]}"
        assert r.content == content


@pytest.mark.e2e
class TestSignedUrlValidation:
    def test_reserved_prefix_location_rejected(self, e2e_client, platform_admin):
        r = e2e_client.post(
            "/api/files/signed-url",
            headers=platform_admin.headers,
            json={
                "path": "x.txt",
                "method": "GET",
                "location": "_repo",
                "content_type": "text/plain",
            },
        )
        assert r.status_code == 400
        assert "reserved bucket prefix" in r.text

    def test_invalid_freeform_name_rejected(self, e2e_client, platform_admin):
        r = e2e_client.post(
            "/api/files/signed-url",
            headers=platform_admin.headers,
            json={
                "path": "x.txt",
                "method": "GET",
                "location": "Bad Name!",
                "content_type": "text/plain",
            },
        )
        assert r.status_code == 400
        assert "must match" in r.text or "Invalid location name" in r.text

    def test_path_traversal_rejected(self, e2e_client, platform_admin):
        r = e2e_client.post(
            "/api/files/signed-url",
            headers=platform_admin.headers,
            json={
                "path": "../etc/passwd",
                "method": "GET",
                "location": "uploads",
                "content_type": "text/plain",
            },
        )
        assert r.status_code == 400
        assert "traversal" in r.text.lower()

    def test_temp_requires_scope(self, e2e_client, platform_admin):
        r = e2e_client.post(
            "/api/files/signed-url",
            headers=platform_admin.headers,
            json={
                "path": "x.txt",
                "method": "GET",
                "location": "temp",
                "content_type": "text/plain",
                "scope": None,
            },
        )
        assert r.status_code == 400
        assert "scope" in r.text.lower()
