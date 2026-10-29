"""Settings -> Kubernetes: worker scaling and isolated-job policy.

One ``SystemConfig`` row (``kubernetes`` / ``remote_execution``) holds every
setting: the scaling of worker Jobs, whether build-class platform jobs run
isolated in their own pod, which types do, and per-type concurrency. A field
never stored takes its default. Every write merges into the stored payload
under a transaction advisory lock, so editing one field never erases another.

On start, before the Kubernetes loops run, each field never stored takes the
deployment variable it replaces, once (``import_kubernetes_policy_from_env``),
and records where it came from until an admin edits it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.jobs.platform.catalog import (
    PlatformJobMetadata,
    get_platform_job_metadata,
    list_platform_job_metadata,
)
from src.jobs.platform.policy import PlatformJobPolicy
from src.models.contracts.kubernetes import (
    POLICY_BOUNDS,
    KubernetesExecutionJobType,
    KubernetesExecutionSettings,
    KubernetesPolicyProvenance,
    KubernetesPolicyValues,
    KubernetesStatus,
)
from src.models.orm.config import SystemConfig
from src.services.deployment_environment import (
    KUBERNETES_POLICY_VARIABLES,
    set_variables,
    unread_environment_variables,
)

logger = logging.getLogger(__name__)

KUBERNETES_CONFIG_CATEGORY = "kubernetes"
KUBERNETES_CONFIG_KEY = "remote_execution"
IMPORT_LOCK = "bifrost:kubernetes-settings:import"
PROVENANCE_KEY = "imported_from_env"

#: Job types isolated before anyone touches Settings. These are the measured
#: high-memory compilers; anything classified later defaults off until
#: explicitly enabled here.
DEFAULT_REMOTE_JOB_TYPES = frozenset(
    {
        "application.deploy",
        "application.sdk_update",
        "solution.deploy",
        "solution.export",
        "solution.git_sync",
    }
)


@dataclass(frozen=True)
class KubernetesPolicy:
    """The settings in effect: stored values, else defaults."""

    warm_workers: int = 1
    max_workers: int = 6
    worker_memory_mib: int = 2048
    largest_worker_mib: int = 8192
    idle_seconds: int = 300
    isolated_jobs_enabled: bool = False
    max_isolated_pods: int = 2
    enabled_job_types: frozenset[str] = DEFAULT_REMOTE_JOB_TYPES
    max_concurrency: Mapping[str, int] = field(default_factory=dict)


class PlatformJobPolicyView(Protocol):
    job_type: str
    policy: PlatformJobPolicy


#: The scalar settings an admin edits on the page (and the import fills).
POLICY_FIELDS = tuple(
    item.name
    for item in fields(KubernetesPolicy)
    if item.name not in ("enabled_job_types", "max_concurrency")
)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _policy_from(payload: Mapping[str, Any]) -> KubernetesPolicy:
    values: dict[str, Any] = {}
    for name in POLICY_FIELDS:
        value = payload.get(name)
        if name == "isolated_jobs_enabled":
            if isinstance(value, bool):
                values[name] = value
        elif _is_int(value):
            values[name] = value
    types = payload.get("enabled_job_types")
    if isinstance(types, list):
        values["enabled_job_types"] = frozenset(
            job_type for job_type in types if isinstance(job_type, str)
        )
    concurrency = payload.get("max_concurrency")
    if isinstance(concurrency, dict):
        values["max_concurrency"] = {
            job_type: value
            for job_type, value in concurrency.items()
            if isinstance(job_type, str) and _is_int(value) and 1 <= value <= 32
        }
    return KubernetesPolicy(**values)


def _validate_consistent(policy: KubernetesPolicy) -> None:
    for name, (low, high) in POLICY_BOUNDS.items():
        value = getattr(policy, name)
        if not low <= value <= high:
            raise ValueError(f"{name} must be between {low} and {high}")
    if policy.warm_workers > policy.max_workers:
        raise ValueError("Warm workers must not exceed max workers")
    if policy.worker_memory_mib > policy.largest_worker_mib:
        raise ValueError("Worker memory must not exceed the largest worker")


def _memory_request_mib() -> int:
    """The deployment's worker memory request; a worker's limit is never below it."""
    from src.config import get_settings

    return get_settings().pool_worker_memory_request_mib


def validate_policy(policy: KubernetesPolicy, *, memory_request_mib: int) -> None:
    """Raise ``ValueError`` for a value out of range, an inconsistent pair, or
    Worker memory below the deployment's memory request (no worker Job or
    isolated pod could be created)."""
    _validate_consistent(policy)
    if policy.worker_memory_mib < memory_request_mib:
        raise ValueError(
            "Worker memory must be at least the worker memory request "
            f"({memory_request_mib} MiB, BIFROST_POOL_WORKER_MEMORY_REQUEST_MIB)"
        )


async def _stored_config(db: AsyncSession) -> SystemConfig | None:
    result = await db.execute(
        select(SystemConfig).where(
            SystemConfig.category == KUBERNETES_CONFIG_CATEGORY,
            SystemConfig.key == KUBERNETES_CONFIG_KEY,
            SystemConfig.organization_id.is_(None),
        )
    )
    return result.scalars().first()


async def _stored_payload(db: AsyncSession) -> dict[str, Any]:
    config = await _stored_config(db)
    return dict(config.value_json or {}) if config is not None else {}


async def load_kubernetes_policy(db: AsyncSession) -> KubernetesPolicy:
    """The settings in effect. Controllers read this every pass."""
    return _policy_from(await _stored_payload(db))


async def _merge(
    db: AsyncSession,
    mutate: Callable[[dict[str, Any]], bool],
    *,
    updated_by: str,
) -> None:
    """Read, change and write the stored payload under its advisory lock.

    ``mutate`` changes the payload in place and returns whether it changed
    anything; nothing is written when it did not. The lock is held until the
    caller's transaction ends.
    """
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"{KUBERNETES_CONFIG_CATEGORY}/{KUBERNETES_CONFIG_KEY}"},
    )
    config = await _stored_config(db)
    payload = dict(config.value_json or {}) if config is not None else {}
    if not mutate(payload):
        return
    if config is None:
        db.add(
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
        config.updated_at = datetime.now(timezone.utc)
        config.updated_by = updated_by
    await db.flush()


def _edit(payload: dict[str, Any], name: str, value: Any) -> None:
    """An admin edit: store the value and forget where it was imported from."""
    payload[name] = value
    provenance = dict(payload.get(PROVENANCE_KEY) or {})
    if provenance.pop(name, None) is not None:
        payload[PROVENANCE_KEY] = provenance


def _parse_imported(name: str, raw: str) -> Any:
    """The setting's value for a deployment variable, or None when invalid."""
    value = raw.strip()
    if name == "isolated_jobs_enabled":
        return {"kubernetes": True, "local": False}.get(value.lower())
    if name == "enabled_job_types":
        listed = {part.strip() for part in value.split(",") if part.strip()}
        return sorted(DEFAULT_REMOTE_JOB_TYPES & listed) if listed else None
    try:
        number = int(value)
    except ValueError:
        return None
    low, high = POLICY_BOUNDS[name]
    return number if low <= number <= high else None


async def import_kubernetes_policy_from_env(
    db: AsyncSession, environ: Mapping[str, str]
) -> list[str]:
    """Give each setting never stored the deployment variable it replaces.

    Runs under its own blocking advisory lock, so of several replicas
    starting at once one imports and the others find the values stored.
    Records ``imported_from_env`` per field. An invalid value, a Worker memory
    below the deployment's memory request, or a value that conflicts with
    one already stored is skipped with a warning.

    The import keeps what the variables did before: the controller sized
    its largest worker as the larger of the two memory variables, and never
    launched more than max workers. So Worker memory above the largest
    worker raises the largest worker to it, and warm workers above max
    workers are lowered to max, unless that field is already stored. The
    merged result is validated; nothing is stored that a later edit would
    have to undo. Commits; returns the fields imported.
    """
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": IMPORT_LOCK}
    )
    present = set_variables(environ)
    memory_request_mib = _memory_request_mib()
    imported: list[str] = []
    at = datetime.now(timezone.utc).isoformat()

    def skip(variable: str, reason: str) -> None:
        logger.warning("Ignoring %s: %s", variable, reason)

    def mutate(payload: dict[str, Any]) -> bool:
        provenance = dict(payload.get(PROVENANCE_KEY) or {})
        values: dict[str, Any] = {}
        for name, variable in KUBERNETES_POLICY_VARIABLES.items():
            if name in payload or variable not in present:
                continue
            value = _parse_imported(name, present[variable])
            if value is None:
                skip(
                    variable, "not a valid value for this setting; the default applies"
                )
                continue
            if name == "worker_memory_mib" and value < memory_request_mib:
                skip(
                    variable,
                    f"below the worker memory request ({memory_request_mib} MiB); "
                    "the default applies",
                )
                continue
            values[name] = value
            provenance[name] = {"variable": variable, "at": at}

        merged = _policy_from({**payload, **values})
        if merged.worker_memory_mib > merged.largest_worker_mib:
            if "largest_worker_mib" in payload:
                skip(
                    KUBERNETES_POLICY_VARIABLES["worker_memory_mib"],
                    "above the largest worker stored in Settings",
                )
                del values["worker_memory_mib"]
                provenance.pop("worker_memory_mib")
            else:
                values["largest_worker_mib"] = merged.worker_memory_mib
                provenance["largest_worker_mib"] = {
                    "variable": KUBERNETES_POLICY_VARIABLES["worker_memory_mib"],
                    "at": at,
                }
        if merged.warm_workers > merged.max_workers:
            if "warm_workers" in payload:
                skip(
                    KUBERNETES_POLICY_VARIABLES["max_workers"],
                    "below the warm workers stored in Settings",
                )
                del values["max_workers"]
                provenance.pop("max_workers")
            else:
                values["warm_workers"] = merged.max_workers

        if not values:
            return False
        _validate_consistent(_policy_from({**payload, **values}))
        payload.update(values)
        payload[PROVENANCE_KEY] = provenance
        imported.extend(values)
        return True

    await _merge(db, mutate, updated_by="environment import")
    await db.commit()
    if imported:
        logger.info(
            "Imported Kubernetes settings from the environment",
            extra={"fields": imported},
        )
    return imported


class KubernetesExecutionService:
    def __init__(self, db: AsyncSession) -> None:
        self.session = db

    @staticmethod
    def _require_build_class(job_type: str) -> PlatformJobMetadata:
        definition = get_platform_job_metadata(job_type)
        if definition is None or definition.policy.execution_class != "build":
            raise KeyError(f"Not a remotely-eligible job type: {job_type}")
        return definition

    async def enabled_job_types(self) -> frozenset[str]:
        return (await load_kubernetes_policy(self.session)).enabled_job_types

    async def is_remote_enabled(self, job_type: str) -> bool:
        return job_type in await self.enabled_job_types()

    async def set_job_type_enabled(
        self, job_type: str, enabled: bool, *, updated_by: str
    ) -> None:
        """Toggle one build-class job type."""
        self._require_build_class(job_type)

        def mutate(payload: dict[str, Any]) -> bool:
            current = set(_policy_from(payload).enabled_job_types)
            if enabled:
                current.add(job_type)
            else:
                current.discard(job_type)
            _edit(payload, "enabled_job_types", sorted(current))
            return True

        await _merge(self.session, mutate, updated_by=updated_by)

    async def set_job_type_concurrency(
        self, job_type: str, value: int | None, *, updated_by: str
    ) -> None:
        """Override one build-class job type's concurrency (None clears it)."""
        self._require_build_class(job_type)
        if value is not None and not 1 <= value <= 32:
            raise ValueError("max_concurrency must be between 1 and 32")

        def mutate(payload: dict[str, Any]) -> bool:
            current = dict(_policy_from(payload).max_concurrency)
            if value is None:
                current.pop(job_type, None)
            else:
                current[job_type] = value
            payload["max_concurrency"] = dict(sorted(current.items()))
            return True

        await _merge(self.session, mutate, updated_by=updated_by)

    async def update_policy(
        self, changes: Mapping[str, Any], *, updated_by: str
    ) -> None:
        """Change some scaling and isolation settings.

        The resulting settings are validated as a whole; ``ValueError``
        leaves everything as it was.
        """
        unknown = set(changes) - set(POLICY_FIELDS)
        if unknown:
            raise ValueError(f"Unknown settings: {', '.join(sorted(unknown))}")
        missing = sorted(name for name, value in changes.items() if value is None)
        if missing:
            raise ValueError(f"A value is required for: {', '.join(missing)}")

        memory_request_mib = _memory_request_mib()

        def mutate(payload: dict[str, Any]) -> bool:
            for name, value in changes.items():
                _edit(payload, name, value)
            validate_policy(
                _policy_from(payload), memory_request_mib=memory_request_mib
            )
            return bool(changes)

        await _merge(self.session, mutate, updated_by=updated_by)

    async def effective_max_concurrency(
        self, definition: PlatformJobPolicyView
    ) -> int | None:
        """UI override wins when set, otherwise the code policy default."""
        overrides = (await load_kubernetes_policy(self.session)).max_concurrency
        if definition.job_type in overrides:
            return overrides[definition.job_type]
        return definition.policy.max_concurrency

    async def describe(self, environ: Mapping[str, str]) -> KubernetesExecutionSettings:
        """Everything Settings -> Kubernetes shows."""
        from src.services.legacy_broker_drain import legacy_broker_drained

        payload = await _stored_payload(self.session)
        policy = _policy_from(payload)
        provenance: dict[str, KubernetesPolicyProvenance] = {}
        stored_provenance = payload.get(PROVENANCE_KEY)
        if isinstance(stored_provenance, dict):
            for name, entry in stored_provenance.items():
                if isinstance(entry, dict):
                    provenance[name] = KubernetesPolicyProvenance(
                        variable=entry.get("variable"),
                        imported_at=entry.get("at"),
                    )
        items = [
            KubernetesExecutionJobType(
                job_type=definition.job_type,
                title=definition.display_name or definition.job_type,
                description=definition.description or "",
                enabled=definition.job_type in policy.enabled_job_types,
                default_enabled=definition.job_type in DEFAULT_REMOTE_JOB_TYPES,
                max_concurrency=policy.max_concurrency.get(definition.job_type),
                default_max_concurrency=definition.policy.max_concurrency,
            )
            for definition in sorted(
                (
                    definition
                    for definition in list_platform_job_metadata()
                    if definition.policy.execution_class == "build"
                ),
                key=lambda definition: definition.job_type,
            )
        ]
        return KubernetesExecutionSettings(
            policy=KubernetesPolicyValues(
                **{name: getattr(policy, name) for name in POLICY_FIELDS}
            ),
            imported_from_env=provenance,
            job_types=items,
            unread_variables=unread_environment_variables(
                environ, broker_drained=await legacy_broker_drained(self.session)
            ),
        )

    @staticmethod
    async def status() -> KubernetesStatus:
        from src.config import get_settings
        from src.services.kubernetes_control import may_manage_jobs

        return KubernetesStatus(configured=await may_manage_jobs(get_settings()))


async def decide_execution_backend(
    db: AsyncSession, definition: PlatformJobPolicyView
) -> str:
    """Local or isolated placement for one enqueue.

    A build-class job runs isolated when isolated jobs are on and its type
    is enabled in Settings -> Kubernetes.
    """
    if definition.policy.execution_class != "build":
        return "local"
    policy = await load_kubernetes_policy(db)
    if policy.isolated_jobs_enabled and definition.job_type in policy.enabled_job_types:
        return "kubernetes"
    return "local"
