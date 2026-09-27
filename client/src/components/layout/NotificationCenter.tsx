import {
	useState,
	useCallback,
	type ComponentType,
	type SVGProps,
	type ReactNode,
} from "react";
import { useNavigate } from "react-router-dom";
import {
	Bell,
	X,
	AlertCircle,
	AlertTriangle,
	Info,
	CheckCircle,
	Trash2,
	Loader2,
	Upload,
	Package,
	Cog,
	Play,
	FileCode,
	Database,
} from "lucide-react";
import { Github } from "@/components/icons/GithubIcon";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import {
	AlertDialog,
	AlertDialogAction,
	AlertDialogCancel,
	AlertDialogContent,
	AlertDialogDescription,
	AlertDialogFooter,
	AlertDialogHeader,
	AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
	Popover,
	PopoverContent,
	PopoverTrigger,
} from "@/components/ui/popover";
import { useNotifications } from "@/hooks/useNotifications";
import {
	type Notification,
	type NotificationCategory,
	type NotificationStatus,
	isActiveNotification,
	isAwaitingActionNotification,
	getNotificationCounts,
	useNotificationStore,
	type OneOffNotification,
	type AlertStatus,
	getAlertCounts,
} from "@/stores/notificationStore";
import { useEditorStore } from "@/stores/editorStore";
import { fileService } from "@/services/fileService";
import { downloadSolutionExportJob } from "@/services/solutions";
import { cn } from "@/lib/utils";
import { authFetch } from "@/lib/api-client";
import { toast } from "sonner";

/**
 * Notification Center component for the header
 *
 * Displays:
 * 1. Progress notifications - Long-running operations with status and optional progress bar
 * 2. One-off alerts - Dismissable success/error/warning/info messages
 */

// Icon mapping for notification categories
type IconComponent = ComponentType<SVGProps<SVGSVGElement>>;
const categoryIcons: Record<NotificationCategory, IconComponent> = {
	github_setup: Github,
	github_sync: Github,
	file_upload: Upload,
	package_install: Package,
	embedding_reindex: Database,
	system: Cog,
};

// Status config for one-off alerts
const alertStatusConfig: Record<
	AlertStatus,
	{ icon: typeof AlertCircle; color: string }
> = {
	error: {
		icon: AlertCircle,
		color: "text-destructive",
	},
	warning: {
		icon: AlertTriangle,
		color: "text-[var(--bf-warning)]",
	},
	info: {
		icon: Info,
		color: "text-primary",
	},
	success: {
		icon: CheckCircle,
		color: "text-[var(--bf-success)]",
	},
};

// Status config for progress notifications
const notificationStatusConfig: Record<NotificationStatus, { color: string }> =
	{
		pending: { color: "text-primary" },
		running: { color: "text-primary" },
		awaiting_action: { color: "text-[var(--bf-warning)]" },
		completed: { color: "text-[var(--bf-success)]" },
		failed: { color: "text-destructive" },
		cancelled: { color: "text-muted-foreground" },
	};

function downloadBlob(blob: Blob, filename: string) {
	const url = URL.createObjectURL(blob);
	const a = document.createElement("a");
	a.href = url;
	a.download = filename;
	document.body.appendChild(a);
	a.click();
	a.remove();
	URL.revokeObjectURL(url);
}

// Action handler for notification actions
async function handleNotificationAction(
	notification: Notification,
	navigate: ReturnType<typeof useNavigate>,
) {
	const action = notification.metadata?.action as string | undefined;
	const actionUrl = notification.metadata?.action_url as string | undefined;
	const jobId = notification.metadata?.job_id as string | undefined;

	// Handle navigation actions (e.g., configure_pricing)
	if (actionUrl) {
		navigate(actionUrl);
		return;
	}

	if (action === "run_maintenance") {
		try {
			const response = await authFetch("/api/maintenance/reindex", {
				method: "POST",
				body: JSON.stringify({
					notification_id: notification.id,
				}),
			});

			if (!response.ok) {
				const errorData = await response.json().catch(() => ({}));
				toast.error("Failed to start maintenance", {
					description: errorData.detail || "Unknown error",
				});
			}
			// Backend updates notification status via WebSocket - no need to handle success here
		} catch (error) {
			toast.error("Failed to start maintenance", {
				description:
					error instanceof Error ? error.message : "Unknown error",
			});
		}
	}

	if (action === "download_solution_export" && jobId) {
		try {
			const { blob, filename } = await downloadSolutionExportJob(jobId);
			downloadBlob(blob, filename);
		} catch (error) {
			toast.error("Failed to download backup export", {
				description:
					error instanceof Error ? error.message : "Unknown error",
			});
		}
	}
}

function NotificationFrame({
	title,
	icon,
	action,
	children,
}: {
	title: string;
	icon: ReactNode;
	action?: ReactNode;
	children: ReactNode;
}) {
	return (
		<article
			aria-label={title}
			className="min-w-0 space-y-3 border-b p-[var(--bf-surface-pad)] last:border-b-0"
		>
			<div className="flex items-start gap-3">
				<div aria-hidden="true" className="mt-2 shrink-0">
					{icon}
				</div>
				<h4 className="min-w-0 flex-1 pt-2 text-sm font-semibold leading-5 [overflow-wrap:anywhere]">
					{title}
				</h4>
				{action}
			</div>
			<div className="space-y-2 text-sm leading-6 [overflow-wrap:anywhere]">
				{children}
			</div>
		</article>
	);
}

function ProgressNotificationItem({
	notification,
	onDismiss,
	onAction,
	onOpenFile,
}: {
	notification: Notification;
	onDismiss: () => void;
	onAction: (notification: Notification) => Promise<void>;
	onOpenFile: (filePath: string, lineNumber?: number) => Promise<void>;
}) {
	const [isActionLoading, setIsActionLoading] = useState(false);
	const [detailsOpen, setDetailsOpen] = useState(false);
	const Icon = categoryIcons[notification.category] || Cog;
	const statusConfig = notificationStatusConfig[notification.status];
	const isActive = isActiveNotification(notification);
	const isAwaitingAction = isAwaitingActionNotification(notification);

	// Check if notification has an action button (but not view_file - that's a link, not a button)
	const action = notification.metadata?.action as string | undefined;
	const hasCompletedDownloadAction =
		notification.status === "completed" &&
		action === "download_solution_export" &&
		!!notification.metadata?.job_id;
	const hasAction =
		(isAwaitingAction || hasCompletedDownloadAction) &&
		!!action &&
		action !== "view_file";
	const actionLabel =
		(notification.metadata?.action_label as string) || "Run";

	// Optional explainer dialog (e.g. Kubernetes enable/disable notices):
	// a "Learn more" button opens roomier copy plus a primary navigation.
	const details = notification.metadata?.details as
		| {
				title?: unknown;
				paragraphs?: unknown;
				primary_label?: unknown;
		  }
		| undefined;
	const detailParagraphs = Array.isArray(details?.paragraphs)
		? details.paragraphs.filter(
				(paragraph): paragraph is string =>
					typeof paragraph === "string",
			)
		: [];
	const hasDetails =
		typeof details?.title === "string" && detailParagraphs.length > 0;
	const detailsTitle =
		typeof details?.title === "string" ? details.title : "";
	const detailsPrimaryLabel =
		typeof details?.primary_label === "string"
			? details.primary_label
			: "Open settings";

	// Check if notification has a file link (view_file action with file_path)
	const filePath = notification.metadata?.file_path as string | undefined;
	const lineNumber = notification.metadata?.line_number as number | undefined;
	const hasFileLink = !!filePath;

	const handleAction = async () => {
		setIsActionLoading(true);
		try {
			await onAction(notification);
		} finally {
			setIsActionLoading(false);
		}
	};

	const StateIcon = isActive
		? Loader2
		: notification.status === "completed"
			? CheckCircle
			: notification.status === "failed"
				? AlertCircle
				: isAwaitingAction
					? AlertTriangle
					: Icon;
	return (
		<NotificationFrame
			title={notification.title}
			icon={
				<StateIcon
					className={cn(
						"size-5",
						statusConfig.color,
						isActive && "animate-spin motion-reduce:animate-none",
					)}
				/>
			}
			action={
				!isActive ? (
					<Button
						type="button"
						variant="ghost"
						size="icon-lg"
						aria-label={`Dismiss ${notification.title}`}
						onClick={onDismiss}
					>
						<X aria-hidden="true" className="size-4" />
					</Button>
				) : notification.category === "embedding_reindex" ? (
					<Button
						type="button"
						variant="ghost"
						className="min-h-11 px-2"
						onClick={onDismiss}
					>
						Cancel
					</Button>
				) : undefined
			}
		>
			{notification.description && (
				<p className="text-muted-foreground">
					{notification.description}
				</p>
			)}
			{hasDetails && (
				<>
					<Button
						type="button"
						variant="outline"
						className="min-h-11 h-auto w-full whitespace-normal py-2"
						onClick={() => setDetailsOpen(true)}
					>
						Learn more
					</Button>
					<AlertDialog
						open={detailsOpen}
						onOpenChange={setDetailsOpen}
					>
						<AlertDialogContent>
							<AlertDialogHeader>
								<AlertDialogTitle>
									{detailsTitle}
								</AlertDialogTitle>
								<AlertDialogDescription asChild>
									<div className="space-y-2 text-left">
										{detailParagraphs.map(
											(paragraph, index) => (
												<p key={index}>{paragraph}</p>
											),
										)}
									</div>
								</AlertDialogDescription>
							</AlertDialogHeader>
							<AlertDialogFooter>
								<AlertDialogCancel>Close</AlertDialogCancel>
								<AlertDialogAction
									onClick={() => {
										setDetailsOpen(false);
										void onAction(notification);
									}}
								>
									{detailsPrimaryLabel}
								</AlertDialogAction>
							</AlertDialogFooter>
						</AlertDialogContent>
					</AlertDialog>
				</>
			)}
			{notification.error && (
				<p className="text-destructive">{notification.error}</p>
			)}
			{hasFileLink && (
				<button
					type="button"
					onClick={() => void onOpenFile(filePath, lineNumber)}
					className="flex min-h-11 w-full items-center gap-2 rounded-[var(--bf-radius-control)] text-left text-xs text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
				>
					<FileCode aria-hidden="true" className="size-4 shrink-0" />
					<span className="min-w-0 [overflow-wrap:anywhere]">
						{filePath}
					</span>
				</button>
			)}
			{isActive && notification.percent !== null && (
				<Progress
					value={notification.percent}
					aria-label={`${notification.title} progress`}
					className="h-1.5"
				/>
			)}
			{hasAction && (
				<Button
					type="button"
					className="min-h-11 h-auto w-full whitespace-normal py-2"
					onClick={handleAction}
					disabled={isActionLoading}
				>
					{isActionLoading ? (
						<Loader2
							aria-hidden="true"
							className="size-4 animate-spin motion-reduce:animate-none"
						/>
					) : (
						<Play aria-hidden="true" className="size-4" />
					)}
					{actionLabel}
				</Button>
			)}
			<p className="text-xs text-muted-foreground">
				{new Date(notification.updatedAt).toLocaleString()}
			</p>
		</NotificationFrame>
	);
}

function AlertNotificationItem({
	alert,
	onDismiss,
}: {
	alert: OneOffNotification;
	onDismiss: () => void;
}) {
	const config = alertStatusConfig[alert.status];
	const Icon = config.icon;

	return (
		<NotificationFrame
			title={alert.title}
			icon={<Icon className={cn("size-5", config.color)} />}
			action={
				<Button
					type="button"
					variant="ghost"
					size="icon-lg"
					aria-label={`Dismiss ${alert.title}`}
					onClick={onDismiss}
				>
					<X aria-hidden="true" className="size-4" />
				</Button>
			}
		>
			<p className="text-muted-foreground">{alert.body}</p>
			<p className="text-xs text-muted-foreground">
				{new Date(alert.createdAt).toLocaleString()}
			</p>
		</NotificationFrame>
	);
}

export function NotificationCenter({
	triggerClassName,
}: { triggerClassName?: string } = {}) {
	const [isOpen, setIsOpen] = useState(false);
	const navigate = useNavigate();
	const {
		notifications,
		dismiss,
		clearAll,
		isLoading,
		error,
		refetch,
		isFetching,
	} = useNotifications();
	const alerts = useNotificationStore((state) => state.alerts);
	const removeAlert = useNotificationStore((state) => state.removeAlert);
	const clearAlerts = useNotificationStore((state) => state.clearAlerts);

	// Editor store actions for opening files
	const openFileInTab = useEditorStore((state) => state.openFileInTab);
	const openEditor = useEditorStore((state) => state.openEditor);
	const revealLine = useEditorStore((state) => state.revealLine);

	// Handler for notification actions (navigation, maintenance, etc.)
	const handleAction = async (notification: Notification) => {
		await handleNotificationAction(notification, navigate);
		// Close popover after navigation action
		if (notification.metadata?.action_url) {
			setIsOpen(false);
		}
	};

	// Handler for opening a file in the editor (used by file link in notifications)
	const openFileInEditor = useCallback(
		async (filePath: string, lineNumber?: number) => {
			try {
				const response = await fileService.readFile(filePath);
				const fileName = filePath.split("/").pop() || filePath;
				const extension = fileName.includes(".")
					? fileName.split(".").pop() || ""
					: "";

				const fileMetadata = {
					path: filePath,
					name: fileName,
					type: "file" as const,
					size: response.size,
					extension,
					modified: new Date().toISOString(),
					entity_type: null,
					entity_id: null,
				};
				openEditor();
				openFileInTab(
					fileMetadata,
					response.content,
					response.encoding as "utf-8" | "base64",
					response.etag,
				);
				if (lineNumber) {
					// Small delay to ensure editor is ready before revealing line
					setTimeout(() => revealLine(lineNumber), 100);
				}
				setIsOpen(false);
			} catch (error) {
				console.error("Failed to open file:", error);
				toast.error("Failed to open file");
			}
		},
		[openFileInTab, openEditor, revealLine],
	);

	const notificationCounts = getNotificationCounts(notifications);
	const alertCounts = getAlertCounts(alerts);

	// Total count for badge - includes awaiting_action as they need user attention
	const totalCount =
		notificationCounts.active +
		notificationCounts.awaitingAction +
		notificationCounts.failed +
		alertCounts.error +
		alertCounts.warning;

	// Badge color based on highest priority
	const getBadgeVariant = () => {
		if (notificationCounts.failed > 0 || alertCounts.error > 0)
			return "destructive";
		if (alertCounts.warning > 0 || notificationCounts.awaitingAction > 0)
			return "default";
		if (notificationCounts.active > 0) return "secondary";
		return "secondary";
	};

	// Sort notifications: active first, then awaiting_action, then by date
	const sortedNotifications = [...notifications].sort((a, b) => {
		// Priority: active (0) > awaiting_action (1) > completed/failed (2)
		const getPriority = (n: Notification) => {
			if (isActiveNotification(n)) return 0;
			if (isAwaitingActionNotification(n)) return 1;
			return 2;
		};
		const aPriority = getPriority(a);
		const bPriority = getPriority(b);
		if (aPriority !== bPriority) return aPriority - bPriority;

		// Then by date (newest first)
		return (
			new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime()
		);
	});

	// Sort alerts by status priority, then date
	const sortedAlerts = [...alerts].sort((a, b) => {
		const priority: Record<AlertStatus, number> = {
			error: 0,
			warning: 1,
			info: 2,
			success: 3,
		};
		if (priority[a.status] !== priority[b.status]) {
			return priority[a.status] - priority[b.status];
		}
		return (
			new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime()
		);
	});

	const handleClearAll = () => {
		clearAll();
		clearAlerts();
	};

	const hasNotifications =
		sortedNotifications.length > 0 || sortedAlerts.length > 0;

	return (
		<Popover open={isOpen} onOpenChange={setIsOpen}>
			<PopoverTrigger asChild>
				<Button
					type="button"
					variant="ghost"
					size="icon"
					className={cn("relative", triggerClassName)}
					aria-label="Notifications"
				>
					<Bell className="h-4 w-4" />
					{totalCount > 0 && (
						<Badge
							variant={getBadgeVariant()}
							className="absolute -top-1 -right-1 h-5 w-5 flex items-center justify-center p-0 text-xs"
						>
							{totalCount > 99 ? "99+" : totalCount}
						</Badge>
					)}
				</Button>
			</PopoverTrigger>
			<PopoverContent
				aria-label="Notifications"
				className="flex w-[min(24rem,calc(100vw-2rem))] max-h-[min(36rem,var(--radix-popover-content-available-height))] flex-col gap-0 overflow-hidden p-0"
				align="end"
				sideOffset={8}
				collisionPadding={16}
			>
				<div className="flex shrink-0 items-center justify-between gap-3 px-[var(--bf-surface-pad)] py-3 border-b">
					<h3 className="font-semibold">Notifications</h3>
					{hasNotifications && (
						<Button
							type="button"
							variant="ghost"
							size="sm"
							className="min-h-11 text-xs"
							onClick={handleClearAll}
						>
							<Trash2 className="h-3 w-3 mr-1" />
							Clear all
						</Button>
					)}
				</div>
				<div className="min-h-0 overflow-y-auto">
					{error && (
						<div
							role="alert"
							className="space-y-2 border-b p-[var(--bf-surface-pad)] text-sm"
						>
							<p>Couldn't load notifications.</p>
							<Button
								type="button"
								variant="outline"
								className="min-h-11"
								disabled={isFetching}
								onClick={() => void refetch()}
							>
								{isFetching ? "Retrying…" : "Retry"}
							</Button>
						</div>
					)}
					{isLoading && !hasNotifications ? (
						<div
							role="status"
							className="p-[var(--bf-surface-pad)] text-sm text-muted-foreground"
						>
							Loading notifications…
						</div>
					) : !hasNotifications && !error ? (
						<div className="flex flex-col items-center justify-center h-32 text-muted-foreground">
							<Bell className="h-8 w-8 mb-2 opacity-50" />
							<p className="text-sm">No notifications</p>
						</div>
					) : (
						<div className="min-w-0">
							{/* Progress notifications */}
							{sortedNotifications.map((notification) => (
								<ProgressNotificationItem
									key={notification.id}
									notification={notification}
									onDismiss={() => dismiss(notification.id)}
									onAction={handleAction}
									onOpenFile={openFileInEditor}
								/>
							))}

							{/* Divider if both types present */}
							{sortedNotifications.length > 0 &&
								sortedAlerts.length > 0 && (
									<div className="border-t" />
								)}

							{/* One-off alerts */}
							{sortedAlerts.map((alert) => (
								<AlertNotificationItem
									key={alert.id}
									alert={alert}
									onDismiss={() => removeAlert(alert.id)}
								/>
							))}
						</div>
					)}
				</div>
			</PopoverContent>
		</Popover>
	);
}
