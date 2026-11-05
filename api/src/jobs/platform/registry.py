"""Lazy registry for scheduler-owned platform-job handlers."""

from __future__ import annotations

from importlib import import_module

from src.jobs.platform.base import PlatformJobDefinition
from src.jobs.platform.catalog import (
    PlatformJobMetadata,
    get_platform_job_metadata,
    list_platform_job_metadata,
)


_DEFINITIONS: dict[str, PlatformJobDefinition] = {}


def get_platform_job_definition(job_type: str) -> PlatformJobDefinition | None:
    """Load only the requested handler module and materialize its definition."""
    cached = _DEFINITIONS.get(job_type)
    if cached is not None:
        return cached
    metadata = get_platform_job_metadata(job_type)
    if metadata is None:
        return None
    module = import_module(metadata.module)
    definition = getattr(module, metadata.definition_name)
    if not isinstance(definition, PlatformJobDefinition):
        raise TypeError(
            f"{metadata.module}.{metadata.definition_name} is not a platform-job definition"
        )
    _DEFINITIONS[job_type] = definition
    return definition


def list_platform_job_definitions() -> list[PlatformJobDefinition]:
    return [
        definition
        for metadata in list_platform_job_metadata()
        if (definition := get_platform_job_definition(metadata.job_type)) is not None
    ]


__all__ = [
    "PlatformJobMetadata",
    "get_platform_job_definition",
    "get_platform_job_metadata",
    "list_platform_job_definitions",
    "list_platform_job_metadata",
]
