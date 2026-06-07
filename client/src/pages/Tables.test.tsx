/**
 * Tests for the Tables list page "Show orphaned" toggle: toggling it threads
 * `include_orphaned` into the list fetch, and orphaned rows render an origin
 * badge.
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

const orphanedTable = {
	id: "tbl-1",
	name: "Leftover Customers",
	description: "",
	organization_id: null,
	created_at: "2026-01-01T00:00:00Z",
	orphaned_at: "2026-02-01T00:00:00Z",
	origin_solution_slug: "crm-sync",
};

beforeEach(() => {
	vi.clearAllMocks();
	mockUseTables.mockReturnValue({
		data: { tables: [orphanedTable] },
		isLoading: false,
		refetch: vi.fn(),
	});
	mockUseDeleteTable.mockReturnValue({ mutateAsync: vi.fn() });
});

async function renderPage() {
	const { Tables } = await import("./Tables");
	return renderWithProviders(<Tables />);
}

describe("Tables — Show orphaned toggle", () => {
	it("fetches without include_orphaned by default", async () => {
		await renderPage();
		// useTables(scope, includeOrphaned) — default off
		expect(mockUseTables).toHaveBeenLastCalledWith(undefined, false);
	});

	it("threads include_orphaned=true when toggled on", async () => {
		const { user } = await renderPage();
		await user.click(
			screen.getByRole("checkbox", { name: /show orphaned/i }),
		);
		expect(mockUseTables).toHaveBeenLastCalledWith(undefined, true);
	});

	it("badges an orphaned row with its origin solution", async () => {
		await renderPage();
		expect(screen.getByText(/orphaned · from crm-sync/i)).toBeInTheDocument();
	});
});
