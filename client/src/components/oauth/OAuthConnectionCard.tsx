import {
	Trash2,
	ExternalLink,
	Clock,
	CheckCircle2,
	XCircle,
	Loader2,
	AlertCircle,
	Copy,
	Check,
	Pencil,
	RefreshCw,
	MoreVertical,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import {
	Card,
	CardContent,
	CardFooter,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
	Tooltip,
	TooltipContent,
	TooltipProvider,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { copyToClipboard } from "@/lib/clipboard";
import type { components } from "@/lib/v1";
import { getStatusLabel, isExpired, expiresSoon } from "@/lib/client-types";

type OAuthConnectionDetail = components["schemas"]["OAuthConnectionDetail"];

interface OAuthConnectionCardProps {
	connection: OAuthConnectionDetail;
	onAuthorize: (connectionName: string) => Promise<string | void>;
	onEdit: (connectionName: string) => void;
	onRefresh: (connectionName: string) => void;
	onDelete: (connectionName: string) => void;
	onCancel?: (connectionName: string) => void;
	isAuthorizing?: boolean;
	isRefreshing?: boolean;
	isDeleting?: boolean;
	isCanceling?: boolean;
}

export function OAuthConnectionCard({
	connection,
	onAuthorize,
	onEdit,
	onRefresh,
	onDelete,
	onCancel,
	isAuthorizing = false,
	isRefreshing = false,
	isDeleting = false,
	isCanceling = false,
}: OAuthConnectionCardProps) {
	const [copiedCallback, setCopiedCallback] = useState(false);
	const [isCopying, setIsCopying] = useState(false);
	const [copyError, setCopyError] = useState(false);
	const copyAttempt = useRef(0);
	const copyPending = useRef(false);
	const copyTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

	const callbackUrl = `${window.location.origin}/oauth/callback/${connection.connection_name}`;

	const [copyUrl, setCopyUrl] = useState(callbackUrl);
	if (copyUrl !== callbackUrl) {
		setCopyUrl(callbackUrl);
		setCopiedCallback(false);
		setIsCopying(false);
		setCopyError(false);
	}

	useEffect(() => {
		copyPending.current = false;
		return () => {
			copyAttempt.current += 1;
			clearTimeout(copyTimer.current);
		};
	}, [callbackUrl]);

	const handleCopyCallback = async () => {
		if (copyPending.current) return;
		copyPending.current = true;
		const attempt = ++copyAttempt.current;
		clearTimeout(copyTimer.current);
		setIsCopying(true);
		setCopiedCallback(false);
		setCopyError(false);
		const succeeded = await copyToClipboard(callbackUrl);
		if (attempt !== copyAttempt.current) return;
		copyPending.current = false;
		setIsCopying(false);
		if (!succeeded) {
			setCopyError(true);
			return;
		}
		setCopiedCallback(true);
		toast.success("Callback URL copied to clipboard");
		copyTimer.current = setTimeout(() => setCopiedCallback(false), 2000);
	};

	const handleAuthorizeClick = async () => {
		await onAuthorize(connection.connection_name);
	};

	const getStatusIcon = () => {
		switch (connection.status) {
			case "completed":
				return (
					<CheckCircle2 className="h-4 w-4 text-[var(--bf-success)]" />
				);
			case "failed":
				return (
					<XCircle className="h-4 w-4 text-[var(--bf-danger)]" />
				);
			case "waiting_callback":
			case "testing":
				return (
					<Loader2 className="h-4 w-4 motion-safe:animate-spin text-[var(--bf-warning)]" />
				);
			default:
				return <Clock className="h-4 w-4 text-muted-foreground" />;
		}
	};

	const isWorkloadIdentity = connection.oauth_flow_type === "workload_identity";
	const canConnect =
		connection.oauth_flow_type !== "client_credentials" && !isWorkloadIdentity;
	const needsReconnection =
		connection.status === "not_connected" || connection.status === "failed";

	const expirationWarning =
		!isWorkloadIdentity &&
		connection.expires_at &&
		expiresSoon(connection.expires_at);
	const isTokenExpired =
		!isWorkloadIdentity && connection.expires_at && isExpired(connection.expires_at);

	const formatDateTime = (dateStr?: string) => {
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

	// Compact format for badges
	const formatDateTimeCompact = (dateStr?: string) => {
		if (!dateStr) return "Never";

		const utcDateStr = dateStr.endsWith("Z") || dateStr.includes("+") || dateStr.includes("-", 10) ? dateStr : `${dateStr}Z`;
		const date = new Date(utcDateStr);
		const now = new Date();
		const diffMs = date.getTime() - now.getTime();
		const diffMins = Math.floor(Math.abs(diffMs) / 60000);
		const diffHours = Math.floor(Math.abs(diffMs) / 3600000);
		const diffDays = Math.floor(Math.abs(diffMs) / 86400000);

		// For dates within 7 days, show compact relative time
		if (diffDays < 7) {
			if (diffMs < 0) {
				// Past
				if (diffMins < 60) return `${diffMins}m ago`;
				if (diffHours < 24) return `${diffHours}h ago`;
				return `${diffDays}d ago`;
			}

			// Future
			if (diffMins < 60) return `${diffMins}m`;
			if (diffHours < 24) return `${diffHours}h`;
			return `${diffDays}d`;
		}

		// Absolute dates
		return date.toLocaleDateString(undefined, {
			month: "short",
			day: "numeric",
		});
	};

	return (
		<Card className="flex h-full flex-col overflow-hidden rounded-[var(--bf-radius-surface)] border-border/70 bg-card shadow-sm transition-shadow hover:shadow-md motion-reduce:transition-none">
			<CardHeader className="pb-4">
				<div className="flex items-start justify-between gap-3">
					<div className="min-w-0 flex-1 space-y-2">
						<CardTitle className="text-base font-semibold leading-6 [overflow-wrap:anywhere]">
							{connection.connection_name}
						</CardTitle>
						<Badge
							variant="outline"
							className="w-fit rounded-[var(--bf-radius-control)] text-xs"
						>
							{isWorkloadIdentity
								? "SDK Managed"
								: connection.oauth_flow_type === "client_credentials"
									? "Client credentials"
									: "Authorization code"}
						</Badge>
					</div>
					<div className="flex shrink-0 items-center gap-2">
						{!isWorkloadIdentity && connection.expires_at && (
							<TooltipProvider>
								<Tooltip>
									<TooltipTrigger asChild>
										<div
											className={cn(
												"flex items-center gap-1 text-xs leading-5 cursor-help whitespace-nowrap",
												isTokenExpired
													? "text-[var(--bf-danger)]"
													: expirationWarning
														? "text-[var(--bf-warning)]"
														: "text-muted-foreground",
											)}
										>
											{isTokenExpired ? (
												<XCircle className="h-3.5 w-3.5" />
											) : (
												<CheckCircle2 className="h-3.5 w-3.5" />
											)}
											<span>
												{formatDateTimeCompact(
													connection.expires_at,
												)}
											</span>
										</div>
									</TooltipTrigger>
									<TooltipContent>
										<p>
											{isTokenExpired
												? "Expired"
												: "Expires"}{" "}
											{formatDateTime(
												connection.expires_at,
											)}
										</p>
									</TooltipContent>
								</Tooltip>
							</TooltipProvider>
						)}
						{!isWorkloadIdentity && <TooltipProvider>
							<Tooltip>
								<TooltipTrigger asChild>
									<div className="cursor-help">
										{getStatusIcon()}
									</div>
								</TooltipTrigger>
								<TooltipContent>
									<p>{getStatusLabel(connection.status)}</p>
								</TooltipContent>
							</Tooltip>
						</TooltipProvider>}
					</div>
				</div>
			</CardHeader>

			<CardContent className="flex-1 space-y-4">
				{/* Callback URL for not connected state */}
				{needsReconnection && canConnect && (
					<div className="space-y-2 rounded-[var(--bf-radius-surface)] border border-border/70 bg-muted/20 p-3">
						<div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2">
							<p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Callback URL</p>

							<Button
								type="button"
								variant="outline"
								size="icon-lg"
								onClick={handleCopyCallback}
								disabled={isCopying}
								aria-busy={isCopying}
								className="shrink-0"
								aria-label={
									isCopying ? "Copying callback URL" : copiedCallback
										? "Callback URL copied"
										: "Copy callback URL"
								}
							>
								{copiedCallback ? (
									<Check className="h-4 w-4" />
								) : (
									<Copy className="h-4 w-4" />
								)}
							</Button>
							<code className="col-span-2 min-w-0 rounded-[var(--bf-radius-control)] border border-border/70 bg-background px-2.5 py-2 text-xs leading-5 [overflow-wrap:anywhere]">
								{callbackUrl}
							</code>
						</div>
						{copyError && <p role="alert" className="text-sm text-[var(--bf-danger)]">Could not copy. Try again or select the URL to copy it manually.</p>}
						<p className="text-xs leading-5 text-muted-foreground">
							Add this URL to your OAuth app's allowed redirect
							URIs
						</p>
					</div>
				)}

				{/* Status Message - only show for non-completed statuses */}
				{connection.status_message &&
					connection.status !== "completed" && (
						<div className="rounded-[var(--bf-radius-surface)] border border-border/70 bg-muted/30 p-3 text-sm leading-6 text-muted-foreground">
							{connection.status_message}
						</div>
					)}

				{/* Expiration Warning */}
				{!isWorkloadIdentity && connection.status === "completed" && isTokenExpired && (
					<div className="flex items-start gap-2 rounded-[var(--bf-radius-surface)] border border-[var(--bf-danger)]/20 bg-[var(--bf-danger-soft)]/60 p-3 text-sm leading-6 text-[var(--bf-danger)]">
						<AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
						<span>
							Token expired. Reconnect to continue using this
							connection.
						</span>
					</div>
				)}

				{!isWorkloadIdentity &&
					connection.status === "completed" &&
					!isTokenExpired &&
					expirationWarning && (
						<div className="flex items-start gap-2 rounded-[var(--bf-radius-surface)] border border-[var(--bf-warning)]/20 bg-[var(--bf-warning-soft)]/60 p-3 text-sm leading-6 text-[var(--bf-warning)]">
							<AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
							<span>Token expires soon</span>
						</div>
					)}
			</CardContent>

			<CardFooter className="border-t border-border/70 px-6 py-4">
				<div className="flex w-full flex-col gap-2 sm:flex-row sm:items-center">
					{needsReconnection && canConnect && (
						<Button
							onClick={handleAuthorizeClick}
							disabled={isAuthorizing}
							className="min-h-11 w-full sm:flex-1"
						>
							{isAuthorizing ? (
								<>
									<Loader2 className="mr-2 h-4 w-4 motion-safe:animate-spin" />
									Connecting...
								</>
							) : (
								<>
									<ExternalLink className="mr-2 h-4 w-4" />
									{connection.status === "failed"
										? "Reconnect"
										: "Connect"}
								</>
							)}
						</Button>
					)}

					{/* For client_credentials with not_connected/failed status, show Get Token button */}
					{needsReconnection &&
						connection.oauth_flow_type === "client_credentials" && (
						<Button
							onClick={() => onRefresh(connection.connection_name)}
							disabled={isRefreshing}
							className="min-h-11 w-full sm:flex-1"
						>
							{isRefreshing ? (
								<>
									<Loader2 className="mr-2 h-4 w-4 motion-safe:animate-spin" />
									Getting Token...
								</>
							) : (
								<>
									<RefreshCw className="mr-2 h-4 w-4" />
									{connection.status === "failed"
										? "Retry"
										: "Get Token"}
								</>
							)}
						</Button>
					)}

					{connection.status === "completed" && canConnect && (
						<Button
							onClick={handleAuthorizeClick}
							disabled={isAuthorizing}
							variant="outline"
							className="min-h-11 w-full sm:flex-1"
						>
							{isAuthorizing ? (
								<>
									<Loader2 className="mr-2 h-4 w-4 motion-safe:animate-spin" />
									Reconnecting...
								</>
							) : (
								<>
									<ExternalLink className="mr-2 h-4 w-4" />
									Reconnect
								</>
							)}
						</Button>
					)}

					{/* For client_credentials, show Refresh Token button where Reconnect would be */}
						{connection.status === "completed" &&
						connection.oauth_flow_type === "client_credentials" &&
						connection.expires_at && (
							<Button
								onClick={() =>
									onRefresh(connection.connection_name)
								}
								disabled={isRefreshing}
								variant="outline"
								className="min-h-11 w-full sm:flex-1"
							>
								{isRefreshing ? (
									<>
										<Loader2 className="mr-2 h-4 w-4 motion-safe:animate-spin" />
										Refreshing...
									</>
								) : (
									<>
										<RefreshCw className="mr-2 h-4 w-4" />
										Refresh Token
									</>
								)}
							</Button>
						)}

					{connection.status === "waiting_callback" && (
						<>
							<Button
								variant="outline"
								className="min-h-11 w-full sm:flex-1"
								disabled
							>
								<Loader2 className="mr-2 h-4 w-4 motion-safe:animate-spin" />
								Connecting...
							</Button>
							{onCancel && (
								<Button
									variant="ghost"
									onClick={() =>
										onCancel(connection.connection_name)
									}
									disabled={isCanceling}
									className="min-h-11 w-full sm:w-auto"
								>
									{isCanceling ? "Canceling..." : "Cancel"}
								</Button>
							)}
						</>
					)}

					{connection.status === "testing" && (
						<Button
							variant="outline"
							className="min-h-11 w-full sm:flex-1"
							disabled
						>
							<Loader2 className="mr-2 h-4 w-4 motion-safe:animate-spin" />
							Testing connection...
						</Button>
					)}

					<DropdownMenu>
						<DropdownMenuTrigger asChild>
							<Button
								variant="outline"
								size="icon-lg"
								className="shrink-0"
								aria-label="Connection actions"
							>
								<MoreVertical className="h-4 w-4" />
							</Button>
						</DropdownMenuTrigger>
						<DropdownMenuContent align="end">
							{/* Only show refresh in dropdown for authorization_code flow (canConnect) */}
							{connection.status === "completed" &&
								connection.expires_at &&
								canConnect && (
									<DropdownMenuItem
										onClick={() =>
											onRefresh(
												connection.connection_name,
											)
										}
										disabled={isRefreshing}
									>
										<RefreshCw
											className={`mr-2 h-4 w-4 ${isRefreshing ? "motion-safe:animate-spin" : ""}`}
										/>
										{isRefreshing
											? "Refreshing..."
											: "Refresh token"}
									</DropdownMenuItem>
								)}
							<DropdownMenuItem
								onClick={() =>
									onEdit(connection.connection_name)
								}
							>
								<Pencil className="mr-2 h-4 w-4" />
								Edit
							</DropdownMenuItem>
							<DropdownMenuItem
								onClick={() =>
									onDelete(connection.connection_name)
								}
								disabled={isDeleting}
								className="text-[var(--bf-danger)] focus:text-[var(--bf-danger)]"
							>
								<Trash2 className="mr-2 h-4 w-4" />
								{isDeleting ? "Deleting..." : "Delete"}
							</DropdownMenuItem>
						</DropdownMenuContent>
					</DropdownMenu>
				</div>
			</CardFooter>
		</Card>
	);
}
