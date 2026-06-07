import { useMemo, useState } from "react";
import { useParams, useNavigate, useLocation } from "react-router-dom";
import { parseSolutionFrom } from "@/lib/solution-back-nav";
import { ArrowLeft, Loader2, Play, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";
import { useWorkflowsMetadata, useExecuteWorkflow } from "@/hooks/useWorkflows";
import { WorkflowParametersForm } from "@/components/workflows/WorkflowParametersForm";
import {
	ScheduleControls,
	type Schedule,
} from "@/components/execution/ScheduleControls";
import { getErrorMessage } from "@/lib/api-error";
import type { components } from "@/lib/v1";

type WorkflowExecutionRequest =
	components["schemas"]["WorkflowExecutionRequest"];

export function ExecuteWorkflow() {
	const { workflowName } = useParams();
	const navigate = useNavigate();
	const { search } = useLocation();
	const fromSolution = parseSolutionFrom(search);
	const backTo = fromSolution ? `/solutions/${fromSolution}` : "/workflows";
	const { data, isLoading } = useWorkflowsMetadata();
	const executeWorkflow = useExecuteWorkflow();

	// Track navigation state to keep button disabled through redirect
	const [isNavigating, setIsNavigating] = useState(false);
	// User-edited overrides for the parameter inputs. Defaults are merged in
	// on render so we don't need an effect to seed state.
	const [overrides, setOverrides] = useState<Record<string, unknown>>({});
	const [schedule, setSchedule] = useState<Schedule | null>(null);

	const workflow = data?.workflows?.find((w) => w.name === workflowName);

	// Compute defaults from the workflow's parameter schema, matching the
	// uncontrolled-mode defaults WorkflowParametersForm would have computed
	// internally. Merge with user overrides to produce the current values.
	const paramValues = useMemo<Record<string, unknown>>(() => {
		const defaults = (workflow?.parameters || []).reduce(
			(acc: Record<string, unknown>, param) => {
				if (!param.name) return acc;
				acc[param.name] =
					param.default_value ?? (param.type === "bool" ? false : "");
				return acc;
			},
			{} as Record<string, unknown>,
		);
		return { ...defaults, ...overrides };
	}, [workflow, overrides]);

	const handleExecute = async (parameters: Record<string, unknown>) => {
		if (!workflow) return;

		setIsNavigating(true);
		try {
			const body: WorkflowExecutionRequest = {
				workflow_id: workflow.id,
				input_data: parameters,
				form_id: null,
				transient: false,
				code: null,
				script_name: null,
				...(schedule ?? {}),
			};
			const result = await executeWorkflow.mutateAsync({ body });

			// Scheduled run: the execution hasn't happened yet. Send the user to
			// /history (where the new row will show with the Scheduled badge)
			// rather than the details page.
			if (result.status === "Scheduled") {
				const when = result.scheduled_at
					? new Date(result.scheduled_at).toLocaleString()
					: "later";
				toast.success(`Scheduled for ${when}`);
				navigate("/history");
				return;
			}

			// Run-now: redirect directly to execution details with context so the
			// page can display immediately without waiting for DB.
			navigate(`/history/${result.execution_id}`, {
				state: {
					workflow_name: workflow.name,
					workflow_id: workflow.id,
					input_data: parameters,
				},
			});
			// Don't reset isNavigating - component will unmount on navigation
		} catch (error) {
			setIsNavigating(false); // Only re-enable button on error
			toast.error("Failed to execute workflow", {
				description: getErrorMessage(error, "Unknown error occurred"),
			});
		}
	};

	if (isLoading) {
		return (
			<div className="space-y-6">
				<Skeleton className="h-12 w-64" />
				<Skeleton className="h-96 w-full" />
			</div>
		);
	}

	if (!workflow) {
		return (
			<div className="space-y-6">
				<Alert variant="destructive">
					<XCircle className="h-4 w-4" />
					<AlertTitle>Error</AlertTitle>
					<AlertDescription>Workflow not found</AlertDescription>
				</Alert>
				<Button onClick={() => navigate(backTo)}>
					<ArrowLeft className="mr-2 h-4 w-4" />
					{fromSolution ? "Back to Solution" : "Back to Workflows"}
				</Button>
			</div>
		);
	}

	return (
		<div className="space-y-6">
			<div className="flex justify-center">
				<div className="w-full max-w-2xl">
					<div className="flex items-center gap-4">
						<Button
							variant="ghost"
							size="icon"
							onClick={() => navigate(backTo)}
							aria-label={
								fromSolution
									? "Back to Solution"
									: "Back to Workflows"
							}
						>
							<ArrowLeft className="h-4 w-4" />
						</Button>
						<div>
							<h1 className="text-4xl font-extrabold tracking-tight">
								Execute Workflow
							</h1>
							<p className="mt-2 text-muted-foreground">
								Workflow:{" "}
								<span className="font-mono">
									{workflow.name}
								</span>
							</p>
						</div>
					</div>
				</div>
			</div>

			<div className="flex justify-center">
				<div className="w-full max-w-2xl">
					<Card>
						<CardHeader>
							<CardTitle>{workflow.name}</CardTitle>
							{workflow.description && (
								<CardDescription>
									{workflow.description}
								</CardDescription>
							)}
						</CardHeader>
						<CardContent>
							<form
								onSubmit={(e) => {
									e.preventDefault();
									void handleExecute(paramValues);
								}}
							>
								<WorkflowParametersForm
									parameters={workflow.parameters || []}
									onExecute={handleExecute}
									isExecuting={
										executeWorkflow.isPending || isNavigating
									}
									values={paramValues}
									onChange={setOverrides}
									renderAsDiv
									showExecuteButton={false}
								/>
								<div className="mt-6">
									<ScheduleControls
										value={schedule}
										onChange={setSchedule}
										disabled={
											executeWorkflow.isPending ||
											isNavigating
										}
									/>
								</div>
								<Button
									type="submit"
									className="w-full mt-6"
									disabled={
										executeWorkflow.isPending || isNavigating
									}
								>
									{executeWorkflow.isPending ||
									isNavigating ? (
										<Loader2 className="mr-2 h-4 w-4 animate-spin" />
									) : (
										<Play className="mr-2 h-4 w-4" />
									)}
									{executeWorkflow.isPending || isNavigating
										? "Executing..."
										: "Execute Workflow"}
								</Button>
							</form>
						</CardContent>
					</Card>
				</div>
			</div>
		</div>
	);
}
