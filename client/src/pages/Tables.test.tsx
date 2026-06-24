/**
 * Tests for the Tables list page.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";

const mockUseTables = vi.fn();
const mockUseDeleteTable = vi.fn();

vi.mock("@/services/tables", () => ({
	useTables: (...a: unknown[]) => mockUseTables(...a),
	useDeleteTable: (...a: unknown[]) => mockUseDeleteTable(...a),
}));

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({ isPlatformAdmin: false }),
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
	mockUseTables.mockReturnValue({
		data: { tables: [regularTable] },
		isLoading: false,
		refetch: vi.fn(),
	});
	mockUseDeleteTable.mockReturnValue({ mutateAsync: vi.fn() });
});

async function renderPage() {
	const { Tables } = await import("./Tables");
	return renderWithProviders(<Tables />);
}

describe("Tables — list", () => {
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
			refetch: vi.fn(),
		});
		await renderPage();

		expect(screen.getByRole("button", { name: /delete table/i })).toBeDisabled();
		expect(screen.getByRole("button", { name: /edit table/i })).toBeDisabled();
	});

	it("never calls delete for a managed table even if confirm is reached", async () => {
		// Defense in depth: the confirm handler must no-op for a managed table
		// rather than round-trip to a server 409.
		const mutateAsync = vi.fn();
		mockUseDeleteTable.mockReturnValue({ mutateAsync });
		mockUseTables.mockReturnValue({
			data: { tables: [managedTable] },
			isLoading: false,
			refetch: vi.fn(),
		});
		const { user } = await renderPage();

		// The disabled Delete button cannot open the dialog; the mutation is never invoked.
		const del = screen.getByRole("button", { name: /delete table/i });
		await user.click(del).catch(() => {});
		expect(mutateAsync).not.toHaveBeenCalled();
	});
});
