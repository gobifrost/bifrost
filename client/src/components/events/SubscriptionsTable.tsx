import { useId, useState } from "react";
import {
	Bot,
	CheckCircle2,
	GitBranch,
	Loader2,
	Plus,
	XCircle,
	Zap,
} from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
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
import { Button } from "@/components/ui/button";
import { RecordActionsMenu } from "@/components/common/RecordActionsMenu";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
	useDeleteSubscription,
	useSubscriptions,
	useUpdateSubscription,
	type EventSubscription,
} from "@/services/events";
import { CreateSubscriptionDialog } from "./CreateSubscriptionDialog";
import { EditSubscriptionDialog } from "./EditSubscriptionDialog";

interface SubscriptionsTableProps {
	sourceId: string;
	canManage?: boolean;
}

function getSubscriptionMeta(subscription: EventSubscription) {
	const targetType = subscription.target_type || "workflow";
	const isAgent = targetType === "agent";
	const resourceName = isAgent
		? subscription.agent_name || subscription.agent_id || "Unknown Agent"
		: subscription.workflow_name ||
			subscription.workflow_id ||
			"Unknown Workflow";
	const successRate =
		subscription.delivery_count > 0
			? Math.round(
					(subscription.success_count / subscription.delivery_count) *
						100,
				)
			: null;

	return { isAgent, resourceName, successRate };
}

function SubscriptionKindBadge({ isAgent }: { isAgent: boolean }) {
	return (
		<Badge variant={isAgent ? "secondary" : "outline"} className="gap-1">
			{isAgent ? (
				<Bot aria-hidden="true" className="size-3" />
			) : (
				<GitBranch aria-hidden="true" className="size-3" />
			)}
			{isAgent ? "Agent" : "Workflow"}
		</Badge>
	);
}

function SubscriptionEventType({
	eventType,
}: {
	eventType: string | null | undefined;
}) {
	if (!eventType) {
		return <span className="text-muted-foreground">All events</span>;
	}

	return (
		<span className="block max-w-full font-mono text-sm leading-6 whitespace-normal [overflow-wrap:anywhere] text-foreground">
			{eventType}
		</span>
	);
}

function SubscriptionActiveControl({
	subscription,
	onToggleActive,
	pending,
	canManage,
}: {
	subscription: EventSubscription;
	onToggleActive: (subscription: EventSubscription) => void;
	pending: boolean;
	canManage: boolean;
}) {
	const labelId = useId();
	const switchId = useId();

	return (
		<div
			className="inline-flex min-h-11 items-center gap-2"
			onClick={(event) => event.stopPropagation()}
		>
			<label
				id={labelId}
				htmlFor={switchId}
				className="inline-flex min-h-11 cursor-pointer select-none items-center text-xs font-medium uppercase tracking-wide text-muted-foreground"
				onClick={(event) => {
					if (pending || !canManage) return;
					event.preventDefault();
					onToggleActive(subscription);
				}}
			>
				<span className="sr-only">Subscription status</span>
				<span aria-hidden="true">
					{subscription.is_active ? "Active" : "Inactive"}
				</span>
			</label>
			<Switch
				id={switchId}
				aria-labelledby={labelId}
				checked={subscription.is_active}
				onCheckedChange={() => onToggleActive(subscription)}
				onClick={(event) => event.stopPropagation()}
				disabled={pending || !canManage}
				className="after:-inset-y-3"
			/>
		</div>
	);
}

function SubscriptionActionsMenu({
	subscription,
	onOpenEdit,
	onOpenDelete,
}: {
	subscription: EventSubscription;
	onOpenEdit: (subscription: EventSubscription) => void;
	onOpenDelete: (subscription: EventSubscription) => void;
}) {
	return (
		<RecordActionsMenu
			label={`${getSubscriptionMeta(subscription).resourceName} actions`}
		>
			<DropdownMenuItem
				className="min-h-11"
				onSelect={() => onOpenEdit(subscription)}
			>
				Edit
			</DropdownMenuItem>
			<DropdownMenuItem
				variant="destructive"
				className="min-h-11"
				onSelect={() => onOpenDelete(subscription)}
			>
				Delete
			</DropdownMenuItem>
		</RecordActionsMenu>
	);
}

function SubscriptionResourceButton({
	subscription,
	onOpenEdit,
}: {
	subscription: EventSubscription;
	onOpenEdit: (subscription: EventSubscription) => void;
}) {
	const { resourceName } = getSubscriptionMeta(subscription);

	return (
		<button
			type="button"
			className="min-h-11 w-full min-w-0 rounded-[var(--bf-radius-control)] text-left font-medium leading-6 text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
			onClick={(event) => {
				event.stopPropagation();
				onOpenEdit(subscription);
			}}
			aria-label={`Edit subscription for ${resourceName}`}
		>
			<span className="block [overflow-wrap:anywhere]">
				{resourceName}
			</span>
		</button>
	);
}

function SubscriptionLoadError({
	cached,
	pending,
	onRetry,
}: {
	cached: boolean;
	pending: boolean;
	onRetry: () => void;
}) {
	return (
		<div
			role="alert"
			className="flex flex-col gap-3 rounded-[var(--bf-radius-control)] border border-[var(--bf-warning)]/20 bg-[var(--bf-warning-soft)] p-4 text-sm sm:flex-row sm:items-center sm:justify-between"
		>
			<div className="space-y-1">
				<p className="font-medium text-foreground">
					Could not load subscriptions.
				</p>
				<p className="text-muted-foreground">
					{cached
						? "Previously loaded subscriptions are still shown."
						: "Retry to continue."}
				</p>
			</div>
			<Button
				type="button"
				variant="outline"
				className="min-h-11 shrink-0 gap-2"
				disabled={pending}
				onClick={onRetry}
			>
				{pending ? (
					<Loader2
						aria-hidden="true"
						className="size-4 animate-spin motion-reduce:animate-none"
					/>
				) : null}
				{pending ? "Retrying…" : "Retry subscriptions"}
			</Button>
		</div>
	);
}

function SubscriptionDesktopRow({
	subscription,
	onOpenEdit,
	onOpenDelete,
	onToggleActive,
	pending,
	canManage,
}: {
	subscription: EventSubscription;
	onOpenEdit: (subscription: EventSubscription) => void;
	onOpenDelete: (subscription: EventSubscription) => void;
	onToggleActive: (subscription: EventSubscription) => void;
	pending: boolean;
	canManage: boolean;
}) {
	const { isAgent, successRate } = getSubscriptionMeta(subscription);

	return (
		<DataTableRow
			clickable={canManage}
			onClick={canManage ? () => onOpenEdit(subscription) : undefined}
		>
			<DataTableCell>
				<SubscriptionKindBadge isAgent={isAgent} />
			</DataTableCell>
			<DataTableCell className="font-medium [overflow-wrap:anywhere]">
				<SubscriptionResourceButton
					subscription={subscription}
					onOpenEdit={onOpenEdit}
				/>
			</DataTableCell>
			<DataTableCell>
				<SubscriptionEventType eventType={subscription.event_type} />
			</DataTableCell>
			<DataTableCell className="text-right">
				{subscription.delivery_count || 0}
			</DataTableCell>
			<DataTableCell className="text-right">
				{successRate !== null ? (
					<div className="flex items-center justify-end gap-1">
						{successRate >= 90 ? (
							<CheckCircle2
								aria-hidden="true"
								className="size-4 text-[var(--bf-success)]"
							/>
						) : successRate < 50 ? (
							<XCircle
								aria-hidden="true"
								className="size-4 text-destructive"
							/>
						) : null}
						<span
							className={
								successRate >= 90
									? "text-[var(--bf-success)]"
									: successRate < 50
										? "text-destructive"
										: undefined
							}
						>
							{successRate}%
						</span>
					</div>
				) : (
					<span className="text-muted-foreground">—</span>
				)}
			</DataTableCell>
			{canManage && (
				<>
					<DataTableCell className="text-right">
						<SubscriptionActiveControl
							subscription={subscription}
							onToggleActive={onToggleActive}
							pending={pending}
									canManage={canManage}
						/>
					</DataTableCell>
					<DataTableCell className="text-right">
						<SubscriptionActionsMenu
							subscription={subscription}
							onOpenEdit={onOpenEdit}
							onOpenDelete={onOpenDelete}
						/>
					</DataTableCell>
				</>
			)}
		</DataTableRow>
	);
}

function SubscriptionMobileRecord({
	subscription,
	onOpenEdit,
	onOpenDelete,
	onToggleActive,
	pending,
	canManage,
}: {
	subscription: EventSubscription;
	onOpenEdit: (subscription: EventSubscription) => void;
	onOpenDelete: (subscription: EventSubscription) => void;
	onToggleActive: (subscription: EventSubscription) => void;
	pending: boolean;
	canManage: boolean;
}) {
	const { isAgent, successRate } = getSubscriptionMeta(subscription);

	return (
		<li className="space-y-4 rounded-[var(--bf-radius-surface)] border border-border bg-card p-[var(--bf-surface-pad)] shadow-sm">
			<div className="flex items-start justify-between gap-3">
				<SubscriptionKindBadge isAgent={isAgent} />
				{canManage && <div className="text-right">
					<SubscriptionActiveControl
						subscription={subscription}
						onToggleActive={onToggleActive}
						pending={pending}
							canManage={canManage}
					/>
				</div>}
			</div>

			<div className="flex items-start justify-between gap-3">
				{canManage ? <SubscriptionResourceButton
					subscription={subscription}
					onOpenEdit={onOpenEdit}
				/> : <span className="font-medium">{getSubscriptionMeta(subscription).resourceName}</span>}
				{canManage && <div className="shrink-0">
					<SubscriptionActionsMenu
						subscription={subscription}
						onOpenEdit={onOpenEdit}
						onOpenDelete={onOpenDelete}
					/>
				</div>}
			</div>

			<dl className="grid gap-3 sm:grid-cols-2">
				<div className="min-w-0 space-y-1">
					<dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
						Event Type Filter
					</dt>
					<dd className="min-w-0 text-sm [overflow-wrap:anywhere] whitespace-normal text-foreground">
						<SubscriptionEventType
							eventType={subscription.event_type}
						/>
					</dd>
				</div>
				<div className="min-w-0 space-y-1">
					<dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
						Deliveries
					</dt>
					<dd className="text-sm font-medium text-foreground">
						{subscription.delivery_count || 0}
					</dd>
				</div>
				<div className="min-w-0 space-y-1">
					<dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
						Success Rate
					</dt>
					<dd className="text-sm font-medium text-foreground">
						{successRate !== null ? (
							<span
								className={
									successRate >= 90
										? "text-[var(--bf-success)]"
										: successRate < 50
											? "text-destructive"
											: undefined
								}
							>
								{successRate}%
							</span>
						) : (
							<span className="text-muted-foreground">—</span>
						)}
					</dd>
				</div>
			</dl>
		</li>
	);
}

export function SubscriptionsTable({
	sourceId,
	canManage = false,
}: SubscriptionsTableProps) {
	const [createDialogOpen, setCreateDialogOpen] = useState(false);
	const [editDialogOpen, setEditDialogOpen] = useState(false);
	const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
	const [selectedSubscription, setSelectedSubscription] =
		useState<EventSubscription | null>(null);
	const [deleteError, setDeleteError] = useState<string | null>(null);

	const { data, isLoading, isError, isFetching, refetch } =
		useSubscriptions(sourceId);
	const deleteMutation = useDeleteSubscription();
	const updateMutation = useUpdateSubscription();

	const subscriptions = data?.items || [];
	const hasCachedSubscriptions = data !== undefined;

	const handleOpenEdit = (subscription: EventSubscription) => {
		if (!canManage) return;
		setSelectedSubscription(subscription);
		setEditDialogOpen(true);
	};

	const handleToggleActive = async (subscription: EventSubscription) => {
		if (!canManage) return;
		try {
			await updateMutation.mutateAsync({
				params: {
					path: {
						source_id: sourceId,
						subscription_id: subscription.id,
					},
				},
				body: { is_active: !subscription.is_active },
			});
			toast.success(
				subscription.is_active
					? "Subscription deactivated"
					: "Subscription activated",
			);
		} catch {
			toast.error("Failed to update subscription");
		}
	};

	const handleOpenDelete = (subscription: EventSubscription) => {
		if (!canManage) return;
		setSelectedSubscription(subscription);
		setDeleteError(null);
		setDeleteDialogOpen(true);
	};

	const handleConfirmDelete = async () => {
		if (!canManage || !selectedSubscription) return;

		setDeleteError(null);
		try {
			await deleteMutation.mutateAsync({
				params: {
					path: {
						source_id: sourceId,
						subscription_id: selectedSubscription.id,
					},
				},
			});
			toast.success("Subscription deleted");
			setDeleteDialogOpen(false);
			setSelectedSubscription(null);
			void refetch();
		} catch {
			const message =
				"Could not delete this subscription. Your selection is still here. Try again.";
			setDeleteError(message);
			toast.error("Failed to delete subscription");
		}
	};

	const handleCreateSuccess = () => {
		setCreateDialogOpen(false);
		void refetch();
	};

	const handleEditClose = (open: boolean) => {
		setEditDialogOpen(open);
		if (!open) setSelectedSubscription(null);
	};

	const handleDeleteDialogOpenChange = (open: boolean) => {
		if (!open && deleteMutation.isPending) return;
		setDeleteDialogOpen(open);
		if (!open) {
			setSelectedSubscription(null);
			setDeleteError(null);
		}
	};

	if (isLoading && !hasCachedSubscriptions) {
		return (
			<div className="space-y-3">
				{[...Array(3)].map((_, index) => (
					<Skeleton key={index} className="h-12 w-full" />
				))}
			</div>
		);
	}

	if (isError && !hasCachedSubscriptions) {
		return (
			<div className="space-y-4">
				<p className="text-sm text-muted-foreground">
					Workflows and agents triggered when events are received
				</p>
				<SubscriptionLoadError
					cached={false}
					pending={isFetching}
					onRetry={() => void refetch()}
				/>
			</div>
		);
	}

	return (
		<>
			<div className="@container/subscriptions flex min-w-0 flex-col lg:min-h-0 lg:flex-1">
				<div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
					<p className="text-sm text-muted-foreground">
						Workflows and agents triggered when events are received
					</p>
					{canManage && <Button
						type="button"
						className="min-h-11 gap-2"
						onClick={() => setCreateDialogOpen(true)}
					>
						<Plus aria-hidden="true" className="size-4" />
						Add Subscription
					</Button>}
				</div>

				{isError && hasCachedSubscriptions ? (
					<div className="mb-4">
						<SubscriptionLoadError
							cached
							pending={isFetching}
							onRetry={() => void refetch()}
						/>
					</div>
				) : null}

				{subscriptions.length === 0 ? (
					<div className="flex flex-col items-center justify-center py-12">
						<Zap
							aria-hidden="true"
							className="mb-4 size-12 text-muted-foreground"
						/>
						<h3 className="mb-2 text-lg font-semibold">
							No Subscriptions
						</h3>
						<p className="mb-4 text-center text-muted-foreground">
							Add a subscription to trigger workflows or agents
							when events arrive.
						</p>
						{canManage && <Button
							type="button"
							className="min-h-11 gap-2"
							onClick={() => setCreateDialogOpen(true)}
						>
							<Plus aria-hidden="true" className="size-4" />
							Add Subscription
						</Button>}
					</div>
				) : (
					<>
						<div className="hidden min-h-0 flex-1 @[64rem]/subscriptions:block">
							<DataTable className="max-h-full">
								<DataTableHeader>
									<DataTableRow>
										<DataTableHead className="w-[100px]">
											Type
										</DataTableHead>
										<DataTableHead>Resource</DataTableHead>
										<DataTableHead>
											Event Type Filter
										</DataTableHead>
										<DataTableHead className="text-right">
											Deliveries
										</DataTableHead>
										<DataTableHead className="text-right">
											Success Rate
										</DataTableHead>
										{canManage && <>
											<DataTableHead className="w-[120px] text-right">Status</DataTableHead>
											<DataTableHead className="w-[120px] text-right" />
										</>}
									</DataTableRow>
								</DataTableHeader>
								<DataTableBody>
									{subscriptions.map((subscription) => (
										<SubscriptionDesktopRow
											key={subscription.id}
											subscription={subscription}
											onOpenEdit={handleOpenEdit}
											onOpenDelete={handleOpenDelete}
											onToggleActive={handleToggleActive}
											pending={updateMutation.isPending}
											canManage={canManage}
										/>
									))}
								</DataTableBody>
							</DataTable>
						</div>

						<ul className="grid gap-3 lg:min-h-0 lg:overflow-auto @[64rem]/subscriptions:hidden">
							{subscriptions.map((subscription) => (
								<SubscriptionMobileRecord
									key={subscription.id}
									subscription={subscription}
									onOpenEdit={handleOpenEdit}
									onOpenDelete={handleOpenDelete}
									onToggleActive={handleToggleActive}
									pending={updateMutation.isPending}
									canManage={canManage}
								/>
							))}
						</ul>
					</>
				)}
			</div>

			<CreateSubscriptionDialog
				open={createDialogOpen}
				onOpenChange={setCreateDialogOpen}
				sourceId={sourceId}
				onSuccess={handleCreateSuccess}
			/>

			<EditSubscriptionDialog
				subscription={selectedSubscription}
				sourceId={sourceId}
				open={editDialogOpen}
				onOpenChange={handleEditClose}
			/>

			<AlertDialog
				open={deleteDialogOpen}
				onOpenChange={handleDeleteDialogOpenChange}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Delete Subscription</AlertDialogTitle>
						<AlertDialogDescription>
							Are you sure you want to delete this subscription?
							Events will no longer trigger this resource. This
							action cannot be undone.
						</AlertDialogDescription>
					</AlertDialogHeader>
					{deleteError && (
						<p role="alert" className="text-sm text-destructive">
							{deleteError}
						</p>
					)}
					<AlertDialogFooter>
						<AlertDialogCancel disabled={deleteMutation.isPending}>
							Cancel
						</AlertDialogCancel>
						<AlertDialogAction
							onClick={(event) => {
								event.preventDefault();
								void handleConfirmDelete();
							}}
							disabled={deleteMutation.isPending}
							className="min-h-11 bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							{deleteMutation.isPending ? (
								<>
									<Loader2
										aria-hidden="true"
										className="mr-2 size-4 animate-spin motion-reduce:animate-none"
									/>
									Deleting…
								</>
							) : deleteError ? (
								"Retry delete"
							) : (
								"Delete"
							)}
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</>
	);
}
