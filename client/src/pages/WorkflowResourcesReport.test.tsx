import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { WorkflowResourcesReport } from "./WorkflowResourcesReport";
import { useLocation } from "react-router-dom";

function LocationProbe() {
	const location = useLocation();
	return (
		<output role="status" aria-label="Current URL">
			{location.pathname}
			{location.search}
		</output>
	);
}

const mockUseWorkflowResourceReport = vi.fn();
let mockTotal: number | null = null;
vi.mock("@/services/workflow-resources", () => ({
	useWorkflowResourceReport: (...args: unknown[]) =>
		mockUseWorkflowResourceReport(...args),
}));
vi.mock("@/components/forms/OrganizationSelect", () => ({
	OrganizationSelect: () => <div>All organizations</div>,
}));

beforeEach(() => {
	vi.clearAllMocks();
	mockTotal = null;
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
				total: mockTotal ?? (filters.view === "runs" ? 2 : 1),
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
						max_peak_cpu_cores: 0.98,
						max_peak_process_rss_bytes: 125829120,
						total_ai_cost: "0.30",
					},
				],
			},
		}),
	);
});

afterEach(() => vi.useRealTimers());

describe("WorkflowResourcesReport", () => {
	it("restores view, filters, dates, and page from the URL", async () => {
		const { user } = renderWithProviders(
			<>
				<WorkflowResourcesReport reportSwitch={null} />
				<LocationProbe />
			</>,
			{
				initialEntries: [
					"/usage?tab=workflow&workflow_tab=workflows&workflow_search=patch&workflow_org=org-1&workflow_status=Failed&workflow_from=2026-09-01&workflow_to=2026-09-12&workflow_page=2",
				],
			},
		);
		expect(
			screen.getByRole("tab", { name: "By Workflow" }),
		).toHaveAttribute("data-state", "active");
		expect(mockUseWorkflowResourceReport).toHaveBeenLastCalledWith(
			expect.objectContaining({
				view: "workflows",
				workflow: "patch",
				orgId: "org-1",
				status: "Failed",
				sort: "cpu",
				page: 2,
			}),
		);
		expect(
			screen.getByRole("navigation", { name: "Workflow resource pages" }),
		).toBeVisible();
		await user.click(screen.getByRole("tab", { name: "Runs" }));
		expect(
			screen.queryByRole("navigation", {
				name: "Workflow resource pages",
			}),
		).not.toBeInTheDocument();
		expect(
			screen.getByRole("status", { name: "Current URL" }),
		).not.toHaveTextContent("workflow_page=2");
		expect(mockUseWorkflowResourceReport).toHaveBeenLastCalledWith(
			expect.objectContaining({
				view: "runs",
				workflow: "patch",
				sort: "started",
				page: 1,
			}),
		);
	});

	it("keeps its date window stable while the report rerenders", () => {
		vi.useFakeTimers({ toFake: ["Date"] });
		vi.setSystemTime(new Date("2026-09-24T20:00:00Z"));
		const { rerender } = renderWithProviders(
			<WorkflowResourcesReport reportSwitch={null} />,
		);
		const firstWindow = mockUseWorkflowResourceReport.mock.lastCall?.[0];
		vi.setSystemTime(new Date("2026-09-24T20:01:00Z"));
		rerender(<WorkflowResourcesReport reportSwitch={null} />);
		expect(mockUseWorkflowResourceReport.mock.lastCall?.[0]).toEqual(
			firstWindow,
		);
	});

	it("shows CPU percentages and a run link in the shared data table", () => {
		renderWithProviders(<WorkflowResourcesReport reportSwitch={null} />);
		expect(screen.getByText("75%")).toBeVisible();
		expect(screen.getByText("98%")).toBeVisible();
		expect(screen.getByText("120 MiB")).toBeVisible();
		expect(
			screen.getByRole("link", { name: "Build patch plan" }),
		).toHaveAttribute("href", "/history/run-1");
	});

	it("shows pagination only when another page is available", async () => {
		const { user, rerender } = renderWithProviders(
			<WorkflowResourcesReport reportSwitch={null} />,
		);
		expect(
			screen.queryByRole("navigation", {
				name: "Workflow resource pages",
			}),
		).not.toBeInTheDocument();
		await user.click(screen.getByRole("tab", { name: "By Workflow" }));
		expect(
			screen.queryByRole("navigation", {
				name: "Workflow resource pages",
			}),
		).not.toBeInTheDocument();
		mockTotal = 51;
		rerender(<WorkflowResourcesReport reportSwitch={null} />);
		expect(
			screen.getByRole("navigation", { name: "Workflow resource pages" }),
		).toBeVisible();
	});

	it("shows peak CPU by workflow and drills into its runs", async () => {
		const { user } = renderWithProviders(
			<WorkflowResourcesReport reportSwitch={null} />,
		);
		await user.click(screen.getByRole("tab", { name: "By Workflow" }));
		expect(
			screen.getByRole("columnheader", { name: "Peak CPU %" }),
		).toBeVisible();
		expect(screen.getByText("98%")).toBeVisible();
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
