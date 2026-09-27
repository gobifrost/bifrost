"""Product opt-in state for Kubernetes remote execution.

Bifrost owns job classification (execution_class in code); operators own the
deployment ceiling (backend flag + job-type allowlist in config); product
users own this per-type opt-in through Settings. A job launches a pod only
when all three agree.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.jobs.platform.base import PlatformJobDefinition
from src.jobs.platform.registry import list_platform_job_definitions
from src.models.contracts.kubernetes import (
    KubernetesExecutionJobType,
    KubernetesExecutionSettings,
    KubernetesStatus,
)
from src.models.orm.config import SystemConfig

logger = logging.getLogger(__name__)

KUBERNETES_CONFIG_CATEGORY = "kubernetes"
KUBERNETES_CONFIG_KEY = "remote_execution"
KUBERNETES_NOTICE_KEY = "detection_notice"

#: Job types remotely enabled before anyone touches Settings. These are the
#: measured high-memory compilers; anything classified later defaults off
#: until explicitly enabled here.
DEFAULT_REMOTE_JOB_TYPES = frozenset(
    {"application.deploy", "application.sdk_update"}
)


def is_kubernetes_configured(settings: Any) -> bool:
    """Whether the deployment configured the remote-build backend.

    This is the gate for the Settings UI: the tab appears only when the
    operator opted the deployment in with a complete backend setup.
    """
    return (
        getattr(settings, "platform_build_backend", "local") == "kubernetes"
        and bool(getattr(settings, "kubernetes_build_namespace", None))
        and bool(getattr(settings, "kubernetes_build_image", None))
        and bool(getattr(settings, "kubernetes_build_configmap", None))
        and bool(getattr(settings, "kubernetes_build_secret", None))
        and bool(getattr(settings, "kubernetes_build_service_account", None))
    )


class KubernetesExecutionService:
    def __init__(self, db: AsyncSession) -> None:
        self.session = db

    @staticmethod
    def _require_build_class(job_type: str) -> PlatformJobDefinition:
        from src.jobs.platform.registry import get_platform_job_definition

        definition = get_platform_job_definition(job_type)
        if (
            definition is None
            or definition.policy.execution_class != "build"
        ):
            raise KeyError(f"Not a remotely-eligible job type: {job_type}")
        return definition
    async def _stored_types(self) -> frozenset[str] | None:
        config = await self._stored_config()
        if config is None or not config.value_json:
            return None
        stored = config.value_json.get("enabled_job_types")
        if not isinstance(stored, list):
            return None
        return frozenset(
            job_type for job_type in stored if isinstance(job_type, str)
        )

    async def _stored_config(self) -> SystemConfig | None:
        result = await self.session.execute(
            select(SystemConfig).where(
                SystemConfig.category == KUBERNETES_CONFIG_CATEGORY,
                SystemConfig.key == KUBERNETES_CONFIG_KEY,
                SystemConfig.organization_id.is_(None),
            )
        )
        return result.scalars().first()

    async def _save_state(
        self,
        *,
        enabled: frozenset[str],
        concurrency: dict[str, int],
        updated_by: str,
    ) -> None:
        now = datetime.now(timezone.utc)
        payload = {
            "enabled_job_types": sorted(enabled),
            "max_concurrency": dict(sorted(concurrency.items())),
        }
        config = await self._stored_config()
        if config is None:
            self.session.add(
                SystemConfig(
                    id=uuid4(),
                    category=KUBERNETES_CONFIG_CATEGORY,
                    key=KUBERNETES_CONFIG_KEY,
                    value_json=payload,
                    organization_id=None,
                    created_by=updated_by,
                    updated_by=updated_by,
                )
            )
        else:
            config.value_json = payload
            config.updated_at = now
            config.updated_by = updated_by
        await self.session.flush()

    async def _stored_concurrency(self) -> dict[str, int]:
        config = await self._stored_config()
        if config is None or not config.value_json:
            return {}
        stored = config.value_json.get("max_concurrency")
        if not isinstance(stored, dict):
            return {}
        return {
            job_type: value
            for job_type, value in stored.items()
            if isinstance(job_type, str)
            and isinstance(value, int)
            and 1 <= value <= 32
        }

    async def enabled_job_types(self) -> frozenset[str]:
        stored = await self._stored_types()
        return stored if stored is not None else DEFAULT_REMOTE_JOB_TYPES

    async def is_remote_enabled(self, job_type: str) -> bool:
        return job_type in await self.enabled_job_types()

    async def set_job_type_enabled(
        self, job_type: str, enabled: bool, *, updated_by: str
    ) -> frozenset[str]:
        """Toggle one build-class job type. Returns the resulting set."""
        self._require_build_class(job_type)
        current = set(await self.enabled_job_types())
        if enabled:
            current.add(job_type)
        else:
            current.discard(job_type)
        await self._save_state(
            enabled=frozenset(current),
            concurrency=await self._stored_concurrency(),
            updated_by=updated_by,
        )
        return frozenset(current)

    async def set_job_type_concurrency(
        self, job_type: str, value: int | None, *, updated_by: str
    ) -> dict[str, int]:
        """Override one build-class job type's concurrency (None clears it)."""
        self._require_build_class(job_type)
        if value is not None and not 1 <= value <= 32:
            raise ValueError("max_concurrency must be between 1 and 32")
        current = await self._stored_concurrency()
        if value is None:
            current.pop(job_type, None)
        else:
            current[job_type] = value
        await self._save_state(
            enabled=await self.enabled_job_types(),
            concurrency=current,
            updated_by=updated_by,
        )
        return current

    async def effective_max_concurrency(
        self, definition: PlatformJobDefinition
    ) -> int | None:
        """UI override wins when set, otherwise the code policy default."""
        overrides = await self._stored_concurrency()
        if definition.job_type in overrides:
            return overrides[definition.job_type]
        return definition.policy.max_concurrency

    async def list_job_types(self, settings: Any) -> KubernetesExecutionSettings:
        """All build-class job types with product + deployment state."""
        from src.services.platform_jobs import kubernetes_remote_job_types

        enabled = await self.enabled_job_types()
        allowed = kubernetes_remote_job_types(settings)
        overrides = await self._stored_concurrency()
        items = []
        for definition in sorted(
            (
                definition
                for definition in list_platform_job_definitions()
                if definition.policy.execution_class == "build"
            ),
            key=lambda definition: definition.job_type,
        ):
            items.append(
                KubernetesExecutionJobType(
                    job_type=definition.job_type,
                    title=definition.display_name or definition.job_type,
                    description=definition.description or "",
                    enabled=definition.job_type in enabled,
                    default_enabled=(
                        definition.job_type in DEFAULT_REMOTE_JOB_TYPES
                    ),
                    allowed_by_deployment=definition.job_type in allowed,
                    max_concurrency=overrides.get(definition.job_type),
                    default_max_concurrency=(
                        definition.policy.max_concurrency
                    ),
                )
            )
        return KubernetesExecutionSettings(job_types=items)

    @staticmethod
    async def status(settings: Any) -> KubernetesStatus:
        return KubernetesStatus(
            configured=is_kubernetes_configured(settings),
            backend=str(
                getattr(settings, "platform_build_backend", "local")
            ),
        )


async def decide_execution_backend(
    db: AsyncSession,
    settings: Any,
    definition: PlatformJobDefinition,
) -> str:
    """Decide local vs kubernetes placement for one enqueue.
    Three gates must agree: Bifrost classification (build class), operator
    ceiling (backend flag + deployment allowlist), and product opt-in
    (Settings UI, defaulting to the measured high-memory jobs). Imported
    lazily by callers: this module pulls the job registry, which pulls
    platform_jobs at load time.
    """
    from src.services.platform_jobs import kubernetes_remote_job_types

    if definition.policy.execution_class != "build":
        return "local"
    if getattr(settings, "platform_build_backend", "local") != "kubernetes":
        return "local"
    if definition.job_type not in kubernetes_remote_job_types(settings):
        return "local"
    try:
        service = KubernetesExecutionService(db)
        if await service.is_remote_enabled(definition.job_type):
            return "kubernetes"
        return "local"
    except Exception:
        # Never fail an enqueue on a settings-table outage: fall back to
        # the measured defaults so build work keeps its prior placement.
        logger.warning(
            "Falling back to default remote-execution opt-in for %s",
            definition.job_type,
            exc_info=True,
        )
        return (
            "kubernetes"
            if definition.job_type in DEFAULT_REMOTE_JOB_TYPES
            else "local"
        )


def _detection_snapshot(settings: Any) -> dict[str, Any]:
    return {
        "configured": is_kubernetes_configured(settings),
        "backend": str(getattr(settings, "platform_build_backend", "local")),
    }


def _detection_details(configured: bool) -> dict[str, Any]:
    if configured:
        return {
            "title": "Kubernetes execution is on",
            "paragraphs": [
                "App deploys and SDK rebuilds now launch temporary pods with "
                "their own memory instead of competing with the scheduler, "
                "which stays warm for lightweight work.",
                "If the cluster has no room, work visibly waits and then "
                "fails with a capacity error — it never falls back silently "
                "into the scheduler.",
                "You control which job types go remote and how many may "
                "overlap. Nothing else moves.",
            ],
            "primary_label": "Open execution settings",
            "primary_url": "/settings/kubernetes-executions",
        }
    return {
        "title": "Kubernetes execution is off",
        "paragraphs": [
            "The deployment no longer advertises the remote-build backend, "
            "so builds run in the scheduler again.",
            "Your per-type toggles are preserved and take effect again if "
            "the backend is re-enabled.",
        ],
        "primary_label": "Open execution settings",
        "primary_url": "/settings/kubernetes-executions",
    }


async def announce_detection_if_transitioned(db: AsyncSession) -> str | None:
    """Emit one admin notice per configured/unconfigured transition.

    Compares the live deployment snapshot against the last announced one
    (durable in system_configs, row-locked so concurrent replicas agree).
    Fresh installs that never configured the backend stay silent; every
    flip in either direction notifies exactly once — dismissing parks it
    until the next flip. Returns "enabled", "disabled", or None.
    """
    settings = get_settings()
    current = _detection_snapshot(settings)
    row = (
        await db.execute(
            select(SystemConfig)
            .where(
                SystemConfig.category == KUBERNETES_CONFIG_CATEGORY,
                SystemConfig.key == KUBERNETES_NOTICE_KEY,
                SystemConfig.organization_id.is_(None),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    announced = (
        row.value_json if row is not None and row.value_json else None
    )
    if announced == current:
        return None

    now = datetime.now(timezone.utc)
    if row is None:
        db.add(
            SystemConfig(
                id=uuid4(),
                category=KUBERNETES_CONFIG_CATEGORY,
                key=KUBERNETES_NOTICE_KEY,
                value_json=current,
                organization_id=None,
            )
        )
    else:
        row.value_json = current
        row.updated_at = now
    await db.flush()

    if announced is None and not current["configured"]:
        # Never configured: nothing to announce.
        return None

    outcome = "enabled" if current["configured"] else "disabled"
    from src.models.contracts.notifications import (
        NotificationCategory,
        NotificationCreate,
        NotificationStatus,
    )
    from src.services.notification_service import get_notification_service

    service = get_notification_service()
    title = (
        "Kubernetes execution enabled"
        if current["configured"]
        else "Kubernetes execution disabled"
    )
    existing = await service.find_admin_notification_by_title(
        title=title, category=NotificationCategory.SYSTEM
    )
    if existing is None:
        await service.create_notification(
            user_id="system",
            request=NotificationCreate(
                category=NotificationCategory.SYSTEM,
                title=title,
                description=(
                    "Heavy builds now run in Kubernetes pods."
                    if current["configured"]
                    else "Builds run in the scheduler again."
                ),
                metadata={
                    "action_url": "/settings/kubernetes-executions",
                    "details": _detection_details(current["configured"]),
                },
            ),
            for_admins=True,
            initial_status=NotificationStatus.AWAITING_ACTION,
        )
    logger.info("Announced Kubernetes execution transition: %s", outcome)
    return outcome
