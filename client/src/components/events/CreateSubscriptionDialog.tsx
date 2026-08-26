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
import { useCreateSubscription } from "@/services/events";
import { useWorkflows } from "@/hooks/useWorkflows";
import { useAgents } from "@/hooks/useAgents";
import { WorkflowSelectorDialog } from "@/components/workflows/WorkflowSelectorDialog";
import { AgentSelectorDialog } from "@/components/agents/AgentSelectorDialog";
import { InputMappingForm } from "@/components/events/InputMappingForm";
import {
	EventCriteriaForm,
	validateCriteria,
	type EventCriteria,
} from "@/components/events/EventCriteriaForm";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import type { components } from "@/lib/v1";

type WorkflowMetadata = components["schemas"]["WorkflowMetadata"];

interface CreateSubscriptionDialogProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	sourceId: string;
	onSuccess?: () => void;
}

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

function CreateSubscriptionDialogContent({
	onOpenChange,
	sourceId,
	onSuccess,
}: Omit<CreateSubscriptionDialogProps, "open">) {
	const createMutation = useCreateSubscription();

	// Form state
	const [targetType, setTargetType] = useState<"workflow" | "agent">("workflow");
	const [workflowId, setWorkflowId] = useState("");
	const [agentId, setAgentId] = useState("");
	const [eventType, setEventType] = useState("");
	const [criteria, setCriteria] = useState<EventCriteria | null>(null);
	const [inputMapping, setInputMapping] = useState<Record<string, unknown>>(
		{},
	);
	const [errors, setErrors] = useState<string[]>([]);
	const [workflowDialogOpen, setWorkflowDialogOpen] = useState(false);
	const [agentDialogOpen, setAgentDialogOpen] = useState(false);

	// Fetch available workflows for display name lookup
	const { data: workflowsData } = useWorkflows();
	const workflows: WorkflowMetadata[] = workflowsData || [];

	// Fetch available agents
	const { data: agentsData } = useAgents();
	const agents = agentsData || [];

	// Get selected workflow for display and parameter info
	const selectedWorkflow = workflows.find((w) => w.id === workflowId);

	const isLoading = createMutation.isPending;

	const handleWorkflowSelect = (ids: string[]) => {
		const newId = ids[0] || "";
		setWorkflowId(newId);
		// Reset input mapping when workflow changes
		setInputMapping({});
	};

	const validateForm = (): boolean => {
		const newErrors: string[] = [];
		if (targetType === "workflow" && !workflowId) {
			newErrors.push("Please select a workflow");
		}
		if (targetType === "agent" && !agentId) {
			newErrors.push("Please select an agent");
		}
		newErrors.push(...validateCriteria(criteria));
		setErrors(newErrors);
		return newErrors.length === 0;
	};

	const handleSubmit = async (e: React.FormEvent) => {
		e.preventDefault();
		if (!validateForm()) return;

		try {
			const cleanedMapping = cleanInputMapping(inputMapping);

			await createMutation.mutateAsync({
				params: {
					path: { source_id: sourceId },
				},
				body: {
					target_type: targetType,
					workflow_id: targetType === "workflow" ? workflowId : undefined,
					agent_id: targetType === "agent" ? agentId : undefined,
					event_type: eventType.trim() || undefined,
					criteria: criteria ?? undefined,
					input_mapping: targetType === "workflow" ? cleanedMapping : undefined,
				// eslint-disable-next-line @typescript-eslint/no-explicit-any
				} as any,
			});

			toast.success("Subscription created");
			onOpenChange(false);
			onSuccess?.();
		} catch (error) {
			console.error("Failed to create subscription:", error);
			toast.error("Failed to create subscription");
		}
	};

	return (
		<form onSubmit={handleSubmit}>
			<DialogHeader>
				<DialogTitle>Add Subscription</DialogTitle>
				<DialogDescription>
					Subscribe a workflow or agent to receive events from this source.
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

				{/* Target Type */}
				<div className="space-y-2">
					<Label>Target Type</Label>
					<Select
						value={targetType}
						onValueChange={(v) => {
							setTargetType(v as "workflow" | "agent");
							setWorkflowId("");
							setAgentId("");
							setInputMapping({});
						}}
					>
						<SelectTrigger>
							<SelectValue />
						</SelectTrigger>
						<SelectContent>
							<SelectItem value="workflow">Workflow</SelectItem>
							<SelectItem value="agent">Agent</SelectItem>
						</SelectContent>
					</Select>
				</div>

				{/* Workflow Selector */}
				{targetType === "workflow" && (
					<div className="space-y-2">
						<Label>Workflow</Label>
						<Button
							type="button"
							variant="outline"
							className="w-full justify-start font-normal"
							onClick={() => setWorkflowDialogOpen(true)}
						>
							{selectedWorkflow?.name || "Select a workflow..."}
						</Button>
						<WorkflowSelectorDialog
							open={workflowDialogOpen}
							onOpenChange={setWorkflowDialogOpen}
							entityRoles={[]}
							mode="single"
							selectedWorkflowIds={workflowId ? [workflowId] : []}
							onSelect={handleWorkflowSelect}
							title="Select Workflow"
							description="Choose a workflow to receive events from this source."
						/>
						<p className="text-xs text-muted-foreground">
							The workflow will receive the event data as input
							parameters.
						</p>
					</div>
				)}

				{/* Agent Selector */}
				{targetType === "agent" && (
					<div className="space-y-2">
						<Label>Agent</Label>
						<Button
							type="button"
							variant="outline"
							className="w-full justify-start font-normal"
							onClick={() => setAgentDialogOpen(true)}
						>
							{agents.find((a) => a.id === agentId)?.name ||
								"Select an agent..."}
						</Button>
						<AgentSelectorDialog
							open={agentDialogOpen}
							onOpenChange={setAgentDialogOpen}
							selectedAgentId={agentId || null}
							onSelect={setAgentId}
						/>
						<p className="text-xs text-muted-foreground">
							The agent will run autonomously with the event data as input.
						</p>
					</div>
				)}

				{/* Event Type Filter (optional) */}
				<div className="space-y-2">
					<Label htmlFor="event-type">
						Event Type Filter (optional)
					</Label>
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
						Only matching events queue this target. Criteria are bounded data, not executable expressions.
					</p>
					<EventCriteriaForm value={criteria} onChange={setCriteria} disabled={isLoading} />
				</div>

				{/* Input Mapping (shown when workflow has parameters) */}
				{targetType === "workflow" &&
					selectedWorkflow?.parameters &&
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
					Add Subscription
				</Button>
			</DialogFooter>
		</form>
	);
}

export function CreateSubscriptionDialog({
	open,
	onOpenChange,
	sourceId,
	onSuccess,
}: CreateSubscriptionDialogProps) {
	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-[760px]">
				{open && (
					<CreateSubscriptionDialogContent
						onOpenChange={onOpenChange}
						sourceId={sourceId}
						onSuccess={onSuccess}
					/>
				)}
			</DialogContent>
		</Dialog>
	);
}
