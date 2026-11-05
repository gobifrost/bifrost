"""Public contracts for Settings -> Kubernetes."""

from datetime import datetime

from pydantic import BaseModel, Field

#: Inclusive bounds of each scaling and isolation setting. The service
#: validates imported values against the same bounds.
POLICY_BOUNDS: dict[str, tuple[int, int]] = {
    "warm_workers": (0, 100),
    "max_workers": (1, 100),
    "worker_memory_mib": (256, 1_048_576),
    "largest_worker_mib": (256, 1_048_576),
    "idle_seconds": (1, 86_400),
    "max_isolated_pods": (1, 100),
}


class KubernetesStatus(BaseModel):
    """Whether this API may manage Kubernetes Jobs (the page gate)."""

    configured: bool


class KubernetesExecutionJobType(BaseModel):
    """One isolatable job type with its per-type state."""

    job_type: str
    title: str
    description: str
    enabled: bool
    default_enabled: bool
    # Operator override for PlatformJobPolicy.max_concurrency. None means
    # the code default applies; shown so the UI can offer reset-to-default.
    max_concurrency: int | None = None
    default_max_concurrency: int | None = None


class KubernetesPolicyValues(BaseModel):
    """Worker scaling and isolated-job settings in effect."""

    warm_workers: int
    max_workers: int
    worker_memory_mib: int
    largest_worker_mib: int
    idle_seconds: int
    isolated_jobs_enabled: bool
    max_isolated_pods: int


class KubernetesPolicyProvenance(BaseModel):
    """A setting whose value was imported from a deployment variable."""

    variable: str
    imported_at: datetime


class KubernetesExecutionSettings(BaseModel):
    """Everything Settings -> Kubernetes shows."""

    policy: KubernetesPolicyValues
    # Field name -> the variable it was imported from, until an admin edits it.
    imported_from_env: dict[str, KubernetesPolicyProvenance]
    job_types: list[KubernetesExecutionJobType]
    # Variables set in this API's environment that nothing reads any more.
    unread_variables: list[str]


class KubernetesExecutionUpdate(BaseModel):
    """Toggle isolation and/or concurrency for one job type.

    max_concurrency is tri-state: omitted leaves the override unchanged,
    null clears it back to the code default, 1-32 sets it.
    """

    job_type: str
    enabled: bool
    max_concurrency: int | None = Field(default=None, ge=1, le=32)


class KubernetesPolicyUpdate(BaseModel):
    """Change any of the scaling and isolation settings; omitted ones stay."""

    warm_workers: int | None = Field(
        default=None, ge=POLICY_BOUNDS["warm_workers"][0], le=POLICY_BOUNDS["warm_workers"][1]
    )
    max_workers: int | None = Field(
        default=None, ge=POLICY_BOUNDS["max_workers"][0], le=POLICY_BOUNDS["max_workers"][1]
    )
    worker_memory_mib: int | None = Field(
        default=None,
        ge=POLICY_BOUNDS["worker_memory_mib"][0],
        le=POLICY_BOUNDS["worker_memory_mib"][1],
    )
    largest_worker_mib: int | None = Field(
        default=None,
        ge=POLICY_BOUNDS["largest_worker_mib"][0],
        le=POLICY_BOUNDS["largest_worker_mib"][1],
    )
    idle_seconds: int | None = Field(
        default=None, ge=POLICY_BOUNDS["idle_seconds"][0], le=POLICY_BOUNDS["idle_seconds"][1]
    )
    isolated_jobs_enabled: bool | None = None
    max_isolated_pods: int | None = Field(
        default=None,
        ge=POLICY_BOUNDS["max_isolated_pods"][0],
        le=POLICY_BOUNDS["max_isolated_pods"][1],
    )
