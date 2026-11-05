/**
 * WorkflowSelectorDialog Component
 *
 * A dialog wrapper for workflow selection that adds role context and mismatch warnings.
 * Shows entity roles alongside the workflow list to help users understand access implications.
 *
 * Features:
 * - Left panel: Entity roles (read-only, for context)
 * - Right panel: Workflow list with role status indicators
 * - Inline warnings: Shows which workflows are missing entity roles
 * - Auto-assign option: "Select & Assign Roles" button adds missing roles to workflows
 * - Single/multi selection modes
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useReducedMotion } from "framer-motion";
import {
	AlertTriangle,
	Building2,
	Globe,
	Loader2,
	Search,
	Shield,
} from "lucide-react";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { $api } from "@/lib/api-client";
import { fetchWorkflowRolesBatch } from "@/hooks/useWorkflowRoles";
import { useOrganizations } from "@/hooks/useOrganizations";
import { useOrganizationName } from "@/hooks/useOrganizationName";
import type { components } from "@/lib/v1";

type WorkflowMetadata = components["schemas"]["WorkflowMetadata"];
type ExecutableType = components["schemas"]["ExecutableType"];
/**
 * Role representation for the dialog
 */
export interface EntityRole {
	id: string;
	name: string;
}

export interface WorkflowSelectorDialogProps {
	/** Whether the dialog is open */
	open: boolean;
	/** Callback when dialog open state changes */
	onOpenChange: (open: boolean) => void;
	/** Roles from the parent entity (form, agent, app) for context */
	entityRoles: EntityRole[];
	/** Filter by workflow type */
	workflowType?: ExecutableType;
	/** Single vs multi-select mode */
	mode: "single" | "multi";
	/** Currently selected workflow IDs */
	selectedWorkflowIds: string[];
	/** Callback with selected IDs and whether to auto-assign roles */
	onSelect: (workflowIds: string[], assignRoles: boolean) => void;
	/** Optional org scope filter */
	organizationId?: string | null;
	/** Dialog title override */
	title?: string;
	/** Dialog description override */
	description?: string;
}

/**
 * Workflow item with its role information
 */
interface WorkflowWithRoles extends WorkflowMetadata {
	roleIds: string[];
	hasMismatch: boolean;
	missingRoleNames: string[];
}

export function WorkflowSelectorDialog({
	open,
	onOpenChange,
	entityRoles,
	workflowType,
	mode,
	selectedWorkflowIds,
	onSelect,
	organizationId,
	title,
	description,
}: WorkflowSelectorDialogProps) {
	// Local state for selection (allows cancellation)
	const [localSelection, setLocalSelection] = useState<Set<string>>(
		new Set(selectedWorkflowIds),
	);
	const [assignRolesOnSelect, setAssignRolesOnSelect] = useState(true);
	const [searchQuery, setSearchQuery] = useState("");
	const [workflowRolesMap, setWorkflowRolesMap] = useState<Map<string, string[]>>(
		new Map(),
	);
	const [isLoadingRoles, setIsLoadingRoles] = useState(false);
	const prefersReducedMotion = useReducedMotion();

	// Reset local selection when dialog opens. Use the "adjusting state on
	// prop change" idiom: track the previous open state and reset during
	// render rather than in a useEffect.
	const [prevOpen, setPrevOpen] = useState(open);
	if (prevOpen !== open) {
		setPrevOpen(open);
		if (open) {
			setLocalSelection(new Set(selectedWorkflowIds));
			setSearchQuery("");
		}
	}

	// Build query params for scope
	const queryParams = useMemo(() => {
		const params: Record<string, string | undefined> = {};
		if (organizationId === null) {
			params.scope = "global";
		} else if (organizationId !== undefined) {
			params.scope = organizationId;
		}
		if (workflowType) {
			params.type = workflowType;
		}
		return params;
	}, [organizationId, workflowType]);

	// Fetch workflows
	const workflowsQuery = $api.useQuery(
		"get",
		"/api/workflows",
		{
			params: {
				query: {
					scope: queryParams.scope,
					type: queryParams.type as ExecutableType | undefined,
				},
			},
		},
		{ enabled: open },
	);
	const {
		data: workflows,
		isLoading: isLoadingWorkflows,
		error,
		refetch: refetchWorkflows,
	} = workflowsQuery;

	// Fetch roles for all workflows when dialog opens
	useEffect(() => {
		if (!open || !workflows || workflows.length === 0) return;

		const loadRoles = async () => {
			setIsLoadingRoles(true);
			try {
				const workflowIds = workflows.map((w) => w.id);
				const roleMap = await fetchWorkflowRolesBatch(workflowIds);
				setWorkflowRolesMap(roleMap);
			} catch (error) {
				console.error("Failed to load workflow roles:", error);
			} finally {
				setIsLoadingRoles(false);
			}
		};

		loadRoles();
	}, [open, workflows]);

	// Enhance workflows with role information
	const workflowsWithRoles: WorkflowWithRoles[] = useMemo(() => {
		if (!workflows) return [];

		return workflows.map((workflow) => {
			const roleIds = workflowRolesMap.get(workflow.id) || [];
			const roleIdSet = new Set(roleIds);

			// Find entity roles that are NOT in the workflow's roles
			const missingRoleIds = entityRoles.filter((r) => !roleIdSet.has(r.id));
			const missingRoleNames = missingRoleIds.map((r) => r.name);
			const hasMismatch = missingRoleIds.length > 0 && entityRoles.length > 0;

			return {
				...workflow,
				roleIds,
				hasMismatch,
				missingRoleNames,
			};
		});
	}, [workflows, workflowRolesMap, entityRoles]);

	// Filter and sort workflows
	const filteredWorkflows = useMemo(() => {
		let filtered = workflowsWithRoles;

		// Apply search filter
		if (searchQuery.trim()) {
			const query = searchQuery.toLowerCase();
			filtered = filtered.filter(
				(w) =>
					w.name?.toLowerCase().includes(query) ||
					w.description?.toLowerCase().includes(query),
			);
		}

		// Sort: global first, then alphabetically
		return [...filtered].sort((a, b) => {
			const aIsGlobal = !a.organization_id;
			const bIsGlobal = !b.organization_id;
			if (aIsGlobal !== bIsGlobal) {
				return aIsGlobal ? -1 : 1;
			}
			return (a.name ?? "").localeCompare(b.name ?? "");
		});
	}, [workflowsWithRoles, searchQuery]);

	// Count selected workflows with mismatches
	const selectedMismatchCount = useMemo(() => {
		return filteredWorkflows.filter(
			(w) => localSelection.has(w.id) && w.hasMismatch,
		).length;
	}, [filteredWorkflows, localSelection]);

	// Handle workflow selection
	const handleWorkflowToggle = useCallback(
		(workflowId: string) => {
			setLocalSelection((prev) => {
				const next = new Set(prev);
				if (mode === "single") {
					// Single select: replace selection
					next.clear();
					next.add(workflowId);
				} else {
					// Multi select: toggle
					if (next.has(workflowId)) {
						next.delete(workflowId);
					} else {
						next.add(workflowId);
					}
				}
				return next;
			});
		},
		[mode],
	);

	// Handle confirm
	const handleConfirm = useCallback(() => {
		onSelect(Array.from(localSelection), assignRolesOnSelect);
		onOpenChange(false);
	}, [localSelection, assignRolesOnSelect, onSelect, onOpenChange]);

	// Handle cancel
	const handleCancel = useCallback(() => {
		onOpenChange(false);
	}, [onOpenChange]);

	// Fetch organizations for name lookup
	const { data: organizations } = useOrganizations({});

	const resolveOrganizationName = useOrganizationName(organizations);
	const getOrgName = useCallback(
		(orgId: string | null | undefined): string | null => {
			if (!orgId) return null;
			return resolveOrganizationName(orgId);
		},
		[resolveOrganizationName],
	);

	const isLoading = isLoadingWorkflows || isLoadingRoles;
	const handleRetry = useCallback(() => {
		void refetchWorkflows();
	}, [refetchWorkflows]);

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="max-h-[min(90vh,52rem)] w-[min(calc(100vw-1rem),72rem)] overflow-hidden p-0 sm:max-w-none">
				<div className="flex max-h-[min(90vh,52rem)] min-h-0 flex-col overflow-hidden">
					<div className="border-b border-border/70 bg-muted/20 p-4 sm:p-6">
						<DialogHeader>
							<DialogTitle className="text-left">
								{title ||
									(mode === "single"
										? "Select Workflow"
										: "Select Workflows")}
							</DialogTitle>
							<DialogDescription className="max-w-prose leading-6">
								{description ||
									(entityRoles.length > 0
										? "Select workflows that should be accessible to users with the entity's roles."
										: "Select workflows to use with this entity.")}
							</DialogDescription>
						</DialogHeader>
					</div>

					<div className="flex-1 min-h-0 flex flex-col gap-4 overflow-hidden p-4 sm:p-6 lg:flex-row">
						{/* Left panel: Entity roles (context) */}
						{entityRoles.length > 0 && (
							<div className="w-full flex-shrink-0 rounded-[var(--bf-radius-surface)] border border-border/70 bg-card p-3 shadow-sm lg:w-60">
								<div className="mb-3 flex items-center gap-2">
									<Shield className="h-4 w-4 shrink-0 text-muted-foreground" />
									<span className="text-sm font-medium leading-6">
										Entity Roles
									</span>
								</div>
								<div className="space-y-1.5">
									{entityRoles.map((role) => (
										<div
											key={role.id}
											className="flex items-center gap-2 text-sm leading-6"
										>
											<div className="h-2 w-2 rounded-full bg-primary" />
											<span className="min-w-0 flex-1 [overflow-wrap:anywhere]">
												{role.name}
											</span>
										</div>
									))}
								</div>
								<div className="mt-4 border-t border-border/70 pt-3">
									<p className="text-xs leading-5 text-muted-foreground">
										These roles determine who can access the parent entity.
										Workflows should have matching roles for proper access
										control.
									</p>
								</div>
							</div>
						)}

						{/* Right panel: Workflow list */}
						<div className="flex min-h-0 flex-1 min-w-0 flex-col overflow-hidden rounded-[var(--bf-radius-surface)] border border-border/70 bg-card shadow-sm">
							{/* Search */}
							<div className="border-b border-border/70 bg-muted/20 p-3">
								<div className="relative">
									<Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
									<Input
										placeholder="Search workflows..."
										value={searchQuery}
										onChange={(e) => setSearchQuery(e.target.value)}
										className="min-h-11 pl-9"
									/>
								</div>
							</div>

							{/* Workflow list */}
							<div className="flex-1 overflow-y-auto p-2">
								{isLoading ? (
									<div className="flex items-center justify-center gap-2 py-8 text-muted-foreground">
										<Loader2
											className={cn(
												"h-6 w-6",
												!prefersReducedMotion &&
													"motion-safe:animate-spin",
											)}
										/>
										<span className="text-sm leading-6">
											Loading workflows...
										</span>
									</div>
								) : error ? (
									<div className="space-y-3 rounded-[var(--bf-radius-surface)] border border-[var(--bf-danger)]/20 bg-[var(--bf-danger-soft)]/60 p-4 text-[var(--bf-danger)]">
										<p className="text-sm leading-6">
											Failed to load workflows
										</p>
										<Button
											type="button"
											variant="outline"
											className="min-h-11"
											onClick={handleRetry}
										>
											Retry
										</Button>
									</div>
								) : filteredWorkflows.length === 0 ? (
									<div className="flex items-center justify-center py-8 text-center text-muted-foreground">
										<span className="text-sm leading-6">
											{searchQuery
												? "No workflows match your search"
												: "No workflows available"}
										</span>
									</div>
								) : (
									<div className="space-y-1">
										{filteredWorkflows.map((workflow) => {
											const isSelected = localSelection.has(workflow.id);
											return (
												<WorkflowListItem
													key={workflow.id}
													workflow={workflow}
													isSelected={isSelected}
													onToggle={() => handleWorkflowToggle(workflow.id)}
													mode={mode}
													showRoleBadges={entityRoles.length > 0}
													orgName={getOrgName(workflow.organization_id)}
												/>
											);
										})}
									</div>
								)}
							</div>
						</div>
					</div>

					{/* Footer */}
					<DialogFooter className="flex-shrink-0 flex-col items-start gap-2 border-t border-border/70 bg-muted/20 p-4 sm:flex-row sm:items-center">
						{/* Auto-assign checkbox (only shown when there are entity roles and mismatches) */}
						{entityRoles.length > 0 && selectedMismatchCount > 0 && (
							<div className="mr-auto flex flex-1 items-start gap-3 rounded-[var(--bf-radius-surface)] border border-[var(--bf-warning)]/20 bg-[var(--bf-warning-soft)]/60 p-3">
								<AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-[var(--bf-warning)]" />
								<div className="flex-1 min-w-0">
									<p className="text-sm leading-6 text-[var(--bf-warning)]">
										{selectedMismatchCount} selected workflow
										{selectedMismatchCount !== 1 ? "s have" : " has"} role
										mismatches
									</p>
									<label className="mt-1.5 flex cursor-pointer items-center gap-2">
										<Checkbox
											checked={assignRolesOnSelect}
											onCheckedChange={(checked) =>
												setAssignRolesOnSelect(checked === true)
											}
										/>
										<span className="text-xs leading-5 text-[var(--bf-warning)]">
											Auto-assign missing roles on save
										</span>
									</label>
								</div>
							</div>
						)}

						<div className="ml-auto flex gap-2">
							<Button
								variant="outline"
								className="min-h-11"
								onClick={handleCancel}
							>
								Cancel
							</Button>
							<Button
								className="min-h-11"
								onClick={handleConfirm}
								disabled={localSelection.size === 0}
							>
								{assignRolesOnSelect && selectedMismatchCount > 0
									? "Select & Assign Roles"
									: "Select"}
							</Button>
						</div>
					</DialogFooter>
				</div>
				</DialogContent>
		</Dialog>
	);
}

/**
 * Individual workflow list item
 */
interface WorkflowListItemProps {
	workflow: WorkflowWithRoles;
	isSelected: boolean;
	onToggle: () => void;
	mode: "single" | "multi";
	showRoleBadges: boolean;
	orgName: string | null;
}

function WorkflowListItem({
	workflow,
	isSelected,
	onToggle,
	mode,
	showRoleBadges,
	orgName,
}: WorkflowListItemProps) {
	return (
		<button
			type="button"
			onClick={onToggle}
			className={cn(
				"flex min-h-11 w-full items-start rounded-[var(--bf-radius-surface)] border p-3 text-left transition-colors",
				"hover:bg-accent/50",
				isSelected
					? "border-primary bg-primary/5"
					: "border-border/50 bg-transparent",
			)}
		>
			<div className="flex items-start gap-3">
				{/* Selection indicator */}
				<div className="mt-0.5 flex-shrink-0">
					{mode === "multi" ? (
						<Checkbox checked={isSelected} className="pointer-events-none" />
					) : (
						<div
							className={cn(
								"h-4 w-4 rounded-full border-2 flex items-center justify-center",
								isSelected ? "border-primary bg-primary" : "border-muted-foreground",
							)}
						>
							{isSelected && (
								<div className="h-1.5 w-1.5 rounded-full bg-white" />
							)}
						</div>
					)}
				</div>

				{/* Workflow info */}
				<div className="flex-1 min-w-0">
					<div className="flex flex-wrap items-center gap-2">
						<span className="min-w-0 font-medium leading-6 [overflow-wrap:anywhere]">
							{workflow.name}
						</span>
						{/* Organization badge */}
						{orgName ? (
							<Badge
								variant="outline"
								className="h-5 px-1.5 py-0 text-xs text-muted-foreground"
							>
								<Building2 className="h-3 w-3 mr-1" />
								<span className="max-w-[10rem] [overflow-wrap:anywhere]">
									{orgName}
								</span>
							</Badge>
						) : (
							<Badge
								variant="default"
								className="h-5 px-1.5 py-0 text-xs"
							>
								<Globe className="h-3 w-3 mr-1" />
								Global
							</Badge>
						)}
						{/* Type badge */}
						{workflow.type && workflow.type !== "workflow" && (
							<Badge variant="secondary" className="h-5 px-1.5 py-0 text-xs">
								{workflow.type === "tool" ? "Tool" : "Data Provider"}
							</Badge>
						)}
					</div>

					{workflow.description && (
						<p className="mt-0.5 text-sm leading-6 text-muted-foreground [overflow-wrap:anywhere]">
							{workflow.description}
						</p>
					)}

					{/* Role badges */}
					{showRoleBadges && (
						<div className="mt-2 flex flex-wrap items-center gap-1.5">
							{workflow.roleIds.length > 0 ? (
								<>
									<span className="text-xs leading-5 text-muted-foreground">
										Roles:
									</span>
									{workflow.roleIds.slice(0, 3).map((roleId) => (
										<Badge
											key={roleId}
											variant="secondary"
											className="h-5 px-1.5 py-0 text-xs"
										>
											{roleId.slice(0, 8)}...
										</Badge>
									))}
									{workflow.roleIds.length > 3 && (
										<span className="text-xs leading-5 text-muted-foreground">
											+{workflow.roleIds.length - 3} more
										</span>
									)}
								</>
							) : (
								<Badge
									variant="outline"
									className="h-5 px-1.5 py-0 text-xs text-muted-foreground"
								>
									No roles assigned
								</Badge>
							)}
						</div>
					)}

					{/* Mismatch warning */}
					{workflow.hasMismatch && (
						<div className="mt-2 flex items-start gap-1.5 text-[var(--bf-warning)]">
							<AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
							<span className="text-xs leading-5 [overflow-wrap:anywhere]">
								Missing: {workflow.missingRoleNames.join(", ")}
							</span>
						</div>
					)}
				</div>
			</div>
		</button>
	);
}

export default WorkflowSelectorDialog;
