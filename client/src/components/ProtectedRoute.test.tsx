import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@/test-utils";

const auth = {
	isAuthenticated: true,
	isLoading: false,
	isPlatformAdmin: false,
	isOrgUser: false,
	hasRole: vi.fn((_role: string) => false),
};

vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => auth }));
vi.mock("@/components/NoAccess", () => ({ NoAccess: () => <p>No access</p> }));

const authz = {
	isLoading: false,
	isError: false,
	isFetching: false,
	refetch: vi.fn(),
	authorization: undefined as AuthorizationSummary | undefined,
};
vi.mock("@/services/authorization", () => ({
	useAuthorization: () => ({
		...authz,
		meets: (requirement: PermissionRequirement) =>
			meetsRequirement(authz.authorization, requirement),
	}),
}));

import { EMBED_TOKEN_KEY, type EmbedTokenClaims } from "@/lib/auth-token";
import {
	meetsRequirement,
	type AuthorizationSummary,
	type PermissionRequirement,
} from "@/lib/authorization";
import { ProtectedRoute } from "./ProtectedRoute";

function setEmbedTokenClaims(claims: EmbedTokenClaims) {
	sessionStorage.setItem(
		EMBED_TOKEN_KEY,
		`header.${btoa(JSON.stringify(claims)).replace(/=/g, "")}.signature`,
	);
}

const operator: AuthorizationSummary = {
	is_platform_admin: false,
	home_organization_id: "00000000-0000-0000-0000-000000000002",
	provider_organization_id: "00000000-0000-0000-0000-000000000002",
	base_role: { id: "r", name: "User" },
	grants: [
		{
			permission: "users.read",
			boundary: { kind: "managed_organizations", organization_id: null },
		},
	],
};

beforeEach(() => {
	sessionStorage.clear();
	auth.isAuthenticated = true;
	auth.isLoading = false;
	auth.isPlatformAdmin = false;
	auth.isOrgUser = false;
	auth.hasRole.mockReturnValue(false);
	authz.isLoading = false;
	authz.isError = false;
	authz.authorization = operator;
});

describe("ProtectedRoute", () => {
	it("renders children after auth loads for an unrestricted route", () => {
		renderWithProviders(
			<ProtectedRoute>
				<div>Private content</div>
			</ProtectedRoute>,
		);

		expect(screen.getByText("Private content")).toBeInTheDocument();
	});

	it("allows org users and embed users into org routes", () => {
		auth.hasRole.mockReturnValue(true);

		renderWithProviders(
			<ProtectedRoute requireOrgUser>
				<div>Private content</div>
			</ProtectedRoute>,
		);

		expect(screen.getByText("Private content")).toBeInTheDocument();
	});

	it("renders nothing while auth is loading", () => {
		auth.isLoading = true;

		renderWithProviders(
			<ProtectedRoute>
				<div>Private content</div>
			</ProtectedRoute>,
		);

		expect(
			screen.getByRole("status", { name: /loading access/i }),
		).toBeInTheDocument();
		expect(screen.queryByText("Private content")).not.toBeInTheDocument();
	});
});

it("does not show permission denial while signed out", () => {
	auth.isAuthenticated = false;
	auth.isPlatformAdmin = false;
	renderWithProviders(
		<ProtectedRoute requirePermission={{ permission: "users.read" }}>
			<p>Private content</p>
		</ProtectedRoute>,
	);
	expect(
		screen.getByRole("status", { name: "Opening sign in…" }),
	).toBeVisible();
	expect(screen.queryByText("No access")).not.toBeInTheDocument();
});

describe("ProtectedRoute permission mode", () => {
	it("admits a caller holding the permission anywhere", () => {
		renderWithProviders(
			<ProtectedRoute requirePermission={{ permission: "users.read" }}>
				<p>Users page</p>
			</ProtectedRoute>,
		);
		expect(screen.getByText("Users page")).toBeInTheDocument();
	});

	it("refuses a caller without the permission", () => {
		renderWithProviders(
			<ProtectedRoute requirePermission={{ permission: "roles.read" }}>
				<p>Roles page</p>
			</ProtectedRoute>,
		);
		expect(screen.getByText("No access")).toBeInTheDocument();
		expect(screen.queryByText("Roles page")).not.toBeInTheDocument();
	});

	it("admits an HMAC form session only on its scoped result route", () => {
		auth.hasRole.mockImplementation((role) => role === "EmbedUser");
		authz.authorization = undefined;
		setEmbedTokenClaims({
			embed: true,
			embed_kind: "form",
			grant: "hmac",
			form_id: "form-1",
		});

		renderWithProviders(
			<ProtectedRoute
				requirePermission={{ permission: "executions.readbasic" }}
				allowHmacFormExecutionResult
			>
				<p>Execution result</p>
			</ProtectedRoute>,
		);

		expect(screen.getByText("Execution result")).toBeInTheDocument();
	});

	it("keeps HMAC form sessions out of ordinary permission routes", () => {
		auth.hasRole.mockImplementation((role) => role === "EmbedUser");
		authz.authorization = undefined;
		setEmbedTokenClaims({
			embed: true,
			embed_kind: "form",
			grant: "hmac",
			form_id: "form-1",
		});

		renderWithProviders(
			<ProtectedRoute
				requirePermission={{ permission: "executions.readbasic" }}
			>
				<p>Execution result</p>
			</ProtectedRoute>,
		);

		expect(screen.getByText("No access")).toBeInTheDocument();
		expect(screen.queryByText("Execution result")).not.toBeInTheDocument();
	});

	it("does not treat a public form session as an execution-result capability", () => {
		auth.hasRole.mockImplementation((role) => role === "EmbedUser");
		authz.authorization = undefined;
		setEmbedTokenClaims({
			embed: true,
			embed_kind: "form",
			grant: "public",
			form_id: "form-1",
		});

		renderWithProviders(
			<ProtectedRoute
				requirePermission={{ permission: "executions.readbasic" }}
				allowHmacFormExecutionResult
			>
				<p>Execution result</p>
			</ProtectedRoute>,
		);

		expect(screen.getByText("No access")).toBeInTheDocument();
		expect(screen.queryByText("Execution result")).not.toBeInTheDocument();
	});

	it("needs a platform-wide grant for a global requirement", () => {
		renderWithProviders(
			<ProtectedRoute
				requirePermission={{ permission: "users.read", at: "global" }}
			>
				<p>Platform page</p>
			</ProtectedRoute>,
		);
		expect(screen.getByText("No access")).toBeInTheDocument();
	});

	it("waits for the caller's authorization", () => {
		authz.isLoading = true;
		renderWithProviders(
			<ProtectedRoute requirePermission={{ permission: "users.read" }}>
				<p>Users page</p>
			</ProtectedRoute>,
		);
		expect(
			screen.getByRole("status", { name: /loading access/i }),
		).toBeInTheDocument();
	});

	it("offers a retry when authorization cannot be loaded", async () => {
		authz.isError = true;
		authz.authorization = undefined;
		const { user } = renderWithProviders(
			<ProtectedRoute requirePermission={{ permission: "users.read" }}>
				<p>Users page</p>
			</ProtectedRoute>,
		);
		await user.click(screen.getByRole("button", { name: "Try again" }));
		expect(authz.refetch).toHaveBeenCalled();
	});
});
