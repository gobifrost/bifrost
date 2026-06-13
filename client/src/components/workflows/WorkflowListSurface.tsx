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
	Shield,
	Unlink,
	Users,
	Webhook,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
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
	isLoading?: boolean;
	isPlatformAdmin: boolean;
	canManageWorkflows: boolean;
	getOrgName: (orgId: string | null | undefined) => string;
	hasGlobalKey?: boolean;
	workflowsWithKeys?: Set<string>;
	openingWorkflowId?: string | null;
	onViewHistory: (workflow: WorkflowListItem) => void;
	onOpenCode?: (workflow: WorkflowListItem) => void;
	onEditScope?: (workflow: WorkflowListItem) => void;
	onEditEndpoint?: (workflow: WorkflowListItem) => void;
	onResolveOrphaned?: (workflow: WorkflowListItem) => void;
	onExecute: (workflow: WorkflowListItem) => void;
	onOpenEmpty?: () => void;
	emptySearchActive?: boolean;
}

function WorkflowTypeBadge({ workflow }: { workflow: WorkflowListItem }) {
	if (workflow.type === "tool") {
		return (
			<Badge
				variant="secondary"
				className="bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300"
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
				className="bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300"
				title="Provides data for forms and apps"
			>
				<Database className="mr-1 h-3 w-3" />
				Data Provider
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

function executeLabel(workflow: WorkflowListItem): string {
	if (workflow.type === "tool") return "Test Tool";
	if (workflow.type === "data_provider") return "Preview Data";
	return "Execute Workflow";
}

export function WorkflowListSurface({
	workflows,
	viewMode,
	isLoading = false,
	isPlatformAdmin,
	canManageWorkflows,
	getOrgName,
	hasGlobalKey = false,
	workflowsWithKeys = new Set<string>(),
	openingWorkflowId = null,
	onViewHistory,
	onOpenCode,
	onEditScope,
	onEditEndpoint,
	onResolveOrphaned,
	onExecute,
	onOpenEmpty,
	emptySearchActive = false,
}: WorkflowListSurfaceProps) {
	if (isLoading) {
		return viewMode === "grid" ? (
			<div className="grid gap-4 grid-cols-[repeat(auto-fill,minmax(300px,1fr))]">
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
							? "No workflows match your search"
							: "No workflows available"}
					</h3>
					<p className="mt-2 text-sm text-muted-foreground">
						{emptySearchActive
							? "Try adjusting your search term or clear the filter"
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
							{isPlatformAdmin && (
								<DataTableHead className="w-0 whitespace-nowrap">
									Organization
								</DataTableHead>
							)}
							<DataTableHead>Name</DataTableHead>
							<DataTableHead>Description</DataTableHead>
							<DataTableHead className="w-0 whitespace-nowrap text-right">
								<span className="sr-only">Actions</span>
							</DataTableHead>
						</DataTableRow>
					</DataTableHeader>
					<DataTableBody>
						{workflows.map((workflow) => (
							<DataTableRow key={workflow.id ?? workflow.name}>
								{isPlatformAdmin && (
									<DataTableCell className="w-0 whitespace-nowrap">
										{workflow.organization_id ? (
											<Badge variant="outline" className="text-xs">
												<Building2 className="mr-1 h-3 w-3" />
												{getOrgName(workflow.organization_id)}
											</Badge>
										) : (
											<Badge variant="default" className="text-xs">
												<Globe className="mr-1 h-3 w-3" />
												Global
											</Badge>
										)}
									</DataTableCell>
								)}
								<DataTableCell className="font-mono font-medium">
									<div className="flex items-center gap-2">
										<span>{workflow.name}</span>
										{workflow.is_orphaned && (
											<Badge
												variant="outline"
												className="bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-300 cursor-pointer hover:bg-yellow-200 dark:hover:bg-yellow-800"
												title="This workflow's file no longer exists. Click to resolve."
												onClick={(e) => {
													e.stopPropagation();
													onResolveOrphaned?.(workflow);
												}}
											>
												<Unlink className="mr-1 h-3 w-3" />
												Orphaned
											</Badge>
										)}
									</div>
								</DataTableCell>
								<DataTableCell className="max-w-xs truncate text-muted-foreground">
									{workflow.description || (
										<span className="italic">No description</span>
									)}
								</DataTableCell>
								<DataTableCell className="w-0 whitespace-nowrap text-right">
									<div className="flex items-center justify-end gap-1">
										<Button
											variant="outline"
											size="icon-sm"
											onClick={() => onViewHistory(workflow)}
											title="View history"
										>
											<History className="h-4 w-4" />
										</Button>
										{onOpenCode && (
											<Button
												variant="outline"
												size="icon-sm"
												onClick={() => onOpenCode(workflow)}
												disabled={
													openingWorkflowId ===
													(workflow.id ?? workflow.name)
												}
												title="Open in editor"
											>
												{openingWorkflowId === (workflow.id ?? workflow.name) ? (
													<Loader2 className="h-4 w-4 animate-spin" />
												) : (
													<Code2 className="h-4 w-4" />
												)}
											</Button>
										)}
										{workflow.is_solution_managed && (
											<SolutionManagedBadge solutionId={workflow.solution_id} />
										)}
										{isPlatformAdmin &&
											canManageWorkflows &&
											!workflow.is_solution_managed &&
											onEditScope && (
												<Button
													variant="ghost"
													size="icon-sm"
													onClick={() => onEditScope(workflow)}
													title="Edit organization scope"
												>
													<Pencil className="h-4 w-4" />
												</Button>
											)}
										<Button
											variant="outline"
											size="icon-sm"
											onClick={() => onExecute(workflow)}
											title="Execute"
										>
											<PlayCircle className="h-4 w-4" />
										</Button>
									</div>
								</DataTableCell>
							</DataTableRow>
						))}
					</DataTableBody>
				</DataTable>
			</div>
		);
	}

	return (
		<div className="grid gap-4 grid-cols-[repeat(auto-fill,minmax(300px,1fr))]">
			{workflows.map((workflow) => (
				<Card
					key={workflow.id ?? workflow.name}
					className="hover:border-primary transition-colors flex flex-col"
				>
					<CardHeader className="pb-2">
						<div className="flex items-center justify-between gap-2 mb-3">
							<div className="flex items-center gap-2">
								<WorkflowTypeBadge workflow={workflow} />
							</div>
							<div className="flex items-center gap-1">
								<Tooltip>
									<TooltipTrigger asChild>
										<Button
											variant="outline"
											size="icon-sm"
											onClick={() => onViewHistory(workflow)}
											title="View history"
										>
											<History className="h-3.5 w-3.5" />
										</Button>
									</TooltipTrigger>
									<TooltipContent>View history</TooltipContent>
								</Tooltip>
								{onOpenCode && (
									<Tooltip>
										<TooltipTrigger asChild>
											<Button
												variant="outline"
												size="icon-sm"
												onClick={() => onOpenCode(workflow)}
												disabled={
													openingWorkflowId ===
													(workflow.id ?? workflow.name)
												}
												title="Open in editor"
											>
												{openingWorkflowId === (workflow.id ?? workflow.name) ? (
													<Loader2 className="h-3.5 w-3.5 animate-spin" />
												) : (
													<Code2 className="h-3.5 w-3.5" />
												)}
											</Button>
										</TooltipTrigger>
										<TooltipContent>Open in editor</TooltipContent>
									</Tooltip>
								)}
								{workflow.is_solution_managed && (
									<SolutionManagedBadge solutionId={workflow.solution_id} />
								)}
								{isPlatformAdmin &&
									canManageWorkflows &&
									!workflow.is_solution_managed &&
									onEditScope && (
										<Button
											variant="outline"
											size="icon-sm"
											onClick={() => onEditScope(workflow)}
											title="Edit organization scope"
										>
											<Pencil className="h-3.5 w-3.5" />
										</Button>
									)}
							</div>
						</div>

						<CardTitle className="font-mono text-base break-all">
							{workflow.name}
						</CardTitle>
						{workflow.description && (
							<CardDescription className="mt-2 text-sm break-words line-clamp-2">
								{workflow.description}
							</CardDescription>
						)}
					</CardHeader>

					<CardContent className="pt-0 mt-auto space-y-3">
						<div className="flex items-center gap-2 text-xs text-muted-foreground">
							{workflow.category && <span>{workflow.category}</span>}
							{workflow.category &&
								(isPlatformAdmin ||
									workflow.endpoint_enabled ||
									workflow.is_orphaned ||
									workflow.disable_global_key) && <span>·</span>}
							{isPlatformAdmin && (
								<span className="flex items-center gap-1">
									{workflow.organization_id ? (
										<>
											<Building2 className="h-3 w-3" />
											{getOrgName(workflow.organization_id)}
										</>
									) : (
										<>
											<Globe className="h-3 w-3" />
											Global
										</>
									)}
								</span>
							)}
							{isPlatformAdmin && workflow.access_level && (
								<>
									<span>·</span>
									<Tooltip>
										<TooltipTrigger asChild>
											<span className="flex items-center gap-1 cursor-help">
												{workflow.access_level === "role_based" ? (
													<>
														<Shield className="h-3 w-3" />
														Roles
													</>
												) : (
													<>
														<Users className="h-3 w-3" />
														{workflow.access_level === "everyone"
															? "Everyone"
															: "Auth"}
													</>
												)}
											</span>
										</TooltipTrigger>
										<TooltipContent>
											{workflow.access_level === "authenticated"
												? "Any signed-in user except external users can execute"
												: workflow.access_level === "everyone"
													? "Any signed-in user, including external users can execute"
													: "Role-based access required"}
										</TooltipContent>
									</Tooltip>
								</>
							)}
						</div>

						{(workflow.endpoint_enabled ||
							workflow.is_orphaned ||
							workflow.disable_global_key) && (
							<div className="flex flex-wrap items-center gap-1.5">
								{workflow.is_orphaned && (
									<Badge
										variant="outline"
										className="bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-300 cursor-pointer hover:bg-yellow-200 dark:hover:bg-yellow-800"
										title="This workflow's file no longer exists. Click to resolve."
										onClick={(e) => {
											e.stopPropagation();
											onResolveOrphaned?.(workflow);
										}}
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
													  workflowsWithKeys.has(workflow.name ?? "")
													? "default"
													: "outline"
										}
										className={`transition-colors ${
											onEditEndpoint ? "cursor-pointer" : ""
										} ${
											workflow.public_endpoint
												? "bg-orange-600 hover:bg-orange-700 border-orange-600"
												: hasGlobalKey ||
													  workflowsWithKeys.has(workflow.name ?? "")
													? "bg-green-600 hover:bg-green-700"
													: "text-muted-foreground hover:bg-accent"
										}`}
										onClick={(e) => {
											e.stopPropagation();
											onEditEndpoint?.(workflow);
										}}
										title={
											workflow.public_endpoint
												? "Public webhook endpoint - no authentication required"
												: hasGlobalKey ||
													  workflowsWithKeys.has(workflow.name ?? "")
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
										className="bg-orange-600 text-white hover:bg-orange-700 border-orange-600"
										title="This workflow only accepts workflow-specific API keys (global keys are disabled)"
									>
										Global Opt-Out
									</Badge>
								)}
							</div>
						)}

						<Button className="w-full" onClick={() => onExecute(workflow)}>
							<PlayCircle className="mr-2 h-4 w-4" />
							{executeLabel(workflow)}
						</Button>
					</CardContent>
				</Card>
			))}
		</div>
	);
}
