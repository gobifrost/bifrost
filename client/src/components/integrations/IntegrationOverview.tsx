import type { ReactNode } from "react";
import {
	Eye,
	Loader2,
	MoreVertical,
	Pencil,
	Plus,
	RotateCw,
	Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
	Popover,
	PopoverContent,
	PopoverTrigger,
} from "@/components/ui/popover";
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

type OAuthTone = "success" | "warning" | "destructive" | "outline";

/** The default OAuth connection's one-line status: a badge plus detail. */
function oauthStatus({
	integration,
	oauthConfig,
	isOAuthConnected,
	isOAuthExpired,
	isOAuthExpiringSoon,
	canUseAuthCodeFlow,
}: Pick<
	IntegrationOverviewProps,
	| "integration"
	| "oauthConfig"
	| "isOAuthConnected"
	| "isOAuthExpired"
	| "isOAuthExpiringSoon"
	| "canUseAuthCodeFlow"
>): { label: string; tone: OAuthTone; detail?: string } {
	if (!integration.has_oauth_config) {
		return { label: "Not Configured", tone: "outline" };
	}
	if (isOAuthExpired) {
		return {
			label: "Expired",
			tone: "destructive",
			detail: "Token expired - reconnect required",
		};
	}
	if (isOAuthConnected) {
		const expires = oauthConfig?.expires_at
			? `Expires ${formatDateTime(oauthConfig.expires_at)}`
			: undefined;
		if (isOAuthExpiringSoon) {
			return {
				label: "Expires Soon",
				tone: "warning",
				detail: `${expires} - consider refreshing`,
			};
		}
		if (oauthConfig?.has_refresh_token === false && canUseAuthCodeFlow) {
			return {
				label: "Connected",
				tone: "warning",
				detail: "No refresh token - reconnect manually when it expires",
			};
		}
		return { label: "Connected", tone: "success", detail: expires };
	}
	if (oauthConfig?.status === "failed") {
		return { label: "Failed", tone: "destructive" };
	}
	return {
		label: oauthConfig
			? getStatusLabel(oauthConfig.status)
			: "Not Connected",
		tone: "warning",
	};
}

function formatDefaultValue(field: ConfigSchemaField, value: unknown) {
	if (value === null || value === undefined) return "\u2014";
	if (field.type === "secret")
		return "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022";
	return typeof value === "object" ? JSON.stringify(value) : String(value);
}

/** One labelled row of the overview strip. */
function OverviewRow({
	title,
	hint,
	summary,
	actions,
}: {
	title: string;
	hint: string;
	summary: ReactNode;
	actions?: ReactNode;
}) {
	return (
		<section
			aria-label={title}
			className="flex min-w-0 flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:gap-4"
		>
			<div className="min-w-0 sm:w-48 sm:shrink-0">
				<h2 className="text-sm font-medium">{title}</h2>
				<p className="text-xs text-muted-foreground">{hint}</p>
			</div>
			<div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-3 gap-y-1 text-sm">
				{summary}
			</div>
			{actions && (
				<div className="flex flex-wrap items-center gap-2 sm:shrink-0">
					{actions}
				</div>
			)}
		</section>
	);
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
	const status = oauthStatus({
		integration,
		oauthConfig,
		isOAuthConnected,
		isOAuthExpired,
		isOAuthExpiringSoon,
		canUseAuthCodeFlow,
	});
	const schema = integration.config_schema ?? [];
	const setDefaults = schema.filter((field) => {
		const value = integration.config_defaults?.[field.key];
		return value !== null && value !== undefined;
	}).length;
	const entityLabel = integration.entity_id_name || "Entity ID";

	const oauthActions = canManageDefinitions && (
		<>
			{!integration.has_oauth_config && (
				<Button
					variant="outline"
					size="sm"
					className="min-h-11 sm:min-h-8"
					onClick={onCreateOAuthConfig}
				>
					<Plus className="h-3 w-3" />
					Set Up OAuth
				</Button>
			)}
			{integration.has_oauth_config && canUseAuthCodeFlow && (
				<Button
					variant={
						isOAuthConnected && !isOAuthExpired
							? "outline"
							: "default"
					}
					size="sm"
					className="min-h-11 sm:min-h-8"
					onClick={onOAuthConnect}
					disabled={isAuthorizePending}
				>
					{isAuthorizePending ? (
						<>
							<Loader2 className="h-3 w-3 animate-spin motion-reduce:animate-none" />
							Connecting...
						</>
					) : isOAuthConnected ? (
						"Reconnect default"
					) : (
						"Connect default"
					)}
				</Button>
			)}
			{integration.has_oauth_config &&
				!canUseAuthCodeFlow &&
				!isOAuthConnected &&
				oauthConfig && (
					<Button
						size="sm"
						className="min-h-11 sm:min-h-8"
						onClick={onOAuthRefresh}
						disabled={isRefreshPending}
					>
						{isRefreshPending ? (
							<>
								<Loader2 className="h-3 w-3 animate-spin motion-reduce:animate-none" />
								Getting Token...
							</>
						) : oauthConfig.status === "failed" ? (
							"Retry"
						) : (
							"Get Token"
						)}
					</Button>
				)}
			{isOAuthConnected && oauthConfig?.expires_at && (
				<Button
					variant="outline"
					size="sm"
					className="min-h-11 sm:min-h-8"
					onClick={onOAuthRefresh}
					disabled={isRefreshPending}
					aria-label="Refresh default token"
					title="Refresh default token"
				>
					{isRefreshPending ? (
						<Loader2 className="h-3 w-3 animate-spin motion-reduce:animate-none" />
					) : (
						<RotateCw className="h-3 w-3" />
					)}
					Refresh
				</Button>
			)}
			{integration.has_oauth_config && (
				<DropdownMenu>
					<DropdownMenuTrigger asChild>
						<Button
							variant="ghost"
							size="icon"
							className="h-11 w-11 sm:h-8 sm:w-8"
							aria-label="OAuth configuration actions"
						>
							<MoreVertical className="h-4 w-4 shrink-0" />
						</Button>
					</DropdownMenuTrigger>
					<DropdownMenuContent align="end">
						<DropdownMenuItem onClick={onEditOAuthConfig}>
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
		</>
	);

	return (
		<div className="min-w-0 divide-y divide-border/60 rounded-xl border bg-card text-card-foreground">
			<OverviewRow
				title="Default OAuth"
				hint="Fallback for organizations without their own connection."
				summary={
					<>
						<Badge variant={status.tone}>{status.label}</Badge>
						{oauthConfig?.oauth_flow_type && (
							<span className="text-muted-foreground">
								{oauthConfig.oauth_flow_type}
							</span>
						)}
						{status.detail && (
							<span
								className={
									status.tone === "destructive"
										? "text-destructive"
										: "text-muted-foreground"
								}
							>
								{status.detail}
							</span>
						)}
					</>
				}
				actions={oauthActions || undefined}
			/>
			<OverviewRow
				title="Defaults"
				hint="Applied to new mappings."
				summary={
					<>
						<span className="text-muted-foreground">
							Default {entityLabel}
						</span>
						<span className="max-w-full rounded-md bg-muted px-2 py-0.5 font-mono text-xs [overflow-wrap:anywhere]">
							{integration.default_entity_id || "Not set"}
						</span>
						{schema.length > 0 && (
							<span className="text-muted-foreground">
								{setDefaults} of {schema.length} config defaults
								set
							</span>
						)}
					</>
				}
				actions={
					<>
						{schema.length > 0 && (
							<Popover>
								<PopoverTrigger asChild>
									<Button
										variant="ghost"
										size="sm"
										className="min-h-11 sm:min-h-8"
									>
										<Eye className="h-3 w-3" />
										View Defaults
									</Button>
								</PopoverTrigger>
								<PopoverContent align="end" className="w-80">
									<dl
										aria-label="Configuration defaults"
										className="max-h-72 space-y-2 overflow-auto text-sm"
									>
										{schema.map((field) => (
											<div
												key={field.key}
												className="flex min-w-0 flex-wrap items-center justify-between gap-2"
											>
												<dt className="min-w-0 text-muted-foreground [overflow-wrap:anywhere]">
													{field.key}
													{field.required && (
														<span className="ml-1 text-destructive">
															*
														</span>
													)}
												</dt>
												<dd className="max-w-full rounded-md bg-muted px-2 py-0.5 font-mono text-xs [overflow-wrap:anywhere]">
													{formatDefaultValue(
														field,
														integration
															.config_defaults?.[
															field.key
														],
													)}
												</dd>
											</div>
										))}
									</dl>
								</PopoverContent>
							</Popover>
						)}
						{canManageDefinitions && (
							<Button
								variant="outline"
								size="sm"
								className="min-h-11 sm:min-h-8"
								onClick={onOpenDefaultsDialog}
								aria-label="Configure default values"
							>
								<Pencil className="h-3 w-3" />
								Configure
							</Button>
						)}
					</>
				}
			/>
		</div>
	);
}
