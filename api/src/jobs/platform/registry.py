"""Explicit registry for scheduler-owned platform-job handlers."""

from src.jobs.platform.application_publish import (
    APPLICATION_PUBLISH_DEFINITION,
)
from src.jobs.platform.base import PlatformJobDefinition

_DEFINITIONS = {
    APPLICATION_PUBLISH_DEFINITION.job_type: APPLICATION_PUBLISH_DEFINITION,
}


def get_platform_job_definition(job_type: str) -> PlatformJobDefinition | None:
    return _DEFINITIONS.get(job_type)
