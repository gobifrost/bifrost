import { useQueryClient } from "@tanstack/react-query";
import { useLayoutEffect, useRef, useState } from "react";
import { Pin, Play, ChevronDown, Plus, X, Settings2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogHeader,
	DialogTitle,
	DialogDescription,
} from "@/components/ui/dialog";
import { WorkflowParametersForm } from "@/components/workflows/WorkflowParametersForm";
import type { components } from "@/lib/v1";
type WorkflowMetadata = components["schemas"]["WorkflowMetadata"];
import { ExecutionOrganizationSelect } from "@/components/execution/ExecutionOrganizationSelect";
import { ExecutionDrawer } from "@/pages/ExecutionHistory/components/ExecutionDrawer";
import { RunStatusBadge } from "@/components/execution/RunStatusBadge";
import { useWorkflowsMetadata, useExecuteWorkflow } from "@/hooks/useWorkflows";
import { useExecutions } from "@/hooks/useExecutions";
import { useExecutionHistory } from "@/hooks/useExecutionStream";
import { useAuth } from "@/contexts/AuthContext";
import { useAuthorization } from "@/services/authorization";
import { orgTarget } from "@/lib/authorization";
import { getErrorMessage } from "@/lib/api-error";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import {
	DropdownMenu,
	DropdownMenuTrigger,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuSeparator,
	DropdownMenuLabel,
} from "@/components/ui/dropdown-menu";

interface WorkflowLink {
	workflowId: string;
	label: string;
	pinned: boolean;
}

function LinkedRun({
	workflow,
	label,
	onWatch,
}: {
	workflow: WorkflowMetadata;
	label: string;
	onWatch: (id: string) => void;
}) {
	const { data } = useExecutions(undefined, {
		workflow_id: workflow.id,
		limit: 1,
	});
	const run = data?.executions?.[0];
	return run ? (
		<Button
			variant="ghost"
			size="sm"
			onClick={() => onWatch(run.execution_id)}
			aria-label={`Watch ${label}`}
		>
			<RunStatusBadge status={run.status} />
		</Button>
	) : (
		<span className="text-xs text-muted-foreground">No Runs Yet</span>
	);
}

/** Review spike: integration-owned links are kept in page-session state. */
export function IntegrationWorkflowLinks({
	integrationName,
	canManage,
}: {
	integrationName: string;
	canManage: boolean;
}) {
	const { data } = useWorkflowsMetadata();
	const workflows = (data?.workflows || []).filter(
		(w) => w.type !== "service",
	);
	const [links, setLinks] = useState<WorkflowLink[]>([]);
	const [manageOpen, setManageOpen] = useState(false);
	const [workflowId, setWorkflowId] = useState("");
	const [label, setLabel] = useState("");
	const available = workflows.filter(
		(w) => !links.some((link) => link.workflowId === w.id),
	);
	const linked = links.map((link) => ({
		...link,
		workflow: workflows.find((w) => w.id === link.workflowId),
	}));
	const toolbarRef = useRef<HTMLDivElement>(null);
	const measurementRef = useRef<HTMLDivElement>(null);
	const menuRef = useRef<HTMLButtonElement>(null);
	const [visiblePins, setVisiblePins] = useState(0);
	const pinned = linked.filter((link) => link.pinned);
	const menuLinks = [...pinned, ...linked.filter((link) => !link.pinned)];
	// Measure the actual buttons so labels, fonts and available width determine overflow.
	useLayoutEffect(() => {
		const toolbar = toolbarRef.current;
		const measurement = measurementRef.current;
		const menu = menuRef.current;
		if (!toolbar || !measurement || !menu) return;
		const measure = () => {
			toolbar.style.minWidth = `${menu.offsetWidth}px`;
			const gap = parseFloat(getComputedStyle(toolbar).columnGap) || 0;
			let used = menu.offsetWidth;
			let count = 0;
			for (const button of Array.from(measurement.children)) {
				used += (button as HTMLElement).offsetWidth + gap;
				if (used > toolbar.clientWidth) break;
				count++;
			}
			setVisiblePins(count);
		};
		measure();
		const observer = new ResizeObserver(measure);
		observer.observe(toolbar);
		observer.observe(measurement);
		observer.observe(menu);
		return () => observer.disconnect();
	});
	const launch = (id: string) => {
		const workflow = workflows.find((w) => w.id === id);
		if (workflow) {
			setSelected(workflow);
			setOrgId(undefined);
			setError(null);
		}
	};
	const editLink = (id: string, values: Partial<WorkflowLink>) =>
		setLinks((current) =>
			current.map((link) =>
				link.workflowId === id ? { ...link, ...values } : link,
			),
		);
	const { user } = useAuth();
	const authorization = useAuthorization();
	useExecutionHistory({
		scope: "GLOBAL",
		userId: user?.id,
		includeOthers: authorization.canAnywhere("executions.read.all"),
		enabled: links.length > 0,
	});
	const execute = useExecuteWorkflow();
	const queryClient = useQueryClient();
	const busy = useRef(false);
	const [selected, setSelected] = useState<WorkflowMetadata | null>(null);
	const [orgId, setOrgId] = useState<string>();
	const [executionId, setExecutionId] = useState<string | null>(null);
	const [error, setError] = useState<string | null>(null);
	const start = async (input: Record<string, unknown>) => {
		if (!selected || busy.current) return;
		busy.current = true;
		setError(null);
		try {
			const run = await execute.mutateAsync({
				body: {
					workflow_id: selected.id,
					input_data: input,
					...(orgId ? { org_id: orgId } : {}),
					form_id: null,
					transient: false,
					code: null,
					script_name: null,
				},
			});
			setSelected(null);
			setExecutionId(run.execution_id);
			void queryClient.invalidateQueries({
				queryKey: ["get", "/api/executions"],
			});
		} catch (e) {
			setError(getErrorMessage(e, "Could not start workflow"));
		} finally {
			busy.current = false;
		}
	};
	return (
		<>
			<div
				role="group"
				aria-label="Integration workflows"
				ref={toolbarRef}
				className="relative flex w-full min-w-0 items-center justify-end gap-2 sm:w-0 sm:flex-1"
			>
				<div
					ref={measurementRef}
					aria-hidden="true"
					inert
					className="pointer-events-none invisible absolute left-0 top-0 flex w-max gap-2"
				>
					{pinned.map((link) => (
						<Button
							key={link.workflowId}
							variant="outline"
							tabIndex={-1}
						>
							<Play className="size-4" />
							<span className="max-w-[24ch] truncate">
								{link.label || link.workflow?.name}
							</span>
						</Button>
					))}
				</div>
				{pinned.slice(0, visiblePins).map((link) => (
					<Button
						key={link.workflowId}
						variant="outline"
						disabled={
							!link.workflow ||
							!authorization.canAt(
								"workflows.execute",
								orgTarget(link.workflow.organization_id),
							)
						}
						onClick={() => launch(link.workflowId)}
						title={link.workflow?.name}
					>
						<Play className="size-4" />
						<span className="max-w-[24ch] truncate">
							{link.label || link.workflow?.name}
						</span>
					</Button>
				))}
				<DropdownMenu>
					<DropdownMenuTrigger asChild>
						<Button
							ref={menuRef}
							variant="outline"
							title={
								pinned.length > visiblePins
									? `${pinned.length - visiblePins} pinned actions are available in this menu`
									: "All linked workflows"
							}
						>
							<ChevronDown className="size-4" />
							Workflows
							{links.length > 0 && (
								<span className="text-muted-foreground">
									{links.length}
								</span>
							)}
						</Button>
					</DropdownMenuTrigger>
					<DropdownMenuContent
						align="end"
						className="w-80 max-w-[calc(100vw-2rem)]"
					>
						<DropdownMenuLabel>Linked Workflows</DropdownMenuLabel>
						<div className="max-h-72 overflow-auto">
							{menuLinks.map((link) => (
								<DropdownMenuItem
									key={link.workflowId}
									className="min-h-11"
									disabled={
										!link.workflow ||
										!authorization.canAt(
											"workflows.execute",
											orgTarget(
												link.workflow.organization_id,
											),
										)
									}
									onSelect={() => launch(link.workflowId)}
								>
									<Play className="size-4" />
									<span className="min-w-0 flex-1">
										<span className="block truncate">
											{link.label || link.workflow?.name}
										</span>
										<span className="block truncate text-xs text-muted-foreground">
											{link.workflow?.name ||
												"Workflow Unavailable"}
										</span>
									</span>
									{link.pinned && (
										<Pin className="size-3.5 text-muted-foreground" />
									)}
								</DropdownMenuItem>
							))}
						</div>
						{links.length === 0 && (
							<p className="px-2 py-3 text-sm text-muted-foreground">
								No workflows linked yet.
							</p>
						)}
						{canManage && (
							<>
								<DropdownMenuSeparator />
								<DropdownMenuItem
									className="min-h-11"
									onSelect={() => setManageOpen(true)}
								>
									<Settings2 className="size-4" />
									Manage Linked Workflows
								</DropdownMenuItem>
							</>
						)}
					</DropdownMenuContent>
				</DropdownMenu>
			</div>
			<Dialog open={manageOpen} onOpenChange={setManageOpen}>
				<DialogContent className="sm:max-w-2xl max-h-[85dvh] overflow-auto">
					<DialogHeader>
						<DialogTitle>Linked Workflows</DialogTitle>
						<DialogDescription>
							Link workflows to {integrationName}. Every workflow
							appears in the menu; pin frequent actions to show a
							button at the top.
						</DialogDescription>
					</DialogHeader>
					{linked.length > 0 && (
						<div className="max-h-80 space-y-4 overflow-auto">
							{linked.map((link) => (
								<div
									key={link.workflowId}
									className="space-y-2 border-b pb-4"
								>
									<div className="flex items-end gap-2">
										<div className="min-w-0 flex-1 space-y-2">
											<Label
												htmlFor={`label-${link.workflowId}`}
											>
												Label
											</Label>
											<Input
												id={`label-${link.workflowId}`}
												value={link.label}
												onChange={(e) =>
													editLink(link.workflowId, {
														label: e.target.value,
													})
												}
											/>
											<p className="break-all text-xs text-muted-foreground">
												{link.workflow?.name ||
													"Workflow Unavailable"}
											</p>
										</div>
										<Button
											variant="ghost"
											size="icon"
											aria-label={`Unlink ${link.label}`}
											onClick={() =>
												setLinks((current) =>
													current.filter(
														(l) =>
															l.workflowId !==
															link.workflowId,
													),
												)
											}
										>
											<X className="size-4" />
										</Button>
									</div>
									<div className="flex flex-wrap items-center justify-between gap-2">
										<label className="inline-flex min-h-11 items-center gap-2 text-sm">
											<Checkbox
												checked={link.pinned}
												onCheckedChange={(checked) =>
													editLink(link.workflowId, {
														pinned:
															checked === true,
													})
												}
											/>
											Pin as Top Button
										</label>
										{link.workflow && (
											<LinkedRun
												workflow={link.workflow}
												label={link.label}
												onWatch={(id) => {
													setManageOpen(false);
													setExecutionId(id);
												}}
											/>
										)}
									</div>
								</div>
							))}
						</div>
					)}
					<div className="space-y-3 border-t pt-4">
						<h3 className="text-sm font-medium">Link a Workflow</h3>
						<Label htmlFor="linked-workflow">Workflow</Label>
						<Select
							value={workflowId}
							onValueChange={setWorkflowId}
						>
							<SelectTrigger
								id="linked-workflow"
								className="w-full"
							>
								<SelectValue placeholder="Choose a workflow" />
							</SelectTrigger>
							<SelectContent>
								{available.map((w) => (
									<SelectItem key={w.id} value={w.id}>
										{w.name}
									</SelectItem>
								))}
							</SelectContent>
						</Select>
						<Label htmlFor="new-workflow-label">Button Label</Label>
						<Input
							id="new-workflow-label"
							placeholder="For example, Sync Now"
							value={label}
							onChange={(e) => setLabel(e.target.value)}
						/>
						<Button
							disabled={!workflowId || !label.trim()}
							onClick={() => {
								setLinks((current) => [
									...current,
									{
										workflowId,
										label: label.trim(),
										pinned: false,
									},
								]);
								setWorkflowId("");
								setLabel("");
							}}
						>
							<Plus className="size-4" />
							Link Workflow
						</Button>
					</div>
					<div className="flex justify-end">
						<Button
							variant="outline"
							onClick={() => setManageOpen(false)}
						>
							Done
						</Button>
					</div>
				</DialogContent>
			</Dialog>
			<Dialog
				open={!!selected}
				onOpenChange={(open) => {
					if (!open && !busy.current) setSelected(null);
				}}
			>
				<DialogContent className="max-h-[85dvh] overflow-auto sm:max-w-xl">
					<DialogHeader>
						<DialogTitle className="break-all">
							{selected?.name}
						</DialogTitle>
						<DialogDescription>
							Review the organization and inputs before starting
							this workflow.
						</DialogDescription>
					</DialogHeader>
					{selected && (
						<>
							<ExecutionOrganizationSelect
								value={orgId}
								onChange={setOrgId}
								organizationId={selected.organization_id}
								disabled={execute.isPending}
							/>
							{error && (
								<p
									role="alert"
									className="text-sm text-destructive"
								>
									{error}
								</p>
							)}
							<WorkflowParametersForm
								key={selected.id}
								parameters={selected.parameters || []}
								onExecute={start}
								isExecuting={execute.isPending}
								executeButtonText="Run Workflow"
							/>
						</>
					)}
				</DialogContent>
			</Dialog>
			<ExecutionDrawer
				executionId={executionId}
				open={!!executionId}
				onOpenChange={(open) => {
					if (!open) setExecutionId(null);
				}}
				onExecutionChange={setExecutionId}
			/>
		</>
	);
}
