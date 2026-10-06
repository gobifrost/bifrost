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

const create = vi.hoisted(() => ({ mutateAsync: vi.fn() }));
vi.mock("@/services/identities", () => ({
	useCreateIdentity: () => ({ ...create, isPending: false }),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn() } }));

type SelectProps = {
	value: string | null | undefined;
	onChange: (value: string | null | undefined) => void;
	showGlobal?: boolean;
	filterOrganizations?: (organization: { id: string }) => boolean;
};
const select = vi.hoisted(() => ({
	props: undefined as SelectProps | undefined,
}));
// The picker itself has its own tests; this stands in with a native select.
vi.mock("@/components/forms/OrganizationSelect", () => ({
	OrganizationSelect: (props: SelectProps & { id?: string }) => {
		select.props = props;
		return (
			<select
				id={props.id}
				value={props.value === null ? "global" : (props.value ?? "")}
				onChange={(event) =>
					props.onChange(
						event.target.value === "global"
							? null
							: event.target.value,
					)
				}
			>
				<option value="">Select</option>
				<option value="global">Global</option>
				<option value="org-1">Contoso</option>
			</select>
		);
	},
}));

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

beforeEach(() => {
	authz.summary = adminSummary();
	select.props = undefined;
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
		await user.selectOptions(
			screen.getByRole("combobox", { name: "Organization" }),
			"global",
		);
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
		await user.selectOptions(
			screen.getByRole("combobox", { name: "Organization" }),
			"org-1",
		);
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
		await user.selectOptions(
			screen.getByRole("combobox", { name: "Organization" }),
			"org-1",
		);
		await user.click(
			screen.getByRole("button", { name: "Create Identity" }),
		);

		expect(await screen.findByRole("alert")).toHaveTextContent(
			"Organization not found",
		);
		expect(onCreated).not.toHaveBeenCalled();
	});

	it("offers only the places the caller can create identities in", () => {
		authz.summary = {
			...adminSummary(),
			is_platform_admin: false,
			grants: [
				{
					permission: "users.lifecycle.readwrite",
					boundary: {
						kind: "organization",
						organization_id: "org-1",
					},
				},
			],
		};
		renderDialog();

		expect(select.props?.showGlobal).toBe(false);
		expect(select.props?.filterOrganizations?.({ id: "org-1" })).toBe(true);
		expect(select.props?.filterOrganizations?.({ id: "org-2" })).toBe(
			false,
		);
	});
});
