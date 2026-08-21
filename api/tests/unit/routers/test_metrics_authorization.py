"""Authorization boundaries for metrics administration routes."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.models.contracts.roi import ROISettingsRequest
from src.routers import ai_pricing, metrics, roi_reports, roi_settings, usage_reports
from src.services.authorization import AuthorizationBoundary, AuthorizationContext


def _authorization(
    *,
    capabilities: set[str],
    boundary: AuthorizationBoundary | None = None,
    email: str = "metrics-operator@example.com",
) -> AuthorizationContext:
    principal = UserPrincipal(
        user_id=uuid4(),
        email=email,
        name="Metrics Operator",
        organization_id=None,
    )
    return AuthorizationContext(
        requester=principal,
        effective_actor=principal,
        selected_boundary=boundary or AuthorizationBoundary.platform(),
        effective_capabilities=frozenset(capabilities),
        grant_sources=(),
    )


@pytest.mark.parametrize(
    "check",
    [
        lambda authorization: metrics._require_metrics_platform(authorization),
        lambda authorization: roi_reports._require_roi_report(authorization),
        lambda authorization: roi_settings._require_roi_settings(authorization),
        lambda authorization: ai_pricing._require_ai_pricing(authorization),
    ],
)
def test_metrics_helpers_require_explicit_platform_boundary(check) -> None:
    authorization = _authorization(
        capabilities={"metrics.read"},
        boundary=AuthorizationBoundary.organization(uuid4()),
    )

    with pytest.raises(HTTPException) as exc:
        check(authorization)

    assert exc.value.status_code == 409
    assert exc.value.detail == (
        "The selected authorization boundary does not match this resource; "
        "select platform"
    )


def test_usage_report_allows_exact_organization_boundary() -> None:
    organization_id = uuid4()
    authorization = _authorization(
        capabilities={"metrics.read"},
        boundary=AuthorizationBoundary.organization(organization_id),
    )

    usage_reports._require_usage_report(authorization)

    assert (
        usage_reports._usage_report_filter_org_id(authorization, None)
        == str(organization_id)
    )


def test_usage_report_rejects_cross_org_query_in_exact_boundary() -> None:
    selected_org_id = uuid4()
    requested_org_id = uuid4()
    authorization = _authorization(
        capabilities={"metrics.read"},
        boundary=AuthorizationBoundary.organization(selected_org_id),
    )

    with pytest.raises(HTTPException) as exc:
        usage_reports._usage_report_filter_org_id(
            authorization,
            str(requested_org_id),
        )

    assert exc.value.status_code == 409
    assert f"select organization:{requested_org_id}" in exc.value.detail


def test_usage_report_rejects_managed_organizations_boundary() -> None:
    authorization = _authorization(
        capabilities={"metrics.read"},
        boundary=AuthorizationBoundary.managed_organizations(),
    )

    with pytest.raises(HTTPException) as exc:
        usage_reports._usage_report_filter_org_id(authorization, None)

    assert exc.value.status_code == 409
    assert exc.value.detail == "Select Global or one exact organization to view usage"


def test_metrics_write_helpers_require_readwrite_capability() -> None:
    authorization = _authorization(capabilities={"metrics.read"})

    with pytest.raises(HTTPException) as exc:
        roi_settings._require_roi_settings(authorization, "metrics.readwrite")

    assert exc.value.status_code == 403
    assert exc.value.detail == "Missing required capability: metrics.readwrite"


@pytest.mark.asyncio
async def test_update_roi_settings_uses_effective_actor_and_audits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updated_by_values: list[str] = []
    audit_events: list[tuple[str, dict[str, object]]] = []
    commits = 0

    class _DB:
        async def commit(self) -> None:
            nonlocal commits
            commits += 1

    class _Settings:
        time_saved_unit = "hours"
        value_unit = "USD"

    class _Service:
        def __init__(self, db):  # noqa: ANN001
            self.db = db

        async def save_settings(
            self,
            *,
            time_saved_unit: str,
            value_unit: str,
            updated_by: str,
        ) -> _Settings:
            assert time_saved_unit == "hours"
            assert value_unit == "USD"
            updated_by_values.append(updated_by)
            return _Settings()

    async def _emit_audit(db, action, **kwargs):  # noqa: ANN001, ANN003, ANN201
        audit_events.append((action, kwargs))

    monkeypatch.setattr(roi_settings, "ROISettingsService", _Service)
    monkeypatch.setattr(roi_settings, "emit_audit", _emit_audit)

    result = await roi_settings.update_roi_settings(
        ROISettingsRequest(time_saved_unit="hours", value_unit="USD"),
        _DB(),
        _authorization(
            capabilities={"metrics.readwrite"},
            email="operator@example.com",
        ),
    )

    assert result.time_saved_unit == "hours"
    assert result.value_unit == "USD"
    assert updated_by_values == ["operator@example.com"]
    assert commits == 1
    assert audit_events == [
        (
            "roi_settings.update",
            {
                "resource_type": "roi_settings",
                "details": {
                    "time_saved_unit": "hours",
                    "value_unit": "USD",
                },
            },
        )
    ]
