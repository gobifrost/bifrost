import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => ({
		data: [
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
		],
		isLoading: false,
		refetch: vi.fn(),
	}),
	useCreateOrganization: () => ({ isPending: false, mutateAsync: vi.fn() }),
	useUpdateOrganization: () => ({ isPending: false, mutateAsync: vi.fn() }),
	useDeleteOrganization: () => ({ isPending: false, mutateAsync: vi.fn() }),
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

describe("Organizations", () => {
	it("opens the required-instructions editor for a selected organization", async () => {
		const user = userEvent.setup();
		render(<Organizations />);

		await user.click(
			screen.getByRole("button", { name: "Edit required instructions" }),
		);

		expect(
			screen.getByRole("heading", { name: "Acme" }),
		).toBeVisible();
		expect(screen.getByText("Instructions for org-1")).toBeVisible();
	});
});
