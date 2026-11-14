import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";
import { Login } from "./Login";
import {
	getAuthStatus,
	initOAuth,
	PREFERRED_SSO_REDIRECT_ATTEMPTED_KEY,
	type AuthStatus,
} from "@/services/auth";

const login = vi.fn();
const loginWithMfa = vi.fn();
const loginWithPasskey = vi.fn();

vi.mock("@/contexts/AuthContext", () => ({
	useAuth: () => ({
		login,
		loginWithMfa,
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
	passkey_login_enabled: true,
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
		login.mockReset();
		loginWithMfa.mockReset();
		loginWithPasskey.mockReset();
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
		expect(sessionStorage.getItem("oauth_redirect_from")).toBe(
			"/workflows",
		);
		expect(loginWithPasskey).not.toHaveBeenCalled();
	});

	it("stores a same-origin path for an absolute MCP return_to", async () => {
		const returnTo = encodeURIComponent(
			`${window.location.origin}/mcp/callback?internal_state=abc`,
		);
		renderWithProviders(<Login />, {
			initialEntries: [`/login?return_to=${returnTo}`],
		});

		await waitFor(() => {
			expect(initOAuth).toHaveBeenCalled();
		});
		expect(sessionStorage.getItem("oauth_redirect_from")).toBe(
			"/mcp/callback?internal_state=abc",
		);
	});

	it("stores / for a cross-origin return_to", async () => {
		renderWithProviders(<Login />, {
			initialEntries: [
				"/login?return_to=https%3A%2F%2Fevil.example.com%2Fmcp%2Fcallback",
			],
		});

		await waitFor(() => {
			expect(initOAuth).toHaveBeenCalled();
		});
		expect(sessionStorage.getItem("oauth_redirect_from")).toBe("/");
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

	it("submits a full formatted recovery code without truncating it", async () => {
		sessionStorage.setItem(PREFERRED_SSO_REDIRECT_ATTEMPTED_KEY, "true");
		login.mockResolvedValueOnce({
			success: false,
			mfaRequired: true,
			mfaToken: "mfa-token",
			availableMethods: ["totp"],
			expiresIn: 300,
		});
		loginWithMfa.mockResolvedValueOnce(undefined);
		renderWithProviders(<Login />, { initialEntries: ["/login"] });

		await screen.findByLabelText("Email");
		const user = (
			await import("@testing-library/user-event")
		).default.setup();
		await user.type(screen.getByLabelText("Email"), "admin@example.com");
		await user.type(screen.getByLabelText("Password"), "password");
		await user.click(screen.getByRole("button", { name: "Sign In" }));

		const mfaInput = await screen.findByLabelText("Authentication Code");
		await user.type(mfaInput, "ABCD-1234");
		await user.click(screen.getByRole("button", { name: "Verify" }));

		await waitFor(() =>
			expect(loginWithMfa).toHaveBeenCalledWith(
				"mfa-token",
				"ABCD-1234",
				false,
			),
		);
	});
});

describe("SSO-only login policy", () => {
	const interactiveStatus: AuthStatus = {
		needs_setup: false,
		password_login_enabled: true,
		passkey_login_enabled: true,
		mfa_required_for_password: false,
		oauth_providers: [
			{
				name: "microsoft",
				display_name: "Microsoft",
				icon: "microsoft",
			},
		],
		auto_redirect_to_sso: false,
		default_sso_provider: null,
	};

	beforeEach(() => {
		vi.clearAllMocks();
		login.mockReset();
		loginWithMfa.mockReset();
		loginWithPasskey.mockReset();
		sessionStorage.clear();
	});

	it("collapses password and passkey into the platform admin expander", async () => {
		const status: AuthStatus = {
			...interactiveStatus,
			password_login_enabled: false,
			passkey_login_enabled: false,
		};
		vi.mocked(getAuthStatus).mockResolvedValue(status);

		const { user } = renderWithProviders(<Login />, {
			initialEntries: ["/login"],
		});

		expect(
			await screen.findByRole("button", { name: "Microsoft" }),
		).toBeInTheDocument();
		// The admin form lives inside the closed expander, not on the page.
		const expander = screen
			.getByText("Excluded Account Sign-In")
			.closest("details");
		expect(expander).not.toHaveAttribute("open");
		expect(screen.getByLabelText("Email").closest("details")).toBe(
			expander,
		);
		expect(loginWithPasskey).not.toHaveBeenCalled();

		await user.click(screen.getByText("Excluded Account Sign-In"));
		expect(expander).toHaveAttribute("open");
		expect(await screen.findByLabelText("Password")).toBeInTheDocument();
	});

	it("collapses the passkey button into the expander and skips the automatic attempt", async () => {
		const status: AuthStatus = {
			...interactiveStatus,
			passkey_login_enabled: false,
		};
		vi.mocked(getAuthStatus).mockResolvedValue(status);

		renderWithProviders(<Login />, { initialEntries: ["/login"] });

		expect(await screen.findByLabelText("Email")).toBeInTheDocument();
		const expander = screen
			.getByText("Excluded Account Sign-In")
			.closest("details");
		expect(expander).not.toHaveAttribute("open");
		expect(
			screen
				.getByRole("button", { name: /sign in with passkey/i })
				.closest("details"),
		).toBe(expander);

		await new Promise((resolve) => setTimeout(resolve, 400));
		expect(loginWithPasskey).not.toHaveBeenCalled();
		expect(sessionStorage.getItem("passkey_auto_attempted")).toBeNull();
	});
});
