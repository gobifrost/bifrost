import { Workflows } from "./Workflows";
/**
 * Tests for the Workflows page — focused on the SolutionManagedBadge
 * affordance: managed workflows show the shared admin-only badge and hide the
 * "Edit" control; non-managed workflows keep it.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { useLocation } from "react-router-dom";
import { renderWithProviders, screen, within } from "@/test-utils";

const mockUseWorkflowsFiltered = vi.fn();
const mockUseWorkflowsMetadata = vi.fn();
const mockUseIsDesktop = vi.fn();
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
	useIsDesktop: () => mockUseIsDesktop(),
}));

vi.mock("@/components/workflows/WorkflowSidebar", () => ({
	WorkflowSidebar: ({
		className,
		onClose,
	}: {
		className?: string;
		onClose?: () => void;
	}) => (
		<aside data-testid="workflow-sidebar" className={className}>
			{onClose && (
				<button type="button" onClick={onClose}>
					Close sidebar
				</button>
			)}
		</aside>
	),
}));
vi.mock("@/components/workflows/WorkflowEditDialog", () => ({
	WorkflowEditDialog: () => null,
}));
vi.mock("@/components/workflows/OrphanedWorkflowDialog", () => ({
	OrphanedWorkflowDialog: () => null,
}));
vi.mock("@/components/forms/OrganizationSelect", () => ({
	OrganizationSelect: () => null,
}));
const mockReadFile = vi.fn();
vi.mock("@/services/fileService", () => ({ fileService: { readFile: (...args: unknown[]) => mockReadFile(...args) } }));

const mockUseServicesList = vi.fn();
vi.mock("@/components/services/useServiceQueries", () => ({
	useServicesList: (...args: unknown[]) => mockUseServicesList(...args),
	useServiceDetail: () => ({ data: undefined }),
	useServiceAttempts: () => ({ data: undefined }),
}));

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
	mockUseIsDesktop.mockReturnValue(true);
	mockUseAuth.mockReturnValue({ isPlatformAdmin: true });
	mockUseWorkflowsMetadata.mockReturnValue({ data: { workflows: [] } });
	mockUseWorkflowsFiltered.mockReturnValue({
		data: [],
		isLoading: false,
		refetch: vi.fn(),
	});
	mockUseServicesList.mockReturnValue({ data: { items: [], total: 0 } });
});

function LocationProbe() {
	const location = useLocation();
	return (
		<output aria-label="location">
			{location.pathname + location.search}
		</output>
	);
}

async function renderPage() {
	return renderWithProviders(
		<>
			<LocationProbe />
			<Workflows />
		</>,
	);
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
		const { user } = await renderPage();
		await user.click(
			screen.getByRole("button", { name: "managed_wf actions" }),
		);
		const badge = screen.getByTestId("solution-managed-badge");
		expect(badge).toHaveAttribute("href", "/solutions/s1");
		expect(
			screen.queryByRole("menuitem", {
				name: /^edit$/i,
			}),
		).not.toBeInTheDocument();
	});

	it("shows the scope-edit control and no badge on a non-managed workflow", async () => {
		mockUseWorkflowsFiltered.mockReturnValue({
			data: [makeWorkflow()],
			isLoading: false,
			refetch: vi.fn(),
		});
		const { user } = await renderPage();
		await user.click(
			screen.getByRole("button", { name: "sync_tickets actions" }),
		);
		expect(
			screen.queryByTestId("solution-managed-badge"),
		).not.toBeInTheDocument();
		expect(
			screen.getByRole("menuitem", { name: /^edit$/i }),
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
		const user = await renderTable([
			makeWorkflow({
				id: "m",
				name: "managed_wf",
				is_solution_managed: true,
				solution_id: "s1",
			}),
		]);
		await user.click(
			screen.getByRole("button", { name: "managed_wf actions" }),
		);
		const table = document.querySelector("table")!;
		expect(
			within(table).getByTestId("solution-managed-badge"),
		).toHaveAttribute("href", "/solutions/s1");
		expect(
			screen.queryByRole("menuitem", {
				name: /^edit$/i,
			}),
		).not.toBeInTheDocument();
	});

	it("shows the scope-edit control and no badge on a non-managed row", async () => {
		const user = await renderTable([makeWorkflow()]);
		await user.click(
			screen.getByRole("button", { name: "sync_tickets actions" }),
		);
		const table = document.querySelector("table")!;
		expect(
			within(table).queryByTestId("solution-managed-badge"),
		).not.toBeInTheDocument();
		expect(
			screen.getByRole("menuitem", {
				name: /^edit$/i,
			}),
		).toBeInTheDocument();
	});
});

describe("Workflows — mobile filters shell", () => {
	it("shows an expandable full-width filter panel above the list on mobile", async () => {
		mockUseIsDesktop.mockReturnValue(false);
		mockUseWorkflowsFiltered.mockReturnValue({
			data: [makeWorkflow()],
			isLoading: false,
			refetch: vi.fn(),
		});

		const { user } = await renderPage();

		const showFilters = screen.getByRole("button", {
			name: /show filters/i,
		});
		expect(showFilters).toBeInTheDocument();
		expect(
			screen.queryByTestId("workflow-sidebar"),
		).not.toBeInTheDocument();

		await user.click(showFilters);

		expect(screen.getByTestId("workflow-sidebar")).toHaveClass("w-full");
	});
});

describe("Workflows — desktop filter shell", () => {
	it("keeps the collapsed sidebar control at 44px when the desktop filter panel is closed", async () => {
		mockUseIsDesktop.mockReturnValue(true);
		mockUseWorkflowsFiltered.mockReturnValue({
			data: [makeWorkflow()],
			isLoading: false,
			refetch: vi.fn(),
		});

		const { user } = await renderPage();

		expect(screen.getByTestId("workflow-sidebar")).toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Close sidebar" }));

		const showFilters = screen.getByRole("button", {
			name: /show filters/i,
		});
		expect(showFilters).toHaveAttribute("data-size", "icon-lg");
	});
});

describe("Workflows read recovery", () => {
	it("offers retry instead of an empty list when the initial request fails", async () => {
		const refetch = vi.fn();
		mockUseWorkflowsFiltered.mockReturnValue({
			data: undefined,
			isLoading: false,
			isError: true,
			isFetching: false,
			refetch,
		});
		const { user } = await renderPage();
		expect(screen.getByRole("alert")).toHaveTextContent(
			"Couldn't load workflows.",
		);
		expect(screen.queryByText(/no workflows/i)).not.toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Retry loading" }));
		expect(refetch).toHaveBeenCalledOnce();
	});

	it("retains cached records and disables retry while refreshing", async () => {
		mockUseWorkflowsFiltered.mockReturnValue({
			data: [makeWorkflow()],
			isLoading: false,
			isError: true,
			isFetching: true,
			refetch: vi.fn(),
		});
		await renderPage();
		expect(screen.getByRole("alert")).toHaveTextContent(
			"Previously loaded records are shown below.",
		);
		expect(screen.getByText("sync_tickets")).toBeVisible();
		expect(
			screen.getByRole("button", { name: "Retrying…" }),
		).toBeDisabled();
	});
});

it("explains an empty type filter and lets users clear it", async () => {
	mockUseWorkflowsFiltered.mockReturnValue({
		data: [],
		isLoading: false,
		isFetching: false,
		refetch: vi.fn(),
	});
	const { user } = await renderPage();
	await user.click(screen.getByRole("radio", { name: "Tools" }));
	expect(screen.getByText("1 filter applied")).toBeVisible();
	expect(
		screen.getByRole("heading", {
			name: "No workflows match your filters",
		}),
	).toBeVisible();
	expect(
		screen.queryByRole("button", { name: "Open editor" }),
	).not.toBeInTheDocument();
	await user.click(screen.getByRole("button", { name: "Clear filters" }));
	expect(screen.getByRole("radio", { name: "All" })).toHaveAttribute(
		"aria-checked",
		"true",
	);
	expect(screen.queryByText("1 filter applied")).not.toBeInTheDocument();
	expect(
		screen.getByRole("heading", { name: "No workflows available" }),
	).toBeVisible();
});

it("opens metadata for the selected workflow ID when names repeat", async () => {
	mockUseWorkflowsFiltered.mockReturnValue({ data: [makeWorkflow()], isLoading: false, refetch: vi.fn() });
	mockUseWorkflowsMetadata.mockReturnValue({ data: { workflows: [
		{ id: "other-workflow", name: "sync_tickets", relative_file_path: "other.py" },
		{ id: "wf-1", name: "sync_tickets", relative_file_path: "selected.py" },
	] } });
	mockReadFile.mockRejectedValue(new Error("Synthetic read failure"));
	const { user } = await renderPage();
	await user.click(screen.getByRole("button", { name: "sync_tickets actions" }));
	await user.click(screen.getByRole("menuitem", { name: "Open in editor" }));
	expect(mockReadFile).toHaveBeenCalledWith("selected.py");
});


it("finds the edited display name and excludes unrelated workflows", async () => {
 mockUseWorkflowsFiltered.mockReturnValue({
  data: [makeWorkflow({ display_name: "Customer onboarding" }), makeWorkflow({ id: "wf-2", name: "archive_logs", display_name: "Archive logs" })],
  isLoading: false, refetch: vi.fn(),
 });
 const { user } = await renderPage();
 await user.type(screen.getByRole("textbox", { name: "Search by name, description, or category..." }), "Customer onboarding");
 await vi.waitFor(() => expect(screen.queryByRole("button", { name: "archive_logs actions" })).not.toBeInTheDocument());
	expect(screen.getByRole("button", { name: "sync_tickets actions" })).toBeInTheDocument();
});

describe("Workflows — service row links", () => {
	it("links workflow-list service rows to the detail route", async () => {
		mockUseWorkflowsFiltered.mockReturnValue({
			data: [
				makeWorkflow({
					id: "a1a1a1a1-a1a1-4a1a-8a1a-a1a1a1a1a1a1",
					name: "telegram_bridge",
					type: "service",
				}),
			],
			isLoading: false,
			refetch: vi.fn(),
		});
		mockUseServicesList.mockReturnValue({
			data: {
				items: [
					{
						id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
						workflow_id: "a1a1a1a1-a1a1-4a1a-8a1a-a1a1a1a1a1a1",
					},
				],
				total: 1,
			},
		});
		await renderPage();
		const link = screen.getByRole("link", { name: "telegram_bridge" });
		expect(link).toHaveAttribute(
			"href",
			"/services/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
		);
	});

	it("shows no service link when the workflow has no live definition", async () => {
		mockUseWorkflowsFiltered.mockReturnValue({
			data: [
				makeWorkflow({
					id: "a1a1a1a1-a1a1-4a1a-8a1a-a1a1a1a1a1a1",
					name: "telegram_bridge",
					type: "service",
				}),
			],
			isLoading: false,
			refetch: vi.fn(),
		});
		mockUseServicesList.mockReturnValue({
			data: { items: [], total: 0 },
		});
		await renderPage();
		expect(
			screen.queryByRole("link", { name: "telegram_bridge" }),
		).not.toBeInTheDocument();
	});
});
