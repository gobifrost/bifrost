"""End-to-end coverage for the workflow resource report."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from src.models.enums import ExecutionStatus
from src.models.orm import AIUsage, Execution, Workflow


@pytest.mark.e2e
class TestWorkflowResourceReports:
    def test_requires_platform_admin(self, e2e_client, org1_user) -> None:
        response = e2e_client.get(
            "/api/reports/workflow-resources",
            headers=org1_user.headers,
            params={
                "started_after": "2026-01-01T00:00:00Z",
                "started_before": "2026-01-02T00:00:00Z",
            },
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_reports_each_execution_once_with_aggregated_ai_usage(
        self, e2e_client, platform_admin, db_session, org1, org2
    ) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        workflow_name = "workflow-resource-report-test"
        in_range = now - timedelta(minutes=10)

        cpu_heavy = Execution(
            workflow_name=workflow_name,
            executed_by_name="Resource report test",
            organization_id=UUID(org1["id"]),
            status=ExecutionStatus.SUCCESS,
            started_at=in_range,
            completed_at=in_range + timedelta(seconds=2),
            duration_ms=2000,
            cpu_total_seconds=1.5,
            peak_cpu_cores=0.75,
            peak_process_rss_bytes=123_000_000,
        )
        no_ai = Execution(
            workflow_name=workflow_name,
            executed_by_name="Resource report test",
            organization_id=UUID(org1["id"]),
            status=ExecutionStatus.FAILED,
            started_at=in_range + timedelta(seconds=1),
            completed_at=in_range + timedelta(seconds=2),
            duration_ms=1000,
            cpu_total_seconds=0.25,
            peak_cpu_cores=1.25,
            peak_process_rss_bytes=91_000_000,
        )
        other_org = Execution(
            workflow_name=workflow_name,
            executed_by_name="Resource report test",
            organization_id=UUID(org2["id"]),
            status=ExecutionStatus.SUCCESS,
            started_at=in_range,
            duration_ms=500,
            cpu_total_seconds=0.1,
            peak_process_rss_bytes=50_000_000,
        )
        missing_telemetry = Execution(
            workflow_name=workflow_name,
            executed_by_name="Resource report test",
            organization_id=UUID(org1["id"]),
            status=ExecutionStatus.SUCCESS,
            started_at=in_range + timedelta(seconds=2),
            duration_ms=1500,
            cpu_total_seconds=0.5,
            peak_process_rss_bytes=None,
            peak_cpu_cores=None,
        )
        unrelated = Execution(
            workflow_name="another-workflow",
            executed_by_name="Resource report test",
            organization_id=UUID(org1["id"]),
            status=ExecutionStatus.SUCCESS,
            started_at=in_range,
            duration_ms=100,
            cpu_total_seconds=0.1,
        )
        db_session.add_all([cpu_heavy, no_ai, other_org, missing_telemetry, unrelated])
        await db_session.flush()
        db_session.add_all(
            [
                AIUsage(
                    execution_id=cpu_heavy.id,
                    organization_id=UUID(org1["id"]),
                    provider="openai",
                    model="gpt-test",
                    input_tokens=100,
                    output_tokens=50,
                    cost=Decimal("0.10"),
                ),
                AIUsage(
                    execution_id=cpu_heavy.id,
                    organization_id=UUID(org1["id"]),
                    provider="openai",
                    model="gpt-test",
                    input_tokens=200,
                    output_tokens=75,
                    cost=Decimal("0.20"),
                ),
            ]
        )
        await db_session.commit()

        params = {
            "started_after": (now - timedelta(hours=1)).isoformat(),
            "started_before": (now + timedelta(hours=1)).isoformat(),
            "org_id": org1["id"],
            "workflow": workflow_name,
            "view": "runs",
            "sort": "cpu",
            "page_size": 50,
        }
        response = e2e_client.get(
            "/api/reports/workflow-resources",
            headers=platform_admin.headers,
            params=params,
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["summary"] == {
            "run_count": 3,
            "total_cpu_seconds": 2.25,
            "total_duration_ms": 4500,
            "total_ai_cost": "0.30000000",
            "total_ai_calls": 2,
        }
        assert body["total"] == 3
        # Sorted by CPU descending; missing-telemetry run is last.
        assert [run["execution_id"] for run in body["runs"]] == [
            str(cpu_heavy.id),
            str(missing_telemetry.id),
            str(no_ai.id),
        ]
        assert body["runs"][0]["avg_cpu_cores"] == 0.75
        assert body["runs"][0]["ai_calls"] == 2
        assert body["runs"][0]["ai_tokens"] == 425
        assert body["runs"][0]["peak_process_rss_bytes"] == 123_000_000
        assert body["runs"][1]["avg_cpu_cores"] is not None
        assert body["runs"][1]["peak_cpu_cores"] is None
        assert body["runs"][1]["peak_process_rss_bytes"] is None
        assert body["runs"][2]["ai_calls"] == 0
        assert body["runs"][2]["ai_cost"] == "0"

        # Pagination returns a stable second page.
        page_response = e2e_client.get(
            "/api/reports/workflow-resources",
            headers=platform_admin.headers,
            params={**params, "page": 2, "page_size": 1},
        )
        assert page_response.status_code == 200, page_response.text
        page_body = page_response.json()
        assert page_body["total"] == 3
        assert page_body["page"] == 2
        assert len(page_body["runs"]) == 1
        assert page_body["runs"][0]["execution_id"] == str(missing_telemetry.id)

        workflow_response = e2e_client.get(
            "/api/reports/workflow-resources",
            headers=platform_admin.headers,
            params={**params, "view": "workflows"},
        )
        assert workflow_response.status_code == 200, workflow_response.text
        workflow_body = workflow_response.json()
        assert workflow_body["total"] == 1
        assert workflow_body["workflows"] == [
            {
                "workflow_id": None,
                "workflow_name": workflow_name,
                "run_count": 3,
                "failed_count": 1,
                "total_cpu_seconds": 2.25,
                "total_duration_ms": 4500,
                "max_peak_cpu_cores": 1.25,
                "max_peak_process_rss_bytes": 123_000_000,
                "total_ai_cost": "0.30000000",
            }
        ]

    @pytest.mark.asyncio
    async def test_same_named_workflows_keep_separate_rankings(
        self, e2e_client, platform_admin, db_session, org1, org2
    ) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        name = "resource-shared-name-test"
        first = Workflow(
            name=name,
            function_name="main",
            path="workflows/resource-first.py",
            organization_id=UUID(org1["id"]),
        )
        second = Workflow(
            name=name,
            function_name="main",
            path="workflows/resource-second.py",
            organization_id=UUID(org2["id"]),
        )
        db_session.add_all([first, second])
        await db_session.flush()
        for workflow, org in ((first, org1), (second, org2)):
            db_session.add(
                Execution(
                    workflow_id=workflow.id,
                    workflow_name=name,
                    executed_by_name="Resource report test",
                    organization_id=UUID(org["id"]),
                    status=ExecutionStatus.SUCCESS,
                    started_at=now - timedelta(minutes=5),
                    duration_ms=1000,
                    cpu_total_seconds=0.5,
                )
            )
        await db_session.commit()

        params = {
            "started_after": (now - timedelta(hours=1)).isoformat(),
            "started_before": (now + timedelta(hours=1)).isoformat(),
            "workflow": name,
            "view": "workflows",
        }
        response = e2e_client.get(
            "/api/reports/workflow-resources",
            headers=platform_admin.headers,
            params=params,
        )
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 2
        assert {row["workflow_id"] for row in response.json()["workflows"]} == {
            str(first.id),
            str(second.id),
        }

        selected = e2e_client.get(
            "/api/reports/workflow-resources",
            headers=platform_admin.headers,
            params={**params, "view": "runs", "workflow_id": str(first.id)},
        )
        assert selected.status_code == 200, selected.text
        assert selected.json()["total"] == 1
        assert selected.json()["runs"][0]["workflow_id"] == str(first.id)

    def test_rejects_invalid_time_window(self, e2e_client, platform_admin) -> None:
        response = e2e_client.get(
            "/api/reports/workflow-resources",
            headers=platform_admin.headers,
            params={
                "started_after": "2026-01-02T00:00:00Z",
                "started_before": "2026-01-01T00:00:00Z",
            },
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_status_and_workflow_filters(
        self, e2e_client, platform_admin, db_session, org1
    ) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        in_range = now - timedelta(minutes=5)

        success_run = Execution(
            workflow_name="status-filter-success",
            executed_by_name="Resource report test",
            organization_id=UUID(org1["id"]),
            status=ExecutionStatus.SUCCESS,
            started_at=in_range,
            duration_ms=100,
            cpu_total_seconds=0.1,
        )
        failed_run = Execution(
            workflow_name="status-filter-failed",
            executed_by_name="Resource report test",
            organization_id=UUID(org1["id"]),
            status=ExecutionStatus.FAILED,
            started_at=in_range,
            duration_ms=100,
            cpu_total_seconds=0.1,
        )
        db_session.add_all([success_run, failed_run])
        await db_session.commit()

        params = {
            "started_after": (now - timedelta(hours=1)).isoformat(),
            "started_before": (now + timedelta(hours=1)).isoformat(),
            "status": "Failed",
            "workflow": "status-filter-",
        }
        response = e2e_client.get(
            "/api/reports/workflow-resources",
            headers=platform_admin.headers,
            params=params,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["summary"]["run_count"] == 1
        assert body["runs"][0]["execution_id"] == str(failed_run.id)

        partial_response = e2e_client.get(
            "/api/reports/workflow-resources",
            headers=platform_admin.headers,
            params={
                **params,
                "status": "Success",
                "workflow": "filter-success",
            },
        )
        assert partial_response.status_code == 200, partial_response.text
        partial_body = partial_response.json()
        assert partial_body["summary"]["run_count"] == 1
        assert partial_body["runs"][0]["execution_id"] == str(success_run.id)
