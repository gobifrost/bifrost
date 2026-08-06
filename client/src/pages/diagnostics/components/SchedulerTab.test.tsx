import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/services/schedulerDiagnostics", async () => {
	const actual = await vi.importActual<typeof import("@/services/schedulerDiagnostics")>("@/services/schedulerDiagnostics");
	return {
		...actual,
		getSchedulerDiagnostics: vi.fn().mockResolvedValue({
			generated_at: "2026-08-05T22:00:00Z",
			leader: { owner_id: "scheduler-a", lease_expires_at: "2026-08-05T22:01:00Z", healthy: true },
			capacity: { replicas_online: 2, slots_total: 2, slots_running: 2, jobs_queued: 3, jobs_waiting_for_memory: 1, oldest_queued_seconds: 180, max_memory_utilization_percent: 92 },
			replicas: [{ id: "scheduler-a", hostname: "scheduler-1", pid: 1, job_slots: 2, is_leader: true, online: true, started_at: "2026-08-05T21:00:00Z", last_heartbeat_at: "2026-08-05T22:00:00Z", memory_current_bytes: 900, memory_limit_bytes: 1000, active_platform_job_ids: [], active_platform_jobs: 0 }],
			tasks: [{ task_id: "oauth_token_refresh", name: "Refresh expiring OAuth tokens", schedule: "Every 15 minutes", execution_mode: "durable_job", enabled: true, next_run_at: "2026-08-05T22:15:00Z", last_run: { id: "00000000-0000-0000-0000-000000000001", status: "enqueued", leader_owner_id: "scheduler-a", started_at: "2026-08-05T22:00:00Z", completed_at: "2026-08-05T22:00:01Z", duration_ms: 1000, summary: "Durable job enqueued", error_message: null, platform_job_id: null, platform_job_status: null } }],
			logs: [{ id: 1, source: "scheduler", level: "info", code: "scheduled_task_completed", message: "OAuth sweep enqueued", scheduler_run_id: null, platform_job_id: null, created_at: "2026-08-05T22:00:00Z" }],
		}),
	};
});

import { SchedulerTab } from "./SchedulerTab";

describe("SchedulerTab", () => {
	it("shows schedule state and both scaling signals", async () => {
		const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
		render(<QueryClientProvider client={client}><SchedulerTab /></QueryClientProvider>);

		expect(await screen.findByText("Refresh expiring OAuth tokens")).toBeInTheDocument();
		expect(screen.getByText(/waiting for memory/i)).toBeInTheDocument();
		expect(screen.getByText(/Add scheduler replicas/i)).toBeInTheDocument();
		expect(screen.getByText("OAuth sweep enqueued")).toBeInTheDocument();
		expect(screen.getByText("Trigger leader")).toBeInTheDocument();
	});
});
