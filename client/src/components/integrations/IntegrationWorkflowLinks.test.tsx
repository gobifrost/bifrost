import { act, useState, useEffect } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, within } from "@/test-utils";
import type { components } from "@/lib/v1";
import { IntegrationWorkflowLinks } from "./IntegrationWorkflowLinks";
type WorkflowLink = components["schemas"]["IntegrationWorkflowLink"];
const mocks = vi.hoisted(() => ({
	execute: vi.fn(),
	update: vi.fn(),
	setSaved: (_links: WorkflowLink[]) => {},
	canRun: true,
	width: 1000,
	resize: [] as (() => void)[],
}));
vi.mock("@/services/integrations", () => ({
	useUpdateIntegration: () => ({
		isPending: false,
		mutateAsync: async (request: {
			body: { workflow_links: WorkflowLink[] };
		}) => {
			await mocks.update(request);
			mocks.setSaved(request.body.workflow_links);
		},
	}),
}));
function Fixture({
	integrationName,
	canManage,
}: {
	integrationName: string;
	canManage: boolean;
}) {
	const [saved, setSaved] = useState<WorkflowLink[]>([]);
	useEffect(() => {
		mocks.setSaved = setSaved;
	}, []);
	return (
		<IntegrationWorkflowLinks
			integrationId="integration"
			integrationName={integrationName}
			canManage={canManage}
			savedLinks={saved}
		/>
	);
}
vi.stubGlobal(
	"ResizeObserver",
	class {
		constructor(private callback: () => void) {}
		observe(target: Element) {
			if (target.getAttribute("aria-label") === "Integration workflows")
				mocks.resize.push(this.callback);
		}
		disconnect() {
			mocks.resize = mocks.resize.filter(
				(callback) => callback !== this.callback,
			);
		}
	},
);
vi.mock("@/hooks/useWorkflows", () => ({
	useWorkflowsMetadata: () => ({
		data: {
			workflows: [
				{
					id: "sync",
					name: "A Very Long Customer Synchronization Workflow Name",
					type: "workflow",
					parameters: [],
				},
				{
					id: "refresh",
					name: "Refresh Tokens for Each Linked Organization",
					type: "workflow",
					parameters: [],
				},
			],
		},
	}),
	useExecuteWorkflow: () => ({
		mutateAsync: mocks.execute,
		isPending: false,
	}),
}));
vi.mock("@/hooks/useExecutions", () => ({
	useExecutions: () => ({ data: { executions: [] } }),
}));
vi.mock("@/hooks/useExecutionStream", () => ({ useExecutionHistory: vi.fn() }));
vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({ user: { id: "user" } }),
}));
vi.mock("@/services/authorization", () => ({
	useAuthorization: () => ({
		canAt: () => mocks.canRun,
		canAnywhere: () => false,
	}),
}));
vi.mock("@/components/execution/ExecutionOrganizationSelect", () => ({
	ExecutionOrganizationSelect: () => <p>Run in Organization</p>,
}));
vi.mock("@/pages/ExecutionHistory/components/ExecutionDrawer", () => ({
	ExecutionDrawer: ({
		executionId,
		open,
	}: {
		executionId: string;
		open: boolean;
	}) =>
		open ? (
			<div role="region" aria-label="History execution drawer">
				{executionId}
			</div>
		) : null,
}));
beforeEach(() => {
	mocks.execute.mockReset();
	mocks.update.mockReset();
	mocks.canRun = true;
	mocks.width = 1000;
	mocks.resize = [];
	vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockImplementation(
		() => mocks.width,
	);
	vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockImplementation(
		function (this: HTMLElement) {
			return (this as HTMLElement).textContent?.includes("Workflows")
				? 100
				: 150;
		},
	);
});
async function openManage(
	user: ReturnType<typeof renderWithProviders>["user"],
) {
	await user.click(screen.getByRole("button", { name: "Workflows" }));
	await user.click(
		screen.getByRole("menuitem", { name: "Manage Linked Workflows" }),
	);
}
async function link(
	user: ReturnType<typeof renderWithProviders>["user"],
	name: string,
	label: string,
) {
	await user.click(screen.getByRole("combobox", { name: "Workflow" }));
	await user.click(screen.getByRole("option", { name }));
	await user.type(
		screen.getByRole("textbox", { name: "Button Label" }),
		label,
	);
	await user.click(screen.getByRole("button", { name: "Link Workflow" }));
}
describe("integration-owned workflow links", () => {
	it("keeps all labeled links in the menu and promotes only pinned links to top buttons", async () => {
		const { user } = renderWithProviders(
			<Fixture integrationName="Partner" canManage />,
		);
		await openManage(user);
		await link(
			user,
			"A Very Long Customer Synchronization Workflow Name",
			"Sync Now",
		);
		await user.click(
			screen.getByRole("checkbox", { name: "Pin as Top Button" }),
		);
		await link(
			user,
			"Refresh Tokens for Each Linked Organization",
			"Refresh Tokens",
		);
		await user.click(screen.getByRole("button", { name: "Save Changes" }));
		const top = screen.getByRole("group", {
			name: "Integration workflows",
		});
		expect(
			within(top).getByRole("button", { name: "Sync Now" }),
		).toBeVisible();
		expect(
			within(top).queryByRole("button", { name: "Refresh Tokens" }),
		).not.toBeInTheDocument();
		await user.click(
			within(top).getByRole("button", { name: /Workflows/ }),
		);
		expect(
			screen.getByRole("menuitem", { name: /Sync Now/ }),
		).toBeVisible();
		expect(
			screen.getByRole("menuitem", { name: /Refresh Tokens/ }),
		).toBeVisible();
	});
	it("launches the actual linked workflow and opens the shared execution drawer", async () => {
		mocks.execute.mockResolvedValue({ execution_id: "accepted-run" });
		const { user } = renderWithProviders(
			<Fixture integrationName="Partner" canManage />,
		);
		await openManage(user);
		await link(
			user,
			"A Very Long Customer Synchronization Workflow Name",
			"Sync Now",
		);
		await user.click(
			screen.getByRole("checkbox", { name: "Pin as Top Button" }),
		);
		await user.click(screen.getByRole("button", { name: "Save Changes" }));
		await user.click(screen.getByRole("button", { name: "Sync Now" }));
		await user.click(screen.getByRole("button", { name: "Run Workflow" }));
		expect(mocks.execute.mock.calls[0][0].body).toMatchObject({
			workflow_id: "sync",
			input_data: {},
		});
		expect(
			await screen.findByRole("region", {
				name: "History execution drawer",
			}),
		).toHaveTextContent("accepted-run");
	});
	it("does not expose relationship management to a reader", async () => {
		const { user } = renderWithProviders(
			<Fixture integrationName="Partner" canManage={false} />,
		);
		await user.click(screen.getByRole("button", { name: "Workflows" }));
		expect(
			screen.queryByRole("menuitem", { name: "Manage Linked Workflows" }),
		).not.toBeInTheDocument();
	});
});

it("moves pinned buttons into the existing menu when space runs out and restores them on resize", async () => {
	const { user } = renderWithProviders(
		<Fixture integrationName="Partner" canManage />,
	);
	await openManage(user);
	await link(
		user,
		"A Very Long Customer Synchronization Workflow Name",
		"Sync Now",
	);
	await user.click(
		screen.getByRole("checkbox", { name: "Pin as Top Button" }),
	);
	await link(
		user,
		"Refresh Tokens for Each Linked Organization",
		"Refresh Tokens",
	);
	await user.click(
		screen.getAllByRole("checkbox", { name: "Pin as Top Button" })[1],
	);
	await user.click(screen.getByRole("button", { name: "Save Changes" }));
	const top = screen.getByRole("group", { name: "Integration workflows" });
	expect(
		within(top).getByRole("button", { name: "Refresh Tokens" }),
	).toBeVisible();
	act(() => {
		mocks.width = 300;
		mocks.resize.forEach((callback) => callback());
	});
	expect(within(top).getByRole("button", { name: "Sync Now" })).toBeVisible();
	expect(
		within(top).queryByRole("button", { name: "Refresh Tokens" }),
	).not.toBeInTheDocument();
	await user.click(within(top).getByRole("button", { name: /Workflows/ }));
	expect(
		screen.getByRole("menuitem", { name: /Refresh Tokens/ }),
	).toBeVisible();
	await user.keyboard("{Escape}");
	act(() => {
		mocks.width = 180;
		mocks.resize.forEach((callback) => callback());
	});
	expect(
		within(top).queryByRole("button", { name: "Sync Now" }),
	).not.toBeInTheDocument();
	expect(
		within(top).getByRole("button", { name: /Workflows/ }),
	).toBeVisible();
	act(() => {
		mocks.width = 700;
		mocks.resize.forEach((callback) => callback());
	});
	expect(
		within(top).getByRole("button", { name: "Refresh Tokens" }),
	).toBeVisible();
});

it("lists pinned workflows before earlier unpinned links in the dropdown", async () => {
	const { user } = renderWithProviders(
		<Fixture integrationName="Partner" canManage />,
	);
	await openManage(user);
	await link(
		user,
		"A Very Long Customer Synchronization Workflow Name",
		"Sync Now",
	);
	await link(
		user,
		"Refresh Tokens for Each Linked Organization",
		"Refresh Tokens",
	);
	await user.click(
		screen.getAllByRole("checkbox", { name: "Pin as Top Button" })[1],
	);
	await user.click(screen.getByRole("button", { name: "Save Changes" }));
	await user.click(
		within(
			screen.getByRole("group", { name: "Integration workflows" }),
		).getByRole("button", { name: /Workflows/ }),
	);
	const items = screen.getAllByRole("menuitem");
	expect(items[0]).toHaveTextContent("Refresh Tokens");
	expect(items[1]).toHaveTextContent("Sync Now");
});

it("keeps failed saves visible and retains edits for correction", async () => {
	mocks.update.mockRejectedValueOnce(new Error("Save rejected"));
	const { user } = renderWithProviders(
		<Fixture integrationName="Partner" canManage />,
	);
	await openManage(user);
	await link(
		user,
		"A Very Long Customer Synchronization Workflow Name",
		"Sync Now",
	);
	await user.click(screen.getByRole("button", { name: "Save Changes" }));
	expect(await screen.findByRole("alert")).toHaveTextContent("Save rejected");
	expect(
		screen.getByRole("dialog", { name: "Linked Workflows" }),
	).toBeVisible();
	expect(screen.getByRole("textbox", { name: "Label" })).toHaveValue(
		"Sync Now",
	);
	await user.click(screen.getByRole("button", { name: "Cancel" }));
	await openManage(user);
	expect(
		screen.queryByRole("textbox", { name: "Label" }),
	).not.toBeInTheDocument();
});

it("retains missing workflow labels but disables their shortcuts", async () => {
	const { user } = renderWithProviders(
		<IntegrationWorkflowLinks
			integrationId="integration"
			integrationName="Partner"
			canManage={false}
			savedLinks={[
				{ workflow_id: "deleted", label: "Retired Sync", pinned: true },
			]}
		/>,
	);
	expect(screen.getByRole("button", { name: "Retired Sync" })).toBeDisabled();
	await user.click(screen.getByRole("button", { name: /Workflows/ }));
	expect(
		screen.getByRole("menuitem", { name: /Retired Sync/ }),
	).toHaveAttribute("aria-disabled", "true");
});

 it("keeps the compact workflow control named and its menu available", async () => {
  const { user } = renderWithProviders(<IntegrationWorkflowLinks compactOnMobile integrationId="integration" integrationName="AWS" canManage={false} savedLinks={[]} />);
  await user.click(screen.getByRole("button", { name: "Workflows" }));
  expect(screen.getByText("Linked Workflows")).toBeInTheDocument();
 });
