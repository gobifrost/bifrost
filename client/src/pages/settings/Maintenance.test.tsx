import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";

const { mockAuthFetch, mockExportAll, mockToast } = vi.hoisted(() => ({
	mockAuthFetch: vi.fn(),
	mockExportAll: vi.fn(),
	mockToast: {
		success: vi.fn(),
		error: vi.fn(),
		info: vi.fn(),
		warning: vi.fn(),
	},
}));

vi.mock("@/lib/api-client", () => ({
	authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}));

vi.mock("@/services/exportImport", () => ({
	exportAll: (...args: unknown[]) => mockExportAll(...args),
}));

vi.mock("sonner", () => ({
	toast: mockToast,
}));

vi.mock("@/pages/settings/ArtifactRetentionSettings", () => ({
	ArtifactRetentionSettings: () => (
		<section>Artifact retention settings</section>
	),
}));

vi.mock("@/pages/settings/AuditRetentionSettings", () => ({
	AuditRetentionSettings: () => <section>Audit retention settings</section>,
}));

vi.mock("@/components/ImportDialog", () => ({
	ImportDialog: ({
		open,
		entityType,
	}: {
		open: boolean;
		entityType: string;
	}) =>
		open ? (
			<div role="dialog" aria-label={`Import dialog for ${entityType}`}>
				Import dialog for {entityType}
			</div>
		) : null,
}));

import { Maintenance } from "./Maintenance";

function jsonResponse(body: unknown, ok = true) {
	return {
		ok,
		json: () => Promise.resolve(body),
	} as Response;
}

beforeEach(() => {
	vi.useRealTimers();
	vi.clearAllMocks();
	mockAuthFetch.mockReset();
	mockExportAll.mockReset();
});

afterEach(() => {
	vi.restoreAllMocks();
	vi.useRealTimers();
});

describe("Maintenance", () => {
	it("runs selected documentation and app dependency actions in fixed queue order and shows the last results", async () => {
		mockAuthFetch
			.mockResolvedValueOnce(
				jsonResponse({
					status: "complete",
					files_indexed: 3,
					files_unchanged: 2,
					files_deleted: 1,
					duration_ms: 750,
					message: "Docs indexed",
				}),
			)
			.mockResolvedValueOnce(
				jsonResponse({
					apps_scanned: 4,
					files_scanned: 12,
					dependencies_rebuilt: 5,
					issues_found: 1,
					issues: [
						{
							app_id: "app-1",
							app_name: "Ticket App",
							app_slug: "ticket-app",
							file_path: "apps/ticket/App.tsx",
							dependency_type: "workflow",
							dependency_id: "missing_workflow",
						},
					],
					notification_created: true,
				}),
			);

		const { user } = renderWithProviders(<Maintenance />);

		await user.click(screen.getByLabelText(/Index Documents/));
		await user.click(screen.getByLabelText(/Rebuild App Dependencies/));
		await user.click(
			screen.getByRole("button", { name: "Run Selected (2)" }),
		);

		await waitFor(() => expect(mockAuthFetch).toHaveBeenCalledTimes(2));
		expect(mockAuthFetch).toHaveBeenNthCalledWith(
			1,
			"/api/maintenance/index-docs",
			{ method: "POST" },
		);
		expect(mockAuthFetch).toHaveBeenNthCalledWith(
			2,
			"/api/maintenance/scan-app-dependencies",
			{ method: "POST" },
		);
		expect(mockToast.success).toHaveBeenCalledWith(
			"Documentation indexed successfully",
			expect.objectContaining({ description: "Docs indexed" }),
		);
		expect(mockToast.warning).toHaveBeenCalledWith(
			"Dependencies rebuilt with issues",
			expect.objectContaining({
				description:
					"Rebuilt 5 dependencies, found 1 broken references",
			}),
		);
		expect(
			await screen.findByRole("region", {
				name: "Missing workflow references",
			}),
		).toHaveTextContent("apps/ticket/App.tsx");
		expect(screen.getByText("missing_workflow")).toBeVisible();
	});

	it("keeps reimport completion errors visible and leaves the action retryable", async () => {
		mockAuthFetch
			.mockResolvedValueOnce(jsonResponse({ job_id: "job-1" }))
			.mockResolvedValueOnce(
				jsonResponse({
					status: "failed",
					error: "Synthetic reimport failure",
				}),
			);

		const { user } = renderWithProviders(<Maintenance />);

		await user.click(screen.getByLabelText(/Reimport from Repository/));
		await user.click(
			screen.getByRole("button", { name: "Run Selected (1)" }),
		);

		await waitFor(() =>
			expect(mockAuthFetch).toHaveBeenCalledWith(
				"/api/maintenance/reimport",
				expect.objectContaining({ method: "POST" }),
			),
		);
		await new Promise((resolve) => setTimeout(resolve, 2100));

		await waitFor(() =>
			expect(mockToast.error).toHaveBeenCalledWith(
				"Reimport failed",
				expect.objectContaining({
					description: "Synthetic reimport failure",
				}),
			),
		);
		expect(
			screen.getByText("Failed. Run the selected action again to retry."),
		).toBeVisible();
		expect(
			screen.getByRole("button", { name: "Run Selected (1)" }),
		).toBeEnabled();
	});

	it("invokes Export All through the export service and restores the button", async () => {
		mockExportAll.mockResolvedValueOnce(undefined);
		const { user } = renderWithProviders(<Maintenance />);

		await user.click(screen.getByRole("button", { name: "Export All" }));

		await waitFor(() => expect(mockExportAll).toHaveBeenCalledWith({}));
		expect(mockToast.success).toHaveBeenCalledWith("Export downloaded");
		expect(
			screen.getByRole("button", { name: "Export All" }),
		).toBeEnabled();
	});

	it("wires Import All to the all-entity import dialog", async () => {
		const { user } = renderWithProviders(<Maintenance />);

		await user.click(screen.getByRole("button", { name: "Import All" }));

		expect(
			screen.getByRole("dialog", { name: /Import dialog for all/ }),
		).toBeVisible();
	});
});
