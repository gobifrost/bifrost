import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, waitFor, within } from "@/test-utils";

const mockUseUsersFiltered = vi.fn();
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

vi.mock("@/hooks/useUsers", () => ({
	useUsersFiltered: (...args: unknown[]) => mockUseUsersFiltered(...args),
	useDeleteUser: () => mockUseDeleteUser(),
	useUpdateUser: () => mockUseUpdateUser(),
}));

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: (...args: unknown[]) => mockUseOrganizations(...args),
}));

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => mockUseAuth(),
}));

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
	EditUserDialog: () => null,
}));

vi.mock("@/components/users/BulkUserDialogs", () => ({
	BulkMoveOrgDialog: () => null,
	BulkReplaceRolesDialog: () => null,
	BulkResultDialog: () => null,
	BulkSetActiveDialog: () => null,
}));

import { Users } from "./Users";

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
		mockUseUsersFiltered.mockReturnValue({
			data: [pendingInviteUser()],
			isLoading: false,
			refetch: vi.fn(),
		});
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

		await user.click(screen.getByRole("button", { name: /user actions/i }));
		await user.click(screen.getByText(/generate registration link/i));

		expect(
			await screen.findByRole("heading", { name: /user created/i }),
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

		await user.click(screen.getByRole("button", { name: /user actions/i }));
		await user.click(screen.getByText(/copy registration link/i));

		await waitFor(() => {
			expect(
				screen.getByRole("heading", { name: /user created/i }),
			).toBeInTheDocument();
		});
		expect(mockToastSuccess).not.toHaveBeenCalled();
	});

	it("sends the registration email from a generated link", async () => {
		const { user } = renderWithProviders(<Users />);

		await user.click(screen.getByRole("button", { name: /user actions/i }));
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
		mockUseUsersFiltered.mockReset();
		mockUseUsersFiltered.mockReturnValue({
			data: [makeUser()],
			isLoading: false,
			refetch: mockRefetch,
		});
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
});
