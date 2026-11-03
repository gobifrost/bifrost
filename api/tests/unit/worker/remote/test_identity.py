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


def test_external_launcher_uses_its_reserved_incarnation_and_resource() -> None:
    identity = LauncherIdentity.from_environment({
        "HOSTNAME": "external-1", "BIFROST_WORKER_FORM": "external",
        "BIFROST_WORKER_INCARNATION_ID": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "BIFROST_WORKER_RESOURCE_UID": "resource-123",
    })
    assert identity.form == "external"
    assert identity.incarnation_id == UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    assert identity.resource_uid == "resource-123"
    assert identity.job_uid is None and identity.pod_uid is None


def test_external_launcher_requires_its_prepared_resource_binding() -> None:
    with pytest.raises(IdentityError, match="resource UID"):
        LauncherIdentity.from_environment({
            "HOSTNAME": "external-1", "BIFROST_WORKER_FORM": "external",
            "BIFROST_WORKER_INCARNATION_ID": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        })


def test_static_launcher_cannot_impersonate_a_managed_incarnation() -> None:
    with pytest.raises(IdentityError, match="managed incarnation"):
        LauncherIdentity.from_environment({
            "HOSTNAME": "static-1",
            "BIFROST_WORKER_INCARNATION_ID": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        })
