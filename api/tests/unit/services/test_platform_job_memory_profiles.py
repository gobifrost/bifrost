from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.jobs.platform.solution_deploy import (
    SOLUTION_DEPLOY_DEFINITION,
    SolutionDeployPayload,
)
from src.models.orm.platform_job_memory_profiles import PlatformJobMemoryProfile
from src.services.platform_job_memory_profiles import (
    UNKNOWN_SOLUTION_WORKLOAD_FLOOR_BYTES,
    build_solution_memory_profile_key,
)
from src.services.platform_jobs import enqueue_platform_job


def test_solution_memory_profile_key_distinguishes_source_and_prebuilt() -> None:
    source_preview = SimpleNamespace(
        version="2026.08.23",
        apps=[
            {
                "app_model": "standalone_v2",
                "dependencies": {"react": "18.3.1"},
                "src_files": {"apps/demo/src/main.tsx": "console.log('demo')"},
                "bin_files": {"apps/demo/public/logo.png": b"image"},
            }
        ],
    )
    prebuilt_preview = SimpleNamespace(
        version="2026.08.23",
        apps=[
            {
                "app_model": "standalone_v2",
                "dependencies": {"react": "18.3.1"},
                "src_files": {"apps/demo/src/main.tsx": "console.log('demo')"},
                "bin_files": {"apps/demo/public/logo.png": b"image"},
                "dist_files": {"dist/index.html": "<!doctype html>"},
            }
        ],
    )

    source_key = build_solution_memory_profile_key(source_preview)
    prebuilt_key = build_solution_memory_profile_key(prebuilt_preview)

    assert source_key != prebuilt_key
    assert source_key.startswith("solution.deploy.memory.v1:")
    assert prebuilt_key.startswith("solution.deploy.memory.v1:")


@pytest.mark.asyncio
async def test_enqueue_platform_job_uses_learned_memory_profile(
    db_session,
) -> None:
    profile_key = "solution.deploy.memory.v1:test-profile"
    db_session.add(
        PlatformJobMemoryProfile(
            profile_key=profile_key,
            memory_required_bytes=900 * 1024 * 1024,
            observed_high_water_bytes=512 * 1024 * 1024,
            sample_count=3,
        )
    )
    await db_session.commit()

    job, reused = await enqueue_platform_job(
        db_session,
        SOLUTION_DEPLOY_DEFINITION,
        SolutionDeployPayload(
            deploy_job_id=uuid4(),
            kind="deploy",
            install_id=uuid4(),
            input_sha256="a" * 64,
            options={},
        ),
        dedupe_key=str(uuid4()),
        organization_id=None,
        requested_by_user_id=uuid4(),
        requested_by_email="dev@example.com",
        requested_by_name="Dev",
        resource_type="solution_deploy",
        resource_id=str(uuid4()),
        title="Solution deploy",
        action_url="/solutions/test",
        memory_profile_key=profile_key,
    )

    assert reused is False
    assert job.memory_profile_key == profile_key
    assert job.memory_required_bytes == 900 * 1024 * 1024


@pytest.mark.asyncio
async def test_enqueue_platform_job_keeps_unknown_solution_floor(
    db_session,
) -> None:
    job, reused = await enqueue_platform_job(
        db_session,
        SOLUTION_DEPLOY_DEFINITION,
        SolutionDeployPayload(
            deploy_job_id=uuid4(),
            kind="deploy",
            install_id=uuid4(),
            input_sha256="b" * 64,
            options={},
        ),
        dedupe_key=str(uuid4()),
        organization_id=None,
        requested_by_user_id=uuid4(),
        requested_by_email="dev@example.com",
        requested_by_name="Dev",
        resource_type="solution_deploy",
        resource_id=str(uuid4()),
        title="Solution deploy",
        action_url="/solutions/test",
        memory_profile_key="solution.deploy.memory.v1:missing",
    )

    assert reused is False
    assert job.memory_required_bytes == UNKNOWN_SOLUTION_WORKLOAD_FLOOR_BYTES
