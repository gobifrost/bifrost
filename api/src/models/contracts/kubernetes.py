"""Public contracts for Kubernetes elastic execution settings."""

from pydantic import BaseModel, Field


class KubernetesStatus(BaseModel):
    """Whether this deployment configured remote build execution."""

    configured: bool
    backend: str


class KubernetesExecutionJobType(BaseModel):
    """One remotely-eligible job type with its product opt-in state."""

    job_type: str
    title: str
    description: str
    enabled: bool
    default_enabled: bool
    allowed_by_deployment: bool
    # Operator override for PlatformJobPolicy.max_concurrency. None means
    # the code default applies; shown so the UI can offer reset-to-default.
    max_concurrency: int | None = None
    default_max_concurrency: int | None = None


class KubernetesExecutionSettings(BaseModel):
    """Product opt-in state for remote execution, per job type."""

    job_types: list[KubernetesExecutionJobType]


class KubernetesExecutionUpdate(BaseModel):
    """Toggle remote execution and/or concurrency for one job type.

    max_concurrency is tri-state: omitted leaves the override unchanged,
    null clears it back to the code default, 1-32 sets it.
    """

    job_type: str
    enabled: bool
    max_concurrency: int | None = Field(default=None, ge=1, le=32)
