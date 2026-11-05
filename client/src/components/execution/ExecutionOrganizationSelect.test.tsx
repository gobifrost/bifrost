import { beforeEach, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";
import {
	canAt,
	canAnywhere,
	type AuthorizationSummary,
} from "@/lib/authorization";
import { ExecutionOrganizationSelect } from "./ExecutionOrganizationSelect";

const state = vi.hoisted(() => ({
	authorization: undefined as AuthorizationSummary | undefined,
}));
vi.mock("@/services/authorization", () => ({
	useAuthorization: () => ({
		authorization: state.authorization,
		canAt: (permission: string, target: Parameters<typeof canAt>[2]) =>
			canAt(state.authorization, permission, target),
		canAnywhere: (permission: string) =>
			canAnywhere(state.authorization, permission),
	}),
}));
vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => ({
		data: [
			{ id: "home", name: "Covi" },
			{ id: "allowed", name: "Contoso" },
			{ id: "denied", name: "Fabrikam" },
		],
		isLoading: false,
	}),
}));

beforeEach(() => {
	state.authorization = {
		is_platform_admin: false,
		home_organization_id: "home",
		home_organization_name: "Covi",
		provider_organization_id: "home",
		base_role: { id: "base-role", name: "Organization User" },
		grants: [
			{
				permission: "organizations.read",
				boundary: {
					kind: "managed_organizations",
					organization_id: null,
				},
			},
			{
				permission: "workflows.execute",
				boundary: { kind: "home", organization_id: null },
			},
			{
				permission: "workflows.execute",
				boundary: { kind: "organization", organization_id: "allowed" },
			},
		],
	};
});

it("offers only organizations where the caller can run workflows and can restore the default", async () => {
	const onChange = vi.fn();
	const { user } = renderWithProviders(
		<ExecutionOrganizationSelect value={undefined} onChange={onChange} />,
	);
	expect(
		screen.getByRole("combobox", { name: "Run in Organization" }),
	).toHaveTextContent("Covi");
	await user.click(screen.getByRole("combobox"));
	expect(
		screen.queryByRole("option", { name: "Fabrikam" }),
	).not.toBeInTheDocument();
	expect(
		screen.queryByRole("option", { name: /^Global/ }),
	).not.toBeInTheDocument();
	await user.click(screen.getByRole("option", { name: "Contoso" }));
	expect(onChange).toHaveBeenLastCalledWith("allowed");
	await user.click(screen.getByRole("combobox"));
	await user.click(screen.getByRole("option", { name: "Covi" }));
	expect(onChange).toHaveBeenLastCalledWith(undefined);
});

it("keeps an organization-owned resource anchored to its organization", () => {
	renderWithProviders(
		<ExecutionOrganizationSelect
			value={undefined}
			onChange={vi.fn()}
			organizationId="allowed"
		/>,
	);
	expect(screen.getByRole("combobox")).toBeDisabled();
	expect(screen.getByRole("combobox")).toHaveTextContent("Contoso");
});

it("shows the home organization without requiring organization discovery", () => {
	state.authorization!.grants = state.authorization!.grants.filter(
		(grant) => grant.permission !== "organizations.read",
	);
	renderWithProviders(
		<ExecutionOrganizationSelect value={undefined} onChange={vi.fn()} />,
	);
	expect(screen.getByRole("combobox")).toBeDisabled();
	expect(screen.getByRole("combobox")).toHaveTextContent("Covi");
});

it("filters form destinations by form access as well as workflow execution", async () => {
	state.authorization!.grants.push({
		permission: "forms.readbasic",
		boundary: { kind: "home", organization_id: null },
	});
	const { user } = renderWithProviders(
		<ExecutionOrganizationSelect
			value={undefined}
			onChange={vi.fn()}
			resourceType="form"
		/>,
	);
	await user.click(screen.getByRole("combobox"));
	expect(screen.getByRole("option", { name: "Covi" })).toBeVisible();
	expect(
		screen.queryByRole("option", { name: "Contoso" }),
	).not.toBeInTheDocument();
});

it("can restore an omitted default even when the caller has no home organization", async () => {
	state.authorization!.home_organization_id = null;
	const onChange = vi.fn();
	const { user } = renderWithProviders(
		<ExecutionOrganizationSelect value="allowed" onChange={onChange} />,
	);
	await user.click(screen.getByRole("button", { name: "Use Default" }));
	expect(onChange).toHaveBeenCalledWith(undefined);
});

it("locks organization changes while a run or upload is in progress", () => {
	renderWithProviders(
		<ExecutionOrganizationSelect
			value="allowed"
			onChange={vi.fn()}
			disabled
		/>,
	);
	expect(screen.getByRole("combobox")).toBeDisabled();
	expect(screen.getByRole("button", { name: "Use Default" })).toBeDisabled();
});
