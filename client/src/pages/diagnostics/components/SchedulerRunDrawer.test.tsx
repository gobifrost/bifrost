import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
	vi.restoreAllMocks();
});

vi.mock("@/services/schedulerDiagnostics", async () => {
	const actual = await vi.importActual<typeof import("@/services/schedulerDiagnostics")>("@/services/schedulerDiagnostics");
	return {
		...actual,
		getSchedulerTaskHistory: vi.fn().mockResolvedValue({
			task_id: "oauth_token_refresh",
			name: "Refresh Expiring OAuth Tokens",
			runs: [
				{
					id: "00000000-0000-0000-0000-000000000002",
					status: "enqueued",
					platform_job_status: "succeeded",
					leader_owner_id: "scheduler-a",
					started_at: "2026-08-05T22:15:00Z",
					completed_at: "2026-08-05T22:15:01Z",
					duration_ms: 1000,
					summary: "Second sweep completed",
					platform_job_id: "10000000-0000-0000-0000-000000000002",
					platform_job_memory_start_bytes: 100,
					platform_job_memory_peak_bytes: 300,
					logs: [
						{ id: 2, source: "scheduler", level: "info", code: "scheduled_task_completed", message: "Second run log", created_at: "2026-08-05T22:15:01Z" },
						{ id: 1, source: "scheduler", level: "info", code: "scheduled_task_started", message: "Second run started", created_at: "2026-08-05T22:15:00Z" },
					],
				},
				{
					id: "00000000-0000-0000-0000-000000000001",
					status: "failed",
					leader_owner_id: "scheduler-b",
					started_at: "2026-08-05T22:00:00Z",
					completed_at: "2026-08-05T22:00:01Z",
					duration_ms: 1000,
					error_message: "First sweep failed",
					logs: [{ id: 1, source: "scheduler", level: "error", code: "scheduled_task_failed", message: "First run log", created_at: "2026-08-05T22:00:01Z" }],
				},
			],
		}),
	};
});

import { SchedulerRunDrawer } from "./SchedulerRunDrawer";

const task = {
	task_id: "oauth_token_refresh",
	name: "Refresh Expiring OAuth Tokens",
	schedule: "Every 15 minutes",
	execution_mode: "durable_job",
	enabled: true,
	next_run_at: "2026-08-05T22:30:00Z",
	last_run: null,
};

describe("SchedulerRunDrawer", () => {
	it("filters published logs by the selected recent run", async () => {
		const user = userEvent.setup();
		const writeText = vi
			.spyOn(navigator.clipboard, "writeText")
			.mockResolvedValue(undefined);
		const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
		render(
			<QueryClientProvider client={client}>
				<SchedulerRunDrawer task={task} onClose={vi.fn()} />
			</QueryClientProvider>,
		);

		expect(await screen.findByText("Second run log")).toBeInTheDocument();
		expect(screen.getAllByText(/Second run (started|log)/).map((node) => node.textContent)).toEqual([
			"Second run started",
			"Second run log",
		]);
		expect(screen.queryByText("First run log")).not.toBeInTheDocument();
		expect(screen.getByText("00000000-0000-0000-0000-000000000002")).toBeInTheDocument();
		expect(screen.getByText("Container Memory Change")).toBeInTheDocument();
		expect(screen.getByText("Shared scheduler cgroup")).toBeInTheDocument();
		expect(screen.getByText("200 B")).toBeInTheDocument();
		expect(
			screen.getByRole("button", {
				name: /View Succeeded run 00000000-0000-0000-0000-000000000002/i,
			}),
		).toHaveClass("border-l-4", "border-l-green-500");
		expect(
			screen.getByRole("button", {
				name: /View Failed run 00000000-0000-0000-0000-000000000001/i,
			}),
		).toHaveClass("border-l-4", "border-l-destructive");
		await user.click(screen.getByRole("button", { name: "Copy run ID" }));
		expect(writeText).toHaveBeenCalledWith("00000000-0000-0000-0000-000000000002");

		await user.click(
			screen.getByRole("button", {
				name: /00000000-0000-0000-0000-000000000001/i,
			}),
		);
		expect(await screen.findByText("First run log")).toBeInTheDocument();
		expect(screen.queryByText("Second run log")).not.toBeInTheDocument();
		expect(screen.getByText("First sweep failed")).toBeInTheDocument();
		expect(screen.getByText("00000000-0000-0000-0000-000000000001")).toBeInTheDocument();
		expect(screen.getByText("Not recorded")).toBeInTheDocument();
	});
});
