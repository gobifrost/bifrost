import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders, screen } from "@/test-utils";
import { BuilderTab } from "./BuilderTab";

const mockGetSetup = vi.fn();
const mockListJobs = vi.fn();

vi.mock("@/services/builderRunner", () => ({
	getBuilderRunnerSetup: (...args: unknown[]) => mockGetSetup(...args),
}));
vi.mock("@/services/platformJobs", () => ({
	listPlatformJobs: (...args: unknown[]) => mockListJobs(...args),
}));

beforeEach(() => {
	vi.clearAllMocks();
	mockGetSetup.mockResolvedValue({
		config: { provider: "local", enabled: true },
		readiness: { provider: "local", ready: true, connected: true },
		runner_image: "ghcr.io/gobifrost/bifrost-build:dev",
	});
	mockListJobs.mockResolvedValue([
		{
			id: "job-1",
			job_type: "solution.builder.turn",
			title: "Building Customer Intake",
			requested_by_name: "Platform Admin",
			status: "succeeded",
			progress: { phase: "Complete", percent: 100 },
			result: {
				harness_diagnostics: {
					tool_call_count: 4,
					tool_error_count: 0,
					compaction_count: 1,
				},
			},
			error: null,
			external_provider: "local",
			external_run_id: "worker-run-1",
			action_url: "/solutions/solution-1/builder",
			started_at: "2026-08-16T12:00:00Z",
			completed_at: "2026-08-16T12:00:04Z",
			created_at: "2026-08-16T12:00:00Z",
			updated_at: "2026-08-16T12:00:04Z",
		},
	]);
});

describe("Builder diagnostics", () => {
	it("shows runner health, recent jobs, and privacy-safe harness metrics", async () => {
		renderWithProviders(<BuilderTab />);

		expect(await screen.findByText("Ready for users")).toBeInTheDocument();
		expect(
			screen.getByText("Building Customer Intake"),
		).toBeInTheDocument();
		expect(
			screen.getByText(/Harness: 4 tool calls · 0 errors · 1 compaction/),
		).toBeInTheDocument();
		expect(screen.getByRole("link", { name: /open/i })).toHaveAttribute(
			"href",
			"/solutions/solution-1/builder",
		);
	});
});
