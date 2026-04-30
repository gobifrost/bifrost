"""
Unit tests for POST /api/files/signed-url endpoint.

Tests path validation, location/scope handling, and presigned URL generation.
Path resolution is delegated to `shared.file_paths.resolve_s3_key`.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.routers.files import (
    SignedUrlRequest,
    SignedUrlResponse,
    get_signed_url,
)


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

        req = SignedUrlRequest(path="report.pdf", scope="org-a")
        result = await get_signed_url(req, MagicMock(), MagicMock(), AsyncMock())
        assert result.path == "uploads/org-a/report.pdf"

    @pytest.mark.asyncio
    async def test_uploads_requires_scope(self):
        from fastapi import HTTPException

        req = SignedUrlRequest(path="report.pdf")  # default location=uploads, no scope
        with pytest.raises(HTTPException) as exc_info:
            await get_signed_url(req, MagicMock(), MagicMock(), AsyncMock())
        assert exc_info.value.status_code == 400
        assert "scope" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    @patch("src.routers.files.FileStorageService")
    async def test_workspace_unscoped(self, mock_fss_class):
        mock_fss = MagicMock()
        mock_fss.generate_presigned_upload_url = AsyncMock(return_value="https://s3/url")
        mock_fss_class.return_value = mock_fss

        req = SignedUrlRequest(path="report.pdf", location="workspace")
        result = await get_signed_url(req, MagicMock(), MagicMock(), AsyncMock())
        assert result.path == "_repo/report.pdf"

    @pytest.mark.asyncio
    @patch("src.routers.files.FileStorageService")
    async def test_temp_scoped(self, mock_fss_class):
        mock_fss = MagicMock()
        mock_fss.generate_presigned_download_url = AsyncMock(return_value="https://s3/url")
        mock_fss_class.return_value = mock_fss

        req = SignedUrlRequest(path="x.bin", location="temp", scope="org-a", method="GET")
        result = await get_signed_url(req, MagicMock(), MagicMock(), AsyncMock())
        assert result.path == "_tmp/org-a/x.bin"

    @pytest.mark.asyncio
    @patch("src.routers.files.FileStorageService")
    async def test_freeform_scoped(self, mock_fss_class):
        mock_fss = MagicMock()
        mock_fss.generate_presigned_download_url = AsyncMock(return_value="https://s3/url")
        mock_fss_class.return_value = mock_fss

        req = SignedUrlRequest(path="q1.pdf", location="reports", scope="org-a", method="GET")
        result = await get_signed_url(req, MagicMock(), MagicMock(), AsyncMock())
        assert result.path == "reports/org-a/q1.pdf"


class TestPathValidation:
    """Test that handler returns 400 on resolver-rejected inputs."""

    @pytest.mark.asyncio
    async def test_rejects_path_traversal(self):
        from fastapi import HTTPException

        req = SignedUrlRequest(path="../etc/passwd", scope="org-a")
        with pytest.raises(HTTPException) as exc_info:
            await get_signed_url(req, MagicMock(), MagicMock(), AsyncMock())
        assert exc_info.value.status_code == 400
        assert "traversal" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    async def test_rejects_absolute_path(self):
        from fastapi import HTTPException

        req = SignedUrlRequest(path="/absolute/path", scope="org-a")
        with pytest.raises(HTTPException) as exc_info:
            await get_signed_url(req, MagicMock(), MagicMock(), AsyncMock())
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_rejects_reserved_location_name(self):
        from fastapi import HTTPException

        req = SignedUrlRequest(path="x.txt", location="_repo", scope="org-a")
        with pytest.raises(HTTPException) as exc_info:
            await get_signed_url(req, MagicMock(), MagicMock(), AsyncMock())
        assert exc_info.value.status_code == 400
        assert "reserved bucket prefix" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_rejects_invalid_freeform_name(self):
        from fastapi import HTTPException

        req = SignedUrlRequest(path="x.txt", location="Bad Name!", scope="org-a")
        with pytest.raises(HTTPException) as exc_info:
            await get_signed_url(req, MagicMock(), MagicMock(), AsyncMock())
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_temp_requires_scope(self):
        from fastapi import HTTPException

        req = SignedUrlRequest(path="x.txt", location="temp", scope=None)
        with pytest.raises(HTTPException) as exc_info:
            await get_signed_url(req, MagicMock(), MagicMock(), AsyncMock())
        assert exc_info.value.status_code == 400
        assert "scope" in str(exc_info.value.detail).lower()


class TestPresignedUrlGeneration:
    """Test that correct S3 method is called based on request method."""

    @pytest.mark.asyncio
    @patch("src.routers.files.FileStorageService")
    async def test_put_calls_upload(self, mock_fss_class):
        mock_fss = MagicMock()
        mock_fss.generate_presigned_upload_url = AsyncMock(return_value="https://s3/put-url")
        mock_fss_class.return_value = mock_fss

        req = SignedUrlRequest(path="file.pdf", method="PUT", content_type="application/pdf", scope="org-a")
        result = await get_signed_url(req, MagicMock(), MagicMock(), AsyncMock())
        assert result.url == "https://s3/put-url"
        mock_fss.generate_presigned_upload_url.assert_awaited_once_with(
            path="uploads/org-a/file.pdf",
            content_type="application/pdf",
        )

    @pytest.mark.asyncio
    @patch("src.routers.files.FileStorageService")
    async def test_get_calls_download(self, mock_fss_class):
        mock_fss = MagicMock()
        mock_fss.generate_presigned_download_url = AsyncMock(return_value="https://s3/get-url")
        mock_fss_class.return_value = mock_fss

        req = SignedUrlRequest(path="file.pdf", method="GET", scope="org-a")
        result = await get_signed_url(req, MagicMock(), MagicMock(), AsyncMock())
        assert result.url == "https://s3/get-url"
        mock_fss.generate_presigned_download_url.assert_awaited_once_with(
            path="uploads/org-a/file.pdf",
        )
