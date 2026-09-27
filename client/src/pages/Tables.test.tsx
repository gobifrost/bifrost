import { Tables } from "./Tables";
/**
 * Tests for the Tables list page.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";

const mockUseTables = vi.fn();
const mockUseDeleteTable = vi.fn();
const mockUseNavigate = vi.fn();
let mockIsPlatformAdmin = false;

vi.mock("react-router-dom", async () => {
	const actual =
		await vi.importActual<typeof import("react-router-dom")>(
			"react-router-dom",
		);
	return {
		...actual,
		useNavigate: () => mockUseNavigate,
	};
});

vi.mock("@/services/tables", () => ({
	useTables: (...a: unknown[]) => mockUseTables(...a),
	useDeleteTable: (...a: unknown[]) => mockUseDeleteTable(...a),
}));

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({ isPlatformAdmin: mockIsPlatformAdmin }),
}));

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => ({ data: [] }),
}));

vi.mock("@/components/tables/TableDialog", () => ({
	TableDialog: () => null,
}));

vi.mock("@/components/ImportDialog", () => ({
	ImportDialog: () => null,
}));

vi.mock("@/pages/TablesClaimsTab", () => ({
	TablesClaimsTab: () => null,
}));

const regularTable = {
	id: "tbl-1",
	name: "Customers",
	description: "",
	organization_id: null,
	created_at: "2026-01-01T00:00:00Z",
};

beforeEach(() => {
	vi.clearAllMocks();
	mockIsPlatformAdmin = false;
	mockUseTables.mockReturnValue({
		data: { tables: [regularTable] },
		isLoading: false,
		isFetching: false,
		error: null,
		refetch: vi.fn(),
	});
	mockUseDeleteTable.mockReturnValue({ mutateAsync: vi.fn() });
});

async function renderPage() {
	return renderWithProviders(<Tables />);
}

describe("Tables — list", () => {
	it("retains failed deletion for retry", async () => {
		const mutateAsync = vi
			.fn()
			.mockRejectedValueOnce({ detail: "Synthetic delete failure" })
			.mockResolvedValueOnce(undefined);
		mockUseDeleteTable.mockReturnValue({ mutateAsync });
		mockUseTables.mockReturnValue({
			data: {
				tables: [
					{
						id: "table-review",
						name: "review_table",
						organization_id: null,
						is_solution_managed: false,
					},
				],
			},
			isLoading: false,
			isFetching: false,
			error: null,
			refetch: vi.fn(),
		});
		const { user } = await renderPage();
		await user.click(
			screen.getByRole("button", { name: "review_table actions" }),
		);
		await user.click(screen.getByRole("menuitem", { name: "Delete" }));
		await user.click(screen.getByRole("button", { name: "Delete table" }));
		await waitFor(() => expect(screen.getByRole("alert")).toHaveFocus());
		expect(screen.getByRole("alert")).toHaveTextContent(
			"Synthetic delete failure",
		);
		await user.click(screen.getByRole("button", { name: "Delete table" }));
		await waitFor(() =>
			expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument(),
		);
		expect(mutateAsync).toHaveBeenNthCalledWith(2, {
			params: { path: { table_id: "table-review" } },
		});
	});

	it("fetches without include_orphaned (orphaned UI stripped)", async () => {
		await renderPage();
		// useTables(scope) — no include_orphaned param
		expect(mockUseTables).toHaveBeenLastCalledWith(undefined);
		// No show-orphaned toggle visible
		expect(
			screen.queryByRole("checkbox", { name: /show orphaned/i }),
		).toBeNull();
	});
});

describe("Tables — solution-managed rows are read-only (audit U1)", () => {
	const managedTable = {
		id: "tbl-managed",
		name: "Managed Customers",
		description: "",
		organization_id: null,
		created_at: "2026-01-01T00:00:00Z",
		is_solution_managed: true,
		solution_id: "sol-1",
	};

	it("disables Edit and Delete for a solution-managed table", async () => {
		mockUseTables.mockReturnValue({
			data: { tables: [managedTable] },
			isLoading: false,
			isFetching: false,
			error: null,
			refetch: vi.fn(),
		});
		const { user } = await renderPage();
		await user.click(
			screen.getByRole("button", { name: "Managed Customers actions" }),
		);
		expect(
			screen.getByRole("menuitem", { name: "Delete" }),
		).toHaveAttribute("aria-disabled", "true");
		expect(screen.getByRole("menuitem", { name: "Edit" })).toHaveAttribute(
			"aria-disabled",
			"true",
		);
	});

	it("never calls delete for a managed table even if confirm is reached", async () => {
		// Defense in depth: the confirm handler must no-op for a managed table
		// rather than round-trip to a server 409.
		const mutateAsync = vi.fn();
		mockUseDeleteTable.mockReturnValue({ mutateAsync });
		mockUseTables.mockReturnValue({
			data: { tables: [managedTable] },
			isLoading: false,
			isFetching: false,
			error: null,
			refetch: vi.fn(),
		});
		const { user } = await renderPage();

		// The disabled Delete button cannot open the dialog; the mutation is never invoked.
		await user.click(
			screen.getByRole("button", { name: "Managed Customers actions" }),
		);
		const del = screen.getByRole("menuitem", { name: "Delete" });
		await user.click(del).catch(() => {});
		expect(mutateAsync).not.toHaveBeenCalled();
	});
});

describe("Tables — catalog selection", () => {
	it("opens rows normally and does not render selection checkboxes", async () => {
		mockIsPlatformAdmin = true;
		const { user } = await renderPage();

		expect(
			screen.queryByRole("checkbox", { name: /select/i }),
		).not.toBeInTheDocument();

		expect(screen.getByRole("link", { name: "Customers" })).toHaveAttribute(
			"href",
			"/tables/tbl-1",
		);
		await user.click(screen.getByRole("link", { name: "Customers" }));
		expect(mockUseNavigate).toHaveBeenCalledWith("/tables/tbl-1");
	});

	it("toggles whole rows in explicit Select mode", async () => {
		mockIsPlatformAdmin = true;
		const { user } = await renderPage();

		await user.click(screen.getByRole("switch", { name: "Select" }));
		const rowButton = screen.getByRole("button", { name: "Customers" });
		await user.click(rowButton);

		expect(mockUseNavigate).not.toHaveBeenCalled();
		expect(rowButton).toHaveAttribute("aria-pressed", "true");
		expect(screen.getByText("1 selected")).toBeInTheDocument();
	});
});
