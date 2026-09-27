import { Solutions } from "./Solutions";
/**
 * Tests for the Solutions list page — card/table rendering, search + org
 * filtering, and the CreateEditSolution install flow (dialog dropzone,
 * preview, scope re-preview, upgrade/downgrade guards). Uninstall lives on
 * the detail page (SolutionDetail.test.tsx).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, renderWithProviders, screen, within } from "@/test-utils";
import { waitFor } from "@testing-library/react";
import { toast } from "sonner";

const mockIsDesktop = vi.fn(() => true);
vi.mock("@/hooks/useMediaQuery", () => ({
	useIsDesktop: () => mockIsDesktop(),
}));
const mockNavigate = vi.fn();
const mockSetSearchParams = vi.fn();
let mockSearchParams = new URLSearchParams();
vi.mock("react-router-dom", async () => {
	const actual =
		await vi.importActual<typeof import("react-router-dom")>(
			"react-router-dom",
		);
	return {
		...actual,
		useNavigate: () => mockNavigate,
		useSearchParams: () => [mockSearchParams, mockSetSearchParams] as const,
	};
});

vi.mock("sonner", () => ({
	toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn() },
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
const mockUpdateSelectedSolutionAppSdks = vi.fn();
const mockPreviewInstall = vi.fn();
const mockInstallSolution = vi.fn();
const mockUpdateSolution = vi.fn();
const mockPreviewSolutionFromRepo = vi.fn();
const mockInstallSolutionFromRepo = vi.fn();
const mockPreviewWorkspaceBundle = vi.fn();
vi.mock("@/services/solutions", () => ({
	listSolutions: (...a: unknown[]) => mockListSolutions(...a),
	updateSelectedSolutionAppSdks: (...a: unknown[]) =>
		mockUpdateSelectedSolutionAppSdks(...a),
	previewInstall: (...a: unknown[]) => mockPreviewInstall(...a),
	installSolution: (...a: unknown[]) => mockInstallSolution(...a),
	updateSolution: (...a: unknown[]) => mockUpdateSolution(...a),
	previewSolutionFromRepo: (...a: unknown[]) =>
		mockPreviewSolutionFromRepo(...a),
	installSolutionFromRepo: (...a: unknown[]) =>
		mockInstallSolutionFromRepo(...a),
	previewWorkspaceBundle: (...a: unknown[]) =>
		mockPreviewWorkspaceBundle(...a),
}));

const mockTrackAccepted = vi.fn();
let mockSdkStates: Record<string, string> = {};
vi.mock("@/hooks/useApplicationSdkUpdateJobs", () => ({
	useApplicationSdkUpdateJobs: () => ({
		trackAccepted: mockTrackAccepted,
		getUpdateState: (id: string) => mockSdkStates[id] ?? "idle",
		hasUpdateState: (id: string) => id in mockSdkStates,
		isAnyUpdating: (ids: string[]) =>
			ids.some(
				(id) =>
					mockSdkStates[id] === "queued" ||
					mockSdkStates[id] === "updating",
			),
	}),
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
		setup_complete: true,
		status: "active",
		sdk_status: "not_applicable",
		sdk_actionable_count: 0,
		scope: "global",
		...overrides,
	};
}

beforeEach(() => {
	vi.clearAllMocks();
	mockListSolutions.mockResolvedValue({ solutions: [] });
	mockUpdateSelectedSolutionAppSdks.mockResolvedValue({
		accepted: [],
		skipped: [],
	});
	mockTrackAccepted.mockReset();
	mockSdkStates = {};
	mockSearchParams = new URLSearchParams();
	vi.mocked(toast.success).mockClear();
	vi.mocked(toast.warning).mockClear();
	vi.mocked(toast.error).mockClear();
});

async function renderPage() {
	return renderWithProviders(<Solutions />);
}

/**
 * Open the install dialog via the + button, choose the managed Solution
 * destination and the From-zip source, and upload a file through the
 * dropzone's file input.
 */
async function uploadThroughDialog(
	user: ReturnType<typeof renderWithProviders>["user"],
	file: File,
) {
	await user.click(screen.getByTestId("open-install"));
	const dialog = await screen.findByTestId("solution-dialog");
	await user.click(within(dialog).getByTestId("destination-solution"));
	await user.click(within(dialog).getByTestId("source-zip"));
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
		expect(screen.queryByRole("button", { name: /uninstall/i })).toBeNull();
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

	it("renders Solution SDK aggregate status from the list response without per-Solution status calls", async () => {
		mockListSolutions.mockResolvedValue({
			solutions: [
				makeSolution({
					id: "sdk",
					name: "SDK Solution",
					slug: "sdk-solution",
					sdk_status: "update_available",
					sdk_actionable_count: 2,
				}),
			],
		});
		await renderPage();

		await screen.findByText("SDK Solution");
		expect(screen.getByLabelText("SDK update available")).toBeVisible();
		expect(
			screen.getByLabelText("2 apps can update SDK"),
		).toHaveTextContent("2 updates");
	});

	it("renders colored entity count badges in a wrapping card footer", async () => {
		mockListSolutions.mockResolvedValue({
			solutions: [
				makeSolution({
					id: "inventory",
					name: "Inventory",
					slug: "inventory",
					entity_counts: {
						workflows: 2,
						apps: 1,
						forms: 0,
						agents: 1,
						tables: 3,
						claims: 0,
						files: 4,
					},
				}),
			],
		});
		await renderPage();

		const card = await screen.findByTestId("install-card");
		const footer = within(card).getByTestId("solution-card-counts");
		expect(footer).toHaveClass("flex-wrap");
		expect(
			within(footer).getByTestId("solution-count-workflows"),
		).toHaveTextContent("2");
		expect(
			within(footer).getByTestId("solution-count-apps"),
		).toHaveTextContent("1");
		expect(
			within(footer).getByTestId("solution-count-agents"),
		).toHaveTextContent("1");
		expect(
			within(footer).getByTestId("solution-count-tables"),
		).toHaveTextContent("3");
		expect(
			within(footer).getByTestId("solution-count-files"),
		).toHaveTextContent("4");
		expect(within(footer).queryByTestId("solution-count-forms")).toBeNull();
		expect(
			within(footer).queryByTestId("solution-count-claims"),
		).toBeNull();
	});

	it("shows an inactive badge for solutions with status=inactive", async () => {
		mockListSolutions.mockResolvedValue({
			solutions: [
				makeSolution({ id: "a", name: "Active One", status: "active" }),
				makeSolution({
					id: "b",
					name: "Inactive One",
					slug: "inactive-sol",
					status: "inactive",
				}),
			],
		});
		await renderPage();
		await screen.findByText("Active One");

		// By default inactive installs are hidden.
		expect(screen.queryByText("Inactive One")).toBeNull();
		// No inactive badge visible yet.
		expect(screen.queryByTestId("inactive-badge")).toBeNull();
	});

	it("shows inactive solutions when the show-inactive toggle is checked", async () => {
		mockListSolutions.mockResolvedValue({
			solutions: [
				makeSolution({ id: "a", name: "Active One", status: "active" }),
				makeSolution({
					id: "b",
					name: "Inactive One",
					slug: "inactive-sol",
					status: "inactive",
				}),
			],
		});
		const { user } = await renderPage();
		await screen.findByText("Active One");

		const toggle = screen.getByRole("switch", { name: "Show Inactive" });
		await user.click(toggle);

		await waitFor(() =>
			expect(screen.getByText("Inactive One")).toBeInTheDocument(),
		);
		expect(screen.getByTestId("inactive-badge")).toBeInTheDocument();
	});

	it("shows an 'Update available' badge only when update_available_version is set", async () => {
		mockListSolutions.mockResolvedValue({
			solutions: [
				makeSolution({
					id: "stale",
					name: "Stale Install",
					slug: "stale",
					version: "1.0.0",
					update_available_version: "1.1.0",
				}),
				makeSolution({
					id: "current",
					name: "Current Install",
					slug: "current",
					version: "1.1.0",
					update_available_version: null,
				}),
			],
		});
		await renderPage();
		await screen.findByText("Stale Install");

		const badges = screen.getAllByTestId("update-available-badge");
		expect(badges).toHaveLength(1);
		expect(badges[0]).toHaveTextContent("v1.1.0");
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

		await user.type(
			screen.getByRole("textbox", { name: "Search solutions" }),
			"alp",
		);

		await waitFor(() =>
			expect(screen.getAllByTestId("install-card")).toHaveLength(1),
		);
		expect(screen.getByText("Alpha")).toBeInTheDocument();
		expect(screen.queryByText("Beta")).toBeNull();
	});
});

describe("Solutions — bulk SDK updates", () => {
	it("updates every actionable active solution in organization scope while ignoring search text", async () => {
		let resolveBatch: (
			value: Awaited<
				ReturnType<typeof mockUpdateSelectedSolutionAppSdks>
			>,
		) => void = () => {};
		mockListSolutions.mockResolvedValue({
			solutions: [
				makeSolution({
					id: "sol-1",
					name: "Visible SDK",
					slug: "visible-sdk",
					organization_id: "org-1",
					sdk_status: "update_available",
					sdk_actionable_count: 1,
				}),
				makeSolution({
					id: "sol-2",
					name: "Hidden SDK",
					slug: "hidden-sdk",
					organization_id: "org-1",
					sdk_status: "unknown",
					sdk_actionable_count: 2,
				}),
				makeSolution({
					id: "sol-3",
					name: "Current SDK",
					slug: "current-sdk",
					organization_id: "org-1",
					sdk_status: "current",
					sdk_actionable_count: 0,
				}),
				makeSolution({
					id: "sol-4",
					name: "Inactive SDK",
					slug: "inactive-sdk",
					organization_id: "org-1",
					status: "inactive",
					sdk_status: "update_available",
					sdk_actionable_count: 1,
				}),
				makeSolution({
					id: "sol-5",
					name: "Other Org SDK",
					slug: "other-org-sdk",
					organization_id: "org-2",
					sdk_status: "current",
					sdk_actionable_count: 0,
				}),
			],
		});
		mockUpdateSelectedSolutionAppSdks.mockReturnValue(
			new Promise((resolve) => {
				resolveBatch = resolve;
			}),
		);
		const { user } = await renderPage();

		await screen.findByText("Visible SDK");
		await user.type(
			screen.getByRole("textbox", { name: "Search solutions" }),
			"Visible",
		);
		await waitFor(() =>
			expect(
				screen.getByRole("button", { name: "Update all SDKs (2)" }),
			).toHaveAccessibleDescription(
				"Includes all actionable Solutions in the current organization scope, including 1 hidden by search.",
			),
		);
		await user.click(
			screen.getByRole("button", { name: "Update all SDKs (2)" }),
		);

		expect(
			screen.getByRole("button", { name: "Queueing…" }),
		).toBeDisabled();
		expect(screen.getByRole("button", { name: "Select" })).toBeDisabled();
		expect(mockUpdateSelectedSolutionAppSdks).toHaveBeenCalledWith([
			"sol-1",
			"sol-2",
		]);
		resolveBatch({
			accepted: [
				{
					application_id: "app-1",
					solution_id: "sol-1",
					job_id: "job-1",
					status: "queued",
					reused: false,
				},
			],
			skipped: [{ application_id: "app-2", reason: "current" }],
		});
		await waitFor(() =>
			expect(toast.success).toHaveBeenCalledWith(
				"Queued SDK updates for 1 App. 1 skipped.",
			),
		);
		expect(mockTrackAccepted).toHaveBeenCalledWith([
			expect.objectContaining({ application_id: "app-1" }),
		]);
	});

	it("selects only actionable visible solutions and clears selection after success", async () => {
		mockListSolutions.mockResolvedValue({
			solutions: [
				makeSolution({
					id: "sol-1",
					name: "Dispatch Solution",
					slug: "dispatch",
					sdk_status: "update_available",
					sdk_actionable_count: 1,
				}),
				makeSolution({
					id: "sol-2",
					name: "Runbook Solution",
					slug: "runbook",
					sdk_status: "unknown",
					sdk_actionable_count: 1,
				}),
				makeSolution({
					id: "sol-3",
					name: "Current Solution",
					slug: "current",
					sdk_status: "current",
					sdk_actionable_count: 0,
				}),
			],
		});
		mockUpdateSelectedSolutionAppSdks.mockResolvedValue({
			accepted: [
				{
					application_id: "app-1",
					solution_id: "sol-1",
					job_id: "job-1",
					status: "queued",
					reused: false,
				},
			],
			skipped: [],
		});
		const { user } = await renderPage();
		await screen.findByText("Dispatch Solution");

		await user.click(screen.getByRole("button", { name: "Select" }));
		await user.click(screen.getByRole("button", { name: "Select all" }));

		expect(
			screen.getByRole("button", { name: "Dispatch Solution" }),
		).toHaveAttribute("aria-pressed", "true");
		expect(
			screen.getByRole("button", { name: "Runbook Solution" }),
		).toHaveAttribute("aria-pressed", "true");
		expect(
			screen.getByRole("button", { name: "Current Solution" }),
		).toHaveAttribute("aria-disabled", "true");
		await user.click(
			screen.getByRole("button", { name: "Update selected (2)" }),
		);

		expect(mockUpdateSelectedSolutionAppSdks).toHaveBeenCalledWith([
			"sol-1",
			"sol-2",
		]);
		await waitFor(() =>
			expect(
				screen.queryByRole("button", { name: "Update selected (2)" }),
			).not.toBeInTheDocument(),
		);
		expect(screen.getByRole("button", { name: "Select" })).toBeVisible();
	});

	it("does not nest interactive links or actions inside selection cards", async () => {
		mockListSolutions.mockResolvedValue({
			solutions: [
				makeSolution({
					id: "sol-1",
					name: "Dispatch Solution",
					slug: "dispatch",
					sdk_status: "update_available",
					sdk_actionable_count: 1,
				}),
			],
		});
		const { user } = await renderPage();
		const normalCard = await screen.findByRole("article", {
			name: "Dispatch Solution",
		});

		expect(
			within(normalCard).getByRole("link", {
				name: "Dispatch Solution",
			}),
		).toBeInTheDocument();
		expect(
			within(normalCard).getByRole("button", {
				name: "Update SDKs for Dispatch Solution",
			}),
		).toBeInTheDocument();
		await user.click(normalCard);
		expect(mockNavigate).toHaveBeenCalledWith("/solutions/sol-1");
		mockNavigate.mockClear();

		await user.click(screen.getByRole("button", { name: "Select" }));
		const selectionCard = screen.getByRole("button", {
			name: "Dispatch Solution",
		});

		expect(
			within(selectionCard).queryByRole("link", {
				name: "Dispatch Solution",
			}),
		).not.toBeInTheDocument();
		expect(
			within(selectionCard).queryByRole("button", {
				name: "Update SDKs for Dispatch Solution",
			}),
		).not.toBeInTheDocument();
	});

	it("opens a solution table row href on ctrl-click outside selection mode", async () => {
		const open = vi.spyOn(window, "open").mockImplementation(() => null);
		mockListSolutions.mockResolvedValue({
			solutions: [
				makeSolution({
					id: "sol-1",
					name: "Dispatch Solution",
					slug: "dispatch",
				}),
			],
		});

		const { user } = await renderPage();
		await user.click(screen.getByRole("radio", { name: "Table view" }));
		const row = await screen.findByTestId("install-row");

		fireEvent.click(within(row).getByText("dispatch"), { ctrlKey: true });

		expect(open).toHaveBeenCalledWith("/solutions/sol-1", "_blank");
		expect(mockNavigate).not.toHaveBeenCalled();
	});

	it("marks accepted solutions as updating and removes them from bulk actions", async () => {
		mockListSolutions.mockResolvedValue({
			solutions: [
				makeSolution({
					id: "sol-1",
					name: "Dispatch Solution",
					slug: "dispatch",
					sdk_status: "update_available",
					sdk_actionable_count: 1,
				}),
				makeSolution({
					id: "sol-2",
					name: "Runbook Solution",
					slug: "runbook",
					sdk_status: "update_available",
					sdk_actionable_count: 1,
				}),
			],
		});
		mockUpdateSelectedSolutionAppSdks.mockResolvedValue({
			accepted: [
				{
					application_id: "app-1",
					solution_id: "sol-1",
					job_id: "job-1",
					status: "queued",
					reused: false,
				},
				{
					application_id: "app-2",
					solution_id: "sol-2",
					job_id: "job-2",
					status: "queued",
					reused: false,
				},
			],
			skipped: [],
		});
		const { user, rerender } = await renderPage();
		await screen.findByText("Dispatch Solution");

		await user.click(
			screen.getByRole("button", { name: "Update all SDKs (2)" }),
		);

		await waitFor(() =>
			expect(screen.getAllByLabelText("SDK update queued")).toHaveLength(2),
		);
		expect(
			screen.queryByRole("button", { name: "Update all SDKs (2)" }),
		).not.toBeInTheDocument();
		expect(screen.getByRole("button", { name: "Select" })).toBeDisabled();
		expect(
			screen.queryByRole("button", {
				name: "Update SDKs for Dispatch Solution",
			}),
		).not.toBeInTheDocument();

		mockSdkStates = { "app-1": "idle", "app-2": "updating" };
		rerender(<Solutions />);
		await waitFor(() =>
			expect(screen.getAllByLabelText("Updating SDK")).toHaveLength(1),
		);
		expect(
			screen.getByRole("button", {
				name: "Update SDKs for Dispatch Solution",
			}),
		).toBeVisible();
		expect(
			screen.queryByRole("button", {
				name: "Update SDKs for Runbook Solution",
			}),
		).not.toBeInTheDocument();
	});

	it("moves submitted solutions from queued to updating and clears them after completion", async () => {
		mockListSolutions.mockResolvedValue({
			solutions: [
				makeSolution({
					id: "sol-1",
					name: "Dispatch Solution",
					slug: "dispatch",
					sdk_status: "update_available",
					sdk_actionable_count: 1,
				}),
				makeSolution({
					id: "sol-2",
					name: "Runbook Solution",
					slug: "runbook",
					sdk_status: "update_available",
					sdk_actionable_count: 1,
				}),
			],
		});
		mockUpdateSelectedSolutionAppSdks.mockResolvedValue({
			accepted: [
				{
					application_id: "app-1",
					solution_id: "sol-1",
					job_id: "job-1",
					status: "queued",
					reused: false,
				},
			],
			skipped: [{ application_id: "app-2", reason: "current" }],
		});
		const { user, rerender } = await renderPage();
		await screen.findByText("Dispatch Solution");

		await user.click(
			screen.getByRole("button", { name: "Update all SDKs (2)" }),
		);

		await waitFor(() =>
			expect(screen.getAllByLabelText("SDK update queued")).toHaveLength(1),
		);
		expect(
			screen.getByRole("button", { name: "Update all SDKs (1)" }),
		).toBeVisible();
		expect(
			screen.queryByRole("button", {
				name: "Update SDKs for Dispatch Solution",
			}),
		).not.toBeInTheDocument();
		expect(
			screen.getByRole("button", {
				name: "Update SDKs for Runbook Solution",
			}),
		).toBeVisible();
		mockSdkStates = { "app-1": "updating" };
		rerender(<Solutions />);
		await waitFor(() =>
			expect(screen.getAllByLabelText("Updating SDK")).toHaveLength(1),
		);

		mockSdkStates = { "app-1": "idle" };
		rerender(<Solutions />);

		await waitFor(() =>
			expect(
				screen.queryByLabelText("Updating SDK"),
			).not.toBeInTheDocument(),
		);
		expect(
			screen.queryByLabelText("SDK update queued"),
		).not.toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "Update all SDKs (2)" }),
		).toBeVisible();
	});

	it("does not mark a solution as updating when every app is skipped", async () => {
		mockListSolutions.mockResolvedValue({
			solutions: [
				makeSolution({
					id: "sol-1",
					name: "Dispatch Solution",
					slug: "dispatch",
					sdk_status: "update_available",
					sdk_actionable_count: 1,
				}),
			],
		});
		mockUpdateSelectedSolutionAppSdks.mockResolvedValue({
			accepted: [],
			skipped: [{ application_id: "app-1", reason: "conflict" }],
		});
		const { user } = await renderPage();
		await screen.findByText("Dispatch Solution");

		await user.click(
			screen.getByRole("button", { name: "Update all SDKs (1)" }),
		);

		await waitFor(() =>
			expect(toast.warning).toHaveBeenCalledWith(
				"No SDK updates were queued. 1 skipped.",
			),
		);
		expect(screen.queryByLabelText("Updating SDK")).not.toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "Update all SDKs (1)" }),
		).toBeVisible();
	});

	it("keeps selection after a batch request failure", async () => {
		mockListSolutions.mockResolvedValue({
			solutions: [
				makeSolution({
					id: "sol-1",
					name: "Dispatch Solution",
					slug: "dispatch",
					sdk_status: "update_available",
					sdk_actionable_count: 1,
				}),
			],
		});
		mockUpdateSelectedSolutionAppSdks.mockRejectedValue(new Error("boom"));
		const { user } = await renderPage();
		await screen.findByText("Dispatch Solution");

		await user.click(screen.getByRole("button", { name: "Select" }));
		await user.click(
			screen.getByRole("button", { name: "Dispatch Solution" }),
		);
		await user.click(
			screen.getByRole("button", { name: "Update selected (1)" }),
		);

		expect(toast.error).toHaveBeenCalledWith(
			"Failed to queue Solution app SDK updates",
		);
		expect(
			screen.getByRole("button", { name: "Dispatch Solution" }),
		).toHaveAttribute("aria-pressed", "true");
	});

	it("warns when every requested Solution SDK update is skipped", async () => {
		mockListSolutions.mockResolvedValue({
			solutions: [
				makeSolution({
					id: "sol-1",
					name: "Dispatch Solution",
					slug: "dispatch",
					sdk_status: "update_available",
					sdk_actionable_count: 1,
				}),
			],
		});
		mockUpdateSelectedSolutionAppSdks.mockResolvedValue({
			accepted: [],
			skipped: [{ application_id: "app-1", reason: "conflict" }],
		});
		const { user } = await renderPage();
		await screen.findByText("Dispatch Solution");

		await user.click(
			screen.getByRole("button", { name: "Update all SDKs (1)" }),
		);

		await waitFor(() =>
			expect(toast.warning).toHaveBeenCalledWith(
				"No SDK updates were queued. 1 skipped.",
			),
		);
		expect(toast.success).not.toHaveBeenCalled();
	});

	it("uses semantic checkboxes in table selection mode", async () => {
		mockListSolutions.mockResolvedValue({
			solutions: [
				makeSolution({
					id: "sol-1",
					name: "Dispatch Solution",
					slug: "dispatch",
					sdk_status: "update_available",
					sdk_actionable_count: 1,
				}),
				makeSolution({
					id: "sol-2",
					name: "Current Solution",
					slug: "current",
					sdk_status: "current",
					sdk_actionable_count: 0,
				}),
			],
		});
		const { user } = await renderPage();
		await screen.findByText("Dispatch Solution");

		await user.click(screen.getByRole("radio", { name: /table view/i }));
		await user.click(screen.getByRole("button", { name: "Select" }));
		await user.click(
			screen.getByRole("checkbox", {
				name: "Select Dispatch Solution for SDK update",
			}),
		);

		expect(
			screen.getByRole("checkbox", {
				name: "Select Dispatch Solution for SDK update",
			}),
		).toBeChecked();
		expect(
			screen.getByRole("checkbox", {
				name: "Select Current Solution for SDK update",
			}),
		).toBeDisabled();
	});

	it("exposes a direct per-card SDK update action outside selection mode", async () => {
		mockListSolutions.mockResolvedValue({
			solutions: [
				makeSolution({
					id: "sol-1",
					name: "Dispatch Solution",
					slug: "dispatch",
					sdk_status: "update_available",
					sdk_actionable_count: 1,
				}),
			],
		});
		const { user } = await renderPage();
		await screen.findByText("Dispatch Solution");

		await user.click(
			screen.getByRole("button", {
				name: "Update SDKs for Dispatch Solution",
			}),
		);

		expect(mockUpdateSelectedSolutionAppSdks).toHaveBeenCalledWith([
			"sol-1",
		]);
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
				repo_subpath: null,
				git_ref: null,
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
			within(dialog).getByText(
				/API_KEY: secret→string, required→optional/,
			),
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
	it("a dropped file asks for the destination first, then previews the chosen path", async () => {
		mockPreviewInstall.mockResolvedValue({
			slug: "dropped",
			name: "Dropped Solution",
			scope: "global",
			workflows: [],
			config_schemas: [],
		});
		const { user } = await renderPage();
		await screen.findByText(/no solutions installed yet/i);

		const file = new File(["zip"], "dropped.zip", {
			type: "application/zip",
		});
		const dropzone = screen.getByTestId("install-dropzone");
		const { fireEvent } = await import("@testing-library/react");
		fireEvent.drop(dropzone, {
			dataTransfer: { files: [file], types: ["Files"] },
		});

		// The file does not imply a destination: the picker comes first and
		// nothing previews yet.
		const dialog = await screen.findByTestId("solution-dialog");
		expect(within(dialog).getByTestId("destination-picker")).toBeInTheDocument();
		expect(mockPreviewInstall).not.toHaveBeenCalled();

		// Managed path: the dropped file prefills the zip source.
		await user.click(within(dialog).getByTestId("destination-solution"));
		expect(within(dialog).getByText(file.name)).toBeInTheDocument();
		await waitFor(() =>
			expect(mockPreviewInstall).toHaveBeenCalledWith(file, {
				organizationId: "",
			}),
		);
	});

	it("a dropped file can go to the workspace import instead", async () => {
		mockPreviewWorkspaceBundle.mockResolvedValue({
			preview_token: "workspace-preview",
			package_name: "Dropped",
			package_sha256: "a".repeat(64),
			conflict_count: 0,
			source_kind: "zip",
			items: [],
			warnings: [],
		});
		const { user } = await renderPage();
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
		await user.click(within(dialog).getByTestId("destination-workspace"));
		// The dropped file prefills the workspace zip source and previews.
		await waitFor(() =>
			expect(mockPreviewWorkspaceBundle).toHaveBeenCalledWith(file, { organizationId: "" }),
		);
		expect(
			await within(dialog).findByTestId("workspace-import-footer"),
		).toBeInTheDocument();
	});
});

describe("Solutions — destination-first install", () => {
	it("opens a destination picker from the + button, then a source picker (no empty-shell create)", async () => {
		const { user } = await renderPage();
		await screen.findByText(/no solutions installed yet/i);

		await user.click(screen.getByTestId("open-install"));
		const dialog = await screen.findByTestId("solution-dialog");
		expect(within(dialog).getByTestId("destination-picker")).toBeInTheDocument();
		expect(within(dialog).getByTestId("destination-workspace")).toBeInTheDocument();
		expect(within(dialog).getByTestId("destination-solution")).toBeInTheDocument();
		// No blank-create form: no name input, no immediate install button.
		expect(within(dialog).queryByTestId("confirm-install")).toBeNull();

		await user.click(within(dialog).getByTestId("destination-solution"));
		expect(within(dialog).getByTestId("source-picker")).toBeInTheDocument();
		expect(within(dialog).getByTestId("source-repo")).toBeInTheDocument();
		expect(within(dialog).getByTestId("source-zip")).toBeInTheDocument();
	});

	it("the empty state opens the destination picker too", async () => {
		const { user } = await renderPage();
		await user.click(
			await screen.findByText(/no solutions installed yet/i),
		);
		const dialog = await screen.findByTestId("solution-dialog");
		expect(within(dialog).getByTestId("destination-picker")).toBeInTheDocument();
	});
});

describe("Solutions — repo deep link", () => {
	it("opens the From-repository form pre-filled from ?repo=&path=&ref=", async () => {
		mockSearchParams = new URLSearchParams({
			repo: "https://github.com/acme/solutions",
			path: "microsoft-csp",
			ref: "main",
		});
		await renderPage();

		const dialog = await screen.findByTestId("solution-dialog");
		expect(within(dialog).getByTestId("repo-url")).toHaveValue(
			"https://github.com/acme/solutions",
		);
		expect(within(dialog).getByTestId("repo-subpath")).toHaveValue(
			"microsoft-csp",
		);
		expect(within(dialog).getByTestId("repo-ref")).toHaveValue("main");

		// The deep-link params are consumed (stripped) so a refresh won't re-open.
		await waitFor(() => expect(mockSetSearchParams).toHaveBeenCalled());
	});

	it("does not open a dialog when there is no ?repo param", async () => {
		await renderPage();
		await screen.findByText(/no solutions installed yet/i);
		expect(screen.queryByTestId("solution-dialog")).toBeNull();
	});
});
