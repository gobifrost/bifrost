"""Live-stack proof for the scalable scheduler diagnostics surface."""

import time

import pytest


@pytest.mark.e2e
class TestSchedulerDiagnostics:
    def test_admin_sees_every_schedule_and_durable_maintenance_handoffs(
        self,
        e2e_client,
        platform_admin,
    ):
        deadline = time.monotonic() + 20
        body = {}
        while time.monotonic() < deadline:
            response = e2e_client.get(
                "/api/platform/scheduler",
                headers=platform_admin.headers,
            )
            assert response.status_code == 200, response.text
            body = response.json()
            tasks = {task["task_id"]: task for task in body["tasks"]}
            if (
                body["leader"]["healthy"]
                and body["capacity"]["replicas_online"] >= 1
                and all(
                    tasks[task_id]["last_run"] is not None
                    for task_id in (
                        "oauth_token_refresh",
                        "webhook_renewal",
                        "solution_update_check",
                        "file_index_reconciliation",
                    )
                )
            ):
                break
            time.sleep(0.25)

        assert body["leader"]["healthy"]
        assert body["capacity"]["slots_total"] >= 1
        tasks = {task["task_id"]: task for task in body["tasks"]}
        expected = {
            "schedule_processor",
            "deferred_execution_promoter",
            "execution_cleanup",
            "oauth_token_refresh",
            "metrics_refresh",
            "knowledge_storage_refresh",
            "file_index_reconciliation",
            "webhook_renewal",
            "solution_update_check",
            "solution_export_job_cleanup",
            "event_cleanup",
            "stuck_event_cleanup",
            "worker_metrics_sampling",
            "worker_metrics_cleanup",
            "scheduler_diagnostics_cleanup",
            "summary_backfill_reconciliation",
            "artifact_retention_cleanup",
        }
        assert set(tasks) == expected
        for task_id in (
            "oauth_token_refresh",
            "webhook_renewal",
            "solution_update_check",
            "file_index_reconciliation",
        ):
            assert tasks[task_id]["execution_mode"] == "durable_job"
            assert tasks[task_id]["last_run"]["status"] == "enqueued"
        assert tasks["artifact_retention_cleanup"]["execution_mode"] == "durable_job"
        deadline = time.monotonic() + 20
        history = {}
        while time.monotonic() < deadline:
            history_response = e2e_client.get(
                "/api/platform/scheduler/tasks/oauth_token_refresh/runs?limit=5",
                headers=platform_admin.headers,
            )
            assert history_response.status_code == 200, history_response.text
            history = history_response.json()
            if history["runs"] and {
                "oauth_refresh_started",
                "oauth_refresh_completed",
            }.issubset({log["code"] for log in history["runs"][0]["logs"]}):
                break
            time.sleep(0.25)

        assert history["name"] == "Refresh Expiring OAuth Tokens"
        assert history["runs"]
        assert {
            "oauth_refresh_started",
            "oauth_refresh_completed",
        }.issubset({log["code"] for log in history["runs"][0]["logs"]})
        assert history["runs"][0]["logs"]
        log_order = [
            (log["created_at"], log["id"])
            for log in history["runs"][0]["logs"]
        ]
        assert log_order == sorted(log_order)

    def test_org_user_cannot_read_scheduler_diagnostics(self, e2e_client, org1_user):
        response = e2e_client.get(
            "/api/platform/scheduler",
            headers=org1_user.headers,
        )
        assert response.status_code == 403

        history_response = e2e_client.get(
            "/api/platform/scheduler/tasks/oauth_token_refresh/runs",
            headers=org1_user.headers,
        )
        assert history_response.status_code == 403

    def test_unknown_schedule_history_is_not_found(self, e2e_client, platform_admin):
        response = e2e_client.get(
            "/api/platform/scheduler/tasks/not-a-schedule/runs",
            headers=platform_admin.headers,
        )
        assert response.status_code == 404
