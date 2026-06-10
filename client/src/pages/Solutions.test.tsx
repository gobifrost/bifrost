/**
 * Tests for the Solutions list page — install list rendering, empty state,
 * the drag-and-drop / file-picker install preview flow, and the type-to-confirm
 * uninstall guard.
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

const mockListSolutions = vi.fn();
const mockPreviewInstall = vi.fn();
const mockInstallSolution = vi.fn();
const mockDeleteSolution = vi.fn();
vi.mock("@/services/solutions", () => ({
	listSolutions: (...a: unknown[]) => mockListSolutions(...a),
	previewInstall: (...a: unknown[]) => mockPreviewInstall(...a),
	installSolution: (...a: unknown[]) => mockInstallSolution(...a),
	deleteSolution: (...a: unknown[]) => mockDeleteSolution(...a),
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

describe("Solutions — list", () => {
	it("renders install cards with scope and source chips", async () => {
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
		// Scope chips: org name + Global
		expect(screen.getByText("Acme Corp")).toBeInTheDocument();
		expect(screen.getByText("Global")).toBeInTheDocument();
		// Source chips: Git vs Manual
		expect(screen.getByText("Git")).toBeInTheDocument();
		expect(screen.getByText("Manual")).toBeInTheDocument();
	});

	it("shows an empty state when there are no installs", async () => {
		mockListSolutions.mockResolvedValue({ solutions: [] });
		await renderPage();
		expect(
			await screen.findByText(/no solutions installed yet/i),
		).toBeInTheDocument();
	});
});

describe("Solutions — install preview flow", () => {
	it("opens the preview dialog with an entity summary and config input", async () => {
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
		const input = screen.getByTestId(
			"install-file-input",
		) as HTMLInputElement;
		await user.upload(input, file);

		const dialog = await screen.findByTestId("preview-dialog");
		expect(mockPreviewInstall).toHaveBeenCalledWith(file);
		expect(within(dialog).getByText(/New Solution/)).toBeInTheDocument();
		// Entity summary chip count for workflows
		expect(within(dialog).getByTestId("preview-summary")).toHaveTextContent(
			"workflows",
		);
		// Config input for the declared key
		expect(
			within(dialog).getByLabelText(/api_token/i),
		).toBeInTheDocument();
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
		await user.upload(
			screen.getByTestId("install-file-input") as HTMLInputElement,
			file,
		);
		await screen.findByTestId("preview-dialog");
		await user.click(screen.getByTestId("confirm-install"));

		await waitFor(() =>
			expect(mockInstallSolution).toHaveBeenCalledWith(
				expect.objectContaining({ file, organizationId: "" }),
			),
		);
		await waitFor(() =>
			expect(mockNavigate).toHaveBeenCalledWith("/solutions/installed-1"),
		);
	});
});

describe("Solutions — list version badge", () => {
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
	it("renders an upgrade title, diff entries, and no scope picker when existing_install is present", async () => {
		mockPreviewInstall.mockResolvedValue(makeUpgradePreview());
		const { user } = await renderPage();
		await screen.findByText(/no solutions installed yet/i);

		const file = new File(["zip"], "my-solution.zip", {
			type: "application/zip",
		});
		await user.upload(
			screen.getByTestId("install-file-input") as HTMLInputElement,
			file,
		);

		const dialog = await screen.findByTestId("preview-dialog");
		// Upgrade title with both versions
		expect(
			within(dialog).getByText(/Upgrade My Solution v1\.0\.0 → v2\.0\.0/),
		).toBeInTheDocument();
		// Entity diff entries
		expect(within(dialog).getByText(/new_flow/)).toBeInTheDocument();
		expect(within(dialog).getByText(/old_flow/)).toBeInTheDocument();
		// Config schema diff: added, removed, changed
		expect(within(dialog).getByText(/NEW_KEY/)).toBeInTheDocument();
		expect(within(dialog).getByText(/DEAD_KEY/)).toBeInTheDocument();
		expect(
			within(dialog).getByText(/API_KEY: secret→string, required→optional/),
		).toBeInTheDocument();
		// Confirm button is Upgrade, and no scope picker (no second install path)
		expect(within(dialog).getByTestId("confirm-install")).toHaveTextContent(
			"Upgrade",
		);
		expect(within(dialog).queryByTestId("scope-select")).toBeNull();
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
		await user.upload(
			screen.getByTestId("install-file-input") as HTMLInputElement,
			file,
		);
		await screen.findByTestId("preview-dialog");
		await user.click(screen.getByTestId("confirm-install"));

		// First attempt without force
		await waitFor(() =>
			expect(mockInstallSolution).toHaveBeenCalledWith(
				expect.objectContaining({ file, force: false }),
			),
		);

		// Downgrade confirm step
		const confirm = await screen.findByTestId("downgrade-confirm");
		expect(confirm).toHaveTextContent(
			"This is a DOWNGRADE: v1.0.0 → v0.9.0",
		);
		await user.click(screen.getByTestId("confirm-downgrade"));

		await waitFor(() =>
			expect(mockInstallSolution).toHaveBeenCalledTimes(2),
		);
		expect(mockInstallSolution).toHaveBeenLastCalledWith(
			expect.objectContaining({ file, force: true }),
		);
		await waitFor(() =>
			expect(mockNavigate).toHaveBeenCalledWith("/solutions/sol-1"),
		);
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
		await user.upload(
			screen.getByTestId("install-file-input") as HTMLInputElement,
			file,
		);
		await screen.findByTestId("preview-dialog");
		await user.click(screen.getByTestId("confirm-install"));

		expect(
			await screen.findByText(/Scope mismatch/),
		).toBeInTheDocument();
		expect(screen.queryByTestId("downgrade-confirm")).toBeNull();
	});
});

describe("Solutions — uninstall guard", () => {
	it("requires typing the name before Uninstall is enabled", async () => {
		mockListSolutions.mockResolvedValue({
			solutions: [makeSolution({ id: "sol-x", name: "Delete Me" })],
		});
		mockDeleteSolution.mockResolvedValue({
			solution_id: "sol-x",
			workflows_deleted: 1,
			apps_deleted: 0,
			forms_deleted: 0,
			agents_deleted: 0,
			config_declarations_deleted: 0,
			tables_orphaned: 2,
			config_values_orphaned: 1,
		});
		const { user } = await renderPage();

		await screen.findByText("Delete Me");
		await user.click(screen.getByRole("button", { name: /uninstall/i }));

		const dialog = await screen.findByTestId("delete-dialog");
		const confirmBtn = within(dialog).getByTestId("confirm-delete");
		expect(confirmBtn).toBeDisabled();

		const input = within(dialog).getByTestId("delete-confirm-input");
		await user.type(input, "Wrong Name");
		expect(confirmBtn).toBeDisabled();

		await user.clear(input);
		await user.type(input, "Delete Me");
		expect(confirmBtn).toBeEnabled();

		await user.click(confirmBtn);
		await waitFor(() =>
			expect(mockDeleteSolution).toHaveBeenCalledWith("sol-x"),
		);
	});
});
