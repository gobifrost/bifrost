import type { ComponentType } from "react";
import {
	AlertTriangle,
	CheckCircle2,
	ChevronDown,
	CircleDashed,
	Loader2,
	Pencil,
	Plus,
	RotateCw,
	Trash2,
	XCircle,
} from "lucide-react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
		return { label: "Not Set Up", tone: "outline" };
	}
	if (isOAuthExpired) {
		return {
			label: "Expired",
			tone: "destructive",
			detail: "Token expired - reconnect required",
		};
	}
	if (isOAuthConnected) {
		if (isOAuthExpiringSoon) {
			return {
				label: "Expires Soon",
				tone: "warning",
				detail: "Expires soon - consider refreshing the token",
			};
		}
		if (oauthConfig?.has_refresh_token === false && canUseAuthCodeFlow) {
			return {
				label: "Connected",
				tone: "warning",
				detail: "No refresh token - reconnect manually when it expires",
			};
		}
		return { label: "Connected", tone: "success" };
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

const TONE_ICON: Record<OAuthTone, ComponentType<{ className?: string }>> = {
	success: CheckCircle2,
	warning: AlertTriangle,
	destructive: XCircle,
	outline: CircleDashed,
};
const TONE_TEXT: Record<OAuthTone, string> = {
	success: "text-success",
	warning: "text-warning",
	destructive: "text-destructive",
	outline: "text-muted-foreground",
};

/** "authorization_code" -> "Authorization Code". */
function flowLabel(flow?: string) {
	return (flow ?? "")
		.split("_")
		.filter(Boolean)
		.map((word) => word[0].toUpperCase() + word.slice(1))
		.join(" ");
}

/** Connect, reconnect, get a token, or retry: the one action that fixes the default connection. */
function OAuthPrimaryAction({
	integration,
	oauthConfig,
	isOAuthConnected,
	isOAuthExpired,
	canUseAuthCodeFlow,
	onOAuthConnect,
	onOAuthRefresh,
	isAuthorizePending,
	isRefreshPending,
}: IntegrationOverviewProps) {
	if (!integration.has_oauth_config || !oauthConfig) return null;
	if (canUseAuthCodeFlow) {
		return (
			<Button
				size="sm"
				variant={
					isOAuthConnected && !isOAuthExpired ? "outline" : "default"
				}
				className="min-h-11 sm:min-h-8"
				onClick={onOAuthConnect}
				disabled={isAuthorizePending}
			>
				{isAuthorizePending ? (
					<>
						<Loader2 className="size-3 animate-spin motion-reduce:animate-none" />
						Connecting...
					</>
				) : isOAuthConnected ? (
					"Reconnect default"
				) : (
					"Connect default"
				)}
			</Button>
		);
	}
	if (isOAuthConnected) return null;
	return (
		<Button
			size="sm"
			className="min-h-11 sm:min-h-8"
			onClick={onOAuthRefresh}
			disabled={isRefreshPending}
		>
			{isRefreshPending ? (
				<>
					<Loader2 className="size-3 animate-spin motion-reduce:animate-none" />
					Getting Token...
				</>
			) : oauthConfig.status === "failed" ? (
				"Retry"
			) : (
				"Get Token"
			)}
		</Button>
	);
}

/**
 * The integration's default OAuth connection and defaults as two header
 * chips. Each opens a popover with details and the actions that change it.
 */
export function IntegrationStatusChips(props: IntegrationOverviewProps) {
	const {
		integration,
		oauthConfig,
		isOAuthConnected,
		isRefreshPending,
		canManageDefinitions,
		onOAuthRefresh,
		onEditOAuthConfig,
		onDeleteOAuthConfig,
		onCreateOAuthConfig,
		onOpenDefaultsDialog,
	} = props;
	const status = oauthStatus(props);
	const StatusIcon = TONE_ICON[status.tone];
	const schema = integration.config_schema ?? [];
	const setDefaults = schema.filter((field) => {
		const value = integration.config_defaults?.[field.key];
		return value !== null && value !== undefined;
	}).length;
	const entityLabel = integration.entity_id_name || "Entity ID";
	const connectedFor =
		isOAuthConnected && oauthConfig?.expires_at && status.tone === "success"
			? ` · expires ${formatDateTime(oauthConfig.expires_at)}`
			: "";

	return (
		<div className="flex min-w-0 flex-wrap items-center gap-2">
			<Popover>
				<PopoverTrigger asChild>
					<Button
						variant="outline"
						size="sm"
						className="min-h-11 sm:min-h-8"
						aria-label={`Default OAuth: ${status.label}`}
					>
						<StatusIcon
							className={`size-4 ${TONE_TEXT[status.tone]}`}
						/>
						<span>
							OAuth: {status.label}
							<span className="text-muted-foreground">
								{connectedFor}
							</span>
						</span>
						<ChevronDown className="size-3 opacity-60" />
					</Button>
				</PopoverTrigger>
				<PopoverContent arrow align="start" className="w-80 space-y-3">
					<div className="flex items-start justify-between gap-2">
						<div>
							<h2 className="text-sm font-medium">
								Default OAuth
							</h2>
							<p className="text-xs text-muted-foreground">
								Fallback for organizations without their own
								connection.
							</p>
						</div>
						<Badge variant={status.tone}>{status.label}</Badge>
					</div>
					{integration.has_oauth_config && oauthConfig ? (
						<dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
							<dt className="text-muted-foreground">Flow</dt>
							<dd>{flowLabel(oauthConfig.oauth_flow_type)}</dd>
							{oauthConfig.expires_at && (
								<>
									<dt className="text-muted-foreground">
										{status.label === "Expired"
											? "Expired"
											: "Expires"}
									</dt>
									<dd>
										{formatDateTime(oauthConfig.expires_at)}
									</dd>
								</>
							)}
						</dl>
					) : (
						<p className="text-sm text-muted-foreground">
							Add OAuth settings when this integration needs
							default authentication.
						</p>
					)}
					{status.detail && (
						<p className={`text-sm ${TONE_TEXT[status.tone]}`}>
							{status.detail}
						</p>
					)}
					{canManageDefinitions && (
						<div className="flex flex-wrap gap-2 border-t border-border/60 pt-3">
							{!integration.has_oauth_config ? (
								<Button
									size="sm"
									className="min-h-11 sm:min-h-8"
									onClick={onCreateOAuthConfig}
								>
									<Plus className="size-3" />
									Set Up OAuth
								</Button>
							) : (
								<>
									<OAuthPrimaryAction {...props} />
									{isOAuthConnected &&
										oauthConfig?.expires_at && (
											<Button
												variant="outline"
												size="sm"
												className="min-h-11 sm:min-h-8"
												onClick={onOAuthRefresh}
												disabled={isRefreshPending}
												aria-label="Refresh default token"
											>
												{isRefreshPending ? (
													<Loader2 className="size-3 animate-spin motion-reduce:animate-none" />
												) : (
													<RotateCw className="size-3" />
												)}
												Refresh
											</Button>
										)}
									<Button
										variant="ghost"
										size="sm"
										className="min-h-11 sm:min-h-8"
										onClick={onEditOAuthConfig}
									>
										<Pencil className="size-3" />
										Edit
									</Button>
									<Button
										variant="ghost"
										size="sm"
										className="min-h-11 text-destructive sm:min-h-8"
										onClick={onDeleteOAuthConfig}
									>
										<Trash2 className="size-3" />
										Delete
									</Button>
								</>
							)}
						</div>
					)}
				</PopoverContent>
			</Popover>

			<Popover>
				<PopoverTrigger asChild>
					<Button
						variant="outline"
						size="sm"
						className="min-h-11 sm:min-h-8"
						aria-label="Defaults"
					>
						<span>
							{entityLabel}:{" "}
							<span className="font-mono text-xs">
								{integration.default_entity_id || "not set"}
							</span>
							{schema.length > 0 && (
								<span className="text-muted-foreground">
									{" "}
									· {setDefaults} of {schema.length} defaults
								</span>
							)}
						</span>
						<ChevronDown className="size-3 opacity-60" />
					</Button>
				</PopoverTrigger>
				<PopoverContent arrow align="start" className="w-80 space-y-3">
					<div>
						<h2 className="text-sm font-medium">Defaults</h2>
						<p className="text-xs text-muted-foreground">
							Applied to new mappings.
						</p>
					</div>
					<dl
						aria-label="Configuration defaults"
						className="max-h-72 space-y-2 overflow-auto text-sm"
					>
						<div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
							<dt className="text-muted-foreground">
								Default {entityLabel}
							</dt>
							<dd className="max-w-full rounded-md bg-muted px-2 py-0.5 font-mono text-xs [overflow-wrap:anywhere]">
								{integration.default_entity_id || "\u2014"}
							</dd>
						</div>
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
										integration.config_defaults?.[
											field.key
										],
									)}
								</dd>
							</div>
						))}
					</dl>
					{canManageDefinitions && (
						<div className="border-t border-border/60 pt-3">
							<Button
								variant="outline"
								size="sm"
								className="min-h-11 sm:min-h-8"
								onClick={onOpenDefaultsDialog}
								aria-label="Configure default values"
							>
								<Pencil className="size-3" />
								Configure
							</Button>
						</div>
					)}
				</PopoverContent>
			</Popover>
		</div>
	);
}

/** Shown only when the default connection is broken and needs action. */
export function IntegrationOAuthAlert(props: IntegrationOverviewProps) {
	const status = oauthStatus(props);
	if (status.tone !== "destructive") return null;
	return (
		<Alert variant="destructive">
			<AlertTitle>Default OAuth {status.label.toLowerCase()}</AlertTitle>
			<AlertDescription className="flex flex-wrap items-center justify-between gap-3">
				<span>
					Organizations without their own connection can't
					authenticate until it is fixed.
				</span>
				{props.canManageDefinitions && (
					<OAuthPrimaryAction {...props} />
				)}
			</AlertDescription>
		</Alert>
	);
}
