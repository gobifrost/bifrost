"""Unit tests for app code files router."""

import pytest
from fastapi import HTTPException

from src.routers.app_code_files import validate_file_path


class TestValidateFilePath:
    """Tests for the validate_file_path function."""

    # ==========================================================================
    # Valid paths
    # ==========================================================================

    def test_valid_root_layout(self):
        """Root _layout.tsx is valid."""
        validate_file_path("_layout.tsx")

    def test_valid_root_providers(self):
        """Root _providers.tsx is valid."""
        validate_file_path("_providers.tsx")

    def test_valid_pages_index(self):
        """pages/index.tsx is valid."""
        validate_file_path("pages/index.tsx")

    def test_valid_pages_layout(self):
        """pages/_layout.tsx is valid."""
        validate_file_path("pages/_layout.tsx")

    def test_valid_pages_nested(self):
        """Nested pages are valid."""
        validate_file_path("pages/clients/index.tsx")
        validate_file_path("pages/clients/_layout.tsx")

    def test_valid_pages_dynamic(self):
        """Dynamic route segments in pages are valid."""
        validate_file_path("pages/clients/[id].tsx")
        validate_file_path("pages/clients/[id]/edit.tsx")

    def test_valid_components_file(self):
        """Component files are valid."""
        validate_file_path("components/Button.tsx")
        validate_file_path("components/ClientCard.tsx")

    def test_valid_components_nested(self):
        """Nested component folders are valid."""
        validate_file_path("components/ui/Button.tsx")
        validate_file_path("components/forms/ClientForm.tsx")

    def test_valid_modules_file(self):
        """Module files are valid."""
        validate_file_path("modules/api.ts")
        validate_file_path("modules/utils.ts")

    def test_valid_modules_nested(self):
        """Nested module folders are valid."""
        validate_file_path("modules/services/api.ts")
        validate_file_path("modules/hooks/useAuth.ts")

    def test_valid_path_with_underscores(self):
        """Paths with underscores are valid."""
        validate_file_path("components/my_component.tsx")
        validate_file_path("modules/api_client.ts")

    def test_valid_path_with_hyphens(self):
        """Paths with hyphens are valid."""
        validate_file_path("components/my-component.tsx")
        validate_file_path("modules/api-client.ts")

    # ==========================================================================
    # Invalid paths - empty/malformed
    # ==========================================================================

    def test_invalid_empty_path(self):
        """Empty path is rejected."""
        with pytest.raises(HTTPException) as exc_info:
            validate_file_path("")
        assert exc_info.value.status_code == 400
        assert "cannot be empty" in exc_info.value.detail

    def test_invalid_double_slashes(self):
        """Double slashes are rejected."""
        with pytest.raises(HTTPException) as exc_info:
            validate_file_path("pages//index")
        assert exc_info.value.status_code == 400
        assert "empty segments" in exc_info.value.detail

    # ==========================================================================
    # Invalid paths - root level
    # ==========================================================================

    def test_invalid_root_arbitrary_file(self):
        """Arbitrary files at root are rejected."""
        with pytest.raises(HTTPException) as exc_info:
            validate_file_path("main.tsx")
        assert exc_info.value.status_code == 400
        assert "_layout" in exc_info.value.detail
        assert "_providers" in exc_info.value.detail

    def test_invalid_root_index(self):
        """index at root is rejected (must be in pages/)."""
        with pytest.raises(HTTPException) as exc_info:
            validate_file_path("index.tsx")
        assert exc_info.value.status_code == 400

    # ==========================================================================
    # Invalid paths - wrong top directory
    # ==========================================================================

    def test_invalid_top_dir(self):
        """Invalid top-level directories are rejected."""
        with pytest.raises(HTTPException) as exc_info:
            validate_file_path("services/api.ts")
        assert exc_info.value.status_code == 400
        assert "pages" in exc_info.value.detail
        assert "components" in exc_info.value.detail
        assert "modules" in exc_info.value.detail

    def test_invalid_top_dir_utils(self):
        """utils/ directory is rejected."""
        with pytest.raises(HTTPException) as exc_info:
            validate_file_path("utils/helpers.ts")
        assert exc_info.value.status_code == 400

    # ==========================================================================
    # Invalid paths - dynamic segments outside pages/
    # ==========================================================================

    def test_invalid_dynamic_in_components(self):
        """Dynamic segments in components/ are rejected."""
        with pytest.raises(HTTPException) as exc_info:
            validate_file_path("components/[id].tsx")
        assert exc_info.value.status_code == 400
        assert "Dynamic segments" in exc_info.value.detail
        assert "pages/" in exc_info.value.detail

    def test_invalid_dynamic_in_modules(self):
        """Dynamic segments in modules/ are rejected."""
        with pytest.raises(HTTPException) as exc_info:
            validate_file_path("modules/[id]/utils.ts")
        assert exc_info.value.status_code == 400
        assert "Dynamic segments" in exc_info.value.detail

    # ==========================================================================
    # Invalid paths - _layout outside pages/
    # ==========================================================================

    def test_invalid_layout_in_components(self):
        """_layout in components/ is rejected."""
        with pytest.raises(HTTPException) as exc_info:
            validate_file_path("components/_layout.tsx")
        assert exc_info.value.status_code == 400
        assert "_layout" in exc_info.value.detail
        assert "pages/" in exc_info.value.detail

    def test_invalid_layout_in_modules(self):
        """_layout in modules/ is rejected."""
        with pytest.raises(HTTPException) as exc_info:
            validate_file_path("modules/_layout.tsx")
        assert exc_info.value.status_code == 400

    # ==========================================================================
    # Invalid paths - special characters
    # ==========================================================================

    def test_invalid_special_chars(self):
        """Paths with special characters are rejected."""
        with pytest.raises(HTTPException) as exc_info:
            validate_file_path("components/my.component.tsx")
        assert exc_info.value.status_code == 400
        assert "Invalid" in exc_info.value.detail

    def test_invalid_spaces(self):
        """Paths with spaces are rejected."""
        with pytest.raises(HTTPException) as exc_info:
            validate_file_path("components/my component.tsx")
        assert exc_info.value.status_code == 400
        assert "Invalid" in exc_info.value.detail

    # ==========================================================================
    # Edge cases
    # ==========================================================================

    def test_strips_leading_slash(self):
        """Leading slashes are stripped."""
        validate_file_path("/pages/index.tsx")

    def test_strips_trailing_slash(self):
        """Trailing slashes are stripped."""
        validate_file_path("pages/index.tsx/")

    def test_valid_deeply_nested(self):
        """Deeply nested paths are valid."""
        validate_file_path("components/ui/forms/fields/TextInput.tsx")
        validate_file_path("modules/services/auth/providers/oauth.ts")
        validate_file_path("pages/admin/users/[id]/settings/profile.tsx")


class TestGetV2DistAsset:
    """get_v2_dist_asset must only 404 on a genuinely missing key — real
    storage errors must be logged and re-raised, not swallowed as 404."""

    @staticmethod
    def _setup(monkeypatch, read_dist_exc: Exception):
        from types import SimpleNamespace
        from uuid import uuid4

        import src.routers.app_code_files as router_mod
        import src.services.solutions.app_build as build_mod

        app_id = uuid4()
        fake_app = SimpleNamespace(id=app_id)

        async def _fake_get_app(ctx, _app_id):
            return fake_app

        monkeypatch.setattr(router_mod, "get_application_or_404", _fake_get_app)

        class _FakeBuilder:
            async def read_dist(self, _app_id, _rel):
                raise read_dist_exc

        monkeypatch.setattr(build_mod, "SolutionAppBuilder", _FakeBuilder)
        return app_id

    async def test_missing_key_returns_404(self, monkeypatch):
        """The not-found type read_dist actually raises (botocore ClientError
        with Code=NoSuchKey, from get_object) still maps to 404."""
        from botocore.exceptions import ClientError

        from src.routers.app_code_files import get_v2_dist_asset

        not_found = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "The specified key does not exist."}},
            "GetObject",
        )
        app_id = self._setup(monkeypatch, not_found)

        with pytest.raises(HTTPException) as exc_info:
            await get_v2_dist_asset(app_id=app_id, path="index.html", ctx=None, _user=None)
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "dist asset not found: index.html"

    async def test_storage_error_is_logged_and_reraised_not_404(self, monkeypatch, caplog):
        """A real storage failure (RuntimeError) must NOT become a 404 — it
        surfaces as-is and is logged."""
        from src.routers.app_code_files import get_v2_dist_asset

        app_id = self._setup(monkeypatch, RuntimeError("s3 exploded"))

        with caplog.at_level("ERROR", logger="src.routers.app_code_files"):
            with pytest.raises(RuntimeError, match="s3 exploded"):
                await get_v2_dist_asset(app_id=app_id, path="index.html", ctx=None, _user=None)
        assert any("dist asset read failed" in r.message for r in caplog.records)

    async def test_non_notfound_client_error_is_logged_and_reraised(self, monkeypatch, caplog):
        """A ClientError that is NOT NoSuchKey (e.g. AccessDenied) is a real
        storage error, not a missing asset."""
        from botocore.exceptions import ClientError

        from src.routers.app_code_files import get_v2_dist_asset

        denied = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "denied"}}, "GetObject"
        )
        app_id = self._setup(monkeypatch, denied)

        with caplog.at_level("ERROR", logger="src.routers.app_code_files"):
            with pytest.raises(ClientError):
                await get_v2_dist_asset(app_id=app_id, path="main.js", ctx=None, _user=None)
        assert any("dist asset read failed" in r.message for r in caplog.records)
