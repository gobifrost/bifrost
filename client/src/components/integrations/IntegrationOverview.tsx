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
	const utcDateStr = dateStr.endsWith("Z") || dateStr.includes("+") || dateStr.includes("-", 10) ? dateStr : `${dateStr}Z`;
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
}: IntegrationOverviewProps) {
	return (
		<div className="grid grid-cols-1 md:grid-cols-2 gap-4">
			{/* Configuration Defaults */}
			<Card>
				<CardHeader className="pb-3">
					<div>
						<CardTitle className="text-base">
							Configuration Defaults
						</CardTitle>
						<CardDescription>
							Default config values for new mappings
						</CardDescription>
					</div>
				</CardHeader>
				<CardContent>
					{/* Default Entity ID section */}
					<div className="mb-4">
						<div className="flex items-center justify-between text-sm">
							<div className="flex flex-col">
								<span className="text-muted-foreground">
									Default{" "}
									{integration.entity_id_name ||
										"Entity ID"}
								</span>
								<span className="text-xs text-muted-foreground/70">
									Used when org mapping is not set
								</span>
							</div>
							<div className="flex items-center gap-2">
								<span className="font-mono text-xs bg-muted px-2 py-0.5 rounded">
									{integration.default_entity_id || "\u2014"}
								</span>
								<Button
									variant="ghost"
									size="sm"
									className="h-6 w-6 p-0"
									onClick={onOpenDefaultsDialog}
									title="Edit default values"
								>
									<Pencil className="h-3 w-3" />
								</Button>
							</div>
						</div>
					</div>

					{integration.config_schema &&
					integration.config_schema.length > 0 ? (
						<div className="space-y-2">
							{integration.config_schema.map((field) => {
								const defaultValue =
									integration.config_defaults?.[
										field.key
									];
								return (
									<div
										key={field.key}
										className="flex items-center justify-between text-sm"
									>
										<span className="text-muted-foreground">
											{field.key}
											{field.required && (
												<span className="text-destructive ml-1">
													*
												</span>
											)}
										</span>
										<span className="font-mono text-xs bg-muted px-2 py-0.5 rounded">
											{defaultValue !== null &&
											defaultValue !== undefined
												? field.type === "secret"
													? "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022"
													: String(defaultValue)
												: "\u2014"}
										</span>
									</div>
								);
							})}
						</div>
					) : null}
				</CardContent>
			</Card>

			{/* Compact OAuth Status */}
			<Card className="hover:shadow-md transition-shadow">
				<CardHeader className="pb-3">
					<div className="flex items-center justify-between">
						<div>
							<CardTitle className="text-base">
								OAuth
							</CardTitle>
							<CardDescription>
								Connection status and authentication
							</CardDescription>
						</div>
						<div className="flex items-center gap-2">
							{oauthConfig && (
								<Badge
									variant="outline"
									className="text-xs"
								>
									{oauthConfig.oauth_flow_type}
								</Badge>
							)}
							{isOAuthConnected ? (
								<CheckCircle2 className="h-4 w-4 text-green-600" />
							) : oauthConfig?.status === "failed" ? (
								<XCircle className="h-4 w-4 text-red-600" />
							) : integration.has_oauth_config ? (
								<AlertCircle className="h-4 w-4 text-yellow-600" />
							) : null}
							{integration.has_oauth_config && (
								<DropdownMenu>
									<DropdownMenuTrigger asChild>
										<Button
											variant="ghost"
											size="icon"
											className="h-8 w-8"
										>
											<MoreVertical className="h-4 w-4" />
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
											onClick={onDeleteOAuthConfig}
											className="text-destructive focus:text-destructive"
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
								<div className="flex items-center gap-2 p-2 rounded bg-red-50 dark:bg-red-950 text-red-700 dark:text-red-300 text-sm">
									<AlertCircle className="h-4 w-4" />
									Token expired - reconnect required
								</div>
							)}
							{isOAuthExpiringSoon && !isOAuthExpired && (
								<div className="flex items-center gap-2 p-2 rounded bg-yellow-50 dark:bg-yellow-950 text-yellow-700 dark:text-yellow-300 text-sm">
									<Clock className="h-4 w-4" />
									Token expires soon - consider refreshing
								</div>
							)}

							{/* No refresh token warning - only show for authorization_code flow */}
							{isOAuthConnected &&
								oauthConfig &&
								oauthConfig.has_refresh_token === false &&
								canUseAuthCodeFlow && (
									<div className="flex items-center gap-2 p-2 rounded bg-yellow-50 dark:bg-yellow-950 text-yellow-700 dark:text-yellow-300 text-sm">
										<AlertCircle className="h-4 w-4" />
										No refresh token - manual
										reconnection required when token
										expires
									</div>
								)}

							{/* Connection status */}
							<div className="flex items-center justify-between">
								<span className="text-sm text-muted-foreground">
									Status
								</span>
								<span className="text-sm font-medium">
									{isOAuthConnected
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
								<div className="flex items-center justify-between">
									<span className="text-sm text-muted-foreground">
										Expires
									</span>
									<span className="text-sm font-mono">
										{formatDateTime(
											oauthConfig.expires_at,
										)}
									</span>
								</div>
							)}

							{/* Action buttons */}
							<div className="flex items-center gap-2 pt-1">
								{canUseAuthCodeFlow && (
									<Button
										variant={
											isOAuthConnected
												? "outline"
												: "default"
										}
										size="sm"
										className="flex-1"
										onClick={onOAuthConnect}
										disabled={isAuthorizePending}
									>
										{isAuthorizePending ? (
											<>
												<Loader2 className="mr-2 h-3 w-3 animate-spin" />
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
											className="flex-1"
											onClick={onOAuthRefresh}
											disabled={isRefreshPending}
										>
											{isRefreshPending ? (
												<>
													<Loader2 className="mr-2 h-3 w-3 animate-spin" />
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
											onClick={onOAuthRefresh}
											disabled={isRefreshPending}
										>
											{isRefreshPending ? (
												<>
													<Loader2 className="mr-2 h-3 w-3 animate-spin" />
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
							<p className="text-xs text-muted-foreground mt-1">
								Used when an organization isn't individually connected via its mapping.
							</p>
						</div>
					) : (
						<div className="text-center py-4">
							<LinkIcon className="h-8 w-8 text-muted-foreground mx-auto" />
							<p className="mt-2 text-sm text-muted-foreground">
								No OAuth configured
							</p>
							<Button
								variant="outline"
								size="sm"
								className="mt-3"
								onClick={onCreateOAuthConfig}
							>
								<Plus className="h-3 w-3 mr-2" />
								Configure
							</Button>
						</div>
					)}
				</CardContent>
			</Card>
		</div>
	);
}
