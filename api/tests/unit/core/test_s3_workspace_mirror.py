"""S3 acceleration mechanics stay private to its workspace mirror."""
import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.config import Settings
from src.core.storage.s3_mirror import S3WorkspaceMirror


@pytest.fixture
def mock_settings():
    return Settings(_env_file=None, s3_bucket="bifrost-local", s3_endpoint_url="http://seaweedfs:8333",
                    s3_access_key="bifrost", s3_secret_key="bifrost123", s3_region="us-east-1", redis_url="")


@pytest.fixture
def mirror(mock_settings):
    return S3WorkspaceMirror(mock_settings)


class TestBuildSyncCmd:
    """Tests for _build_sync_cmd command construction."""

    def test_basic_sync_down(self, mirror):
        cmd = mirror._build_sync_cmd(
            source="s3://bifrost-local/_repo/",
            dest="/tmp/work",
        )
        assert cmd == [
            "aws", "s3", "sync",
            "s3://bifrost-local/_repo/",
            "/tmp/work",
            "--endpoint-url", "http://seaweedfs:8333",
            "--only-show-errors",
        ]

    def test_sync_up_with_delete(self, mirror):
        cmd = mirror._build_sync_cmd(
            source="/tmp/work",
            dest="s3://bifrost-local/_repo/",
            delete=True,
        )
        assert cmd == [
            "aws", "s3", "sync",
            "/tmp/work",
            "s3://bifrost-local/_repo/",
            "--delete",
            "--endpoint-url", "http://seaweedfs:8333",
            "--only-show-errors",
        ]

    def test_no_endpoint_for_aws(self, mock_settings):
        """When endpoint_url is None (real AWS), omit --endpoint-url."""
        mock_settings.s3_endpoint_url = None
        mgr = S3WorkspaceMirror(settings=mock_settings)
        cmd = mgr._build_sync_cmd(
            source="s3://prod-bucket/_repo/",
            dest="/tmp/work",
        )
        assert "--endpoint-url" not in cmd
        assert cmd == [
            "aws", "s3", "sync",
            "s3://prod-bucket/_repo/",
            "/tmp/work",
            "--only-show-errors",
        ]


class TestBuildEnv:
    """Tests for _build_env environment variable construction."""

    def test_sets_aws_credentials(self, mirror):
        env = mirror._build_env()
        assert env["AWS_ACCESS_KEY_ID"] == "bifrost"
        assert env["AWS_SECRET_ACCESS_KEY"] == "bifrost123"
        assert env["AWS_DEFAULT_REGION"] == "us-east-1"

    def test_inherits_os_environ(self, mirror):
        """Should include existing env vars (PATH, etc.)."""
        env = mirror._build_env()
        assert "PATH" in env


class TestRunAwsCli:
    """Tests for _run_aws_cli subprocess execution."""

    @pytest.mark.asyncio
    async def test_success(self, mirror):
        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(return_value=(b"", b""))
        mock_process.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
            await mirror._run_aws_cli(["aws", "s3", "sync", "src", "dst"])
            mock_exec.assert_called_once()
            # Verify env includes AWS credentials
            call_kwargs = mock_exec.call_args
            env = call_kwargs.kwargs["env"]
            assert env["AWS_ACCESS_KEY_ID"] == "bifrost"

    @pytest.mark.asyncio
    async def test_failure_raises_runtime_error(self, mirror):
        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(
            return_value=(b"", b"fatal: bucket not found")
        )
        mock_process.returncode = 1

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            with pytest.raises(RuntimeError, match="aws s3 sync failed"):
                await mirror._run_aws_cli(["aws", "s3", "sync", "src", "dst"])


class TestS3TimestampAmbiguity:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("etag, expected_mtime", [
        ("6654c734ccab8f440ff0825eb443dc7f", 100),  # MD5 of v1: unchanged
        ("1b267619c4812cc46ee281747884ca50e", 0),  # MD5 of v2: same-size edit
        ("opaque-multipart-2", 0),
    ])
    async def test_equal_stats_only_skip_verified_content(self, mirror, tmp_path, etag, expected_mtime):
        import os
        from datetime import datetime, timezone

        from src.core.storage.contracts import ObjectPage, ObjectSummary, ObjectVersion

        local = tmp_path / "note.txt"
        local.write_bytes(b"v1")
        os.utime(local, (100, 100))
        metadata = ObjectSummary("_repo/note.txt", 2, ObjectVersion(etag), datetime.fromtimestamp(100, timezone.utc))
        store = AsyncMock()
        store.__aenter__.return_value = store
        store.list_page.return_value = ObjectPage((metadata,), (), None)
        with patch("src.core.storage.s3_mirror.S3ObjectStore.from_settings", return_value=store):
            await mirror._invalidate_ambiguous_sync_timestamps("_repo/", tmp_path)
        assert local.stat().st_mtime == expected_mtime
        assert local.read_bytes() == b"v1"



@pytest.mark.parametrize("prefix", ["", "/", "../", "_repo/../", "_repo//", "_repo\\outside/"])
def test_native_prefix_cannot_escape_workspace_namespace(mirror, prefix):
    with pytest.raises(ValueError):
        mirror._uri(prefix)


@pytest.mark.parametrize("cancellations", [1, 2])
async def test_cancelled_native_transfer_kills_and_drains_before_releasing_owner(mirror, cancellations):
    started, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    process = Mock()
    process.returncode = None
    async def communicate():
        started.set()
        await release.wait()
        finished.set()
        return b"", b""
    process.communicate = communicate
    process.wait = AsyncMock(return_value=-9)
    with patch("asyncio.create_subprocess_exec", return_value=process):
        owner = asyncio.create_task(mirror._run_aws_cli(["aws", "s3", "sync", "source", "target"]))
        await started.wait()
        try:
            for _ in range(cancellations):
                owner.cancel()
                await asyncio.sleep(0)
                assert not owner.done()
            process.kill.assert_called_once()
            release.set()
            await asyncio.wait({owner})
            with pytest.raises(asyncio.CancelledError):
                owner.result()
            assert finished.is_set()
            process.wait.assert_awaited_once()
        finally:
            release.set()
            await asyncio.gather(owner, return_exceptions=True)


async def test_download_refuses_invalid_prefix_before_touching_target(mirror, tmp_path):
    target = tmp_path / "not-created"
    with pytest.raises(ValueError):
        await mirror.download("../", target)
    assert not target.exists()


async def test_rejected_snapshot_does_not_start_native_upload(mirror, tmp_path):
    from src.core.storage.contracts import ObjectPage, ObjectSummary, ObjectVersion
    store = AsyncMock()
    store.__aenter__.return_value = store
    store.list_page.return_value = ObjectPage((ObjectSummary("_repo/file", 1, ObjectVersion("newer"), None),), (), None)
    def validate(snapshot):
        assert snapshot == {"file": "newer"}
        raise RuntimeError("stale baseline")
    with patch("src.core.storage.s3_mirror.S3ObjectStore.from_settings", return_value=store), patch.object(mirror, "_run_aws_cli", new_callable=AsyncMock) as transfer:
        with pytest.raises(RuntimeError, match="stale baseline"):
            await mirror.upload(tmp_path, "_repo/", validate_snapshot=validate)
    transfer.assert_not_awaited()
