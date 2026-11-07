import { useIsMutating } from "@tanstack/react-query";
import { SsoExclusions } from "@/components/users/SsoExclusions";
import {
	LoginPolicy,
	PreferredSignIn,
	type LoginPreferenceValue,
} from "./oauth/PreferredSignIn";
import { OAuthReadError } from "./oauth/OAuthReadError";
import { OAuthProviderCard } from "./oauth/OAuthProviderCard";
/**
 * OAuth SSO Configuration Settings
 *
 * Configure OAuth SSO providers (Microsoft, Google, OIDC) for single sign-on.
 * Platform admin only.
 */

import { useState } from "react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
	Accordion,
	AccordionContent,
	AccordionItem,
	AccordionTrigger,
} from "@/components/ui/accordion";
import { toast } from "sonner";
import { Loader2, ExternalLink } from "lucide-react";
import {
	useOAuthConfigs,
	useUpdateOAuthLoginPreference,
	useUpdateMicrosoftConfig,
	useUpdateGoogleConfig,
	useUpdateOIDCConfig,
	useDeleteOAuthConfig,
	useTestOAuthConfig,
} from "@/services/oauth-config";

export function OAuth() {
	const policyPending =
		useIsMutating({
			mutationKey: ["put", "/api/settings/oauth/login-preference"],
		}) > 0;
	// Form state for each provider
	const [microsoftForm, setMicrosoftForm] = useState({
		client_id: "",
		client_secret: "",
		tenant_id: "common",
	});
	const [googleForm, setGoogleForm] = useState({
		client_id: "",
		client_secret: "",
	});
	const [oidcForm, setOidcForm] = useState({
		discovery_url: "",
		client_id: "",
		client_secret: "",
		display_name: "SSO",
	});
	const [loginPreferenceDraft, setLoginPreferenceDraft] =
		useState<LoginPreferenceValue | null>(null);

	// Load configurations
	const {
		data: configData,
		isLoading,
		isError,
		isFetching,
		refetch,
	} = useOAuthConfigs();

	// Mutations
	const updateMicrosoft = useUpdateMicrosoftConfig();
	const updateGoogle = useUpdateGoogleConfig();
	const updateOIDC = useUpdateOIDCConfig();
	const updateLoginPreference = useUpdateOAuthLoginPreference();
	const deleteConfig = useDeleteOAuthConfig();
	const testConfig = useTestOAuthConfig();

	if (isLoading) {
		return (
			<div
				role="status"
				aria-label="Loading SSO configuration"
				className="flex items-center justify-center py-12"
			>
				<Loader2 className="h-8 w-8 animate-spin motion-reduce:animate-none text-muted-foreground" />
			</div>
		);
	}

	const readError = isError ? (
		<OAuthReadError
			cached={!!configData}
			pending={isFetching}
			onRetry={() => {
				void refetch();
			}}
		/>
	) : null;
	if (!configData) return readError;

	const providers = configData?.providers || [];

	const microsoftConfig = providers.find((p) => p.provider === "microsoft");
	const googleConfig = providers.find((p) => p.provider === "google");
	const oidcConfig = providers.find((p) => p.provider === "oidc");
	const configuredProviders = providers.filter(
		(provider) => provider.configured,
	);
	const autoRedirectToSso =
		loginPreferenceDraft?.auto_redirect_to_sso ??
		configData?.login_preference.auto_redirect_to_sso ??
		false;
	const defaultSsoProvider = loginPreferenceDraft
		? loginPreferenceDraft.default_sso_provider
		: (configData?.login_preference.default_sso_provider ?? null);
	const passwordLoginEnabled =
		loginPreferenceDraft?.password_login_enabled ??
		configData?.login_preference.password_login_enabled ??
		true;
	const passkeyLoginEnabled =
		loginPreferenceDraft?.passkey_login_enabled ??
		configData?.login_preference.passkey_login_enabled ??
		true;

	const saveLoginPreference = async () => {
		if (updateLoginPreference.isPending || policyPending) return;
		try {
			// Built as a detached object so the payload shape stays explicit.
			const body = {
				...configData.login_preference,
				auto_redirect_to_sso: autoRedirectToSso,
				default_sso_provider: defaultSsoProvider ?? null,
				password_login_enabled: passwordLoginEnabled,
				passkey_login_enabled: passkeyLoginEnabled,
			};
			await updateLoginPreference.mutateAsync({ body });
			await refetch();
			setLoginPreferenceDraft(null);
			toast.success("Login preference updated");
		} catch (error) {
			toast.error("Failed to update login preference", {
				description:
					error instanceof Error ? error.message : "Unknown error",
			});
		}
	};

	return (
		<div className="space-y-6">
			{readError}
			<PreferredSignIn
				configuredProviders={configuredProviders}
				autoRedirectToSso={autoRedirectToSso}
				defaultSsoProvider={defaultSsoProvider}
				passwordLoginEnabled={passwordLoginEnabled}
				passkeyLoginEnabled={passkeyLoginEnabled}
				pending={updateLoginPreference.isPending || policyPending}
				failed={updateLoginPreference.isError}
				onChange={setLoginPreferenceDraft}
				onSave={() => {
					void saveLoginPreference();
				}}
			/>
			<LoginPolicy
				passwordLoginEnabled={passwordLoginEnabled}
				passkeyLoginEnabled={passkeyLoginEnabled}
				hasProviders={configuredProviders.length > 0}
				pending={updateLoginPreference.isPending || policyPending}
				failed={updateLoginPreference.isError}
				onChange={(policy) => {
					setLoginPreferenceDraft({
						auto_redirect_to_sso: autoRedirectToSso,
						default_sso_provider: defaultSsoProvider,
						...policy,
					});
				}}
				onSave={() => {
					void saveLoginPreference();
				}}
			/>

			<SsoExclusions />

			{/* Microsoft */}
			<OAuthProviderCard
				provider="microsoft"
				onEdit={() =>
					setMicrosoftForm({
						client_id: microsoftConfig?.client_id ?? "",
						client_secret: "",
						tenant_id: microsoftConfig?.tenant_id ?? "common",
					})
				}
				onCancel={() =>
					setMicrosoftForm({
						client_id: "",
						client_secret: "",
						tenant_id: "common",
					})
				}
				title="Microsoft Entra ID"
				description="Allow users to sign in with their Microsoft work, school, or personal accounts."
				configured={microsoftConfig?.configured || false}
				clientId={microsoftConfig?.client_id}
				clientSecretSet={microsoftConfig?.client_secret_set || false}
				extraFields={[
					{
						label: "Tenant ID",
						value: microsoftConfig?.tenant_id,
					},
				]}
				callbackUrl={`${window.location.origin}/auth/callback/microsoft`}
				onSave={async () => {
					await updateMicrosoft.mutateAsync({
						body: {
							...microsoftForm,
							client_secret: microsoftForm.client_secret || null,
							token_endpoint_auth_method:
								microsoftConfig?.token_endpoint_auth_method ??
								"client_secret_post",
							private_key_alg:
								microsoftConfig?.private_key_alg ?? "RS256",
							clear_private_key: false,
						},
					});
					await refetch();
					setMicrosoftForm({
						client_id: "",
						client_secret: "",
						tenant_id: "common",
					});
				}}
				onDelete={async () => {
					await deleteConfig.mutateAsync({
						params: { path: { provider: "microsoft" } },
					});
					await refetch();
					setMicrosoftForm({
						client_id: "",
						client_secret: "",
						tenant_id: "common",
					});
				}}
				onTest={async () => {
					const result = await testConfig.mutateAsync({
						params: { path: { provider: "microsoft" } },
					});
					return {
						success: result.success,
						message: result.message,
					};
				}}
			>
				<div className="space-y-4">
					<div className="space-y-2">
						<Label htmlFor="ms-client-id">
							Application (Client) ID
						</Label>
						<Input
							id="ms-client-id"
							placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
							value={microsoftForm.client_id}
							onChange={(e) =>
								setMicrosoftForm((prev) => ({
									...prev,
									client_id: e.target.value,
								}))
							}
						/>
					</div>
					<div className="space-y-2">
						<Label htmlFor="ms-client-secret">Client Secret</Label>
						<Input
							id="ms-client-secret"
							type="password"
							placeholder={
								microsoftConfig?.client_secret_set
									? "Enter new secret to change"
									: "Enter client secret"
							}
							value={microsoftForm.client_secret}
							onChange={(e) =>
								setMicrosoftForm((prev) => ({
									...prev,
									client_secret: e.target.value,
								}))
							}
						/>
					</div>
					<div className="space-y-2">
						<Label htmlFor="ms-tenant-id">Tenant ID</Label>
						<Input
							id="ms-tenant-id"
							placeholder="common"
							value={microsoftForm.tenant_id}
							onChange={(e) =>
								setMicrosoftForm((prev) => ({
									...prev,
									tenant_id: e.target.value,
								}))
							}
						/>
						<p className="text-xs text-muted-foreground">
							Use "common" for multi-tenant (any Microsoft
							account), "organizations" for work/school only, or a
							specific tenant ID.
						</p>
					</div>

					<Accordion type="single" collapsible className="w-full">
						<AccordionItem value="instructions">
							<AccordionTrigger className="text-sm">
								Setup Instructions
							</AccordionTrigger>
							<AccordionContent className="text-sm text-muted-foreground space-y-2">
								<ol className="list-decimal ml-4 space-y-1">
									<li>
										Go to{" "}
										<a
											href="https://portal.azure.com/#blade/Microsoft_AAD_RegisteredApps/ApplicationsListBlade"
											target="_blank"
											rel="noopener noreferrer"
											className="text-primary hover:underline inline-flex items-center gap-1"
										>
											Azure Portal App Registrations
											<ExternalLink className="h-3 w-3" />
										</a>
									</li>
									<li>
										Create a new registration or select an
										existing one
									</li>
									<li>
										Under "Authentication", add a Web
										platform redirect URI with the callback
										URL above
									</li>
									<li>
										Under "Certificates & secrets", create a
										new client secret
									</li>
									<li>
										Copy the Application ID and secret value
									</li>
								</ol>
								<p className="mt-2 font-medium">
									Required API Permissions:
								</p>
								<ul className="list-disc ml-4">
									<li>User.Read (delegated)</li>
									<li>email (delegated)</li>
									<li>openid (delegated)</li>
									<li>profile (delegated)</li>
								</ul>
							</AccordionContent>
						</AccordionItem>
					</Accordion>
				</div>
			</OAuthProviderCard>

			{/* Google */}
			<OAuthProviderCard
				provider="google"
				onEdit={() =>
					setGoogleForm({
						client_id: googleConfig?.client_id ?? "",
						client_secret: "",
					})
				}
				onCancel={() =>
					setGoogleForm({ client_id: "", client_secret: "" })
				}
				title="Google"
				description="Allow users to sign in with their Google accounts."
				configured={googleConfig?.configured || false}
				clientId={googleConfig?.client_id}
				clientSecretSet={googleConfig?.client_secret_set || false}
				callbackUrl={`${window.location.origin}/auth/callback/google`}
				onSave={async () => {
					await updateGoogle.mutateAsync({
						body: {
							...googleForm,
							client_secret: googleForm.client_secret || null,
							token_endpoint_auth_method:
								googleConfig?.token_endpoint_auth_method ??
								"client_secret_post",
							private_key_alg:
								googleConfig?.private_key_alg ?? "RS256",
							clear_private_key: false,
						},
					});
					await refetch();
					setGoogleForm({ client_id: "", client_secret: "" });
				}}
				onDelete={async () => {
					await deleteConfig.mutateAsync({
						params: { path: { provider: "google" } },
					});
					await refetch();
					setGoogleForm({ client_id: "", client_secret: "" });
				}}
				onTest={async () => {
					const result = await testConfig.mutateAsync({
						params: { path: { provider: "google" } },
					});
					return {
						success: result.success,
						message: result.message,
					};
				}}
			>
				<div className="space-y-4">
					<div className="space-y-2">
						<Label htmlFor="google-client-id">Client ID</Label>
						<Input
							id="google-client-id"
							placeholder="xxxxxxxxxxxx.apps.googleusercontent.com"
							value={googleForm.client_id}
							onChange={(e) =>
								setGoogleForm((prev) => ({
									...prev,
									client_id: e.target.value,
								}))
							}
						/>
					</div>
					<div className="space-y-2">
						<Label htmlFor="google-client-secret">
							Client Secret
						</Label>
						<Input
							id="google-client-secret"
							type="password"
							placeholder={
								googleConfig?.client_secret_set
									? "Enter new secret to change"
									: "Enter client secret"
							}
							value={googleForm.client_secret}
							onChange={(e) =>
								setGoogleForm((prev) => ({
									...prev,
									client_secret: e.target.value,
								}))
							}
						/>
					</div>

					<Accordion type="single" collapsible className="w-full">
						<AccordionItem value="instructions">
							<AccordionTrigger className="text-sm">
								Setup Instructions
							</AccordionTrigger>
							<AccordionContent className="text-sm text-muted-foreground space-y-2">
								<ol className="list-decimal ml-4 space-y-1">
									<li>
										Go to{" "}
										<a
											href="https://console.cloud.google.com/apis/credentials"
											target="_blank"
											rel="noopener noreferrer"
											className="text-primary hover:underline inline-flex items-center gap-1"
										>
											Google Cloud Console Credentials
											<ExternalLink className="h-3 w-3" />
										</a>
									</li>
									<li>
										Create an OAuth 2.0 Client ID (Web
										application type)
									</li>
									<li>
										Add the callback URL above as an
										authorized redirect URI
									</li>
									<li>
										Copy the Client ID and Client secret
									</li>
								</ol>
								<p className="mt-2 font-medium">
									OAuth Consent Screen:
								</p>
								<ul className="list-disc ml-4">
									<li>
										Set user type (Internal for G Suite,
										External for any Google account)
									</li>
									<li>Add scopes: email, profile, openid</li>
								</ul>
							</AccordionContent>
						</AccordionItem>
					</Accordion>
				</div>
			</OAuthProviderCard>

			{/* OIDC */}
			<OAuthProviderCard
				provider="oidc"
				onEdit={() =>
					setOidcForm({
						client_id: oidcConfig?.client_id ?? "",
						client_secret: "",
						discovery_url: oidcConfig?.discovery_url ?? "",
						display_name: oidcConfig?.display_name ?? "SSO",
					})
				}
				onCancel={() =>
					setOidcForm({
						client_id: "",
						client_secret: "",
						discovery_url: "",
						display_name: "SSO",
					})
				}
				title="OIDC Provider"
				description="Allow users to sign in with any OpenID Connect provider (Okta, Auth0, Keycloak, etc.)."
				configured={oidcConfig?.configured || false}
				clientId={oidcConfig?.client_id}
				clientSecretSet={oidcConfig?.client_secret_set || false}
				extraFields={[
					{
						label: "Discovery URL",
						value: oidcConfig?.discovery_url,
					},
					{
						label: "Button Label",
						value: oidcConfig?.display_name,
					},
				]}
				callbackUrl={`${window.location.origin}/auth/callback/oidc`}
				onSave={async () => {
					await updateOIDC.mutateAsync({
						body: {
							...oidcForm,
							client_secret: oidcForm.client_secret || null,
							token_endpoint_auth_method:
								oidcConfig?.token_endpoint_auth_method ??
								"client_secret_post",
							private_key_alg:
								oidcConfig?.private_key_alg ?? "RS256",
							clear_private_key: false,
						},
					});
					await refetch();
					setOidcForm({
						client_id: "",
						client_secret: "",
						discovery_url: "",
						display_name: "SSO",
					});
				}}
				onDelete={async () => {
					await deleteConfig.mutateAsync({
						params: { path: { provider: "oidc" } },
					});
					await refetch();
					setOidcForm({
						client_id: "",
						client_secret: "",
						discovery_url: "",
						display_name: "SSO",
					});
				}}
				onTest={async () => {
					const result = await testConfig.mutateAsync({
						params: { path: { provider: "oidc" } },
					});
					return {
						success: result.success,
						message: result.message,
					};
				}}
			>
				<div className="space-y-4">
					<div className="space-y-2">
						<Label htmlFor="oidc-discovery-url">
							Discovery URL
						</Label>
						<Input
							id="oidc-discovery-url"
							placeholder="https://provider.com/.well-known/openid-configuration"
							value={oidcForm.discovery_url}
							onChange={(e) =>
								setOidcForm((prev) => ({
									...prev,
									discovery_url: e.target.value,
								}))
							}
						/>
						<p className="text-xs text-muted-foreground">
							The OIDC discovery endpoint URL (must be HTTPS)
						</p>
					</div>
					<div className="space-y-2">
						<Label htmlFor="oidc-client-id">Client ID</Label>
						<Input
							id="oidc-client-id"
							placeholder="Enter client ID"
							value={oidcForm.client_id}
							onChange={(e) =>
								setOidcForm((prev) => ({
									...prev,
									client_id: e.target.value,
								}))
							}
						/>
					</div>
					<div className="space-y-2">
						<Label htmlFor="oidc-client-secret">
							Client Secret
						</Label>
						<Input
							id="oidc-client-secret"
							type="password"
							placeholder={
								oidcConfig?.client_secret_set
									? "Enter new secret to change"
									: "Enter client secret"
							}
							value={oidcForm.client_secret}
							onChange={(e) =>
								setOidcForm((prev) => ({
									...prev,
									client_secret: e.target.value,
								}))
							}
						/>
					</div>
					<div className="space-y-2">
						<Label htmlFor="oidc-display-name">Button Label</Label>
						<Input
							id="oidc-display-name"
							placeholder="SSO"
							value={oidcForm.display_name}
							onChange={(e) =>
								setOidcForm((prev) => ({
									...prev,
									display_name: e.target.value,
								}))
							}
						/>
						<p className="text-xs text-muted-foreground">
							Text shown on the login button (e.g., "Okta",
							"Auth0", "Company SSO")
						</p>
					</div>

					<Accordion type="single" collapsible className="w-full">
						<AccordionItem value="instructions">
							<AccordionTrigger className="text-sm">
								Setup Instructions
							</AccordionTrigger>
							<AccordionContent className="text-sm text-muted-foreground space-y-2">
								<p>
									In your OIDC provider, create a new
									application:
								</p>
								<ol className="list-decimal ml-4 space-y-1">
									<li>
										Set the application type to "Web" or
										"Regular Web Application"
									</li>
									<li>
										Add the callback URL above as a redirect
										URI
									</li>
									<li>
										Enable the scopes: openid, email,
										profile
									</li>
									<li>
										Copy the discovery URL, client ID, and
										client secret
									</li>
								</ol>
								<p className="mt-2 font-medium">
									Discovery URL Examples:
								</p>
								<ul className="list-disc ml-4 font-mono text-sm [overflow-wrap:anywhere]">
									<li>
										Okta:
										https://your-org.okta.com/.well-known/openid-configuration
									</li>
									<li>
										Auth0:
										https://your-tenant.auth0.com/.well-known/openid-configuration
									</li>
									<li>
										Keycloak:
										https://your-server/realms/your-realm/.well-known/openid-configuration
									</li>
								</ul>
							</AccordionContent>
						</AccordionItem>
					</Accordion>
				</div>
			</OAuthProviderCard>
		</div>
	);
}
