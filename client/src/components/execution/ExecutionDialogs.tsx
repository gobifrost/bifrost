import { useState } from "react";
import { Loader2, Play } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { WorkflowParametersForm } from "@/components/workflows/WorkflowParametersForm";
import { useWorkflowParameters } from "@/services/workflowParameters";

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

interface ExecutionCancelDialogProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	workflowName: string;
	onConfirm: () => void;
	isCancelling?: boolean;
	error?: string;
}

export function ExecutionCancelDialog({
	open,
	onOpenChange,
	workflowName,
	onConfirm,
	isCancelling,
	error,
}: ExecutionCancelDialogProps) {
	return (
		<AlertDialog open={open} onOpenChange={(next) => { if (!isCancelling) onOpenChange(next); }}>
			<AlertDialogContent>
				<AlertDialogHeader>
					<AlertDialogTitle>Cancel Execution?</AlertDialogTitle>
					<AlertDialogDescription>
						Are you sure you want to cancel the execution of{" "}
						<span className="font-semibold [overflow-wrap:anywhere]">
							{workflowName}
						</span>
						? This action cannot be undone.
					</AlertDialogDescription>
				</AlertDialogHeader>
				{error && <p role="alert" className="text-sm text-destructive">{error}</p>}
				<AlertDialogFooter className="[&_button]:min-h-11 [&_button]:whitespace-normal">
					<AlertDialogCancel disabled={isCancelling}>No, keep running</AlertDialogCancel>
					<AlertDialogAction
						onClick={(event) => { if (isCancelling !== undefined) event.preventDefault(); onConfirm(); }}
						disabled={isCancelling}
						className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
					>
						{isCancelling ? "Requesting cancellation…" : error ? "Retry cancellation" : "Yes, cancel execution"}
					</AlertDialogAction>
				</AlertDialogFooter>
			</AlertDialogContent>
		</AlertDialog>
	);
}

interface ExecutionRerunDialogProps {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	workflowId: string | null;
	workflowName: string;
	inputData: Record<string, unknown>;
	isRerunning: boolean;
	onConfirm: (parameters: Record<string, unknown>) => void;
	error?: string;
}

export function ExecutionRerunDialog({
	open,
	onOpenChange,
	workflowId,
	workflowName,
	inputData,
	isRerunning,
	onConfirm,
	error,
}: ExecutionRerunDialogProps) {
	const parameters = useWorkflowParameters(open ? workflowId : null);
	const [overrides, setOverrides] = useState<Record<string, unknown>>({});
	const values = Object.fromEntries(
		(parameters.data ?? []).map((parameter) => [
			parameter.name,
			Object.hasOwn(overrides, parameter.name)
				? overrides[parameter.name]
				: Object.hasOwn(inputData, parameter.name)
					? inputData[parameter.name]
					: (parameter.default_value ??
						(parameter.type === "bool" ? false : "")),
		]),
	);
	const ready =
		!!workflowId && parameters.data !== undefined && !parameters.isError;

	return (
		<Dialog
			open={open}
			onOpenChange={(next) => {
				if (!isRerunning) onOpenChange(next);
			}}
		>
			<DialogContent
				className="flex max-w-xl flex-col overflow-clip"
				showCloseButton={!isRerunning}
			>
				<DialogHeader>
					<DialogTitle>Rerun Workflow</DialogTitle>
					<DialogDescription>
						Review or edit the previous inputs for{" "}
						<span className="font-semibold">{workflowName}</span>{" "}
						before starting a new execution.
					</DialogDescription>
				</DialogHeader>
				<form
					className="flex min-h-0 flex-col gap-6"
					onSubmit={(event) => {
						event.preventDefault();
						if (ready && !isRerunning) onConfirm(values);
					}}
				>
					<div className="min-h-0 max-h-[55dvh] overflow-auto px-1 py-1">
						{!workflowId ? (
							<p role="alert">
								The workflow is no longer available.
							</p>
						) : parameters.isError ? (
							<div className="space-y-3">
								<p role="alert">
									Couldn’t load the workflow’s parameters. It
									may have been removed or you may no longer
									have access.
								</p>
								<Button
									type="button"
									variant="outline"
									onClick={() => void parameters.refetch()}
								>
									Try Again
								</Button>
							</div>
						) : parameters.data === undefined ? (
							<p
								role="status"
								className="flex items-center gap-2 text-muted-foreground"
							>
								<Loader2 className="size-4 animate-spin motion-reduce:animate-none" />{" "}
								Loading Parameters…
							</p>
						) : (
							<WorkflowParametersForm
								parameters={parameters.data}
								values={values}
								onChange={setOverrides}
								onExecute={onConfirm}
								isExecuting={isRerunning}
								showExecuteButton={false}
								renderAsDiv
							/>
						)}
					</div>
					{error && (
						<p role="alert" className="text-sm text-destructive">
							{error}
						</p>
					)}
					<DialogFooter className="shrink-0 [&_button]:min-h-11">
						<Button
							type="button"
							variant="outline"
							disabled={isRerunning}
							onClick={() => onOpenChange(false)}
						>
							Cancel
						</Button>
						<Button type="submit" disabled={!ready || isRerunning}>
							{isRerunning ? (
								<Loader2 className="mr-2 size-4 animate-spin motion-reduce:animate-none" />
							) : (
								<Play className="mr-2 size-4" />
							)}
							{isRerunning ? "Rerunning…" : "Rerun Workflow"}
						</Button>
					</DialogFooter>
				</form>
			</DialogContent>
		</Dialog>
	);
}
