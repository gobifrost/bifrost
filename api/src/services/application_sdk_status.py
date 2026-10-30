from __future__ import annotations

import asyncio
from dataclasses import dataclass
from functools import lru_cache
import logging
from typing import Literal, Protocol
from uuid import UUID

from shared.version import get_version
from src.services.sdk_package import (
    sdk_contract_version,
    sdk_fingerprint,
    sdk_package_version,
)

logger = logging.getLogger(__name__)

ApplicationSdkStatus = Literal[
    "not_applicable",
    "unknown",
    "current",
    "update_available",
    "update_required",
]


@dataclass(frozen=True)
class CurrentApplicationSdkMetadata:
    package_version: str
    fingerprint: str | None
    contract_version: int | None


class ApplicationSdkStatusInput(Protocol):
    app_model: str
    solution_id: UUID | None
    repo_path: str | None
    active_deployment_id: UUID | None
    sdk_package_version: str | None
    sdk_fingerprint: str | None
    sdk_contract_version: int | None
    sdk_built_at: object | None


@lru_cache(maxsize=1)
def current_sdk_metadata() -> CurrentApplicationSdkMetadata:
    version = get_version()
    return CurrentApplicationSdkMetadata(
        package_version=sdk_package_version(version),
        fingerprint=sdk_fingerprint(version),
        contract_version=sdk_contract_version(),
    )


async def load_current_sdk_metadata() -> CurrentApplicationSdkMetadata:
    try:
        return await asyncio.to_thread(current_sdk_metadata)
    except Exception:  # noqa: BLE001 - SDK build/toolchain failure should degrade app status, not fail app metadata routes
        logger.exception("failed to compute current application SDK metadata")
        return CurrentApplicationSdkMetadata(
            package_version=sdk_package_version(get_version()),
            fingerprint=None,
            contract_version=None,
        )


def application_sdk_status(
    application: ApplicationSdkStatusInput,
    current: CurrentApplicationSdkMetadata,
) -> ApplicationSdkStatus:
    if application.app_model == "inline_v1":
        return "not_applicable"
    if (
        not application.sdk_package_version
        or not application.sdk_fingerprint
        or application.sdk_contract_version is None
        or application.sdk_built_at is None
        or not current.fingerprint
        or current.contract_version is None
    ):
        return "unknown"
    if application.sdk_contract_version != current.contract_version:
        return "update_required"
    if application.sdk_fingerprint == current.fingerprint:
        return "current"
    return "update_available"


def sdk_source_available(application: ApplicationSdkStatusInput) -> bool:
    if application.app_model == "inline_v1":
        return False
    if application.solution_id is not None:
        return application.repo_path is not None
    return (
        application.active_deployment_id is not None
        and application.sdk_built_at is not None
    )
