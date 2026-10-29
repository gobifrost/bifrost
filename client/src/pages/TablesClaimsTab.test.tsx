import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";

const mockListClaims = vi.fn();
const mockCreateClaim = vi.fn();
const mockUpdateClaim = vi.fn();
const mockDeleteClaim = vi.fn();
const mockUseAuth = vi.fn();
const mockUseAuthorization = vi.fn();
const mockUseOrganizations = vi.fn();

vi.mock("@/services/claims", () => ({
	listClaims: (...args: unknown[]) => mockListClaims(...args),
	createClaim: (...args: unknown[]) => mockCreateClaim(...args),
	updateClaim: (...args: unknown[]) => mockUpdateClaim(...args),
	deleteClaim: (...args: unknown[]) => mockDeleteClaim(...args),
}));

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => mockUseAuth(),
}));

vi.mock("@/services/authorization", () => ({
	useAuthorization: () => mockUseAuthorization(),
}));

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: (...args: unknown[]) => mockUseOrganizations(...args),
}));

import { TablesClaimsTab } from "./TablesClaimsTab";

beforeEach(() => {
	mockListClaims.mockReset();
	mockCreateClaim.mockReset();
	mockUpdateClaim.mockReset();
	mockDeleteClaim.mockReset();
	mockUseAuth.mockReturnValue({
		isPlatformAdmin: true,
		user: {
			id: "dev-user",
			email: "dev@gobifrost.com",
			organizationId: "22222222-2222-4222-8222-222222222222",
			isSuperuser: true,
		},
	});
	mockUseAuthorization.mockReturnValue({
		canAnywhere: () => true,
		canAt: () => true,
	});
	mockUseOrganizations.mockReturnValue({ data: [] });
});

describe("TablesClaimsTab", () => {
	it("uses a compact loading status while claims load", () => {
		mockListClaims.mockReturnValue(new Promise(() => undefined));

		renderWithProviders(<TablesClaimsTab />);

		expect(
			screen.getByRole("status", { name: "Loading custom claims" }),
		).toHaveTextContent("Loading custom claims");
		expect(
			screen.queryByText("No custom claims yet"),
		).not.toBeInTheDocument();
	});

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
						organization_id: "22222222-2222-4222-8222-222222222222",
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

		await user.click(
			screen.getByRole("button", { name: "allowed_campus_ids actions" }),
		);
		await user.click(screen.getByRole("menuitem", { name: "Delete" }));
		// Confirmation dialog appears — accept it.
		await user.click(screen.getByRole("button", { name: /^delete$/i }));

		await waitFor(() =>
			expect(mockDeleteClaim).toHaveBeenCalledWith("allowed_campus_ids", {
				scope: "22222222-2222-4222-8222-222222222222",
			}),
		);
		expect(mockListClaims).toHaveBeenCalledTimes(2);
	});

	it("shows the Managed badge and hides Edit/Delete for solution-managed claims", async () => {
		mockListClaims.mockResolvedValue({
			claims: [
				{
					id: "33333333-3333-4333-8333-333333333333",
					organization_id: "22222222-2222-4222-8222-222222222222",
					solution_id: "44444444-4444-4444-8444-444444444444",
					is_solution_managed: true,
					name: "managed_claim",
					type: "list",
					description: null,
					query: { table: "managed_table", select: "id" },
				},
				{
					id: "11111111-1111-4111-8111-111111111111",
					organization_id: "22222222-2222-4222-8222-222222222222",
					solution_id: null,
					is_solution_managed: false,
					name: "loose_claim",
					type: "list",
					description: null,
					query: { table: "user_campus_access", select: "campus_id" },
				},
			],
		});

		renderWithProviders(<TablesClaimsTab />);
		await screen.findByText("managed_claim");

		// Managed claim shows the badge; the loose claim does not.
		expect(screen.getAllByTestId("solution-managed-badge")).toHaveLength(1);

		expect(
			screen.getByRole("button", { name: "loose_claim actions" }),
		).toBeVisible();
		expect(
			screen.queryByRole("button", { name: "managed_claim actions" }),
		).not.toBeInTheDocument();
	});

	it("uses claim grants for create and row actions, excluding other organizations", async () => {
		mockUseAuth.mockReturnValue({
			isPlatformAdmin: false,
			user: {
				id: "role-user",
				email: "role@example.com",
				organizationId: "org-allowed",
				isSuperuser: false,
			},
		});
		mockUseAuthorization.mockReturnValue({
			canAnywhere: (permission: string) =>
				permission === "claims.read" || permission === "claims.readwrite",
			canAt: (permission: string, target: { kind: string; id?: string }) =>
				permission === "claims.readwrite" &&
				target.kind === "org" &&
				target.id === "org-allowed",
		});
		mockListClaims.mockResolvedValue({
			claims: [
				{
					id: "claim-allowed",
					organization_id: "org-allowed",
					name: "allowed_claim",
					type: "list",
					query: { table: "records", select: "id" },
				},
				{
					id: "claim-blocked",
					organization_id: "org-blocked",
					name: "blocked_claim",
					type: "list",
					query: { table: "records", select: "id" },
				},
			],
		});

		renderWithProviders(<TablesClaimsTab />);

		await screen.findByText("allowed_claim");
		expect(screen.getByRole("button", { name: "Add Claim" })).toBeVisible();
		expect(
			screen.getByRole("button", { name: "allowed_claim actions" }),
		).toBeVisible();
		expect(
			screen.queryByRole("button", { name: "blocked_claim actions" }),
		).not.toBeInTheDocument();
	});

	it("keeps claims read-only without a claim write grant", async () => {
		mockUseAuth.mockReturnValue({
			isPlatformAdmin: false,
			user: {
				id: "reader-user",
				email: "reader@example.com",
				organizationId: "org-reader",
				isSuperuser: false,
			},
		});
		mockUseAuthorization.mockReturnValue({
			canAnywhere: (permission: string) => permission === "claims.read",
			canAt: () => false,
		});
		mockListClaims.mockResolvedValue({
			claims: [
				{
					id: "claim-reader",
					organization_id: "org-reader",
					name: "read_only_claim",
					type: "list",
					query: { table: "records", select: "id" },
				},
			],
		});

		renderWithProviders(<TablesClaimsTab />);

		await screen.findByText("read_only_claim");
		expect(
			screen.queryByRole("button", { name: "Add Claim" }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: "read_only_claim actions" }),
		).not.toBeInTheDocument();
	});
	it("offers retry after an initial failure without showing a false empty state", async () => {
		mockListClaims
			.mockRejectedValueOnce(new Error("Synthetic failure"))
			.mockResolvedValueOnce({ claims: [] });
		const { user } = renderWithProviders(<TablesClaimsTab />);
		await screen.findByText("Custom claims could not be loaded");
		expect(
			screen.queryByText("No custom claims yet"),
		).not.toBeInTheDocument();
		await user.click(screen.getByRole("button", { name: "Retry claims" }));
		await screen.findByText("No custom claims yet");
		expect(
			screen.queryByText("Custom claims could not be loaded"),
		).not.toBeInTheDocument();
	});
	it("retains loaded claims after a failed refresh", async () => {
		mockListClaims
			.mockResolvedValueOnce({
				claims: [
					{
						id: "claim-1",
						name: "review_access",
						organization_id: null,
						type: "list",
						query: { table: "records", select: "id" },
					},
				],
			})
			.mockRejectedValueOnce(new Error("Synthetic failure"));
		const { user } = renderWithProviders(<TablesClaimsTab />);
		await screen.findByText("review_access");
		await user.click(
			screen.getByRole("button", { name: "Refresh claims" }),
		);
		await screen.findByText(
			"Showing the last loaded claims. Refresh to get the latest changes.",
		);
		expect(screen.getByText("review_access")).toBeVisible();
	});
});
