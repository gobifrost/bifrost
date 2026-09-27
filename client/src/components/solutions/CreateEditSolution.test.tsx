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
	installSolutionFromRepo,
	previewInstall,
	previewSolutionFromRepo,
	previewWorkspaceBundle,
	previewWorkspaceBundleFromRepo,
	importWorkspaceBundle,
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

const ghConfig = {
	data: { configured: true, token_saved: true },
	isLoading: false,
};
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
	installSolutionFromRepo: vi.fn(),
	previewSolutionFromRepo: vi.fn(),
	previewWorkspaceBundle: vi.fn(),
	previewWorkspaceBundleFromRepo: vi.fn(),
	importWorkspaceBundle: vi.fn(),
	updateSolution: (...a: unknown[]) => mockUpdateSolution(...a),
}));

const mockPreviewSolutionFromRepo = vi.mocked(previewSolutionFromRepo);

const mockRunGitOp = vi.fn();
vi.mock("@/components/editor/runGitOperation", () => ({
	runGitOp: (...args: unknown[]) => mockRunGitOp(...args),
}));

vi.mock("@/components/forms/OrganizationSelect", () => ({
	OrganizationSelect: ({
		value,
		onChange,
	}: {
		value?: string | null;
		onChange: (value: string | null) => void;
	}) => (
		<select
			aria-label="Target scope"
			value={value ?? "global"}
			onChange={(event) =>
				onChange(event.currentTarget.value === "global" ? null : event.currentTarget.value)
			}
		>
			<option value="global">Global</option>
			<option value="org-1">Acme Corp</option>
		</select>
	),
}));

function makeSolution(overrides: Partial<Solution> = {}): Solution {
	return {
		id: "sol-1",
		slug: "my-solution",
		name: "My Solution",
		organization_id: null,
		global_repo_access: false,
		allow_outbound_access: false,
		allow_inbound_access: true,
		git_connected: false,
		git_repo_url: null,
		scope: "global",
		...overrides,
	} as Solution;
}

beforeEach(() => {
	vi.clearAllMocks();
	mockPreviewSolutionFromRepo.mockResolvedValue(makePreview({ diff: {} }));
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
		expect(
			within(dialog).getByText(
				/allow shared module imports and read fallback to loose/i,
			),
		).toBeInTheDocument();
		// The old manual toggle is gone — connection is derived from the URL.
		expect(within(dialog).queryByLabelText(/git connected/i)).toBeNull();
		expect(within(dialog).getByTestId("git-section")).toBeInTheDocument();
		expect(within(dialog).getByText("Not connected")).toBeInTheDocument();
	});

	it("PATCHes outbound and inbound access under the new keys", async () => {
		mockUpdateSolution.mockResolvedValue(makeSolution());
		const { user } = renderEdit(makeSolution());

		const dialog = await screen.findByTestId("solution-dialog");
		await user.click(
			within(dialog).getByRole("switch", { name: /allow outbound access/i }),
		);
		await user.click(
			within(dialog).getByRole("switch", { name: /allow inbound access/i }),
		);
		await user.click(
			within(dialog).getByRole("button", { name: /save changes/i }),
		);
		await waitFor(() =>
			expect(mockUpdateSolution).toHaveBeenCalledWith("sol-1", {
				allow_outbound_access: true,
				allow_inbound_access: false,
			}),
		);
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
		await screen.findByRole("heading", { name: "Connect Git?" });
		await user.click(screen.getByRole("button", { name: "Connect Git" }));

		await waitFor(() =>
			expect(mockUpdateSolution).toHaveBeenCalledWith("sol-1", {
				git_repo_url: "https://github.com/acme/solution-my-solution-x1",
				git_connected: true,
				repo_subpath: null,
				git_ref: null,
			}),
		);
		expect(onSaved).toHaveBeenCalled();
	});

	it("previews and confirms a manual Solution before Git becomes its only writer", async () => {
		mockUpdateSolution.mockResolvedValue(makeSolution());
		mockPreviewSolutionFromRepo.mockResolvedValue(
			makePreview({
				slug: "my-solution",
				diff: { workflows: { added: ["remote_sync"], removed: [] } },
			}),
		);
		const { user } = renderEdit(makeSolution());

		const dialog = await screen.findByTestId("solution-dialog");
		await user.type(
			within(dialog).getByTestId("git-repo-url"),
			"https://github.com/acme/solution-my-solution",
		);
		await user.type(
			within(dialog).getByTestId("git-repo-subpath"),
			"solutions/my-solution",
		);
		await user.type(within(dialog).getByTestId("git-repo-ref"), "main");
		await user.click(
			within(dialog).getByRole("button", { name: /save changes/i }),
		);

		expect(mockUpdateSolution).not.toHaveBeenCalled();
		expect(
			await screen.findByRole("heading", { name: "Connect Git?" }),
		).toBeInTheDocument();
		await waitFor(() =>
			expect(mockPreviewSolutionFromRepo).toHaveBeenCalledWith({
				repo_url: "https://github.com/acme/solution-my-solution",
				repo_subpath: "solutions/my-solution",
				git_ref: "main",
				organization_id: null,
			}),
		);
		expect(
			screen.getByText(/Git becomes this Solution's only writer/i),
		).toBeInTheDocument();
		expect(screen.getByTestId("upgrade-diff")).toHaveTextContent(
			"remote_sync",
		);

		await user.click(screen.getByRole("button", { name: "Connect Git" }));
		await waitFor(() =>
			expect(mockUpdateSolution).toHaveBeenCalledWith("sol-1", {
				git_connected: true,
				git_repo_url: "https://github.com/acme/solution-my-solution",
				repo_subpath: "solutions/my-solution",
				git_ref: "main",
			}),
		);
	});

	it("does not connect when the reviewed repository declares another Solution", async () => {
		mockPreviewSolutionFromRepo.mockResolvedValue(
			makePreview({ slug: "other-solution" }),
		);
		const { user } = renderEdit(makeSolution());

		const dialog = await screen.findByTestId("solution-dialog");
		await user.type(
			within(dialog).getByTestId("git-repo-url"),
			"https://github.com/acme/other-solution",
		);
		await user.click(
			within(dialog).getByRole("button", { name: /save changes/i }),
		);

		expect(
			await screen.findByText(/declares “other-solution”/i),
		).toBeInTheDocument();
		expect(screen.getByRole("button", { name: "Connect Git" })).toBeDisabled();
		expect(mockUpdateSolution).not.toHaveBeenCalled();
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

	it("exposes Subfolder and Ref inputs and PATCHes them when connecting", async () => {
		mockUpdateSolution.mockResolvedValue(makeSolution());
		const { user } = renderEdit(makeSolution());

		const dialog = await screen.findByTestId("solution-dialog");
		await user.type(
			within(dialog).getByTestId("git-repo-url"),
			"https://github.com/acme/repo",
		);
		await user.type(
			within(dialog).getByTestId("git-repo-subpath"),
			"solutions/mine",
		);
		await user.type(within(dialog).getByTestId("git-repo-ref"), "release");
		await user.click(
			within(dialog).getByRole("button", { name: /save changes/i }),
		);
		await screen.findByRole("heading", { name: "Connect Git?" });
		await user.click(screen.getByRole("button", { name: "Connect Git" }));

		await waitFor(() =>
			expect(mockUpdateSolution).toHaveBeenCalledWith("sol-1", {
				git_repo_url: "https://github.com/acme/repo",
				git_connected: true,
				repo_subpath: "solutions/mine",
				git_ref: "release",
			}),
		);
	});

	it("prefills Subfolder and Ref from the install and reconnect-edits them", async () => {
		mockUpdateSolution.mockResolvedValue(makeSolution());
		const { user } = renderEdit(
			makeSolution({
				git_connected: true,
				git_repo_url: "https://github.com/acme/repo",
				repo_subpath: "old/path",
				git_ref: "main",
			} as Partial<Solution>),
		);

		const dialog = await screen.findByTestId("solution-dialog");
		expect(within(dialog).getByTestId("git-repo-subpath")).toHaveValue(
			"old/path",
		);
		expect(within(dialog).getByTestId("git-repo-ref")).toHaveValue("main");

		await user.clear(within(dialog).getByTestId("git-repo-subpath"));
		await user.type(
			within(dialog).getByTestId("git-repo-subpath"),
			"new/path",
		);
		await user.click(
			within(dialog).getByRole("button", { name: /save changes/i }),
		);

		await waitFor(() =>
			expect(mockUpdateSolution).toHaveBeenCalledWith("sol-1", {
				repo_subpath: "new/path",
			}),
		);
	});

	it("explicit Disconnect flips git_connected off and clears the repo coords", async () => {
		mockUpdateSolution.mockResolvedValue(makeSolution());
		const { user } = renderEdit(
			makeSolution({
				git_connected: true,
				git_repo_url: "https://github.com/acme/repo",
				repo_subpath: "p",
				git_ref: "main",
			} as Partial<Solution>),
		);

		const dialog = await screen.findByTestId("solution-dialog");
		await user.click(within(dialog).getByTestId("git-disconnect"));
		expect(within(dialog).getByText("Not connected")).toBeInTheDocument();
		await user.click(
			within(dialog).getByRole("button", { name: /save changes/i }),
		);

		await waitFor(() =>
			expect(mockUpdateSolution).toHaveBeenCalledWith("sol-1", {
				git_connected: false,
				git_repo_url: null,
				repo_subpath: null,
				git_ref: null,
			}),
		);
	});

	it("offers to create a solution-slug-named repository", async () => {
		const { user } = renderEdit(makeSolution());

		const dialog = await screen.findByTestId("solution-dialog");
		const createBtn = within(dialog).getByTestId("create-repo");
		expect(createBtn).toHaveTextContent("Create repository");
		const repositoryName = within(dialog).getByText(
			/^solution-my-solution-/i,
		).textContent;
		expect(repositoryName).toMatch(/^solution-my-solution-[a-z0-9]{6}$/);

		await user.click(createBtn);
		expect(mockCreateRepoMutate).toHaveBeenCalledWith(
			expect.objectContaining({
				body: expect.objectContaining({
					name: repositoryName,
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
	it("passes explicit reactivate intent to zip installs", async () => {
		vi.mocked(previewInstall).mockResolvedValue(makePreview());
		vi.mocked(installSolution).mockResolvedValue(
			makeSolution({ status: "active" } as Partial<Solution>) as Solution,
		);
		const file = new File(["zip"], "solution.zip", {
			type: "application/zip",
		});
		const { user } = renderWithProviders(
			<CreateEditSolution
				mode={{
					kind: "create",
					file,
					organizationId: "org-1",
					intent: "reactivate",
				}}
				open
				onClose={vi.fn()}
				onSaved={vi.fn()}
			/>,
		);

		expect(
			await screen.findByText("Reactivate Solution"),
		).toBeInTheDocument();
		const reactivateButton = await screen.findByRole("button", {
			name: "Reactivate",
		});
		await waitFor(() => expect(reactivateButton).toBeEnabled());
		await user.click(reactivateButton);

		await waitFor(() => expect(installSolution).toHaveBeenCalledOnce());
		expect(vi.mocked(installSolution).mock.calls[0][0]).toMatchObject({
			file,
			organizationId: "org-1",
			reactivate: true,
		});
	});

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
		const file = new File(["zip"], "solution.zip", {
			type: "application/zip",
		});
		const { user } = renderWithProviders(
			<CreateEditSolution
				mode={{ kind: "create", destination: "solution", file, organizationId: null }}
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

		const file = new File(["zip"], "solution.zip", {
			type: "application/zip",
		});
		const { user } = renderWithProviders(
			<CreateEditSolution
				mode={{ kind: "create", destination: "solution", file, organizationId: null }}
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

		const file = new File(["zip"], "backup.zip", {
			type: "application/zip",
		});
		renderWithProviders(
			<CreateEditSolution
				mode={{ kind: "create", destination: "solution", file, organizationId: null }}
				open
				onClose={vi.fn()}
				onSaved={vi.fn()}
			/>,
		);

		// Password field appears after preview resolves.
		const passwordInput = await screen.findByTestId(
			"backup-password-input",
		);
		expect(passwordInput).toBeInTheDocument();
		expect(passwordInput).toHaveAttribute("type", "password");
	});

	it("does NOT show the password field for a normal (non-encrypted) zip", async () => {
		vi.mocked(previewInstall).mockResolvedValue(
			makePreview({ requires_password: false }),
		);

		const file = new File(["zip"], "solution.zip", {
			type: "application/zip",
		});
		renderWithProviders(
			<CreateEditSolution
				mode={{ kind: "create", destination: "solution", file, organizationId: null }}
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
		vi.mocked(installSolution).mockResolvedValue(
			makeSolution() as Solution,
		);

		const file = new File(["zip"], "backup.zip", {
			type: "application/zip",
		});
		const onSaved = vi.fn();
		const { user } = renderWithProviders(
			<CreateEditSolution
				mode={{ kind: "create", destination: "solution", file, organizationId: null }}
				open
				onClose={vi.fn()}
				onSaved={onSaved}
			/>,
		);

		// Enter the password.
		const passwordInput = await screen.findByTestId(
			"backup-password-input",
		);
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
		const wrongPwdErr = new Error("wrong password") as Error & {
			status: number;
		};
		wrongPwdErr.status = 422;
		vi.mocked(installSolution)
			.mockRejectedValueOnce(wrongPwdErr)
			.mockResolvedValueOnce(makeSolution() as Solution);

		const file = new File(["zip"], "backup.zip", {
			type: "application/zip",
		});
		const onSaved = vi.fn();
		const { user } = renderWithProviders(
			<CreateEditSolution
				mode={{ kind: "create", destination: "solution", file, organizationId: null }}
				open
				onClose={vi.fn()}
				onSaved={onSaved}
			/>,
		);

		// Enter wrong password and click install.
		const passwordInput = await screen.findByTestId(
			"backup-password-input",
		);
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
		await user.type(
			screen.getByTestId("backup-password-input"),
			"correct!",
		);
		await user.click(screen.getByTestId("confirm-install"));

		await waitFor(() => expect(installSolution).toHaveBeenCalledTimes(2));
		expect(vi.mocked(installSolution).mock.calls[1][0]).toMatchObject({
			password: "correct!",
		});
		await waitFor(() => expect(onSaved).toHaveBeenCalled());
	});
});

describe("CreateEditSolution — destination-first flow", () => {
	function renderCreate(
		mode: Parameters<typeof CreateEditSolution>[0]["mode"],
	) {
		const onSaved = vi.fn();
		const onClose = vi.fn();
		const utils = renderWithProviders(
			<CreateEditSolution
				mode={mode}
				open
				onClose={onClose}
				onSaved={onSaved}
			/>,
		);
		return { ...utils, onSaved, onClose };
	}

	it("offers exactly two destinations first (workspace vs Solution)", async () => {
		renderCreate({ kind: "create" });

		const picker = await screen.findByTestId("destination-picker");
		expect(within(picker).getByTestId("destination-workspace")).toHaveTextContent(
			/import into workspace/i,
		);
		expect(within(picker).getByTestId("destination-solution")).toHaveTextContent(
			/import as a solution/i,
		);
		expect(within(picker).queryByTestId("source-repo")).toBeNull();
		expect(within(picker).queryByTestId("source-zip")).toBeNull();
		// No empty-shell create form — no name field, no install button yet.
		expect(screen.queryByTestId("confirm-install")).toBeNull();
		expect(screen.queryByTestId("confirm-install-repo")).toBeNull();
	});

	it("workspace destination leads to a two-option source screen", async () => {
		const { user } = renderCreate({ kind: "create" });
		await user.click(await screen.findByTestId("destination-workspace"));

		const picker = await screen.findByTestId("source-picker");
		expect(within(picker).getByTestId("source-repo")).toHaveTextContent(
			/from a repository/i,
		);
		expect(within(picker).getByTestId("source-zip")).toHaveTextContent(
			/from a zip/i,
		);
	});

	it("solution destination leads to the same two source options", async () => {
		const { user } = renderCreate({ kind: "create" });
		await user.click(await screen.findByTestId("destination-solution"));

		const picker = await screen.findByTestId("source-picker");
		expect(within(picker).getByTestId("source-repo")).toBeInTheDocument();
		expect(within(picker).getByTestId("source-zip")).toBeInTheDocument();
	});

	it("source screen Back returns to the destination screen", async () => {
		const { user } = renderCreate({ kind: "create" });
		await user.click(await screen.findByTestId("destination-workspace"));
		await screen.findByTestId("source-picker");

		await user.click(screen.getByRole("button", { name: /back/i }));
		expect(await screen.findByTestId("destination-picker")).toBeInTheDocument();
	});

	it("solution + zip shows the managed dropzone (zip path)", async () => {
		const { user } = renderCreate({ kind: "create" });
		await user.click(await screen.findByTestId("destination-solution"));
		await user.click(await screen.findByTestId("source-zip"));
		expect(
			await screen.findByTestId("dialog-dropzone"),
		).toBeInTheDocument();
	});

	it("solution + repo shows the managed repo form", async () => {
		const { user } = renderCreate({ kind: "create" });
		await user.click(await screen.findByTestId("destination-solution"));
		await user.click(await screen.findByTestId("source-repo"));
		expect(await screen.findByTestId("repo-url")).toBeInTheDocument();
		expect(screen.getByTestId("repo-subpath")).toBeInTheDocument();
		expect(screen.getByTestId("repo-ref")).toBeInTheDocument();
	});

	it("workspace upload and review use the wider dialog", async () => {
		vi.mocked(previewWorkspaceBundle).mockResolvedValue({
			preview_token: "workspace-preview",
			package_name: "Workspace",
			package_sha256: "a".repeat(64),
			conflict_count: 0,
			source_kind: "zip",
			items: [],
		});
		const { user } = renderCreate({ kind: "create" });

		await user.click(await screen.findByTestId("destination-workspace"));
		await user.click(await screen.findByTestId("source-zip"));

		expect(await screen.findByText("Import into workspace")).toBeInTheDocument();
		expect(screen.getByTestId("workspace-dialog-dropzone")).toBeInTheDocument();
		expect(screen.getByTestId("solution-dialog")).toHaveClass("sm:max-w-4xl");
		expect(screen.getAllByRole("button", { name: /^back$/i })).toHaveLength(1);

		const fileInput = document.querySelector<HTMLInputElement>('input[type="file"]');
		expect(fileInput).not.toBeNull();
		await user.upload(fileInput!, new File(["zip"], "workspace.zip", { type: "application/zip" }));

		expect(await screen.findByText("Review workspace import")).toBeInTheDocument();
		expect(await screen.findByTestId("workspace-import-footer")).toBeInTheDocument();
		expect(screen.getByTestId("solution-dialog")).toHaveClass("sm:max-w-4xl");
	});

	it("workspace + repo shows the snapshot repo form (no Solution lifecycle)", async () => {
		const { user } = renderCreate({ kind: "create" });

		await user.click(await screen.findByTestId("destination-workspace"));
		await user.click(await screen.findByTestId("source-repo"));

		expect(await screen.findByTestId("workspace-repo-url")).toBeInTheDocument();
		expect(screen.getByTestId("workspace-repo-ref")).toBeInTheDocument();
		expect(screen.getByTestId("workspace-repo-subpath")).toBeInTheDocument();
		expect(screen.getByText(/one-time snapshot/i)).toBeInTheDocument();
		expect(screen.queryByTestId("confirm-install-repo")).toBeNull();
		// The snapshot form is not the review: it stays narrow.
		expect(screen.getByTestId("solution-dialog")).not.toHaveClass("sm:max-w-6xl");
		// Exactly one Back, in the footer — the form body has none.
		expect(screen.getAllByRole("button", { name: /^back$/i })).toHaveLength(1);
	});

	it("workspace + repo previews a snapshot and opens the collision review", async () => {
		vi.mocked(previewWorkspaceBundleFromRepo).mockResolvedValue({
			preview_token: "workspace-repo-preview",
			package_name: "Workspace",
			package_sha256: "a".repeat(64),
			conflict_count: 0,
			source_kind: "repo",
			repo_url: "https://example.com/repo.git",
			git_ref: "main",
			repo_subpath: null,
			resolved_commit: "abc123",
			items: [],
		});
		const { user } = renderCreate({ kind: "create" });

		await user.click(await screen.findByTestId("destination-workspace"));
		await user.click(await screen.findByTestId("source-repo"));
		await user.type(await screen.findByTestId("workspace-repo-url"), "https://example.com/repo.git");
		await user.click(screen.getByTestId("workspace-repo-preview"));

		await waitFor(() => expect(previewWorkspaceBundleFromRepo).toHaveBeenCalledWith({
			repo_url: "https://example.com/repo.git",
			git_ref: null,
			repo_subpath: null,
			organization_id: null,
		}));
		expect(await screen.findByTestId("workspace-import-footer")).toBeInTheDocument();
	});

	it("a prefilled file asks for the destination; each choice uses the file", async () => {
		vi.mocked(previewWorkspaceBundle).mockResolvedValue({
			preview_token: "workspace-preview",
			package_name: "Workspace",
			package_sha256: "a".repeat(64),
			conflict_count: 0,
			source_kind: "zip",
			items: [],
		});
		const file = new File(["zip"], "dropped.zip", { type: "application/zip" });

		// Workspace branch: the file prefills the zip source and auto-previews.
		const { user } = renderCreate({ kind: "create", file });
		await user.click(await screen.findByTestId("destination-workspace"));
		expect(screen.queryByTestId("source-picker")).toBeNull();
		await waitFor(() => expect(previewWorkspaceBundle).toHaveBeenCalledWith(file, { organizationId: "" }));
		expect(await screen.findByTestId("workspace-import-footer")).toBeInTheDocument();
	});

	it("a prefilled file routes to the managed zip body on the solution destination", async () => {
		vi.mocked(previewInstall).mockResolvedValue(makePreview());
		const file = new File(["zip"], "dropped.zip", { type: "application/zip" });
		const { user } = renderCreate({ kind: "create", file });

		expect(await screen.findByTestId("destination-picker")).toBeInTheDocument();
		await user.click(screen.getByTestId("destination-solution"));
		expect(screen.queryByTestId("source-picker")).toBeNull();
		expect(await screen.findByText(file.name)).toBeInTheDocument();
	});

	it("dropping a file on the workspace dropzone previews it", async () => {
		vi.mocked(previewWorkspaceBundle).mockResolvedValue({
			preview_token: "workspace-preview",
			package_name: "Workspace",
			package_sha256: "a".repeat(64),
			conflict_count: 0,
			source_kind: "zip",
			items: [],
		});
		renderCreate({
			kind: "create",
			destination: "workspace",
			source: "zip",
		});
		const dropzone = await screen.findByTestId("workspace-dialog-dropzone");
		const file = new File(["zip"], "dropped.zip", { type: "application/zip" });
		const { fireEvent } = await import("@testing-library/react");
		fireEvent.drop(dropzone, { dataTransfer: { files: [file] } });

		await waitFor(() => expect(previewWorkspaceBundle).toHaveBeenCalledWith(file, { organizationId: "" }));
		expect(await screen.findByTestId("workspace-import-footer")).toBeInTheDocument();
		expect(screen.getByLabelText("Target scope")).toHaveValue("global");
	});

	it("workspace scope defaults to Global and previews in an org on change", async () => {
		vi.mocked(previewWorkspaceBundleFromRepo).mockResolvedValue({
			preview_token: "workspace-repo-preview",
			package_name: "Workspace",
			package_sha256: "a".repeat(64),
			conflict_count: 0,
			source_kind: "repo",
			repo_url: "https://example.com/repo.git",
			git_ref: null,
			repo_subpath: null,
			resolved_commit: "abc123",
			organization_id: "org-1",
			items: [],
		});
		const { user } = renderCreate({ kind: "create" });

		await user.click(await screen.findByTestId("destination-workspace"));
		await user.click(await screen.findByTestId("source-repo"));

		// Global by default.
		expect(screen.getByLabelText("Target scope")).toHaveValue("global");
		await user.type(await screen.findByTestId("workspace-repo-url"), "https://example.com/repo.git");
		await user.selectOptions(screen.getByLabelText("Target scope"), "org-1");
		await user.click(screen.getByTestId("workspace-repo-preview"));

		await waitFor(() => expect(previewWorkspaceBundleFromRepo).toHaveBeenCalledWith({
			repo_url: "https://example.com/repo.git",
			git_ref: null,
			repo_subpath: null,
			organization_id: "org-1",
		}));
		expect(screen.getByLabelText("Target scope")).toHaveValue("org-1");
	});

	it("changing scope keeps the chosen file and previews it in the new scope", async () => {
		vi.mocked(previewWorkspaceBundle).mockRejectedValueOnce(new Error("boom"));
		vi.mocked(previewWorkspaceBundle).mockResolvedValueOnce({
			preview_token: "scoped-preview",
			package_name: "Workspace",
			package_sha256: "a".repeat(64),
			organization_id: "org-1",
			items: [],
		} as never);
		renderCreate({
			kind: "create",
			destination: "workspace",
			source: "zip",
		});
		const dropzone = await screen.findByTestId("workspace-dialog-dropzone");
		const file = new File(["zip"], "dropped.zip", { type: "application/zip" });
		const { fireEvent } = await import("@testing-library/react");
		fireEvent.drop(dropzone, { dataTransfer: { files: [file] } });

		expect(await screen.findAllByText("boom")).toHaveLength(1);
		fireEvent.change(screen.getByLabelText("Target scope"), { target: { value: "org-1" } });

		await waitFor(() => expect(screen.queryByText("boom")).toBeNull());
		await waitFor(() => expect(previewWorkspaceBundle).toHaveBeenLastCalledWith(file, { organizationId: "org-1" }));
		expect(screen.getByLabelText("Target scope")).toHaveValue("org-1");
		expect(screen.getByLabelText("Target scope")).toHaveValue("org-1");
		expect(screen.getByTestId("solution-dialog")).toHaveClass("sm:max-w-4xl");
	});

	it("explicit destination + source skips both pickers", async () => {
		renderCreate({ kind: "create", destination: "solution", source: "zip" });

		expect(await screen.findByTestId("dialog-dropzone")).toBeInTheDocument();
		expect(screen.getByTestId("solution-dialog")).toHaveClass("sm:max-w-xl");
		expect(screen.queryByTestId("destination-picker")).toBeNull();
		expect(screen.queryByTestId("source-picker")).toBeNull();
	});

	it("reactivate skips the destination screen (fixed destination)", async () => {
		renderCreate({ kind: "create", intent: "reactivate" });

		expect(screen.queryByTestId("destination-picker")).toBeNull();
		expect(await screen.findByTestId("dialog-dropzone")).toBeInTheDocument();
	});

	it("repo prefill implies the managed repo path without pickers", async () => {
		renderCreate({
			kind: "create",
			repo: { url: "https://example.com/repo.git", subpath: null, ref: null },
		});

		expect(await screen.findByTestId("repo-url")).toBeInTheDocument();
		expect(screen.queryByTestId("destination-picker")).toBeNull();
		expect(screen.queryByTestId("source-picker")).toBeNull();
	});

	it("queues a reviewed workspace import through the shared platform-job observer", async () => {
		vi.mocked(previewWorkspaceBundle).mockResolvedValue({
			preview_token: "workspace-preview",
			package_name: "Workspace",
			package_sha256: "a".repeat(64),
			conflict_count: 0,
			source_kind: "zip",
			items: [{ id: "entity:config:cfg-1", kind: "config", name: "API_TOKEN", classification: "create", scope_change: false }],
			config_schemas: [{ key: "API_TOKEN", type: "secret", required: true, requires_input: true, description: "API access token" }],
		});
		vi.mocked(importWorkspaceBundle).mockResolvedValue({
			job_id: "workspace-job",
			status: "queued",
			reused: false,
		});
		mockRunGitOp.mockImplementation(async (queue, _title, onQueued, onUpdate) => {
			const accepted = await queue("requested-job");
			onQueued?.(accepted.job_id);
			onUpdate?.({ status: "succeeded", result: {} });
			return {};
		});
		const onClose = vi.fn();
		const { user } = renderWithProviders(
			<CreateEditSolution mode={{ kind: "create", destination: "workspace", source: "zip" }} open onClose={onClose} onSaved={vi.fn()} />,
		);

		const fileInput = document.querySelector<HTMLInputElement>('input[type="file"]');
		expect(fileInput).not.toBeNull();
		await user.upload(fileInput!, new File(["zip"], "workspace.zip", { type: "application/zip" }));
		const tokenInput = await screen.findByLabelText(/API_TOKEN/);
		expect(tokenInput).toHaveAttribute("type", "password");
		expect(tokenInput).toHaveAttribute("aria-required", "true");
		expect(screen.getByRole("button", { name: /start import job/i })).toBeDisabled();
		expect(screen.queryByText(/you can still import/i)).not.toBeInTheDocument();
		await user.type(tokenInput, "entered-test-token");
		expect(screen.getByRole("button", { name: /start import job/i })).toBeEnabled();
		await user.click(await screen.findByRole("button", { name: /start import job/i }));

		await waitFor(() => expect(importWorkspaceBundle).toHaveBeenCalledWith({
			preview_token: "workspace-preview",
			decisions: [],
			config_values: { API_TOKEN: "entered-test-token" },
		}));
		expect(mockRunGitOp).toHaveBeenCalledTimes(1);
		expect(onClose).toHaveBeenCalledTimes(1);
	});

	it("shows config guidance in placeholders without changing their values", async () => {
		vi.mocked(previewWorkspaceBundle).mockResolvedValue({
			preview_token: "workspace-preview",
			package_name: "Workspace",
			package_sha256: "a".repeat(64),
			conflict_count: 0,
			source_kind: "zip",
			items: [
				{ id: "entity:config:cfg-1", kind: "config", name: "API_TOKEN", classification: "unchanged", scope_change: false },
				{ id: "entity:config:cfg-2", kind: "config", name: "REGION", classification: "create", scope_change: false },
				{ id: "entity:config:cfg-3", kind: "config", name: "OWNER", classification: "create", scope_change: false },
			],
			config_schemas: [
				{ key: "API_TOKEN", type: "secret", required: true, requires_input: false, exists: true, has_existing_value: true },
				{ key: "REGION", type: "string", required: false, requires_input: false, has_package_default: true },
				{ key: "OWNER", type: "string", required: false, requires_input: false, description: "Workspace owner" },
			],
		});
		const { user } = renderWithProviders(
			<CreateEditSolution mode={{ kind: "create", destination: "workspace", source: "zip" }} open onClose={vi.fn()} onSaved={vi.fn()} />,
		);
		const fileInput = document.querySelector<HTMLInputElement>('input[type="file"]');
		await user.upload(fileInput!, new File(["zip"], "workspace.zip", { type: "application/zip" }));
		const tokenInput = await screen.findByLabelText(/API_TOKEN/);
		expect(tokenInput).not.toHaveAttribute("aria-required", "true");
		expect(tokenInput).toHaveValue("");
		expect(tokenInput).toHaveAttribute("placeholder", "Existing value if left blank");
		expect(screen.getByLabelText(/REGION/)).toHaveAttribute("placeholder", "Package default if left blank");
		expect(screen.getByLabelText(/OWNER/)).toHaveAttribute("placeholder", "Workspace owner");
		expect(screen.queryByText("Existing value will be kept if left blank.")).toBeNull();
	});
});

describe("CreateEditSolution — repo install path", () => {
	it("resolves a repo URL, renders the confirmation, then installs from repo", async () => {
		vi.mocked(previewSolutionFromRepo).mockResolvedValue(
			makePreview({
				name: "CSP",
				slug: "microsoft-csp",
				config_schemas: [
					{
						key: "TENANT_ID",
						type: "string",
						required: true,
						description: null,
					},
				],
				connection_schemas: [
					{
						integration_name: "microsoft",
						display_name: "Microsoft",
					},
				],
				agents: [
					{ name: "Support", knowledge_sources: ["support_kb"] },
				],
			} as Partial<SolutionInstallPreview>),
		);
		vi.mocked(installSolutionFromRepo).mockResolvedValue(
			makeSolution({ id: "repo-install-1", name: "CSP" }) as Solution,
		);

		const onSaved = vi.fn();
		const { user } = renderWithProviders(
			<CreateEditSolution
				mode={{ kind: "create", destination: "solution", source: "repo" }}
				open
				onClose={vi.fn()}
				onSaved={onSaved}
			/>,
		);

		await user.type(
			await screen.findByTestId("repo-url"),
			"https://github.com/acme/solutions",
		);
		await user.type(screen.getByTestId("repo-subpath"), "microsoft-csp");
		await user.type(screen.getByTestId("repo-ref"), "main");
		await user.click(screen.getByTestId("resolve-repo"));

		await waitFor(() =>
			expect(previewSolutionFromRepo).toHaveBeenCalledWith({
				repo_url: "https://github.com/acme/solutions",
				repo_subpath: "microsoft-csp",
				git_ref: "main",
			}),
		);

		// Shared confirmation: entity summary + declared config keys.
		expect(
			await screen.findByTestId("preview-summary"),
		).toBeInTheDocument();
		expect(screen.getByTestId("config-section")).toHaveTextContent(
			"TENANT_ID",
		);
		// Declared integrations are surfaced in the preview summary (audit U-prev).
		expect(screen.getByTestId("preview-summary")).toHaveTextContent(
			"integrations",
		);
		// Agents' knowledge namespaces are surfaced as a non-blocking note so the
		// install doesn't look self-contained when a corpus must be populated.
		const kbNote = screen.getByTestId("knowledge-namespace-note");
		expect(kbNote).toHaveTextContent("support_kb");

		await user.click(screen.getByTestId("confirm-install-repo"));

		await waitFor(() =>
			expect(installSolutionFromRepo).toHaveBeenCalledWith({
				repo_url: "https://github.com/acme/solutions",
				repo_subpath: "microsoft-csp",
				git_ref: "main",
			}),
		);
		await waitFor(() => expect(onSaved).toHaveBeenCalled());
	});

	it("pre-fills the repo fields from the repo prefill (deep link)", async () => {
		renderWithProviders(
			<CreateEditSolution
				mode={{
					kind: "create",
					destination: "solution",
					source: "repo",
					repo: {
						url: "https://github.com/acme/solutions",
						subpath: "microsoft-csp",
						ref: "v2",
					},
				}}
				open
				onClose={vi.fn()}
				onSaved={vi.fn()}
			/>,
		);

		expect(await screen.findByTestId("repo-url")).toHaveValue(
			"https://github.com/acme/solutions",
		);
		expect(screen.getByTestId("repo-subpath")).toHaveValue("microsoft-csp");
		expect(screen.getByTestId("repo-ref")).toHaveValue("v2");
	});

	it("omits blank subpath/ref from the request body", async () => {
		vi.mocked(previewSolutionFromRepo).mockResolvedValue(makePreview());
		const { user } = renderWithProviders(
			<CreateEditSolution
				mode={{ kind: "create", destination: "solution", source: "repo" }}
				open
				onClose={vi.fn()}
				onSaved={vi.fn()}
			/>,
		);

		await user.type(
			await screen.findByTestId("repo-url"),
			"https://github.com/acme/solutions",
		);
		await user.click(screen.getByTestId("resolve-repo"));

		await waitFor(() =>
			expect(previewSolutionFromRepo).toHaveBeenCalledWith({
				repo_url: "https://github.com/acme/solutions",
			}),
		);
	});
});

it("retains secret replacement confirmation and retries the same overwrite choice after failure", async () => {
	vi.mocked(previewInstall).mockResolvedValue(makePreview());
	vi.mocked(installSolution)
		.mockReset()
		.mockRejectedValueOnce(
			collisionError(
				"Import would overwrite existing config values: API_KEY. Re-run with replace to overwrite.",
			),
		)
		.mockRejectedValueOnce(new Error("Synthetic replacement failure"))
		.mockResolvedValueOnce(makeSolution() as Solution);
	const onSaved = vi.fn();
	const { user } = renderWithProviders(
		<CreateEditSolution
			mode={{
				kind: "create",
				destination: "solution",
				file: new File(["fixture"], "fixture.zip"),
				organizationId: null,
			}}
			open
			onClose={vi.fn()}
			onSaved={onSaved}
		/>,
	);
	const install = await screen.findByTestId("confirm-install");
	await waitFor(() => expect(install).toBeEnabled());
	await user.click(install);
	await user.click(await screen.findByTestId("confirm-replace-secrets"));
	const prompt = screen.getByTestId("replace-secrets-prompt");
	await waitFor(() =>
		expect(within(prompt).getByRole("alert")).toHaveFocus(),
	);
	expect(prompt).toHaveTextContent("Synthetic replacement failure");
	await user.click(within(prompt).getByTestId("confirm-replace-secrets"));
	await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
	expect(vi.mocked(installSolution).mock.calls[2][0]).toEqual(
		vi.mocked(installSolution).mock.calls[1][0],
	);
});
