/**
 * OAuth SSO Configuration Settings
 *
 * Configure OAuth SSO providers (Microsoft, Google, OIDC) for single sign-on.
 * Platform admin only.
 */

import { useState } from "react";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import {
	Accordion,
	AccordionContent,
	AccordionItem,
	AccordionTrigger,
} from "@/components/ui/accordion";
import { Badge } from "@/components/ui/badge";
import { toast } from "sonner";
import {
	Loader2,
	Shield,
	CheckCircle2,
	AlertCircle,
	Trash2,
	ExternalLink,
	Copy,
	Key,
} from "lucide-react";
import {
	useOAuthConfigs,
	useUpdateMicrosoftConfig,
	useUpdateGoogleConfig,
	useUpdateOIDCConfig,
	useDeleteOAuthConfig,
	useTestOAuthConfig,
} from "@/services/oauth-config";

type OAuthProvider = "microsoft" | "google" | "oidc";

interface ProviderCardProps {
	provider: OAuthProvider;
	title: string;
	description: string;
	configured: boolean;
	clientId?: string | null;
	clientSecretSet: boolean;
	extraFields?: { label: string; value?: string | null }[];
	callbackUrl: string;
	onSave: (data: Record<string, string>) => Promise<void>;
	onDelete: () => Promise<void>;
	onTest: () => Promise<{ success: boolean; message: string }>;
	children: React.ReactNode;
}

function ProviderCard({
	provider: _provider,
	title,
	description,
	configured,
	clientId,
	clientSecretSet,
	extraFields,
	callbackUrl,
	onSave,
	onDelete,
	onTest,
	children,
}: ProviderCardProps) {
	const [isEditing, setIsEditing] = useState(!configured);
	const [saving, setSaving] = useState(false);
	const [testing, setTesting] = useState(false);
	const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
	const [testResult, setTestResult] = useState<{
		success: boolean;
		message: string;
	} | null>(null);
	const [formData, setFormData] = useState<Record<string, string>>({});

	const handleCopyCallback = () => {
		navigator.clipboard.writeText(callbackUrl);
		toast.success("Callback URL copied to clipboard");
	};

	const handleTest = async () => {
		setTesting(true);
		setTestResult(null);
		try {
			const result = await onTest();
			setTestResult(result);
			if (result.success) {
				toast.success("Connection test passed", {
					description: result.message,
				});
			} else {
				toast.error("Connection test failed", {
					description: result.message,
				});
			}
		} catch (error) {
			const message =
				error instanceof Error ? error.message : "Unknown error";
			setTestResult({ success: false, message });
			toast.error("Test failed", { description: message });
		} finally {
			setTesting(false);
		}
	};

	const handleSave = async () => {
		setSaving(true);
		try {
			await onSave(formData);
			toast.success(`${title} configuration saved`);
			setIsEditing(false);
			setFormData({});
			setTestResult(null);
		} catch (error) {
			toast.error("Failed to save configuration", {
				description:
					error instanceof Error ? error.message : "Unknown error",
			});
		} finally {
			setSaving(false);
		}
	};

	const handleDelete = async () => {
		setSaving(true);
		setShowDeleteConfirm(false);
		try {
			await onDelete();
			toast.success(`${title} configuration removed`);
			setIsEditing(true);
			setFormData({});
			setTestResult(null);
		} catch (error) {
			toast.error("Failed to remove configuration", {
				description:
					error instanceof Error ? error.message : "Unknown error",
			});
		} finally {
			setSaving(false);
		}
	};

	return (
		<>
			<Card>
				<CardHeader>
					<div className="flex items-center justify-between">
						<div className="flex items-center gap-2">
							<Shield className="h-5 w-5" />
							<CardTitle className="text-lg">{title}</CardTitle>
							{configured && (
								<Badge
									variant="outline"
									className="bg-green-50 text-green-700 border-green-200 dark:bg-green-950/20 dark:text-green-400 dark:border-green-800"
								>
									<CheckCircle2 className="h-3 w-3 mr-1" />
									Configured
								</Badge>
							)}
						</div>
						{configured && (
							<Button
								variant="ghost"
								size="sm"
								onClick={() => setShowDeleteConfirm(true)}
								className="text-destructive hover:text-destructive"
							>
								<Trash2 className="h-4 w-4 mr-1" />
								Remove
							</Button>
						)}
					</div>
					<CardDescription>{description}</CardDescription>
				</CardHeader>
				<CardContent className="space-y-4">
					{/* Callback URL */}
					<div className="rounded-lg bg-muted/50 p-3 ring-1 ring-foreground/5">
						<div className="flex items-center justify-between">
							<div>
								<p className="text-sm font-medium">
									Callback URL
								</p>
								<p className="text-xs text-muted-foreground mt-1 font-mono break-all">
									{callbackUrl}
								</p>
							</div>
							<Button
								variant="ghost"
								size="sm"
								onClick={handleCopyCallback}
							>
								<Copy className="h-4 w-4" />
							</Button>
						</div>
					</div>

					{/* Current Configuration (when configured and not editing) */}
					{configured && !isEditing && (
						<div className="space-y-3 rounded-lg p-4 ring-1 ring-foreground/5">
							<div className="grid gap-2">
								<div className="flex items-center justify-between text-sm">
									<span className="text-muted-foreground">
										Client ID
									</span>
									<span className="font-mono">
										{clientId || "Not set"}
									</span>
								</div>
								<div className="flex items-center justify-between text-sm">
									<span className="text-muted-foreground">
										Client Secret
									</span>
									<span>
										{clientSecretSet ? (
											<Badge variant="secondary">
												<Key className="h-3 w-3 mr-1" />
												Saved
											</Badge>
										) : (
											"Not set"
										)}
									</span>
								</div>
								{extraFields?.map((field) => (
									<div
										key={field.label}
										className="flex items-center justify-between text-sm"
									>
										<span className="text-muted-foreground">
											{field.label}
										</span>
										<span className="font-mono">
											{field.value || "Not set"}
										</span>
									</div>
								))}
							</div>
							<div className="flex gap-2 pt-2">
								<Button
									variant="outline"
									size="sm"
									onClick={() => setIsEditing(true)}
								>
									Edit
								</Button>
								<Button
									variant="secondary"
									size="sm"
									onClick={handleTest}
									disabled={testing}
								>
									{testing ? (
										<>
											<Loader2 className="h-4 w-4 mr-2 animate-spin" />
											Testing...
										</>
									) : testResult?.success ? (
										<>
											<CheckCircle2 className="h-4 w-4 mr-2 text-green-600" />
											Connected
										</>
									) : testResult?.success === false ? (
										<>
											<AlertCircle className="h-4 w-4 mr-2 text-destructive" />
											Failed
										</>
									) : (
										"Test Connection"
									)}
								</Button>
							</div>
							{testResult && !testResult.success && (
								<p className="text-sm text-destructive mt-2">
									{testResult.message}
								</p>
							)}
						</div>
					)}

					{/* Edit Form */}
					{isEditing && (
						<div className="space-y-4">
							{children}
							<div className="flex gap-2 pt-2">
								{configured && (
									<Button
										variant="outline"
										onClick={() => {
											setIsEditing(false);
											setFormData({});
											setTestResult(null);
										}}
									>
										Cancel
									</Button>
								)}
								<Button onClick={handleSave} disabled={saving}>
									{saving ? (
										<>
											<Loader2 className="h-4 w-4 mr-2 animate-spin" />
											Saving...
										</>
									) : (
										"Save Configuration"
									)}
								</Button>
							</div>
						</div>
					)}
				</CardContent>
			</Card>

			{/* Delete Confirmation Dialog */}
			<Dialog
				open={showDeleteConfirm}
				onOpenChange={setShowDeleteConfirm}
			>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>Remove {title} Configuration</DialogTitle>
						<DialogDescription>
							Are you sure you want to remove {title} SSO? Users
							will no longer be able to sign in with {title} until
							reconfigured.
						</DialogDescription>
					</DialogHeader>
					<DialogFooter>
						<Button
							variant="outline"
							onClick={() => setShowDeleteConfirm(false)}
							disabled={saving}
						>
							Cancel
						</Button>
						<Button
							variant="destructive"
							onClick={handleDelete}
							disabled={saving}
						>
							{saving ? (
								<>
									<Loader2 className="h-4 w-4 mr-2 animate-spin" />
									Removing...
								</>
							) : (
								"Remove Configuration"
							)}
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>
		</>
	);
}

export function OAuth() {
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

	// Load configurations
	const { data: configData, isLoading, refetch } = useOAuthConfigs();

	// Mutations
	const updateMicrosoft = useUpdateMicrosoftConfig();
	const updateGoogle = useUpdateGoogleConfig();
	const updateOIDC = useUpdateOIDCConfig();
	const deleteConfig = useDeleteOAuthConfig();
	const testConfig = useTestOAuthConfig();

	if (isLoading) {
		return (
			<div className="flex items-center justify-center py-12">
				<Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
			</div>
		);
	}

	const providers = configData?.providers || [];

	const microsoftConfig = providers.find((p) => p.provider === "microsoft");
	const googleConfig = providers.find((p) => p.provider === "google");
	const oidcConfig = providers.find((p) => p.provider === "oidc");

	return (
		<div className="space-y-6">
			{/* Microsoft */}
			<ProviderCard
				provider="microsoft"
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
						body: microsoftForm,
					});
					await refetch();
				}}
				onDelete={async () => {
					await deleteConfig.mutateAsync({
						params: { path: { provider: "microsoft" } },
					});
					await refetch();
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
			</ProviderCard>

			{/* Google */}
			<ProviderCard
				provider="google"
				title="Google"
				description="Allow users to sign in with their Google accounts."
				configured={googleConfig?.configured || false}
				clientId={googleConfig?.client_id}
				clientSecretSet={googleConfig?.client_secret_set || false}
				callbackUrl={`${window.location.origin}/auth/callback/google`}
				onSave={async () => {
					await updateGoogle.mutateAsync({
						body: googleForm,
					});
					await refetch();
				}}
				onDelete={async () => {
					await deleteConfig.mutateAsync({
						params: { path: { provider: "google" } },
					});
					await refetch();
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
			</ProviderCard>

			{/* OIDC */}
			<ProviderCard
				provider="oidc"
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
						body: oidcForm,
					});
					await refetch();
				}}
				onDelete={async () => {
					await deleteConfig.mutateAsync({
						params: { path: { provider: "oidc" } },
					});
					await refetch();
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
								<ul className="list-disc ml-4 font-mono text-xs">
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
			</ProviderCard>
		</div>
	);
}
