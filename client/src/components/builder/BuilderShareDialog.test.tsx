import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders, screen, waitFor } from "@/test-utils";
import { BuilderShareDialog } from "./BuilderShareDialog";

const mockList = vi.fn();
const mockSave = vi.fn();
const mockRemove = vi.fn();

vi.mock("@/services/builder", () => ({
	listBuilderCollaborators: (...args: unknown[]) => mockList(...args),
	saveBuilderCollaborator: (...args: unknown[]) => mockSave(...args),
	removeBuilderCollaborator: (...args: unknown[]) => mockRemove(...args),
}));

beforeEach(() => {
	vi.clearAllMocks();
	mockList.mockResolvedValue([]);
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
});
