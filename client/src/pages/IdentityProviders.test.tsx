import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Route, Routes, useLocation } from "react-router-dom";

import { renderWithProviders, screen, waitFor, within } from "@/test-utils";
import {
	canAnywhere,
	canAt,
	meetsRequirement,
	type AuthorizationSummary,
	type AuthorizationTarget,
	type PermissionRequirement,
} from "@/lib/authorization";
import type { IdentityProvider } from "@/services/identity-providers";

const mockUseMediaQuery = vi.fn(() => false);
vi.mock("@/hooks/useMediaQuery", () => ({
	useMediaQuery: () => mockUseMediaQuery(),
}));
afterEach(() => mockUseMediaQuery.mockReturnValue(false));

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

const mockUseIdentityProviders = vi.fn();
vi.mock("@/services/identity-providers", async (importOriginal) => ({
	...(await importOriginal<typeof import("@/services/identity-providers")>()),
	useIdentityProviders: () => mockUseIdentityProviders(),
}));

const dialog = vi.hoisted(() => ({
	onCreated: undefined as undefined | ((created: unknown) => void),
}));
vi.mock("@/components/identity-providers/NewIdentityProviderDialog", () => ({
	NewIdentityProviderDialog: (props: {
		open: boolean;
		onCreated: (created: unknown) => void;
	}) => {
		dialog.onCreated = props.onCreated;
		return props.open ? <p>New provider dialog</p> : null;
	},
}));

import { IdentityProviders } from "./IdentityProviders";

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

const DAY = 24 * 60 * 60 * 1000;

function provider(overrides: Partial<IdentityProvider> = {}): IdentityProvider {
	return {
		id: "idp-1",
		organization_id: "org-1",
		organization_name: "Contoso",
		name: "Contoso Entra ID",
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
		last_request_at: null,
		last_sync_at: null,
		created_at: new Date().toISOString(),
		updated_at: new Date().toISOString(),
		...overrides,
	};
}

function query(data: IdentityProvider[]) {
	return {
		data,
		isLoading: false,
		isError: false,
		isFetching: false,
		refetch: vi.fn(),
	};
}

function LocationProbe() {
	const location = useLocation();
	return (
		<p data-testid="location">
			{location.pathname}|{JSON.stringify(location.state)}
		</p>
	);
}

function renderPage() {
	return renderWithProviders(
		<Routes>
			<Route
				path="/users/identity-providers"
				element={<IdentityProviders />}
			/>
			<Route path="*" element={<LocationProbe />} />
		</Routes>,
		{ initialEntries: ["/users/identity-providers"] },
	);
}

describe("IdentityProviders", () => {
	beforeEach(() => {
		authz.summary = summary(true);
		mockUseIdentityProviders.mockReturnValue(query([]));
	});

	it("lists each connection with its domains, status and mapped groups", () => {
		mockUseIdentityProviders.mockReturnValue(query([provider()]));
		renderPage();

		const row = screen
			.getByRole("link", { name: "Contoso Entra ID" })
			.closest("tr");
		expect(row).not.toBeNull();
		const cells = within(row as HTMLElement);
		expect(cells.getByText("Contoso")).toBeInTheDocument();
		expect(cells.getByText("Microsoft Entra ID")).toBeInTheDocument();
		expect(cells.getByText("@contoso.com")).toBeInTheDocument();
		expect(cells.getByText("Enabled")).toBeInTheDocument();
		expect(cells.getByText("2/2")).toBeInTheDocument();
	});

	it("flags an expired token in the list", () => {
		mockUseIdentityProviders.mockReturnValue(
			query([
				provider({
					token: {
						hint: "abcd",
						expires_at: new Date(Date.now() - DAY).toISOString(),
						created_at: new Date().toISOString(),
						last_used_at: null,
					},
				}),
			]),
		);
		renderPage();

		expect(screen.getByText(/^Expired/)).toBeInTheDocument();
	});

	it("filters by domain", async () => {
		mockUseIdentityProviders.mockReturnValue(
			query([
				provider(),
				provider({
					id: "idp-2",
					name: "Fabrikam Okta",
					vendor: "okta",
					verified_domains: ["fabrikam.com"],
				}),
			]),
		);
		const { user } = renderPage();

		await user.type(screen.getByRole("textbox"), "fabrikam");

		expect(
			screen.getByRole("link", { name: "Fabrikam Okta" }),
		).toBeInTheDocument();
		await waitFor(() =>
			expect(
				screen.queryByRole("link", { name: "Contoso Entra ID" }),
			).toBeNull(),
		);
	});

	it("offers Add Provider only to callers who can manage connections", () => {
		authz.summary = summary(false);
		renderPage();

		expect(
			screen.queryByRole("button", { name: /Add Provider/ }),
		).toBeNull();
		expect(screen.getByText("No Identity Providers")).toBeInTheDocument();
	});

	it("opens the new connection with its first token to reveal once", async () => {
		const { user } = renderPage();
		await user.click(screen.getByRole("button", { name: /Add Provider/ }));
		expect(screen.getByText("New provider dialog")).toBeInTheDocument();

		dialog.onCreated?.({
			identity_provider: { id: "idp-9" },
			token: { token: "bfidp_secret" },
		});

		expect(await screen.findByTestId("location")).toHaveTextContent(
			'/users/identity-providers/idp-9|{"issuedToken":{"token":"bfidp_secret"}}',
		);
	});
});
