import { ExecutionOrganizationSelect } from "@/components/execution/ExecutionOrganizationSelect";
import {
	PageWorkspace,
	PageScrollArea,
} from "@/components/layout/PageWorkspace";
import { useEffect, useMemo, useRef, useState } from "react";
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
	const {
		data,
		isLoading,
		error: metadataError,
		refetch,
	} = useWorkflowsMetadata();
	const executeWorkflow = useExecuteWorkflow();
	const [organization, setOrganization] = useState<{
		workflowId: string;
		orgId?: string;
	}>();
	const orgId =
		organization?.workflowId === workflowName
			? organization?.orgId
			: undefined;

	// Track navigation state to keep button disabled through redirect
	const [isNavigating, setIsNavigating] = useState(false);
	const [submitError, setSubmitError] = useState<string | null>(null);
	const submitErrorRef = useRef<HTMLDivElement>(null);
	// User-edited overrides for the parameter inputs. Defaults are merged in
	// on render so we don't need an effect to seed state.
	const [overrides, setOverrides] = useState<Record<string, unknown>>({});
	const [schedule, setSchedule] = useState<Schedule | null>(null);

	const workflow = data?.workflows?.find((w) => w.name === workflowName);
	const isScheduled = schedule !== null;
	const submitLabel = isScheduled ? "Schedule workflow" : "Execute Workflow";
	const submittingLabel = isScheduled ? "Scheduling..." : "Executing...";

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
		setSubmitError(null);
		try {
			const body: WorkflowExecutionRequest = {
				workflow_id: workflow.id,
				...(orgId ? { org_id: orgId } : {}),
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
			setSubmitError(getErrorMessage(error, "Unknown error occurred"));
		}
	};

	useEffect(() => {
		if (!submitError) return;
		submitErrorRef.current?.focus();
		submitErrorRef.current?.scrollIntoView({ block: "center" });
	}, [submitError]);

	if (isLoading) {
		return (
			<div className="mx-auto min-w-0 max-w-2xl space-y-6">
				<Skeleton className="h-10 w-64 max-w-full" />
				<Skeleton className="h-96 w-full" />
			</div>
		);
	}

	if (metadataError || !workflow) {
		return (
			<div className="mx-auto min-w-0 max-w-2xl space-y-6">
				<Alert variant="destructive">
					<XCircle className="h-4 w-4" />
					<AlertTitle>
						{metadataError
							? "Unable to load workflow"
							: "Workflow not found"}
					</AlertTitle>
					<AlertDescription>
						{metadataError
							? "Workflow information could not be loaded. Try again."
							: "This workflow may have been removed or may not be available to you."}
					</AlertDescription>
				</Alert>
				{metadataError && (
					<Button variant="outline" onClick={() => void refetch()}>
						Try again
					</Button>
				)}
				<Button onClick={() => navigate(backTo)}>
					<ArrowLeft className="mr-2 h-4 w-4" />
					{fromSolution ? "Back to Solution" : "Back to Workflows"}
				</Button>
			</div>
		);
	}

	return (
		<PageWorkspace className="mx-auto min-w-0 max-w-2xl ">
			<div className="shrink-0 space-y-6">
				<div className="flex justify-center">
					<div className="w-full max-w-2xl">
						<div className="flex items-start gap-3">
							<Button
								variant="ghost"
								size="icon"
								className="size-11 shrink-0"
								onClick={() => navigate(backTo)}
								aria-label={
									fromSolution
										? "Back to Solution"
										: "Back to Workflows"
								}
							>
								<ArrowLeft className="h-4 w-4" />
							</Button>
							<div className="min-w-0">
								<h1 className="font-display text-2xl font-semibold tracking-tight sm:text-3xl">
									Execute Workflow
								</h1>
								<p className="mt-2 text-sm leading-6 text-muted-foreground">
									Workflow:{" "}
									<span className="font-mono [overflow-wrap:anywhere]">
										{workflow.name}
									</span>
								</p>
							</div>
						</div>
					</div>
				</div>
			</div>

			<PageScrollArea className="space-y-6">
				<div className="flex justify-center">
					<div className="w-full max-w-2xl">
						<Card>
							<CardHeader>
								<CardTitle>Parameters</CardTitle>
								{workflow.description && (
									<CardDescription className="[overflow-wrap:anywhere]">
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
									<div className="mb-6">
										<ExecutionOrganizationSelect
											value={orgId}
											organizationId={
												workflow.organization_id
											}
											disabled={
												executeWorkflow.isPending ||
												isNavigating
											}
											onChange={(orgId) =>
												setOrganization({
													workflowId: workflowName!,
													orgId,
												})
											}
										/>
									</div>
									<WorkflowParametersForm
										parameters={workflow.parameters || []}
										onExecute={handleExecute}
										isExecuting={
											executeWorkflow.isPending ||
											isNavigating
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
									{submitError && (
										<div
											ref={submitErrorRef}
											tabIndex={-1}
											className="mt-6 outline-none"
										>
											<Alert
												variant="destructive"
												aria-live="assertive"
											>
												<XCircle className="h-4 w-4" />
												<AlertTitle>
													Failed to{" "}
													{isScheduled
														? "schedule"
														: "execute"}{" "}
													workflow
												</AlertTitle>
												<AlertDescription>
													{submitError}
												</AlertDescription>
											</Alert>
										</div>
									)}
									<Button
										type="submit"
										className="mt-6 min-h-11 w-full"
										disabled={
											executeWorkflow.isPending ||
											isNavigating
										}
									>
										{executeWorkflow.isPending ||
										isNavigating ? (
											<Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
										) : (
											<Play className="mr-2 h-4 w-4" />
										)}
										{executeWorkflow.isPending ||
										isNavigating
											? submittingLabel
											: submitLabel}
									</Button>
								</form>
							</CardContent>
						</Card>
					</div>
				</div>
			</PageScrollArea>
		</PageWorkspace>
	);
}
