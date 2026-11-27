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

import { useEffect, useMemo, useRef, useState } from "react";
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
import {
	Tooltip,
	TooltipContent,
	TooltipProvider,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import { copyToClipboard } from "@/lib/clipboard";
import { toast } from "sonner";

export type OAuthFlowType =
	| "authorization_code"
	| "client_credentials"
	| "workload_identity";

export interface OAuthProviderData {
	oauth_flow_type: OAuthFlowType;
	client_id: string;
	client_secret: string;
	authorization_url: string | null;
	token_url: string;
	scopes: string;
	audience: string | null;
	tenant_id: string | null;
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
	/** Integration-only: host runtime supplies the provider identity. */
	allowWorkloadIdentity?: boolean;
}

const DEFAULT_VALUES: OAuthProviderData = {
	oauth_flow_type: "authorization_code",
	client_id: "",
	client_secret: "",
	authorization_url: "",
	token_url: "",
	scopes: "",
	audience: "",
	tenant_id: "",
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
	allowWorkloadIdentity = false,
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
	const [copyError, setCopyError] = useState<string | null>(null);
	const copyResetTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

	const [previousInitial, setPreviousInitial] = useState(computedInitial);
	if (previousInitial !== computedInitial) {
		setPreviousInitial(computedInitial);
		setData(computedInitial);
	}
	const [previousUri, setPreviousUri] = useState(redirectUri);
	if (previousUri !== redirectUri) {
		setPreviousUri(redirectUri);
		setCopiedRedirect(false);
		setCopyError(null);
	}
	const copyGeneration = useRef(0);
	useEffect(() => () => {
		copyGeneration.current += 1;
		if (copyResetTimerRef.current) clearTimeout(copyResetTimerRef.current);
	}, [redirectUri]);

	const handleCopyRedirectUri = async () => {
		if (!redirectUri) return;
		const generation = ++copyGeneration.current;
		if (copyResetTimerRef.current) clearTimeout(copyResetTimerRef.current);
		setCopiedRedirect(false);
		setCopyError(null);
		const copied = await copyToClipboard(redirectUri);
		if (generation !== copyGeneration.current) return;
		if (!copied) {
			setCopiedRedirect(false);
			setCopyError("Could not copy the redirect URI. Copy it manually.");
			toast.error("Could not copy redirect URI");
			return;
		}

		setCopiedRedirect(true);
		toast.success("Redirect URI copied to clipboard");
		if (copyResetTimerRef.current) {
			clearTimeout(copyResetTimerRef.current);
		}
		copyResetTimerRef.current = setTimeout(() => {
			setCopiedRedirect(false);
			copyResetTimerRef.current = null;
		}, 2000);
	};

	const handleSubmit = (e: React.FormEvent) => {
		e.preventDefault();
		onSubmit({
			...data,
			authorization_url:
				data.oauth_flow_type === "authorization_code"
					? data.authorization_url
					: null,
			client_secret:
				data.oauth_flow_type === "workload_identity" ? "" : data.client_secret,
			token_url:
				data.oauth_flow_type === "workload_identity" ? "" : data.token_url,
		});
	};

	const isAuthCode = data.oauth_flow_type === "authorization_code";
	const isWorkloadIdentity = data.oauth_flow_type === "workload_identity";
	const workloadIdentityHelp =
		"Authentication is handled by your workflow’s provider SDK using a configured runtime identity. Configure identity trust and permissions externally. Bifrost does not obtain or refresh tokens in this mode.";

	return (
		<form
			id={formId}
			onSubmit={handleSubmit}
			className="space-y-5"
			data-testid="oauth-provider-editor"
		>
			{redirectUri && isAuthCode && (
				<Alert className="border-[var(--bf-info)]/20 bg-[var(--bf-info-soft)]/60 text-foreground">
					<Info className="h-4 w-4 text-[var(--bf-info)]" />
					<AlertDescription>
						<div className="space-y-3">
							<p className="text-sm font-semibold leading-6">
								Your Redirect URI:
							</p>
							<div className="flex items-start gap-2">
								<code className="min-w-0 flex-1 rounded-[var(--bf-radius-control)] border border-border/70 bg-background px-2.5 py-2 text-xs leading-5 [overflow-wrap:anywhere]">
									{redirectUri}
								</code>
								<Button
									type="button"
									variant="outline"
									size="icon-lg"
									onClick={handleCopyRedirectUri}
									className="shrink-0"
									aria-label={
										copiedRedirect
											? "Redirect URI copied"
											: "Copy redirect URI"
									}
								>
									{copiedRedirect ? (
										<Check className="h-4 w-4" />
									) : (
										<Copy className="h-4 w-4" />
									)}
								</Button>
							</div>
							{copyError ? (
								<Alert variant="destructive" className="py-2">
									<AlertDescription>{copyError}</AlertDescription>
								</Alert>
							) : null}
							<p className="text-xs leading-5 text-muted-foreground">
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
						<SelectTrigger className="h-11 w-full rounded-[var(--bf-radius-control)]">
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="authorization_code">
								Authorization Code (Interactive)
							</SelectItem>
							<SelectItem value="client_credentials">
								Client Credentials (Service-to-Service)
							</SelectItem>
							{allowWorkloadIdentity && (
								<SelectItem value="workload_identity">
									Workload Identity
								</SelectItem>
							)}
						</SelectContent>
					</Select>
					<p className="text-xs leading-5 text-muted-foreground">
						{isWorkloadIdentity
							? "The workflow provider SDK uses the Bifrost host runtime identity."
							: isAuthCode
							? "Requires user authorization. Use for delegated permissions."
							: "No user authorization required. Use for application permissions."}
					</p>
				</div>
			)}

			{isWorkloadIdentity && (
				<Alert className="border-[var(--bf-info)]/20 bg-[var(--bf-info-soft)]/60 text-foreground">
					<Info className="h-4 w-4 text-[var(--bf-info)]" />
					<AlertDescription className="flex items-start gap-2">
						<span>SDK Managed</span>
						<TooltipProvider>
							<Tooltip>
								<TooltipTrigger asChild>
									<Button
										type="button"
										variant="ghost"
										size="icon"
										className="size-6 shrink-0"
										aria-label={workloadIdentityHelp}
									>
										<Info className="size-4" />
									</Button>
								</TooltipTrigger>
								<TooltipContent>{workloadIdentityHelp}</TooltipContent>
							</Tooltip>
						</TooltipProvider>
					</AlertDescription>
				</Alert>
			)}
			{isWorkloadIdentity && isEditMode && (
				<Alert variant="destructive">
					<AlertDescription>
						Switching to Workload Identity removes this connection’s stored
						OAuth credentials. Workflows must use their provider SDK.
					</AlertDescription>
				</Alert>
			)}

			<div className="grid gap-4 md:grid-cols-2">
				<div className="space-y-2">
					<Label htmlFor="client_id">
						Client ID {isWorkloadIdentity ? "(optional)" : "*"}
					</Label>
					<Input
						id="client_id"
						value={data.client_id}
						onChange={(e) =>
							setData({ ...data, client_id: e.target.value })
						}
						placeholder="abc123..."
						required={!isWorkloadIdentity}
						className="h-11 font-mono text-xs"
						disabled={disabled}
					/>
				</div>

				{!isWorkloadIdentity && <div className="space-y-2">
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
						className="h-11"
					/>
					<p className="text-xs leading-5 text-muted-foreground">
						{isEditMode
							? "Leave empty to keep the existing secret, or enter a new one to update"
							: data.oauth_flow_type === "client_credentials"
								? "Required: Client credentials flow requires a client secret"
								: "Optional: Leave empty for PKCE (Proof Key for Code Exchange) flow"}
					</p>
				</div>}
			</div>

			{isWorkloadIdentity && (
				<div className="space-y-2">
					<Label htmlFor="tenant_id">Tenant ID</Label>
					<Input
						id="tenant_id"
						value={data.tenant_id ?? ""}
						onChange={(e) => setData({ ...data, tenant_id: e.target.value })}
						placeholder="tenant-id"
						className="h-11 font-mono text-xs"
						disabled={disabled}
					/>
				</div>
			)}

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
						className="h-11 font-mono text-xs"
						disabled={disabled}
					/>
				</div>
			)}

			{!isWorkloadIdentity && (
				<>
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
							className="h-11 font-mono text-xs"
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
							className="h-11 font-mono text-xs"
							disabled={disabled}
						/>
						<p className="text-xs leading-5 text-muted-foreground">
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
						<p className="text-xs leading-5 text-muted-foreground">
							OAuth permissions to request. Leave empty for default scopes.
						</p>
					</div>
				</>
			)}
		</form>
	);
}
