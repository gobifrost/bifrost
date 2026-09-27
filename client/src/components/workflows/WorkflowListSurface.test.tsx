import { describe, expect, it, vi } from "vitest";
import { useLocation } from "react-router-dom";
import { renderWithProviders, screen } from "@/test-utils";
import {
	WorkflowListSurface,
	type WorkflowListItem,
} from "./WorkflowListSurface";

function LocationProbe() {
	const location = useLocation();
	return (
		<output aria-label="location">
			{location.pathname + location.search}
		</output>
	);
}

describe("workflow recovery actions", () => {
	it.each(["grid", "table"] as const)(
		"keeps endpoint and missing-file actions keyboard accessible in %s",
		async (viewMode) => {
			const workflow = {
				id: "review-workflow",
				name: "review_workflow",
				type: "workflow",
				endpoint_enabled: true,
				is_orphaned: true,
			} as WorkflowListItem;
			const onEditEndpoint = vi.fn();
			const onResolveOrphaned = vi.fn();
			const onExecute = vi.fn();
			const { user } = renderWithProviders(
				<WorkflowListSurface
					workflows={[workflow]}
					viewMode={viewMode}
					isPlatformAdmin
					canManageWorkflows
					getOrgName={() => "Global"}
					onEditEndpoint={onEditEndpoint}
					onResolveOrphaned={onResolveOrphaned}
					onExecute={onExecute}
				/>,
			);
			expect(
				screen.queryByRole("link", { name: "review_workflow" }),
			).not.toBeInTheDocument();
			expect(screen.getByText("review_workflow")).toBeVisible();
			const menu = screen.getByRole("button", {
				name: "review_workflow actions",
			});
			menu.focus();
			await user.keyboard("{Enter}");
			screen.getByRole("menuitem", { name: "Edit endpoint" }).focus();
			await user.keyboard("{Enter}");
			expect(onEditEndpoint).toHaveBeenCalledExactlyOnceWith(workflow);
			await user.click(menu);
			screen
				.getByRole("menuitem", { name: "Resolve missing file" })
				.focus();
			await user.keyboard("{Enter}");
			expect(onResolveOrphaned).toHaveBeenCalledExactlyOnceWith(workflow);
			expect(onExecute).not.toHaveBeenCalled();
		},
	);

	it("opens execution from the grid card primary target", async () => {
		const workflow = {
			id: "review-workflow",
			name: "review_workflow",
			type: "workflow",
		} as WorkflowListItem;
		const onExecute = vi.fn();
		const { user } = renderWithProviders(
			<WorkflowListSurface
				workflows={[workflow]}
				viewMode="grid"
				isPlatformAdmin
				canManageWorkflows
				getOrgName={() => "Global"}
				onExecute={onExecute}
			/>,
		);

		const title = screen.getByRole("link", { name: "review_workflow" });
		expect(title).toHaveAttribute(
			"href",
			"/workflows/review_workflow/execute",
		);

		await user.click(title);

		expect(onExecute).toHaveBeenCalledExactlyOnceWith(workflow);
	});

	it("opens the workflow execute screen from the table row", async () => {
		const workflow = {
			id: "review-workflow",
			name: "review_workflow",
			type: "workflow",
		} as WorkflowListItem;
		const onExecute = vi.fn();
		const { user } = renderWithProviders(
			<>
				<WorkflowListSurface
					workflows={[workflow]}
					viewMode="table"
					isPlatformAdmin
					canManageWorkflows
					getOrgName={() => "Global"}
					onExecute={onExecute}
				/>
				<LocationProbe />
			</>,
		);

		expect(
			screen.queryByRole("button", {
				name: "Execute Workflow: review_workflow",
			}),
		).not.toBeInTheDocument();

		await user.click(screen.getByRole("row", { name: /review_workflow/i }));

		expect(screen.getByLabelText("location")).toHaveTextContent(
			"/workflows/review_workflow/execute",
		);
		expect(onExecute).not.toHaveBeenCalled();
	});
});

it.each(["grid", "table"] as const)(
	"preserves solution context in %s links",
	(viewMode) => {
		const workflow = {
			id: "w1",
			name: "review_workflow",
			type: "workflow",
		} as WorkflowListItem;
		renderWithProviders(
			<WorkflowListSurface
				workflows={[workflow]}
				viewMode={viewMode}
				isPlatformAdmin
				canManageWorkflows
				getOrgName={() => "Global"}
				onExecute={vi.fn()}
				navigationSearch="?from=solution:install-1"
			/>,
		);
		expect(
			screen.getByRole("link", { name: "review_workflow" }),
		).toHaveAttribute(
			"href",
			"/workflows/review_workflow/execute?from=solution:install-1",
		);
	},
);

describe("service rows", () => {
	it.each(["grid", "table"] as const)(
		"labels services and removes execute navigation in %s",
		(viewMode) => {
			const workflow = {
				id: "svc-1",
				name: "telegram_bridge",
				type: "service",
			} as WorkflowListItem;
			renderWithProviders(
				<WorkflowListSurface
					workflows={[workflow]}
					viewMode={viewMode}
					isPlatformAdmin
					canManageWorkflows
					getOrgName={() => "Global"}
					onExecute={vi.fn()}
				/>,
			);

			expect(screen.getByText("Service")).toBeVisible();
			expect(
				screen.queryByRole("link", { name: "telegram_bridge" }),
			).not.toBeInTheDocument();
		},
	);

	it("does not navigate to execute when clicking a service row", async () => {
		const workflow = {
			id: "svc-1",
			name: "telegram_bridge",
			type: "service",
		} as WorkflowListItem;
		const onExecute = vi.fn();
		const { user } = renderWithProviders(
			<>
				<WorkflowListSurface
					workflows={[workflow]}
					viewMode="table"
					isPlatformAdmin
					canManageWorkflows
					getOrgName={() => "Global"}
					onExecute={onExecute}
				/>
				<LocationProbe />
			</>,
		);

		await user.click(screen.getByRole("row", { name: /telegram_bridge/i }));

		expect(screen.getByLabelText("location")).toHaveTextContent("/");
		expect(onExecute).not.toHaveBeenCalled();
	});
});

describe("service detail links", () => {
	it.each(["grid", "table"] as const)(
		"navigates service rows to the detail route in %s mode",
		async (viewMode) => {
			const workflow = {
				id: "svc-1",
				name: "telegram_bridge",
				type: "service",
			} as WorkflowListItem;
			const onExecute = vi.fn();
			const { user } = renderWithProviders(
				<>
					<WorkflowListSurface
						workflows={[workflow]}
						viewMode={viewMode}
						isPlatformAdmin
						canManageWorkflows
						getOrgName={() => "Global"}
						onExecute={onExecute}
						getServiceHref={() => "/services/svc-1"}
					/>
					<LocationProbe />
				</>,
			);

			await user.click(
				screen.getByRole("link", { name: "telegram_bridge" }),
			);

			expect(screen.getByLabelText("location")).toHaveTextContent(
				"/services/svc-1",
			);
			expect(onExecute).not.toHaveBeenCalled();
		},
	);
});
