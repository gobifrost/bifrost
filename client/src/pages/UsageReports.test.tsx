import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import { UsageReports } from "./UsageReports";

const boundaryState = vi.hoisted(() => ({
	selectedBoundary: "organization:org-1" as string | undefined,
	selectedTarget: { kind: "organization", label: "Acme Co" } as
		| { kind: string; label?: string }
		| undefined,
	capabilities: new Set<string>(["metrics.read", "metrics.readwrite"]),
}));
const usageReportMock = vi.hoisted(() => vi.fn());

vi.mock("@/contexts/AuthorizationBoundaryContext", () => ({
	useAuthorizationBoundary: () => ({
		selectedBoundary: boundaryState.selectedBoundary,
		selectedTarget: boundaryState.selectedTarget,
		hasSelectedCapability: (capability: string) =>
			boundaryState.capabilities.has(capability),
	}),
}));

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => ({ data: [] }),
}));

vi.mock("@/components/forms/OrganizationSelect", () => ({
	OrganizationSelect: () => <div>Organization select</div>,
}));

vi.mock("@/components/reports/UsageSummaryCards", () => ({
	UsageSummaryCards: () => <div>Summary cards</div>,
}));
vi.mock("@/components/reports/UsageCharts", () => ({
	UsageCharts: () => <div>Usage charts</div>,
}));
vi.mock("@/components/reports/UsageTables", () => ({
	WorkflowTable: () => <div>Workflow table</div>,
	ConversationTable: () => <div>Conversation table</div>,
	AgentTable: () => <div>Agent table</div>,
	OrganizationTable: () => <div>Organization table</div>,
	KnowledgeStorageTable: () => <div>Knowledge storage table</div>,
}));
vi.mock("@/components/reports/UsageLimitsPanel", () => ({
	UsageLimitsPanel: () => <div>Limits panel</div>,
}));
vi.mock("@/services/usage", () => ({
	useUsageReport: (...args: unknown[]) => usageReportMock(...args),
}));

describe("UsageReports", () => {
	beforeEach(() => {
		usageReportMock.mockReset();
		usageReportMock.mockReturnValue({ data: null, isLoading: false, error: null });
		boundaryState.selectedBoundary = "organization:org-1";
		boundaryState.selectedTarget = { kind: "organization", label: "Acme Co" };
		boundaryState.capabilities = new Set(["metrics.read", "metrics.readwrite"]);
	});

	it("locks overview usage to the selected organization boundary", () => {
		renderWithProviders(<UsageReports />);

		expect(screen.getByRole("heading", { name: "Usage" })).toBeVisible();
		expect(usageReportMock).toHaveBeenCalledWith(
			expect.any(String),
			expect.any(String),
			"all",
			"org-1",
			{ enabled: true },
		);
		expect(
			screen.getByText(/locked to the selected organization/i),
		).toBeVisible();
	});

	it("suppresses overview query while the Limits tab is active", async () => {
		const { user } = renderWithProviders(<UsageReports />);

		await user.click(screen.getByRole("tab", { name: /limits/i }));

		expect(usageReportMock).toHaveBeenLastCalledWith(
			expect.any(String),
			expect.any(String),
			"all",
			"org-1",
			{ enabled: false },
		);
		expect(screen.getByText("Limits panel")).toBeVisible();
	});

	it("shows an exact-boundary prompt and suppresses queries for managed organizations", () => {
		boundaryState.selectedBoundary = "managed_organizations";
		boundaryState.selectedTarget = { kind: "managed_organizations" };

		renderWithProviders(<UsageReports />);

		expect(
			screen.getByText(/choose global or one exact organization/i),
		).toBeVisible();
		expect(usageReportMock).toHaveBeenCalledWith(
			expect.any(String),
			expect.any(String),
			"all",
			null,
			{ enabled: false },
		);
	});
});
