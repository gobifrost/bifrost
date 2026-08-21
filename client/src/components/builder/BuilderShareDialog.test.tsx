import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders, screen, waitFor } from "@/test-utils";
import { BuilderShareDialog } from "./BuilderShareDialog";

const mockList = vi.fn();
const mockSave = vi.fn();
const mockRemove = vi.fn();
const mockListRoles = vi.fn();
const mockListRoleGrants = vi.fn();
const mockSaveRoleGrant = vi.fn();
const mockRemoveRoleGrant = vi.fn();

vi.mock("@/services/builder", () => ({
	listBuilderCollaborators: (...args: unknown[]) => mockList(...args),
	saveBuilderCollaborator: (...args: unknown[]) => mockSave(...args),
	removeBuilderCollaborator: (...args: unknown[]) => mockRemove(...args),
	listBuilderGrantableRoles: (...args: unknown[]) => mockListRoles(...args),
	listBuilderRoleGrants: (...args: unknown[]) => mockListRoleGrants(...args),
	saveBuilderRoleGrant: (...args: unknown[]) => mockSaveRoleGrant(...args),
	removeBuilderRoleGrant: (...args: unknown[]) => mockRemoveRoleGrant(...args),
}));

beforeEach(() => {
	vi.clearAllMocks();
	mockList.mockResolvedValue([]);
	mockListRoles.mockResolvedValue([]);
	mockListRoleGrants.mockResolvedValue([]);
	mockSave.mockResolvedValue({
		id: "grant-1",
		user_id: "user-2",
		name: "Taylor Reviewer",
		email: "taylor@example.com",
		access: "edit",
		created_at: "2026-08-07T12:00:00Z",
		updated_at: "2026-08-07T12:00:00Z",
	});
});

describe("BuilderShareDialog", () => {
	it("explains the private collaboration boundary and invites an editor", async () => {
		const { user } = renderWithProviders(
			<BuilderShareDialog
				solutionId="sol-1"
				solutionName="Expense Tracker"
				open
				onOpenChange={vi.fn()}
			/>,
		);

		expect(await screen.findByText(/only you have access/i)).toBeInTheDocument();
		expect(screen.getByText(/same customer organization/i)).toBeInTheDocument();
		await user.type(screen.getByLabelText(/email address/i), "taylor@example.com");
		await user.click(screen.getByRole("button", { name: /^invite$/i }));

		await waitFor(() =>
			expect(mockSave).toHaveBeenCalledWith("sol-1", {
				email: "taylor@example.com",
				access: "edit",
			}),
		);
	});

	it("shows each existing collaborator with access and removal controls", async () => {
		mockList.mockResolvedValue([
			{
				id: "grant-1",
				user_id: "user-2",
				name: "Taylor Reviewer",
				email: "taylor@example.com",
				access: "view",
				created_at: "2026-08-07T12:00:00Z",
				updated_at: "2026-08-07T12:00:00Z",
			},
		]);
		mockRemove.mockResolvedValue(undefined);
		const { user } = renderWithProviders(
			<BuilderShareDialog solutionId="sol-1" solutionName="Expense Tracker" open onOpenChange={vi.fn()} />,
		);

		expect(await screen.findByText("Taylor Reviewer")).toBeInTheDocument();
		expect(screen.getByRole("combobox", { name: /access for taylor reviewer/i })).toHaveTextContent("Can view");
		await user.click(screen.getByRole("button", { name: /remove taylor reviewer/i }));
		await waitFor(() => expect(mockRemove).toHaveBeenCalledWith("sol-1", "user-2"));
	});

	it("grants an eligible Role access to the Solution", async () => {
		mockListRoles.mockResolvedValue([
			{ id: "role-1", name: "Customer Builders", assignable_to_resources: true },
		]);
		mockSaveRoleGrant.mockResolvedValue({
			id: "role-grant-1",
			role_id: "role-1",
			access: "view",
		});
		const { user } = renderWithProviders(
			<BuilderShareDialog solutionId="sol-1" solutionName="Expense Tracker" open onOpenChange={vi.fn()} />,
		);

		await user.click(await screen.findByRole("combobox", { name: /role with access/i }));
		await user.click(screen.getByRole("option", { name: "Customer Builders" }));
		await user.click(screen.getByRole("button", { name: /add role/i }));

		await waitFor(() =>
			expect(mockSaveRoleGrant).toHaveBeenCalledWith("sol-1", {
				role_id: "role-1",
				access: "view",
			}),
		);
	});
});
