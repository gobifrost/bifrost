import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@/lib/api-error";
import {
	canAnywhere,
	canAt,
	type AuthorizationSummary,
	type AuthorizationTarget,
} from "@/lib/authorization";
import type {
	AccessTrace,
	PermissionCatalogEntry,
	UserAccessMap,
} from "@/services/access";

const check = vi.hoisted(() => ({ mutateAsync: vi.fn() }));
const accessMap: UserAccessMap = {
	user_id: "user-1",
	name: "Avery Example",
	email: "avery@contoso.example",
	home_organization: { id: "org-1", name: "Contoso" },
	is_platform_admin: false,
	is_protected: false,
	privileged_permissions: [],
	reach: [
		{
			kind: "home",
			organization_id: "org-1",
			organization_name: "Contoso",
			label: "Contoso (Home)",
		},
		{
			kind: "organization",
			organization_id: "org-2",
			organization_name: "Fabrikam",
			label: "Fabrikam",
		},
	],
	rows: [
		{
			place: {
				kind: "organization",
				organization_id: "org-2",
				organization_name: "Fabrikam",
				label: "Fabrikam",
			},
			grants: [
				{
					permission: "tables.read",
					domain: "tables",
					action: "read",
					scope: "per_organization",
					sources: [
						{
							role_id: "role-helpdesk",
							role_name: "Helpdesk",
							via: "additional",
						},
					],
				},
			],
		},
	],
};
const catalog: PermissionCatalogEntry[] = [
	{
		domain: "tables",
		title: "Tables",
		area: "Data & Content",
		description: "",
		who_should_hold: "",
		actions: ["read", "readwrite"],
		names: {
			"tables.read": "Read Tables",
			"tables.readwrite": "Read and Write Tables",
		},
		privileged: [],
		scope: "per_organization",
		enforced: true,
	},
];
vi.mock("@/services/access", () => ({
	useUserAccessMap: () => ({ data: accessMap }),
	usePermissionCatalog: () => ({ data: catalog }),
	useCheckUserAccess: () => ({ ...check, isPending: false }),
}));

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: (options: { enabled?: boolean }) => ({
		data: options.enabled
			? [
					{ id: "org-1", name: "Contoso" },
					{ id: "org-3", name: "Northwind" },
				]
			: undefined,
	}),
}));

vi.mock("@/hooks/useWorkflows", () => ({
	useWorkflowsMetadata: () => ({
		data: {
			workflows: [
				{
					id: "wf-1",
					name: "nightly_sync",
					display_name: "Nightly Sync",
				},
			],
		},
	}),
}));

const authz = vi.hoisted(() => ({
	summary: undefined as AuthorizationSummary | undefined,
}));
vi.mock("@/services/authorization", () => ({
	useAuthorization: () => ({
		canAt: (permission: string, target: AuthorizationTarget) =>
			canAt(authz.summary, permission, target),
		canAnywhere: (permission: string) =>
			canAnywhere(authz.summary, permission),
	}),
}));

import { TestAccessPanel } from "./TestAccessPanel";

const PROVIDER = "org-provider";

function adminSummary(): AuthorizationSummary {
	return {
		is_platform_admin: true,
		home_organization_id: PROVIDER,
		provider_organization_id: PROVIDER,
		base_role: { id: "admin-role", name: "Platform Admin" },
		grants: [],
	};
}

const trace: AccessTrace = {
	outcome: "success",
	enforced: false,
	steps: [
		{
			key: "run_user",
			label: "Run user",
			status: "passed",
			reason: "person",
			facts: { user_id: "user-1", is_platform_admin: false },
		},
		{
			key: "powers",
			label: "Workflow powers",
			status: "not_applicable",
			reason: "no_workflow",
			facts: {},
		},
		{
			key: "target",
			label: "Target in reach",
			status: "passed",
			reason: "role:role-helpdesk@organization",
			facts: { organization_id: "org-2" },
		},
		{
			key: "permission",
			label: "Permission",
			status: "passed",
			reason: "role:role-helpdesk:tables.read@organization",
			facts: { permission: "tables.read" },
		},
	],
};

async function choose(
	user: ReturnType<typeof userEvent.setup>,
	name: string,
	option: string,
) {
	await user.click(screen.getByRole("combobox", { name }));
	await user.click(await screen.findByRole("option", { name: option }));
}

beforeEach(() => {
	authz.summary = adminSummary();
	check.mutateAsync.mockReset();
	check.mutateAsync.mockResolvedValue(trace);
});

describe("TestAccessPanel", () => {
	it("tests a typed operation in an organization and shows the trace", async () => {
		const user = userEvent.setup();
		render(<TestAccessPanel subjectId="user-1" />);

		await choose(user, "Organization", "Fabrikam");
		await user.type(
			screen.getByRole("combobox", { name: "Operation" }),
			"GET /api/tables",
		);
		await user.click(screen.getByRole("button", { name: "Test Access" }));

		expect(check.mutateAsync).toHaveBeenCalledWith({
			params: { path: { user_id: "user-1" } },
			body: {
				organization_id: "org-2",
				operation: "GET /api/tables",
				workflow_id: null,
			},
		});
		const strip = await screen.findByRole("list", { name: "Access Trace" });
		expect(strip).toHaveTextContent("Helpdesk is placed on Fabrikam.");
		expect(strip).toHaveTextContent("Helpdesk grants Read Tables there.");
	});

	it('sends Global as "global", with a suggested operation', async () => {
		const user = userEvent.setup();
		render(<TestAccessPanel subjectId="user-1" />);

		await choose(user, "Organization", "Global");
		await user.type(
			screen.getByRole("combobox", { name: "Operation" }),
			"read tab",
		);
		await user.click(screen.getByRole("option", { name: /Read Tables/ }));
		await user.click(screen.getByRole("button", { name: "Test Access" }));

		expect(check.mutateAsync).toHaveBeenCalledWith(
			expect.objectContaining({
				body: {
					organization_id: "global",
					operation: "tables.list",
					workflow_id: null,
				},
			}),
		);
	});

	it("starts from a given workflow and organization", async () => {
		const user = userEvent.setup();
		render(
			<TestAccessPanel
				subjectId="identity-1"
				defaultWorkflowId="wf-1"
				defaultOrganizationId="org-1"
			/>,
		);

		expect(
			screen.getByRole("combobox", { name: "Workflow" }),
		).toHaveTextContent("Nightly Sync");
		await user.type(
			screen.getByRole("combobox", { name: "Operation" }),
			"agents.list",
		);
		await user.click(screen.getByRole("button", { name: "Test Access" }));

		expect(check.mutateAsync).toHaveBeenCalledWith({
			params: { path: { user_id: "identity-1" } },
			body: {
				organization_id: "org-1",
				operation: "agents.list",
				workflow_id: "wf-1",
			},
		});
	});

	it("offers only the places the caller can test in", async () => {
		authz.summary = {
			...adminSummary(),
			is_platform_admin: false,
			grants: [
				{
					permission: "roleassignments.read",
					boundary: {
						kind: "organization",
						organization_id: "org-2",
					},
				},
			],
		};
		const user = userEvent.setup();
		render(<TestAccessPanel subjectId="user-1" />);

		await user.click(
			screen.getByRole("combobox", { name: "Organization" }),
		);

		expect(
			screen.getAllByRole("option").map((option) => option.textContent),
		).toEqual(["Fabrikam"]);
	});

	it("ignores an answer for selections that have since changed", async () => {
		let answer: (trace: AccessTrace) => void = () => {};
		check.mutateAsync.mockReturnValue(
			new Promise<AccessTrace>((resolve) => {
				answer = resolve;
			}),
		);
		const user = userEvent.setup();
		render(
			<TestAccessPanel
				subjectId="user-1"
				defaultOrganizationId="org-1"
			/>,
		);
		const operation = screen.getByRole("combobox", { name: "Operation" });

		await user.type(operation, "agents.list");
		await user.click(screen.getByRole("button", { name: "Test Access" }));
		await user.type(operation, "x");
		await act(async () => answer(trace));

		expect(
			screen.queryByRole("list", { name: "Access Trace" }),
		).not.toBeInTheDocument();
		expect(screen.queryByText("Would be allowed")).not.toBeInTheDocument();
	});

	it("explains a refused check", async () => {
		check.mutateAsync.mockRejectedValue(
			new ApiError(
				"Unknown operation 'nope': use an access-list catalog id or \"METHOD /api/path\"",
				422,
			),
		);
		const user = userEvent.setup();
		render(
			<TestAccessPanel
				subjectId="user-1"
				defaultOrganizationId="global"
			/>,
		);

		await user.type(
			screen.getByRole("combobox", { name: "Operation" }),
			"nope",
		);
		await user.click(screen.getByRole("button", { name: "Test Access" }));

		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Unknown operation 'nope'",
		);
		expect(
			within(document.body).queryByRole("list", { name: "Access Trace" }),
		).not.toBeInTheDocument();
	});
});
