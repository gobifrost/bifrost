/**
 * Tests for the polished Solution detail view — breadcrumb, tab counts, the
 * required-config warning banner, entity links carrying `?from=solution:`, and
 * the Configs tab as the config-value entry surface.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";

const mockNavigate = vi.fn();
vi.mock("react-router-dom", async () => {
	const actual =
		await vi.importActual<typeof import("react-router-dom")>(
			"react-router-dom",
		);
	return {
		...actual,
		useNavigate: () => mockNavigate,
		useParams: () => ({ solutionId: "sol-1" }),
	};
});

vi.mock("sonner", () => ({
	toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => ({
		data: [{ id: "org-1", name: "Acme Corp" }],
	}),
}));

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({ isPlatformAdmin: true }),
}));

const mockGetSolutionEntities = vi.fn();
const mockUpdateSolution = vi.fn();
const mockDeleteSolution = vi.fn();
const mockSetSolutionConfig = vi.fn();
const mockExportSolution = vi.fn();
const mockGetSolutionCaptureCandidates = vi.fn();
const mockCaptureSolutionEntities = vi.fn();
const mockSyncSolution = vi.fn();
vi.mock("@/services/solutions", () => ({
	getSolutionEntities: (...a: unknown[]) => mockGetSolutionEntities(...a),
	updateSolution: (...a: unknown[]) => mockUpdateSolution(...a),
	deleteSolution: (...a: unknown[]) => mockDeleteSolution(...a),
	setSolutionConfig: (...a: unknown[]) => mockSetSolutionConfig(...a),
	exportSolution: (...a: unknown[]) => mockExportSolution(...a),
	syncSolution: (...a: unknown[]) => mockSyncSolution(...a),
	getSolutionCaptureCandidates: (...a: unknown[]) =>
		mockGetSolutionCaptureCandidates(...a),
	captureSolutionEntities: (...a: unknown[]) => mockCaptureSolutionEntities(...a),
}));

function makeEntities() {
	return {
		solution: {
			id: "sol-1",
			slug: "my-solution",
			name: "My Solution",
			organization_id: "org-1",
			global_repo_access: false,
			git_connected: false,
			git_repo_url: null,
			scope: "org",
		},
		workflows: [
			{
				id: "wf-1",
				name: "Sync Tickets",
				description: "Sync external tickets",
				type: "workflow",
				category: "Support",
				path: "workflows/tickets.py",
				function_name: "sync_tickets",
			},
		],
			apps: [
				{
					id: "app-1",
					name: "Solution App",
					slug: "solution-app",
					description: "Solution app",
					app_model: "standalone_v2",
					is_published: true,
					has_unpublished_changes: false,
					logo: "data:image/svg+xml;base64,PHN2Zy8+",
				},
			],
			forms: [
				{
					id: "form-1",
					name: "Ticket Intake",
					description: "Collect ticket context",
					is_active: true,
					organization_id: "org-1",
				},
			],
		agents: [],
		tables: [{ id: "tbl-1", name: "Customers" }],
		claims: [
			{
				id: "claim-1",
				name: "customer_regions",
				description: "Regions for the current user",
				type: "list",
				source_table: "customers",
				select: "region",
			},
		],
		configs: [
			{
				id: "cfg-1",
				key: "api_token",
				type: "secret",
				required: true,
				description: "Upstream API token",
				value_set: false,
			},
			{
				id: "cfg-2",
				key: "base_url",
				type: "string",
				required: false,
				description: null,
				value_set: true,
			},
		],
		required_configs_unset: ["api_token"],
	};
}

async function renderPage() {
	const { SolutionDetail } = await import("./SolutionDetail");
	return renderWithProviders(<SolutionDetail />);
}

beforeEach(() => {
	vi.clearAllMocks();
	mockGetSolutionEntities.mockResolvedValue(makeEntities());
	mockGetSolutionCaptureCandidates.mockResolvedValue({
		workflows: [],
		apps: [],
		forms: [],
		agents: [],
		tables: [{ id: "tbl-2", name: "Orders", description: "Order data" }],
		claims: [],
		configs: [],
	});
	mockCaptureSolutionEntities.mockResolvedValue({
		solution_id: "sol-1",
		workflows_captured: 0,
		apps_captured: 0,
		forms_captured: 0,
		agents_captured: 0,
		tables_captured: 1,
		claims_captured: 0,
		config_declarations_captured: 0,
	});
});

describe("SolutionDetail", () => {
	it("renders the breadcrumb link and install name", async () => {
		await renderPage();
		await screen.findByTestId("solution-detail");

		const crumb = screen.getByRole("link", { name: /solutions/i });
		expect(crumb).toHaveAttribute("href", "/solutions");
		expect(
			screen.getByRole("heading", { name: "My Solution" }),
		).toBeInTheDocument();
	});

	it("renders the version and upgraded-from subtext", async () => {
		const entities = makeEntities();
		entities.solution = {
			...entities.solution,
			version: "2.1.0",
			upgraded_from_version: "2.0.0",
		} as typeof entities.solution;
		mockGetSolutionEntities.mockResolvedValue(entities);
		await renderPage();
		await screen.findByTestId("solution-detail");

		expect(screen.getByText("v2.1.0")).toBeInTheDocument();
		expect(screen.getByText(/upgraded from v2\.0\.0/i)).toBeInTheDocument();
	});

	it("renders the 3 top-level tabs with Contents total + Configuration count", async () => {
		await renderPage();
		await screen.findByTestId("solution-detail");

		expect(screen.getByTestId("tab-overview")).toHaveTextContent("Overview");

		// Contents collapses the 6 entity inventories; its count is the total
		// (1 workflow + 1 app + 1 form + 0 agents + 1 table + 1 claim = 5 in the
		// fixture).
		const contents = screen.getByTestId("tab-contents");
		expect(contents).toHaveTextContent("Contents");
		expect(contents).toHaveTextContent("5");

		const configuration = screen.getByTestId("tab-configuration");
		expect(configuration).toHaveTextContent("Configuration");
		expect(configuration).toHaveTextContent("2");
	});

	it("shows the per-type chips inside Contents", async () => {
		const { user } = await renderPage();
		await screen.findByTestId("solution-detail");

		await user.click(screen.getByTestId("tab-contents"));
		const tables = screen.getByTestId("chip-tables");
		expect(tables).toHaveTextContent("Tables");
		expect(tables).toHaveTextContent("1");
		const workflows = screen.getByTestId("chip-workflows");
		expect(workflows).toHaveTextContent("Workflows");
		expect(workflows).toHaveTextContent("1");
		expect(screen.getByTestId("chip-claims")).toHaveTextContent("Custom Claims");
	});

	it("renders the update action and the overflow menu in the header", async () => {
		const { user } = await renderPage();
		await screen.findByTestId("solution-detail");

		expect(screen.getByRole("button", { name: /update/i })).toBeInTheDocument();

		// The secondary actions (Capture, Export, Edit, Delete) live behind the
		// "⋯" overflow menu now, not as a flat row of buttons.
		await user.click(screen.getByTestId("solution-actions"));
		expect(
			await screen.findByRole("menuitem", { name: /capture/i }),
		).toBeInTheDocument();
	});

	it("opens the scoped update dialog from the header", async () => {
		const { user } = await renderPage();
		await screen.findByTestId("solution-detail");

		await user.click(screen.getByRole("button", { name: /update/i }));

		expect(
			await screen.findByRole("heading", { name: /update solution/i }),
		).toBeInTheDocument();
	});

	it("opens the capture picker from the header", async () => {
		const { user } = await renderPage();
		await screen.findByTestId("solution-detail");

		await user.click(screen.getByTestId("solution-actions"));
		await user.click(await screen.findByRole("menuitem", { name: /capture/i }));

		expect(
			await screen.findByRole("heading", { name: /capture existing entities/i }),
		).toBeInTheDocument();
		expect(await screen.findByLabelText(/capture orders/i)).toBeInTheDocument();
	});

		it("shows the setup-incomplete banner", async () => {
			await renderPage();
			expect(
				await screen.findByTestId("required-config-warning"),
			).toBeInTheDocument();
			expect(
				screen.getByText(/setup incomplete .* 1 required config needs a value/i),
			).toBeInTheDocument();
		});

		it("uses the workflow list execute action instead of making the card open execution", async () => {
			const { user } = await renderPage();
			await screen.findByTestId("solution-detail");

			await user.click(screen.getByTestId("tab-contents"));
			await user.click(screen.getByTestId("chip-workflows"));
			const execute = screen.getByRole("button", { name: /execute workflow/i });
			await user.click(execute);

			expect(mockNavigate).toHaveBeenCalledWith(
				"/workflows/Sync%20Tickets/execute?from=solution:sol-1",
			);
		});

		it("uses the forms list launch action without exposing edit controls", async () => {
			const { user } = await renderPage();
			await screen.findByTestId("solution-detail");

			await user.click(screen.getByTestId("tab-contents"));
			await user.click(screen.getByTestId("chip-forms"));
			expect(screen.getByText("Ticket Intake")).toBeInTheDocument();
			expect(
				screen.queryByRole("button", { name: /edit form/i }),
			).not.toBeInTheDocument();

			await user.click(screen.getByRole("button", { name: /launch/i }));
			expect(mockNavigate).toHaveBeenCalledWith(
				"/execute/form-1?from=solution:sol-1",
			);
		});

		it("uses the applications list open behavior for solution apps", async () => {
			const { user } = await renderPage();
			await screen.findByTestId("solution-detail");

			await user.click(screen.getByTestId("tab-contents"));
			await user.click(screen.getByTestId("chip-apps"));
			expect(screen.queryByText(/open published/i)).not.toBeInTheDocument();
			expect(screen.getByTestId("entity-logo")).toHaveAttribute(
				"src",
				"data:image/svg+xml;base64,PHN2Zy8+",
			);
			await user.click(screen.getByRole("button", { name: /solution app/i }));

			expect(mockNavigate).toHaveBeenCalledWith(
				"/apps/solution-app?from=solution:sol-1",
			);
		});

		it("navigates a table row to its entity page with ?from=solution:", async () => {
			const { user } = await renderPage();
			await screen.findByTestId("solution-detail");

		await user.click(screen.getByTestId("tab-contents"));
		await user.click(screen.getByTestId("chip-tables"));
		// Entity surfaces render the shared DataTable (Roles paradigm): rows are
		// clickable and navigate, carrying the from=solution backlink.
		await user.click(screen.getByRole("row", { name: /customers/i }));
		expect(mockNavigate).toHaveBeenCalledWith(
			"/tables/tbl-1?from=solution:sol-1",
		);
	});

	it("filters entity rows with the tab's search box", async () => {
		const { user } = await renderPage();
		await screen.findByTestId("solution-detail");

		await user.click(screen.getByTestId("tab-contents"));
		await user.click(screen.getByTestId("chip-tables"));
		expect(screen.getByRole("row", { name: /customers/i })).toBeInTheDocument();

		await user.type(
			screen.getByPlaceholderText("Search tables..."),
			"zzz",
		);
		// SearchBox debounces input before propagating it.
		expect(await screen.findByText(/no tables match/i)).toBeInTheDocument();
		expect(
			screen.queryByRole("row", { name: /customers/i }),
		).not.toBeInTheDocument();
	});

	it("shows Set/Not set status and config inputs on the Configuration tab", async () => {
		const { user } = await renderPage();
		await screen.findByTestId("solution-detail");

		await user.click(screen.getByTestId("tab-configuration"));

		expect(screen.getByTestId("config-status-api_token")).toHaveTextContent(
			"Not set",
		);
		expect(screen.getByTestId("config-status-base_url")).toHaveTextContent(
			"Set",
		);
		expect(
			screen.getByTestId("config-value-input-api_token"),
		).toBeInTheDocument();
		expect(screen.getByTestId("save-config-api_token")).toBeInTheDocument();
	});

	it("saves a config value with the right key, value, type, and org", async () => {
		mockSetSolutionConfig.mockResolvedValue(undefined);
		const { user } = await renderPage();
		await screen.findByTestId("solution-detail");

		await user.click(screen.getByTestId("tab-configuration"));
		await user.type(
			screen.getByTestId("config-value-input-api_token"),
			"sekret",
		);
		await user.click(screen.getByTestId("save-config-api_token"));

		expect(mockSetSolutionConfig).toHaveBeenCalledWith({
			key: "api_token",
			value: "sekret",
			type: "secret",
			organizationId: "org-1",
		});
	});

	it("shows the zip 'Update' action when not git-connected", async () => {
		await renderPage();
		await screen.findByTestId("solution-detail");

		expect(screen.getByTestId("update-solution")).toBeInTheDocument();
		expect(screen.queryByTestId("update-now")).not.toBeInTheDocument();
		expect(
			screen.queryByTestId("update-available-badge"),
		).not.toBeInTheDocument();
	});

	it("surfaces 'Update now' + an Update-available badge for a git-connected install with an available update", async () => {
		const entities = makeEntities();
		entities.solution = {
			...entities.solution,
			git_connected: true,
			git_repo_url: "https://github.com/acme/sol",
			version: "1.0.0",
			update_available_version: "1.1.0",
		} as unknown as typeof entities.solution;
		mockGetSolutionEntities.mockResolvedValue(entities);

		await renderPage();
		await screen.findByTestId("solution-detail");

		expect(screen.getByTestId("update-available-badge")).toHaveTextContent(
			"v1.1.0",
		);
		// The git-connected pull action replaces the zip re-upload action.
		expect(screen.getByTestId("update-now")).toBeInTheDocument();
		expect(screen.queryByTestId("update-solution")).not.toBeInTheDocument();
	});

	it("'Update now' confirms then calls syncSolution and invalidates", async () => {
		mockSyncSolution.mockResolvedValue(undefined);
		const entities = makeEntities();
		entities.solution = {
			...entities.solution,
			git_connected: true,
			git_repo_url: "https://github.com/acme/sol",
			version: "1.0.0",
			update_available_version: "1.1.0",
		} as unknown as typeof entities.solution;
		mockGetSolutionEntities.mockResolvedValue(entities);

		const { user } = await renderPage();
		await screen.findByTestId("solution-detail");

		await user.click(screen.getByTestId("update-now"));
		// Confirm dialog before the destructive pull/replace.
		await screen.findByTestId("update-now-dialog");
		await user.click(screen.getByTestId("confirm-update-now"));

		await waitFor(() =>
			expect(mockSyncSolution).toHaveBeenCalledWith("sol-1"),
		);
		// On success the entities query refetches (clears the badge once the
		// backend drops update_available_version).
		await waitFor(() =>
			expect(mockGetSolutionEntities.mock.calls.length).toBeGreaterThan(1),
		);
	});

	it("renders a Files chip in Contents when the install has files", async () => {
		const entities = makeEntities();
		(entities as Record<string, unknown>).files = [
			{ location: "solutions", path: "data/hello.txt", size: 2 },
		];
		mockGetSolutionEntities.mockResolvedValue(entities);
		const { user } = await renderPage();
		await screen.findByTestId("solution-detail");

		await user.click(screen.getByTestId("tab-contents"));
		const filesChip = screen.getByTestId("chip-files");
		expect(filesChip).toHaveTextContent("Files");
		expect(filesChip).toHaveTextContent("1");
	});

	it("navigates the Files chip to /files?install=<id>", async () => {
		const entities = makeEntities();
		(entities as Record<string, unknown>).files = [
			{ location: "solutions", path: "data/hello.txt", size: 2 },
		];
		mockGetSolutionEntities.mockResolvedValue(entities);
		const { user } = await renderPage();
		await screen.findByTestId("solution-detail");

		await user.click(screen.getByTestId("tab-contents"));
		await user.click(screen.getByTestId("chip-files"));

		// Files tab renders a Browse Files link pointing at the scoped Files page.
		const link = await screen.findByTestId("files-view-link");
		expect(link).toHaveAttribute(
			"href",
			"/files?install=sol-1&from=solution:sol-1",
		);
	});
});
