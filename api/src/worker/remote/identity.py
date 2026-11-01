"""Immutable identity loaded once by a physical remote launcher."""

from __future__ import annotations

import os
import re
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
    resource_uid: str | None = None

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> "LauncherIdentity":
        job_uid = environment.get("BIFROST_KUBERNETES_JOB_UID")
        pod_uid = environment.get("BIFROST_KUBERNETES_POD_UID")
        form = environment.get("BIFROST_WORKER_FORM") or ("job" if job_uid or pod_uid else "static")
        if form not in ("job", "static", "external"):
            raise IdentityError("unsupported physical worker form")
        if form != "job" and (job_uid is not None or pod_uid is not None):
            raise IdentityError("non-Kubernetes launcher cannot claim Kubernetes identities")
        resource_uid = environment.get("BIFROST_WORKER_RESOURCE_UID")
        if form == "job":
            if not job_uid:
                raise IdentityError("remote Job UID is required")
            if not pod_uid:
                raise IdentityError("remote Pod UID is required")
        if form in ("job", "external"):
            try:
                incarnation_id = UUID(environment["BIFROST_WORKER_INCARNATION_ID"])
            except (KeyError, ValueError) as exc:
                raise IdentityError("remote worker incarnation UUID is required") from exc
        else:
            if "BIFROST_WORKER_INCARNATION_ID" in environment:
                raise IdentityError("static launchers cannot select a managed incarnation")
            incarnation_id = uuid4()
        if form == "external":
            if resource_uid is None or re.fullmatch(r"[a-zA-Z0-9._:-]{1,255}", resource_uid) is None:
                raise IdentityError("external launcher requires its capacity resource UID")
        elif resource_uid is not None:
            raise IdentityError("only external launchers may select a capacity resource")
        launcher_id = uuid4()
        hostname = environment.get("HOSTNAME", "")
        if not hostname:
            raise IdentityError("remote hostname is required")
        return cls(launcher_id, incarnation_id, job_uid, pod_uid, hostname, form, resource_uid)


_identity: LauncherIdentity | None = None


def load_launcher_identity() -> LauncherIdentity:
    """Read identity once so reconnects cannot switch the physical worker."""
    global _identity
    if _identity is None:
        _identity = LauncherIdentity.from_environment(os.environ)
    return _identity
