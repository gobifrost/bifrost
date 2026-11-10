"""Focused contracts for durable Fast Path classification."""

import pytest

from shared.execution_lanes import resolve_execution_lane
from src.services.kubernetes_execution import KubernetesPolicy, validate_policy


@pytest.mark.parametrize(
    ("configured", "auto_enabled", "trusted_app", "expected"),
    [
        ("normal", True, True, "normal"),
        ("fast_path", False, False, "fast_path"),
        ("auto", False, True, "normal"),
        ("auto", True, False, "normal"),
        ("auto", True, True, "fast_path"),
        (None, True, True, "normal"),
    ],
)
def test_resolve_execution_lane_snapshots_only_trusted_app_promotion(
    configured: str | None, auto_enabled: bool, trusted_app: bool, expected: str
) -> None:
    assert resolve_execution_lane(
        configured,
        auto_priority_app_workflows=auto_enabled,
        trusted_app_context=trusted_app,
    ) == expected


def test_policy_preserves_normal_warm_floor_inside_total_cap() -> None:
    validate_policy(
        KubernetesPolicy(warm_workers=2, max_workers=4, reserved_fast_path_workers=2),
        memory_request_mib=256,
    )
    with pytest.raises(ValueError, match="Warm workers plus reserved Fast Path workers"):
        validate_policy(
            KubernetesPolicy(warm_workers=2, max_workers=3, reserved_fast_path_workers=2),
            memory_request_mib=256,
        )


def test_reservation_must_leave_a_normal_worker_slot() -> None:
    with pytest.raises(ValueError, match="leave capacity for Normal"):
        validate_policy(
            KubernetesPolicy(warm_workers=0, max_workers=1, reserved_fast_path_workers=1),
            memory_request_mib=256,
        )


async def test_explicit_fast_path_requires_configured_capacity(db_session) -> None:
    from shared.execution_lanes import FastPathUnavailable, resolve_admitted_execution_lane
    from unittest.mock import AsyncMock, patch

    with patch("shared.execution_lanes.has_static_fast_path_capacity", new=AsyncMock(return_value=False)):
        with pytest.raises(FastPathUnavailable, match="no dedicated workers"):
            await resolve_admitted_execution_lane(db_session, "fast_path")
    with patch("shared.execution_lanes.has_static_fast_path_capacity", new=AsyncMock(return_value=True)):
        assert await resolve_admitted_execution_lane(db_session, "fast_path") == "fast_path"
