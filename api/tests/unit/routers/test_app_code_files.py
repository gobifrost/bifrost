"""Unit tests for app code files router."""

import pytest
from fastapi import HTTPException
from unittest.mock import ANY, AsyncMock, MagicMock

from src.routers.app_code_files import standalone_v2_runtime_contract, validate_file_path


@pytest.mark.parametrize(
    "meta",
    [
        '<meta name="bifrost-app-runtime" content="mount-v1">',
        "<META content='mount-v1' data-x='1' name='bifrost-app-runtime'>",
    ],
)
def test_standalone_v2_runtime_contract_detects_mount_marker(meta: str):
    assert standalone_v2_runtime_contract(f"<html><head>{meta}</head></html>") == "mount-v1"


@pytest.mark.parametrize(
    "html",
    [
        "<html><head></head></html>",
        '<meta name="bifrost-app-runtime" content="future-v2">',
        '<meta name="something-else" content="mount-v1">',
    ],
)
def test_standalone_v2_runtime_contract_rejects_missing_or_unknown_marker(html: str):
    assert standalone_v2_runtime_contract(html) is None


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

        app_id = uuid4()
        fake_app = SimpleNamespace(id=app_id)
        fake_ctx = SimpleNamespace(
            user=SimpleNamespace(embed=False),
        )

        async def _fake_get_app(ctx, authorization, _app_id):
            return fake_app

        monkeypatch.setattr(
            "src.routers.app_code_files.authorized_application_by_id", _fake_get_app
        )

        class _FakeBuilder:
            async def read_dist(self, _app_id, _rel):
                raise read_dist_exc

        monkeypatch.setattr(
            "src.services.solutions.app_build.SolutionAppBuilder", _FakeBuilder
        )
        return app_id, fake_ctx

    @staticmethod
    def _authorization():
        authorization = MagicMock()
        authorization.require = MagicMock()
        return authorization

    async def test_missing_key_returns_404(self, monkeypatch):
        """The not-found type read_dist actually raises (botocore ClientError
        with Code=NoSuchKey, from get_object) still maps to 404."""
        from botocore.exceptions import ClientError

        from src.routers.app_code_files import get_v2_dist_asset

        not_found = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "The specified key does not exist."}},
            "GetObject",
        )
        app_id, ctx = self._setup(monkeypatch, not_found)
        authorization = self._authorization()

        with pytest.raises(HTTPException) as exc_info:
            await get_v2_dist_asset(
                app_id=app_id,
                path="index.html",
                ctx=ctx,
                authorization=authorization,
            )
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "dist asset not found: index.html"
        authorization.require.assert_called_once_with("apps.read")

    async def test_storage_error_is_logged_and_reraised_not_404(self, monkeypatch, caplog):
        """A real storage failure (RuntimeError) must NOT become a 404 — it
        surfaces as-is and is logged."""
        from src.routers.app_code_files import get_v2_dist_asset

        app_id, ctx = self._setup(monkeypatch, RuntimeError("s3 exploded"))
        authorization = self._authorization()

        with caplog.at_level("ERROR", logger="src.routers.app_code_files"):
            with pytest.raises(RuntimeError, match="s3 exploded"):
                await get_v2_dist_asset(
                    app_id=app_id,
                    path="index.html",
                    ctx=ctx,
                    authorization=authorization,
                )
        assert any("dist asset read failed" in r.message for r in caplog.records)
        authorization.require.assert_called_once_with("apps.read")

    async def test_non_notfound_client_error_is_logged_and_reraised(self, monkeypatch, caplog):
        """A ClientError that is NOT NoSuchKey (e.g. AccessDenied) is a real
        storage error, not a missing asset."""
        from botocore.exceptions import ClientError

        from src.routers.app_code_files import get_v2_dist_asset

        denied = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "denied"}}, "GetObject"
        )
        app_id, ctx = self._setup(monkeypatch, denied)
        authorization = self._authorization()

        with caplog.at_level("ERROR", logger="src.routers.app_code_files"):
            with pytest.raises(ClientError):
                await get_v2_dist_asset(
                    app_id=app_id,
                    path="main.js",
                    ctx=ctx,
                    authorization=authorization,
                )
        assert any("dist asset read failed" in r.message for r in caplog.records)
        authorization.require.assert_called_once_with("apps.read")


class TestBundleAssetAuthorization:
    async def test_exact_boundary_authorized_caller_reads_bundle_asset(
        self,
        monkeypatch,
    ):
        from types import SimpleNamespace
        from uuid import uuid4

        from src.routers.app_code_files import FileMode, get_bundle_asset

        app_id = uuid4()
        ctx = SimpleNamespace(user=SimpleNamespace(embed=False))
        authorization = MagicMock()
        authorization.require = MagicMock()
        authorized = AsyncMock(return_value=SimpleNamespace(id=app_id))
        storage = MagicMock()
        storage.read_file = AsyncMock(return_value=b"console.log('ok')")
        monkeypatch.setattr(
            "src.routers.app_code_files.authorized_application_by_id",
            authorized,
        )
        monkeypatch.setattr(
            "src.routers.app_code_files.AppStorageService",
            MagicMock(return_value=storage),
        )

        response = await get_bundle_asset(
            app_id=app_id,
            filename="main.js",
            mode=FileMode.draft,
            ctx=ctx,
            authorization=authorization,
        )

        authorization.require.assert_called_once_with("apps.read")
        authorized.assert_awaited_once()
        storage.read_file.assert_awaited_once_with(str(app_id), "preview", "main.js")
        assert response.media_type == "application/javascript"
        assert response.body == b"console.log('ok')"

    async def test_denied_caller_does_not_read_bundle_asset_storage(
        self,
        monkeypatch,
    ):
        from types import SimpleNamespace
        from uuid import uuid4

        from src.routers.app_code_files import get_bundle_asset

        app_id = uuid4()
        ctx = SimpleNamespace(user=SimpleNamespace(embed=False))
        authorization = MagicMock()
        authorization.require = MagicMock()
        denied = HTTPException(status_code=404, detail="Application not found")
        authorized = AsyncMock(side_effect=denied)
        storage_factory = MagicMock()
        monkeypatch.setattr(
            "src.routers.app_code_files.authorized_application_by_id",
            authorized,
        )
        monkeypatch.setattr(
            "src.routers.app_code_files.AppStorageService",
            storage_factory,
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_bundle_asset(
                app_id=app_id,
                filename="main.js",
                ctx=ctx,
                authorization=authorization,
            )

        assert exc_info.value is denied
        authorization.require.assert_called_once_with("apps.read")
        authorized.assert_awaited_once()
        storage_factory.assert_not_called()


class TestRuntimeApplicationAuthorization:
    async def test_embed_token_is_admitted_only_for_bound_app(self):
        from types import SimpleNamespace
        from uuid import uuid4

        from src.routers.app_code_files import authorized_runtime_application_by_id

        app_id = uuid4()
        app = SimpleNamespace(id=app_id)
        user = SimpleNamespace(embed=True, app_id=str(app_id))
        ctx = SimpleNamespace(user=user, db=MagicMock())
        ctx.db.get = AsyncMock(return_value=app)

        resolved = await authorized_runtime_application_by_id(
            ctx,
            authorization=None,
            app_id=app_id,
        )

        assert resolved is app
        ctx.db.get.assert_awaited_once_with(ANY, app_id)

    async def test_embed_token_is_denied_for_unbound_app(self):
        from types import SimpleNamespace
        from uuid import uuid4

        from src.routers.app_code_files import authorized_runtime_application_by_id

        bound_app_id = uuid4()
        requested_app_id = uuid4()
        user = SimpleNamespace(embed=True, app_id=str(bound_app_id))
        ctx = SimpleNamespace(user=user, db=MagicMock())
        ctx.db.get = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await authorized_runtime_application_by_id(
                ctx,
                authorization=None,
                app_id=requested_app_id,
            )

        assert exc_info.value.status_code == 404
        ctx.db.get.assert_not_awaited()
