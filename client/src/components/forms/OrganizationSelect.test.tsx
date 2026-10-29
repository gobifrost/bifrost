import { OrganizationSelect } from "./OrganizationSelect";
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
const mockUseAuthorization = vi.fn();

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: (...args: unknown[]) => useOrganizationsMock(...args),
}));
vi.mock("@/services/authorization", () => ({
	useAuthorization: () => mockUseAuthorization(),
}));

beforeEach(() => {
	useOrganizationsMock.mockReturnValue({ data: mockOrgs, isLoading: false });
	mockUseAuthorization.mockReturnValue({ authorization: undefined });
});

async function renderSelect(overrides: Record<string, unknown> = {}) {

	const onChange = vi.fn();
	const utils = renderWithProviders(
		<OrganizationSelect value={null} onChange={onChange} {...overrides} />,
	);
	return { ...utils, onChange };
}

describe("OrganizationSelect", () => {
	it("selects a known resource scope while organization discovery is disabled", async () => {
		const { user, onChange } = await renderSelect({
			loadOrganizations: false,
			knownOrganizations: [{ id: "contoso", name: "Contoso" }],
			showGlobal: false,
		});
		expect(useOrganizationsMock).toHaveBeenCalledWith({ enabled: false });
		await user.click(screen.getByRole("combobox"));
		expect(screen.queryByRole("option", { name: /^Global/ })).not.toBeInTheDocument();
		expect(screen.getAllByRole("option")).toHaveLength(1);
		await user.click(screen.getByRole("option", { name: "Contoso" }));
		expect(onChange).toHaveBeenCalledWith("contoso");
	});
	it("shows 'Global' when value is null", async () => {
		await renderSelect({ value: null });

		expect(screen.getByText("Global")).toBeInTheDocument();
	});

	it("shows the organization name when value is a real org id", async () => {
		await renderSelect({ value: "org-1" });

		expect(screen.getByText("Acme")).toBeInTheDocument();
	});

	it("shows the home organization label without offering an excluded destination", async () => {
		useOrganizationsMock.mockReturnValue({ data: [], isLoading: false });
		mockUseAuthorization.mockReturnValue({
			authorization: {
				home_organization_id: "home-org",
				home_organization_name: "Covi",
			},
		});
		const { user } = await renderSelect({
			value: "home-org",
			filterOrganizations: () => false,
		});

		expect(screen.getByText("Covi")).toBeVisible();
		await user.click(screen.getByRole("combobox"));
		expect(screen.queryByRole("option", { name: "Covi" })).toBeNull();
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
	it("describes an unavailable selection instead of waiting forever", async () => {
		useOrganizationsMock.mockReturnValue({ data: [], isLoading: false });
		await renderSelect({ value: "missing-organization" });
		expect(screen.getByText("Organization unavailable")).toBeVisible();
		expect(screen.getByText("missing-organization")).toBeVisible();
		expect(screen.queryByText("Loading...")).not.toBeInTheDocument();
	});
	it("forwards form identity and description to the trigger", async () => {
		const ref = { current: null };
		await renderSelect({
			label: "Customer scope",
			id: "customer-scope",
			"aria-describedby": "scope-help",
			ref,
		});
		const trigger = screen.getByRole("combobox", {
			name: "Customer scope",
		});
		expect(trigger).toHaveAttribute("id", "customer-scope");
		expect(trigger).toHaveAttribute("aria-describedby", "scope-help");
		expect(ref.current).toBe(trigger);
	});
});

 it("offers personal scope only when enabled and returns its explicit value", async () => {
 const { PERSONAL_SCOPE } = await import("./OrganizationSelect");
 const { user, onChange } = await renderSelect({ showPersonal: true });
 await user.click(screen.getByRole("combobox"));
 await user.click(screen.getByRole("option", { name: /Only me/ }));
 expect(onChange).toHaveBeenCalledWith(PERSONAL_SCOPE);
 });
