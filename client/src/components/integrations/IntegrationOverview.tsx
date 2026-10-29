import {
	Loader2,
	Link as LinkIcon,
	CheckCircle2,
	XCircle,
	Plus,
	AlertCircle,
	Clock,
	RotateCw,
	Pencil,
	MoreVertical,
	Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { getStatusLabel } from "@/lib/client-types";

// Format datetime with relative time for dates within 7 days
const formatDateTime = (dateStr?: string | null) => {
	if (!dateStr) return "Never";

	// Parse the date - backend sends UTC timestamps without 'Z' suffix
	// Add 'Z' to explicitly mark it as UTC, then JavaScript will convert to local time
	const utcDateStr =
		dateStr.endsWith("Z") ||
		dateStr.includes("+") ||
		dateStr.includes("-", 10)
			? dateStr
			: `${dateStr}Z`;
	const date = new Date(utcDateStr);
	const now = new Date();
	const diffMs = date.getTime() - now.getTime();
	const diffMins = Math.floor(Math.abs(diffMs) / 60000);
	const diffHours = Math.floor(Math.abs(diffMs) / 3600000);
	const diffDays = Math.floor(Math.abs(diffMs) / 86400000);

	// For dates within 7 days, show relative time
	if (diffDays < 7) {
		// Past dates (negative diffMs) - show "X ago"
		if (diffMs < 0) {
			if (diffMins < 60) {
				return `${diffMins} minute${diffMins !== 1 ? "s" : ""} ago`;
			} else if (diffHours < 24) {
				return `${diffHours} hour${diffHours !== 1 ? "s" : ""} ago`;
			} else {
				return `${diffDays} day${diffDays !== 1 ? "s" : ""} ago`;
			}
		}

		// Future dates (positive diffMs) - show "in X"
		if (diffMs > 0) {
			if (diffMins < 60) {
				return `in ${diffMins} minute${diffMins !== 1 ? "s" : ""}`;
			} else if (diffHours < 24) {
				return `in ${diffHours} hour${diffHours !== 1 ? "s" : ""}`;
			} else {
				return `in ${diffDays} day${diffDays !== 1 ? "s" : ""}`;
			}
		}

		// Exactly now
		return "just now";
	}

	// Absolute dates for far past/future (converts to user's local timezone)
	return date.toLocaleString(undefined, {
		month: "short",
		day: "numeric",
		year: "numeric",
		hour: "numeric",
		minute: "2-digit",
	});
};

interface OAuthConfig {
	status: string;
	expires_at?: string | null;
	oauth_flow_type?: string;
	has_refresh_token?: boolean;
}

interface ConfigSchemaField {
	key: string;
	type: string;
	required?: boolean;
}

interface IntegrationData {
	name: string;
	has_oauth_config: boolean;
	oauth_config?: OAuthConfig | null;
	config_schema?: ConfigSchemaField[] | null;
	config_defaults?: Record<string, unknown> | null;
	default_entity_id?: string | null;
	entity_id_name?: string | null;
}

export interface IntegrationOverviewProps {
	integration: IntegrationData;
	oauthConfig: OAuthConfig | undefined | null;
	isOAuthConnected: boolean;
	isOAuthExpired: boolean | "" | null | undefined;
	isOAuthExpiringSoon: boolean | "" | null | undefined;
	canUseAuthCodeFlow: boolean | undefined;
	onOpenDefaultsDialog: () => void;
	onOAuthConnect: () => void;
	onOAuthRefresh: () => void;
	onEditOAuthConfig: () => void;
	onDeleteOAuthConfig: () => void;
	onCreateOAuthConfig: () => void;
	isAuthorizePending: boolean;
	isRefreshPending: boolean;
	canManageDefinitions: boolean;
}

export function IntegrationOverview({
	integration,
	oauthConfig,
	isOAuthConnected,
	isOAuthExpired,
	isOAuthExpiringSoon,
	canUseAuthCodeFlow,
	onOpenDefaultsDialog,
	onOAuthConnect,
	onOAuthRefresh,
	onEditOAuthConfig,
	onDeleteOAuthConfig,
	onCreateOAuthConfig,
	isAuthorizePending,
	isRefreshPending,
	canManageDefinitions,
}: IntegrationOverviewProps) {
	return (
		<div className="grid min-w-0 grid-cols-1 gap-4 xl:grid-cols-2">
			{/* Configuration Defaults */}
			<Card>
				<CardHeader className="pb-3">
					<div className="flex min-w-0 flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
						<div className="min-w-0">
							<CardTitle className="text-base">
								Configuration Defaults
							</CardTitle>
							<CardDescription>
								Default config values for new mappings
							</CardDescription>
						</div>
						{canManageDefinitions && (
							<Button
								variant="ghost"
								size="sm"
								className="min-h-11 self-start"
								onClick={onOpenDefaultsDialog}
								aria-label="Configure default values"
							>
								<Pencil className="h-4 w-4 shrink-0" />{" "}
								Configure
							</Button>
						)}
					</div>
				</CardHeader>
				<CardContent>
					{/* Default Entity ID section */}
					<div className="space-y-2">
						<div className="flex min-w-0 flex-wrap items-center justify-between gap-2 text-sm">
							<div className="flex min-w-0 flex-col gap-0.5">
								<span className="text-muted-foreground">
									Default{" "}
									{integration.entity_id_name || "Entity ID"}
								</span>
								<span className="text-xs text-muted-foreground">
									Used when org mapping is not set
								</span>
							</div>
							<span className="max-w-full font-mono text-xs bg-muted px-2 py-1 rounded-[var(--bf-radius-control)] [overflow-wrap:anywhere]">
								{integration.default_entity_id || "\u2014"}
							</span>
						</div>

						{integration.config_schema &&
						integration.config_schema.length > 0 ? (
							<div className="space-y-1.5 border-t border-border/60 pt-2">
								{integration.config_schema.map((field) => {
									const defaultValue =
										integration.config_defaults?.[
											field.key
										];
									return (
										<div
											key={field.key}
											className="flex min-w-0 flex-wrap items-center justify-between gap-2 text-sm"
										>
											<span className="min-w-0 text-muted-foreground [overflow-wrap:anywhere]">
												{field.key}
												{field.required && (
													<span className="text-destructive ml-1">
														*
													</span>
												)}
											</span>
											<span className="max-w-full font-mono text-xs bg-muted px-2 py-1 rounded-[var(--bf-radius-control)] [overflow-wrap:anywhere]">
												{defaultValue !== null &&
												defaultValue !== undefined
													? field.type === "secret"
														? "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022"
														: typeof defaultValue ===
															  "object"
															? JSON.stringify(
																	defaultValue,
																)
															: String(
																	defaultValue,
																)
													: "\u2014"}
											</span>
										</div>
									);
								})}
							</div>
						) : null}
					</div>
				</CardContent>
			</Card>

			{/* Compact OAuth Status */}
			<Card className="min-w-0">
				<CardHeader className="pb-3">
					<div className="flex min-w-0 flex-wrap items-start justify-between gap-3">
						<div>
							<CardTitle className="text-base">OAuth</CardTitle>
							<CardDescription>
								Connection status and authentication
							</CardDescription>
						</div>
						<div className="flex min-w-0 flex-wrap items-center gap-2">
							{oauthConfig && (
								<Badge
									variant="outline"
									className="h-auto min-h-5 max-w-full whitespace-normal [overflow-wrap:anywhere] text-xs"
								>
									{oauthConfig.oauth_flow_type}
								</Badge>
							)}
							{isOAuthExpired ? (
								<XCircle className="h-4 w-4 text-destructive" />
							) : isOAuthConnected ? (
								<CheckCircle2 className="h-4 w-4 text-[var(--bf-success)]" />
							) : oauthConfig?.status === "failed" ? (
								<XCircle className="h-4 w-4 text-destructive" />
							) : integration.has_oauth_config ? (
								<AlertCircle className="h-4 w-4 text-[var(--bf-warning)]" />
							) : null}
							{canManageDefinitions &&
								integration.has_oauth_config && (
									<DropdownMenu>
										<DropdownMenuTrigger asChild>
											<Button
												variant="ghost"
												size="icon"
												className="min-h-11 min-w-11"
												aria-label="OAuth configuration actions"
											>
												<MoreVertical className="h-4 w-4 shrink-0" />
											</Button>
										</DropdownMenuTrigger>
										<DropdownMenuContent align="end">
											<DropdownMenuItem
												onClick={onEditOAuthConfig}
											>
												<Pencil className="h-4 w-4 mr-2" />
												Edit Configuration
											</DropdownMenuItem>
											<DropdownMenuItem
												variant="destructive"
												onClick={onDeleteOAuthConfig}
												className="focus:text-destructive"
											>
												<Trash2 className="h-4 w-4 mr-2" />
												Delete Configuration
											</DropdownMenuItem>
										</DropdownMenuContent>
									</DropdownMenu>
								)}
						</div>
					</div>
				</CardHeader>
				<CardContent>
					{integration.has_oauth_config ? (
						<div className="space-y-3">
							{/* Expiration warnings */}
							{isOAuthExpired && (
								<div className="flex min-w-0 flex-wrap items-center gap-2 p-2 rounded-md bg-destructive/10 text-destructive text-sm">
									<AlertCircle className="h-4 w-4 shrink-0" />
									Token expired - reconnect required
								</div>
							)}
							{isOAuthExpiringSoon && !isOAuthExpired && (
								<div className="flex min-w-0 flex-wrap items-center gap-2 p-2 rounded-md bg-[var(--bf-warning-soft)] text-[var(--bf-warning)] text-sm">
									<Clock className="h-4 w-4 shrink-0" />
									Token expires soon - consider refreshing
								</div>
							)}

							{/* No refresh token warning - only show for authorization_code flow */}
							{isOAuthConnected &&
								oauthConfig &&
								oauthConfig.has_refresh_token === false &&
								canUseAuthCodeFlow && (
									<div className="flex min-w-0 flex-wrap items-center gap-2 p-2 rounded-md bg-[var(--bf-warning-soft)] text-[var(--bf-warning)] text-sm">
										<AlertCircle className="h-4 w-4 shrink-0" />
										No refresh token - manual reconnection
										required when token expires
									</div>
								)}

							{/* Connection status */}
							<div className="flex min-w-0 flex-wrap items-start justify-between gap-3">
								<span className="text-sm text-muted-foreground">
									Status
								</span>
								<span className="text-sm font-medium">
									{isOAuthExpired
										? "Expired"
										: isOAuthConnected
											? "Connected"
											: oauthConfig?.status === "failed"
												? "Failed"
												: oauthConfig
													? getStatusLabel(
															oauthConfig.status,
														)
													: "Not Connected"}
								</span>
							</div>

							{oauthConfig?.expires_at && !isOAuthExpired && (
								<div className="flex min-w-0 flex-wrap items-start justify-between gap-3">
									<span className="text-sm text-muted-foreground">
										Expires
									</span>
									<span className="text-sm font-mono">
										{formatDateTime(oauthConfig.expires_at)}
									</span>
								</div>
							)}

							{/* Action buttons */}
							{canManageDefinitions && (
								<div className="flex min-w-0 flex-wrap items-center gap-2 pt-1">
									{canUseAuthCodeFlow && (
										<Button
											variant={
												isOAuthConnected
													? "outline"
													: "default"
											}
											size="sm"
											className="min-h-11 flex-1"
											onClick={onOAuthConnect}
											disabled={isAuthorizePending}
										>
											{isAuthorizePending ? (
												<>
													<Loader2 className="mr-2 h-3 w-3 animate-spin motion-reduce:animate-none" />
													Connecting...
												</>
											) : isOAuthConnected ? (
												"Reconnect default"
											) : (
												"Connect default"
											)}
										</Button>
									)}
									{/* For client_credentials flow when not connected, show Get Token button */}
									{!canUseAuthCodeFlow &&
										!isOAuthConnected &&
										oauthConfig && (
											<Button
												variant="default"
												size="sm"
												className="min-h-11 flex-1"
												onClick={onOAuthRefresh}
												disabled={isRefreshPending}
											>
												{isRefreshPending ? (
													<>
														<Loader2 className="mr-2 h-3 w-3 animate-spin motion-reduce:animate-none" />
														Getting Token...
													</>
												) : oauthConfig?.status ===
												  "failed" ? (
													"Retry"
												) : (
													"Get Token"
												)}
											</Button>
										)}
									{isOAuthConnected &&
										oauthConfig?.expires_at && (
											<Button
												variant="outline"
												size="sm"
												className="min-h-11"
												onClick={onOAuthRefresh}
												disabled={isRefreshPending}
											>
												{isRefreshPending ? (
													<>
														<Loader2 className="mr-2 h-3 w-3 animate-spin motion-reduce:animate-none" />
														Refreshing...
													</>
												) : (
													<>
														<RotateCw className="mr-2 h-3 w-3" />
														Refresh default token
													</>
												)}
											</Button>
										)}
								</div>
							)}
							{canManageDefinitions && (
								<p className="text-xs text-muted-foreground mt-1">
									Used when an organization isn't individually
									connected via its mapping.
								</p>
							)}
						</div>
					) : (
						<div className="flex min-w-0 flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
							<div className="flex min-w-0 items-center gap-3">
								<LinkIcon className="h-5 w-5 shrink-0 text-muted-foreground" />
								<div className="min-w-0">
									<p className="text-sm font-medium">
										No OAuth configured
									</p>
									<p className="text-sm text-muted-foreground">
										Add OAuth settings when this integration
										needs default authentication.
									</p>
								</div>
							</div>
							{canManageDefinitions && (
								<Button
									variant="outline"
									size="sm"
									className="min-h-11 self-start sm:self-auto"
									onClick={onCreateOAuthConfig}
								>
									<Plus className="h-3 w-3 mr-2" />
									Configure
								</Button>
							)}
						</div>
					)}
				</CardContent>
			</Card>
		</div>
	);
}
