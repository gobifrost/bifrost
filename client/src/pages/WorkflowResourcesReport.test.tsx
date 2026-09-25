import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { WorkflowResourcesReport } from "./WorkflowResourcesReport";

const mockUseWorkflowResourceReport = vi.fn();
vi.mock("@/services/workflow-resources", () => ({
	useWorkflowResourceReport: (...args: unknown[]) =>
		mockUseWorkflowResourceReport(...args),
}));
vi.mock("@/components/forms/OrganizationSelect", () => ({
	OrganizationSelect: () => <div>All organizations</div>,
}));

beforeEach(() => {
	vi.clearAllMocks();
	mockUseWorkflowResourceReport.mockImplementation(
		(filters: { view: string }) => ({
			isLoading: false,
			error: null,
			isFetching: false,
			refetch: vi.fn(),
			data: {
				summary: {
					run_count: 2,
					total_cpu_seconds: 2.25,
					total_duration_ms: 3000,
					total_ai_cost: "0.30",
					total_ai_calls: 2,
				},
				total: filters.view === "runs" ? 2 : 1,
				runs: [
					{
						execution_id: "run-1",
						workflow_name: "Build patch plan",
						organization_name: "Covi",
						status: "Success",
						started_at: "2026-09-24T22:20:00Z",
						duration_ms: 2000,
						cpu_total_seconds: 1.5,
						avg_cpu_cores: 0.75,
						peak_cpu_cores: 0.98,
						peak_process_rss_bytes: 125829120,
						ai_cost: "0",
						ai_calls: 0,
						ai_tokens: 0,
					},
				],
				workflows: [
					{
						workflow_id: "workflow-1",
						workflow_name: "Build patch plan",
						run_count: 2,
						failed_count: 1,
						total_cpu_seconds: 2.25,
						total_duration_ms: 3000,
						max_peak_process_rss_bytes: 125829120,
						total_ai_cost: "0.30",
					},
				],
			},
		}),
	);
});

describe("WorkflowResourcesReport", () => {
	it("shows average and sampled peak CPU and opens a run", () => {
		renderWithProviders(<WorkflowResourcesReport />);
		expect(screen.getByText("75% of one core")).toBeVisible();
		expect(screen.getByText("98% of one core")).toBeVisible();
		expect(screen.getByText("120 MiB")).toBeVisible();
		expect(screen.getByRole("link", { name: "View run" })).toHaveAttribute(
			"href",
			"/history/run-1",
		);
	});

	it("drills from a workflow ranking into its runs", async () => {
		const { user } = renderWithProviders(<WorkflowResourcesReport />);
		await user.click(screen.getByRole("tab", { name: "By workflow" }));
		await user.click(
			screen.getByRole("button", { name: "Build patch plan" }),
		);
		expect(screen.getByRole("tab", { name: "Runs" })).toHaveAttribute(
			"data-state",
			"active",
		);
		expect(mockUseWorkflowResourceReport).toHaveBeenLastCalledWith(
			expect.objectContaining({
				view: "runs",
				workflowId: "workflow-1",
				workflow: undefined,
			}),
		);
	});
});
