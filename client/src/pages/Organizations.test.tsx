import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { mockUseOrganizations, mockUpdate } = vi.hoisted(() => ({
	mockUseOrganizations: vi.fn(),
	mockUpdate: vi.fn(),
}));

const organizations = [
	{
		id: "org-1",
		name: "Acme",
		domain: "acme.example",
		is_active: true,
		is_provider: false,
		settings: {},
		created_at: "2026-08-13T00:00:00Z",
		updated_at: "2026-08-13T00:00:00Z",
		created_by: "admin@example.com",
	},
	{
		id: "org-2",
		name: "Dormant Co",
		domain: "dormant.example",
		is_active: false,
		is_provider: false,
		settings: {},
		created_at: "2026-08-12T00:00:00Z",
		updated_at: "2026-08-12T00:00:00Z",
		created_by: "admin@example.com",
	},
];

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: (options?: { includeInactive?: boolean }) => {
		mockUseOrganizations(options);
		return {
			data: options?.includeInactive
				? organizations
				: organizations.filter((org) => org.is_active),
			isLoading: false,
			refetch: vi.fn(),
		};
	},
	useCreateOrganization: () => ({ isPending: false, mutateAsync: vi.fn() }),
	useUpdateOrganization: () => ({
		isPending: false,
		mutateAsync: mockUpdate,
	}),
}));

vi.mock("@/hooks/useSearch", () => ({
	useSearch: (items: unknown[]) => items,
}));

vi.mock("@/pages/settings/RequiredInstructionsSettings", () => ({
	RequiredInstructionsSettings: ({ organizationId }: { organizationId: string }) => (
		<div>Instructions for {organizationId}</div>
	),
}));

import { Organizations } from "./Organizations";

beforeEach(() => {
	mockUseOrganizations.mockClear();
	mockUpdate.mockReset();
	mockUpdate.mockResolvedValue({});
});

describe("Organizations", () => {
	it("hides inactive organizations until requested", async () => {
		const user = userEvent.setup();
		render(<Organizations />);

		expect(screen.getByText("Acme")).toBeVisible();
		expect(screen.queryByText("Dormant Co")).not.toBeInTheDocument();

		await user.click(screen.getByRole("switch", { name: "Show Inactive" }));

		expect(screen.getByText("Dormant Co")).toBeVisible();
		expect(mockUseOrganizations).toHaveBeenLastCalledWith({
			includeInactive: true,
		});
	});

	it("opens a tabbed editor from the organization row", async () => {
		const user = userEvent.setup();
		render(<Organizations />);

		await user.click(screen.getByRole("row", { name: /Acme/ }));

		expect(
			screen.getByRole("heading", { name: "Edit Organization" }),
		).toBeVisible();
		expect(screen.getByRole("tab", { name: "General" })).toBeVisible();

		await user.click(screen.getByRole("tab", { name: "Instructions" }));

		expect(screen.getByText("Instructions for org-1")).toBeVisible();
	});

	it("saves organization status from the General tab", async () => {
		const user = userEvent.setup();
		render(<Organizations />);

		await user.click(screen.getByRole("row", { name: /Acme/ }));
		await user.click(
			screen.getByRole("switch", { name: "Organization Status" }),
		);
		await user.click(screen.getByRole("button", { name: "Save Changes" }));

		await waitFor(() =>
			expect(mockUpdate).toHaveBeenCalledWith({
				params: { path: { org_id: "org-1" } },
				body: {
					name: "Acme",
					domain: "acme.example",
					is_active: false,
				},
			}),
		);
	});

	it("offers a reversible disable action from the row menu", async () => {
		const user = userEvent.setup();
		render(<Organizations />);

		await user.click(screen.getByRole("button", { name: "Acme actions" }));
		await user.click(
			screen.getByRole("menuitem", { name: "Disable Acme" }),
		);
		expect(
			screen.getByRole("heading", { name: "Disable Acme?" }),
		).toBeVisible();

		await user.click(screen.getByRole("button", { name: "Disable" }));

		await waitFor(() =>
			expect(mockUpdate).toHaveBeenCalledWith({
				params: { path: { org_id: "org-1" } },
				body: { is_active: false },
			}),
		);
	});
});
