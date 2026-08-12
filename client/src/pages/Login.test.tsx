import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";
import { Login } from "./Login";
import {
	getAuthStatus,
	initOAuth,
	PREFERRED_SSO_REDIRECT_ATTEMPTED_KEY,
} from "@/services/auth";

const loginWithPasskey = vi.fn();

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({
		login: vi.fn(),
		loginWithMfa: vi.fn(),
		loginWithPasskey,
		isAuthenticated: false,
		isLoading: false,
	}),
}));

vi.mock("@/services/auth", async (importOriginal) => {
	const actual = await importOriginal<typeof import("@/services/auth")>();
	return {
		...actual,
		getAuthStatus: vi.fn(),
		hashOAuthState: vi.fn(),
		initOAuth: vi.fn(),
	};
});

vi.mock("@/services/passkeys", () => ({
	supportsPasskeys: () => true,
}));

vi.mock("@/components/branding/Logo", () => ({
	Logo: () => null,
}));

vi.mock("@/lib/applicationName", () => ({
	useApplicationName: () => "Bifrost",
}));

const preferredStatus = {
	needs_setup: false,
	password_login_enabled: true,
	mfa_required_for_password: false,
	oauth_providers: [
		{
			name: "microsoft",
			display_name: "Microsoft",
			icon: "microsoft",
		},
	],
	auto_redirect_to_sso: true,
	default_sso_provider: "microsoft" as const,
};

describe("Login preferred SSO redirect", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		sessionStorage.clear();
		vi.mocked(getAuthStatus).mockResolvedValue(preferredStatus);
		vi.mocked(initOAuth).mockImplementation(
			() => new Promise(() => undefined),
		);
	});

	it("tries the preferred provider once before passkey or credentials", async () => {
		renderWithProviders(<Login />, {
			initialEntries: ["/login?returnTo=/workflows"],
		});

		await waitFor(() => {
			expect(initOAuth).toHaveBeenCalledWith(
				"microsoft",
				`${window.location.origin}/auth/callback/microsoft`,
			);
		});
		expect(
			sessionStorage.getItem(PREFERRED_SSO_REDIRECT_ATTEMPTED_KEY),
		).toBe("true");
		expect(sessionStorage.getItem("oauth_redirect_from")).toBe("/workflows");
		expect(loginWithPasskey).not.toHaveBeenCalled();
	});

	it("shows the full login screen after the preferred attempt", async () => {
		sessionStorage.setItem(PREFERRED_SSO_REDIRECT_ATTEMPTED_KEY, "true");

		renderWithProviders(<Login />, { initialEntries: ["/login"] });

		expect(await screen.findByLabelText("Email")).toBeInTheDocument();
		expect(screen.getByLabelText("Password")).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: "Microsoft" }),
		).toBeInTheDocument();
		expect(
			screen.getByRole("button", { name: /sign in with passkey/i }),
		).toBeInTheDocument();
		expect(initOAuth).not.toHaveBeenCalled();
		expect(loginWithPasskey).not.toHaveBeenCalled();
	});
});
