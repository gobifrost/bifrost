import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Route, Routes } from "react-router-dom";
import { renderWithProviders, screen, waitFor, within } from "@/test-utils";

const mockUseMediaQuery = vi.fn(() => false);
vi.mock("@/hooks/useMediaQuery", () => ({
	useMediaQuery: () => mockUseMediaQuery(),
}));
afterEach(() => mockUseMediaQuery.mockReturnValue(false));
const mockUseUsersPage = vi.fn();
const mockUseUser = vi.fn();
const mockUseDeleteUser = vi.fn();
const mockUseUpdateUser = vi.fn();
const mockUseOrganizations = vi.fn();
const mockUseAuth = vi.fn();
const mockUseOrgScope = vi.fn();
const mockResendMutate = vi.fn();
const mockRegenerateMutate = vi.fn();
const mockRevokeMutate = vi.fn();
const mockSendInviteMutate = vi.fn();
const mockUseEventSources = vi.fn();
const mockToastSuccess = vi.fn();
const mockToastError = vi.fn();
const mockRefetch = vi.fn();
const mockEditUserDialog = vi.fn();

vi.mock("@/hooks/useUsers", () => ({
	useUsersPage: (...args: unknown[]) => mockUseUsersPage(...args),
	useUser: (...args: unknown[]) => mockUseUser(...args),
	useDeleteUser: () => mockUseDeleteUser(),
	useUpdateUser: () => mockUseUpdateUser(),
}));

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: (...args: unknown[]) => mockUseOrganizations(...args),
}));

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => mockUseAuth(),
}));

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

const PROVIDER_ORG = "org-provider";

function adminSummary(): AuthorizationSummary {
	return {
		is_platform_admin: true,
		home_organization_id: PROVIDER_ORG,
		provider_organization_id: PROVIDER_ORG,
		base_role: { id: "admin-role", name: "Platform Admin" },
		grants: [],
	};
}

function operatorSummary(): AuthorizationSummary {
	return {
		...adminSummary(),
		is_platform_admin: false,
		base_role: { id: "user-role", name: "User" },
		grants: [
			"users.read",
			"users.readwrite",
			"organizations.read",
			"roleassignments.read",
			"roleassignments.readwrite",
		].map((permission) => ({
			permission,
			boundary: { kind: "managed_organizations", organization_id: null },
		})),
	};
}

vi.mock("@/contexts/OrgScopeContext", () => ({
	useOrgScope: () => mockUseOrgScope(),
}));

vi.mock("@/hooks/useUserInvites", () => ({
	useResendInvite: () => ({ mutate: mockResendMutate }),
	useRegenerateInvite: () => ({ mutate: mockRegenerateMutate }),
	useRevokeInvite: () => ({ mutate: mockRevokeMutate }),
	useSendInvite: () => ({
		mutateAsync: mockSendInviteMutate,
		isPending: false,
	}),
}));

vi.mock("@/services/events", () => ({
	useEventSources: () => mockUseEventSources(),
}));

vi.mock("sonner", () => ({
	toast: {
		success: (...args: unknown[]) => mockToastSuccess(...args),
		error: (...args: unknown[]) => mockToastError(...args),
	},
}));

vi.mock("@/components/forms/OrganizationSelect", () => ({
	OrganizationSelect: () => null,
}));

vi.mock("@/components/users/CreateUserDialog", () => ({
	CreateUserDialog: () => null,
}));

vi.mock("@/components/users/EditUserDialog", () => ({
	EditUserDialog: (props: { open: boolean; user?: { id: string } }) => {
		mockEditUserDialog(props);
		return props.open ? (
			<div role="dialog">Edit user test dialog</div>
		) : null;
	},
}));

vi.mock("@/components/users/BulkUserDialogs", () => ({
	BulkMoveOrgDialog: () => null,
	BulkReplaceRolesDialog: () => null,
	BulkResultDialog: () => null,
	BulkSetActiveDialog: () => null,
}));

import {
	canAnywhere,
	canAt,
	meetsRequirement,
	type AuthorizationSummary,
	type AuthorizationTarget,
	type PermissionRequirement,
} from "@/lib/authorization";
import { Users } from "./Users";

beforeEach(() => {
	authz.summary = adminSummary();
});

function renderUsersRoute(initialEntry = "/users") {
	return renderWithProviders(
		<Routes>
			<Route path="/users" element={<Users />} />
			<Route path="/users/:userId" element={<Users />} />
		</Routes>,
		{ initialEntries: [initialEntry] },
	);
}

const registrationUrl = "https://example.test/accept-invite?token=invite-token";

function pendingInviteUser() {
	return {
		id: "user-1",
		email: "alice@example.com",
		name: "Alice",
		is_active: true,
		is_superuser: false,
		organization_id: "org-1",
		invite_status: "pending",
		created_at: "2026-06-01T00:00:00Z",
		last_login: null,
	};
}

function makeUser(overrides: Record<string, unknown> = {}) {
	return {
		id: "user-1",
		email: "dev@gobifrost.com",
		name: "Dev Admin",
		is_active: true,
		is_superuser: true,
		is_verified: true,
		is_registered: true,
		is_system: false,
		mfa_enabled: false,
		organization_id: "org-provider",
		last_login: null,
		created_at: "2026-06-01T00:00:00Z",
		updated_at: "2026-06-01T00:00:00Z",
		invite_status: "active",
		...overrides,
	};
}

describe("Users — registration links", () => {
	let originalWriteText: typeof navigator.clipboard.writeText | undefined;

	beforeEach(() => {
		originalWriteText = navigator.clipboard?.writeText.bind(
			navigator.clipboard,
		);
		mockUseUsersPage.mockReturnValue({
			data: { items: [pendingInviteUser()], total: 1 },
			isLoading: false,
			isFetching: false,
			isError: false,
			refetch: vi.fn(),
		});
		mockUseUser.mockReturnValue({ data: undefined });
		mockUseDeleteUser.mockReturnValue({ mutateAsync: vi.fn() });
		mockUseUpdateUser.mockReturnValue({ mutateAsync: vi.fn() });
		mockUseOrganizations.mockReturnValue({
			data: [{ id: "org-1", name: "Acme", is_provider: false }],
		});
		mockUseAuth.mockReturnValue({
			user: { id: "admin-1" },
			isPlatformAdmin: false,
		});
		mockUseOrgScope.mockReturnValue({
			scope: { type: "global", orgName: null },
		});
		mockRegenerateMutate.mockImplementation((_userId, options) => {
			options?.onSuccess?.({
				registration_url: registrationUrl,
				event_emitted: false,
			});
		});
		mockResendMutate.mockReset();
		mockRevokeMutate.mockReset();
		mockSendInviteMutate.mockReset();
		mockSendInviteMutate.mockResolvedValue({});
		mockUseEventSources.mockReturnValue({
			data: {
				items: [
					{
						id: "source-1",
						source_type: "topic",
						event_type: "user.invited",
						is_active: true,
						subscription_count: 1,
					},
				],
			},
		});
		mockToastSuccess.mockReset();
		mockToastError.mockReset();
		mockEditUserDialog.mockClear();
	});

	it("waits for the route-selected user instead of pre-opening the dialog", async () => {
		mockUseUser.mockReturnValue({
			data: makeUser(),
			isLoading: false,
			isError: false,
			refetch: vi.fn(),
		});
		const { user } = renderUsersRoute();

		await user.click(screen.getByText("Alice"));

		expect(await screen.findByRole("dialog")).toBeInTheDocument();
		expect(mockEditUserDialog).toHaveBeenLastCalledWith(
			expect.objectContaining({
				open: true,
				user: expect.objectContaining({ id: "user-1" }),
			}),
		);
	});

	it("shows loading, error, and not-found states for user deep links", async () => {
		const retry = vi.fn();
		let phase: "loading" | "error" | "retrying" | "resolved" = "loading";
		mockUseUser.mockImplementation((userId?: string) => {
			if (!userId) {
				return {
					data: undefined,
					isLoading: false,
					isFetching: false,
					isError: false,
					refetch: retry,
				};
			}
			if (phase === "loading") {
				return {
					data: undefined,
					isLoading: true,
					isFetching: true,
					isError: false,
					refetch: retry,
				};
			}
			if (phase === "error") {
				return {
					data: undefined,
					isLoading: false,
					isFetching: false,
					isError: true,
					error: new Error("User API down"),
					refetch: retry,
				};
			}
			if (phase === "retrying") {
				return {
					data: undefined,
					isLoading: false,
					isFetching: true,
					isError: true,
					error: new Error("User API down"),
					refetch: retry,
				};
			}
			return {
				data: undefined,
				isLoading: false,
				isFetching: false,
				isError: false,
				refetch: retry,
			};
		});
		const { rerender, user } = renderUsersRoute("/users/user-1");

		expect(
			screen.getByRole("status", { name: "Loading user" }),
		).toBeVisible();
		expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

		phase = "error";
		rerender(
			<Routes>
				<Route path="/users" element={<Users />} />
				<Route path="/users/:userId" element={<Users />} />
			</Routes>,
		);

		expect(
			screen.getByRole("alert", { name: "User could not be loaded" }),
		).toHaveTextContent("User API down");
		await user.click(screen.getByRole("button", { name: "Retry user" }));
		expect(retry).toHaveBeenCalledOnce();
		phase = "retrying";
		rerender(
			<Routes>
				<Route path="/users" element={<Users />} />
				<Route path="/users/:userId" element={<Users />} />
			</Routes>,
		);
		expect(
			screen.getByRole("button", { name: "Retrying user…" }),
		).toBeDisabled();
		phase = "resolved";
		rerender(
			<Routes>
				<Route path="/users" element={<Users />} />
				<Route path="/users/:userId" element={<Users />} />
			</Routes>,
		);
		await user.click(screen.getByRole("button", { name: "Back to users" }));
		expect(
			screen.queryByRole("alert", { name: "User could not be loaded" }),
		).not.toBeInTheDocument();
		expect(screen.getByText("Alice")).toBeVisible();
	});

	it("shows a not-found state for missing user deep links", () => {
		const retry = vi.fn();
		mockUseUser.mockReturnValue({
			data: undefined,
			isLoading: false,
			isError: false,
			refetch: retry,
		});
		renderUsersRoute("/users/user-1");
		expect(
			screen.getByRole("alert", { name: "User not found" }),
		).toHaveTextContent(
			"The selected user may have been deleted or you no longer have access to it.",
		);
	});

	afterEach(() => {
		if (originalWriteText && navigator.clipboard) {
			(
				navigator.clipboard as unknown as {
					writeText: typeof originalWriteText;
				}
			).writeText = originalWriteText;
		}
	});

	it("shows a generated registration link in a modal", async () => {
		const { user } = renderWithProviders(<Users />);

		await user.click(screen.getByRole("button", { name: "Alice actions" }));
		await user.click(screen.getByText(/generate registration link/i));

		expect(
			await screen.findByRole("heading", {
				name: /registration link ready/i,
			}),
		).toBeInTheDocument();
		expect(screen.queryByText("Destination")).not.toBeInTheDocument();
		expect(screen.queryByText(registrationUrl)).not.toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: /send registration email/i }),
		).toBeEnabled();
		expect(
			screen.getByRole("button", { name: /copy registration link/i }),
		).toBeInTheDocument();
		expect(
			screen.queryByRole("textbox", { name: /registration link/i }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("link", { name: /open link/i }),
		).not.toBeInTheDocument();
		expect(mockRegenerateMutate).toHaveBeenCalledWith(
			"user-1",
			expect.objectContaining({
				onSuccess: expect.any(Function),
				onError: expect.any(Function),
			}),
		);
	});

	it("shows the modal instead of crashing when clipboard is unavailable", async () => {
		(
			navigator.clipboard as unknown as {
				writeText: undefined;
			}
		).writeText = undefined;
		const { user } = renderWithProviders(<Users />);

		await user.click(screen.getByRole("button", { name: "Alice actions" }));
		await user.click(screen.getByText(/copy registration link/i));

		await waitFor(() => {
			expect(
				screen.getByRole("heading", {
					name: /registration link ready/i,
				}),
			).toBeInTheDocument();
		});
		expect(mockToastSuccess).not.toHaveBeenCalled();
	});

	it("retains a generated link when delivery fails and retries the same link", async () => {
		mockSendInviteMutate
			.mockRejectedValueOnce({ detail: "Synthetic delivery failure" })
			.mockResolvedValueOnce({});
		const { user } = renderWithProviders(<Users />);
		await user.click(screen.getByRole("button", { name: "Alice actions" }));
		await user.click(screen.getByText(/generate registration link/i));
		await user.click(
			await screen.findByRole("button", {
				name: /send registration email/i,
			}),
		);
		await waitFor(() =>
			expect(screen.getByRole("alert")).toHaveTextContent(
				"Synthetic delivery failure",
			),
		);
		expect(
			screen.getByRole("button", { name: /copy registration link/i }),
		).toBeEnabled();
		await user.click(
			screen.getByRole("button", { name: /send registration email/i }),
		);
		await waitFor(() =>
			expect(mockSendInviteMutate).toHaveBeenCalledTimes(2),
		);
		expect(mockSendInviteMutate).toHaveBeenNthCalledWith(2, {
			userId: "user-1",
			registrationUrl,
		});
	});

	it("sends the registration email from a generated link", async () => {
		const { user } = renderWithProviders(<Users />);

		await user.click(screen.getByRole("button", { name: "Alice actions" }));
		await user.click(screen.getByText(/generate registration link/i));
		await user.click(
			await screen.findByRole("button", {
				name: /send registration email/i,
			}),
		);

		await waitFor(() => {
			expect(mockSendInviteMutate).toHaveBeenCalledWith({
				userId: "user-1",
				registrationUrl,
			});
		});
		expect(mockToastSuccess).toHaveBeenCalledWith(
			"Registration email sent",
		);
	});
});

describe("Users", () => {
	beforeEach(() => {
		mockRefetch.mockReset();
		mockUseUsersPage.mockReset();
		mockUseUsersPage.mockReturnValue({
			data: { items: [makeUser()], total: 1 },
			isLoading: false,
			isFetching: false,
			isError: false,
			refetch: mockRefetch,
		});
		mockUseUser.mockReturnValue({ data: undefined });
		mockUseOrganizations.mockReset();
		mockUseOrganizations.mockReturnValue({
			data: [
				{
					id: "org-provider",
					name: "Provider",
					domain: null,
					is_provider: true,
					is_active: true,
				},
			],
		});
		mockUseAuth.mockReturnValue({
			isPlatformAdmin: true,
			user: { id: "current-user" },
		});
		mockUseOrgScope.mockReturnValue({
			scope: { type: "global", orgId: null, orgName: null },
		});
		mockUseDeleteUser.mockReturnValue({
			mutateAsync: vi.fn(),
			isPending: false,
		});
		mockUseUpdateUser.mockReturnValue({
			mutateAsync: vi.fn(),
			isPending: false,
		});
		mockUseEventSources.mockReturnValue({ data: { items: [] } });
	});

	it("shows the provider organization for provider-scoped superusers", () => {
		renderWithProviders(<Users />);

		const row = screen.getByText("Dev Admin").closest("tr");
		expect(row).not.toBeNull();
		expect(within(row!).getByText("Provider")).toBeInTheDocument();
		expect(row).not.toHaveTextContent("—");
	});

	it("includes inactive users when Show Inactive is enabled", async () => {
		const { user } = renderWithProviders(<Users />);

		expect(mockUseUsersPage).toHaveBeenLastCalledWith(
			expect.objectContaining({
				includeInactive: false,
				offset: 0,
				limit: 25,
			}),
		);

		await user.click(screen.getByRole("switch", { name: "Show Inactive" }));

		expect(mockUseUsersPage).toHaveBeenLastCalledWith(
			expect.objectContaining({
				includeInactive: true,
				offset: 0,
				limit: 25,
			}),
		);
	});

	it("keeps pagination in the bounded table footer", () => {
		mockUseUsersPage.mockReturnValue({
			data: { items: [makeUser()], total: 30 },
			isLoading: false,
			isFetching: false,
			isError: false,
			refetch: mockRefetch,
		});

		renderWithProviders(<Users />);

		const pagination = screen.getByRole("navigation", {
			name: /pagination/i,
		});
		expect(pagination.closest("tfoot")).not.toBeNull();
		expect(
			screen.getAllByRole("table")[0].parentElement?.parentElement,
		).toHaveClass("max-h-full");
	});
	it("keeps mobile selection, account identity and server sorting available", async () => {
		mockUseMediaQuery.mockReturnValue(true);
		const { user } = renderWithProviders(<Users />);
		expect(screen.queryByRole("table")).not.toBeInTheDocument();
		const records = screen.getByRole("list", { name: "Users" });
		expect(within(records).getByText("Platform admin")).toBeVisible();
		expect(within(records).getByText("dev@gobifrost.com")).toBeVisible();
		await user.click(
			screen.getByRole("checkbox", { name: "Select Dev Admin" }),
		);
		expect(
			screen.getByRole("region", { name: "Bulk user actions" }),
		).toHaveTextContent("1 selected");
		await user.selectOptions(
			screen.getByRole("combobox", { name: "Sort users" }),
			"last_login:desc",
		);
		expect(mockUseUsersPage).toHaveBeenLastCalledWith(
			expect.objectContaining({
				sortBy: "last_login",
				sortDirection: "desc",
				offset: 0,
			}),
		);
	});
	it("keeps self-selection disabled on mobile", () => {
		mockUseMediaQuery.mockReturnValue(true);
		mockUseAuth.mockReturnValue({
			isPlatformAdmin: true,
			user: { id: "user-1" },
		});
		renderWithProviders(<Users />);
		expect(
			screen.getByRole("checkbox", { name: "Cannot select yourself" }),
		).toBeDisabled();
	});
	it("retains cached records and selection when refresh fails", async () => {
		mockUseMediaQuery.mockReturnValue(true);
		const { user, rerender } = renderWithProviders(<Users />);
		await user.click(
			screen.getByRole("checkbox", { name: "Select Dev Admin" }),
		);
		mockUseUsersPage.mockReturnValue({
			data: { items: [makeUser()], total: 1 },
			isLoading: false,
			isFetching: false,
			isError: true,
			refetch: mockRefetch,
		});
		rerender(<Users />);
		expect(screen.getByRole("alert")).toHaveTextContent(
			"Users could not be refreshed",
		);
		expect(
			screen.getByRole("checkbox", { name: "Select Dev Admin" }),
		).toBeChecked();
		await user.click(screen.getByRole("button", { name: "Retry users" }));
		expect(mockRefetch).toHaveBeenCalledOnce();
	});
});

describe("Users — permission-driven actions", () => {
	beforeEach(() => {
		mockUseUser.mockReturnValue({ data: undefined });
		mockUseDeleteUser.mockReturnValue({ mutateAsync: vi.fn() });
		mockUseUpdateUser.mockReturnValue({ mutateAsync: vi.fn() });
		mockUseOrganizations.mockReturnValue({
			data: [
				{ id: "org-1", name: "Acme", is_provider: false },
				{ id: PROVIDER_ORG, name: "Provider", is_provider: true },
			],
		});
		mockUseAuth.mockReturnValue({ user: { id: "current-user" } });
		mockUseOrgScope.mockReturnValue({
			scope: { type: "global", orgName: null },
		});
		mockUseEventSources.mockReturnValue({ data: { items: [] } });
		mockUseUsersPage.mockReturnValue({
			data: {
				items: [
					makeUser({
						id: "ordinary",
						name: "Ordinary Person",
						email: "ordinary@acme.test",
						is_superuser: false,
						organization_id: "org-1",
					}),
					makeUser({
						id: "protected",
						name: "Guarded Admin",
						email: "guarded@acme.test",
						is_superuser: false,
						is_protected: true,
						organization_id: "org-1",
					}),
				],
				total: 2,
			},
			isLoading: false,
			isFetching: false,
			isError: false,
			refetch: vi.fn(),
		});
	});

	it("gives an operator support actions only, and no deletion", async () => {
		authz.summary = operatorSummary();
		const { user } = renderUsersRoute();

		expect(
			screen.getByRole("button", { name: "Create user" }),
		).toBeInTheDocument();
		await user.click(
			screen.getByRole("button", { name: "Ordinary Person actions" }),
		);
		expect(
			screen.getByRole("menuitem", { name: "Disable" }),
		).not.toHaveAttribute("data-disabled");
		expect(
			screen.queryByRole("menuitem", { name: "Delete" }),
		).not.toBeInTheDocument();
	});

	it("shows an operator a protected user's actions disabled, with why", async () => {
		authz.summary = operatorSummary();
		const { user } = renderUsersRoute();

		expect(screen.getAllByText("Protected").length).toBeGreaterThan(0);
		await user.click(
			screen.getByRole("button", { name: "Guarded Admin actions" }),
		);
		expect(
			screen.getByText(/only a platform admin can change it/i),
		).toBeInTheDocument();
		expect(
			screen.getByRole("menuitem", { name: "Disable" }),
		).toHaveAttribute("data-disabled");
	});

	it("lets a Platform Admin change protected users", async () => {
		const { user } = renderUsersRoute();

		await user.click(
			screen.getByRole("button", { name: "Guarded Admin actions" }),
		);
		expect(
			screen.queryByText(/only a platform admin can change it/i),
		).not.toBeInTheDocument();
		expect(
			screen.getByRole("menuitem", { name: "Delete" }),
		).not.toHaveAttribute("data-disabled");
	});

	it("hides creation, selection and row actions from a read-only viewer", () => {
		authz.summary = {
			...operatorSummary(),
			grants: operatorSummary().grants.filter((grant) =>
				grant.permission.endsWith(".read"),
			),
		};
		renderUsersRoute();

		expect(
			screen.queryByRole("button", { name: "Create user" }),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("checkbox", {
				name: "Select all visible users",
			}),
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole("button", { name: "Ordinary Person actions" }),
		).not.toBeInTheDocument();
	});
});
