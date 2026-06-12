/**
 * Tests for the polished Solution detail view — breadcrumb, tab counts, the
 * required-config warning banner, entity links carrying `?from=solution:`, and
 * the Configs tab as the config-value entry surface.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";

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

const mockGetSolutionEntities = vi.fn();
const mockUpdateSolution = vi.fn();
const mockDeleteSolution = vi.fn();
const mockSetSolutionConfig = vi.fn();
vi.mock("@/services/solutions", () => ({
	getSolutionEntities: (...a: unknown[]) => mockGetSolutionEntities(...a),
	updateSolution: (...a: unknown[]) => mockUpdateSolution(...a),
	deleteSolution: (...a: unknown[]) => mockDeleteSolution(...a),
	setSolutionConfig: (...a: unknown[]) => mockSetSolutionConfig(...a),
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
		workflows: [{ id: "wf-1", name: "Sync Tickets" }],
		apps: [],
		forms: [],
		agents: [],
		tables: [{ id: "tbl-1", name: "Customers" }],
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

	it("renders tabs with counts", async () => {
		await renderPage();
		await screen.findByTestId("solution-detail");

		const tables = screen.getByTestId("tab-tables");
		expect(tables).toHaveTextContent("Tables");
		expect(tables).toHaveTextContent("1");

		const workflows = screen.getByTestId("tab-workflows");
		expect(workflows).toHaveTextContent("Workflows");
		expect(workflows).toHaveTextContent("1");

		const configs = screen.getByTestId("tab-configs");
		expect(configs).toHaveTextContent("Configs");
		expect(configs).toHaveTextContent("2");
	});

	it("shows the required-unset config warning banner", async () => {
		await renderPage();
		expect(
			await screen.findByTestId("required-config-warning"),
		).toBeInTheDocument();
		expect(
			screen.getByText(/1 required config needs a value/i),
		).toBeInTheDocument();
	});

	it("navigates a table row to its entity page with ?from=solution:", async () => {
		const { user } = await renderPage();
		await screen.findByTestId("solution-detail");

		await user.click(screen.getByTestId("tab-tables"));
		// Entity tabs render the shared DataTable (Roles paradigm): rows are
		// clickable and navigate, carrying the from=solution backlink.
		await user.click(screen.getByRole("row", { name: /customers/i }));
		expect(mockNavigate).toHaveBeenCalledWith(
			"/tables/tbl-1?from=solution:sol-1",
		);
	});

	it("filters entity rows with the tab's search box", async () => {
		const { user } = await renderPage();
		await screen.findByTestId("solution-detail");

		await user.click(screen.getByTestId("tab-tables"));
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

	it("shows Set/Not set status and config inputs on the Configs tab", async () => {
		const { user } = await renderPage();
		await screen.findByTestId("solution-detail");

		await user.click(screen.getByTestId("tab-configs"));

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

		await user.click(screen.getByTestId("tab-configs"));
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
});
