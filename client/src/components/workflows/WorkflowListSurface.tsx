import { Link, useNavigate } from "react-router-dom";
import {
	AlertTriangle,
	Bot,
	Building2,
	Code,
	Code2,
	Database,
	Globe,
	History,
	Loader2,
	Pencil,
	PlayCircle,
	Radio,
	Shield,
	Unlink,
	Users,
	Webhook,
} from "lucide-react";

import { ResourceCatalogCard } from "@/components/catalog/ResourceCatalogCard";
import { RecordActionsMenu } from "@/components/common/RecordActionsMenu";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import { Skeleton } from "@/components/ui/skeleton";
import {
	Tooltip,
	TooltipContent,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import { SolutionManagedBadge } from "@/components/solutions/SolutionManagedBadge";
import type { components } from "@/lib/v1";

type BaseWorkflow = components["schemas"]["WorkflowMetadata"];

export type WorkflowListItem = BaseWorkflow & {
	is_orphaned?: boolean;
	access_level?: "authenticated" | "everyone" | "role_based";
	is_solution_managed?: boolean;
	solution_id?: string | null;
};

export interface WorkflowListSurfaceProps {
	workflows: WorkflowListItem[];
	viewMode: "grid" | "table";
	/** Query string to preserve the originating page in native navigation links. */
	navigationSearch?: string;
	isLoading?: boolean;
	isPlatformAdmin: boolean;
	canManageWorkflows: boolean;
	getOrgName: (orgId: string | null | undefined) => string;
	hasGlobalKey?: boolean;
	workflowsWithKeys?: Set<string>;
	openingWorkflowId?: string | null;
	onOpenCode?: (workflow: WorkflowListItem) => void;
	onEditScope?: (workflow: WorkflowListItem) => void;
	onEditEndpoint?: (workflow: WorkflowListItem) => void;
	onResolveOrphaned?: (workflow: WorkflowListItem) => void;
	onExecute: (workflow: WorkflowListItem) => void;
	/**
	 * Deep link for service rows. Services never one-shot execute, so
	 * without this they render inert; with it they navigate to the
	 * service detail surface instead.
	 */
	getServiceHref?: (workflow: WorkflowListItem) => string | undefined;
	onOpenEmpty?: () => void;
	emptySearchActive?: boolean;
}

function WorkflowTypeBadge({ workflow }: { workflow: WorkflowListItem }) {
	if (workflow.type === "tool") {
		return (
			<Badge
				variant="secondary"
				className="bg-[var(--bf-info-soft)] text-[var(--bf-info)]"
				title={workflow.tool_description || "Available as AI tool"}
			>
				<Bot className="mr-1 h-3 w-3" />
				Tool
			</Badge>
		);
	}
	if (workflow.type === "data_provider") {
		return (
			<Badge
				variant="secondary"
				className="bg-[var(--bf-info-soft)] text-[var(--bf-info)]"
				title="Provides data for forms and apps"
			>
				<Database className="mr-1 h-3 w-3" />
				Data Provider
			</Badge>
		);
	}
	if (workflow.type === "service") {
		return (
			<Badge
				variant="secondary"
				className="bg-[var(--bf-info-soft)] text-[var(--bf-info)]"
				title="Long-lived supervised service — managed via start/stop, not one-shot execution"
			>
				<Radio className="mr-1 h-3 w-3" />
				Service
			</Badge>
		);
	}
	return (
		<Badge variant="secondary" title="Executable workflow">
			<PlayCircle className="mr-1 h-3 w-3" />
			Workflow
		</Badge>
	);
}

function workflowTypeLabel(workflow: WorkflowListItem): string {
	if (workflow.type === "tool") return "Tool";
	if (workflow.type === "data_provider") return "Data Provider";
	if (workflow.type === "service") return "Service";
	return "Workflow";
}

function workflowExecuteHref(
	workflow: WorkflowListItem,
	navigationSearch: string,
): string {
	return `/workflows/${encodeURIComponent(workflow.name ?? "")}/execute${navigationSearch}`;
}

export function WorkflowListSurface({
	workflows,
	viewMode,
	navigationSearch = "",
	isLoading = false,
	isPlatformAdmin,
	canManageWorkflows,
	getOrgName,
	hasGlobalKey = false,
	workflowsWithKeys = new Set<string>(),
	openingWorkflowId = null,
	onOpenCode,
	onEditScope,
	onEditEndpoint,
	onResolveOrphaned,
	onExecute,
	getServiceHref,
	onOpenEmpty,
	emptySearchActive = false,
}: WorkflowListSurfaceProps) {
	const navigate = useNavigate();

	const renderActions = (workflow: WorkflowListItem) => (
		<RecordActionsMenu label={`${workflow.name} actions`}>
			<DropdownMenuItem asChild className="min-h-11">
				<Link
					to={`/history?workflow=${encodeURIComponent(workflow.id ?? "")}`}
				>
					<History aria-hidden="true" className="size-4" />
					View history
				</Link>
			</DropdownMenuItem>
			{onOpenCode && (
				<DropdownMenuItem
					className="min-h-11"
					disabled={
						openingWorkflowId === (workflow.id ?? workflow.name)
					}
					onSelect={() => onOpenCode(workflow)}
				>
					{openingWorkflowId === (workflow.id ?? workflow.name) ? (
						<Loader2
							aria-hidden="true"
							className="size-4 animate-spin motion-reduce:animate-none"
						/>
					) : (
						<Code2 aria-hidden="true" className="size-4" />
					)}
					Open in editor
				</DropdownMenuItem>
			)}
			{isPlatformAdmin &&
				canManageWorkflows &&
				!workflow.is_solution_managed &&
				onEditScope && (
					<DropdownMenuItem
						className="min-h-11"
						onSelect={() => onEditScope(workflow)}
					>
						<Pencil aria-hidden="true" className="size-4" />
						Edit
					</DropdownMenuItem>
				)}
			{workflow.endpoint_enabled && onEditEndpoint && (
				<DropdownMenuItem
					className="min-h-11"
					onSelect={() => onEditEndpoint(workflow)}
				>
					<Webhook aria-hidden="true" className="size-4" />
					Edit endpoint
				</DropdownMenuItem>
			)}
			{workflow.is_orphaned && onResolveOrphaned && (
				<DropdownMenuItem
					className="min-h-11"
					onSelect={() => onResolveOrphaned(workflow)}
				>
					<Unlink aria-hidden="true" className="size-4" />
					Resolve missing file
				</DropdownMenuItem>
			)}
		</RecordActionsMenu>
	);

	if (isLoading) {
		return viewMode === "grid" ? (
			<div className="grid grid-cols-1 gap-4 sm:grid-cols-[repeat(auto-fill,minmax(min(100%,300px),1fr))]">
				{[...Array(6)].map((_, i) => (
					<Skeleton key={i} className="h-56 w-full" />
				))}
			</div>
		) : (
			<div className="space-y-2">
				{[...Array(3)].map((_, i) => (
					<Skeleton key={i} className="h-12 w-full" />
				))}
			</div>
		);
	}

	if (workflows.length === 0) {
		return (
			<Card>
				<CardContent className="flex flex-col items-center justify-center py-12 text-center">
					<Code className="h-12 w-12 text-muted-foreground" />
					<h3 className="mt-4 text-lg font-semibold">
						{emptySearchActive
							? "No workflows match your filters"
							: "No workflows available"}
					</h3>
					<p className="mt-2 text-sm text-muted-foreground">
						{emptySearchActive
							? "Adjust your search or clear the filters to see more workflows."
							: "No workflows have been registered in the workflow engine"}
					</p>
					{!emptySearchActive && onOpenEmpty && (
						<Button
							variant="outline"
							onClick={onOpenEmpty}
							className="mt-4"
						>
							<Code className="mr-2 h-4 w-4" />
							Open editor
						</Button>
					)}
				</CardContent>
			</Card>
		);
	}

	if (viewMode === "table") {
		return (
			<div className="flex-1 min-h-0">
				<DataTable className="max-h-full">
					<DataTableHeader>
						<DataTableRow>
							<DataTableHead>Workflow</DataTableHead>
							<DataTableHead className="w-0 whitespace-nowrap text-right">
								<span className="sr-only">Actions</span>
							</DataTableHead>
						</DataTableRow>
					</DataTableHeader>
					<DataTableBody>
						{workflows.map((workflow) => {
							const executeHref = workflowExecuteHref(
								workflow,
								navigationSearch,
							);
							const serviceHref =
								workflow.type === "service"
									? getServiceHref?.(workflow)
									: undefined;
							// Services run under supervision (start/stop), never
							// one-shot execution — same dead-end as orphaned rows,
							// unless a detail link is provided.
							const executable =
								!workflow.is_orphaned &&
								(workflow.type !== "service" ||
									serviceHref !== undefined);
							const href = serviceHref ?? executeHref;
							return (
								<DataTableRow
									key={workflow.id ?? workflow.name}
									clickable={executable}
									href={executable ? href : undefined}
									onClick={
										executable
											? () => navigate(href)
											: undefined
									}
								>
									<DataTableCell className="min-w-0 whitespace-normal align-top">
										{executable ? (
											<Link
												to={href}
												className="inline-flex min-h-11 min-w-0 items-center font-mono font-medium text-left [overflow-wrap:anywhere] hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
											>
												{workflow.name}
											</Link>
										) : (
											<span className="inline-flex min-h-11 min-w-0 items-center font-mono font-medium text-left [overflow-wrap:anywhere]">
												{workflow.name}
											</span>
										)}
										<div className="mb-2 flex flex-wrap items-center gap-2">
											<WorkflowTypeBadge
												workflow={workflow}
											/>
											{isPlatformAdmin && (
												<span className="text-xs text-muted-foreground">
													{getOrgName(
														workflow.organization_id,
													)}
												</span>
											)}
											{workflow.is_orphaned && (
												<Badge
													variant="outline"
													className="bg-[var(--bf-warning-soft)] text-[var(--bf-warning)]"
												>
													<Unlink className="mr-1 h-3 w-3" />
													Orphaned
												</Badge>
											)}
										</div>
										<p className="max-w-prose text-sm text-muted-foreground [overflow-wrap:anywhere]">
											{workflow.description ||
												"No description"}
										</p>
									</DataTableCell>
									<DataTableCell
										className="w-0 whitespace-nowrap text-right"
										onClick={(event) =>
											event.stopPropagation()
										}
									>
										<div className="flex items-center justify-end gap-1">
											{workflow.is_solution_managed && (
												<SolutionManagedBadge
													solutionId={
														workflow.solution_id
													}
												/>
											)}
											{renderActions(workflow)}
										</div>
									</DataTableCell>
								</DataTableRow>
							);
						})}
					</DataTableBody>
				</DataTable>
			</div>
		);
	}

	return (
		<div className="grid grid-cols-1 gap-4 sm:grid-cols-[repeat(auto-fill,minmax(min(100%,300px),1fr))]">
			{workflows.map((workflow) => {
				const serviceHref =
					workflow.type === "service"
						? getServiceHref?.(workflow)
						: undefined;
				const openable =
					!workflow.is_orphaned &&
					(workflow.type !== "service" || serviceHref !== undefined);
				return (
				<ResourceCatalogCard
					key={workflow.id ?? workflow.name}
					icon={
						<span className="inline-grid size-12 shrink-0 place-items-center overflow-hidden rounded-[var(--bf-radius-control)] border border-primary/15 bg-primary/10 text-primary">
							<Code aria-hidden="true" className="size-6" />
						</span>
					}
					title={workflow.name}
					subtitle={
						<span className="inline-flex flex-wrap items-center gap-1.5">
							<span>{workflowTypeLabel(workflow)}</span>
							{workflow.category ? (
								<>
									<span>·</span>
									<span>{workflow.category}</span>
								</>
							) : null}
						</span>
					}
					description={workflow.description || "No description"}
					action={
						<div className="flex items-center justify-end gap-1">
							{workflow.is_solution_managed && (
								<SolutionManagedBadge
									solutionId={workflow.solution_id}
								/>
							)}
							{renderActions(workflow)}
						</div>
					}
					footer={
						<div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
							<div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
								{isPlatformAdmin && (
									<span className="flex min-w-0 items-center gap-1">
										{workflow.organization_id ? (
											<>
												<Building2 className="size-3.5 shrink-0" />
												<span className="truncate">
													{getOrgName(
														workflow.organization_id,
													)}
												</span>
											</>
										) : (
											<>
												<Globe className="size-3.5 shrink-0" />
												<span className="truncate">
													Global
												</span>
											</>
										)}
									</span>
								)}
								{isPlatformAdmin && workflow.access_level && (
									<Tooltip>
										<TooltipTrigger asChild>
											<span className="relative z-10 flex cursor-help items-center gap-1">
												{workflow.access_level ===
												"role_based" ? (
													<>
														<Shield className="h-3 w-3" />
														Roles
													</>
												) : (
													<>
														<Users className="h-3 w-3" />
														{workflow.access_level ===
														"everyone"
															? "Everyone"
															: "Auth"}
													</>
												)}
											</span>
										</TooltipTrigger>
										<TooltipContent>
											{workflow.access_level ===
											"authenticated"
												? "Any signed-in user except external users can execute"
												: workflow.access_level ===
													  "everyone"
													? "Any signed-in user, including external users can execute"
													: "Role-based access required"}
										</TooltipContent>
									</Tooltip>
								)}
							</div>
						</div>
					}
					onOpen={() => {
						if (serviceHref) navigate(serviceHref);
						else onExecute(workflow);
					}}
					href={
						serviceHref ??
						(!workflow.is_orphaned && workflow.type !== "service"
							? workflowExecuteHref(workflow, navigationSearch)
							: undefined)
					}
					disabled={!openable}
				>
					{(workflow.endpoint_enabled ||
						workflow.is_orphaned ||
						workflow.disable_global_key) && (
						<div className="flex min-w-0 flex-wrap items-center gap-1.5">
							{workflow.is_orphaned && (
								<Badge
									variant="outline"
									className="bg-[var(--bf-warning-soft)] text-[var(--bf-warning)]"
									title="This workflow's file no longer exists."
								>
									<Unlink className="mr-1 h-3 w-3" />
									Orphaned
								</Badge>
							)}
							{workflow.endpoint_enabled && (
								<Badge
									variant={
										workflow.public_endpoint
											? "destructive"
											: hasGlobalKey ||
												  workflowsWithKeys.has(
														workflow.name ?? "",
												  )
												? "default"
												: "outline"
									}
									className={`motion-reduce:transition-none ${
										workflow.public_endpoint
											? "bg-[var(--bf-warning-soft)] text-[var(--bf-warning)] border-[var(--bf-warning)]/20"
											: hasGlobalKey ||
												  workflowsWithKeys.has(
														workflow.name ?? "",
												  )
												? "bg-[var(--bf-success-soft)] text-[var(--bf-success)]"
												: "text-muted-foreground hover:bg-accent"
									}`}
									title={
										workflow.public_endpoint
											? "Public webhook endpoint - no authentication required"
											: hasGlobalKey ||
												  workflowsWithKeys.has(
														workflow.name ?? "",
												  )
												? "HTTP endpoint enabled with API key"
												: "HTTP endpoint (no API key configured)"
									}
								>
									{workflow.public_endpoint ? (
										<AlertTriangle className="mr-1 h-3 w-3" />
									) : (
										<Webhook className="mr-1 h-3 w-3" />
									)}
									Endpoint
								</Badge>
							)}
							{workflow.disable_global_key && (
								<Badge
									variant="outline"
									className="bg-[var(--bf-warning-soft)] text-[var(--bf-warning)] border-[var(--bf-warning)]/20"
									title="This workflow only accepts workflow-specific API keys (global keys are disabled)"
								>
									Global Opt-Out
								</Badge>
							)}
						</div>
					)}
				</ResourceCatalogCard>
				);
			})}
		</div>
	);
}
