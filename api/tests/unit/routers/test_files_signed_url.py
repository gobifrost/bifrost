"""
Unit tests for POST /api/files/signed-url endpoint.

Tests path validation, location/scope handling, and presigned URL generation.
Path resolution is delegated to `shared.file_paths.resolve_s3_key`.
"""

from uuid import UUID

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.core.principal import UserPrincipal
from src.routers.files import (
    SignedUrlRequest,
    SignedUrlResponse,
    get_signed_url,
)

# A concrete org UUID — scope resolution (`resolve_target_org`) validates that a
# non-"global" scope is a real UUID, so these path-resolution tests use one.
ORG_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def _ctx():
    """A signed-url request ctx whose principal is a superuser with no context
    org, so an explicit `scope=` on the request is honored verbatim (a regular
    user would be pinned to their own org) and an omitted scope on a scoped
    location falls back to 'global'. These path-resolution tests pin the
    explicit-scope and global-fallback arms; cross-org pinning has e2e coverage."""
    ctx = MagicMock()
    ctx.org_id = None
    ctx.scope = None
    ctx.user = UserPrincipal(
        user_id=UUID("11111111-1111-1111-1111-111111111111"),
        email="admin@example.com",
        organization_id=None,
        is_superuser=True,
    )
    return ctx


@pytest.fixture(autouse=True)
def _allow_policy():
    """These tests exercise path resolution + S3 method dispatch, not the policy
    gate. Bypass the default-deny file-policy check so resolution is reached;
    policy enforcement has its own e2e coverage (test_file_policies_rest.py)."""
    with patch("src.routers.files._require_file_policy", new=AsyncMock(return_value=None)):
        yield


class TestSignedUrlRequestModel:
    """Test SignedUrlRequest validation."""

    def test_defaults(self):
        req = SignedUrlRequest(path="invoices/report.pdf")
        assert req.method == "PUT"
        assert req.content_type == "application/octet-stream"
        assert req.location == "uploads"  # backwards-compat default
        assert req.scope is None

    def test_explicit_get(self):
        req = SignedUrlRequest(path="data.csv", method="GET")
        assert req.method == "GET"

    def test_explicit_location(self):
        req = SignedUrlRequest(path="file.txt", location="workspace")
        assert req.location == "workspace"

    def test_explicit_scope(self):
        req = SignedUrlRequest(path="file.txt", location="temp", scope="org-123")
        assert req.scope == "org-123"


class TestSignedUrlResponseModel:
    """Test SignedUrlResponse shape."""

    def test_fields(self):
        resp = SignedUrlResponse(url="https://s3/presigned", path="uploads/org-a/file.txt")
        assert resp.url == "https://s3/presigned"
        assert resp.path == "uploads/org-a/file.txt"
        assert resp.expires_in == 600


class TestPathResolution:
    """Test that the handler delegates to shared.file_paths.resolve_s3_key."""

    @pytest.mark.asyncio
    @patch("src.routers.files.FileStorageService")
    async def test_uploads_scoped(self, mock_fss_class):
        mock_fss = MagicMock()
        mock_fss.generate_presigned_upload_url = AsyncMock(return_value="https://s3/url")
        mock_fss_class.return_value = mock_fss

        req = SignedUrlRequest(path="report.pdf", scope=str(ORG_A))
        result = await get_signed_url(req, _ctx(), MagicMock(), AsyncMock())
        assert result.path == f"uploads/{ORG_A}/report.pdf"

    @pytest.mark.asyncio
    @patch("src.routers.files.FileStorageService")
    async def test_uploads_no_scope_falls_back_to_global(self, mock_fss_class):
        # A caller with no org (ctx.org_id=None) and no explicit scope resolves
        # to the 'global' scope (a real logged-in user would default to their
        # own org instead). Resolution succeeds; the policy gate governs access.
        mock_fss = MagicMock()
        mock_fss.generate_presigned_upload_url = AsyncMock(return_value="https://s3/url")
        mock_fss_class.return_value = mock_fss

        req = SignedUrlRequest(path="report.pdf")  # default location=uploads, no scope
        result = await get_signed_url(req, _ctx(), MagicMock(), AsyncMock())
        assert result.path == "uploads/global/report.pdf"

    @pytest.mark.asyncio
    @patch("src.routers.files.FileStorageService")
    async def test_workspace_unscoped(self, mock_fss_class):
        mock_fss = MagicMock()
        mock_fss.generate_presigned_upload_url = AsyncMock(return_value="https://s3/url")
        mock_fss_class.return_value = mock_fss

        req = SignedUrlRequest(path="report.pdf", location="workspace")
        result = await get_signed_url(req, _ctx(), MagicMock(), AsyncMock())
        assert result.path == "_repo/report.pdf"

    @pytest.mark.asyncio
    @patch("src.routers.files.FileStorageService")
    async def test_temp_scoped(self, mock_fss_class):
        mock_fss = MagicMock()
        mock_fss.generate_presigned_download_url = AsyncMock(return_value="https://s3/url")
        mock_fss_class.return_value = mock_fss

        req = SignedUrlRequest(path="x.bin", location="temp", scope=str(ORG_A), method="GET")
        result = await get_signed_url(req, _ctx(), MagicMock(), AsyncMock())
        assert result.path == f"_tmp/{ORG_A}/x.bin"

    @pytest.mark.asyncio
    @patch("src.routers.files.FileStorageService")
    async def test_freeform_scoped(self, mock_fss_class):
        mock_fss = MagicMock()
        mock_fss.generate_presigned_download_url = AsyncMock(return_value="https://s3/url")
        mock_fss_class.return_value = mock_fss

        req = SignedUrlRequest(path="q1.pdf", location="reports", scope=str(ORG_A), method="GET")
        result = await get_signed_url(req, _ctx(), MagicMock(), AsyncMock())
        assert result.path == f"reports/{ORG_A}/q1.pdf"


class TestPathValidation:
    """Test that handler returns 400 on resolver-rejected inputs."""

    @pytest.mark.asyncio
    async def test_rejects_path_traversal(self):
        from fastapi import HTTPException

        req = SignedUrlRequest(path="../etc/passwd", scope=str(ORG_A))
        with pytest.raises(HTTPException) as exc_info:
            await get_signed_url(req, _ctx(), MagicMock(), AsyncMock())
        assert exc_info.value.status_code == 400
        assert "traversal" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    async def test_rejects_absolute_path(self):
        from fastapi import HTTPException

        req = SignedUrlRequest(path="/absolute/path", scope=str(ORG_A))
        with pytest.raises(HTTPException) as exc_info:
            await get_signed_url(req, _ctx(), MagicMock(), AsyncMock())
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_rejects_reserved_location_name(self):
        from fastapi import HTTPException

        req = SignedUrlRequest(path="x.txt", location="_repo", scope=str(ORG_A))
        with pytest.raises(HTTPException) as exc_info:
            await get_signed_url(req, _ctx(), MagicMock(), AsyncMock())
        assert exc_info.value.status_code == 400
        assert "reserved bucket prefix" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_rejects_invalid_freeform_name(self):
        from fastapi import HTTPException

        req = SignedUrlRequest(path="x.txt", location="Bad Name!", scope=str(ORG_A))
        with pytest.raises(HTTPException) as exc_info:
            await get_signed_url(req, _ctx(), MagicMock(), AsyncMock())
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    @patch("src.routers.files.FileStorageService")
    async def test_temp_no_scope_falls_back_to_global(self, mock_fss_class):
        # Same global-fallback arm as uploads: a scopeless caller on a scoped
        # location resolves to 'global' rather than erroring.
        mock_fss = MagicMock()
        mock_fss.generate_presigned_upload_url = AsyncMock(return_value="https://s3/url")
        mock_fss_class.return_value = mock_fss

        req = SignedUrlRequest(path="x.txt", location="temp", scope=None)
        result = await get_signed_url(req, _ctx(), MagicMock(), AsyncMock())
        assert result.path == "_tmp/global/x.txt"


class TestPresignedUrlGeneration:
    """Test that correct S3 method is called based on request method."""

    @pytest.mark.asyncio
    @patch("src.routers.files.FileStorageService")
    async def test_put_calls_upload(self, mock_fss_class):
        mock_fss = MagicMock()
        mock_fss.generate_presigned_upload_url = AsyncMock(return_value="https://s3/put-url")
        mock_fss_class.return_value = mock_fss

        req = SignedUrlRequest(path="file.pdf", method="PUT", content_type="application/pdf", scope=str(ORG_A))
        result = await get_signed_url(req, _ctx(), MagicMock(), AsyncMock())
        assert result.url == "https://s3/put-url"
        mock_fss.generate_presigned_upload_url.assert_awaited_once_with(
            path=f"uploads/{ORG_A}/file.pdf",
            content_type="application/pdf",
        )

    @pytest.mark.asyncio
    @patch("src.routers.files.FileStorageService")
    async def test_get_calls_download(self, mock_fss_class):
        mock_fss = MagicMock()
        mock_fss.generate_presigned_download_url = AsyncMock(return_value="https://s3/get-url")
        mock_fss_class.return_value = mock_fss

        req = SignedUrlRequest(path="file.pdf", method="GET", scope=str(ORG_A))
        result = await get_signed_url(req, _ctx(), MagicMock(), AsyncMock())
        assert result.url == "https://s3/get-url"
        mock_fss.generate_presigned_download_url.assert_awaited_once_with(
            path=f"uploads/{ORG_A}/file.pdf",
        )
