"""Variables this release no longer reads, renamed ones, and their aliases."""

from __future__ import annotations

import logging

import pytest

from src.config import Settings
from src.services.deployment_environment import (
    renamed_environment_variables,
    unread_environment_variables,
    warn_about_environment,
)


def test_unread_lists_the_imported_removed_and_retired_variables_that_are_set() -> None:
    environ = {
        "BIFROST_POOL_MAX_WORKERS": "6",
        "bifrost_platform_build_backend": "kubernetes",
        "BIFROST_KUBERNETES_BUILD_MEMORY_LIMIT_MIB": "2048",
        "BIFROST_MAX_CONCURRENCY": "10",
        "BIFROST_POOL_WORKER_MEMORY_REQUEST_MIB": "512",  # still read
        "BIFROST_KUBERNETES_BUILD_IMAGE": "registry/api:1",  # renamed, still read
    }

    assert unread_environment_variables(environ, broker_drained=False) == [
        "BIFROST_KUBERNETES_BUILD_MEMORY_LIMIT_MIB",
        "BIFROST_MAX_CONCURRENCY",
        "BIFROST_PLATFORM_BUILD_BACKEND",
        "BIFROST_POOL_MAX_WORKERS",
    ]


def test_empty_values_count_as_unset() -> None:
    environ = {"BIFROST_KUBERNETES_BUILD_JOB_TYPES": "", "BIFROST_MAX_WORKERS": "  "}
    assert unread_environment_variables(environ, broker_drained=True) == []
    assert renamed_environment_variables({"BIFROST_KUBERNETES_BUILD_NAMESPACE": ""}) == []


def test_the_broker_url_is_unread_only_once_the_drain_is_recorded() -> None:
    environ = {"BIFROST_RABBITMQ_URL": "amqp://broker/"}
    assert unread_environment_variables(environ, broker_drained=False) == []
    assert unread_environment_variables(environ, broker_drained=True) == [
        "BIFROST_RABBITMQ_URL"
    ]


def test_renamed_lists_old_names_that_supply_the_value() -> None:
    environ = {
        "BIFROST_KUBERNETES_BUILD_IMAGE": "registry/api:1",
        "BIFROST_KUBERNETES_BUILD_NAMESPACE": "bifrost",
        "BIFROST_KUBERNETES_JOB_NAMESPACE": "bifrost",  # the new name wins
    }
    assert renamed_environment_variables(environ) == [
        ("BIFROST_KUBERNETES_BUILD_IMAGE", "BIFROST_KUBERNETES_JOB_IMAGE")
    ]


def test_startup_warns_once_per_non_empty_list(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="src.services.deployment_environment"):
        warn_about_environment({}, broker_drained=True)
        assert caplog.records == []

        warn_about_environment(
            {
                "BIFROST_POOL_MIN_WORKERS": "1",
                "BIFROST_MAX_WORKERS": "4",
                "BIFROST_KUBERNETES_BUILD_SECRET": "bifrost-secrets",
            },
            broker_drained=False,
        )

    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 2
    assert messages[0].endswith("BIFROST_MAX_WORKERS, BIFROST_POOL_MIN_WORKERS")
    assert messages[1].endswith(
        "BIFROST_KUBERNETES_BUILD_SECRET -> BIFROST_KUBERNETES_JOB_SECRET"
    )


@pytest.mark.parametrize(
    ("name", "suffix", "value", "expected"),
    [
        ("kubernetes_job_namespace", "NAMESPACE", "bifrost", "bifrost"),
        ("kubernetes_job_image", "IMAGE", "registry/api:1", "registry/api:1"),
        ("kubernetes_job_configmap", "CONFIGMAP", "bifrost-config", "bifrost-config"),
        ("kubernetes_job_secret", "SECRET", "bifrost-secrets", "bifrost-secrets"),
        ("kubernetes_job_service_account", "SERVICE_ACCOUNT", "runner", "runner"),
        ("kubernetes_job_cpu_request", "CPU_REQUEST", "500m", "500m"),
        ("kubernetes_job_cpu_limit", "CPU_LIMIT", "3", "3"),
        ("kubernetes_job_pending_timeout_seconds", "PENDING_TIMEOUT_SECONDS", "120", 120),
    ],
)
def test_renamed_settings_read_the_new_name_then_the_old(
    monkeypatch: pytest.MonkeyPatch, name: str, suffix: str, value: str, expected: object
) -> None:
    monkeypatch.delenv(f"BIFROST_KUBERNETES_JOB_{suffix}", raising=False)
    monkeypatch.setenv(f"BIFROST_KUBERNETES_BUILD_{suffix}", value)
    assert getattr(Settings(), name) == expected

    monkeypatch.setenv(f"BIFROST_KUBERNETES_JOB_{suffix}", value)
    monkeypatch.setenv(f"BIFROST_KUBERNETES_BUILD_{suffix}", "7" if suffix.endswith("SECONDS") else "old")
    assert getattr(Settings(), name) == expected
