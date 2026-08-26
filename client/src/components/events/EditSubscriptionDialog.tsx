import { useState } from "react";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { AlertCircle, Loader2 } from "lucide-react";
import { toast } from "sonner";
import {
	useUpdateSubscription,
	type EventSubscription,
} from "@/services/events";
import { useWorkflows } from "@/hooks/useWorkflows";
import { InputMappingForm } from "@/components/events/InputMappingForm";
import {
	EventCriteriaForm,
	validateCriteria,
	type EventCriteria,
} from "@/components/events/EventCriteriaForm";
import type { components } from "@/lib/v1";

type WorkflowMetadata = components["schemas"]["WorkflowMetadata"];

/**
 * Remove entries where value is undefined, null, or empty string.
 * Returns undefined if no non-empty values remain.
 */
function cleanInputMapping(
	mapping: Record<string, unknown>,
): Record<string, unknown> | undefined {
	const cleaned = Object.fromEntries(
		Object.entries(mapping).filter(
			([, v]) => v !== undefined && v !== null && v !== "",
		),
	);
	return Object.keys(cleaned).length > 0 ? cleaned : undefined;
}

interface EditSubscriptionDialogProps {
	subscription: EventSubscription | null;
	sourceId: string;
	open: boolean;
	onOpenChange: (open: boolean) => void;
}

function EditSubscriptionDialogContent({
	subscription,
	sourceId,
	onOpenChange,
}: {
	subscription: EventSubscription;
	sourceId: string;
	onOpenChange: (open: boolean) => void;
}) {
	const updateMutation = useUpdateSubscription();

	// Fetch available workflows for parameter info
	const { data: workflowsData } = useWorkflows();
	const workflows: WorkflowMetadata[] = workflowsData || [];
	const selectedWorkflow = workflows.find(
		(w) => w.id === subscription.workflow_id,
	);

	// Form state - initialized from props, component remounts when dialog opens
	const [eventType, setEventType] = useState<string>(
		subscription.event_type ?? "",
	);
	const [criteria, setCriteria] = useState<EventCriteria | null>(
		(
			subscription as EventSubscription & {
				criteria?: EventCriteria | null;
			}
		).criteria ?? null,
	);
	const [inputMapping, setInputMapping] = useState<Record<string, unknown>>(
		(subscription.input_mapping as Record<string, unknown>) ?? {},
	);
	const [errors, setErrors] = useState<string[]>([]);

	const isLoading = updateMutation.isPending;

	const validateForm = (): boolean => {
		const newErrors: string[] = [];
		newErrors.push(...validateCriteria(criteria));
		setErrors(newErrors);
		return newErrors.length === 0;
	};

	const handleSubmit = async (e: React.FormEvent) => {
		e.preventDefault();
		if (!subscription || !validateForm()) return;

		try {
			const cleanedMapping = cleanInputMapping(inputMapping);

			await updateMutation.mutateAsync({
				params: {
					path: {
						source_id: sourceId,
						subscription_id: subscription.id,
					},
				},
				body: {
					event_type: eventType.trim() || null,
					criteria,
					input_mapping: cleanedMapping ?? null,
				// eslint-disable-next-line @typescript-eslint/no-explicit-any
				} as any,
			});

			toast.success("Subscription updated");
			onOpenChange(false);
		} catch (error) {
			console.error("Failed to update subscription:", error);
			toast.error("Failed to update subscription");
		}
	};

	return (
		<form onSubmit={handleSubmit}>
			<DialogHeader>
				<DialogTitle>Edit Subscription</DialogTitle>
				<DialogDescription>
					Update the event type filter and input mapping for this
					subscription.
				</DialogDescription>
			</DialogHeader>

			<div className="space-y-4 py-4">
				{errors.length > 0 && (
					<Alert variant="destructive">
						<AlertCircle className="h-4 w-4" />
						<AlertDescription>
							<ul className="list-disc list-inside">
								{errors.map((error, i) => (
									<li key={i}>{error}</li>
								))}
							</ul>
						</AlertDescription>
					</Alert>
				)}

				{/* Workflow (read-only) */}
				<div className="space-y-2">
					<Label>Workflow</Label>
					<div className="text-sm font-medium">
						{subscription.workflow_name || subscription.workflow_id}
					</div>
					<p className="text-xs text-muted-foreground">
						The workflow cannot be changed. Create a new
						subscription to use a different workflow.
					</p>
				</div>

				{/* Event Type Filter */}
				<div className="space-y-2">
					<Label htmlFor="event-type">Event Type Filter</Label>
					<Input
						id="event-type"
						value={eventType}
						onChange={(e) => setEventType(e.target.value)}
						placeholder="e.g., ticket.created"
					/>
					<p className="text-xs text-muted-foreground">
						Only trigger the workflow for events matching this type.
						Leave empty to receive all events.
					</p>
				</div>

				<div className="space-y-2 border-t pt-4">
					<Label>Rule Criteria (optional)</Label>
					<p className="text-xs text-muted-foreground">
						Only matching events queue this target. Clearing criteria restores unconditional delivery.
					</p>
					<EventCriteriaForm value={criteria} onChange={setCriteria} disabled={isLoading} />
				</div>

				{/* Input Mapping (shown when workflow has parameters) */}
				{selectedWorkflow?.parameters &&
					selectedWorkflow.parameters.length > 0 && (
						<div className="space-y-3">
							<div className="border-t pt-3">
								<Label className="text-sm font-medium">
									Input Mapping (Optional)
								</Label>
								<p className="text-xs text-muted-foreground mt-1">
									Map event data to workflow parameters
									using static values or{" "}
									<code className="bg-muted px-1 py-0.5 rounded text-xs">
										{"{{ template }}"}
									</code>{" "}
									expressions.
								</p>
							</div>
							<InputMappingForm
								parameters={selectedWorkflow.parameters}
								values={inputMapping}
								onChange={setInputMapping}
							/>
						</div>
					)}
			</div>

			<DialogFooter>
				<Button
					type="button"
					variant="outline"
					onClick={() => onOpenChange(false)}
				>
					Cancel
				</Button>
				<Button type="submit" disabled={isLoading}>
					{isLoading && (
						<Loader2 className="mr-2 h-4 w-4 animate-spin" />
					)}
					Save Changes
				</Button>
			</DialogFooter>
		</form>
	);
}

export function EditSubscriptionDialog({
	subscription,
	sourceId,
	open,
	onOpenChange,
}: EditSubscriptionDialogProps) {
	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-[760px]">
				{open && subscription && (
					<EditSubscriptionDialogContent
						subscription={subscription}
						sourceId={sourceId}
						onOpenChange={onOpenChange}
					/>
				)}
			</DialogContent>
		</Dialog>
	);
}
