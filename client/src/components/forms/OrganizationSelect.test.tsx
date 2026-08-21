/**
 * Component tests for OrganizationSelect.
 *
 * Wraps Popover + Command + useOrganizations. We mock the hook and focus on
 * value-mapping (null → "Global", undefined → "All", real IDs pass through)
 * and the new searchability behavior (typing filters the list, domain hits
 * work, disabled blocks the popover).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";

const mockOrgs = [
	{
		id: "org-1",
		name: "Acme",
		domain: "acme.com",
		is_provider: false,
	},
	{
		id: "org-2",
		name: "Globex",
		domain: null,
		is_provider: true,
	},
];

const useOrganizationsMock = vi.fn();
const useAuthorizationBoundaryMock = vi.fn();

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => useOrganizationsMock(),
}));
vi.mock("@/contexts/AuthorizationBoundaryContext", () => ({
	useAuthorizationBoundary: () => useAuthorizationBoundaryMock(),
}));

beforeEach(() => {
	useOrganizationsMock.mockReturnValue({ data: mockOrgs, isLoading: false });
	useAuthorizationBoundaryMock.mockReturnValue({
		selectedBoundary: "organization:org-1",
		selectedTarget: { boundary: "organization:org-1", capabilities: [] },
		targets: [{ boundary: "organization:org-1", capabilities: [] }],
		hasSelectedCapability: () => true,
	});
});

async function renderSelect(overrides: Record<string, unknown> = {}) {
	const { OrganizationSelect } = await import("./OrganizationSelect");
	const onChange = vi.fn();
	const utils = renderWithProviders(
		<OrganizationSelect value={null} onChange={onChange} {...overrides} />,
	);
	return { ...utils, onChange };
}

describe("OrganizationSelect", () => {
	it("shows 'Global' when value is null", async () => {
		await renderSelect({ value: null });

		expect(screen.getByText("Global")).toBeInTheDocument();
	});

	it("shows the organization name when value is a real org id", async () => {
		await renderSelect({ value: "org-1" });

		expect(screen.getByText("Acme")).toBeInTheDocument();
	});

	it("shows 'All' when value is undefined and showAll is true", async () => {
		await renderSelect({ value: undefined, showAll: true });

		expect(screen.getByText("All")).toBeInTheDocument();
	});

	it("disables the trigger while organizations are loading", async () => {
		useOrganizationsMock.mockReturnValueOnce({ data: [], isLoading: true });

		await renderSelect({ value: "org-not-loaded-yet" });

		// The component is disabled because isLoading is true.
		expect(screen.getByRole("combobox")).toBeDisabled();
	});

	it("disables the trigger when the disabled prop is true", async () => {
		await renderSelect({ value: null, disabled: true });

		const trigger = screen.getByRole("combobox");
		expect(trigger).toBeDisabled();
	});

	it("filters organizations by search input", async () => {
		const { user } = await renderSelect({ value: null });

		await user.click(screen.getByRole("combobox"));
		const searchInput = await screen.findByPlaceholderText(
			"Search organizations...",
		);

		// Both orgs visible before filtering.
		expect(screen.getByText("Acme")).toBeInTheDocument();
		expect(screen.getByText("Globex")).toBeInTheDocument();

		await user.type(searchInput, "acme");

		await waitFor(() => {
			expect(screen.queryByText("Globex")).not.toBeInTheDocument();
		});
		expect(screen.getByText("Acme")).toBeInTheDocument();
	});

	it("finds an organization by its domain", async () => {
		const { user } = await renderSelect({ value: null });

		await user.click(screen.getByRole("combobox"));
		const searchInput = await screen.findByPlaceholderText(
			"Search organizations...",
		);

		await user.type(searchInput, "acme.com");

		await waitFor(() => {
			expect(screen.queryByText("Globex")).not.toBeInTheDocument();
		});
		expect(screen.getByText("Acme")).toBeInTheDocument();
	});

	it("keeps Global above the org list and makes it filterable", async () => {
		const { user } = await renderSelect({ value: null, showGlobal: true });

		await user.click(screen.getByRole("combobox"));
		const searchInput = await screen.findByPlaceholderText(
			"Search organizations...",
		);

		// Global is present in the list alongside orgs initially.
		const items = screen.getAllByRole("option");
		expect(items[0]).toHaveTextContent(/global/i);

		// Typing "global" should keep only the Global option.
		await user.type(searchInput, "global");
		await waitFor(() => {
			expect(screen.queryByText("Acme")).not.toBeInTheDocument();
		});
		expect(screen.getAllByText("Global").length).toBeGreaterThan(0);
	});

	it("does not open the popover when disabled", async () => {
		const { user } = await renderSelect({ value: null, disabled: true });

		await user.click(screen.getByRole("combobox"));

		// Disabled trigger means the popover never opens, so the search input
		// never mounts.
		expect(
			screen.queryByPlaceholderText("Search organizations..."),
		).not.toBeInTheDocument();
	});
});
