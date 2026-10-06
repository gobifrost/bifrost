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
		name: `${organizationName ?? "Global"} Identity`,
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

const create = vi.hoisted(() => ({ mutateAsync: vi.fn() }));
vi.mock("@/services/identities", () => ({
	useIdentities: () => ({ data: identities }),
	useCreateIdentity: () => ({ ...create, isPending: false }),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn() } }));

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
	create.mutateAsync.mockReset();
});

describe("NewIdentityDialog", () => {
	it("creates a Global identity from a name", async () => {
		const created = { id: "identity-new", name: "Nightly Sync" };
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
					permission: "users.lifecycle.readwrite",
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
});
