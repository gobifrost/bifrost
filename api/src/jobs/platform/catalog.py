"""Code-owned metadata for scheduler-owned platform jobs.

This module deliberately contains only standard-library data. Supervisors use
it to admit and describe jobs without importing a payload model or handler.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from src.jobs.platform.policy import PlatformJobPolicy
from shared.worker_compute import (
    APP_BUNDLE_CAPABILITY_NAME,
    APP_PREVIEW_CAPABILITY_NAME,
    APP_COMPILE_CAPABILITY_NAME,
    AUDIT_ARCHIVE_CAPABILITY_NAME,
    AUDIT_EXPORT_CAPABILITY_NAME,
    SOLUTION_ARCHIVE_INSPECT_CAPABILITY_NAME,
)


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
    compute_capability: str | None = None
    additional_compute_capabilities: tuple[str, ...] = ()
    execution_owner: Literal["api", "executor"] = "executor"
    api_control: bool = False

    @property
    def required_compute_capabilities(self) -> frozenset[str]:
        """Every physical stage used by this code-owned job handler."""
        return frozenset(
            (() if self.compute_capability is None else (self.compute_capability,))
            + self.additional_compute_capabilities
        )


def _metadata(
    job_type: str,
    module: str,
    definition_name: str,
    policy: PlatformJobPolicy,
    *,
    payload_version: int = 1,
    encrypt_payload: bool = False,
    display_name: str | None = None,
    description: str | None = None,
    compute_capability: str | None = None,
    additional_compute_capabilities: tuple[str, ...] = (),
    execution_owner: Literal["api", "executor"] = "executor",
    api_control: bool = False,
) -> PlatformJobMetadata:
    return PlatformJobMetadata(
        job_type=job_type,
        payload_version=payload_version,
        policy=policy,
        module=module,
        definition_name=definition_name,
        encrypt_payload=encrypt_payload,
        display_name=display_name,
        description=description,
        compute_capability=compute_capability,
        additional_compute_capabilities=additional_compute_capabilities,
        execution_owner=execution_owner,
        api_control=api_control,
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
                compute_capability=APP_COMPILE_CAPABILITY_NAME,
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
                compute_capability=APP_COMPILE_CAPABILITY_NAME,
                payload_version=3,
                description="Rebuilds the web SDK and republishes affected apps in an isolated pod.",
            ),
            _metadata(
                "application.publish",
                "src.jobs.platform.application_publish",
                "APPLICATION_PUBLISH_DEFINITION",
                PlatformJobPolicy(20 * 60, max_attempts=2, max_concurrency=1),
                compute_capability=APP_BUNDLE_CAPABILITY_NAME,
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
                execution_owner="api",
                api_control=True,
            ),
            _metadata(
                "cloudflare.capacity.apply",
                "src.jobs.platform.cloudflare_capacity",
                "CLOUDFLARE_CAPACITY_APPLY_DEFINITION",
                PlatformJobPolicy(
                    60 * 60, max_attempts=2, max_concurrency=1,
                    min_memory_headroom_mb=128, retry_on_failure=True, allow_running_cancellation=True,
                ),
                execution_owner="api",
                api_control=True,
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
                execution_owner="api",
                api_control=True,
            ),
            _metadata(
                "solution.update_check",
                "src.jobs.platform.system_maintenance",
                "SOLUTION_UPDATE_CHECK_DEFINITION",
                PlatformJobPolicy(30 * 60, max_attempts=2, max_concurrency=1),
                payload_version=2,
                execution_owner="api",
                api_control=True,
            ),
            _metadata(
                "workspace.file_index_reconcile",
                "src.jobs.platform.system_maintenance",
                "FILE_INDEX_RECONCILIATION_DEFINITION",
                PlatformJobPolicy(60 * 60, max_attempts=2, max_concurrency=1),
                execution_owner="api",
                api_control=True,
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
                execution_owner="api",
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
                compute_capability=AUDIT_ARCHIVE_CAPABILITY_NAME,
            ),
            _metadata(
                "audit.query",
                "src.jobs.platform.audit_query",
                "AUDIT_QUERY_DEFINITION",
                PlatformJobPolicy(2 * 60 * 60, allow_running_cancellation=True),
                compute_capability=AUDIT_EXPORT_CAPABILITY_NAME,
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
                execution_owner="api",
            ),
            _metadata(
                "model_catalog.refresh",
                "src.jobs.platform.system_maintenance",
                "MODEL_CATALOG_REFRESH_DEFINITION",
                PlatformJobPolicy(5 * 60, max_attempts=2, max_concurrency=1),
                execution_owner="api",
                api_control=True,
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
                execution_owner="api",
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
                compute_capability=SOLUTION_ARCHIVE_INSPECT_CAPABILITY_NAME,
                description="Prepares backup source on a worker; the API encrypts and stores the backup.",
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
                compute_capability=SOLUTION_ARCHIVE_INSPECT_CAPABILITY_NAME,
                additional_compute_capabilities=(APP_COMPILE_CAPABILITY_NAME,),
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
                compute_capability=SOLUTION_ARCHIVE_INSPECT_CAPABILITY_NAME,
                additional_compute_capabilities=(APP_COMPILE_CAPABILITY_NAME,),
                payload_version=2,
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
                execution_owner="api",
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
                compute_capability=APP_PREVIEW_CAPABILITY_NAME,
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
                payload_version=3,
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
                compute_capability=SOLUTION_ARCHIVE_INSPECT_CAPABILITY_NAME,
            ),
            _metadata(
                "agent.summary_backfill",
                "src.jobs.platform.summary_backfill",
                "SUMMARY_BACKFILL_DEFINITION",
                PlatformJobPolicy(15 * 60, max_attempts=2, min_memory_headroom_mb=128),
                encrypt_payload=True,
                execution_owner="api",
                api_control=True,
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
                execution_owner="api",
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
                execution_owner="api",
            ),
            _metadata(
                "workflow.permissions.analyze",
                "src.jobs.platform.workflow_conversion",
                "ANALYZE_JOB_DEFINITION",
                PlatformJobPolicy(
                    60 * 60,
                    max_attempts=2,
                    max_concurrency=1,
                    min_memory_headroom_mb=384,
                    allow_running_cancellation=True,
                ),
                compute_capability="workflow.graph.analyze.v1",
            ),
            _metadata(
                "workflow.permissions.apply",
                "src.jobs.platform.workflow_conversion",
                "APPLY_JOB_DEFINITION",
                PlatformJobPolicy(
                    60 * 60,
                    max_attempts=2,
                    max_concurrency=1,
                    min_memory_headroom_mb=384,
                    allow_running_cancellation=True,
                ),
            ),
            _metadata(
                "workflow.permissions.cutover",
                "src.jobs.platform.workflow_conversion",
                "CUTOVER_JOB_DEFINITION",
                PlatformJobPolicy(
                    15 * 60,
                    max_attempts=2,
                    max_concurrency=1,
                    min_memory_headroom_mb=384,
                    allow_running_cancellation=True,
                ),
                execution_owner="api",
            ),
        )
    }
)


def get_platform_job_metadata(job_type: str) -> PlatformJobMetadata | None:
    return _METADATA.get(job_type)


def list_platform_job_metadata() -> list[PlatformJobMetadata]:
    return list(_METADATA.values())


def api_platform_job_types() -> frozenset[str]:
    """Job types the API control host executes without a physical worker."""
    return frozenset(
        item.job_type
        for item in _METADATA.values()
        if item.execution_owner == "api"
    )


def api_control_platform_job_types() -> frozenset[str]:
    """API jobs that retain one reserved maintenance admission slot."""
    return frozenset(
        item.job_type
        for item in _METADATA.values()
        if item.execution_owner == "api" and item.api_control
    )


def executor_platform_job_types() -> frozenset[str]:
    """Job types admitted to physical worker executors."""
    return frozenset(
        item.job_type
        for item in _METADATA.values()
        if item.execution_owner == "executor"
    )


def platform_job_types_for_compute(capability: str) -> frozenset[str]:
    return frozenset(item.job_type for item in _METADATA.values() if item.compute_capability == capability)
