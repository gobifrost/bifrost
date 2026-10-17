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
	Workflow,
	Bot,
	Send,
	CircleDashed,
	Copy,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { format } from "date-fns";
import { toast } from "sonner";
import {
	useRetryDelivery,
	useCreateDelivery,
	type EventDelivery,
} from "@/services/events";

interface DeliveriesTableProps {
	deliveries: EventDelivery[];
	eventId?: string;
	canManage?: boolean;
}

const deliveryStates = {
	pending: { label: "Pending", Icon: Clock, color: "text-muted-foreground" },
	queued: { label: "Queued", Icon: Loader2, color: "text-[var(--bf-info)]" },
	success: {
		label: "Success",
		Icon: CheckCircle2,
		color: "text-[var(--bf-success)]",
	},
	failed: {
		label: "Failed",
		Icon: XCircle,
		color: "text-[var(--bf-danger)]",
	},
	skipped: {
		label: "Skipped",
		Icon: AlertTriangle,
		color: "text-[var(--bf-warning)]",
	},
	not_delivered: {
		label: "Not Delivered",
		Icon: CircleDashed,
		color: "text-muted-foreground",
	},
};

function DeliveryRecord({
	delivery,
	eventId,
	isAdmin,
}: {
	delivery: EventDelivery;
	eventId?: string;
	isAdmin: boolean;
}) {
	const queryClient = useQueryClient();
	const retryMutation = useRetryDelivery();
	const createMutation = useCreateDelivery();
	const [action, setAction] = useState<"retry" | "send" | null>(null);
	const [error, setError] = useState<string | null>(null);
	const [copyError, setCopyError] = useState(false);
	const isAgent = delivery.target_type === "agent";
	const name = isAgent
		? delivery.agent_name || delivery.agent_id || "Unknown Agent"
		: delivery.workflow_name || delivery.workflow_id || "Unknown Workflow";
	const link =
		isAgent && delivery.agent_id && delivery.agent_run_id
			? `/agents/${delivery.agent_id}/runs/${delivery.agent_run_id}`
			: delivery.execution_id
				? `/history/${delivery.execution_id}`
				: null;
	const state = deliveryStates[
		delivery.status as keyof typeof deliveryStates
	] ?? {
		label: "Unknown",
		Icon: Clock,
		color: "text-muted-foreground",
	};
	const TargetIcon = isAgent ? Bot : Workflow;
	const handleAction = async (nextAction: "retry" | "send") => {
		if (action || !isAdmin || (nextAction === "send" && !eventId)) return;
		setAction(nextAction);
		setError(null);
		try {
			if (nextAction === "retry" && delivery.id) {
				await retryMutation.mutateAsync({
					params: { path: { delivery_id: delivery.id } },
				});
			} else if (nextAction === "send" && eventId) {
				await createMutation.mutateAsync({
					params: { path: { event_id: eventId } },
					body: { subscription_id: delivery.event_subscription_id },
				});
			} else return;
			toast.success(
				nextAction === "retry"
					? "Delivery retry queued"
					: "Event delivery queued",
			);
			queryClient.invalidateQueries({
				predicate: (query) =>
					query.queryKey[0] === "get" &&
					typeof query.queryKey[1] === "string" &&
					query.queryKey[1].includes("/deliveries"),
			});
		} catch {
			setError(
				nextAction === "retry"
					? "Could not queue the retry. Try again."
					: "Could not send this event. Try again.",
			);
		} finally {
			setAction(null);
		}
	};
	const copyErrorMessage = async () => {
		try {
			await navigator.clipboard.writeText(delivery.error_message || "");
			setCopyError(false);
			toast.success("Error copied to clipboard");
		} catch {
			setCopyError(true);
		}
	};
	return (
		<article
			className="min-w-0 rounded-[var(--bf-radius-surface)] border bg-card p-4 space-y-3"
			aria-label={`${name} delivery`}
		>
			<div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
				<span className="inline-flex items-center gap-2 text-sm text-muted-foreground">
					<TargetIcon className="size-4 shrink-0" />
					{isAgent ? "Agent" : "Workflow"}
				</span>
				<Badge
					variant="outline"
					className={`h-auto py-1 text-sm ${state.color}`}
				>
					<state.Icon
						className={`shrink-0 ${delivery.status === "queued" ? "motion-safe:animate-spin" : ""}`}
					/>
					{state.label}
				</Badge>
			</div>
			<h3 className="text-sm font-medium leading-6 [overflow-wrap:anywhere]">
				{name}
			</h3>
			{delivery.error_message && (
				<div className="space-y-2 border-l-2 border-[var(--bf-danger)] pl-3">
					<p className="whitespace-pre-wrap text-sm leading-6 [overflow-wrap:anywhere]">
						{delivery.error_message}
					</p>
					<Button
						type="button"
						variant="outline"
						className="min-h-11"
						onClick={copyErrorMessage}
					>
						<Copy className="size-4" />
						Copy error
					</Button>
					{copyError && (
						<p role="alert" className="text-sm text-destructive">
							Could not copy the error. Select the text above to
							copy it.
						</p>
					)}
				</div>
			)}
			<div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm leading-6 text-muted-foreground">
				{delivery.status === "not_delivered" ? (
					<span>Subscription added after this event arrived</span>
				) : (
					<>
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
					</>
				)}
			</div>
			{(link ||
				(isAdmin &&
					((delivery.status === "failed" && delivery.id) ||
						(delivery.status === "not_delivered" && eventId)))) && (
				<div className="flex flex-wrap items-center gap-2 border-t pt-3">
					{link && (
						<Button variant="outline" className="min-h-11" asChild>
							<a
								href={link}
								target="_blank"
								rel="noopener noreferrer"
							>
								<ExternalLink className="size-4" />
								{isAgent ? "View agent run" : "View execution"}
								<span className="sr-only">
									{" "}
									(opens in a new tab)
								</span>
							</a>
						</Button>
					)}
					{isAdmin && delivery.status === "failed" && delivery.id && (
						<Button
							type="button"
							variant="outline"
							className="min-h-11"
							disabled={action !== null}
							onClick={() => handleAction("retry")}
						>
							{action === "retry" ? (
								<Loader2 className="size-4 motion-safe:animate-spin" />
							) : (
								<RefreshCw className="size-4" />
							)}
							Retry
						</Button>
					)}
					{isAdmin &&
						delivery.status === "not_delivered" &&
						eventId && (
							<Button
								type="button"
								variant="outline"
								className="min-h-11"
								disabled={action !== null}
								onClick={() => handleAction("send")}
							>
								{action === "send" ? (
									<Loader2 className="size-4 motion-safe:animate-spin" />
								) : (
									<Send className="size-4" />
								)}
								Send
							</Button>
						)}
				</div>
			)}
			{error && (
				<p role="alert" className="text-sm leading-6 text-destructive">
					{error}
				</p>
			)}
		</article>
	);
}

export function DeliveriesTable({
	deliveries,
	eventId,
	canManage = false,
}: DeliveriesTableProps) {
	if (!deliveries.length)
		return (
			<div className="py-6 text-center text-sm leading-6 text-muted-foreground">
				No deliveries for this event (no active subscriptions).
			</div>
		);
	return (
		<div className="min-w-0 space-y-3">
			{deliveries.map((delivery) => (
				<DeliveryRecord
					key={delivery.id || `sub-${delivery.event_subscription_id}`}
					delivery={delivery}
					eventId={eventId}
					isAdmin={canManage}
				/>
			))}
		</div>
	);
}
