"""Stable launcher and Kubernetes identity contracts."""

from __future__ import annotations

from uuid import UUID

import pytest

from src.worker.remote.identity import IdentityError, LauncherIdentity, load_launcher_identity


def test_job_launcher_identity_requires_both_kubernetes_uids() -> None:
    with pytest.raises(IdentityError, match="Pod UID"):
        LauncherIdentity.from_environment(
            {
                "BIFROST_WORKER_INCARNATION_ID": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "BIFROST_KUBERNETES_JOB_UID": "job",
            }
        )
    with pytest.raises(IdentityError, match="Job UID"):
        LauncherIdentity.from_environment(
            {
                "BIFROST_WORKER_INCARNATION_ID": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "BIFROST_KUBERNETES_POD_UID": "pod",
            }
        )


def test_launcher_identity_is_immutable_for_the_process(monkeypatch) -> None:
    monkeypatch.setenv("BIFROST_WORKER_INCARNATION_ID", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    monkeypatch.setenv("BIFROST_KUBERNETES_JOB_UID", "job")
    monkeypatch.setenv("BIFROST_KUBERNETES_POD_UID", "pod")

    identity = load_launcher_identity()

    assert identity.incarnation_id == UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    monkeypatch.setenv("BIFROST_KUBERNETES_POD_UID", "replacement-pod")
    assert load_launcher_identity() == identity


def test_static_launcher_generates_process_identity_without_kubernetes_env() -> None:
    identity = LauncherIdentity.from_environment({"HOSTNAME": "scheduler-0"})

    assert identity.form == "static"
    assert identity.job_uid is None
    assert identity.pod_uid is None
