/**
 * Edit-mode tests for CreateEditSolution — the Organization selector replaces
 * the bespoke scope select, and git connection is DERIVED from the repo URL
 * (no manual "git connected" toggle).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, within } from "@/test-utils";
import { waitFor } from "@testing-library/react";
import { CreateEditSolution } from "./CreateEditSolution";
import type { Solution } from "@/services/solutions";

vi.mock("sonner", () => ({
	toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => ({
		data: [{ id: "org-1", name: "Acme Corp" }],
	}),
}));

const ghConfig = { data: { configured: true, token_saved: true }, isLoading: false };
const mockCreateRepoMutate = vi.fn();
vi.mock("@/hooks/useGitHub", () => ({
	useGitHubConfig: () => ghConfig,
	useCreateGitHubRepository: () => ({
		mutate: mockCreateRepoMutate,
		isPending: false,
	}),
}));

const mockUpdateSolution = vi.fn();
vi.mock("@/services/solutions", () => ({
	installSolution: vi.fn(),
	previewInstall: vi.fn(),
	updateSolution: (...a: unknown[]) => mockUpdateSolution(...a),
}));

function makeSolution(overrides: Partial<Solution> = {}): Solution {
	return {
		id: "sol-1",
		slug: "my-solution",
		name: "My Solution",
		organization_id: null,
		global_repo_access: false,
		git_connected: false,
		git_repo_url: null,
		scope: "global",
		...overrides,
	} as Solution;
}

beforeEach(() => {
	vi.clearAllMocks();
});

function renderEdit(solution: Solution) {
	const onSaved = vi.fn();
	const utils = renderWithProviders(
		<CreateEditSolution
			mode={{ kind: "edit", solution }}
			open
			onClose={vi.fn()}
			onSaved={onSaved}
		/>,
	);
	return { ...utils, onSaved };
}

describe("CreateEditSolution — edit mode", () => {
	it("has the Organization selector and NO git-connected toggle", async () => {
		renderEdit(makeSolution());

		const dialog = await screen.findByTestId("solution-dialog");
		expect(within(dialog).getByText("Organization")).toBeInTheDocument();
		// The old manual toggle is gone — connection is derived from the URL.
		expect(within(dialog).queryByLabelText(/git connected/i)).toBeNull();
		expect(within(dialog).getByTestId("git-section")).toBeInTheDocument();
		expect(within(dialog).getByText("Not connected")).toBeInTheDocument();
	});

	it("derives git_connected from the repo URL on save", async () => {
		mockUpdateSolution.mockResolvedValue(makeSolution());
		const { user, onSaved } = renderEdit(makeSolution());

		const dialog = await screen.findByTestId("solution-dialog");
		await user.type(
			within(dialog).getByTestId("git-repo-url"),
			"https://github.com/acme/solution-my-solution-x1",
		);
		await user.click(
			within(dialog).getByRole("button", { name: /save changes/i }),
		);

		await waitFor(() =>
			expect(mockUpdateSolution).toHaveBeenCalledWith("sol-1", {
				git_repo_url: "https://github.com/acme/solution-my-solution-x1",
				git_connected: true,
			}),
		);
		expect(onSaved).toHaveBeenCalled();
	});

	it("clearing the repo URL disconnects git on save", async () => {
		mockUpdateSolution.mockResolvedValue(makeSolution());
		const { user } = renderEdit(
			makeSolution({
				git_connected: true,
				git_repo_url: "https://github.com/acme/old-repo",
			}),
		);

		const dialog = await screen.findByTestId("solution-dialog");
		expect(within(dialog).getByText("Connected")).toBeInTheDocument();
		await user.clear(within(dialog).getByTestId("git-repo-url"));
		await user.click(
			within(dialog).getByRole("button", { name: /save changes/i }),
		);

		await waitFor(() =>
			expect(mockUpdateSolution).toHaveBeenCalledWith("sol-1", {
				git_repo_url: null,
				git_connected: false,
			}),
		);
	});

	it("offers to create a solution-slug-named repository", async () => {
		const { user } = renderEdit(makeSolution());

		const dialog = await screen.findByTestId("solution-dialog");
		const createBtn = within(dialog).getByTestId("create-repo");
		expect(createBtn).toHaveTextContent(/create solution-my-solution-/i);

		await user.click(createBtn);
		expect(mockCreateRepoMutate).toHaveBeenCalledWith(
			expect.objectContaining({
				body: expect.objectContaining({
					name: expect.stringMatching(/^solution-my-solution-[a-z0-9]{6}$/),
					private: true,
				}),
			}),
			expect.anything(),
		);
	});

	it("points at GitHub settings when no token is configured", async () => {
		ghConfig.data = { configured: false, token_saved: false };
		renderEdit(makeSolution());

		const dialog = await screen.findByTestId("solution-dialog");
		expect(
			within(dialog).getByText(/GitHub isn't configured/i),
		).toBeInTheDocument();
		expect(within(dialog).queryByTestId("create-repo")).toBeNull();
		ghConfig.data = { configured: true, token_saved: true };
	});
});
