import { EventSourceActions } from "./EventSourceActions";
import { PageScrollArea } from "@/components/layout/PageWorkspace";
import { useState, useCallback, useEffect } from "react";
import { useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import {
	ArrowLeft,
	Copy,
	Check,
	Webhook,
	Calendar,
	Zap,
	Globe,
	Building2,
	AlertTriangle,
	RefreshCw,
	CircleCheck,
	Clock3,
	UserRound,
	Radio,
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
	useResubscribeEventSource,
	useRotateWebhookSecret,
	type EventSourceType,
} from "@/services/events";
import { HmacSecretReveal } from "@/components/forms/HmacSecretReveal";
import { Switch } from "@/components/ui/switch";
import { SubscriptionsTable } from "./SubscriptionsTable";
import { EventsTable } from "./EventsTable";
import { EditEventSourceDialog } from "./EditEventSourceDialog";
import { MicrosoftGraphIcon } from "./MicrosoftGraphIcon";
import { getErrorMessage } from "@/lib/api-error";
import {
	getGraphSourceSummary,
	isMicrosoftGraphSource,
} from "@/lib/graph-source";
import { formatDistanceToNow } from "date-fns";

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

function SourceWebhookAddress({
	url,
	copied,
	onCopy,
}: {
	url: string;
	copied: boolean;
	onCopy: () => void;
}) {
	return (
		<div className="flex w-full min-w-0 flex-wrap items-start gap-2 rounded-[var(--bf-radius-surface)] border p-3">
			<code className="min-w-0 basis-full text-sm leading-6 [overflow-wrap:anywhere] sm:basis-0 sm:flex-1">
				{url}
			</code>
			<Button
				type="button"
				variant="outline"
				className="min-h-11 shrink-0"
				onClick={onCopy}
				aria-label="Copy webhook URL"
			>
				{copied ? (
					<Check className="h-4 w-4" />
				) : (
					<Copy className="h-4 w-4" />
				)}
				{copied ? "Copied" : "Copy"}
			</Button>
			<span role="status" className="sr-only">
				{copied ? "Webhook URL copied" : ""}
			</span>
		</div>
	);
}

export function EventSourceDetail({
	sourceId,
	onClose,
}: EventSourceDetailProps) {
	const { isPlatformAdmin } = useAuth();
	const { eventId } = useParams<{ eventId?: string }>();
	const queryClient = useQueryClient();
	const [copied, setCopied] = useState(false);
	const [actionError, setActionError] = useState<string | null>(null);
	const [deleteError, setDeleteError] = useState<string | null>(null);
	const [resubscribeError, setResubscribeError] = useState<string | null>(
		null,
	);
	useEffect(() => {
		if (!copied) return;
		const timer = setTimeout(() => setCopied(false), 2000);
		return () => clearTimeout(timer);
	}, [copied]);
	const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
	const [editDialogOpen, setEditDialogOpen] = useState(false);
	const [resubscribeDialogOpen, setResubscribeDialogOpen] = useState(false);
	const [rotateDialogOpen, setRotateDialogOpen] = useState(false);
	const [rotateError, setRotateError] = useState<string | null>(null);
	const [revealedSecret, setRevealedSecret] = useState<string | null>(null);

	const {
		data: source,
		isLoading,
		isError,
		isFetching,
		refetch,
	} = useEventSource(sourceId);
	const updateMutation = useUpdateEventSource();
	const resubscribeMutation = useResubscribeEventSource();
	const rotateMutation = useRotateWebhookSecret();

	// Toggle active status
	const handleToggleActive = async () => {
		if (!source || updateMutation.isPending) return;
		setActionError(null);
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
			setActionError("Could not update this source. Try again.");
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
			setActionError(null);
		} catch {
			setActionError(
				"Could not copy the URL. Select and copy the address, or try again.",
			);
		}
	};

	const handleDelete = async () => {
		if (deleteMutation.isPending) return;
		setDeleteError(null);
		try {
			await deleteMutation.mutateAsync({
				params: { path: { source_id: sourceId } },
			});
			setDeleteDialogOpen(false);
			toast.success("Event source deleted");
			onClose();
		} catch (error) {
			setDeleteError(
				getErrorMessage(error, "Failed to delete event source"),
			);
		}
	};

	const handleResubscribe = async () => {
		if (resubscribeMutation.isPending) return;
		setResubscribeError(null);
		try {
			await resubscribeMutation.mutateAsync({
				params: { path: { source_id: sourceId } },
			});
			setResubscribeDialogOpen(false);
			toast.success("Microsoft Graph subscription recreated");
		} catch (error) {
			setResubscribeError(
				getErrorMessage(error, "Failed to recreate Graph subscription"),
			);
		}
	};

	const handleRotateSecret = async () => {
		if (rotateMutation.isPending) return;
		setRotateError(null);
		try {
			const rotated = await rotateMutation.mutateAsync({
				params: { path: { source_id: sourceId } },
				body: {},
			});
			// Keep the one-time secret only in this dialog, not the mutation cache.
			rotateMutation.reset();
			setRevealedSecret(rotated.raw_secret ?? null);
		} catch (error) {
			setRotateError(
				getErrorMessage(error, "Failed to rotate the signing secret"),
			);
		}
	};

	const closeRotateDialog = () => {
		setRotateDialogOpen(false);
		setRevealedSecret(null);
		setRotateError(null);
	};

	if (isLoading) {
		return (
			<div className="min-w-0 flex flex-col gap-5">
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

	if (isError && !source)
		return (
			<div className="min-w-0 space-y-4 py-8">
				<p role="alert">Could not load this event source.</p>
				<Button
					variant="outline"
					className="min-h-11"
					disabled={isFetching}
					onClick={() => void refetch()}
				>
					Retry
				</Button>
				<Button variant="ghost" className="min-h-11" onClick={onClose}>
					Back to Event Sources
				</Button>
			</div>
		);
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

	const isGraph = isMicrosoftGraphSource(source);
	// Only the generic adapter verifies an HMAC signing secret.
	const isGenericWebhook =
		source.source_type === "webhook" &&
		(source.webhook?.adapter_name ?? "generic") === "generic";
	const graphSummary = getGraphSourceSummary(source);
	const graphStatus =
		graphSummary?.health === "connected"
			? {
					label: "Connected",
					icon: CircleCheck,
					className: "text-[var(--bf-success)]",
				}
			: graphSummary?.health === "expired"
				? {
						label: "Expired",
						icon: Clock3,
						className: "text-destructive",
					}
				: {
						label: "Needs attention",
						icon: AlertTriangle,
						className: "text-[var(--bf-warning)]",
					};
	const GraphStatusIcon = graphStatus.icon;

	return (
		<div className="min-w-0 flex flex-col gap-5 lg:min-h-0 lg:flex-1">
			{/* Header row */}
			<div className="flex min-w-0 flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
				<div className="flex min-w-0 items-start gap-3">
					<Button
						variant="ghost"
						size="icon"
						aria-label="Back to Event Sources"
						className="h-11 w-11 shrink-0"
						onClick={onClose}
					>
						<ArrowLeft className="h-4 w-4" />
					</Button>
					<div className="hidden shrink-0 items-center gap-3 pt-2 text-muted-foreground sm:flex">
						{isGraph ? (
							<MicrosoftGraphIcon className="h-5 w-5 text-[#1686d9]" />
						) : (
							getSourceTypeIcon(source.source_type)
						)}
					</div>
					<div className="min-w-0">
						<h1 className="text-2xl font-semibold tracking-tight [overflow-wrap:anywhere]">
							{source.name}
						</h1>
						<div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground [overflow-wrap:anywhere]">
							<span>
								{isGraph
									? "Microsoft Graph"
									: getSourceTypeLabel(source.source_type)}
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
				<div className="flex shrink-0 flex-wrap items-center gap-3">
					{isPlatformAdmin && (
						<Tooltip>
							<TooltipTrigger asChild>
								<div className="flex items-center gap-2">
									<Switch
										aria-label="Source active"
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
						className="h-11 w-11 shrink-0"
						disabled={isFetching}
						onClick={handleRefresh}
						aria-label="Refresh"
						title="Refresh"
					>
						<RefreshCw className="h-4 w-4" />
					</Button>
					{isPlatformAdmin && (
						<EventSourceActions
							source={source}
							onEdit={() => setEditDialogOpen(true)}
							onDelete={() => setDeleteDialogOpen(true)}
							onRotateSecret={
								isGenericWebhook
									? () => setRotateDialogOpen(true)
									: undefined
							}
						/>
					)}
				</div>
			</div>

			{isError && (
				<div
					role="alert"
					className="flex flex-wrap items-center gap-3 text-sm text-destructive"
				>
					Could not refresh this source. Showing the last loaded
					details.
					<Button
						className="min-h-11"
						variant="outline"
						disabled={isFetching}
						onClick={() => void refetch()}
					>
						Retry
					</Button>
				</div>
			)}
			{actionError && (
				<p role="alert" className="text-sm text-destructive">
					{actionError}
				</p>
			)}
			{/* Metadata badges row */}
			<div className="flex items-center gap-2 flex-wrap">
				{source.source_type === "topic" && source.event_type && (
					<Badge
						variant="outline"
						className="max-w-full whitespace-normal [overflow-wrap:anywhere] font-mono"
					>
						{source.event_type}
					</Badge>
				)}
				{source.webhook?.adapter_name && (
					<Badge
						variant="outline"
						className="max-w-full whitespace-normal [overflow-wrap:anywhere]"
					>
						{isGraph
							? "Microsoft Graph"
							: source.webhook.adapter_name}
					</Badge>
				)}
				<Badge
					variant="outline"
					className="max-w-full whitespace-normal [overflow-wrap:anywhere]"
				>
					{source.subscription_count || 0} subscription
					{(source.subscription_count || 0) !== 1 ? "s" : ""}
				</Badge>
				<Badge
					variant="outline"
					className="max-w-full whitespace-normal [overflow-wrap:anywhere]"
				>
					{source.event_count_24h || 0} event
					{(source.event_count_24h || 0) !== 1 ? "s" : ""} (24h)
				</Badge>
				{source.schedule && (
					<>
						<Badge
							variant="outline"
							className="max-w-full whitespace-normal [overflow-wrap:anywhere] font-mono"
						>
							{source.schedule.cron_expression}
						</Badge>
						<Badge
							variant="outline"
							className="max-w-full whitespace-normal [overflow-wrap:anywhere]"
						>
							{source.schedule.timezone}
						</Badge>
						<Badge
							variant={
								source.schedule.enabled
									? "default"
									: "secondary"
							}
							className={
								source.schedule.enabled
									? "bg-[var(--bf-success-soft)] text-[var(--bf-success)]"
									: "bg-muted text-muted-foreground"
							}
						>
							{source.schedule.enabled ? "Enabled" : "Disabled"}
						</Badge>
					</>
				)}
				{webhookUrl && (
					<SourceWebhookAddress
						url={webhookUrl}
						copied={copied}
						onCopy={handleCopyUrl}
					/>
				)}
			</div>

			{graphSummary && source.webhook && (
				<section
					aria-labelledby="graph-provider-heading"
					className="border-y bg-muted/20 px-4 py-4"
				>
					<div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
						<div className="min-w-0 flex-1">
							<div className="flex flex-wrap items-center gap-2">
								<MicrosoftGraphIcon className="h-5 w-5 text-[#1686d9]" />
								<h2
									id="graph-provider-heading"
									className="font-semibold"
								>
									Microsoft Graph
								</h2>
								<span
									className={`inline-flex items-center gap-1.5 text-sm font-medium ${graphStatus.className}`}
								>
									<GraphStatusIcon className="h-4 w-4" />
									{graphStatus.label}
								</span>
							</div>
							<p className="mt-1 text-sm text-muted-foreground">
								Provider subscription managed by Bifrost and
								renewed automatically.
							</p>

							<dl className="mt-4 grid gap-x-8 gap-y-4 sm:grid-cols-2 xl:grid-cols-4">
								<div className="min-w-0">
									<dt className="flex items-center gap-1.5 text-sm font-medium text-muted-foreground">
										<UserRound className="h-3.5 w-3.5" />{" "}
										User
									</dt>
									<dd
										className="mt-1 [overflow-wrap:anywhere] text-sm font-medium"
										title={
											graphSummary.userSecondary ??
											graphSummary.userLabel
										}
									>
										{graphSummary.userLabel}
									</dd>
									{graphSummary.userSecondary && (
										<div className="[overflow-wrap:anywhere] text-sm text-muted-foreground">
											{graphSummary.userSecondary}
										</div>
									)}
								</div>
								<div className="min-w-0">
									<dt className="flex items-center gap-1.5 text-sm font-medium text-muted-foreground">
										<Radio className="h-3.5 w-3.5" />{" "}
										Resource
									</dt>
									<dd className="mt-1 text-sm font-medium">
										{graphSummary.resourceLabel}
									</dd>
									{graphSummary.resourcePath && (
										<div
											className="[overflow-wrap:anywhere] font-mono text-sm text-muted-foreground"
											title={graphSummary.resourcePath}
										>
											{graphSummary.resourcePath}
										</div>
									)}
								</div>
								<div>
									<dt className="text-sm font-medium text-muted-foreground">
										Changes
									</dt>
									<dd className="mt-1 text-sm font-medium capitalize">
										{graphSummary.changeLabel}
									</dd>
								</div>
								<div>
									<dt className="flex items-center gap-1.5 text-sm font-medium text-muted-foreground">
										<Clock3 className="h-3.5 w-3.5" />{" "}
										Provider expiry
									</dt>
									<dd className="mt-1 text-sm font-medium">
										{source.webhook.expires_at
											? formatDistanceToNow(
													new Date(
														source.webhook
															.expires_at,
													),
													{ addSuffix: true },
												)
											: "Not registered"}
									</dd>
								</div>
							</dl>
						</div>

						{isPlatformAdmin && (
							<Button
								variant="outline"
								onClick={() => setResubscribeDialogOpen(true)}
								disabled={resubscribeMutation.isPending}
								className="min-h-11 shrink-0"
							>
								<RefreshCw
									className={`mr-2 h-4 w-4 ${resubscribeMutation.isPending ? "motion-safe:animate-spin" : ""}`}
								/>
								Resubscribe
							</Button>
						)}
					</div>
				</section>
			)}

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
				<TabsList className="h-auto w-fit max-w-full">
					<TabsTrigger className="min-h-11" value="subscriptions">
						Subscriptions
					</TabsTrigger>
					<TabsTrigger className="min-h-11" value="events">
						Events
					</TabsTrigger>
				</TabsList>

				<TabsContent
					value="subscriptions"
					className="mt-4 flex min-h-0 flex-1 flex-col"
				>
					<PageScrollArea className="lg:flex lg:flex-col lg:overflow-hidden">
						<SubscriptionsTable sourceId={sourceId} />
					</PageScrollArea>
				</TabsContent>

				<TabsContent
					value="events"
					className="mt-4 flex-1 flex flex-col min-h-0"
				>
					<PageScrollArea className="lg:flex lg:flex-col lg:overflow-hidden">
						<EventsTable
							sourceId={sourceId}
							source={source}
							initialEventId={eventId}
						/>
					</PageScrollArea>
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
				onOpenChange={(next) => {
					if (!deleteMutation.isPending) {
						setDeleteDialogOpen(next);
						if (!next) setDeleteError(null);
					}
				}}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Delete Event Source</AlertDialogTitle>
						<AlertDialogDescription>
							Are you sure you want to delete "{source.name}"?
							This will also delete all subscriptions and event
							history. Provider-managed sources are removed from
							the provider first; if that fails, the Bifrost
							source is retained so you can retry. This action
							cannot be undone.
						</AlertDialogDescription>
					</AlertDialogHeader>
					{deleteError && (
						<p
							role="alert"
							className="text-sm text-destructive [overflow-wrap:anywhere]"
						>
							{deleteError}
						</p>
					)}
					<AlertDialogFooter>
						<AlertDialogCancel
							className="min-h-11"
							disabled={deleteMutation.isPending}
						>
							Cancel
						</AlertDialogCancel>
						<AlertDialogAction
							onClick={(event) => {
								event.preventDefault();
								void handleDelete();
							}}
							disabled={deleteMutation.isPending}
							className="min-h-11 bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							Delete
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>

			<AlertDialog
				open={rotateDialogOpen}
				onOpenChange={(next) => {
					if (!next && !rotateMutation.isPending) closeRotateDialog();
				}}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>
							{revealedSecret
								? "New signing secret"
								: "Rotate signing secret?"}
						</AlertDialogTitle>
						<AlertDialogDescription>
							{revealedSecret
								? "Set this secret in the sending service. Requests signed with the previous secret are now rejected."
								: "Bifrost will generate a new signing secret and show it once. Requests signed with the current secret are rejected until you update the sending service."}
						</AlertDialogDescription>
					</AlertDialogHeader>
					{revealedSecret && (
						<HmacSecretReveal
							value={revealedSecret}
							onDismiss={closeRotateDialog}
							label="New webhook signing secret"
						/>
					)}
					{rotateError && (
						<p
							role="alert"
							className="text-sm text-destructive [overflow-wrap:anywhere]"
						>
							{rotateError}
						</p>
					)}
					{!revealedSecret && (
						<AlertDialogFooter>
							<AlertDialogCancel
								className="min-h-11"
								disabled={rotateMutation.isPending}
							>
								Cancel
							</AlertDialogCancel>
							<AlertDialogAction
								className="min-h-11"
								onClick={(event) => {
									event.preventDefault();
									void handleRotateSecret();
								}}
								disabled={rotateMutation.isPending}
							>
								{rotateMutation.isPending
									? "Rotating…"
									: "Rotate secret"}
							</AlertDialogAction>
						</AlertDialogFooter>
					)}
				</AlertDialogContent>
			</AlertDialog>

			<AlertDialog
				open={resubscribeDialogOpen}
				onOpenChange={(next) => {
					if (!resubscribeMutation.isPending) {
						setResubscribeDialogOpen(next);
						if (!next) setResubscribeError(null);
					}
				}}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>
							Resubscribe to Microsoft Graph?
						</AlertDialogTitle>
						<AlertDialogDescription>
							Bifrost will remove the existing Graph registration
							and create a new one using the current organization,
							integration, user, resource, and callback URL. Use
							this when events stop arriving or the provider
							subscription has expired.
						</AlertDialogDescription>
					</AlertDialogHeader>
					{resubscribeError && (
						<p
							role="alert"
							className="text-sm text-destructive [overflow-wrap:anywhere]"
						>
							{resubscribeError}
						</p>
					)}
					<AlertDialogFooter>
						<AlertDialogCancel
							className="min-h-11"
							disabled={resubscribeMutation.isPending}
						>
							Cancel
						</AlertDialogCancel>
						<AlertDialogAction
							className="min-h-11"
							onClick={(event) => {
								event.preventDefault();
								void handleResubscribe();
							}}
							disabled={resubscribeMutation.isPending}
						>
							{resubscribeMutation.isPending
								? "Resubscribing…"
								: "Resubscribe"}
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</div>
	);
}
