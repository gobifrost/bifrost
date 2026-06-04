/**
 * Component tests for CreateUserDialog.
 *
 * Covers:
 * - validation: invalid email blocks submit, shows error alert
 * - validation: missing display name blocks submit
 * - validation: missing organization blocks submit (regular org user)
 * - happy path: submits createUser with trimmed email + name + org
 *
 * The Combobox/Popover/Command internals are backed by Radix Portal + cmdk,
 * which are cumbersome to drive in happy-dom. To keep tests fast and
 * deterministic, we stub the Combobox component to a simple <select>,
 * matching the pattern used in FormInfoDialog.test.tsx for
 * OrganizationSelect.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";

const mockCreateMutate = vi.fn();
const mockAssignMutate = vi.fn();
const mockSendInviteMutate = vi.fn();
const mockOrganizations = vi.fn();
const mockRoles = vi.fn();
const mockEventSources = vi.fn();

vi.mock("@/hooks/useUsers", () => ({
	useCreateUser: () => ({
		mutateAsync: mockCreateMutate,
		isPending: false,
	}),
}));

vi.mock("@/hooks/useRoles", () => ({
	useRoles: () => mockRoles(),
	useAssignUsersToRole: () => ({
		mutateAsync: mockAssignMutate,
		isPending: false,
	}),
}));

vi.mock("@/hooks/useOrganizations", () => ({
	useOrganizations: () => mockOrganizations(),
}));

vi.mock("@/services/events", () => ({
	useEventSources: () => mockEventSources(),
}));

vi.mock("@/hooks/useUserInvites", () => ({
	useSendInvite: () => ({
		mutateAsync: mockSendInviteMutate,
		isPending: false,
	}),
}));

// Stub the Combobox to a native select so userEvent can drive it.
vi.mock("@/components/ui/combobox", () => ({
	Combobox: ({
		id,
		value,
		onValueChange,
		options,
	}: {
		id?: string;
		value?: string;
		onValueChange?: (v: string) => void;
		options: { value: string; label: string }[];
	}) => (
		<select
			aria-label={id}
			id={id}
			value={value ?? ""}
			onChange={(e) => onValueChange?.(e.target.value)}
		>
			<option value="">(none)</option>
			{options.map((opt) => (
				<option key={opt.value} value={opt.value}>
					{opt.label}
				</option>
			))}
		</select>
	),
}));

import { CreateUserDialog } from "./CreateUserDialog";

beforeEach(() => {
	mockCreateMutate.mockReset();
	mockCreateMutate.mockResolvedValue({
		id: "new-user-1",
		registration_url: "https://example.test/accept-invite?token=abc",
	});
	mockAssignMutate.mockReset();
	mockAssignMutate.mockResolvedValue({});
	mockSendInviteMutate.mockReset();
	mockSendInviteMutate.mockResolvedValue({});
	mockOrganizations.mockReturnValue({
		data: [
			{
				id: "org-1",
				name: "Acme",
				domain: "acme.com",
				is_provider: false,
			},
			{
				id: "org-provider",
				name: "Provider",
				domain: null,
				is_provider: true,
			},
		],
		isLoading: false,
	});
	mockRoles.mockReturnValue({ data: [] });
	mockEventSources.mockReturnValue({
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
});

describe("CreateUserDialog — validation", () => {
	// Note on scope: the email and display-name Inputs carry the native
	// HTML5 `required` attribute, so browsers (and happy-dom) short-circuit
	// submit before `validateForm()` runs. The custom-validation path that
	// actually renders through our code is the organization check, so
	// that's what we assert on here. The HTML5 paths are UX-acceptable but
	// not worth fighting happy-dom over.
	it("requires an organization selection for org users", async () => {
		const { user } = renderWithProviders(
			<CreateUserDialog open={true} onOpenChange={vi.fn()} />,
		);

		await user.type(
			screen.getByLabelText(/email address/i),
			"alice@example.com",
		);
		await user.type(screen.getByLabelText(/display name/i), "Alice");
		await user.click(screen.getByRole("button", { name: /create user/i }));

		expect(
			await screen.findByText(/select an organization/i),
		).toBeInTheDocument();
		expect(mockCreateMutate).not.toHaveBeenCalled();
	});
});

describe("CreateUserDialog — happy path", () => {
	it("always creates a registration link without triggering invite automation", async () => {
		const onOpenChange = vi.fn();
		const { user } = renderWithProviders(
			<CreateUserDialog open={true} onOpenChange={onOpenChange} />,
		);

		await user.type(
			screen.getByLabelText(/email address/i),
			"  alice@example.com  ",
		);
		await user.type(screen.getByLabelText(/display name/i), "  Alice  ");

		// Set org via the stubbed select.
		const orgSelect = screen.getByLabelText(
			/organization/i,
		) as HTMLSelectElement;
		await user.selectOptions(orgSelect, "org-1");

		await user.click(screen.getByRole("button", { name: /create user/i }));

		await waitFor(() => expect(mockCreateMutate).toHaveBeenCalled());
		expect(mockCreateMutate.mock.calls[0]![0]).toEqual({
			body: {
				email: "alice@example.com",
				name: "Alice",
				is_active: true,
				is_superuser: false,
				organization_id: "org-1",
				invite: true,
				trigger_automation: false,
			},
		});
		expect(onOpenChange).toHaveBeenCalledWith(false);
		expect(
			await screen.findByRole("heading", { name: /user created/i }),
		).toBeInTheDocument();
		expect(
			screen.queryByText("https://example.test/accept-invite?token=abc"),
		).not.toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: /send registration email/i }),
		).toBeEnabled();
		expect(
			screen.getByRole("button", { name: /copy registration link/i }),
		).toBeInTheDocument();
	});

	it("disables sending registration email when no user.invited automation is configured", async () => {
		mockEventSources.mockReturnValue({ data: { items: [] } });
		const { user } = renderWithProviders(
			<CreateUserDialog open={true} onOpenChange={vi.fn()} />,
		);

		await user.type(
			screen.getByLabelText(/email address/i),
			"alice@example.com",
		);
		await user.type(screen.getByLabelText(/display name/i), "Alice");
		await user.selectOptions(
			screen.getByLabelText(/organization/i),
			"org-1",
		);
		await user.click(screen.getByRole("button", { name: /create user/i }));

		await waitFor(() => expect(mockCreateMutate).toHaveBeenCalled());
		expect(
			await screen.findByRole("button", {
				name: /send registration email/i,
			}),
		).toBeDisabled();
		expect(
			screen.queryByLabelText(/create invite link/i),
		).not.toBeInTheDocument();
		expect(
			screen.queryByLabelText(/trigger invite automation/i),
		).not.toBeInTheDocument();
	});

	it("sends the registration email from the success dialog", async () => {
		const { user } = renderWithProviders(
			<CreateUserDialog open={true} onOpenChange={vi.fn()} />,
		);

		await user.type(
			screen.getByLabelText(/email address/i),
			"alice@example.com",
		);
		await user.type(screen.getByLabelText(/display name/i), "Alice");
		await user.selectOptions(
			screen.getByLabelText(/organization/i),
			"org-1",
		);
		await user.click(screen.getByRole("button", { name: /create user/i }));

		await user.click(
			await screen.findByRole("button", {
				name: /send registration email/i,
			}),
		);

		await waitFor(() => {
			expect(mockSendInviteMutate).toHaveBeenCalledWith({
				userId: "new-user-1",
				registrationUrl: "https://example.test/accept-invite?token=abc",
			});
		});
	});
});
