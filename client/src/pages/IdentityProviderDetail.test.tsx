import { beforeEach, describe, expect, it, vi } from "vitest";
import { Route, Routes } from "react-router-dom";

import { renderWithProviders, screen } from "@/test-utils";
import {
	canAnywhere,
	canAt,
	meetsRequirement,
	type AuthorizationSummary,
	type AuthorizationTarget,
	type PermissionRequirement,
} from "@/lib/authorization";
import type {
	IdentityProvider,
	IdentityProviderGroup,
	OrganizationDomain,
} from "@/services/identity-providers";

const authz = vi.hoisted(() => ({
	summary: undefined as AuthorizationSummary | undefined,
}));
vi.mock("@/services/authorization", () => ({
	useAuthorization: () => ({
		authorization: authz.summary,
		canAt: (permission: string, target: AuthorizationTarget) =>
			canAt(authz.summary, permission, target),
		canAnywhere: (permission: string) =>
			canAnywhere(authz.summary, permission),
		meets: (requirement: PermissionRequirement) =>
			meetsRequirement(authz.summary, requirement),
	}),
}));

const data = vi.hoisted(() => ({
	provider: undefined as IdentityProvider | undefined,
	groups: [] as IdentityProviderGroup[],
	domains: [] as OrganizationDomain[],
}));
const mutation = vi.hoisted(() => () => ({
	mutateAsync: vi.fn(),
	isPending: false,
}));
vi.mock("@/services/identity-providers", async (importOriginal) => ({
	...(await importOriginal<typeof import("@/services/identity-providers")>()),
	useIdentityProvider: () => ({ data: data.provider, isLoading: false }),
	useIdentityProviderGroups: () => ({ data: data.groups, isLoading: false }),
	useOrganizationDomains: () => ({ data: data.domains, isLoading: false }),
	useDeleteIdentityProvider: mutation,
	useUpdateIdentityProvider: mutation,
	useRotateIdentityProviderToken: mutation,
	useMapIdentityProviderGroup: mutation,
	useUnmapIdentityProviderGroup: mutation,
	useAddOrganizationDomain: mutation,
	useVerifyOrganizationDomain: mutation,
	useDeleteOrganizationDomain: mutation,
}));

import { IdentityProviderDetail } from "./IdentityProviderDetail";

const DAY = 24 * 60 * 60 * 1000;

function summary(admin: boolean): AuthorizationSummary {
	return {
		is_platform_admin: admin,
		home_organization_id: "org-provider",
		provider_organization_id: "org-provider",
		base_role: { id: "role", name: admin ? "Platform Admin" : "User" },
		grants: admin
			? []
			: [
					{
						permission: "identityproviders.read",
						boundary: {
							kind: "organization",
							organization_id: "org-1",
						},
					},
				],
	};
}

function provider(overrides: Partial<IdentityProvider> = {}): IdentityProvider {
	return {
		id: "idp-1",
		organization_id: "org-1",
		organization_name: "Contoso",
		name: "MTG-NinjaSSO",
		source_type: "scim",
		vendor: "entra",
		is_enabled: true,
		provision_as_external: false,
		scim_base_url: "https://bifrost.example/api/scim/v2/idp-1",
		token: {
			hint: "abcd",
			expires_at: new Date(Date.now() + 200 * DAY).toISOString(),
			created_at: new Date().toISOString(),
			last_used_at: null,
		},
		verified_domains: ["contoso.com"],
		user_count: 30,
		group_count: 2,
		mapped_group_count: 2,
		pending_access_count: 0,
		last_request_at: null,
		last_sync_at: null,
		created_at: new Date().toISOString(),
		updated_at: new Date().toISOString(),
		...overrides,
	};
}

function renderPage(state?: unknown) {
	return renderWithProviders(
		<Routes>
			<Route
				path="/users/identity-providers/:identityProviderId"
				element={<IdentityProviderDetail />}
			/>
		</Routes>,
		{
			initialEntries: [
				{ pathname: "/users/identity-providers/idp-1", state },
			] as never,
		},
	);
}

describe("IdentityProviderDetail", () => {
	beforeEach(() => {
		authz.summary = summary(true);
		data.provider = provider();
		data.groups = [];
		data.domains = [];
	});

	it("shows the Tenant URL to paste into the identity provider", () => {
		renderPage();

		expect(
			screen.getByRole("heading", { name: "MTG-NinjaSSO" }),
		).toBeInTheDocument();
		expect(
			screen.getByDisplayValue(data.provider!.scim_base_url),
		).toBeInTheDocument();
		expect(screen.getByText("••••abcd")).toBeInTheDocument();
	});

	it("warns when the token has expired", () => {
		data.provider = provider({
			token: {
				hint: "abcd",
				expires_at: new Date(Date.now() - DAY).toISOString(),
				created_at: new Date().toISOString(),
				last_used_at: null,
			},
		});
		renderPage();

		expect(
			screen.getByText("SCIM authentication token has expired"),
		).toBeInTheDocument();
	});

	it("reveals a newly issued token once and hides it when dismissed", async () => {
		const { user } = renderPage({
			issuedToken: {
				token: "bfidp_one_time",
				info: data.provider!.token,
				previous_tokens_expire_at: null,
			},
		});

		expect(screen.getByText("bfidp_one_time")).toBeInTheDocument();
		await user.click(
			screen.getByRole("button", { name: "Dismiss secret" }),
		);
		expect(screen.queryByText("bfidp_one_time")).toBeNull();
	});

	it("lists groups with their mapping status and roles", () => {
		data.groups = [
			{
				id: "g1",
				display_name: "NinjaUsers",
				external_id: null,
				member_count: 12,
				mapped: true,
				roles: [{ id: "r1", name: "Support Tech" }],
			},
			{
				id: "g2",
				display_name: "Everyone",
				external_id: null,
				member_count: 30,
				mapped: true,
				roles: [],
			},
			{
				id: "g3",
				display_name: "Contractors",
				external_id: null,
				member_count: 3,
				mapped: false,
				roles: [],
			},
		];
		renderPage();

		expect(screen.getByText("Support Tech")).toBeInTheDocument();
		expect(screen.getByText("Access Only")).toBeInTheDocument();
		expect(screen.getByText("Not Mapped")).toBeInTheDocument();
	});

	it("gives the TXT record to publish for a pending domain", () => {
		data.domains = [
			{
				id: "d1",
				organization_id: "org-1",
				domain: "contoso.com",
				verified_at: null,
				last_checked_at: null,
				txt_record_name: "contoso.com",
				txt_record_value: "bifrost-verification=abc123",
				created_at: new Date().toISOString(),
			},
		];
		renderPage();

		expect(screen.getByText("Pending")).toBeInTheDocument();
		expect(
			screen.getByDisplayValue("bifrost-verification=abc123"),
		).toBeInTheDocument();
	});

	it("hides changes from a caller who can only read connections", () => {
		authz.summary = summary(false);
		renderPage();

		expect(screen.queryByRole("button", { name: /Edit/ })).toBeNull();
		expect(screen.queryByRole("button", { name: /Delete/ })).toBeNull();
		expect(
			screen.queryByRole("button", { name: /Regenerate Token/ }),
		).toBeNull();
	});
});
