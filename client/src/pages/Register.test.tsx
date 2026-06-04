import { describe, expect, it, vi, beforeEach } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";
import { Register } from "./Register";
import { registerFromInvite } from "@/services/auth";
import { registerInviteWithPasskey } from "@/services/passkeys";

const completeLoginWithToken = vi.fn();
const checkAuthStatus = vi.fn();

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({
		completeLoginWithToken,
		checkAuthStatus,
	}),
}));

vi.mock("@/services/auth", () => ({
	getOAuthProviders: vi.fn(async () => []),
	initOAuth: vi.fn(),
	registerFromInvite: vi.fn(),
}));

// Logo pulls from OrgScopeContext (provided at app root in real use); stub it
// so these tests can render the page without the full provider tree.
vi.mock("@/components/branding/Logo", () => ({
	Logo: () => null,
}));

vi.mock("@/services/passkeys", () => ({
	registerInviteWithPasskey: vi.fn(),
}));

describe("Register", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		checkAuthStatus.mockResolvedValue(undefined);
		vi.mocked(registerInviteWithPasskey).mockResolvedValue({
			user_id: "user-1",
			email: "invitee@example.com",
			access_token: "access-token",
			refresh_token: "refresh-token",
		});
		vi.mocked(registerFromInvite).mockResolvedValue(undefined);
	});

	it("shows passkey registration before the password fallback", () => {
		renderWithProviders(<Register />, {
			initialEntries: ["/accept-invite?token=invite-token"],
		});

		expect(
			screen.getByRole("button", { name: /set up passkey/i }),
		).toBeVisible();
		expect(
			screen.getByRole("button", { name: /use password instead/i }),
		).toBeVisible();
		expect(screen.queryByLabelText(/password/i)).not.toBeInTheDocument();
	});

	it("registers an invite with a passkey and stores returned auth tokens", async () => {
		const { user } = renderWithProviders(<Register />, {
			initialEntries: ["/accept-invite?token=invite-token"],
		});

		await user.click(
			screen.getByRole("button", { name: /set up passkey/i }),
		);

		await waitFor(() => {
			expect(registerInviteWithPasskey).toHaveBeenCalledWith(
				"invite-token",
			);
		});
		expect(completeLoginWithToken).toHaveBeenCalledWith("access-token");
		expect(checkAuthStatus).toHaveBeenCalled();
	});

	it("keeps password as a secondary registration option", async () => {
		const { user } = renderWithProviders(<Register />, {
			initialEntries: ["/accept-invite?token=invite-token"],
		});

		await user.click(
			screen.getByRole("button", { name: /use password instead/i }),
		);
		await user.type(screen.getByLabelText("Password"), "InviteePass123!");
		await user.type(
			screen.getByLabelText(/confirm password/i),
			"InviteePass123!",
		);
		await user.click(
			screen.getByRole("button", { name: /create account/i }),
		);

		await waitFor(() => {
			expect(registerFromInvite).toHaveBeenCalledWith(
				"invite-token",
				"InviteePass123!",
			);
		});
	});
});
