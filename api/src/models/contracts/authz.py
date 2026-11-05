"""Authority-version and runtime-credential contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class LegacyRuntimeRun(BaseModel):
    """A live run still holding legacy-format runtime credentials."""

    kind: Literal["workflow_execution", "service_attempt"]
    id: UUID
    name: str = Field(description="The workflow name, or the service attempt's service ID")
    status: str
    organization_id: UUID | None = None
    started_at: datetime | None = None


class LegacyLauncher(BaseModel):
    """A connected remote launcher that receives legacy-format credentials."""

    worker_id: UUID
    hostname: str | None = None
    state: str
    credential_protocol: int = Field(description="The protocol its last hello reported; 1 when it reported none")


class LegacyCredentialStatus(BaseModel):
    """What still depends on legacy runtime credentials.

    The release that removes legacy runtime tokens (token PR1c) refuses to
    install while either list is non-empty.
    """

    live_runs: list[LegacyRuntimeRun]
    launchers: list[LegacyLauncher]
