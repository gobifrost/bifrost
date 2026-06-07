/**
 * Tests for the Config list page "Show orphaned" toggle: toggling it threads
 * `include_orphaned` into the list fetch, and orphaned rows render an origin
 * badge.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";

const mockUseConfigs = vi.fn();
const mockUseDeleteConfig = vi.fn();

vi.mock("@/hooks/useConfig", () => ({
	useConfigs: (...a: unknown[]) => mockUseConfigs(...a),
	useDeleteConfig: (...a: unknown[]) => mockUseDeleteConfig(...a),
}));

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({ isPlatformAdmin: false }),
}));

vi.mock("@/contexts/OrgScopeContext", () => ({
	useOrgScope: () => ({
		scope: { orgName: "Acme" },
		isGlobalScope: false,
	}),
}));

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => ({ data: [] }),
}));

vi.mock("@/components/config/ConfigDialog", () => ({
	ConfigDialog: () => null,
}));

vi.mock("@/components/ImportDialog", () => ({
	ImportDialog: () => null,
}));

const orphanedConfig = {
	id: "cfg-1",
	key: "api_token",
	value: "x",
	type: "string",
	scope: "global",
	org_id: null,
	description: "",
	integration_name: null,
	orphaned_at: "2026-02-01T00:00:00Z",
	origin_solution_slug: "crm-sync",
};

beforeEach(() => {
	vi.clearAllMocks();
	mockUseConfigs.mockReturnValue({
		data: [orphanedConfig],
		isFetching: false,
		refetch: vi.fn(),
	});
	mockUseDeleteConfig.mockReturnValue({ mutate: vi.fn() });
});

async function renderPage() {
	const { Config } = await import("./Config");
	return renderWithProviders(<Config />);
}

describe("Config — Show orphaned toggle", () => {
	it("fetches without include_orphaned by default", async () => {
		await renderPage();
		// useConfigs(scope, includeOrphaned) — default off
		expect(mockUseConfigs).toHaveBeenLastCalledWith(undefined, false);
	});

	it("threads include_orphaned=true when toggled on", async () => {
		const { user } = await renderPage();
		await user.click(
			screen.getByRole("checkbox", { name: /show orphaned/i }),
		);
		expect(mockUseConfigs).toHaveBeenLastCalledWith(undefined, true);
	});

	it("badges an orphaned row with its origin solution", async () => {
		await renderPage();
		expect(screen.getByText(/orphaned · from crm-sync/i)).toBeInTheDocument();
	});
});
