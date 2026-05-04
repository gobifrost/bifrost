/**
 * OAuthProviderEditor — shared OAuth provider configuration form.
 *
 * Extracted from the inline editor in `CreateOAuthConnectionDialog` so the
 * MCP connection edit page (and any other OAuth-using flow) can render the
 * same fields with identical validation and identical "leave secret blank
 * to keep existing" semantics.
 *
 * Owns its own state. Parent renders `<OAuthProviderEditor />` and submits
 * via the `onSubmit` callback (passed plain values; the parent decides
 * whether to wrap in a dialog footer or a page-level Save button).
 */

import { useEffect, useMemo, useState } from "react";
import { Copy, Check, Info } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "sonner";

export type OAuthFlowType = "authorization_code" | "client_credentials";

export interface OAuthProviderData {
	oauth_flow_type: OAuthFlowType;
	client_id: string;
	client_secret: string;
	authorization_url: string | null;
	token_url: string;
	scopes: string;
	audience: string | null;
}

export interface OAuthProviderEditorProps {
	flowType: OAuthFlowType;
	initialValues?: Partial<OAuthProviderData>;
	onSubmit: (data: OAuthProviderData) => void;
	/** Read-only redirect URI shown for authorization_code flows */
	redirectUri?: string;
	/** Edit mode — secret is left blank to preserve existing */
	isEditMode?: boolean;
	/** Allow the editor to switch between flow types (defaults to true) */
	flowTypeSwitchable?: boolean;
	/** Render the form fields only — caller controls submission via a form id */
	formId?: string;
	/** Disable all inputs (e.g. while parent mutation pending) */
	disabled?: boolean;
}

const DEFAULT_VALUES: OAuthProviderData = {
	oauth_flow_type: "authorization_code",
	client_id: "",
	client_secret: "",
	authorization_url: "",
	token_url: "",
	scopes: "",
	audience: "",
};

/**
 * Standalone OAuth provider editor used by both the integrations dialog
 * (existing) and the MCP connection edit page (new).
 */
export function OAuthProviderEditor({
	flowType,
	initialValues,
	onSubmit,
	redirectUri,
	isEditMode = false,
	flowTypeSwitchable = true,
	formId,
	disabled = false,
}: OAuthProviderEditorProps) {
	const computedInitial = useMemo<OAuthProviderData>(
		() => ({
			...DEFAULT_VALUES,
			...initialValues,
			oauth_flow_type: initialValues?.oauth_flow_type ?? flowType,
		}),
		[initialValues, flowType],
	);

	const [data, setData] = useState<OAuthProviderData>(computedInitial);
	const [copiedRedirect, setCopiedRedirect] = useState(false);

	// Reset internal state when initialValues changes (e.g. dialog reopened
	// with a different connection in edit mode). Wrapped in setTimeout to
	// avoid the react-hooks/set-state-in-effect rule (cascading renders) —
	// matches the existing pattern in CreateOAuthConnectionDialog.tsx.
	useEffect(() => {
		const t = setTimeout(() => setData(computedInitial), 0);
		return () => clearTimeout(t);
	}, [computedInitial]);

	const handleCopyRedirectUri = () => {
		if (!redirectUri) return;
		navigator.clipboard.writeText(redirectUri);
		setCopiedRedirect(true);
		toast.success("Redirect URI copied to clipboard");
		setTimeout(() => setCopiedRedirect(false), 2000);
	};

	const handleSubmit = (e: React.FormEvent) => {
		e.preventDefault();
		onSubmit({
			...data,
			authorization_url:
				data.oauth_flow_type === "authorization_code"
					? data.authorization_url
					: null,
		});
	};

	const isAuthCode = data.oauth_flow_type === "authorization_code";

	return (
		<form
			id={formId}
			onSubmit={handleSubmit}
			className="space-y-4"
			data-testid="oauth-provider-editor"
		>
			{redirectUri && isAuthCode && (
				<Alert>
					<Info className="h-4 w-4" />
					<AlertDescription>
						<div className="space-y-2">
							<p className="font-semibold text-sm">
								Your Redirect URI:
							</p>
							<div className="flex items-center gap-2">
								<code className="flex-1 px-2 py-1 bg-muted rounded text-xs break-all">
									{redirectUri}
								</code>
								<Button
									type="button"
									variant="outline"
									size="sm"
									onClick={handleCopyRedirectUri}
								>
									{copiedRedirect ? (
										<Check className="h-4 w-4" />
									) : (
										<Copy className="h-4 w-4" />
									)}
								</Button>
							</div>
							<p className="text-xs text-muted-foreground">
								Copy this and add it to your OAuth app's allowed
								redirect URIs before continuing
							</p>
						</div>
					</AlertDescription>
				</Alert>
			)}

			{flowTypeSwitchable && (
				<div className="space-y-2">
					<Label htmlFor="oauth_flow_type">OAuth Flow Type *</Label>
					<Select
						value={data.oauth_flow_type}
						onValueChange={(value) =>
							setData({
								...data,
								oauth_flow_type: value as OAuthFlowType,
							})
						}
						disabled={disabled}
					>
						<SelectTrigger>
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="authorization_code">
								Authorization Code (Interactive)
							</SelectItem>
							<SelectItem value="client_credentials">
								Client Credentials (Service-to-Service)
							</SelectItem>
						</SelectContent>
					</Select>
					<p className="text-xs text-muted-foreground">
						{isAuthCode
							? "Requires user authorization. Use for delegated permissions."
							: "No user authorization required. Use for application permissions."}
					</p>
				</div>
			)}

			<div className="grid grid-cols-2 gap-4">
				<div className="space-y-2">
					<Label htmlFor="client_id">Client ID *</Label>
					<Input
						id="client_id"
						value={data.client_id}
						onChange={(e) =>
							setData({ ...data, client_id: e.target.value })
						}
						placeholder="abc123..."
						required
						className="font-mono"
						disabled={disabled}
					/>
				</div>

				<div className="space-y-2">
					<Label htmlFor="client_secret">
						Client Secret{" "}
						{data.oauth_flow_type === "client_credentials" && "*"}
					</Label>
					<Input
						id="client_secret"
						type="password"
						value={data.client_secret}
						onChange={(e) =>
							setData({ ...data, client_secret: e.target.value })
						}
						placeholder={
							isEditMode
								? "Leave empty to keep existing..."
								: data.oauth_flow_type === "client_credentials"
									? "Required for client credentials flow..."
									: "Optional for PKCE flow..."
						}
						required={
							data.oauth_flow_type === "client_credentials" &&
							!isEditMode
						}
						disabled={disabled}
					/>
					<p className="text-xs text-muted-foreground">
						{isEditMode
							? "Leave empty to keep the existing secret, or enter a new one to update"
							: data.oauth_flow_type === "client_credentials"
								? "Required: Client credentials flow requires a client secret"
								: "Optional: Leave empty for PKCE (Proof Key for Code Exchange) flow"}
					</p>
				</div>
			</div>

			{isAuthCode && (
				<div className="space-y-2">
					<Label htmlFor="authorization_url">Authorization URL *</Label>
					<Input
						id="authorization_url"
						value={data.authorization_url ?? ""}
						onChange={(e) =>
							setData({
								...data,
								authorization_url: e.target.value,
							})
						}
						placeholder="https://provider.com/oauth/authorize"
						pattern="https://.*"
						required
						className="font-mono text-xs"
						disabled={disabled}
					/>
				</div>
			)}

			<div className="space-y-2">
				<Label htmlFor="token_url">Token URL *</Label>
				<Input
					id="token_url"
					value={data.token_url}
					onChange={(e) =>
						setData({ ...data, token_url: e.target.value })
					}
					placeholder="https://provider.com/oauth/token"
					pattern="https://.*"
					required
					className="font-mono text-xs"
					disabled={disabled}
				/>
			</div>

			<div className="space-y-2">
				<Label htmlFor="audience">Audience</Label>
				<Input
					id="audience"
					value={data.audience ?? ""}
					onChange={(e) =>
						setData({ ...data, audience: e.target.value })
					}
					placeholder="https://api.example.com"
					className="font-mono text-xs"
					disabled={disabled}
				/>
				<p className="text-xs text-muted-foreground">
					Target API identifier sent with token requests. Required by
					some providers (e.g., Pax8, Auth0).
				</p>
			</div>

			<div className="space-y-2">
				<Label htmlFor="scopes">Scopes (comma or space separated)</Label>
				<Textarea
					id="scopes"
					value={data.scopes}
					onChange={(e) =>
						setData({ ...data, scopes: e.target.value })
					}
					placeholder="read,write or https://graph.microsoft.com/.default"
					rows={2}
					className="font-mono text-xs"
					disabled={disabled}
				/>
				<p className="text-xs text-muted-foreground">
					OAuth permissions to request. Leave empty for default scopes.
				</p>
			</div>
		</form>
	);
}
