/**
 * Tests for the Workflows page — focused on the SolutionManagedBadge
 * affordance: managed workflows show the shared admin-only badge and hide the
 * "Edit organization scope" control; non-managed workflows keep it.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, within } from "@/test-utils";

const mockUseWorkflowsFiltered = vi.fn();
const mockUseWorkflowsMetadata = vi.fn();
vi.mock("@/hooks/useWorkflows", () => ({
	useWorkflowsFiltered: () => mockUseWorkflowsFiltered(),
	useWorkflowsMetadata: () => mockUseWorkflowsMetadata(),
}));

vi.mock("@/hooks/useWorkflowKeys", () => ({
	useWorkflowKeys: () => ({ data: [] }),
}));

const mockUseAuth = vi.fn();
vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => mockUseAuth(),
}));

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => ({ data: [] }),
}));

vi.mock("@/hooks/useMediaQuery", () => ({
	useIsDesktop: () => true,
}));

vi.mock("@/components/workflows/WorkflowSidebar", () => ({
	WorkflowSidebar: () => null,
}));
vi.mock("@/components/workflows/WorkflowEditDialog", () => ({
	WorkflowEditDialog: () => null,
}));
vi.mock("@/components/workflows/OrphanedWorkflowDialog", () => ({
	OrphanedWorkflowDialog: () => null,
}));
vi.mock("@/components/search/SearchBox", () => ({ SearchBox: () => null }));
vi.mock("@/components/forms/OrganizationSelect", () => ({
	OrganizationSelect: () => null,
}));
vi.mock("@/services/fileService", () => ({ fileService: {} }));

function makeWorkflow(overrides: Partial<Record<string, unknown>> = {}) {
	return {
		id: "wf-1",
		name: "sync_tickets",
		description: "Sync tickets",
		type: "workflow",
		category: null,
		organization_id: null,
		endpoint_enabled: false,
		is_orphaned: false,
		is_solution_managed: false,
		solution_id: null,
		...overrides,
	};
}

beforeEach(() => {
	mockUseAuth.mockReturnValue({ isPlatformAdmin: true });
	mockUseWorkflowsMetadata.mockReturnValue({ data: { workflows: [] } });
	mockUseWorkflowsFiltered.mockReturnValue({
		data: [],
		isLoading: false,
		refetch: vi.fn(),
	});
});

async function renderPage() {
	const { Workflows } = await import("./Workflows");
	return renderWithProviders(<Workflows />);
}

describe("Workflows — solution-managed badge (grid view)", () => {
	it("shows the badge and hides the scope-edit control on a managed workflow", async () => {
		mockUseWorkflowsFiltered.mockReturnValue({
			data: [
				makeWorkflow({
					id: "m",
					name: "managed_wf",
					is_solution_managed: true,
					solution_id: "s1",
				}),
			],
			isLoading: false,
			refetch: vi.fn(),
		});
		await renderPage();
		const badge = screen.getByTestId("solution-managed-badge");
		expect(badge).toHaveAttribute("href", "/solutions/s1");
		expect(
			screen.queryByRole("button", { name: /edit organization scope/i }),
		).not.toBeInTheDocument();
	});

	it("shows the scope-edit control and no badge on a non-managed workflow", async () => {
		mockUseWorkflowsFiltered.mockReturnValue({
			data: [makeWorkflow()],
			isLoading: false,
			refetch: vi.fn(),
		});
		await renderPage();
		expect(
			screen.queryByTestId("solution-managed-badge"),
		).not.toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: /edit organization scope/i }),
		).toBeInTheDocument();
	});
});

describe("Workflows — solution-managed badge (table view)", () => {
	async function renderTable(wfs: ReturnType<typeof makeWorkflow>[]) {
		mockUseWorkflowsFiltered.mockReturnValue({
			data: wfs,
			isLoading: false,
			refetch: vi.fn(),
		});
		const { user } = await renderPage();
		await user.click(screen.getByLabelText(/table view/i));
		return user;
	}

	it("shows the badge and hides the scope-edit control on a managed row", async () => {
		await renderTable([
			makeWorkflow({
				id: "m",
				name: "managed_wf",
				is_solution_managed: true,
				solution_id: "s1",
			}),
		]);
		const table = document.querySelector("table")!;
		expect(
			within(table).getByTestId("solution-managed-badge"),
		).toHaveAttribute("href", "/solutions/s1");
		expect(
			within(table).queryByRole("button", {
				name: /edit organization scope/i,
			}),
		).not.toBeInTheDocument();
	});

	it("shows the scope-edit control and no badge on a non-managed row", async () => {
		await renderTable([makeWorkflow()]);
		const table = document.querySelector("table")!;
		expect(
			within(table).queryByTestId("solution-managed-badge"),
		).not.toBeInTheDocument();
		expect(
			within(table).getByRole("button", {
				name: /edit organization scope/i,
			}),
		).toBeInTheDocument();
	});
});
