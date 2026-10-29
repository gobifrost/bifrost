"""Immutable identity loaded once by a physical remote launcher."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping
from uuid import UUID, uuid4


class IdentityError(ValueError):
    """Required physical-launcher identity is absent or malformed."""


@dataclass(frozen=True)
class LauncherIdentity:
    launcher_id: UUID
    incarnation_id: UUID
    job_uid: str | None
    pod_uid: str | None
    hostname: str
    form: str

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> "LauncherIdentity":
        job_uid = environment.get("BIFROST_KUBERNETES_JOB_UID")
        pod_uid = environment.get("BIFROST_KUBERNETES_POD_UID")
        form = "job" if job_uid or pod_uid else "static"
        if form == "job":
            if not job_uid:
                raise IdentityError("remote Job UID is required")
            if not pod_uid:
                raise IdentityError("remote Pod UID is required")
            try:
                incarnation_id = UUID(environment["BIFROST_WORKER_INCARNATION_ID"])
            except (KeyError, ValueError) as exc:
                raise IdentityError("remote worker incarnation UUID is required") from exc
        else:
            incarnation_id = uuid4()
        launcher_id = uuid4()
        hostname = environment.get("HOSTNAME", "")
        if not hostname:
            raise IdentityError("remote hostname is required")
        return cls(launcher_id, incarnation_id, job_uid, pod_uid, hostname, form)


_identity: LauncherIdentity | None = None


def load_launcher_identity() -> LauncherIdentity:
    """Read identity once so reconnects cannot switch the physical worker."""
    global _identity
    if _identity is None:
        _identity = LauncherIdentity.from_environment(os.environ)
    return _identity
