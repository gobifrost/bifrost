import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders, screen } from "@/test-utils";
import {
	canAnywhere,
	canAt,
	meetsRequirement,
	type AuthorizationSummary,
	type AuthorizationTarget,
	type PermissionRequirement,
} from "@/lib/authorization";
import type { Identity } from "@/services/identities";

const authz = vi.hoisted(() => ({
	summary: undefined as AuthorizationSummary | undefined,
}));
vi.mock("@/services/authorization", () => ({
	useAuthorization: () => ({
		authorization: authz.summary,
		isPlatformAdmin: authz.summary?.is_platform_admin ?? false,
		canAt: (permission: string, target: AuthorizationTarget) =>
			canAt(authz.summary, permission, target),
		canAnywhere: (permission: string) =>
			canAnywhere(authz.summary, permission),
		meets: (requirement: PermissionRequirement) =>
			meetsRequirement(authz.summary, requirement),
	}),
}));

function defaultIdentity(
	organizationId: string | null,
	organizationName: string | null,
): Identity {
	return {
		id: `identity-${organizationId ?? "global"}`,
		name: "Default Identity",
		identity_kind: organizationId ? "org_default" : "global_default",
		organization_id: organizationId,
		organization_name: organizationName,
		base_role: { id: "role-user", name: "User" },
		additional_roles: [],
		workflows_using: 0,
	};
}

// Every organization the caller can read users in has a default identity.
const identities: Identity[] = [
	defaultIdentity(null, null),
	defaultIdentity("org-1", "Contoso"),
	{
		...defaultIdentity("org-1", "Contoso"),
		id: "identity-custom",
		name: "Backup Runner",
		identity_kind: "custom",
	},
	defaultIdentity("org-2", "Fabrikam"),
];

const visible = vi.hoisted(() => ({
	identities: [] as Identity[],
	organizations: [] as { id: string; name: string }[],
}));
const mockUseOrganizations = vi.fn();
vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: (options: { enabled?: boolean }) => {
		mockUseOrganizations(options);
		return { data: options.enabled ? visible.organizations : undefined };
	},
}));

const create = vi.hoisted(() => ({ mutateAsync: vi.fn() }));
vi.mock("@/services/identities", async (importOriginal) => ({
	...(await importOriginal<typeof import("@/services/identities")>()),
	useIdentities: () => ({ data: visible.identities }),
	useCreateIdentity: () => ({ ...create, isPending: false }),
}));

const toastSuccess = vi.hoisted(() => vi.fn());
vi.mock("sonner", () => ({ toast: { success: toastSuccess } }));

import { NewIdentityDialog } from "./NewIdentityDialog";

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

function renderDialog() {
	const onCreated = vi.fn();
	const onOpenChange = vi.fn();
	const rendered = renderWithProviders(
		<NewIdentityDialog
			open
			onOpenChange={onOpenChange}
			onCreated={onCreated}
		/>,
	);
	return { ...rendered, onCreated, onOpenChange };
}

async function choose(
	user: ReturnType<typeof renderDialog>["user"],
	place: string,
) {
	await user.click(screen.getByRole("combobox", { name: "Organization" }));
	await user.click(screen.getByRole("option", { name: place }));
}

beforeEach(() => {
	authz.summary = adminSummary();
	visible.identities = identities;
	visible.organizations = [
		{ id: "org-1", name: "Contoso" },
		{ id: "org-2", name: "Fabrikam" },
	];
	mockUseOrganizations.mockReset();
	create.mutateAsync.mockReset();
});

describe("NewIdentityDialog", () => {
	it("creates a Global identity from a name", async () => {
		const created = {
			id: "identity-new",
			name: "Nightly Sync",
			organization_name: null,
		};
		create.mutateAsync.mockResolvedValue(created);
		const { user, onCreated, onOpenChange } = renderDialog();

		expect(
			screen.getByRole("heading", { name: "New Identity" }),
		).toBeInTheDocument();
		await user.type(
			screen.getByRole("textbox", { name: "Name" }),
			"Nightly Sync",
		);
		await choose(user, "Global");
		await user.click(
			screen.getByRole("button", { name: "Create Identity" }),
		);

		expect(create.mutateAsync).toHaveBeenCalledWith({
			body: { name: "Nightly Sync", organization_id: null },
		});
		expect(onCreated).toHaveBeenCalledWith(created);
		expect(onOpenChange).toHaveBeenCalledWith(false);
		expect(toastSuccess).toHaveBeenCalledWith("Identity created", {
			description:
				"Nightly Sync · Global runs with the User base role until you give it more",
		});
	});

	it("waits for a name and an organization", async () => {
		const { user } = renderDialog();
		const submit = screen.getByRole("button", { name: "Create Identity" });

		expect(submit).toBeDisabled();
		await user.type(
			screen.getByRole("textbox", { name: "Name" }),
			"Nightly Sync",
		);
		expect(submit).toBeDisabled();
		await choose(user, "Contoso");
		expect(submit).toBeEnabled();
	});

	it("shows why the server refused", async () => {
		create.mutateAsync.mockRejectedValue({
			detail: "Organization not found",
		});
		const { user, onCreated } = renderDialog();

		await user.type(
			screen.getByRole("textbox", { name: "Name" }),
			"Nightly Sync",
		);
		await choose(user, "Contoso");
		await user.click(
			screen.getByRole("button", { name: "Create Identity" }),
		);

		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Organization not found",
		);
		expect(onCreated).not.toHaveBeenCalled();
	});

	it("shows the server's message when the organization already has the name", async () => {
		create.mutateAsync.mockRejectedValue({
			detail: 'An identity named "Backup Runner" already exists in Contoso',
		});
		const { user, onCreated } = renderDialog();

		await user.type(
			screen.getByRole("textbox", { name: "Name" }),
			"Backup Runner",
		);
		await choose(user, "Contoso");
		await user.click(
			screen.getByRole("button", { name: "Create Identity" }),
		);

		expect(await screen.findByRole("alert")).toHaveTextContent(
			'An identity named "Backup Runner" already exists in Contoso',
		);
		expect(onCreated).not.toHaveBeenCalled();
	});

	it("offers each organization once, from the identities the caller can see", async () => {
		const { user } = renderDialog();

		await user.click(
			screen.getByRole("combobox", { name: "Organization" }),
		);

		expect(
			screen.getAllByRole("option").map((option) => option.textContent),
		).toEqual(["Global", "Contoso", "Fabrikam"]);
	});

	it("offers only the places the caller can create identities in, without reading organizations", async () => {
		authz.summary = {
			...adminSummary(),
			is_platform_admin: false,
			grants: [
				{
					permission: "userlifecycle.readwrite",
					boundary: {
						kind: "organization",
						organization_id: "org-2",
					},
				},
			],
		};
		create.mutateAsync.mockResolvedValue({ id: "identity-new" });
		const { user } = renderDialog();

		await user.type(
			screen.getByRole("textbox", { name: "Name" }),
			"Nightly Sync",
		);
		await user.click(
			screen.getByRole("combobox", { name: "Organization" }),
		);
		expect(
			screen.getAllByRole("option").map((option) => option.textContent),
		).toEqual(["Fabrikam"]);
		await user.click(screen.getByRole("option", { name: "Fabrikam" }));
		await user.click(
			screen.getByRole("button", { name: "Create Identity" }),
		);

		expect(create.mutateAsync).toHaveBeenCalledWith({
			body: { name: "Nightly Sync", organization_id: "org-2" },
		});
	});

	it("adds the organizations a caller can create in but not read users in", async () => {
		// Users can be read in Contoso, so its identities are listed; identities
		// can be created only in Fabrikam, which the organizations list names.
		authz.summary = {
			...adminSummary(),
			is_platform_admin: false,
			grants: [
				{
					permission: "users.read",
					boundary: {
						kind: "organization",
						organization_id: "org-1",
					},
				},
				...["userlifecycle.readwrite", "organizations.read"].map(
					(permission) => ({
						permission,
						boundary: {
							kind: "organization" as const,
							organization_id: "org-2",
						},
					}),
				),
			],
		};
		visible.identities = [defaultIdentity("org-1", "Contoso")];
		visible.organizations = [{ id: "org-2", name: "Fabrikam" }];
		const { user } = renderDialog();

		await user.click(
			screen.getByRole("combobox", { name: "Organization" }),
		);

		expect(
			screen.getAllByRole("option").map((option) => option.textContent),
		).toEqual(["Fabrikam"]);
	});

	it("reads organizations only for a caller who may", () => {
		authz.summary = {
			...adminSummary(),
			is_platform_admin: false,
			grants: [
				{
					permission: "userlifecycle.readwrite",
					boundary: {
						kind: "organization",
						organization_id: "org-2",
					},
				},
			],
		};
		renderDialog();

		expect(mockUseOrganizations).toHaveBeenCalledWith({ enabled: false });
	});
});
