/**
 * Tests for the Solutions list page — card/table rendering, search + org
 * filtering, and the CreateEditSolution install flow (dialog dropzone,
 * preview, scope re-preview, upgrade/downgrade guards). Uninstall lives on
 * the detail page (SolutionDetail.test.tsx).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, within } from "@/test-utils";
import { waitFor } from "@testing-library/react";

const mockNavigate = vi.fn();
vi.mock("react-router-dom", async () => {
	const actual =
		await vi.importActual<typeof import("react-router-dom")>(
			"react-router-dom",
		);
	return { ...actual, useNavigate: () => mockNavigate };
});

vi.mock("sonner", () => ({
	toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => ({
		data: [{ id: "org-1", name: "Acme Corp" }],
	}),
}));

const mockCreateRepoMutate = vi.fn();
vi.mock("@/hooks/useGitHub", () => ({
	useGitHubConfig: () => ({
		data: { configured: true, token_saved: true },
		isLoading: false,
	}),
	useCreateGitHubRepository: () => ({
		mutate: mockCreateRepoMutate,
		isPending: false,
	}),
}));

const mockListSolutions = vi.fn();
const mockPreviewInstall = vi.fn();
const mockInstallSolution = vi.fn();
const mockUpdateSolution = vi.fn();
vi.mock("@/services/solutions", () => ({
	listSolutions: (...a: unknown[]) => mockListSolutions(...a),
	previewInstall: (...a: unknown[]) => mockPreviewInstall(...a),
	installSolution: (...a: unknown[]) => mockInstallSolution(...a),
	updateSolution: (...a: unknown[]) => mockUpdateSolution(...a),
}));

function makeSolution(overrides: Record<string, unknown> = {}) {
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
	};
}

beforeEach(() => {
	vi.clearAllMocks();
	mockListSolutions.mockResolvedValue({ solutions: [] });
});

async function renderPage() {
	const { Solutions } = await import("./Solutions");
	return renderWithProviders(<Solutions />);
}

/** Open the install dialog via the + button and upload a file through it. */
async function uploadThroughDialog(
	user: ReturnType<typeof renderWithProviders>["user"],
	file: File,
) {
	await user.click(screen.getByTestId("open-install"));
	const dialog = await screen.findByTestId("solution-dialog");
	await user.upload(
		within(dialog).getByTestId("install-file-input") as HTMLInputElement,
		file,
	);
	return dialog;
}

describe("Solutions — list", () => {
	it("renders install cards with scope and source chips and no per-card delete", async () => {
		mockListSolutions.mockResolvedValue({
			solutions: [
				makeSolution({
					id: "git",
					name: "Git Solution",
					slug: "git-sol",
					organization_id: "org-1",
					git_connected: true,
					scope: "org",
				}),
				makeSolution({
					id: "manual",
					name: "Manual Solution",
					slug: "manual-sol",
					git_connected: false,
				}),
			],
		});
		await renderPage();

		const cards = await screen.findAllByTestId("install-card");
		expect(cards).toHaveLength(2);

		expect(screen.getByText("Git Solution")).toBeInTheDocument();
		expect(screen.getByText("Manual Solution")).toBeInTheDocument();
		expect(screen.getByText("Acme Corp")).toBeInTheDocument();
		expect(screen.getByText("Git")).toBeInTheDocument();
		expect(screen.getByText("Manual")).toBeInTheDocument();
		// Uninstall moved to the detail page — no per-card delete affordance.
		expect(
			screen.queryByRole("button", { name: /uninstall/i }),
		).toBeNull();
	});

	it("shows an empty state when there are no installs", async () => {
		mockListSolutions.mockResolvedValue({ solutions: [] });
		await renderPage();
		expect(
			await screen.findByText(/no solutions installed yet/i),
		).toBeInTheDocument();
	});

	it("shows a version badge on cards when version is present", async () => {
		mockListSolutions.mockResolvedValue({
			solutions: [
				makeSolution({ id: "v", name: "Versioned", version: "1.2.3" }),
				makeSolution({ id: "nv", name: "Unversioned", slug: "nv" }),
			],
		});
		await renderPage();
		await screen.findByText("Versioned");
		expect(screen.getByText("v1.2.3")).toBeInTheDocument();
	});

	it("switches to a table view with one row per install", async () => {
		mockListSolutions.mockResolvedValue({
			solutions: [
				makeSolution({ id: "a", name: "Alpha", slug: "alpha" }),
				makeSolution({ id: "b", name: "Beta", slug: "beta" }),
			],
		});
		const { user } = await renderPage();
		await screen.findAllByTestId("install-card");

		await user.click(screen.getByRole("radio", { name: /table view/i }));

		const rows = await screen.findAllByTestId("install-row");
		expect(rows).toHaveLength(2);
		expect(within(rows[0]).getByText("Alpha")).toBeInTheDocument();
	});

	it("filters by search term", async () => {
		mockListSolutions.mockResolvedValue({
			solutions: [
				makeSolution({ id: "a", name: "Alpha", slug: "alpha" }),
				makeSolution({ id: "b", name: "Beta", slug: "beta" }),
			],
		});
		const { user } = await renderPage();
		await screen.findAllByTestId("install-card");

		await user.type(screen.getByPlaceholderText(/search solutions/i), "alp");

		await waitFor(() =>
			expect(screen.getAllByTestId("install-card")).toHaveLength(1),
		);
		expect(screen.getByText("Alpha")).toBeInTheDocument();
		expect(screen.queryByText("Beta")).toBeNull();
	});
});

describe("Solutions — install flow (CreateEditSolution)", () => {
	it("opens the dialog with an entity summary, Organization selector, and config input", async () => {
		mockPreviewInstall.mockResolvedValue({
			slug: "new-sol",
			name: "New Solution",
			scope: "global",
			workflows: [{ name: "w1" }, { name: "w2" }],
			apps: [],
			forms: [],
			agents: [],
			tables: [],
			config_schemas: [
				{
					key: "api_token",
					type: "secret",
					required: true,
					description: "API token for the upstream service",
				},
			],
		});
		const { user } = await renderPage();
		await screen.findByText(/no solutions installed yet/i);

		const file = new File(["zip-bytes"], "new-sol.zip", {
			type: "application/zip",
		});
		const dialog = await uploadThroughDialog(user, file);

		expect(mockPreviewInstall).toHaveBeenCalledWith(file, {
			organizationId: "",
		});
		expect(
			await within(dialog).findByText(/New Solution/),
		).toBeInTheDocument();
		expect(within(dialog).getByTestId("preview-summary")).toHaveTextContent(
			"workflows",
		);
		expect(within(dialog).getByLabelText(/api_token/i)).toBeInTheDocument();
		// Standard Organization selector at the top.
		expect(within(dialog).getByText("Organization")).toBeInTheDocument();
		// Git section offers connection (GitHub configured in this suite).
		expect(within(dialog).getByTestId("git-section")).toBeInTheDocument();
	});

	it("installs and navigates to the new install on success", async () => {
		mockPreviewInstall.mockResolvedValue({
			slug: "new-sol",
			name: "New Solution",
			scope: "global",
			workflows: [{ name: "w1" }],
			config_schemas: [],
		});
		mockInstallSolution.mockResolvedValue(
			makeSolution({ id: "installed-1", name: "New Solution" }),
		);
		const { user } = await renderPage();
		await screen.findByText(/no solutions installed yet/i);

		const file = new File(["zip"], "new-sol.zip", {
			type: "application/zip",
		});
		const dialog = await uploadThroughDialog(user, file);
		await within(dialog).findByTestId("preview-summary");
		await user.click(within(dialog).getByTestId("confirm-install"));

		await waitFor(() =>
			expect(mockInstallSolution).toHaveBeenCalledWith(
				expect.objectContaining({ file, organizationId: "" }),
			),
		);
		await waitFor(() =>
			expect(mockNavigate).toHaveBeenCalledWith("/solutions/installed-1"),
		);
		// No repo URL entered — git stays untouched.
		expect(mockUpdateSolution).not.toHaveBeenCalled();
	});

	it("stamps the repo URL on the new install when one is set", async () => {
		mockPreviewInstall.mockResolvedValue({
			slug: "new-sol",
			name: "New Solution",
			scope: "global",
			workflows: [{ name: "w1" }],
			config_schemas: [],
		});
		mockInstallSolution.mockResolvedValue(
			makeSolution({ id: "installed-1", name: "New Solution" }),
		);
		mockUpdateSolution.mockResolvedValue(
			makeSolution({
				id: "installed-1",
				git_repo_url: "https://github.com/acme/solution-new-sol-abc123",
				git_connected: true,
			}),
		);
		const { user } = await renderPage();
		await screen.findByText(/no solutions installed yet/i);

		const dialog = await uploadThroughDialog(
			user,
			new File(["zip"], "new-sol.zip", { type: "application/zip" }),
		);
		await within(dialog).findByTestId("git-section");
		await user.type(
			within(dialog).getByTestId("git-repo-url"),
			"https://github.com/acme/solution-new-sol-abc123",
		);
		await user.click(within(dialog).getByTestId("confirm-install"));

		await waitFor(() =>
			expect(mockUpdateSolution).toHaveBeenCalledWith("installed-1", {
				git_repo_url: "https://github.com/acme/solution-new-sol-abc123",
				git_connected: true,
			}),
		);
	});
});

function makeUpgradePreview(overrides: Record<string, unknown> = {}) {
	return {
		slug: "my-solution",
		name: "My Solution",
		scope: "global",
		version: "2.0.0",
		workflows: [{ name: "w1" }],
		apps: [],
		forms: [],
		agents: [],
		tables: [],
		config_schemas: [],
		existing_install: {
			id: "sol-1",
			name: "My Solution",
			version: "1.0.0",
		},
		diff: {
			workflows: { added: ["new_flow"], removed: ["old_flow"] },
			tables: { added: [], removed: [] },
			forms: { added: [], removed: [] },
			agents: { added: [], removed: [] },
			apps: { added: [], removed: [] },
			config_schemas: {
				added: ["NEW_KEY"],
				removed: ["DEAD_KEY"],
				changed: [
					{
						key: "API_KEY",
						from: { type: "secret", required: true },
						to: { type: "string", required: false },
					},
				],
			},
		},
		...overrides,
	};
}

describe("Solutions — upgrade flow", () => {
	it("renders an upgrade title, diff entries, and no Organization picker when existing_install is present", async () => {
		mockPreviewInstall.mockResolvedValue(makeUpgradePreview());
		const { user } = await renderPage();
		await screen.findByText(/no solutions installed yet/i);

		const file = new File(["zip"], "my-solution.zip", {
			type: "application/zip",
		});
		const dialog = await uploadThroughDialog(user, file);

		expect(
			await within(dialog).findByText(
				/Upgrade My Solution v1\.0\.0 → v2\.0\.0/,
			),
		).toBeInTheDocument();
		expect(within(dialog).getByText(/new_flow/)).toBeInTheDocument();
		expect(within(dialog).getByText(/old_flow/)).toBeInTheDocument();
		expect(within(dialog).getByText(/NEW_KEY/)).toBeInTheDocument();
		expect(within(dialog).getByText(/DEAD_KEY/)).toBeInTheDocument();
		expect(
			within(dialog).getByText(/API_KEY: secret→string, required→optional/),
		).toBeInTheDocument();
		expect(within(dialog).getByTestId("confirm-install")).toHaveTextContent(
			"Upgrade",
		);
		// An upgrade targets the existing install — no Organization picker.
		expect(within(dialog).queryByText("Organization")).toBeNull();
	});

	it("shows a downgrade confirm on a 409 'older than installed' and retries with force", async () => {
		mockPreviewInstall.mockResolvedValue(
			makeUpgradePreview({ version: "0.9.0" }),
		);
		mockInstallSolution
			.mockRejectedValueOnce(
				new Error(
					"Solution version 0.9.0 is older than installed version 1.0.0",
				),
			)
			.mockResolvedValueOnce(
				makeSolution({ id: "sol-1", name: "My Solution" }),
			);
		const { user } = await renderPage();
		await screen.findByText(/no solutions installed yet/i);

		const file = new File(["zip"], "my-solution.zip", {
			type: "application/zip",
		});
		const dialog = await uploadThroughDialog(user, file);
		await within(dialog).findByTestId("confirm-install");
		await user.click(within(dialog).getByTestId("confirm-install"));

		await waitFor(() =>
			expect(mockInstallSolution).toHaveBeenCalledWith(
				expect.objectContaining({ file, force: false }),
			),
		);

		const confirm = await screen.findByTestId("downgrade-confirm");
		expect(confirm).toHaveTextContent("This is a DOWNGRADE: v1.0.0 → v0.9.0");
		await user.click(screen.getByTestId("confirm-downgrade"));

		await waitFor(() => expect(mockInstallSolution).toHaveBeenCalledTimes(2));
		expect(mockInstallSolution).toHaveBeenLastCalledWith(
			expect.objectContaining({ file, force: true }),
		);
		await waitFor(() =>
			expect(mockNavigate).toHaveBeenCalledWith("/solutions/sol-1"),
		);
	});

	it("re-previews against the selected org and enters upgrade mode for an org-scoped install", async () => {
		mockPreviewInstall.mockResolvedValueOnce({
			slug: "my-solution",
			name: "My Solution",
			scope: "org",
			version: "2.0.0",
			workflows: [{ name: "w1" }],
			apps: [],
			forms: [],
			agents: [],
			tables: [],
			config_schemas: [],
		});
		mockPreviewInstall.mockResolvedValueOnce(makeUpgradePreview());
		mockInstallSolution.mockResolvedValue(
			makeSolution({ id: "sol-1", name: "My Solution" }),
		);
		const { user } = await renderPage();
		await screen.findByText(/no solutions installed yet/i);

		const file = new File(["zip"], "my-solution.zip", {
			type: "application/zip",
		});
		const dialog = await uploadThroughDialog(user, file);
		expect(mockPreviewInstall).toHaveBeenCalledWith(file, {
			organizationId: "",
		});
		// Fresh-slug mode: Organization picker present.
		await within(dialog).findByText("Organization");

		// Select an org — preview must re-run against it.
		await user.click(within(dialog).getByRole("combobox"));
		await user.click(
			await screen.findByRole("option", { name: /Acme Corp/ }),
		);

		await waitFor(() =>
			expect(mockPreviewInstall).toHaveBeenCalledWith(file, {
				organizationId: "org-1",
			}),
		);

		expect(
			await within(dialog).findByText(
				/Upgrade My Solution v1\.0\.0 → v2\.0\.0/,
			),
		).toBeInTheDocument();
		expect(within(dialog).getByTestId("confirm-install")).toHaveTextContent(
			"Upgrade",
		);

		await user.click(within(dialog).getByTestId("confirm-install"));
		await waitFor(() =>
			expect(mockInstallSolution).toHaveBeenCalledWith(
				expect.objectContaining({
					file,
					organizationId: "org-1",
					force: false,
				}),
			),
		);
	});

	it("disarms the stale preview when a scope re-preview fails", async () => {
		mockPreviewInstall.mockResolvedValueOnce({
			slug: "my-solution",
			name: "My Solution",
			scope: "org",
			version: "2.0.0",
			workflows: [{ name: "w1" }],
			apps: [],
			forms: [],
			agents: [],
			tables: [],
			config_schemas: [],
		});
		mockPreviewInstall.mockRejectedValueOnce(new Error("network blip"));
		const { user } = await renderPage();
		await screen.findByText(/no solutions installed yet/i);

		const file = new File(["zip"], "my-solution.zip", {
			type: "application/zip",
		});
		const dialog = await uploadThroughDialog(user, file);
		await within(dialog).findByText("Organization");

		await user.click(within(dialog).getByRole("combobox"));
		await user.click(
			await screen.findByRole("option", { name: /Acme Corp/ }),
		);

		await within(dialog).findByText(/network blip/);
		expect(within(dialog).getByTestId("confirm-install")).toBeDisabled();
		expect(mockInstallSolution).not.toHaveBeenCalled();
	});

	it("surfaces non-downgrade install errors inline as before", async () => {
		mockPreviewInstall.mockResolvedValue(makeUpgradePreview());
		mockInstallSolution.mockRejectedValue(
			new Error("Scope mismatch: install exists at a different scope"),
		);
		const { user } = await renderPage();
		await screen.findByText(/no solutions installed yet/i);

		const file = new File(["zip"], "my-solution.zip", {
			type: "application/zip",
		});
		const dialog = await uploadThroughDialog(user, file);
		await within(dialog).findByTestId("confirm-install");
		await user.click(within(dialog).getByTestId("confirm-install"));

		expect(await screen.findByText(/Scope mismatch/)).toBeInTheDocument();
		expect(screen.queryByTestId("downgrade-confirm")).toBeNull();
	});
});

describe("Solutions — page dropzone", () => {
	it("opens the install dialog prefilled when a file is dropped on the page", async () => {
		mockPreviewInstall.mockResolvedValue({
			slug: "dropped",
			name: "Dropped Solution",
			scope: "global",
			workflows: [],
			config_schemas: [],
		});
		await renderPage();
		await screen.findByText(/no solutions installed yet/i);

		const file = new File(["zip"], "dropped.zip", {
			type: "application/zip",
		});
		const dropzone = screen.getByTestId("install-dropzone");
		const { fireEvent } = await import("@testing-library/react");
		fireEvent.drop(dropzone, {
			dataTransfer: { files: [file], types: ["Files"] },
		});

		const dialog = await screen.findByTestId("solution-dialog");
		expect(within(dialog).getByText(file.name)).toBeInTheDocument();
		await waitFor(() =>
			expect(mockPreviewInstall).toHaveBeenCalledWith(file, {
				organizationId: "",
			}),
		);
	});
});
