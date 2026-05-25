"""
Packages Router

Python package management for the workflow runtime.
Allows listing, installing, and uninstalling Python packages.

Package visibility works by querying worker pools registered in Redis.
Each worker registers its installed packages at bifrost:pool:{pool_id}.
"""

import asyncio
import logging
import json
from typing import Awaitable, cast
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status

from src.models import (
    InstallPackageRequest,
    InstalledPackage,
    InstalledPackagesResponse,
    PackageInstallResponse,
    PackageUpdate,
    PackageUpdatesResponse,
)
from src.core.auth import Context, CurrentSuperuser
from src.core.log_safety import log_safe
from src.core.redis_client import get_redis_client
from src.core.requirements_cache import (
    append_package_to_requirements,
    get_requirements,
    remove_package_from_requirements,
    save_requirements,
    warm_requirements_cache,
)
from src.jobs.rabbitmq import publish_broadcast

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/packages", tags=["Packages"])


# =============================================================================
# Helper Functions
# =============================================================================


async def get_packages_from_workers() -> list[InstalledPackage] | None:
    """
    Get installed packages from worker pools registered in Redis.

    Workers register at bifrost:pool:{pool_id} and include a 'packages' field
    with JSON-encoded list of {name, version} dicts.

    Returns:
        Aggregated list of unique packages from all workers, or None if no workers found.
        When multiple workers have different versions, the highest version is used.
    """
    try:
        redis_client = get_redis_client()
        raw_redis = await redis_client._get_redis()
        cursor: int = 0
        all_packages: dict[str, str] = {}  # name -> version (highest wins)

        while True:
            cursor, keys = await cast(
                Awaitable[tuple[int, list[str]]],
                raw_redis.scan(cursor, match="bifrost:pool:*", count=100)
            )
            for key in keys:
                # Skip non-registration keys (heartbeat, commands, etc.)
                # Pool registration keys are exactly "bifrost:pool:{worker_id}" (3 parts)
                # Other keys have more parts like "bifrost:pool:{worker_id}:heartbeat"
                if key.count(":") != 2:
                    continue
                # Pool data is stored as a hash, get the "packages" field
                packages_json = await cast(
                    Awaitable[str | None],
                    raw_redis.hget(key, "packages")
                )
                if packages_json:
                    try:
                        packages_data = json.loads(packages_json)
                        for pkg in packages_data:
                            name = pkg.get("name", "").lower()
                            version = pkg.get("version", "")
                            if name:
                                # Keep highest version if multiple workers report different versions
                                if name not in all_packages or version > all_packages[name]:
                                    all_packages[name] = version
                    except (json.JSONDecodeError, TypeError):
                        logger.warning(f"Failed to parse packages from {key}")
                        continue

            if cursor == 0:
                break

        if not all_packages:
            logger.debug("No worker packages found in Redis")
            return None

        logger.debug(f"Found {len(all_packages)} packages from workers")
        return [
            InstalledPackage(name=name, version=version)
            for name, version in sorted(all_packages.items())
        ]

    except Exception as e:
        logger.warning(f"Failed to get packages from workers: {e}")
        return None


async def check_package_updates() -> list[PackageUpdate]:
    """
    Check for available package updates using pip.

    Uses async subprocess to avoid blocking the event loop.

    Returns:
        List of packages with available updates
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "pip", "list", "--outdated", "--format=json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            logger.error("pip list --outdated timed out")
            return []

        if proc.returncode != 0:
            logger.error(f"pip list --outdated failed: {stderr.decode()}")
            return []

        packages_data = json.loads(stdout.decode())
        return [
            PackageUpdate(
                name=pkg["name"],
                current_version=pkg["version"],
                latest_version=pkg["latest_version"],
            )
            for pkg in packages_data
        ]

    except json.JSONDecodeError:
        logger.error("Failed to parse pip outdated output")
        return []
    except Exception as e:
        logger.error(f"Error checking package updates: {str(e)}")
        return []


async def get_installed_packages_local() -> list[InstalledPackage]:
    """
    Get list of installed Python packages from local pip.

    Uses async subprocess to avoid blocking the event loop.
    This is a fallback when no workers are registered in Redis.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "pip", "list", "--format=json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            logger.error("pip list timed out")
            return []

        if proc.returncode != 0:
            logger.error(f"pip list failed: {stderr.decode()}")
            return []

        packages_data = json.loads(stdout.decode())
        return [
            InstalledPackage(name=pkg["name"], version=pkg["version"])
            for pkg in packages_data
        ]

    except json.JSONDecodeError:
        logger.error("Failed to parse pip output")
        return []
    except Exception as e:
        logger.error(f"Error getting installed packages: {str(e)}")
        return []


async def get_installed_packages() -> list[InstalledPackage]:
    """
    Get list of installed Python packages.

    First tries to get packages from registered workers in Redis.
    Falls back to local pip if no workers are registered.
    """
    # Try to get packages from workers first
    worker_packages = await get_packages_from_workers()
    if worker_packages is not None:
        logger.debug(f"Using packages from workers: {len(worker_packages)} packages")
        return worker_packages

    # Fallback to local pip (useful for dev/test or API-only deployments)
    logger.debug("No workers found, falling back to local pip")
    return await get_installed_packages_local()


# =============================================================================
# HTTP Endpoints
# =============================================================================


@router.get(
    "",
    response_model=InstalledPackagesResponse,
    summary="List installed packages",
    description="List all installed Python packages",
)
async def list_packages(
    ctx: Context,
    user: CurrentSuperuser,
) -> InstalledPackagesResponse:
    """
    List all installed Python packages.

    Returns:
        List of installed packages with versions
    """
    try:
        packages = await get_installed_packages()
        logger.info(f"Listed {len(packages)} installed packages")
        return InstalledPackagesResponse(
            packages=packages,
            total_count=len(packages),
        )

    except Exception as e:
        logger.error(f"Error listing packages: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list packages",
        )


@router.get(
    "/updates",
    response_model=PackageUpdatesResponse,
    summary="Check for package updates",
    description="Check for available updates to installed packages",
)
async def check_updates(
    ctx: Context,
    user: CurrentSuperuser,
) -> PackageUpdatesResponse:
    """
    Check for available package updates.

    Returns:
        List of packages with available updates
    """
    try:
        updates = await check_package_updates()
        logger.info(f"Found {len(updates)} package updates available")
        return PackageUpdatesResponse(
            updates_available=updates,
            total_count=len(updates),
        )

    except Exception as e:
        logger.error(f"Error checking package updates: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to check for package updates",
        )


@router.post(
    "/install",
    response_model=PackageInstallResponse,
    summary="Install a Python package",
    description="Install a Python package or recycle workers from requirements.txt (Platform admin only)",
)
async def install_package(
    request: InstallPackageRequest,
    ctx: Context,
    user: CurrentSuperuser,
) -> PackageInstallResponse:
    """
    Install a Python package by updating requirements.txt and recycling workers.

    When package_name is provided: appends/updates the package in requirements.txt
    (S3 + Redis cache), then broadcasts a recycle signal to all workers.

    When package_name is None: warms the Redis cache from S3 (picking up any
    manual edits to requirements.txt), then broadcasts recycle.

    Workers recycle their process pools; new processes pip install from the
    cached requirements.txt on startup.

    Returns immediately — worker recycling happens asynchronously.
    """
    try:
        if request.package_name:
            # Append/update package in requirements.txt
            package_spec = request.package_name
            if request.version:
                package_spec = f"{request.package_name}=={request.version}"

            cached = await get_requirements()
            if not cached:
                # Redis cache miss — warm from S3 and retry
                await warm_requirements_cache()
                cached = await get_requirements()
            current_content = cached["content"] if cached else ""
            updated_content, is_update = append_package_to_requirements(
                current_content, request.package_name, request.version
            )
            await save_requirements(updated_content)

            logger.info(f"Updated requirements.txt with {log_safe(package_spec)}")
        else:
            # "Install from requirements.txt" — warm cache from S3 so workers
            # pick up the latest file content
            await warm_requirements_cache()
            # Safe default: requirements.txt may contain updates
            is_update = True
            logger.info("Warmed requirements cache from S3 for recycle")

        # Broadcast to all workers — they pip install + recycle processes.
        # run_id ties all workers' phase reports to the same Redis hash so
        # the frontend sees one aggregate status instead of N per-worker logs.
        await publish_broadcast(
            exchange_name="package-installations",
            message={
                "type": "recycle_workers",
                "package": request.package_name,
                "version": request.version if request.version else None,
                "is_update": is_update,
                "run_id": str(uuid4()),
            },
        )

        return PackageInstallResponse(
            package_name=request.package_name,
            version=request.version,
            status="success",
            message="Requirements updated. Workers are recycling to pick up changes.",
        )

    except Exception as e:
        logger.error(f"Error updating requirements: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update requirements",
        )


@router.delete(
    "/{package_name}",
    summary="Uninstall a Python package",
    description="Uninstall a Python package (Platform admin only)",
)
async def uninstall_package(
    package_name: str,
    ctx: Context,
    user: CurrentSuperuser,
) -> dict:
    """Uninstall a package: drop it from requirements.txt and recycle workers.

    Mirrors `install_package` — workers re-pip-install from requirements.txt on
    recycle, and any package removed from requirements is gone after the next
    process spawn.
    """
    try:
        logger.info(f"Uninstalling package: {log_safe(package_name)}")

        cached = await get_requirements()
        if not cached:
            await warm_requirements_cache()
            cached = await get_requirements()
        current_content = cached["content"] if cached else ""
        updated_content, was_present = remove_package_from_requirements(
            current_content, package_name
        )

        if was_present:
            await save_requirements(updated_content)
            logger.info(f"Removed {log_safe(package_name)} from requirements.txt")

        # Tell workers to pip uninstall and recycle. The "action" field
        # is what distinguishes install from uninstall in the consumer.
        # run_id ties all workers' phase reports to the same Redis hash.
        await publish_broadcast(
            exchange_name="package-installations",
            message={
                "type": "recycle_workers",
                "action": "uninstall",
                "package": package_name,
                "version": None,
                "is_update": True,
                "run_id": str(uuid4()),
            },
        )

        return {
            "message": (
                f"Package '{package_name}' removed from requirements; workers recycling."
            ),
            "status": "uninstalled",
            "was_present": was_present,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uninstalling package: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to uninstall package",
        )
