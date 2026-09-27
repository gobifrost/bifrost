import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/detail-route-loaders", () => ({
	prefetchApplicationDetail: vi.fn(),
}));

vi.mock("@/components/solutions/SolutionManagedBadge", () => ({
	SolutionManagedBadge: ({ solutionId }: { solutionId?: string | null }) => (
		<a href={`/solutions/${solutionId}`} aria-label="Managed by a Solution">
			Solution managed
		</a>
	),
}));

import {
	ApplicationListSurface,
	type ApplicationListItem,
} from "./ApplicationListSurface";

function makeApp(
	overrides: Partial<ApplicationListItem> = {},
): ApplicationListItem {
	return {
		id: "11111111-1111-1111-1111-111111111111",
		name: "Dispatch Board",
		description: "Dispatch queue",
		icon: null,
		slug: "dispatch-board",
		organization_id: null,
		published_at: "2026-09-12T12:00:00Z",
		deployed_at: "2026-09-12T12:00:00Z",
		created_at: "2026-09-12T12:00:00Z",
		updated_at: "2026-09-12T12:00:00Z",
		created_by: null,
		is_published: true,
		has_unpublished_changes: false,
		access_level: "authenticated",
		app_model: "standalone_v2",
		is_solution_managed: false,
		solution_id: null,
		role_ids: [],
		repo_path: null,
		logo: null,
		logo_url: null,
		logo_version: null,
		sdk_package_version: "1.0.0",
		sdk_fingerprint: "old",
		sdk_contract_version: 1,
		sdk_built_at: "2026-09-12T12:00:00Z",
		sdk_status: "update_available",
		sdk_source_available: true,
		...overrides,
	};
}

function renderSurface(
	props: Partial<React.ComponentProps<typeof ApplicationListSurface>> = {},
) {
	return render(
		<ApplicationListSurface
			apps={[makeApp()]}
			viewMode="grid"
			isPlatformAdmin={false}
			canManageApps={true}
			getOrgName={() => "Global"}
			onLaunch={vi.fn()}
			onUpdateSdk={vi.fn()}
			{...props}
		/>,
	);
}

describe("ApplicationListSurface SDK update affordances", () => {
	it("renders published app names as native links while preserving normal launch callbacks", async () => {
		const user = userEvent.setup();
		const onLaunch = vi.fn();
		renderSurface({ onLaunch });

		const link = screen.getByRole("link", { name: "Dispatch Board" });
		expect(link).toHaveAttribute("href", "/apps/dispatch-board");

		await user.click(link);
		expect(onLaunch).toHaveBeenCalledWith(
			expect.objectContaining({ id: makeApp().id }),
		);
	});

	it("passes the published app href through the shared catalog card link", () => {
		renderSurface();

		expect(
			screen.getByRole("link", { name: "Dispatch Board" }),
		).toHaveAttribute("href", "/apps/dispatch-board");
	});

	it("leaves modified app name clicks to native link behavior", () => {
		const onLaunch = vi.fn();
		renderSurface({ onLaunch });

		fireEvent.click(screen.getByRole("link", { name: "Dispatch Board" }), {
			ctrlKey: true,
		});

		expect(onLaunch).not.toHaveBeenCalled();
	});

	it("uses preview hrefs for unpublished legacy app names", async () => {
		const user = userEvent.setup();
		const onPreview = vi.fn();
		renderSurface({
			apps: [
				makeApp({
					app_model: "legacy",
					is_published: false,
					has_unpublished_changes: true,
				}),
			],
			onPreview,
		});

		const link = screen.getByRole("link", { name: "Dispatch Board" });
		expect(link).toHaveAttribute("href", "/apps/dispatch-board/preview");

		await user.click(link);
		expect(onPreview).toHaveBeenCalledWith(
			expect.objectContaining({ id: makeApp().id }),
		);
	});

	it("toggles actionable cards as whole-card controls in selection mode", async () => {
		const user = userEvent.setup();
		const onToggleSelection = vi.fn();
		renderSurface({
			apps: [
				makeApp({ id: "app-1", name: "Dispatch Board" }),
				makeApp({
					id: "app-2",
					name: "Asset Intake",
					sdk_status: "update_required",
					sdk_source_available: false,
				}),
			],
			selectionMode: true,
			selectedIds: new Set(["app-1"]),
			onToggleSelection,
		});

		const selectedCard = screen.getByRole("button", {
			name: /dispatch board/i,
		});
		expect(selectedCard).toHaveAttribute("aria-pressed", "true");
		expect(screen.getByLabelText("Dispatch Board selected")).toBeVisible();

		await user.click(selectedCard);
		fireEvent.keyDown(selectedCard, { key: "Enter" });

		expect(onToggleSelection).toHaveBeenCalledTimes(2);
		expect(onToggleSelection).toHaveBeenNthCalledWith(
			1,
			expect.objectContaining({ id: "app-1" }),
		);
		expect(
			screen.getByRole("article", { name: /asset intake/i }),
		).toHaveAttribute("aria-disabled", "true");
		expect(
			screen.queryByRole("button", { name: /asset intake/i }),
		).not.toBeInTheDocument();
	});

	it("uses semantic checkboxes for table selection and only selects actionable apps", async () => {
		const user = userEvent.setup();
		const onToggleSelection = vi.fn();
		const onToggleSelectAllVisible = vi.fn();
		renderSurface({
			viewMode: "table",
			apps: [
				makeApp({ id: "app-1", name: "Dispatch Board" }),
				makeApp({
					id: "app-2",
					name: "Client Portal",
					sdk_status: "current",
					sdk_source_available: true,
				}),
			],
			selectionMode: true,
			selectedIds: new Set(["app-1"]),
			onToggleSelection,
			onToggleSelectAllVisible,
		});

		const selectAll = screen.getByRole("checkbox", {
			name: "Select all visible Applications with SDK updates",
		});
		expect(selectAll).toBeChecked();
		await user.click(selectAll);
		expect(onToggleSelectAllVisible).toHaveBeenCalledOnce();

		const rowCheckbox = screen.getByRole("checkbox", {
			name: "Select Dispatch Board",
		});
		expect(rowCheckbox).toBeChecked();
		await user.click(rowCheckbox);
		expect(onToggleSelection).toHaveBeenCalledWith(
			expect.objectContaining({ id: "app-1" }),
		);
		expect(
			screen.getByRole("checkbox", { name: "Select Client Portal" }),
		).toBeDisabled();
	});

	it("shows SDK drift in the status cluster and queues updates from the overflow menu", async () => {
		const user = userEvent.setup();
		const onUpdateSdk = vi.fn();
		renderSurface({ onUpdateSdk });

		expect(screen.getByText("SDK update available")).toBeVisible();

		await user.click(
			screen.getByRole("button", { name: "Dispatch Board actions" }),
		);
		await user.click(screen.getByRole("menuitem", { name: /update sdk/i }));

		expect(onUpdateSdk).toHaveBeenCalledWith(
			expect.objectContaining({ id: makeApp().id }),
		);
	});

	it("renders source-unavailable state without enabling SDK update", async () => {
		const user = userEvent.setup();
		const onUpdateSdk = vi.fn();
		renderSurface({
			apps: [
				makeApp({
					sdk_status: "update_required",
					sdk_source_available: false,
				}),
			],
			onUpdateSdk,
		});

		expect(screen.getByText("SDK update required")).toBeVisible();
		expect(screen.getByText("Source unavailable")).toBeVisible();

		await user.click(
			screen.getByRole("button", { name: "Dispatch Board actions" }),
		);

		expect(
			screen.getByRole("menuitem", { name: /source unavailable/i }),
		).toHaveAttribute("aria-disabled", "true");
		expect(onUpdateSdk).not.toHaveBeenCalled();
	});

	it("offers rebuild for unknown SDK status when source is available", async () => {
		const user = userEvent.setup();
		const onUpdateSdk = vi.fn();
		renderSurface({
			apps: [
				makeApp({
					sdk_status: "unknown",
					sdk_source_available: true,
				}),
			],
			onUpdateSdk,
		});

		expect(screen.getByText("SDK unknown")).toBeVisible();

		await user.click(
			screen.getByRole("button", { name: "Dispatch Board actions" }),
		);
		await user.click(
			screen.getByRole("menuitem", { name: /rebuild sdk/i }),
		);

		expect(onUpdateSdk).toHaveBeenCalledOnce();
	});

	it("offers retry when the tracked SDK update job failed and source is available", async () => {
		const user = userEvent.setup();
		const onUpdateSdk = vi.fn();
		renderSurface({
			apps: [
				makeApp({
					sdk_status: "update_available",
					sdk_source_available: true,
				}),
			],
			getSdkUpdateState: () => "failed",
			onUpdateSdk,
		});

		expect(screen.getByText("SDK update failed")).toBeVisible();

		await user.click(
			screen.getByRole("button", { name: "Dispatch Board actions" }),
		);
		await user.click(
			screen.getByRole("menuitem", { name: /retry sdk update/i }),
		);

		expect(onUpdateSdk).toHaveBeenCalledOnce();
	});

	it("keeps SDK-only actions available for solution-managed table rows", async () => {
		const user = userEvent.setup();
		const onUpdateSdk = vi.fn();
		renderSurface({
			viewMode: "table",
			apps: [
				makeApp({
					is_solution_managed: true,
					solution_id: "sol-1",
				}),
			],
			onUpdateSdk,
			onOpenSettings: vi.fn(),
			onDelete: vi.fn(),
		});

		await user.click(
			screen.getByRole("button", { name: "Dispatch Board actions" }),
		);

		expect(
			screen.getByRole("menuitem", { name: /update sdk/i }),
		).toBeVisible();
		expect(
			screen.queryByRole("menuitem", { name: /settings/i }),
		).toBeNull();
		expect(screen.queryByRole("menuitem", { name: /delete/i })).toBeNull();
	});

	it("uses row hrefs and keeps table actions in the overflow menu", async () => {
		const user = userEvent.setup();
		const onLaunch = vi.fn();
		const onPreview = vi.fn();
		const onOpenSettings = vi.fn();
		const onOpenCode = vi.fn();
		const open = vi.spyOn(window, "open").mockImplementation(() => null);
		renderSurface({
			viewMode: "table",
			apps: [
				makeApp({ id: "published-app", name: "Published App" }),
				makeApp({
					id: "draft-app",
					name: "Draft App",
					slug: "draft-app",
					app_model: "legacy",
					is_published: false,
					has_unpublished_changes: true,
				}),
			],
			onLaunch,
			onPreview,
			onOpenSettings,
			onOpenCode,
		});

		fireEvent.click(screen.getAllByText("Dispatch queue")[0], {
			ctrlKey: true,
		});
		expect(open).toHaveBeenCalledWith("/apps/dispatch-board", "_blank");
		expect(onLaunch).not.toHaveBeenCalled();
		expect(screen.queryByTitle("Preview draft")).not.toBeInTheDocument();
		expect(screen.queryByTitle("Open application")).not.toBeInTheDocument();

		await user.click(
			screen.getByRole("button", { name: "Draft App actions" }),
		);
		expect(
			screen.getByRole("menuitem", { name: "Preview" }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("menuitem", { name: "Edit" }),
		).toBeInTheDocument();
	});
});

it.each(["grid", "table"] as const)(
	"preserves solution context in %s links",
	(viewMode) => {
		renderSurface({
			viewMode,
			navigationSearch: "?from=solution:install-1",
		});
		expect(
			screen.getByRole("link", { name: "Dispatch Board" }),
		).toHaveAttribute(
			"href",
			"/apps/dispatch-board?from=solution:install-1",
		);
	},
);
