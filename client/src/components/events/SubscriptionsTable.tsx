import { useState } from "react";
import { Bot, GitBranch, Plus, Trash2, Pencil, Zap, CheckCircle2, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
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
import { toast } from "sonner";
import {
	useSubscriptions,
	useDeleteSubscription,
	useUpdateSubscription,
	type EventSubscription,
} from "@/services/events";
import { CreateSubscriptionDialog } from "./CreateSubscriptionDialog";
import { EditSubscriptionDialog } from "./EditSubscriptionDialog";

interface SubscriptionsTableProps {
	sourceId: string;
}

export function SubscriptionsTable({ sourceId }: SubscriptionsTableProps) {
	const [createDialogOpen, setCreateDialogOpen] = useState(false);
	const [editDialogOpen, setEditDialogOpen] = useState(false);
	const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
	const [selectedSubscription, setSelectedSubscription] =
		useState<EventSubscription | null>(null);

	const { data, isLoading, refetch } = useSubscriptions(sourceId);
	const deleteMutation = useDeleteSubscription();
	const updateMutation = useUpdateSubscription();

	const subscriptions = data?.items || [];

	const handleRowClick = (subscription: EventSubscription) => {
		setSelectedSubscription(subscription);
		setEditDialogOpen(true);
	};

	const handleEdit = (
		subscription: EventSubscription,
		e: React.MouseEvent,
	) => {
		e.stopPropagation();
		setSelectedSubscription(subscription);
		setEditDialogOpen(true);
	};

	const handleToggleActive = async (
		subscription: EventSubscription,
		e: React.MouseEvent,
	) => {
		e.stopPropagation();
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

	const handleDelete = (
		subscription: EventSubscription,
		e: React.MouseEvent,
	) => {
		e.stopPropagation();
		setSelectedSubscription(subscription);
		setDeleteDialogOpen(true);
	};

	const handleConfirmDelete = async () => {
		if (!selectedSubscription) return;

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
			refetch();
		} catch {
			toast.error("Failed to delete subscription");
		} finally {
			setDeleteDialogOpen(false);
			setSelectedSubscription(null);
		}
	};

	const handleCreateSuccess = () => {
		setCreateDialogOpen(false);
		refetch();
	};

	const handleEditClose = () => {
		setEditDialogOpen(false);
		setSelectedSubscription(null);
	};

	if (isLoading) {
		return (
			<div className="space-y-3">
				{[...Array(3)].map((_, i) => (
					<Skeleton key={i} className="h-12 w-full" />
				))}
			</div>
		);
	}

	return (
		<>
			{/* Header with Add button */}
			<div className="flex items-center justify-between mb-4">
				<p className="text-sm text-muted-foreground">
					Workflows and agents triggered when events are received
				</p>
				<Button size="sm" onClick={() => setCreateDialogOpen(true)}>
					<Plus className="h-4 w-4 mr-1" />
					Add Subscription
				</Button>
			</div>

			{subscriptions.length === 0 ? (
				<div className="flex flex-col items-center justify-center py-12">
					<Zap className="h-12 w-12 text-muted-foreground mb-4" />
					<h3 className="text-lg font-semibold mb-2">
						No Subscriptions
					</h3>
					<p className="text-muted-foreground text-center mb-4">
						Add a subscription to trigger workflows or agents when
						events arrive.
					</p>
					<Button onClick={() => setCreateDialogOpen(true)}>
						<Plus className="h-4 w-4 mr-1" />
						Add Subscription
					</Button>
				</div>
			) : (
				<DataTable>
					<DataTableHeader>
						<DataTableRow>
							<DataTableHead className="w-[100px]">Type</DataTableHead>
							<DataTableHead>Resource</DataTableHead>
							<DataTableHead>Rule</DataTableHead>
							<DataTableHead className="text-right">
								Deliveries
							</DataTableHead>
							<DataTableHead className="text-right">
								Success Rate
							</DataTableHead>
							<DataTableHead className="text-right w-[80px]">
								Status
							</DataTableHead>
							<DataTableHead className="text-right w-[100px]" />
						</DataTableRow>
					</DataTableHeader>
					<DataTableBody>
						{subscriptions.map((subscription) => {
							// Cast to access fields pending type regeneration
							const sub = subscription as typeof subscription & {
								target_type?: string;
								agent_id?: string | null;
								agent_name?: string | null;
								criteria?: unknown;
								skipped_count?: number;
								evaluation_error_count?: number;
							};
							const targetType = sub.target_type || "workflow";
							const isAgent = targetType === "agent";
							const resourceName = isAgent
								? (sub.agent_name || sub.agent_id || "Unknown Agent")
								: (subscription.workflow_name || subscription.workflow_id);

							const executedCount =
								subscription.success_count + subscription.failed_count;
							const successRate =
								executedCount > 0
									? Math.round(
											(subscription.success_count /
												executedCount) *
												100,
										)
									: null;

							return (
								<DataTableRow
									key={subscription.id}
									clickable
									onClick={() => handleRowClick(subscription)}
								>
									<DataTableCell>
										{isAgent ? (
											<Badge variant="secondary" className="gap-1">
												<Bot className="h-3 w-3" />
												Agent
											</Badge>
										) : (
											<Badge variant="outline" className="gap-1">
												<GitBranch className="h-3 w-3" />
												Workflow
											</Badge>
										)}
									</DataTableCell>
									<DataTableCell className="font-medium">
										{resourceName}
									</DataTableCell>
									<DataTableCell>
									<div className="flex flex-wrap gap-1">
										<Badge variant={sub.criteria ? "secondary" : "outline"}>
											{sub.criteria ? "Conditional" : "All payloads"}
										</Badge>
										{subscription.event_type && (
											<Badge variant="outline">{subscription.event_type}</Badge>
										)}
									</div>
									</DataTableCell>
									<DataTableCell className="text-right">
									<div>{subscription.delivery_count || 0}</div>
									{((sub.skipped_count ?? 0) > 0 ||
										(sub.evaluation_error_count ?? 0) > 0) && (
										<div className="text-xs text-muted-foreground">
											{sub.skipped_count ?? 0} skipped
											{(sub.evaluation_error_count ?? 0) > 0 &&
												` · ${sub.evaluation_error_count} errors`}
										</div>
									)}
									</DataTableCell>
									<DataTableCell className="text-right">
										{successRate !== null ? (
											<div className="flex items-center justify-end gap-1">
												{successRate >= 90 ? (
													<CheckCircle2 className="h-4 w-4 text-green-500" />
												) : successRate < 50 ? (
													<XCircle className="h-4 w-4 text-destructive" />
												) : null}
												<span>{successRate}%</span>
											</div>
										) : (
											<span className="text-muted-foreground">
												—
											</span>
										)}
									</DataTableCell>
									<DataTableCell className="text-right">
										<Switch
											checked={subscription.is_active}
											onCheckedChange={() => {}}
											onClick={(e) =>
												handleToggleActive(
													subscription,
													e,
												)
											}
											disabled={updateMutation.isPending}
										/>
									</DataTableCell>
									<DataTableCell className="text-right">
										<div className="flex items-center justify-end gap-1">
											<Button
												variant="ghost"
												size="icon"
												onClick={(e) =>
													handleEdit(
														subscription,
														e,
													)
												}
												title="Edit subscription"
											>
												<Pencil className="h-4 w-4" />
											</Button>
											<Button
												variant="ghost"
												size="icon"
												onClick={(e) =>
													handleDelete(
														subscription,
														e,
													)
												}
												title="Delete subscription"
											>
												<Trash2 className="h-4 w-4" />
											</Button>
										</div>
									</DataTableCell>
								</DataTableRow>
							);
						})}
					</DataTableBody>
				</DataTable>
			)}

			{/* Create Dialog */}
			<CreateSubscriptionDialog
				open={createDialogOpen}
				onOpenChange={setCreateDialogOpen}
				sourceId={sourceId}
				onSuccess={handleCreateSuccess}
			/>

			{/* Edit Dialog */}
			<EditSubscriptionDialog
				subscription={selectedSubscription}
				sourceId={sourceId}
				open={editDialogOpen}
				onOpenChange={handleEditClose}
			/>

			{/* Delete Confirmation */}
			<AlertDialog
				open={deleteDialogOpen}
				onOpenChange={setDeleteDialogOpen}
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
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction
							onClick={handleConfirmDelete}
							className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							Delete
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</>
	);
}
