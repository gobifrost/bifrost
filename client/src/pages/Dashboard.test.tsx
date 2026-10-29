import { describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { Dashboard } from "./Dashboard";

const query = vi.hoisted(() => vi.fn(() => ({ refetch: vi.fn() })));
vi.mock("@/services/authorization", () => ({
	useAuthorization: () => ({ canAnywhere: (permission: string) => permission === "metrics.read" }),
}));
vi.mock("@/lib/api-client", () => ({ $api: { useQuery: query } }));
vi.mock("@/hooks/useDashboardMetrics", () => ({
	useDashboardMetrics: () => ({ data: { workflow_count: 7, form_count: 3 }, refetch: vi.fn() }),
}));
vi.mock("@/hooks/useExecutionTimeSeries", () => ({ useExecutionTimeSeries: () => ({ refetch: vi.fn() }) }));
vi.mock("@/components/layout/WorkspaceTabs", () => ({ WorkspaceTabs: () => <h1>Dashboard</h1> }));
vi.mock("@/components/dashboard/DashboardStatCards", () => ({ DashboardStatCards: () => <p>Execution metrics</p> }));
vi.mock("@/components/dashboard/ExecutionsOverTimeCard", () => ({ ExecutionsOverTimeCard: () => null, WINDOW_LABELS: { "7d": "7 days" } }));

describe("Dashboard permission boundaries", () => {
	it("renders metrics for a scoped reader and does not request unauthorized inventories", () => {
		renderWithProviders(<Dashboard />);
		expect(screen.getByRole("heading", { name: "Dashboard" })).toBeVisible();
		expect(screen.getByText("Execution metrics")).toBeVisible();
		expect(query).toHaveBeenCalledWith("get", "/api/agents", {}, expect.objectContaining({ enabled: false }));
		expect(query).toHaveBeenCalledWith("get", "/api/applications", {}, expect.objectContaining({ enabled: false }));
	});
});
