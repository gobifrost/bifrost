"""Code-owned metadata for scheduler-owned platform jobs.

This module deliberately contains only standard-library data. Supervisors use
it to admit and describe jobs without importing a payload model or handler.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from src.jobs.platform.policy import PlatformJobPolicy


@dataclass(frozen=True)
class PlatformJobMetadata:
    job_type: str
    payload_version: int
    policy: PlatformJobPolicy
    module: str
    definition_name: str
    encrypt_payload: bool = False
    display_name: str | None = None
    description: str | None = None


def _metadata(
    job_type: str,
    module: str,
    definition_name: str,
    policy: PlatformJobPolicy,
    *,
    encrypt_payload: bool = False,
    display_name: str | None = None,
    description: str | None = None,
) -> PlatformJobMetadata:
    return PlatformJobMetadata(
        job_type=job_type,
        payload_version=1,
        policy=policy,
        module=module,
        definition_name=definition_name,
        encrypt_payload=encrypt_payload,
        display_name=display_name,
        description=description,
    )


_METADATA = MappingProxyType(
    {
        item.job_type: item
        for item in (
            _metadata(
                "application.deploy",
                "src.jobs.platform.application_deploy",
                "APPLICATION_DEPLOY_DEFINITION",
                PlatformJobPolicy(
                    20 * 60,
                    max_attempts=1,
                    max_concurrency=1,
                    min_memory_headroom_mb=512,
                    execution_class="build",
                ),
                display_name="App Deploys",
                description="Compiles and publishes independent apps (npm install and vite build) in an isolated pod.",
            ),
            _metadata(
                "application.sdk_update",
                "src.jobs.platform.application_sdk_update",
                "APPLICATION_SDK_UPDATE_DEFINITION",
                PlatformJobPolicy(
                    20 * 60,
                    max_attempts=1,
                    max_concurrency=1,
                    min_memory_headroom_mb=512,
                    execution_class="build",
                ),
                display_name="App SDK Rebuilds",
                description="Rebuilds the web SDK and republishes affected apps in an isolated pod.",
            ),
            _metadata(
                "application.publish",
                "src.jobs.platform.application_publish",
                "APPLICATION_PUBLISH_DEFINITION",
                PlatformJobPolicy(20 * 60, max_attempts=2, max_concurrency=1),
            ),
            _metadata(
                "oauth.refresh",
                "src.jobs.platform.system_maintenance",
                "OAUTH_REFRESH_DEFINITION",
                PlatformJobPolicy(
                    15 * 60,
                    max_attempts=2,
                    max_concurrency=1,
                    min_memory_headroom_mb=128,
                ),
            ),
            _metadata(
                "webhook.renew",
                "src.jobs.platform.system_maintenance",
                "WEBHOOK_RENEWAL_DEFINITION",
                PlatformJobPolicy(
                    30 * 60,
                    max_attempts=2,
                    max_concurrency=1,
                    min_memory_headroom_mb=128,
                ),
            ),
            _metadata(
                "solution.update_check",
                "src.jobs.platform.system_maintenance",
                "SOLUTION_UPDATE_CHECK_DEFINITION",
                PlatformJobPolicy(30 * 60, max_attempts=2, max_concurrency=1),
            ),
            _metadata(
                "workspace.file_index_reconcile",
                "src.jobs.platform.system_maintenance",
                "FILE_INDEX_RECONCILIATION_DEFINITION",
                PlatformJobPolicy(60 * 60, max_attempts=2, max_concurrency=1),
            ),
            _metadata(
                "artifact.retention_cleanup",
                "src.jobs.platform.system_maintenance",
                "ARTIFACT_RETENTION_CLEANUP_DEFINITION",
                PlatformJobPolicy(
                    30 * 60,
                    max_attempts=2,
                    max_concurrency=1,
                    min_memory_headroom_mb=128,
                ),
            ),
            _metadata(
                "audit.archive",
                "src.jobs.platform.audit_archive",
                "AUDIT_ARCHIVE_DEFINITION",
                PlatformJobPolicy(
                    2 * 60 * 60,
                    max_attempts=3,
                    max_concurrency=1,
                    allow_running_cancellation=True,
                ),
            ),
            _metadata(
                "audit.query",
                "src.jobs.platform.audit_query",
                "AUDIT_QUERY_DEFINITION",
                PlatformJobPolicy(2 * 60 * 60, allow_running_cancellation=True),
            ),
            _metadata(
                "run.retention",
                "src.jobs.platform.run_retention",
                "RUN_RETENTION_DEFINITION",
                PlatformJobPolicy(
                    2 * 60 * 60,
                    max_attempts=3,
                    max_concurrency=1,
                    allow_running_cancellation=True,
                ),
            ),
            _metadata(
                "model_catalog.refresh",
                "src.jobs.platform.system_maintenance",
                "MODEL_CATALOG_REFRESH_DEFINITION",
                PlatformJobPolicy(5 * 60, max_attempts=2, max_concurrency=1),
            ),
            _metadata(
                "files.move",
                "src.jobs.platform.file_move",
                "FILE_MOVE_DEFINITION",
                PlatformJobPolicy(
                    4 * 60 * 60,
                    max_attempts=1,
                    retry_on_runner_loss=False,
                    min_memory_headroom_mb=128,
                ),
            ),
            _metadata(
                "solution.export",
                "src.jobs.platform.solution_export",
                "SOLUTION_EXPORT_DEFINITION",
                PlatformJobPolicy(
                    60 * 60,
                    max_attempts=2,
                    max_concurrency=1,
                    min_memory_headroom_mb=512,
                    execution_class="build",
                ),
                display_name="Solution Backup Exports",
                description="Builds encrypted full-backup archives in an isolated pod.",
            ),
            _metadata(
                "solution.deploy",
                "src.jobs.platform.solution_deploy",
                "SOLUTION_DEPLOY_DEFINITION",
                PlatformJobPolicy(
                    60 * 60,
                    max_attempts=2,
                    max_concurrency=1,
                    min_memory_headroom_mb=512,
                    execution_class="build",
                ),
                encrypt_payload=True,
                display_name="Solution Deploys",
                description="Compiles Solution apps in an isolated pod.",
            ),
            _metadata(
                "solution.git_sync",
                "src.jobs.platform.solution_git_sync",
                "SOLUTION_GIT_SYNC_DEFINITION",
                PlatformJobPolicy(
                    60 * 60,
                    max_attempts=2,
                    max_concurrency=1,
                    retry_on_failure=True,
                    min_memory_headroom_mb=512,
                    allow_running_cancellation=True,
                    execution_class="build",
                ),
                display_name="Solution Git Syncs",
                description="Pulls and compiles connected Solution apps in an isolated pod.",
            ),
            _metadata(
                "embedding.reindex",
                "src.jobs.platform.embedding_reindex",
                "EMBEDDING_REINDEX_DEFINITION",
                PlatformJobPolicy(
                    4 * 60 * 60,
                    max_attempts=2,
                    max_concurrency=1,
                    min_memory_headroom_mb=512,
                ),
            ),
            _metadata(
                "workspace.reimport",
                "src.jobs.platform.reimport",
                "WORKSPACE_REIMPORT_DEFINITION",
                PlatformJobPolicy(
                    60 * 60,
                    max_attempts=2,
                    max_concurrency=1,
                    min_memory_headroom_mb=512,
                ),
            ),
            _metadata(
                "workspace.git",
                "src.jobs.platform.git_operation",
                "GIT_OPERATION_DEFINITION",
                PlatformJobPolicy(
                    60 * 60,
                    max_attempts=2,
                    max_concurrency=1,
                    min_memory_headroom_mb=512,
                ),
            ),
            _metadata(
                "workspace.bundle_import",
                "src.jobs.platform.workspace_bundle_import",
                "WORKSPACE_BUNDLE_IMPORT_DEFINITION",
                PlatformJobPolicy(
                    60 * 60,
                    max_attempts=2,
                    max_concurrency=1,
                    retry_on_failure=True,
                    min_memory_headroom_mb=512,
                    allow_running_cancellation=True,
                ),
                encrypt_payload=True,
            ),
            _metadata(
                "agent.summary_backfill",
                "src.jobs.platform.summary_backfill",
                "SUMMARY_BACKFILL_DEFINITION",
                PlatformJobPolicy(15 * 60, max_attempts=2, min_memory_headroom_mb=128),
                encrypt_payload=True,
            ),
            _metadata(
                "chat.video_generation",
                "src.jobs.platform.video_generation",
                "VIDEO_GENERATION_DEFINITION",
                PlatformJobPolicy(
                    30 * 60,
                    max_attempts=1,
                    retry_on_runner_loss=False,
                    min_memory_headroom_mb=512,
                    allow_running_cancellation=True,
                ),
                encrypt_payload=True,
            ),
            _metadata(
                "sdk.video_generation",
                "src.jobs.platform.video_generation",
                "SDK_VIDEO_GENERATION_DEFINITION",
                PlatformJobPolicy(
                    30 * 60,
                    max_attempts=1,
                    retry_on_runner_loss=False,
                    min_memory_headroom_mb=512,
                    allow_running_cancellation=True,
                ),
                encrypt_payload=True,
            ),
        )
    }
)


def get_platform_job_metadata(job_type: str) -> PlatformJobMetadata | None:
    return _METADATA.get(job_type)


def list_platform_job_metadata() -> list[PlatformJobMetadata]:
    return list(_METADATA.values())
