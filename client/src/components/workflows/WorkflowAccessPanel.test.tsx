import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { components } from "@/lib/v1";
import type { Identity } from "@/services/identities";
import type { UserAccessMap } from "@/services/access";
import type {
	AssignableRole,
	RecommendedAccess,
	RecommendedAccessItem,
} from "@/services/workflowAccess";

type Workflow = components["schemas"]["WorkflowMetadata"];

const CONTOSO = "11111111-1111-4111-8111-111111111111";
const FABRIKAM = "22222222-2222-4222-8222-222222222222";
const PROVIDER = "33333333-3333-4333-8333-333333333333";

function identity(overrides: Partial<Identity>): Identity {
	return {
		id: "identity-default",
		name: "Default Identity",
		identity_kind: "org_default",
		organization_id: CONTOSO,
		organization_name: "Contoso",
		base_role: { id: "role-user", name: "User" },
		additional_roles: [],
		workflows_using: 4,
		...overrides,
	} as Identity;
}

const DEFAULT = identity({});
const SYNC = identity({
	id: "identity-sync",
	name: "Contoso Sync",
	identity_kind: "custom",
	workflows_using: 1,
});

const state = vi.hoisted(() => ({
	identities: [] as Identity[],
	recommendations: undefined as RecommendedAccess | undefined,
	assignableRoles: [] as AssignableRole[],
	reach: [] as UserAccessMap["reach"],
}));
const setRunIdentity = vi.hoisted(() => ({ mutateAsync: vi.fn() }));
const createIdentity = vi.hoisted(() => ({ mutateAsync: vi.fn() }));
const grantToIdentity = vi.hoisted(() => ({
	grant: vi.fn(),
	grantedTo: vi.fn(),
}));
const accessMapFor = vi.hoisted(() => vi.fn());

vi.mock("@/services/workflowAccess", async (importOriginal) => ({
	...(await importOriginal<typeof import("@/services/workflowAccess")>()),
	useWorkflowRunIdentities: () => ({ data: state.identities }),
	useWorkflowRecommendedAccess: () => ({ data: state.recommendations }),
	useSetWorkflowRunIdentity: () => ({ ...setRunIdentity, isPending: false }),
	useGrantToIdentity: (identityId: string | undefined) => {
		grantToIdentity.grantedTo(identityId);
		return {
			assignableRoles: state.assignableRoles,
			isPending: false,
			grant: grantToIdentity.grant,
		};
	},
}));

vi.mock("@/services/identities", async (importOriginal) => ({
	...(await importOriginal<typeof import("@/services/identities")>()),
	useCreateIdentity: () => ({ ...createIdentity, isPending: false }),
}));

vi.mock("@/services/access", () => ({
	useUserAccessMap: (id: string) => {
		accessMapFor(id);
		return { data: { reach: state.reach } };
	},
}));

vi.mock("@/services/authorization", () => ({
	useAuthorization: () => ({
		authorization: { provider_organization_id: PROVIDER },
	}),
}));

vi.mock("@/components/access/TestAccessPanel", () => ({
	TestAccessPanel: (props: {
		subjectId: string;
		defaultWorkflowId?: string;
		defaultOrganizationId?: string;
	}) => (
		<div data-testid="test-access">
			{props.subjectId} {props.defaultWorkflowId}{" "}
			{props.defaultOrganizationId}
		</div>
	),
}));

const toastSuccess = vi.hoisted(() => vi.fn());
vi.mock("sonner", () => ({ toast: { success: toastSuccess } }));

import { WorkflowAccessPanel } from "./WorkflowAccessPanel";

function makeWorkflow(overrides: Partial<Workflow> = {}): Workflow {
	return {
		id: "wf-1",
		name: "nightly_sync",
		display_name: "Nightly Sync",
		organization_id: CONTOSO,
		run_identity_id: null,
		permission_mode: "full",
		...overrides,
	} as Workflow;
}

/** What the server computed, for the identity the workflow runs as now. */
function recommendations(
	items: RecommendedAccessItem[],
	observedRuns = items.length ? 3 : 0,
	identityId = DEFAULT.id,
): RecommendedAccess {
	return {
		identity_id: identityId,
		observed_runs: observedRuns,
		window_days: 30,
		items,
	};
}

const POLICY_ROLE: RecommendedAccessItem = {
	kind: "policy_role",
	label: "Ticket Readers",
	detail: "A table or file policy the workflow uses checks for this role",
	organization_id: CONTOSO,
	grant: {
		role_id: "role-tickets",
		boundaries: [{ kind: "organization", organization_id: CONTOSO }],
	},
};

const REACH: RecommendedAccessItem = {
	kind: "reach",
	label: "Fabrikam",
	detail: "The workflow reads this organization, outside the identity's reach",
	organization_id: FABRIKAM,
	grant: null,
};

function assignableRole(overrides: Partial<AssignableRole>): AssignableRole {
	return {
		id: "role-tickets",
		name: "Ticket Readers",
		is_builtin: false,
		permissions: [],
		can_be_base: false,
		can_be_additional: true,
		boundary_kinds: ["organization"],
		provider_organization_allowed: false,
		fixed_boundaries: [],
		...overrides,
	};
}

beforeEach(() => {
	state.identities = [DEFAULT, SYNC];
	state.recommendations = recommendations([]);
	state.assignableRoles = [];
	state.reach = [
		{
			kind: "home",
			organization_id: CONTOSO,
			organization_name: "Contoso",
			label: "Contoso (Home)",
		},
	];
	setRunIdentity.mutateAsync.mockReset();
	setRunIdentity.mutateAsync.mockResolvedValue({});
	createIdentity.mutateAsync.mockReset();
	grantToIdentity.grant.mockReset();
	grantToIdentity.grant.mockResolvedValue({});
	grantToIdentity.grantedTo.mockReset();
	accessMapFor.mockReset();
	toastSuccess.mockReset();
});

async function chooseIdentity(
	user: ReturnType<typeof userEvent.setup>,
	name: RegExp,
) {
	await user.click(
		screen.getByRole("combobox", { name: "Runs Unattended As" }),
	);
	await user.click(await screen.findByRole("option", { name }));
}

describe("WorkflowAccessPanel", () => {
	it("runs as its organization's default identity when it names none, saying where it belongs", () => {
		render(<WorkflowAccessPanel workflow={makeWorkflow()} />);

		expect(
			screen.getByRole("combobox", { name: "Runs Unattended As" }),
		).toHaveTextContent("Default IdentityContoso · Default");
	});

	it("lists each identity with its organization and kind", async () => {
		state.identities = [
			identity({
				id: "identity-global",
				identity_kind: "global_default",
				organization_id: null,
				organization_name: null,
			}),
			DEFAULT,
			SYNC,
		];
		const user = userEvent.setup();
		render(<WorkflowAccessPanel workflow={makeWorkflow()} />);

		await user.click(
			screen.getByRole("combobox", { name: "Runs Unattended As" }),
		);

		expect(
			screen.getAllByRole("option").map((option) => option.textContent),
		).toEqual([
			"Default IdentityGlobal · Default",
			"Default IdentityContoso · Default",
			"Contoso SyncContoso · Custom",
		]);
	});

	it("saves another identity, and the default as none", async () => {
		// The server's answer moves to the identity just saved.
		setRunIdentity.mutateAsync.mockImplementation(
			async ({ body }: { body: { run_identity_id: string | null } }) => {
				state.recommendations = recommendations(
					[],
					0,
					body.run_identity_id ?? DEFAULT.id,
				);
				return {};
			},
		);
		const user = userEvent.setup();
		const { rerender } = render(
			<WorkflowAccessPanel workflow={makeWorkflow()} />,
		);

		await chooseIdentity(user, /Contoso Sync/);
		expect(setRunIdentity.mutateAsync).toHaveBeenLastCalledWith({
			params: { path: { workflow_id: "wf-1" } },
			body: { run_identity_id: "identity-sync", clear_roles: false },
		});
		expect(toastSuccess).toHaveBeenLastCalledWith("Identity changed", {
			description:
				"Nightly Sync runs as Contoso Sync · Contoso when no person starts it",
		});
		rerender(<WorkflowAccessPanel workflow={makeWorkflow()} />);
		expect(
			screen.getByRole("combobox", { name: "Runs Unattended As" }),
		).toHaveTextContent("Contoso Sync");

		await chooseIdentity(user, /Default Identity/);
		expect(setRunIdentity.mutateAsync).toHaveBeenLastCalledWith({
			params: { path: { workflow_id: "wf-1" } },
			body: { run_identity_id: null, clear_roles: false },
		});
		expect(toastSuccess).toHaveBeenLastCalledWith("Identity changed", {
			description:
				"Nightly Sync runs as Default Identity · Contoso when no person starts it",
		});
	});

	it("shows why the identity can't be handed out and keeps the saved one", async () => {
		setRunIdentity.mutateAsync.mockRejectedValue({
			detail: "Contoso Sync holds powers you don't, so you can't make a workflow run as it",
		});
		const user = userEvent.setup();
		render(<WorkflowAccessPanel workflow={makeWorkflow()} />);

		await chooseIdentity(user, /Contoso Sync/);

		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Contoso Sync holds powers you don't, so you can't make a workflow run as it",
		);
		expect(
			screen.getByRole("combobox", { name: "Runs Unattended As" }),
		).toHaveTextContent("Default Identity");
	});

	it("follows the identity the server says the workflow runs as, not the dialog's older copy", async () => {
		// The dialog still holds the workflow as it was opened (no identity),
		// but it has since been set to Contoso Sync: reopening the Access tab
		// must act on Contoso Sync.
		state.recommendations = recommendations([POLICY_ROLE], 3, SYNC.id);
		const user = userEvent.setup();
		render(<WorkflowAccessPanel workflow={makeWorkflow()} />);

		expect(
			screen.getByRole("combobox", { name: "Runs Unattended As" }),
		).toHaveTextContent("Contoso Sync");
		expect(screen.getByTestId("test-access")).toHaveTextContent(
			"identity-sync",
		);
		await user.click(
			screen.getByRole("button", { name: "Grant to Identity" }),
		);
		expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
		expect(grantToIdentity.grantedTo).toHaveBeenLastCalledWith(
			"identity-sync",
		);
		expect(grantToIdentity.grant).toHaveBeenCalledWith(POLICY_ROLE.grant);
	});

	it("waits to act on an identity until the server says which it is", () => {
		state.recommendations = undefined;
		render(
			<WorkflowAccessPanel
				workflow={makeWorkflow({ run_identity_id: "identity-sync" })}
			/>,
		);

		expect(
			screen.getByRole("combobox", { name: "Runs Unattended As" }),
		).toBeDisabled();
		expect(screen.queryByTestId("test-access")).not.toBeInTheDocument();
		expect(accessMapFor).toHaveBeenLastCalledWith(undefined);
	});

	it("keeps a Solution's workflow on its identity, while still granting it access", async () => {
		state.recommendations = recommendations([POLICY_ROLE]);
		const user = userEvent.setup();
		render(
			<WorkflowAccessPanel
				workflow={makeWorkflow({ is_solution_managed: true })}
			/>,
		);

		const section = screen.getByRole("region", {
			name: "Runs Unattended As",
		});
		expect(
			within(section).getByRole("combobox", {
				name: "Runs Unattended As",
			}),
		).toBeDisabled();
		expect(
			within(section).getByRole("button", {
				name: "Create a Dedicated Identity",
			}),
		).toBeDisabled();
		expect(section).toHaveTextContent(
			"This workflow is managed by a Solution. Re-deploy the Solution to change who it runs as.",
		);

		await user.click(
			screen.getByRole("button", { name: "Grant to Identity" }),
		);
		const dialog = await screen.findByRole("alertdialog");
		expect(
			within(dialog).queryByRole("button", {
				name: "Create a Dedicated Identity Instead",
			}),
		).not.toBeInTheDocument();
		await user.click(within(dialog).getByRole("button", { name: "Grant" }));
		expect(grantToIdentity.grant).toHaveBeenCalledWith(POLICY_ROLE.grant);
		expect(screen.getByTestId("test-access")).toBeInTheDocument();
	});

	it("creates a dedicated identity named for the workflow and runs as it", async () => {
		createIdentity.mutateAsync.mockResolvedValue(
			identity({
				id: "identity-new",
				name: "Nightly Sync Identity",
				identity_kind: "custom",
				workflows_using: 0,
			}),
		);
		const user = userEvent.setup();
		render(<WorkflowAccessPanel workflow={makeWorkflow()} />);

		await user.click(
			screen.getByRole("button", { name: "Create a Dedicated Identity" }),
		);

		expect(createIdentity.mutateAsync).toHaveBeenCalledWith({
			body: { name: "Nightly Sync Identity", organization_id: CONTOSO },
		});
		expect(setRunIdentity.mutateAsync).toHaveBeenCalledWith({
			params: { path: { workflow_id: "wf-1" } },
			body: { run_identity_id: "identity-new", clear_roles: false },
		});
	});

	it("shows why a dedicated identity's name is taken, without trying another", async () => {
		createIdentity.mutateAsync.mockRejectedValue({
			detail: 'An identity named "Nightly Sync Identity" already exists in Contoso',
		});
		const user = userEvent.setup();
		render(<WorkflowAccessPanel workflow={makeWorkflow()} />);

		await user.click(
			screen.getByRole("button", { name: "Create a Dedicated Identity" }),
		);

		expect(await screen.findByRole("alert")).toHaveTextContent(
			'An identity named "Nightly Sync Identity" already exists in Contoso',
		);
		expect(createIdentity.mutateAsync).toHaveBeenCalledTimes(1);
		expect(setRunIdentity.mutateAsync).not.toHaveBeenCalled();
	});

	it("says no runs were observed yet, and the window it looked at", () => {
		render(<WorkflowAccessPanel workflow={makeWorkflow()} />);

		const section = screen.getByRole("region", {
			name: "Recommended Access",
		});
		expect(section).toHaveTextContent(
			"Based on this workflow's runs in the last 30 days, Default Identity · Contoso would also need:",
		);
		expect(section).toHaveTextContent("No Runs Observed Yet");
		expect(section).toHaveTextContent(
			"Recommendations appear after the workflow runs.",
		);
	});

	it("says nothing is missing when runs were observed", () => {
		state.recommendations = recommendations([], 5);
		render(<WorkflowAccessPanel workflow={makeWorkflow()} />);

		const section = screen.getByRole("region", {
			name: "Recommended Access",
		});
		expect(section).toHaveTextContent("Nothing Missing");
		expect(section).toHaveTextContent(
			"Default Identity · Contoso has everything these runs used.",
		);
		expect(section).not.toHaveTextContent("No Runs Observed Yet");
	});

	it("grants a custom identity a missing role right away", async () => {
		state.recommendations = recommendations([POLICY_ROLE], 3, SYNC.id);
		const user = userEvent.setup();
		render(
			<WorkflowAccessPanel
				workflow={makeWorkflow({ run_identity_id: "identity-sync" })}
			/>,
		);

		await user.click(
			screen.getByRole("button", { name: "Grant to Identity" }),
		);

		expect(grantToIdentity.grantedTo).toHaveBeenLastCalledWith(
			"identity-sync",
		);
		expect(grantToIdentity.grant).toHaveBeenCalledWith(POLICY_ROLE.grant);
		expect(toastSuccess).toHaveBeenLastCalledWith("Role granted", {
			description: "Contoso Sync · Contoso now holds Ticket Readers",
		});
	});

	it("confirms a grant to the default identity, naming every workflow it reaches", async () => {
		state.recommendations = recommendations([POLICY_ROLE]);
		const user = userEvent.setup();
		render(<WorkflowAccessPanel workflow={makeWorkflow()} />);

		await user.click(
			screen.getByRole("button", { name: "Grant to Identity" }),
		);

		const dialog = await screen.findByRole("alertdialog");
		expect(
			within(dialog).getByRole("heading", {
				name: "Grant to the Default Identity?",
			}),
		).toBeInTheDocument();
		expect(dialog).toHaveTextContent(
			"Applies to all 4 workflows in Contoso that run as the default identity. To give Ticket Readers to this workflow alone, create a dedicated identity and grant it there.",
		);
		expect(
			within(dialog)
				.getAllByRole("button")
				.map((button) => button.textContent),
		).toEqual(["Cancel", "Create a Dedicated Identity Instead", "Grant"]);
		expect(grantToIdentity.grant).not.toHaveBeenCalled();

		await user.click(within(dialog).getByRole("button", { name: "Grant" }));
		expect(grantToIdentity.grantedTo).toHaveBeenLastCalledWith(
			"identity-default",
		);
		expect(grantToIdentity.grant).toHaveBeenCalledWith(POLICY_ROLE.grant);
	});

	it("offers a dedicated identity instead of granting the default one", async () => {
		state.recommendations = recommendations([POLICY_ROLE]);
		createIdentity.mutateAsync.mockResolvedValue(
			identity({
				id: "identity-new",
				name: "Nightly Sync Identity",
				identity_kind: "custom",
			}),
		);
		const user = userEvent.setup();
		render(<WorkflowAccessPanel workflow={makeWorkflow()} />);

		await user.click(
			screen.getByRole("button", { name: "Grant to Identity" }),
		);
		await user.click(
			within(await screen.findByRole("alertdialog")).getByRole("button", {
				name: "Create a Dedicated Identity Instead",
			}),
		);

		expect(createIdentity.mutateAsync).toHaveBeenCalled();
		expect(setRunIdentity.mutateAsync).toHaveBeenCalledWith({
			params: { path: { workflow_id: "wf-1" } },
			body: { run_identity_id: "identity-new", clear_roles: false },
		});
		expect(grantToIdentity.grant).not.toHaveBeenCalled();
	});

	it("grants access to an organization outside the identity's reach with a role chosen for it", async () => {
		state.recommendations = recommendations([REACH], 3, SYNC.id);
		state.assignableRoles = [
			assignableRole({}),
			assignableRole({
				id: "role-base-only",
				name: "Base Only",
				can_be_additional: false,
			}),
		];
		const user = userEvent.setup();
		render(
			<WorkflowAccessPanel
				workflow={makeWorkflow({ run_identity_id: "identity-sync" })}
			/>,
		);

		await user.click(screen.getByRole("button", { name: "Grant Access" }));
		const dialog = await screen.findByRole("dialog", {
			name: "Grant Access to Fabrikam",
		});
		await user.click(
			within(dialog).getByRole("combobox", { name: "Role" }),
		);
		expect(
			screen.getAllByRole("option").map((option) => option.textContent),
		).toEqual(["Ticket Readers"]);
		await user.click(
			screen.getByRole("option", { name: "Ticket Readers" }),
		);
		await user.click(within(dialog).getByRole("button", { name: "Grant" }));

		expect(grantToIdentity.grantedTo).toHaveBeenLastCalledWith(
			"identity-sync",
		);
		expect(grantToIdentity.grant).toHaveBeenCalledWith({
			role_id: "role-tickets",
			boundaries: [{ kind: "organization", organization_id: FABRIKAM }],
		});
	});

	it("confirms granting access to the default identity", async () => {
		state.recommendations = recommendations([REACH]);
		state.assignableRoles = [assignableRole({})];
		const user = userEvent.setup();
		render(<WorkflowAccessPanel workflow={makeWorkflow()} />);

		await user.click(screen.getByRole("button", { name: "Grant Access" }));
		const dialog = await screen.findByRole("dialog", {
			name: "Grant Access to Fabrikam",
		});
		await user.click(
			within(dialog).getByRole("combobox", { name: "Role" }),
		);
		await user.click(
			screen.getByRole("option", { name: "Ticket Readers" }),
		);
		await user.click(within(dialog).getByRole("button", { name: "Grant" }));

		const confirm = await screen.findByRole("alertdialog");
		expect(confirm).toHaveTextContent(
			"Applies to all 4 workflows in Contoso that run as the default identity",
		);
		expect(grantToIdentity.grant).not.toHaveBeenCalled();
		await user.click(
			within(confirm).getByRole("button", { name: "Grant" }),
		);
		expect(grantToIdentity.grant).toHaveBeenCalledWith({
			role_id: "role-tickets",
			boundaries: [{ kind: "organization", organization_id: FABRIKAM }],
		});
	});

	it("shows a workflow role, and a role no grant meets, as information only", () => {
		state.recommendations = recommendations([
			{
				kind: "workflow_role",
				label: "Ticket Admins",
				detail: "Needed when the identity starts this workflow through the API: it must hold one of these roles",
				organization_id: null,
				grant: null,
			},
			{ ...POLICY_ROLE, label: "Org Admin", grant: null },
		]);
		render(<WorkflowAccessPanel workflow={makeWorkflow()} />);

		const section = screen.getByRole("region", {
			name: "Recommended Access",
		});
		expect(section).toHaveTextContent("Ticket Admins");
		expect(section).toHaveTextContent("Org Admin");
		expect(within(section).queryByRole("button")).toBeNull();
	});

	it("summarizes reach for person-started and unattended runs", () => {
		render(<WorkflowAccessPanel workflow={makeWorkflow()} />);

		const section = screen.getByRole("region", { name: "Reach Summary" });
		expect(section).toHaveTextContent("Started by a Person");
		expect(section).toHaveTextContent("That Person's Reach");
		expect(section).toHaveTextContent("Unattended");
		expect(section).toHaveTextContent("Default Identity");
		expect(
			within(section).getByLabelText("Organization"),
		).toHaveTextContent("Contoso");
		expect(section).toHaveTextContent("Contoso (Home)");
		expect(accessMapFor).toHaveBeenCalledWith("identity-default");
	});

	it("shows the access mode read-only", () => {
		render(<WorkflowAccessPanel workflow={makeWorkflow()} />);

		expect(screen.getByText("Access Mode")).toBeInTheDocument();
		expect(screen.getByText("Full")).toBeInTheDocument();
	});

	it("tests access as the identity, starting from this workflow and its organization", () => {
		const { rerender } = render(
			<WorkflowAccessPanel workflow={makeWorkflow()} />,
		);
		expect(screen.getByTestId("test-access")).toHaveTextContent(
			`identity-default wf-1 ${CONTOSO}`,
		);

		state.identities = [
			identity({
				id: "identity-global",
				identity_kind: "global_default",
				organization_id: null,
				organization_name: null,
			}),
		];
		state.recommendations = recommendations([], 0, "identity-global");
		rerender(
			<WorkflowAccessPanel
				workflow={makeWorkflow({ id: "wf-2", organization_id: null })}
			/>,
		);
		expect(screen.getByTestId("test-access")).toHaveTextContent(
			"identity-global wf-2 global",
		);
	});
});
