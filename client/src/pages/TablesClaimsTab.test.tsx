import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";

const mockListClaims = vi.fn();
const mockCreateClaim = vi.fn();
const mockUpdateClaim = vi.fn();
const mockDeleteClaim = vi.fn();

vi.mock("@/services/claims", () => ({
	listClaims: (...args: unknown[]) => mockListClaims(...args),
	createClaim: (...args: unknown[]) => mockCreateClaim(...args),
	updateClaim: (...args: unknown[]) => mockUpdateClaim(...args),
	deleteClaim: (...args: unknown[]) => mockDeleteClaim(...args),
}));

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({
		isPlatformAdmin: true,
		user: {
			id: "dev-user",
			email: "dev@gobifrost.com",
			organizationId: "22222222-2222-4222-8222-222222222222",
			isSuperuser: true,
		},
	}),
}));

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => ({ data: [] }),
}));

import { TablesClaimsTab } from "./TablesClaimsTab";

beforeEach(() => {
	mockListClaims.mockReset();
	mockCreateClaim.mockReset();
	mockUpdateClaim.mockReset();
	mockDeleteClaim.mockReset();
});

describe("TablesClaimsTab", () => {
	it("lists claims fetched from the service", async () => {
		mockListClaims.mockResolvedValue({
			claims: [
				{
					id: "11111111-1111-4111-8111-111111111111",
					organization_id: "22222222-2222-4222-8222-222222222222",
					name: "allowed_campus_ids",
					type: "list",
					description: null,
					query: { table: "user_campus_access", select: "campus_id" },
				},
			],
		});

		renderWithProviders(<TablesClaimsTab />);

		await waitFor(() =>
			expect(screen.getByText("allowed_campus_ids")).toBeVisible(),
		);
		expect(mockListClaims).toHaveBeenCalledTimes(1);
	});

	it("deletes a claim through the confirmation dialog with scope", async () => {
		mockListClaims
			.mockResolvedValueOnce({
				claims: [
					{
						id: "11111111-1111-4111-8111-111111111111",
						organization_id:
							"22222222-2222-4222-8222-222222222222",
						name: "allowed_campus_ids",
						type: "list",
						description: null,
						query: {
							table: "user_campus_access",
							select: "campus_id",
						},
					},
				],
			})
			.mockResolvedValueOnce({ claims: [] });
		mockDeleteClaim.mockResolvedValue(undefined);

		const { user } = renderWithProviders(<TablesClaimsTab />);
		await screen.findByText("allowed_campus_ids");

		await user.click(screen.getByRole("button", { name: /delete claim/i }));
		// Confirmation dialog appears — accept it.
		await user.click(screen.getByRole("button", { name: /^delete$/i }));

		await waitFor(() =>
			expect(mockDeleteClaim).toHaveBeenCalledWith(
				"allowed_campus_ids",
				{ scope: "22222222-2222-4222-8222-222222222222" },
			),
		);
		expect(mockListClaims).toHaveBeenCalledTimes(2);
	});
});
