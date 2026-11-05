"""Catalog contracts for scheduler-owned platform jobs."""

from __future__ import annotations

import subprocess
import sys
import textwrap


def test_catalog_metadata_matches_each_materialized_definition() -> None:
    from src.jobs.platform.catalog import list_platform_job_metadata
    from src.jobs.platform.registry import get_platform_job_definition

    metadata = list_platform_job_metadata()

    assert len(metadata) == 23
    assert {item.job_type for item in metadata} == {
        "agent.summary_backfill",
        "application.deploy",
        "application.publish",
        "application.sdk_update",
        "artifact.retention_cleanup",
        "audit.archive",
        "audit.query",
        "embedding.reindex",
        "files.move",
        "model_catalog.refresh",
        "oauth.refresh",
        "run.retention",
        "sdk.video_generation",
        "solution.deploy",
        "solution.export",
        "solution.git_sync",
        "solution.update_check",
        "chat.video_generation",
        "webhook.renew",
        "workspace.bundle_import",
        "workspace.file_index_reconcile",
        "workspace.git",
        "workspace.reimport",
    }
    for item in metadata:
        definition = get_platform_job_definition(item.job_type)
        assert definition is not None
        assert definition.job_type == item.job_type
        assert definition.payload_version == item.payload_version
        assert definition.policy == item.policy
        assert definition.encrypt_payload == item.encrypt_payload
        assert definition.display_name == item.display_name
        assert definition.description == item.description


def test_metadata_lookup_imports_no_handler_modules_in_a_fresh_process() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                """
                import sys
                from src.jobs.platform.catalog import get_platform_job_metadata

                metadata = get_platform_job_metadata("application.deploy")
                assert metadata is not None
                assert metadata.job_type == "application.deploy"
                assert metadata.payload_version == 1
                assert metadata.policy.execution_class == "build"
                assert metadata.encrypt_payload is False
                assert "src.jobs.platform.application_deploy" not in sys.modules
                assert not {"pydantic", "sqlalchemy", "fastapi"}.intersection(sys.modules)
                """
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_public_job_projection_uses_metadata_without_importing_handlers() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                """
                import sys
                from datetime import datetime, timezone
                from types import SimpleNamespace
                from uuid import uuid4

                from src.jobs.platform.catalog import list_platform_job_metadata
                from src.services.platform_jobs import platform_job_to_public

                now = datetime.now(timezone.utc)
                metadata = list_platform_job_metadata()
                for item in metadata:
                    job = SimpleNamespace(
                        id=uuid4(),
                        job_type=item.job_type,
                        payload_version=item.payload_version,
                        organization_id=None,
                        resource_type=None,
                        resource_id=None,
                        resource_lock_key=None,
                        priority=100,
                        title=item.job_type,
                        action_url=None,
                        execution_backend="local",
                        requested_by_user_id="fixture",
                        requested_by_name="Fixture",
                        status="running",
                        phase=None,
                        progress_current=0,
                        progress_total=None,
                        progress_percent=None,
                        revision=1,
                        attempt=1,
                        max_attempts=2,
                        result=None,
                        error_code=None,
                        error_message=None,
                        error_retryable=None,
                        notification_id=None,
                        memory_required_bytes=0,
                        memory_start_bytes=None,
                        memory_peak_bytes=None,
                        memory_limit_bytes=None,
                        started_at=now,
                        completed_at=None,
                        created_at=now,
                        updated_at=now,
                    )
                    public = platform_job_to_public(job)
                    assert public.can_cancel is item.policy.allow_running_cancellation

                assert not {item.module for item in metadata}.intersection(sys.modules)
                """
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
