"""
Unit tests for package install consumer.

Tests the consumer that pip installs packages on the worker,
recycles processes, and updates the package list in Redis.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.jobs.consumers.package_install import PackageInstallConsumer


class TestProcessMessage:
    """Tests for process_message method."""

    @pytest.fixture
    def consumer(self) -> PackageInstallConsumer:
        return PackageInstallConsumer()

    @pytest.mark.asyncio
    async def test_specific_package_update_recycles(self, consumer: PackageInstallConsumer):
        """Test that updating a package (is_update=True) triggers recycle with correct phases."""
        with (
            patch("src.jobs.consumers.package_install.report_phase", new_callable=AsyncMock) as rp,
            patch.object(
                consumer, "_pip_install", new_callable=AsyncMock, return_value=None
            ) as mock_pip,
            patch.object(consumer, "_recycle_workers", new_callable=AsyncMock) as mock_recycle,
            patch.object(
                consumer, "_update_pool_packages", new_callable=AsyncMock
            ) as mock_update,
        ):
            await consumer.process_message({
                "type": "recycle_workers",
                "package": "requests",
                "version": "2.31.0",
                "is_update": True,
                "run_id": "test-run-1",
            })

            mock_pip.assert_called_once_with("requests", "2.31.0")
            mock_recycle.assert_called_once()
            mock_update.assert_called_once()
            phases = [c.kwargs["phase"] for c in rp.await_args_list]
            assert phases == ["installing", "recycling", "recycled"]

    @pytest.mark.asyncio
    async def test_new_package_also_recycles(self, consumer: PackageInstallConsumer):
        """Test that a new package (is_update=False) still triggers recycle.

        Worker subprocesses are forked before pip install, so they need
        recycling to see newly installed packages on the filesystem.
        """
        with (
            patch("src.jobs.consumers.package_install.report_phase", new_callable=AsyncMock) as rp,
            patch.object(
                consumer, "_pip_install", new_callable=AsyncMock, return_value=None
            ) as mock_pip,
            patch.object(consumer, "_recycle_workers", new_callable=AsyncMock) as mock_recycle,
            patch.object(
                consumer, "_update_pool_packages", new_callable=AsyncMock
            ) as mock_update,
        ):
            await consumer.process_message({
                "type": "recycle_workers",
                "package": "new-package",
                "version": "1.0.0",
                "is_update": False,
                "run_id": "test-run-2",
            })

            mock_pip.assert_called_once_with("new-package", "1.0.0")
            mock_recycle.assert_called_once()
            mock_update.assert_called_once()
            phases = [c.kwargs["phase"] for c in rp.await_args_list]
            assert phases == ["installing", "recycling", "recycled"]

    @pytest.mark.asyncio
    async def test_missing_is_update_defaults_to_recycle(self, consumer: PackageInstallConsumer):
        """Test that missing is_update defaults to True (safe default)."""
        with (
            patch("src.jobs.consumers.package_install.report_phase", new_callable=AsyncMock) as rp,
            patch.object(
                consumer, "_pip_install", new_callable=AsyncMock, return_value=None
            ),
            patch.object(consumer, "_recycle_workers", new_callable=AsyncMock) as mock_recycle,
            patch.object(
                consumer, "_update_pool_packages", new_callable=AsyncMock
            ),
        ):
            await consumer.process_message({
                "type": "recycle_workers",
                "package": "requests",
                "version": "2.31.0",
                "run_id": "test-run-3",
            })

            mock_recycle.assert_called_once()
            phases = [c.kwargs["phase"] for c in rp.await_args_list]
            assert phases == ["installing", "recycling", "recycled"]

    @pytest.mark.asyncio
    async def test_requirements_install(self, consumer: PackageInstallConsumer):
        """Test that no package triggers requirements.txt install + recycle."""
        with (
            patch("src.jobs.consumers.package_install.report_phase", new_callable=AsyncMock) as rp,
            patch.object(
                consumer, "_pip_install_requirements", new_callable=AsyncMock, return_value=None
            ) as mock_pip_req,
            patch.object(consumer, "_recycle_workers", new_callable=AsyncMock) as mock_recycle,
            patch.object(
                consumer, "_update_pool_packages", new_callable=AsyncMock
            ) as mock_update,
        ):
            await consumer.process_message({
                "type": "recycle_workers",
                "package": None,
                "is_update": True,
                "run_id": "test-run-4",
            })

            mock_pip_req.assert_called_once()
            mock_recycle.assert_called_once()
            mock_update.assert_called_once()
            phases = [c.kwargs["phase"] for c in rp.await_args_list]
            assert phases == ["installing", "recycling", "recycled"]

    @pytest.mark.asyncio
    async def test_install_failure_reports_failed_and_skips_recycle(
        self, consumer: PackageInstallConsumer
    ):
        """Test that pip failure reports failed phase with the real error and skips recycle."""
        with (
            patch("src.jobs.consumers.package_install.report_phase", new_callable=AsyncMock) as rp,
            patch.object(
                consumer, "_pip_install", new_callable=AsyncMock,
                return_value="ERROR: no C compiler",
            ),
            patch.object(consumer, "_recycle_workers", new_callable=AsyncMock) as recycle,
        ):
            await consumer.process_message({
                "action": "install",
                "package": "badpkg",
                "run_id": "test-run-fail",
            })

        phases = [c.kwargs["phase"] for c in rp.await_args_list]
        assert phases[0] == "installing"
        assert phases[-1] == "failed"
        # The real pip error must flow through to the aggregator.
        assert rp.await_args_list[-1].kwargs["error"] == "ERROR: no C compiler"
        recycle.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_uninstall_without_package_reports_failed_and_skips_recycle(
        self, consumer: PackageInstallConsumer
    ):
        """Test that uninstall with no package name fails before any pip call."""
        with (
            patch("src.jobs.consumers.package_install.report_phase", new_callable=AsyncMock) as rp,
            patch.object(consumer, "_recycle_workers", new_callable=AsyncMock) as recycle,
        ):
            await consumer.process_message({
                "action": "uninstall",
                "package": None,
                "run_id": "x",
            })

        phases = [c.kwargs["phase"] for c in rp.await_args_list]
        assert phases == ["installing", "failed"]
        recycle.assert_not_awaited()


class TestRecycleWorkers:
    """Tests for _recycle_workers method."""

    @pytest.fixture
    def consumer(self) -> PackageInstallConsumer:
        return PackageInstallConsumer()

    @pytest.mark.asyncio
    async def test_drains_and_restarts_template(self, consumer: PackageInstallConsumer):
        """Test that workers are drained and template is restarted."""
        mock_pool = MagicMock()
        mock_pool._started = True
        mock_pool.drain_and_restart_template = AsyncMock()

        with patch(
            "src.services.execution.process_pool.get_process_pool",
            return_value=mock_pool,
        ):
            await consumer._recycle_workers()

            mock_pool.drain_and_restart_template.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_when_pool_not_started(self, consumer: PackageInstallConsumer):
        """Test that recycle is skipped when pool is not started."""
        mock_pool = MagicMock()
        mock_pool._started = False
        mock_pool.drain_and_restart_template = AsyncMock()

        with patch(
            "src.services.execution.process_pool.get_process_pool",
            return_value=mock_pool,
        ):
            await consumer._recycle_workers()

            mock_pool.drain_and_restart_template.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_pool_error_gracefully(self, consumer: PackageInstallConsumer):
        """Test that pool errors are handled gracefully."""
        with patch(
            "src.services.execution.process_pool.get_process_pool",
            side_effect=RuntimeError("Pool not initialized"),
        ):
            # Should not raise
            await consumer._recycle_workers()


class TestUpdatePoolPackages:
    """Tests for _update_pool_packages method."""

    @pytest.fixture
    def consumer(self) -> PackageInstallConsumer:
        return PackageInstallConsumer()

    @pytest.mark.asyncio
    async def test_updates_packages(self, consumer: PackageInstallConsumer):
        """Test that pool packages are updated in Redis."""
        mock_pool = MagicMock()
        mock_pool._started = True
        mock_pool.update_packages = AsyncMock()

        with patch(
            "src.services.execution.process_pool.get_process_pool",
            return_value=mock_pool,
        ):
            await consumer._update_pool_packages()

            mock_pool.update_packages.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_error_gracefully(self, consumer: PackageInstallConsumer):
        """Test that errors are handled gracefully."""
        with patch(
            "src.services.execution.process_pool.get_process_pool",
            side_effect=RuntimeError("Pool not initialized"),
        ):
            # Should not raise
            await consumer._update_pool_packages()
