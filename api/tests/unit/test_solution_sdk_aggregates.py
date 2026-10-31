from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.services.application_sdk_status import CurrentApplicationSdkMetadata


@pytest.mark.asyncio
async def test_solution_sdk_list_aggregate_loads_current_metadata_once(monkeypatch):
    from src.routers import solutions as solutions_router

    solution_a = SimpleNamespace(id=uuid4(), status="active")
    solution_b = SimpleNamespace(id=uuid4(), status="active")
    apps = [
        SimpleNamespace(
            id=uuid4(),
            slug="needs-update",
            solution_id=solution_a.id,
            repo_path="apps/needs-update",
            app_model="standalone_v2",
            active_deployment_id=uuid4(),
            sdk_package_version="0.0.1",
            sdk_fingerprint="old",
            sdk_contract_version=1,
            sdk_binding_api_major=None,
            sdk_built_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        SimpleNamespace(
            id=uuid4(),
            slug="current",
            solution_id=solution_b.id,
            repo_path="apps/current",
            app_model="standalone_v2",
            active_deployment_id=uuid4(),
            sdk_package_version="1.0.0",
            sdk_fingerprint="current",
            sdk_contract_version=1,
            sdk_binding_api_major=None,
            sdk_built_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
    ]

    class _Scalars:
        def all(self):
            return apps

    class _Result:
        def scalars(self):
            return _Scalars()

    class _Db:
        async def execute(self, _statement):
            return _Result()

    calls = 0

    async def fake_load_current_sdk_metadata():
        nonlocal calls
        calls += 1
        return CurrentApplicationSdkMetadata(
            package_version="1.0.0",
            fingerprint="current",
            contract_version=1,
        )

    monkeypatch.setattr(
        solutions_router,
        "load_current_sdk_metadata",
        fake_load_current_sdk_metadata,
    )

    statuses = await solutions_router._solution_sdk_statuses_for_rows(
        SimpleNamespace(db=_Db()),
        [solution_a, solution_b],
    )

    assert calls == 1
    assert statuses[solution_a.id].sdk_status == "update_available"
    assert statuses[solution_a.id].actionable_count == 1
    assert statuses[solution_b.id].sdk_status == "current"
    assert statuses[solution_b.id].actionable_count == 0


def test_solution_sdk_aggregate_precedence_treats_update_required_as_actionable(
    monkeypatch,
):
    from src.routers import solutions as solutions_router

    solution_id = uuid4()
    statuses_by_slug = {
        "unknown-app": "unknown",
        "required-app": "update_required",
        "available-app": "update_available",
        "current-app": "current",
    }
    apps = [
        SimpleNamespace(
            id=uuid4(),
            slug=slug,
            solution_id=solution_id,
            repo_path=f"apps/{slug}",
            app_model="standalone_v2",
            active_deployment_id=uuid4(),
            sdk_built_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        for slug in statuses_by_slug
    ]

    monkeypatch.setattr(
        solutions_router,
        "application_sdk_status",
        lambda app, _current_sdk: statuses_by_slug[app.slug],
    )

    status, actionable_apps = solutions_router._solution_sdk_status_from_apps(
        solution_id=solution_id,
        solution_status="active",
        apps=apps,
        current_sdk=CurrentApplicationSdkMetadata(
            package_version="1.0.0",
            fingerprint="current",
            contract_version=1,
        ),
    )

    assert status.sdk_status == "update_required"
    assert status.actionable_count == 3
    assert {app.slug for app in actionable_apps} == {
        "unknown-app",
        "required-app",
        "available-app",
    }
