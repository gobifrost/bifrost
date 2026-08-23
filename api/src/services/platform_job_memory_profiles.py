"""Helpers for learned memory requirements on platform jobs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.platform_job_memory_profiles import PlatformJobMemoryProfile

if TYPE_CHECKING:
    from src.services.platform_jobs import PlatformJobDefinition
    from src.models.orm.platform_jobs import PlatformJob

MIB = 1024 * 1024
UNKNOWN_SOLUTION_WORKLOAD_FLOOR_BYTES = 512 * MIB
MEMORY_PROFILE_SAFETY_MARGIN_BYTES = 128 * MIB
PROFILE_KEY_PREFIX = "solution.deploy.memory.v1"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _sorted_mapping(mapping: Mapping[str, Any] | None) -> list[tuple[str, Any]]:
    if not mapping:
        return []
    return sorted((str(key), value) for key, value in mapping.items())


def _content_digest(value: Any) -> str:
    if isinstance(value, bytes):
        content = value
    elif isinstance(value, str):
        content = value.encode("utf-8")
    else:
        content = _canonical_json(value).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def _fingerprinted_mapping(
    mapping: Mapping[str, Any] | None,
) -> list[tuple[str, str]]:
    if not mapping:
        return []
    return sorted((str(key), _content_digest(value)) for key, value in mapping.items())


def _app_workload_fingerprint(app: Mapping[str, Any]) -> dict[str, Any]:
    src_files = _fingerprinted_mapping(app.get("src_files"))
    bin_files = _fingerprinted_mapping(app.get("bin_files"))
    dist_files = _fingerprinted_mapping(app.get("dist_files"))
    bin_dist_files = _fingerprinted_mapping(app.get("bin_dist_files"))
    dependencies = _sorted_mapping(app.get("dependencies"))
    prebuilt = bool(dist_files or bin_dist_files)
    workload = {
        "app_model": app.get("app_model", "inline_v1"),
        "build_mode": "prebuilt" if prebuilt else "source",
        "dependencies": dependencies,
        "src_files": src_files,
        "bin_files": bin_files,
    }
    if prebuilt:
        workload["dist_files"] = dist_files
        workload["bin_dist_files"] = bin_dist_files
    return workload


def build_solution_memory_profile_key(preview: Any) -> str:
    """Fingerprint a parsed solution preview into a stable workload key."""

    apps = getattr(preview, "apps", None) or []
    payload = {
        "solution_version": getattr(preview, "version", None),
        "apps": sorted(
            (_app_workload_fingerprint(app) for app in apps),
            key=_canonical_json,
        ),
    }
    return f"{PROFILE_KEY_PREFIX}:{_sha256(payload)}"


async def resolve_platform_job_memory_required_bytes(
    db: AsyncSession,
    definition: "PlatformJobDefinition",
    *,
    memory_profile_key: str | None = None,
) -> int:
    """Resolve the persisted admission requirement for one new job."""

    policy_floor = definition.policy.min_memory_headroom_mb * MIB
    if memory_profile_key is None:
        return policy_floor

    profile = await db.get(PlatformJobMemoryProfile, memory_profile_key)
    learned_floor = (
        profile.memory_required_bytes
        if profile is not None
        else UNKNOWN_SOLUTION_WORKLOAD_FLOOR_BYTES
    )
    return max(policy_floor, UNKNOWN_SOLUTION_WORKLOAD_FLOOR_BYTES, learned_floor)


def observed_working_set_delta_bytes(job: "PlatformJob") -> int | None:
    """Compute the attempt's working-set delta from the persisted start/peak."""

    if job.memory_start_bytes is None or job.memory_peak_bytes is None:
        return None
    return max(job.memory_peak_bytes - job.memory_start_bytes, 0)


async def record_platform_job_memory_profile(
    db: AsyncSession,
    job: "PlatformJob",
) -> None:
    """Learn from a terminal attempt using the conservative working-set delta."""

    if not job.memory_profile_key:
        return
    observed = observed_working_set_delta_bytes(job)
    if observed is None or observed <= 0:
        return

    required = max(
        job.memory_required_bytes or 0,
        observed + MEMORY_PROFILE_SAFETY_MARGIN_BYTES,
        UNKNOWN_SOLUTION_WORKLOAD_FLOOR_BYTES,
    )
    profile = await db.get(PlatformJobMemoryProfile, job.memory_profile_key)
    if profile is None:
        db.add(
            PlatformJobMemoryProfile(
                profile_key=job.memory_profile_key,
                memory_required_bytes=required,
                observed_high_water_bytes=observed,
                sample_count=1,
            )
        )
        return

    profile.memory_required_bytes = max(profile.memory_required_bytes, required)
    profile.observed_high_water_bytes = max(profile.observed_high_water_bytes, observed)
    profile.sample_count += 1
