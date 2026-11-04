import {
	Card,
	CardHeader,
	CardTitle,
	CardDescription,
	CardContent,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Loader2, Lock } from "lucide-react";
import type {
	OAuthProvider,
	OAuthProviderConfig,
} from "@/services/oauth-config";

export type LoginPreferenceValue = Required<
	Pick<
		import("@/services/oauth-config").OAuthLoginPreference,
		| "auto_redirect_to_sso"
		| "default_sso_provider"
		| "password_login_enabled"
		| "passkey_login_enabled"
	>
>;

export function PreferredSignIn({
	configuredProviders,
	autoRedirectToSso,
	defaultSsoProvider,
	passwordLoginEnabled,
	passkeyLoginEnabled,
	pending,
	failed,
	onChange,
	onSave,
}: {
	configuredProviders: OAuthProviderConfig[];
	autoRedirectToSso: boolean;
	defaultSsoProvider: OAuthProvider | null;
	passwordLoginEnabled: boolean;
	passkeyLoginEnabled: boolean;
	pending: boolean;
	failed: boolean;
	onChange: (value: LoginPreferenceValue) => void;
	onSave: () => void;
}) {
	return (
		<Card>
			<CardHeader>
				<CardTitle>Preferred sign-in</CardTitle>
				<CardDescription>
					Optionally send users to one configured provider before
					showing the full sign-in screen. Going Back or cancelling
					shows all available sign-in options.
				</CardDescription>
			</CardHeader>
			<CardContent className="space-y-5">
				<div className="flex min-h-11 items-start justify-between gap-4">
					<div className="min-w-0 space-y-1">
						<Label
							htmlFor="preferred-sso-redirect"
							className="min-h-11 cursor-pointer text-base"
						>
							Prefer SSO on login
						</Label>
						<p className="text-sm text-muted-foreground">
							Try the selected provider once before presenting the
							standard login screen.
						</p>
					</div>
					<Switch
						id="preferred-sso-redirect"
						checked={autoRedirectToSso}
						disabled={pending || configuredProviders.length === 0}
						onCheckedChange={(checked) => {
							onChange({
								auto_redirect_to_sso: checked,
								default_sso_provider:
									defaultSsoProvider ??
									(checked
										? (configuredProviders[0]?.provider ??
											null)
										: null),
								password_login_enabled: passwordLoginEnabled,
								passkey_login_enabled: passkeyLoginEnabled,
							});
						}}
					/>
				</div>

				<div className="space-y-2">
					<Label htmlFor="preferred-sso-provider">
						Preferred provider
					</Label>
					<Select
						value={defaultSsoProvider ?? ""}
						onValueChange={(value: OAuthProvider) =>
							onChange({
								auto_redirect_to_sso: autoRedirectToSso,
								default_sso_provider: value,
								password_login_enabled: passwordLoginEnabled,
								passkey_login_enabled: passkeyLoginEnabled,
							})
						}
						disabled={pending || configuredProviders.length === 0}
					>
						<SelectTrigger
							id="preferred-sso-provider"
							className="data-[size=default]:h-auto min-h-11 w-full [&_[data-slot=select-value]]:whitespace-normal [&_[data-slot=select-value]]:text-left [&_[data-slot=select-value]]:[overflow-wrap:anywhere]"
						>
							<SelectValue placeholder="Select a configured provider" />
						</SelectTrigger>
						<SelectContent>
							{configuredProviders.map((provider) => (
								<SelectItem
									key={provider.provider}
									value={provider.provider}
								>
									{provider.provider === "microsoft"
										? "Microsoft Entra ID"
										: provider.provider === "google"
											? "Google"
											: provider.display_name ||
												"OIDC Provider"}
								</SelectItem>
							))}
						</SelectContent>
					</Select>
					{configuredProviders.length === 0 && (
						<p className="text-sm text-muted-foreground">
							Configure a provider below before enabling preferred
							sign-in.
						</p>
					)}
				</div>

				{failed && (
					<p role="alert" className="text-sm text-destructive">
						Could not save preferred sign-in. Your selection is
						still here. Try again.
					</p>
				)}
				<Button
					onClick={onSave}
					className="min-h-11 w-full sm:w-auto"
					disabled={
						pending || (autoRedirectToSso && !defaultSsoProvider)
					}
				>
					{pending && (
						<Loader2 className="h-4 w-4 mr-2 animate-spin motion-reduce:animate-none" />
					)}
					Save preference
				</Button>
			</CardContent>
		</Card>
	);
}

export function LoginPolicy({
	passwordLoginEnabled,
	passkeyLoginEnabled,
	hasProviders,
	pending,
	failed,
	onChange,
	onSave,
}: {
	passwordLoginEnabled: boolean;
	passkeyLoginEnabled: boolean;
	hasProviders: boolean;
	pending: boolean;
	failed: boolean;
	onChange: (value: {
		password_login_enabled: boolean;
		passkey_login_enabled: boolean;
	}) => void;
	onSave: () => void;
}) {
	return (
		<Card>
			<CardHeader>
				<CardTitle>Login Policy</CardTitle>
				<CardDescription>
					Restrict sign-in methods for accounts without an exclusion
					by turning off a sign-in method below.
				</CardDescription>
			</CardHeader>
			<CardContent className="space-y-5">
				<p className="flex items-start gap-2 rounded-md border px-3 py-2 text-sm text-muted-foreground">
					<Lock className="mt-0.5 h-4 w-4 shrink-0" />
					At least one active platform admin must remain excluded for
					recovery when the provider is unreachable.
				</p>
				<div className="flex min-h-11 items-start justify-between gap-4">
					<div className="min-w-0 space-y-1">
						<Label
							htmlFor="login-policy-password"
							className="min-h-11 cursor-pointer text-base"
						>
							Password Sign-In
						</Label>
						<p className="text-sm text-muted-foreground">
							Allow accounts without an exclusion to sign in with
							email and password.
						</p>
					</div>
					<Switch
						id="login-policy-password"
						checked={passwordLoginEnabled}
						disabled={pending || !hasProviders}
						onCheckedChange={(checked) => {
							onChange({
								password_login_enabled: checked,
								passkey_login_enabled: passkeyLoginEnabled,
							});
						}}
					/>
				</div>

				<div className="flex min-h-11 items-start justify-between gap-4">
					<div className="min-w-0 space-y-1">
						<Label
							htmlFor="login-policy-passkey"
							className="min-h-11 cursor-pointer text-base"
						>
							Passkey Sign-In
						</Label>
						<p className="text-sm text-muted-foreground">
							Allow accounts without an exclusion to sign in with
							a passkey.
						</p>
					</div>
					<Switch
						id="login-policy-passkey"
						checked={passkeyLoginEnabled}
						disabled={pending || !hasProviders}
						onCheckedChange={(checked) => {
							onChange({
								password_login_enabled: passwordLoginEnabled,
								passkey_login_enabled: checked,
							});
						}}
					/>
				</div>

				{!hasProviders && (
					<p className="text-sm text-muted-foreground">
						Configure a provider below before restricting password
						or passkey sign-in.
					</p>
				)}
				{failed && (
					<p role="alert" className="text-sm text-destructive">
						Could not save login policy. Your selection is still
						here. Try again.
					</p>
				)}
				<Button
					onClick={onSave}
					className="min-h-11 w-full sm:w-auto"
					disabled={pending || !hasProviders}
				>
					{pending && (
						<Loader2 className="h-4 w-4 mr-2 animate-spin motion-reduce:animate-none" />
					)}
					Save Login Policy
				</Button>
			</CardContent>
		</Card>
	);
}
