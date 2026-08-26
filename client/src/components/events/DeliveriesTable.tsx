import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
	RefreshCw,
	CheckCircle2,
	XCircle,
	Clock,
	Loader2,
	ExternalLink,
	AlertTriangle,
	Workflow as WorkflowIcon,
	Bot,
	Send,
	CircleDashed,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
	Tooltip,
	TooltipContent,
	TooltipProvider,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import { format } from "date-fns";
import { toast } from "sonner";
import { useAuth } from "@/contexts/AuthContext";
import {
	useRetryDelivery,
	useCreateDelivery,
	type EventDelivery,
} from "@/services/events";

// Extended status type to include "not_delivered"
type DeliveryStatus = EventDelivery["status"] | "not_delivered";

type RuleDecision = {
	criteria_version: number | null;
	outcome: "matched" | "not_matched" | "evaluation_error";
	code: string;
};

function getRuleDecision(delivery: EventDelivery): RuleDecision | null {
	return (
		(
			delivery as EventDelivery & {
				rule_decision?: RuleDecision | null;
			}
		).rule_decision ?? null
	);
}

interface DeliveriesTableProps {
	deliveries: EventDelivery[];
	eventId?: string;
}

function getStatusIcon(status: DeliveryStatus, decision?: RuleDecision | null) {
	switch (status) {
		case "pending":
			return <Clock className="h-4 w-4 text-muted-foreground" />;
		case "queued":
			return <Loader2 className="h-4 w-4 text-blue-500 animate-spin" />;
		case "success":
			return <CheckCircle2 className="h-4 w-4 text-green-500" />;
		case "failed":
			return <XCircle className="h-4 w-4 text-destructive" />;
		case "skipped":
			return decision?.outcome === "evaluation_error" ? (
				<XCircle className="h-4 w-4 text-destructive" />
			) : (
				<AlertTriangle className="h-4 w-4 text-amber-500" />
			);
		case "not_delivered":
			return <CircleDashed className="h-4 w-4 text-muted-foreground" />;
		default:
			return <Clock className="h-4 w-4 text-muted-foreground" />;
	}
}

function getStatusLabel(status: DeliveryStatus, decision?: RuleDecision | null) {
	switch (status) {
		case "pending":
			return "Pending";
		case "queued":
			return "Queued";
		case "success":
			return "Success";
		case "failed":
			return "Failed";
		case "skipped":
			return decision?.outcome === "evaluation_error"
				? "Criteria error"
				: "Criteria did not match";
		case "not_delivered":
			return "Not Delivered";
		default:
			return "Unknown";
	}
}

function getStatusVariant(
	status: DeliveryStatus,
): "default" | "secondary" | "destructive" | "outline" {
	switch (status) {
		case "success":
			return "default";
		case "failed":
			return "destructive";
		case "queued":
		case "pending":
			return "outline";
		case "not_delivered":
			return "secondary";
		default:
			return "secondary";
	}
}

export function DeliveriesTable({ deliveries, eventId }: DeliveriesTableProps) {
	const { isPlatformAdmin } = useAuth();
	const queryClient = useQueryClient();
	const retryMutation = useRetryDelivery();
	const createDeliveryMutation = useCreateDelivery();
	const [retryingId, setRetryingId] = useState<string | null>(null);
	const [sendingId, setSendingId] = useState<string | null>(null);

	const handleRetry = async (deliveryId: string) => {
		setRetryingId(deliveryId);
		try {
			await retryMutation.mutateAsync({
				params: {
					path: { delivery_id: deliveryId },
				},
			});
			toast.success("Delivery retry queued");
			// Refresh deliveries
			queryClient.invalidateQueries({
				predicate: (query) =>
					query.queryKey[0] === "get" &&
					(query.queryKey[1] as string)?.includes("/deliveries"),
			});
		} catch {
			toast.error("Failed to retry delivery");
		} finally {
			setRetryingId(null);
		}
	};

	const handleSend = async (subscriptionId: string) => {
		if (!eventId) return;
		setSendingId(subscriptionId);
		try {
			await createDeliveryMutation.mutateAsync({
				params: {
					path: { event_id: eventId },
				},
				body: {
					subscription_id: subscriptionId,
				},
			});
			toast.success("Event delivery queued");
			// Refresh deliveries
			queryClient.invalidateQueries({
				predicate: (query) =>
					query.queryKey[0] === "get" &&
					(query.queryKey[1] as string)?.includes("/deliveries"),
			});
		} catch {
			toast.error("Failed to send event");
		} finally {
			setSendingId(null);
		}
	};

	if (deliveries.length === 0) {
		return (
			<div className="text-center py-6 text-muted-foreground">
				No deliveries for this event (no active subscriptions).
			</div>
		);
	}

	const getBorderColor = (status: DeliveryStatus) => {
		switch (status) {
			case "success":
				return "border-l-green-500";
			case "failed":
				return "border-l-destructive";
			case "queued":
			case "pending":
				return "border-l-blue-500";
			case "skipped":
				return "border-l-amber-500";
			case "not_delivered":
				return "border-l-muted-foreground/50";
			default:
				return "border-l-primary/60";
		}
	};

	return (
		<div className="space-y-2">
			{deliveries.map((delivery) => (
				<div
					key={delivery.id || `sub-${delivery.event_subscription_id}`}
					className={`border rounded-lg p-3 border-l-4 bg-muted/40 ${getBorderColor(delivery.status as DeliveryStatus)}`}
				>
					{/* Top row: Target + Status + Actions */}
					<div className="flex items-center justify-between gap-3">
						<div className="flex items-center gap-2 min-w-0 flex-1">
							{(() => {
								// Cast to access agent fields pending type regeneration
								const d = delivery as typeof delivery & {
									target_type?: string;
									agent_id?: string | null;
									agent_name?: string | null;
									agent_run_id?: string | null;
								};
								const isAgent = d.target_type === "agent";
								const resourceName = isAgent
									? (d.agent_name || d.agent_id || "Unknown Agent")
									: (delivery.workflow_name || delivery.workflow_id);
								const linkUrl = isAgent && d.agent_run_id && d.agent_id
									? `/agents/${d.agent_id}/runs/${d.agent_run_id}`
									: delivery.execution_id
										? `/history/${delivery.execution_id}`
										: null;

								return (
									<>
										<Badge
											variant="outline"
											className="font-mono text-xs shrink-0"
										>
											{isAgent ? (
												<Bot className="mr-1 h-3 w-3" />
											) : (
												<WorkflowIcon className="mr-1 h-3 w-3" />
											)}
											{resourceName}
										</Badge>
										{linkUrl && (
											<TooltipProvider>
												<Tooltip>
													<TooltipTrigger asChild>
														<Button
															variant="ghost"
															size="icon"
															className="h-6 w-6 shrink-0"
															onClick={() => {
																window.open(linkUrl, "_blank");
															}}
														>
															<ExternalLink className="h-3.5 w-3.5" />
														</Button>
													</TooltipTrigger>
													<TooltipContent>
														{isAgent ? "View agent run" : "View execution"}
													</TooltipContent>
												</Tooltip>
											</TooltipProvider>
										)}
									</>
								);
							})()}
						</div>
						<div className="flex items-center gap-2">
							<div className="flex items-center gap-1.5">
								{getStatusIcon(
									delivery.status as DeliveryStatus,
									getRuleDecision(delivery),
								)}
								{delivery.status === "failed" &&
								delivery.error_message ? (
									<TooltipProvider>
										<Tooltip>
											<TooltipTrigger asChild>
												<Badge
													variant="destructive"
													className="cursor-pointer"
													onClick={() => {
														navigator.clipboard.writeText(
															delivery.error_message!,
														);
														toast.success(
															"Error copied to clipboard",
														);
													}}
												>
													Failed
												</Badge>
											</TooltipTrigger>
											<TooltipContent
												side="top"
												className="max-w-xs"
											>
												<p className="text-xs">
													{delivery.error_message}
												</p>
												<p className="text-xs text-muted-foreground mt-1">
													Click to copy
												</p>
											</TooltipContent>
										</Tooltip>
									</TooltipProvider>
								) : (
									<Badge
										variant={getStatusVariant(
											delivery.status as DeliveryStatus,
										)}
									>
										{getStatusLabel(
											delivery.status as DeliveryStatus,
											getRuleDecision(delivery),
										)}
									</Badge>
								)}
							</div>
							{/* Retry button for failed deliveries */}
							{isPlatformAdmin &&
								delivery.status === "failed" &&
								delivery.id && (
									<Button
										variant="outline"
										size="sm"
										onClick={() => handleRetry(delivery.id!)}
										disabled={retryingId === delivery.id}
									>
										{retryingId === delivery.id ? (
											<Loader2 className="h-3.5 w-3.5 animate-spin mr-1" />
										) : (
											<RefreshCw className="h-3.5 w-3.5 mr-1" />
										)}
										Retry
									</Button>
								)}
							{/* Send button for not_delivered subscriptions */}
							{isPlatformAdmin &&
								delivery.status === "not_delivered" && (
									<Button
										variant="outline"
										size="sm"
										onClick={() =>
											handleSend(delivery.event_subscription_id)
										}
										disabled={
											sendingId === delivery.event_subscription_id
										}
									>
										{sendingId ===
										delivery.event_subscription_id ? (
											<Loader2 className="h-3.5 w-3.5 animate-spin mr-1" />
										) : (
											<Send className="h-3.5 w-3.5 mr-1" />
										)}
										Send
									</Button>
								)}
						</div>
					</div>

					{/* Bottom row: Metadata */}
					{delivery.status !== "not_delivered" && (
						<div className="flex items-center gap-4 mt-2 pt-2 border-t border-border/50 text-xs text-muted-foreground">
							<span>
								{delivery.attempt_count} attempt
								{delivery.attempt_count !== 1 ? "s" : ""}
							</span>
							{delivery.completed_at && (
								<span>
									Completed{" "}
									{format(
										new Date(delivery.completed_at),
										"MMM d, HH:mm:ss",
									)}
								</span>
							)}
							{getRuleDecision(delivery) && (
								<span>
									Rule: {getStatusLabel(
										delivery.status as DeliveryStatus,
										getRuleDecision(delivery),
									)}
									{" · "}{getRuleDecision(delivery)?.code}
								</span>
							)}
						</div>
					)}
					{delivery.status === "not_delivered" && (
						<div className="flex items-center gap-4 mt-2 pt-2 border-t border-border/50 text-xs text-muted-foreground">
							<span>
								Subscription added after this event arrived
							</span>
						</div>
					)}
				</div>
			))}
		</div>
	);
}
