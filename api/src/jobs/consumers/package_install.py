"""
Package Installation Consumer

Processes package installation requests from RabbitMQ fanout exchange.
Uses broadcast delivery so all worker instances install the package.

Flow:
1. API handler updates requirements.txt in S3 + Redis cache (persistence)
2. API broadcasts message to this consumer on all workers (with a shared run_id)
3. Consumer runs pip install (makes package available on worker filesystem)
4. Consumer recycles processes (clears Python import cache)
5. Consumer updates package list in Redis (so /api/packages reflects changes)

Progress is aggregated via Redis per run_id (see install_progress.py) so that
each phase is reported once across all workers, not once per worker.
"""

import asyncio
import logging
import os
import sys
from typing import Any

from src.jobs.rabbitmq import BroadcastConsumer
from src.services.execution.install_progress import report_phase

logger = logging.getLogger(__name__)

# Exchange name for broadcast
EXCHANGE_NAME = "package-installations"


class PackageInstallConsumer(BroadcastConsumer):
    """
    Broadcast consumer for package installation.

    Uses fanout exchange so ALL worker instances receive the message,
    pip install the package, and recycle their process pools.

    Message format:
    {
        "type": "recycle_workers",
        "action": "install" | "uninstall" (optional, default "install"),
        "package": "package-name" (optional),
        "version": "1.0.0" (optional),
        "is_update": true | false (optional),
        "run_id": "<uuid>" (shared across workers for phase aggregation),
    }
    """

    def __init__(self):
        super().__init__(exchange_name=EXCHANGE_NAME)

    @property
    def _worker_id(self) -> str:
        return os.environ.get("HOSTNAME", "unknown")

    async def _pip_uninstall(self, package: str) -> str | None:
        """Run pip uninstall for a package on this worker.

        Returns None on success, or the error message on failure.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "pip", "uninstall", "-y", package, "--quiet",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            if proc.returncode == 0:
                logger.info(f"Uninstalled {package}")
                return None
            err = stderr.decode()
            # pip exits non-zero when the package isn't installed; that's fine
            # for our purposes — the post-condition (package absent) holds.
            if "not installed" in err.lower() or "skipping" in err.lower():
                logger.info(f"Package {package} was not installed; nothing to do")
                return None
            logger.warning(f"pip uninstall {package} failed: {err}")
            return err.strip()
        except asyncio.TimeoutError:
            logger.error(f"pip uninstall {package} timed out")
            return f"pip uninstall {package} timed out"
        except Exception as e:
            logger.error(f"pip uninstall {package} error: {e}")
            return f"pip uninstall {package} error: {e}"

    async def _pip_install(self, package: str, version: str | None) -> str | None:
        """
        Run pip install for a specific package on this worker.

        Installs into the worker's Python environment so all child processes
        (which share the same filesystem) can import it after recycle.

        Returns None on success, or the error message on failure.
        """
        package_spec = f"{package}=={version}" if version else package

        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "pip", "install", package_spec, "--quiet",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)

            if proc.returncode == 0:
                logger.info(f"Installed {package_spec}")
                return None
            else:
                err = stderr.decode()
                logger.warning(f"pip install {package_spec} failed: {err}")
                return err.strip()
        except asyncio.TimeoutError:
            logger.error(f"pip install {package_spec} timed out")
            return f"pip install {package_spec} timed out"
        except Exception as e:
            logger.error(f"pip install {package_spec} error: {e}")
            return f"pip install {package_spec} error: {e}"

    async def _pip_install_requirements(self) -> str | None:
        """
        Run pip install from the cached requirements.txt.

        Used when no specific package is provided (e.g., "Install from
        requirements.txt" button).

        Returns None on success, or the error message on failure.
        """
        import tempfile

        from src.core.requirements_cache import get_requirements

        cached = await get_requirements()
        if not cached or not cached["content"].strip():
            logger.info("No cached requirements.txt to install from")
            return None

        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False
            ) as f:
                f.write(cached["content"])
                temp_path = f.name

            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "pip", "install", "-r", temp_path, "--quiet",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)

            os.unlink(temp_path)

            if proc.returncode == 0:
                pkg_count = len([
                    line for line in cached["content"].strip().split("\n") if line.strip()
                ])
                logger.info(f"Installed {pkg_count} packages from requirements.txt")
                return None
            else:
                err = stderr.decode()
                logger.warning(f"pip install -r requirements.txt failed: {err}")
                return err.strip()
        except asyncio.TimeoutError:
            logger.error("pip install -r requirements.txt timed out")
            return "pip install -r requirements.txt timed out"
        except Exception as e:
            logger.error(f"pip install -r requirements.txt error: {e}")
            return f"pip install -r requirements.txt error: {e}"

    async def _update_pool_packages(self) -> None:
        """Update the pool's packages in Redis after installation."""
        try:
            from src.services.execution.process_pool import get_process_pool

            pool = get_process_pool()
            if pool._started:
                await pool.update_packages()
        except Exception as e:
            logger.warning(f"Failed to update packages in Redis: {e}")

    async def _recycle_workers(self) -> None:
        """
        Drain all worker processes and restart the template so that child
        processes forked afterward have a fresh sys.modules that can see
        newly installed packages.
        """
        try:
            from src.services.execution.process_pool import get_process_pool

            pool = get_process_pool()
            if pool._started:
                await pool.drain_and_restart_template()
                logger.info("Drained workers and restarted template after pip install")
            else:
                logger.warning("Pool not started, skipping worker recycle")
        except Exception as e:
            logger.warning(f"Failed to drain/restart after pip install: {e}")

    async def process_message(self, body: dict[str, Any]) -> None:
        """Process a package install or uninstall message.

        `action` is "install" (default) or "uninstall". For "install" with
        `package=None`, runs pip install -r requirements.txt (sync from cache).

        Phases are reported via report_phase() to a shared Redis hash keyed by
        run_id so the frontend sees one aggregate status, not one per worker.
        """
        action = body.get("action", "install")
        package = body.get("package")
        version = body.get("version")
        run_id = body.get("run_id", "current")
        package_spec = (
            f"{package}=={version}" if package and version else (package or "requirements.txt")
        )
        wid = self._worker_id
        logger.info(f"Processing package {action}: {package_spec} (run={run_id})")

        await report_phase(run_id, wid, phase="installing", action=action)

        if action == "uninstall":
            if not package:
                await report_phase(
                    run_id, wid, phase="failed", action=action,
                    package="(none)", error="uninstall requires a package name",
                )
                return
            err = await self._pip_uninstall(package)
        elif package:
            err = await self._pip_install(package, version)
        else:
            err = await self._pip_install_requirements()

        if err is not None:
            await report_phase(
                run_id, wid, phase="failed", action=action,
                package=package_spec, error=err or "pip command failed",
            )
            return

        # Worker subprocesses are forked before pip runs; recycle so they pick
        # up the new on-disk state.
        await report_phase(run_id, wid, phase="recycling", action=action)
        await self._recycle_workers()
        await self._update_pool_packages()
        await report_phase(run_id, wid, phase="recycled", action=action)
        logger.info(f"Package {action} completed on {wid}")
