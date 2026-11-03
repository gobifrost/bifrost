"""Tests for RepoSyncWriter — manifest regeneration into S3 _repo/.bifrost/."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.repo_sync_writer import RepoSyncWriter


@pytest.fixture
def mock_settings():
    settings = MagicMock()
    settings.s3_configured = True
    settings.s3_bucket = "test-bucket"
    settings.s3_endpoint_url = "http://seaweedfs:8333"
    settings.s3_access_key = "test"
    settings.s3_secret_key = "test"
    settings.s3_region = "us-east-1"
    return settings


@pytest.fixture
def mock_db():
    return AsyncMock()


@pytest.fixture
def writer(mock_db, mock_settings):
    with patch("src.services.repo_sync_writer.get_settings", return_value=mock_settings):
        with patch("src.services.repo_sync_writer.RepoStorage"):
            w = RepoSyncWriter(mock_db)
            w._file_index = AsyncMock()
            w._file_index.write = AsyncMock(return_value="abc123")
            w._file_index.delete = AsyncMock()
            w._file_index.repo_storage.list = AsyncMock(return_value=[])
            return w


class TestRegenerateManifest:
    @pytest.mark.asyncio
    async def test_generates_and_writes_split_manifest_files(self, writer):
        from bifrost.manifest import Manifest, ManifestWorkflow

        mock_manifest = Manifest(
            workflows={
                "wf1": ManifestWorkflow(
                    id="11111111-1111-1111-1111-111111111111",
                    path="workflows/wf1.py",
                    function_name="wf1",
                )
            }
        )
        with patch(
            "src.services.repo_sync_writer.generate_manifest",
            new_callable=AsyncMock,
            return_value=mock_manifest,
        ):
            await writer.regenerate_manifest()

        # Should write split file(s), not single metadata.yaml
        write_calls = writer._file_index.write.call_args_list
        written_paths = [call[0][0] for call in write_calls]
        assert ".bifrost/workflows.yaml" in written_paths
        assert ".bifrost/metadata.yaml" not in written_paths

    @pytest.mark.asyncio
    async def test_empty_manifest_cleans_legacy(self, writer):
        from bifrost.manifest import Manifest

        mock_manifest = Manifest()
        with patch(
            "src.services.repo_sync_writer.generate_manifest",
            new_callable=AsyncMock,
            return_value=mock_manifest,
        ):
            await writer.regenerate_manifest()

        # No split files written for empty manifest
        writer._file_index.write.assert_not_awaited()
        # Should attempt to delete legacy file
        delete_calls = writer._file_index.delete.call_args_list
        deleted_paths = [call[0][0] for call in delete_calls]
        assert ".bifrost/metadata.yaml" in deleted_paths

    @pytest.mark.asyncio
    async def test_regeneration_removes_only_uuid_named_legacy_entity_files(self, writer):
        from bifrost.manifest import Manifest

        form_id = "11111111-1111-1111-1111-111111111111"
        agent_id = "22222222-2222-2222-2222-222222222222"
        writer._file_index.repo_storage.list = AsyncMock(
            side_effect=[
                [f"forms/{form_id}.form.yaml", "forms/runbook.form.yaml"],
                [f"agents/{agent_id}.agent.yaml", "agents/support.agent.yaml"],
            ]
        )
        with patch(
            "src.services.repo_sync_writer.generate_manifest",
            new_callable=AsyncMock,
            return_value=Manifest(),
        ):
            await writer.regenerate_manifest()

        deleted_paths = [call.args[0] for call in writer._file_index.delete.await_args_list]
        assert f"forms/{form_id}.form.yaml" in deleted_paths
        assert f"agents/{agent_id}.agent.yaml" in deleted_paths
        assert "forms/runbook.form.yaml" not in deleted_paths
        assert "agents/support.agent.yaml" not in deleted_paths

    @pytest.mark.asyncio
    async def test_skips_when_s3_not_configured(self, writer, mock_settings):
        mock_settings.s3_configured = False

        await writer.regenerate_manifest()

        writer._file_index.write.assert_not_awaited()


class TestLegacyEntityCleanup:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("entity_type", "entity_id", "expected_path"),
        [
            (
                "form",
                "11111111-1111-1111-1111-111111111111",
                "forms/11111111-1111-1111-1111-111111111111.form.yaml",
            ),
            (
                "agent",
                "22222222-2222-2222-2222-222222222222",
                "agents/22222222-2222-2222-2222-222222222222.agent.yaml",
            ),
        ],
    )
    async def test_deletes_exact_legacy_entity_path(
        self, writer, entity_type, entity_id, expected_path
    ):
        await writer.delete_legacy_entity_file(entity_type, entity_id)

        writer._file_index.delete.assert_awaited_once_with(expected_path)
