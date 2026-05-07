import { useState, useCallback, type ComponentType, type SVGProps } from "react";
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
	{ icon: typeof AlertCircle; color: string; bgColor: string }
> = {
	error: {
		icon: AlertCircle,
		color: "text-red-500",
		bgColor: "bg-red-500/10",
	},
	warning: {
		icon: AlertTriangle,
		color: "text-yellow-500",
		bgColor: "bg-yellow-500/10",
	},
	info: {
		icon: Info,
		color: "text-blue-500",
		bgColor: "bg-blue-500/10",
	},
	success: {
		icon: CheckCircle,
		color: "text-green-500",
		bgColor: "bg-green-500/10",
	},
};

// Status config for progress notifications
const notificationStatusConfig: Record<
	NotificationStatus,
	{ color: string; bgColor: string }
> = {
	pending: { color: "text-blue-500", bgColor: "bg-blue-500/10" },
	running: { color: "text-blue-500", bgColor: "bg-blue-500/10" },
	awaiting_action: { color: "text-amber-500", bgColor: "bg-amber-500/10" },
	completed: { color: "text-green-500", bgColor: "bg-green-500/10" },
	failed: { color: "text-red-500", bgColor: "bg-red-500/10" },
	cancelled: { color: "text-muted-foreground", bgColor: "bg-muted/50" },
};

// Action handler for notification actions
async function handleNotificationAction(
	notification: Notification,
	navigate: ReturnType<typeof useNavigate>,
) {
	const action = notification.metadata?.action as string | undefined;
	const actionUrl = notification.metadata?.action_url as string | undefined;

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
	const Icon = categoryIcons[notification.category] || Cog;
	const statusConfig = notificationStatusConfig[notification.status];
	const isActive = isActiveNotification(notification);
	const isAwaitingAction = isAwaitingActionNotification(notification);

	// Check if notification has an action button (but not view_file - that's a link, not a button)
	const action = notification.metadata?.action as string | undefined;
	const hasAction = isAwaitingAction && !!action && action !== "view_file";
	const actionLabel =
		(notification.metadata?.action_label as string) || "Run";

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

	return (
		<div
			className={cn(
				"flex items-start gap-3 p-3 rounded-lg border",
				statusConfig.bgColor,
			)}
		>
			<div className={cn("mt-0.5 flex-shrink-0", statusConfig.color)}>
				{isActive ? (
					<Loader2 className="h-5 w-5 animate-spin" />
				) : notification.status === "completed" ? (
					<CheckCircle className="h-5 w-5" />
				) : notification.status === "failed" ? (
					<AlertCircle className="h-5 w-5" />
				) : isAwaitingAction ? (
					<AlertTriangle className="h-5 w-5" />
				) : (
					<Icon className="h-5 w-5" />
				)}
			</div>
			<div className="flex-1 min-w-0">
				<span className="text-sm font-medium truncate block">
					{notification.title}
				</span>
				{notification.description && (
					<p className="text-xs text-muted-foreground mt-1">
						{notification.description}
					</p>
				)}
				{notification.error && (
					<p className="text-xs text-red-500 mt-1">
						{notification.error}
					</p>
				)}
				{/* Clickable file link for file-related notifications */}
				{hasFileLink && (
					<button
						onClick={() => onOpenFile(filePath, lineNumber)}
						className="flex items-center gap-1.5 text-xs text-blue-400 hover:text-blue-300 hover:underline mt-1.5 transition-colors"
					>
						<FileCode className="h-3 w-3" />
						{filePath}
					</button>
				)}
				{/* Progress bar for determinate progress */}
				{isActive && notification.percent !== null && (
					<Progress
						value={notification.percent}
						className="h-1.5 mt-2"
					/>
				)}
				{/* Action button for awaiting_action notifications */}
				{hasAction && (
					<Button
						size="sm"
						className="mt-2 h-7 text-xs"
						onClick={handleAction}
						disabled={isActionLoading}
					>
						{isActionLoading ? (
							<Loader2 className="h-3 w-3 mr-1 animate-spin" />
						) : (
							<Play className="h-3 w-3 mr-1" />
						)}
						{actionLabel}
					</Button>
				)}
				<p className="text-xs text-muted-foreground/60 mt-1">
					{new Date(notification.updatedAt).toLocaleString()}
				</p>
			</div>
			{/* Dismiss button - show for non-active states (completed, failed, awaiting_action).
			    Embedding-reindex while running gets a Cancel button instead — DELETE on the
			    notification sets the cancel flag the scheduler polls between batches. */}
			{!isActive && (
				<Button
					variant="ghost"
					size="icon"
					className="h-6 w-6 flex-shrink-0"
					onClick={onDismiss}
				>
					<X className="h-3 w-3" />
				</Button>
			)}
			{isActive && notification.category === "embedding_reindex" && (
				<Button
					variant="ghost"
					size="sm"
					className="h-6 px-2 text-xs flex-shrink-0"
					onClick={onDismiss}
				>
					Cancel
				</Button>
			)}
		</div>
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
		<div
			className={cn(
				"flex items-start gap-3 p-3 rounded-lg border",
				config.bgColor,
			)}
		>
			<Icon
				className={cn("h-5 w-5 mt-0.5 flex-shrink-0", config.color)}
			/>
			<div className="flex-1 min-w-0">
				<span className="text-sm font-medium truncate block">
					{alert.title}
				</span>
				<p className="text-xs text-muted-foreground mt-1">
					{alert.body}
				</p>
				<p className="text-xs text-muted-foreground/60 mt-1">
					{new Date(alert.createdAt).toLocaleString()}
				</p>
			</div>
			<Button
				variant="ghost"
				size="icon"
				className="h-6 w-6 flex-shrink-0"
				onClick={onDismiss}
			>
				<X className="h-3 w-3" />
			</Button>
		</div>
	);
}

export function NotificationCenter() {
	const [isOpen, setIsOpen] = useState(false);
	const navigate = useNavigate();
	const { notifications, dismiss, clearAll } = useNotifications();
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
				<Button variant="ghost" size="icon" className="relative">
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
			<PopoverContent className="w-96 p-0" align="end" sideOffset={8}>
				<div className="flex items-center justify-between px-4 py-3 border-b">
					<h3 className="font-semibold">Notifications</h3>
					{hasNotifications && (
						<Button
							variant="ghost"
							size="sm"
							className="h-8 text-xs"
							onClick={handleClearAll}
						>
							<Trash2 className="h-3 w-3 mr-1" />
							Clear all
						</Button>
					)}
				</div>
				<div className="h-[400px] overflow-y-auto">
					{!hasNotifications ? (
						<div className="flex flex-col items-center justify-center h-32 text-muted-foreground">
							<Bell className="h-8 w-8 mb-2 opacity-50" />
							<p className="text-sm">No notifications</p>
						</div>
					) : (
						<div className="p-2 space-y-2">
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
									<div className="border-t my-2" />
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
