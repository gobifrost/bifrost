/**
 * Tests for TableDetail's solution-aware back-link. When the page is reached
 * from a Solution detail view (`?from=solution:{id}`) the back affordance
 * retargets to the Solution; otherwise it points at the tables list.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { Routes, Route } from "react-router-dom";
import { renderWithProviders, screen } from "@/test-utils";

const mockUseTable = vi.fn();
const mockUseDocuments = vi.fn();
const mockUseDeleteDocument = vi.fn();

vi.mock("@/services/tables", () => ({
	useTable: (...a: unknown[]) => mockUseTable(...a),
	useDocuments: (...a: unknown[]) => mockUseDocuments(...a),
	useDeleteDocument: (...a: unknown[]) => mockUseDeleteDocument(...a),
}));

vi.mock("@/components/tables/DocumentDialog", () => ({
	DocumentDialog: () => null,
}));

vi.mock("@/components/tables/TableFilterSidebar", () => ({
	TableFilterSidebar: () => null,
}));

const table = {
	id: "tbl-1",
	name: "Customers",
	description: "",
	organization_id: null,
	created_at: "2026-01-01T00:00:00Z",
};

beforeEach(() => {
	vi.clearAllMocks();
	mockUseTable.mockReturnValue({ data: table, isLoading: false });
	mockUseDocuments.mockReturnValue({
		data: { documents: [], total: 0 },
		isLoading: false,
		refetch: vi.fn(),
	});
	mockUseDeleteDocument.mockReturnValue({ mutateAsync: vi.fn() });
});

async function renderAtRoute(path: string) {
	const { TableDetail } = await import("./TableDetail");
	return renderWithProviders(
		<Routes>
			<Route path="/tables/:tableId" element={<TableDetail />} />
		</Routes>,
		{ initialEntries: [path] },
	);
}

describe("TableDetail — solution back-nav", () => {
	it("retargets the back-link to the solution with ?from=solution:", async () => {
		await renderAtRoute("/tables/tbl-1?from=solution:s1");
		const back = screen.getByRole("link", { name: /back to solution/i });
		expect(back).toHaveAttribute("href", "/solutions/s1");
	});

	it("points the back-link at /tables without ?from", async () => {
		await renderAtRoute("/tables/tbl-1");
		const back = screen.getByRole("link", { name: /back to tables/i });
		expect(back).toHaveAttribute("href", "/tables");
	});
});
