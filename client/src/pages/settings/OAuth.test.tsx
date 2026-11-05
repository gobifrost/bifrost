import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";
import { OAuth } from "./OAuth";
import { LoginPolicy } from "./oauth/PreferredSignIn";

vi.mock("@/components/users/SsoExclusions", () => ({
	SsoExclusions: () => null,
}));

const refetch = vi.fn();
const updateLoginPreference = vi.fn();
const configData = {
	providers: [
		{
			provider: "microsoft",
			configured: true,
			client_id: "client-id",
			client_secret_set: true,
			tenant_id: "organizations",
		},
		{
			provider: "google",
			configured: false,
			client_secret_set: false,
		},
		{
			provider: "oidc",
			configured: false,
			client_secret_set: false,
		},
	],
	callback_url: "http://localhost/auth/oauth/callback",
	login_preference: {
		auto_redirect_to_sso: false,
		default_sso_provider: null,
		password_login_enabled: true,
		passkey_login_enabled: true,
	},
};

vi.mock("@/services/oauth-config", () => ({
	useOAuthConfigs: () => ({
		data: configData,
		isLoading: false,
		refetch,
	}),
	useUpdateOAuthLoginPreference: () => ({
		mutateAsync: updateLoginPreference,
		isPending: false,
	}),
	useUpdateMicrosoftConfig: () => ({ mutateAsync: vi.fn() }),
	useUpdateGoogleConfig: () => ({ mutateAsync: vi.fn() }),
	useUpdateOIDCConfig: () => ({ mutateAsync: vi.fn() }),
	useDeleteOAuthConfig: () => ({ mutateAsync: vi.fn() }),
	useTestOAuthConfig: () => ({ mutateAsync: vi.fn() }),
}));

describe("OAuth settings", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		updateLoginPreference.mockResolvedValue({});
		refetch.mockResolvedValue({});
	});

	it("saves a configured provider as the preferred first attempt", async () => {
		const { user } = renderWithProviders(<OAuth />);

		const preferenceSwitch = screen.getByRole("switch", {
			name: /prefer sso on login/i,
		});
		await user.click(preferenceSwitch);
		expect(preferenceSwitch).toHaveAttribute("aria-checked", "true");
		await user.click(
			screen.getByRole("button", { name: /save preference/i }),
		);

		await waitFor(() => {
			expect(updateLoginPreference).toHaveBeenCalledWith({
				body: {
					auto_redirect_to_sso: true,
					default_sso_provider: "microsoft",
					password_login_enabled: true,
					passkey_login_enabled: true,
				},
			});
		});
	});
});

describe("Login policy card", () => {
	it("saves password and passkey restrictions through the login preference mutation", async () => {
		const { user } = renderWithProviders(<OAuth />);

		await user.click(
			screen.getByRole("switch", { name: /password sign-in/i }),
		);
		await user.click(
			screen.getByRole("switch", { name: /passkey sign-in/i }),
		);
		await user.click(
			screen.getByRole("button", { name: /save login policy/i }),
		);

		await waitFor(() => {
			expect(updateLoginPreference).toHaveBeenCalledWith({
				body: {
					auto_redirect_to_sso: false,
					default_sso_provider: null,
					password_login_enabled: false,
					passkey_login_enabled: false,
				},
			});
		});
	});

	it("disables the policy switches when no provider is configured", () => {
		renderWithProviders(
			<LoginPolicy
				passwordLoginEnabled
				passkeyLoginEnabled
				hasProviders={false}
				pending={false}
				failed={false}
				onChange={() => undefined}
				onSave={() => undefined}
			/>,
		);

		expect(
			screen.getByRole("switch", { name: /password sign-in/i }),
		).toBeDisabled();
		expect(
			screen.getByRole("switch", { name: /passkey sign-in/i }),
		).toBeDisabled();
		expect(
			screen.getByRole("button", { name: /save login policy/i }),
		).toBeDisabled();
	});
});
