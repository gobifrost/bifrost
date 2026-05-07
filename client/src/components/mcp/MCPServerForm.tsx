/**
 * MCPServerForm — discovery-first new server form (mockup §3).
 *
 * Workflow:
 *   1. Admin enters display name + server URL.
 *   2. Clicks "Discover OAuth metadata" → backend fetches the
 *      ``/.well-known/oauth-*`` documents and returns the merged metadata.
 *   3. Discovered fields render read-only in a panel; the admin can flip a
 *      toggle to edit them manually if discovery returned wrong values.
 *   4. Save → POST /api/mcp-servers with the discovery payload.
 *
 * The redirect URL is Bifrost-managed (deterministic per deployment) and
 * shown read-only. The backend computes the canonical value; the frontend
 * just displays whatever the server returned on the public response.
 */

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { Loader2, Search, AlertCircle } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
	Form,
	FormControl,
	FormDescription,
	FormField,
	FormItem,
	FormLabel,
	FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { $api, apiClient } from "@/lib/api-client";
import { toast } from "sonner";

const formSchema = z.object({
	name: z.string().min(1, "Display name is required").max(255),
	server_url: z
		.string()
		.min(1, "Server URL is required")
		.url("Must be a valid URL"),
});

type FormValues = z.infer<typeof formSchema>;

type OAuthFlowType = "authorization_code" | "client_credentials";

interface DiscoveredMetadata {
	authorization_endpoint?: string;
	token_endpoint?: string;
	authorization_url?: string;
	token_url?: string;
	audience?: string;
	resource?: string;
	scopes_supported?: string[];
	scopes?: string[] | string;
	grant_types_supported?: string[];
	[key: string]: unknown;
}

function readMetadata(metadata: DiscoveredMetadata) {
	const authorization_url =
		metadata.authorization_endpoint ?? metadata.authorization_url ?? "";
	const token_url = metadata.token_endpoint ?? metadata.token_url ?? "";
	const audience = metadata.audience ?? metadata.resource ?? "";
	const scopesValue = metadata.scopes_supported ?? metadata.scopes;
	const scopes = Array.isArray(scopesValue)
		? scopesValue.join(" ")
		: typeof scopesValue === "string"
			? scopesValue
			: "";
	return { authorization_url, token_url, audience, scopes };
}

/**
 * Detect OAuth flow type from the discovery's ``grant_types_supported``.
 * If it contains ``client_credentials`` and not ``authorization_code``,
 * default to client_credentials (forcing example: halopsa-mcp). Otherwise
 * default to authorization_code (M365, etc.).
 */
function detectFlowFromMetadata(metadata: DiscoveredMetadata): OAuthFlowType {
	const grants = metadata.grant_types_supported ?? [];
	const hasCC = grants.includes("client_credentials");
	const hasAC = grants.includes("authorization_code");
	if (hasCC && !hasAC) return "client_credentials";
	return "authorization_code";
}

interface MCPServerFormProps {
	/** Called after a successful create. If omitted, navigates to the new server's detail page. */
	onSuccess?: (serverId: string) => void;
	/** Called when the user clicks Cancel. If omitted, navigates back to the list page. */
	onCancel?: () => void;
}

export function MCPServerForm({ onSuccess, onCancel }: MCPServerFormProps = {}) {
	const navigate = useNavigate();
	const queryClient = useQueryClient();
	const createServer = $api.useMutation("post", "/api/mcp-servers");

	const form = useForm<FormValues>({
		resolver: zodResolver(formSchema),
		defaultValues: { name: "", server_url: "" },
	});

	const [metadata, setMetadata] = useState<DiscoveredMetadata | null>(null);
	const [discoveryAttempted, setDiscoveryAttempted] = useState(false);
	const [discovering, setDiscovering] = useState(false);
	const [overrideMode, setOverrideMode] = useState(false);

	// Manual override values — start blank, populated from metadata when shown.
	const [manualAuthUrl, setManualAuthUrl] = useState("");
	const [manualTokenUrl, setManualTokenUrl] = useState("");
	const [manualAudience, setManualAudience] = useState("");
	const [manualScopes, setManualScopes] = useState("");

	// OAuth flow type — detected from discovery, admin can override.
	const [flowType, setFlowType] = useState<OAuthFlowType>("authorization_code");

	const handleDiscover = async () => {
		const url = form.getValues("server_url");
		if (!url) {
			form.setError("server_url", {
				message: "Server URL is required to discover OAuth metadata",
			});
			return;
		}

		setDiscovering(true);
		setDiscoveryAttempted(true);
		setOverrideMode(false);
		try {
			const { data, error } = await apiClient.POST(
				"/api/mcp-servers/discover",
				{ body: { server_url: url } },
			);

			if (error) {
				toast.error("Discovery failed — enter values manually");
				setMetadata(null);
				setOverrideMode(true);
				return;
			}

			const discoveredMetadata = data?.metadata as
				| DiscoveredMetadata
				| null
				| undefined;
			if (!discoveredMetadata) {
				toast.warning(
					"No OAuth metadata found — enter values manually",
				);
				setMetadata(null);
				setOverrideMode(true);
				return;
			}

			setMetadata(discoveredMetadata);
			const parsed = readMetadata(discoveredMetadata);
			setManualAuthUrl(parsed.authorization_url);
			setManualTokenUrl(parsed.token_url);
			setManualAudience(parsed.audience);
			setManualScopes(parsed.scopes);
			setFlowType(detectFlowFromMetadata(discoveredMetadata));
			toast.success("OAuth metadata discovered");
		} catch (err) {
			toast.error(
				err instanceof Error
					? err.message
					: "Discovery failed — enter values manually",
			);
			setMetadata(null);
			setOverrideMode(true);
		} finally {
			setDiscovering(false);
		}
	};

	const handleEnableOverride = () => {
		// Populate manual fields from current metadata if any, then flip to edit
		if (metadata) {
			const parsed = readMetadata(metadata);
			setManualAuthUrl(parsed.authorization_url);
			setManualTokenUrl(parsed.token_url);
			setManualAudience(parsed.audience);
			setManualScopes(parsed.scopes);
		}
		setOverrideMode(true);
	};

	const onSubmit = async (values: FormValues) => {
		// Build the discovery_metadata payload either from the discovered doc
		// or from manual overrides.
		let payload: DiscoveredMetadata | null = null;
		if (overrideMode) {
			payload = {
				authorization_endpoint: manualAuthUrl || undefined,
				token_endpoint: manualTokenUrl || undefined,
				audience: manualAudience || undefined,
				scopes_supported: manualScopes
					? manualScopes.split(/[\s,]+/).filter(Boolean)
					: undefined,
				_source: "manual",
			};
		} else if (metadata) {
			payload = metadata;
		}

		// Build the inline OAuth provider create payload from the
		// discovered/manual values. Only sent when we have a token_url —
		// otherwise the server is auth-less and no provider is created.
		const parsed = metadata ? readMetadata(metadata) : null;
		const tokenUrl = overrideMode
			? manualTokenUrl
			: (parsed?.token_url ?? "");
		const authUrl = overrideMode
			? manualAuthUrl
			: (parsed?.authorization_url ?? "");
		const audience = overrideMode
			? manualAudience
			: (parsed?.audience ?? "");
		const scopesStr = overrideMode
			? manualScopes
			: (parsed?.scopes ?? "");
		const scopes = scopesStr
			? scopesStr.split(/[\s,]+/).filter(Boolean)
			: [];

		// authorization_code requires authorization_url; if the admin chose
		// authorization_code but didn't supply one, fail fast with a toast.
		if (
			tokenUrl &&
			flowType === "authorization_code" &&
			!authUrl
		) {
			toast.error(
				"Authorization URL is required for authorization_code flow",
			);
			return;
		}

		const oauthProviderPayload =
			tokenUrl
				? {
						oauth_flow_type: flowType,
						token_url: tokenUrl,
						authorization_url:
							flowType === "authorization_code"
								? authUrl
								: null,
						scopes,
						audience: audience || null,
					}
				: undefined;

		try {
			const result = await createServer.mutateAsync({
				body: {
					name: values.name,
					server_url: values.server_url,
					discovery_metadata: payload as Record<string, unknown> | null,
					oauth_provider: oauthProviderPayload as never,
					is_active: true,
				},
			});

			toast.success("MCP server created");
			queryClient.invalidateQueries({
				queryKey: ["get", "/api/mcp-servers"],
			});
			if (onSuccess) {
				onSuccess(result.id);
			} else {
				navigate(`/mcp-servers/${result.id}`);
			}
		} catch (err) {
			toast.error(
				err instanceof Error
					? err.message
					: "Failed to create MCP server",
			);
		}
	};

	const showDiscoveryPanel = discoveryAttempted && (metadata || overrideMode);
	const parsed = metadata ? readMetadata(metadata) : null;

	return (
		<Form {...form}>
			<form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
				<FormField
					control={form.control}
					name="name"
					render={({ field }) => (
						<FormItem>
							<FormLabel>Display name</FormLabel>
							<FormControl>
								<Input
									placeholder="Microsoft 365 Copilot"
									{...field}
								/>
							</FormControl>
							<FormMessage />
						</FormItem>
					)}
				/>

				<FormField
					control={form.control}
					name="server_url"
					render={({ field }) => (
						<FormItem>
							<FormLabel>Server URL</FormLabel>
							<FormControl>
								<Input
									type="url"
									placeholder="https://example.com/mcp"
									className="font-mono text-sm"
									{...field}
								/>
							</FormControl>
							<FormDescription>
								MCP endpoint. Streamable HTTP only.
							</FormDescription>
							<FormMessage />
						</FormItem>
					)}
				/>

				<div className="flex items-center gap-3">
					<Button
						type="button"
						variant="outline"
						onClick={handleDiscover}
						disabled={discovering}
					>
						{discovering ? (
							<Loader2 className="h-4 w-4 mr-2 animate-spin" />
						) : (
							<Search className="h-4 w-4 mr-2" />
						)}
						Discover OAuth metadata
					</Button>
					<span className="text-xs text-muted-foreground">
						Fetches{" "}
						<code className="font-mono">
							/.well-known/oauth-authorization-server
						</code>{" "}
						and{" "}
						<code className="font-mono">
							/.well-known/oauth-protected-resource
						</code>
					</span>
				</div>

				{discoveryAttempted && !metadata && !overrideMode && (
					<Alert>
						<AlertCircle className="h-4 w-4" />
						<AlertDescription>
							No OAuth metadata was returned. The backend will
							store the server with no discovery snapshot — you
							can wire OAuth manually after save.
						</AlertDescription>
					</Alert>
				)}

				{showDiscoveryPanel && (
					<div className="rounded-md border bg-muted/30 p-4 space-y-3">
						<div className="flex items-center justify-between">
							<div className="text-sm font-semibold">
								OAuth metadata{" "}
								{metadata && !overrideMode ? (
									<Badge
										variant="default"
										className="ml-1 bg-green-600 hover:bg-green-700"
									>
										Discovered
									</Badge>
								) : (
									<Badge variant="secondary" className="ml-1">
										Manual
									</Badge>
								)}
							</div>
							{!overrideMode && metadata && (
								<Button
									type="button"
									variant="link"
									size="sm"
									onClick={handleEnableOverride}
									className="text-xs"
								>
									Override discovered values manually
								</Button>
							)}
						</div>

						<div className="space-y-2">
							<label className="text-xs font-medium text-muted-foreground">
								OAuth flow
							</label>
							<select
								value={flowType}
								onChange={(e) =>
									setFlowType(e.target.value as OAuthFlowType)
								}
								className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
							>
								<option value="authorization_code">
									Authorization Code — user signs in at the vendor
								</option>
								<option value="client_credentials">
									Client Credentials — server-to-server, no user sign-in
								</option>
							</select>
							<p className="text-xs text-muted-foreground">
								{flowType === "client_credentials"
									? "Each org enters a client_id + secret on its connection. Bifrost exchanges those credentials for an access token directly with the vendor — no browser popup, no user sign-in."
									: "Admin clicks Connect and signs in at the vendor in a popup. Bifrost stores the resulting delegated access token."}
							</p>
						</div>

						{flowType === "authorization_code" && (
							<div className="space-y-2">
								<label className="text-xs font-medium text-muted-foreground">
									Authorization URL
								</label>
								<Input
									readOnly={!overrideMode}
									value={
										overrideMode
											? manualAuthUrl
											: parsed?.authorization_url ?? ""
									}
									onChange={(e) => setManualAuthUrl(e.target.value)}
									className="font-mono text-xs"
								/>
							</div>
						)}

						<div className="space-y-2">
							<label className="text-xs font-medium text-muted-foreground">
								Token URL
							</label>
							<Input
								readOnly={!overrideMode}
								value={
									overrideMode
										? manualTokenUrl
										: parsed?.token_url ?? ""
								}
								onChange={(e) =>
									setManualTokenUrl(e.target.value)
								}
								className="font-mono text-xs"
							/>
						</div>

						<div className="space-y-2">
							<label className="text-xs font-medium text-muted-foreground">
								Audience / resource indicator
							</label>
							<Input
								readOnly={!overrideMode}
								value={
									overrideMode
										? manualAudience
										: parsed?.audience ?? ""
								}
								onChange={(e) =>
									setManualAudience(e.target.value)
								}
								className="font-mono text-xs"
							/>
						</div>

						<div className="space-y-2">
							<label className="text-xs font-medium text-muted-foreground">
								Scopes
							</label>
							<Input
								readOnly={!overrideMode}
								value={
									overrideMode
										? manualScopes
										: parsed?.scopes ?? ""
								}
								onChange={(e) =>
									setManualScopes(e.target.value)
								}
								className="font-mono text-xs"
							/>
						</div>

						{/* Redirect URL is only meaningful for the authorization_code flow.
						    client_credentials is server-to-server; the vendor never redirects
						    a browser anywhere, so showing this just confuses admins. */}
						{flowType === "authorization_code" && (
						<div className="space-y-2">
							<label className="text-xs font-medium text-muted-foreground flex items-center gap-2">
								Redirect URL{" "}
								<Badge
									variant="default"
									className="bg-blue-600 hover:bg-blue-700"
								>
									Bifrost-managed
								</Badge>
							</label>
							<Input
								readOnly
								value={`${window.location.origin}/api/mcp/oauth/callback`}
								className="font-mono text-xs"
							/>
							<p className="text-xs text-muted-foreground">
								Register this exact URL in the vendor's OAuth
								app. Same value across all connections of this
								server.
							</p>
						</div>
						)}
					</div>
				)}

				<div className="flex gap-2 pt-2">
					<Button type="submit" disabled={createServer.isPending}>
						{createServer.isPending ? (
							<>
								<Loader2 className="h-4 w-4 mr-2 animate-spin" />
								Creating...
							</>
						) : (
							"Create Server"
						)}
					</Button>
					<Button
						type="button"
						variant="outline"
						onClick={() => {
							if (onCancel) {
								onCancel();
							} else {
								navigate("/mcp-servers");
							}
						}}
					>
						Cancel
					</Button>
				</div>
			</form>
		</Form>
	);
}
