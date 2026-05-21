"""Unit tests for S3Backend path handling."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.file_backend import LocalBackend, S3Backend


class TestS3BackendPathHandling:
    """Verify S3Backend passes correct path format to each downstream method."""

    def _make_backend(self):
        mock_db = MagicMock()
        with patch("src.services.file_backend.FileStorageService") as MockFSS:
            backend = S3Backend(mock_db)
            backend.storage = MockFSS.return_value
            return backend

    @pytest.mark.asyncio
    async def test_read_passes_workspace_relative_path(self):
        """read() should pass workspace-relative path, not _repo/ prefixed."""
        backend = self._make_backend()
        backend.storage.read_file = AsyncMock(return_value=(b"content", None))

        await backend.read("workflows/test.py", "workspace")

        backend.storage.read_file.assert_called_once_with("workflows/test.py")

    @pytest.mark.asyncio
    async def test_read_rejects_workspace_parent_traversal(self):
        backend = self._make_backend()
        backend.storage.read_file = AsyncMock(return_value=(b"content", None))

        with pytest.raises(ValueError, match="path traversal"):
            await backend.read("../secret.txt", "workspace")

        backend.storage.read_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_write_passes_workspace_relative_path(self):
        """write() should pass workspace-relative path, not _repo/ prefixed."""
        backend = self._make_backend()
        backend.storage.write_file = AsyncMock()

        await backend.write("workflows/test.py", b"content", "workspace", "user")

        backend.storage.write_file.assert_called_once_with(
            "workflows/test.py", b"content", "user"
        )

    @pytest.mark.asyncio
    async def test_write_rejects_absolute_workspace_path(self):
        backend = self._make_backend()
        backend.storage.write_file = AsyncMock()

        with pytest.raises(ValueError, match="must be relative"):
            await backend.write("/etc/passwd", b"content", "workspace", "user")

        backend.storage.write_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_passes_workspace_relative_path(self):
        """delete() should pass workspace-relative path, not _repo/ prefixed."""
        backend = self._make_backend()
        backend.storage.delete_file = AsyncMock()

        await backend.delete("workflows/test.py", "workspace")

        backend.storage.delete_file.assert_called_once_with("workflows/test.py")

    @pytest.mark.asyncio
    async def test_delete_rejects_workspace_parent_traversal(self):
        backend = self._make_backend()
        backend.storage.delete_file = AsyncMock()

        with pytest.raises(ValueError, match="path traversal"):
            await backend.delete("workflows/../../secret.txt", "workspace")

        backend.storage.delete_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_list_passes_workspace_relative_directory(self):
        """list() should pass workspace-relative directory, not _repo/ prefixed."""
        backend = self._make_backend()
        backend.storage.list_files = AsyncMock(return_value=[])

        await backend.list("workflows", "workspace")

        backend.storage.list_files.assert_called_once_with("workflows")

    @pytest.mark.asyncio
    async def test_list_rejects_workspace_parent_traversal(self):
        backend = self._make_backend()
        backend.storage.list_files = AsyncMock(return_value=[])

        with pytest.raises(ValueError, match="path traversal"):
            await backend.list("../", "workspace")

        backend.storage.list_files.assert_not_called()

    @pytest.mark.asyncio
    async def test_exists_passes_full_s3_key(self):
        """exists() should pass _repo/ prefixed path (file_exists expects S3 key)."""
        backend = self._make_backend()
        backend.storage.file_exists = AsyncMock(return_value=True)

        await backend.exists("workflows/test.py", "workspace")

        backend.storage.file_exists.assert_called_once_with("_repo/workflows/test.py")

    @pytest.mark.asyncio
    async def test_exists_rejects_absolute_workspace_path(self):
        backend = self._make_backend()
        backend.storage.file_exists = AsyncMock(return_value=True)

        with pytest.raises(ValueError, match="must be relative"):
            await backend.exists("/etc/passwd", "workspace")

        backend.storage.file_exists.assert_not_called()


class TestLocalBackendSandbox:
    """Verify LocalBackend._resolve_path enforces a real containment check.

    Uses relative_to() rather than str.startswith() so a sibling-prefix path
    (base "/tmp/foo" vs "/tmp/foo_evil/x") cannot bypass the sandbox.
    """

    def _make_backend(self, tmp_path: Path) -> LocalBackend:
        """Build a LocalBackend rooted entirely under tmp_path."""
        backend = LocalBackend.__new__(LocalBackend)
        backend.workspace_root = tmp_path / "workspace"
        backend.temp_root = tmp_path / "temp"
        backend.uploads_root = tmp_path / "uploads"
        for d in (backend.workspace_root, backend.temp_root, backend.uploads_root):
            d.mkdir(parents=True, exist_ok=True)
        return backend

    def test_path_inside_sandbox_resolves(self, tmp_path: Path):
        backend = self._make_backend(tmp_path)

        resolved = backend._resolve_path("workflows/test.py", "workspace")

        assert resolved == (backend.workspace_root / "workflows/test.py").resolve()

    def test_parent_traversal_is_rejected(self, tmp_path: Path):
        backend = self._make_backend(tmp_path)

        with pytest.raises(ValueError, match="must be within workspace"):
            backend._resolve_path("../escape.txt", "workspace")

    def test_sibling_prefix_confusion_is_rejected(self, tmp_path: Path):
        """A path that resolves to a sibling directory sharing a string prefix
        with the sandbox base must be rejected — the old startswith() check
        accepted these (e.g., base /tmp/foo vs /tmp/foo_evil/x)."""
        backend = self._make_backend(tmp_path)
        # Create a real sibling whose name starts with the temp root's name.
        sibling = backend.temp_root.parent / (backend.temp_root.name + "_evil")
        sibling.mkdir()
        attack = sibling / "x"
        attack.write_text("nope")

        with pytest.raises(ValueError, match="must be within temp"):
            backend._resolve_path(str(attack), "temp")
