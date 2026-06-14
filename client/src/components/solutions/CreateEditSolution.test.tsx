/**
 * Edit-mode tests for CreateEditSolution — the Organization selector replaces
 * the bespoke scope select, and git connection is DERIVED from the repo URL
 * (no manual "git connected" toggle).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, within } from "@/test-utils";
import { waitFor } from "@testing-library/react";
import { CreateEditSolution } from "./CreateEditSolution";
import {
	installSolution,
	previewInstall,
	type Solution,
	type SolutionInstallPreview,
} from "@/services/solutions";

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

function makePreview(
	overrides: Partial<SolutionInstallPreview> = {},
): SolutionInstallPreview {
	return {
		slug: "my-solution",
		name: "My Solution",
		version: "1.0.0",
		existing_install: null,
		diff: null,
		workflows: [],
		apps: [],
		forms: [],
		agents: [],
		tables: [],
		claims: [],
		config_schemas: [],
		...overrides,
	} as unknown as SolutionInstallPreview;
}

/** A 409 ContentCollision error shaped like installSolution throws. */
function collisionError(message: string): Error & { status: number } {
	const err = new Error(message) as Error & { status: number };
	err.status = 409;
	return err;
}

describe("CreateEditSolution — install collision prompt", () => {
	it("prompts to replace secrets on a 409 collision, then re-installs with replaceSecrets", async () => {
		vi.mocked(previewInstall).mockResolvedValue(makePreview());
		// First install attempt collides; the confirmed retry succeeds.
		vi.mocked(installSolution)
			.mockRejectedValueOnce(
				collisionError(
					"Import would overwrite existing config values: API_KEY, DB_PASSWORD. Re-run with replace to overwrite.",
				),
			)
			.mockResolvedValueOnce(makeSolution() as Solution);

		const onSaved = vi.fn();
		const file = new File(["zip"], "solution.zip", { type: "application/zip" });
		const { user } = renderWithProviders(
			<CreateEditSolution
				mode={{ kind: "create", file, organizationId: null }}
				open
				onClose={vi.fn()}
				onSaved={onSaved}
			/>,
		);

		// Auto-preview fires for the prefilled file; wait for the Install button.
		const installBtn = await screen.findByTestId("confirm-install");
		await waitFor(() => expect(installBtn).toBeEnabled());
		await user.click(installBtn);

		// Collision prompt appears, naming the colliding keys.
		const prompt = await screen.findByTestId("replace-secrets-prompt");
		expect(prompt).toHaveTextContent("API_KEY, DB_PASSWORD");

		await waitFor(() => expect(installSolution).toHaveBeenCalledTimes(1));
		expect(vi.mocked(installSolution).mock.calls[0][0]).toMatchObject({
			replaceSecrets: undefined,
		});

		// Confirm: re-posts with replaceSecrets: true.
		await user.click(screen.getByTestId("confirm-replace-secrets"));

		await waitFor(() => expect(installSolution).toHaveBeenCalledTimes(2));
		expect(vi.mocked(installSolution).mock.calls[1][0]).toMatchObject({
			replaceSecrets: true,
		});
		await waitFor(() => expect(onSaved).toHaveBeenCalled());
	});

	it("shows a wrong-password error on a 422 without prompting", async () => {
		vi.mocked(previewInstall).mockResolvedValue(makePreview());
		const err = new Error("bad password") as Error & { status: number };
		err.status = 422;
		vi.mocked(installSolution).mockRejectedValue(err);

		const file = new File(["zip"], "solution.zip", { type: "application/zip" });
		const { user } = renderWithProviders(
			<CreateEditSolution
				mode={{ kind: "create", file, organizationId: null }}
				open
				onClose={vi.fn()}
				onSaved={vi.fn()}
			/>,
		);

		const installBtn = await screen.findByTestId("confirm-install");
		await waitFor(() => expect(installBtn).toBeEnabled());
		await user.click(installBtn);

		expect(
			await screen.findByText(/incorrect password.*backup password/i),
		).toBeInTheDocument();
		expect(screen.queryByTestId("replace-secrets-prompt")).toBeNull();
	});
});

describe("CreateEditSolution — full-backup password prompt", () => {
	it("shows the password field when preview.requires_password is true", async () => {
		vi.mocked(previewInstall).mockResolvedValue(
			makePreview({ requires_password: true }),
		);

		const file = new File(["zip"], "backup.zip", { type: "application/zip" });
		renderWithProviders(
			<CreateEditSolution
				mode={{ kind: "create", file, organizationId: null }}
				open
				onClose={vi.fn()}
				onSaved={vi.fn()}
			/>,
		);

		// Password field appears after preview resolves.
		const passwordInput = await screen.findByTestId("backup-password-input");
		expect(passwordInput).toBeInTheDocument();
		expect(passwordInput).toHaveAttribute("type", "password");
	});

	it("does NOT show the password field for a normal (non-encrypted) zip", async () => {
		vi.mocked(previewInstall).mockResolvedValue(
			makePreview({ requires_password: false }),
		);

		const file = new File(["zip"], "solution.zip", { type: "application/zip" });
		renderWithProviders(
			<CreateEditSolution
				mode={{ kind: "create", file, organizationId: null }}
				open
				onClose={vi.fn()}
				onSaved={vi.fn()}
			/>,
		);

		await screen.findByTestId("confirm-install");
		expect(screen.queryByTestId("backup-password-input")).toBeNull();
	});

	it("passes the entered password to installSolution on confirm", async () => {
		vi.mocked(previewInstall).mockResolvedValue(
			makePreview({ requires_password: true }),
		);
		vi.mocked(installSolution).mockResolvedValue(makeSolution() as Solution);

		const file = new File(["zip"], "backup.zip", { type: "application/zip" });
		const onSaved = vi.fn();
		const { user } = renderWithProviders(
			<CreateEditSolution
				mode={{ kind: "create", file, organizationId: null }}
				open
				onClose={vi.fn()}
				onSaved={onSaved}
			/>,
		);

		// Enter the password.
		const passwordInput = await screen.findByTestId("backup-password-input");
		await user.type(passwordInput, "s3cr3t!");

		// Click install.
		const installBtn = await screen.findByTestId("confirm-install");
		await waitFor(() => expect(installBtn).toBeEnabled());
		await user.click(installBtn);

		await waitFor(() => expect(installSolution).toHaveBeenCalledTimes(1));
		expect(vi.mocked(installSolution).mock.calls[0][0]).toMatchObject({
			password: "s3cr3t!",
		});
		await waitFor(() => expect(onSaved).toHaveBeenCalled());
	});

	it("shows an error and clears the password field on a 422, allowing retry", async () => {
		vi.mocked(previewInstall).mockResolvedValue(
			makePreview({ requires_password: true }),
		);
		const wrongPwdErr = new Error("wrong password") as Error & { status: number };
		wrongPwdErr.status = 422;
		vi.mocked(installSolution)
			.mockRejectedValueOnce(wrongPwdErr)
			.mockResolvedValueOnce(makeSolution() as Solution);

		const file = new File(["zip"], "backup.zip", { type: "application/zip" });
		const onSaved = vi.fn();
		const { user } = renderWithProviders(
			<CreateEditSolution
				mode={{ kind: "create", file, organizationId: null }}
				open
				onClose={vi.fn()}
				onSaved={onSaved}
			/>,
		);

		// Enter wrong password and click install.
		const passwordInput = await screen.findByTestId("backup-password-input");
		await user.type(passwordInput, "wrongpass");
		const installBtn = await screen.findByTestId("confirm-install");
		await waitFor(() => expect(installBtn).toBeEnabled());
		await user.click(installBtn);

		// Error message shown; password field cleared.
		expect(
			await screen.findByText(/incorrect password.*backup password/i),
		).toBeInTheDocument();
		expect(screen.getByTestId("backup-password-input")).toHaveValue("");

		// Re-enter correct password and retry.
		await user.type(screen.getByTestId("backup-password-input"), "correct!");
		await user.click(screen.getByTestId("confirm-install"));

		await waitFor(() => expect(installSolution).toHaveBeenCalledTimes(2));
		expect(vi.mocked(installSolution).mock.calls[1][0]).toMatchObject({
			password: "correct!",
		});
		await waitFor(() => expect(onSaved).toHaveBeenCalled());
	});
});
