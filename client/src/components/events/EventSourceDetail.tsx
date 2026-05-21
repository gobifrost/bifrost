import { useState, useCallback } from "react";
import { useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import {
	ArrowLeft,
	Copy,
	Check,
	Trash2,
	Webhook,
	Calendar,
	Zap,
	Globe,
	Building2,
	AlertTriangle,
	RefreshCw,
	Pencil,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
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
	Tooltip,
	TooltipContent,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import { toast } from "sonner";
import { useAuth } from "@/contexts/AuthContext";
import {
	useEventSource,
	useDeleteEventSource,
	useUpdateEventSource,
	type EventSourceType,
} from "@/services/events";
import { Switch } from "@/components/ui/switch";
import { SubscriptionsTable } from "./SubscriptionsTable";
import { EventsTable } from "./EventsTable";
import { EditEventSourceDialog } from "./EditEventSourceDialog";

interface EventSourceDetailProps {
	sourceId: string;
	onClose: () => void;
}

function getSourceTypeIcon(type: EventSourceType) {
	switch (type) {
		case "webhook":
			return <Webhook className="h-5 w-5" />;
		case "schedule":
			return <Calendar className="h-5 w-5" />;
		case "topic":
			return <Zap className="h-5 w-5" />;
	}
}

function getSourceTypeLabel(type: EventSourceType) {
	switch (type) {
		case "webhook":
			return "Webhook";
		case "schedule":
			return "Schedule";
		case "topic":
			return "Topic";
	}
}

export function EventSourceDetail({
	sourceId,
	onClose,
}: EventSourceDetailProps) {
	const { isPlatformAdmin } = useAuth();
	const { eventId } = useParams<{ eventId?: string }>();
	const queryClient = useQueryClient();
	const [copied, setCopied] = useState(false);
	const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
	const [editDialogOpen, setEditDialogOpen] = useState(false);

	const { data: source, isLoading, refetch } = useEventSource(sourceId);
	const updateMutation = useUpdateEventSource();

	// Toggle active status
	const handleToggleActive = async () => {
		if (!source) return;
		try {
			await updateMutation.mutateAsync({
				params: { path: { source_id: sourceId } },
				body: { is_active: !source.is_active },
			});
			toast.success(
				source.is_active
					? "Event source deactivated"
					: "Event source activated",
			);
		} catch {
			toast.error("Failed to update event source");
		}
	};

	// Refresh both source details and events list
	const handleRefresh = useCallback(() => {
		refetch();
		// Invalidate events query to refresh the events list
		queryClient.invalidateQueries({
			queryKey: ["get", "/api/events/sources/{source_id}/events"],
		});
	}, [refetch, queryClient]);
	const deleteMutation = useDeleteEventSource();

	// Build full webhook URL from path
	const webhookUrl = source?.webhook?.callback_url
		? `${window.location.origin}${source.webhook.callback_url}`
		: null;

	const handleCopyUrl = async () => {
		if (!webhookUrl) return;

		try {
			await navigator.clipboard.writeText(webhookUrl);
			setCopied(true);
			toast.success("Webhook URL copied to clipboard");
			setTimeout(() => setCopied(false), 2000);
		} catch {
			toast.error("Failed to copy URL");
		}
	};

	const handleDelete = async () => {
		try {
			await deleteMutation.mutateAsync({
				params: { path: { source_id: sourceId } },
			});
			toast.success("Event source deleted");
			onClose();
		} catch {
			toast.error("Failed to delete event source");
		}
	};

	if (isLoading) {
		return (
			<div className="h-[calc(100vh-8rem)] flex flex-col space-y-4">
				<div className="flex items-center gap-4">
					<Skeleton className="h-10 w-10" />
					<div className="space-y-2">
						<Skeleton className="h-6 w-48" />
						<Skeleton className="h-4 w-32" />
					</div>
				</div>
				<Skeleton className="h-10 w-full" />
				<Skeleton className="flex-1" />
			</div>
		);
	}

	if (!source) {
		return (
			<div className="flex flex-col items-center justify-center py-12">
				<AlertTriangle className="h-12 w-12 text-muted-foreground mb-4" />
				<h3 className="text-lg font-semibold mb-2">
					Event Source Not Found
				</h3>
				<p className="text-muted-foreground mb-4">
					The event source may have been deleted.
				</p>
				<Button variant="outline" onClick={onClose}>
					<ArrowLeft className="h-4 w-4 mr-2" />
					Back to Event Sources
				</Button>
			</div>
		);
	}

	return (
		<div className="h-[calc(100vh-8rem)] flex flex-col space-y-4">
			{/* Header row */}
			<div className="flex items-center justify-between">
				<div className="flex items-center gap-3">
					<Button variant="ghost" size="icon" onClick={onClose}>
						<ArrowLeft className="h-4 w-4" />
					</Button>
					<div className="flex items-center gap-3 text-muted-foreground">
						{getSourceTypeIcon(source.source_type)}
					</div>
					<div>
						<h1 className="text-2xl font-bold tracking-tight">
							{source.name}
						</h1>
						<div className="flex items-center gap-2 text-sm text-muted-foreground">
							<span>
								{getSourceTypeLabel(source.source_type)}
							</span>
							<span>·</span>
							{source.organization_id ? (
								<span className="flex items-center gap-1">
									<Building2 className="h-3 w-3" />
									{source.organization_name || "Organization"}
								</span>
							) : (
								<span className="flex items-center gap-1">
									<Globe className="h-3 w-3" />
									Global
								</span>
							)}
						</div>
					</div>
				</div>
				<div className="flex items-center gap-3">
					{isPlatformAdmin && (
						<Tooltip>
							<TooltipTrigger asChild>
								<div className="flex items-center gap-2">
									<Switch
										checked={source.is_active}
										onCheckedChange={handleToggleActive}
										disabled={updateMutation.isPending}
									/>
									<span className="text-sm text-muted-foreground">
										{source.is_active
											? "Active"
											: "Inactive"}
									</span>
								</div>
							</TooltipTrigger>
							<TooltipContent>
								{source.is_active
									? "Click to deactivate - webhooks will be rejected"
									: "Click to activate - webhooks will be processed"}
							</TooltipContent>
						</Tooltip>
					)}
					<Button
						variant="outline"
						size="icon"
						onClick={handleRefresh}
						title="Refresh"
					>
						<RefreshCw className="h-4 w-4" />
					</Button>
					{isPlatformAdmin && (
						<>
							<Button
								variant="outline"
								size="icon"
								onClick={() => setEditDialogOpen(true)}
								title="Edit"
							>
								<Pencil className="h-4 w-4" />
							</Button>
							<Button
								variant="outline"
								size="icon"
								onClick={() => setDeleteDialogOpen(true)}
								title="Delete"
							>
								<Trash2 className="h-4 w-4" />
							</Button>
						</>
					)}
				</div>
			</div>

			{/* Metadata badges row */}
			<div className="flex items-center gap-2 flex-wrap">
				{source.source_type === "topic" && source.event_type && (
					<Badge variant="outline" className="font-mono">
						{source.event_type}
					</Badge>
				)}
				{source.webhook?.adapter_name && (
					<Badge variant="outline">
						{source.webhook.adapter_name}
					</Badge>
				)}
				<Badge variant="outline">
					{source.subscription_count || 0} subscription
					{(source.subscription_count || 0) !== 1 ? "s" : ""}
				</Badge>
				<Badge variant="outline">
					{source.event_count_24h || 0} event
					{(source.event_count_24h || 0) !== 1 ? "s" : ""} (24h)
				</Badge>
				{source.schedule && (
					<>
						<Badge variant="outline" className="font-mono">
							{source.schedule.cron_expression}
						</Badge>
						<Badge variant="outline">
							{source.schedule.timezone}
						</Badge>
						<Badge
							variant={source.schedule.enabled ? "default" : "secondary"}
							className={
								source.schedule.enabled
									? "bg-green-100 text-green-800 hover:bg-green-100 dark:bg-green-900 dark:text-green-200"
									: "bg-red-100 text-red-800 hover:bg-red-100 dark:bg-red-900 dark:text-red-200"
							}
						>
							{source.schedule.enabled ? "Enabled" : "Disabled"}
						</Badge>
					</>
				)}
				{webhookUrl && (
					<Tooltip>
						<TooltipTrigger asChild>
							<Badge
								variant="outline"
								className="cursor-pointer hover:bg-accent min-w-0"
								onClick={handleCopyUrl}
							>
								{copied ? (
									<Check className="h-3 w-3 mr-1 flex-shrink-0 text-green-500" />
								) : (
									<Copy className="h-3 w-3 mr-1 flex-shrink-0" />
								)}
								<span className="truncate font-mono text-xs">
									{webhookUrl}
								</span>
							</Badge>
						</TooltipTrigger>
						<TooltipContent side="bottom" className="max-w-lg">
							<p className="text-xs mb-1">Click to copy</p>
							<code className="text-xs break-all">
								{webhookUrl}
							</code>
						</TooltipContent>
					</Tooltip>
				)}
			</div>

			{/* Error message if present */}
			{source.error_message && (
				<div className="flex items-start gap-2 p-3 bg-destructive/10 rounded-lg">
					<AlertTriangle className="h-4 w-4 text-destructive mt-0.5" />
					<p className="text-sm text-destructive">
						{source.error_message}
					</p>
				</div>
			)}

			{/* Tabs section - takes remaining space */}
			<Tabs
				defaultValue="events"
				className="flex-1 flex flex-col min-h-0"
			>
				<TabsList className="w-fit">
					<TabsTrigger value="subscriptions">
						Subscriptions
					</TabsTrigger>
					<TabsTrigger value="events">Events</TabsTrigger>
				</TabsList>

				<TabsContent value="subscriptions" className="mt-4 flex-1">
					<SubscriptionsTable sourceId={sourceId} />
				</TabsContent>

				<TabsContent
					value="events"
					className="mt-4 flex-1 flex flex-col min-h-0"
				>
					<EventsTable sourceId={sourceId} initialEventId={eventId} />
				</TabsContent>
			</Tabs>

			{/* Edit Dialog */}
			<EditEventSourceDialog
				source={source}
				open={editDialogOpen}
				onOpenChange={setEditDialogOpen}
			/>

			{/* Delete Confirmation Dialog */}
			<AlertDialog
				open={deleteDialogOpen}
				onOpenChange={setDeleteDialogOpen}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Delete Event Source</AlertDialogTitle>
						<AlertDialogDescription>
							Are you sure you want to delete "{source.name}"?
							This will also delete all subscriptions and event
							history. This action cannot be undone.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction
							onClick={handleDelete}
							className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							Delete
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</div>
	);
}
