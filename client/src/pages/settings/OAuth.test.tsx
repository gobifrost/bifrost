import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test-utils";
import { OAuth } from "./OAuth";
import { LoginPolicy } from "./oauth/PreferredSignIn";

vi.mock("@/components/users/SsoExclusions", () => ({
	SsoExclusions: () => null,
}));

const refetch = vi.fn();
const updateLoginPreference = vi.fn();
const updateMicrosoftConfig = vi.fn();
const updateGoogleConfig = vi.fn();
const updateOidcConfig = vi.fn();
const configData = {
	providers: [
		{
			provider: "microsoft",
			configured: true,
			client_id: "client-id",
			client_secret_set: true,
			trust_email_for_linking: false,
			tenant_id: "organizations",
			token_endpoint_auth_method: "private_key_jwt",
			private_key_alg: "RS384",
		},
		{
			provider: "google",
			configured: false,
			client_secret_set: false,
			trust_email_for_linking: false,
			token_endpoint_auth_method: "private_key_jwt",
			private_key_alg: "RS384",
		},
		{
			provider: "oidc",
			configured: false,
			client_secret_set: false,
			trust_email_for_linking: false,
			token_endpoint_auth_method: "private_key_jwt",
			private_key_alg: "RS384",
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
	useUpdateMicrosoftConfig: () => ({ mutateAsync: updateMicrosoftConfig }),
	useUpdateGoogleConfig: () => ({ mutateAsync: updateGoogleConfig }),
	useUpdateOIDCConfig: () => ({ mutateAsync: updateOidcConfig }),
	useDeleteOAuthConfig: () => ({ mutateAsync: vi.fn() }),
	useTestOAuthConfig: () => ({ mutateAsync: vi.fn() }),
}));

describe("OAuth settings", () => {
	beforeEach(() => {
		vi.clearAllMocks();
		updateLoginPreference.mockResolvedValue({});
		refetch.mockResolvedValue({});
		configData.providers.forEach((provider, index) => {
			provider.configured = index === 0;
			provider.trust_email_for_linking = false;
		});
	});

	it.each([
		[0, updateMicrosoftConfig],
		[1, updateGoogleConfig],
		[2, updateOidcConfig],
	] as const)(
		"preserves provider %i client authentication when saving without a new secret",
		async (index, save) => {
			configData.providers.forEach((provider) => {
				provider.configured = true;
			});
			const { user } = renderWithProviders(<OAuth />);
			await user.click(
				screen.getAllByRole("button", { name: "Edit" })[index]!,
			);
			await user.click(
				screen.getByRole("button", { name: "Save Configuration" }),
			);

			await waitFor(() =>
				expect(save).toHaveBeenCalledWith({
					body: expect.objectContaining({
						token_endpoint_auth_method: "private_key_jwt",
						private_key_alg: "RS384",
						clear_private_key: false,
						client_secret: null,
					}),
				}),
			);
		},
	);

	it("saves custom scopes from the settings form", async () => {
		configData.providers.forEach((provider) => {
			provider.configured = true;
		});
		const { user } = renderWithProviders(<OAuth />);
		await user.click(screen.getAllByRole("button", { name: "Edit" })[1]!);

		const scopes = screen.getByLabelText(/scopes/i);
		await user.clear(scopes);
		await user.type(scopes, "openid email groups");
		await user.click(
			screen.getByRole("button", { name: "Save Configuration" }),
		);

		await waitFor(() =>
			expect(updateGoogleConfig).toHaveBeenCalledWith({
				body: expect.objectContaining({
					scopes: ["openid", "email", "groups"],
				}),
			}),
		);
	});

	it("starts with email trust off and persists an explicit choice when editing", async () => {
		configData.providers.forEach((provider) => {
			provider.configured = true;
		});
		const { user } = renderWithProviders(<OAuth />);
		await user.click(screen.getAllByRole("button", { name: "Edit" })[2]!);
		const trust = screen.getByRole("switch", {
			name: "Trust Provider Email for Account Linking",
		});
		expect(trust).toHaveAttribute("aria-checked", "false");
		await user.click(trust);
		await user.click(
			screen.getByRole("button", { name: "Save Configuration" }),
		);
		await waitFor(() =>
			expect(updateOidcConfig).toHaveBeenCalledWith({
				body: expect.objectContaining({
					trust_email_for_linking: true,
				}),
			}),
		);
	});

	it("loads stored OIDC email trust instead of resetting it on edit", async () => {
		configData.providers.forEach((provider) => {
			provider.configured = true;
		});
		configData.providers[2]!.trust_email_for_linking = true;
		const { user } = renderWithProviders(<OAuth />);
		await user.click(screen.getAllByRole("button", { name: "Edit" })[2]!);
		expect(
			screen.getByRole("switch", {
				name: "Trust Provider Email for Account Linking",
			}),
		).toHaveAttribute("aria-checked", "true");
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
