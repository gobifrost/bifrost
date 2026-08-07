import { useState, useMemo, useCallback } from "react";
import {
	RefreshCw,
	Filter,
	X,
	ArrowUp,
	ArrowDown,
	Loader2,
	Network,
	Trash2,
	Building2,
	Shield,
} from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { useWorkflows, useUpdateWorkflow } from "@/hooks/useWorkflows";
import { useForms, useUpdateForm } from "@/hooks/useForms";
import { useAgents, useUpdateAgent } from "@/hooks/useAgents";
import { useApplications, useUpdateApplication } from "@/hooks/useApplications";
import { useOrganizations } from "@/hooks/useOrganizations";
import { useRoles } from "@/hooks/useRoles";
import {
	useDependencyGraph,
	type EntityType as DependencyEntityType,
} from "@/hooks/useDependencyGraph";
import { WorkflowDeactivationDialog } from "@/components/editor/WorkflowDeactivationDialog";
import { authFetch } from "@/lib/api-client";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import type { components } from "@/lib/v1";

import {
	EntityCard,
	OrgDropTarget,
	RoleDropTarget,
	FilterPopover,
	DependencyGraphDialog,
	DeleteConfirmDialog,
	normalizeEntities,
	type EntityType,
	type RelationshipFilter,
	type SortOption,
	type ApplicationPublic,
} from "@/components/entity-management";

export function EntityManagement() {
	const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
	const [searchTerm, setSearchTerm] = useState("");
	const [typeFilter, setTypeFilter] = useState<string>("all");
	const [orgFilter, setOrgFilter] = useState<string>("all");
	const [accessFilter, setAccessFilter] = useState<string>("all");
	const [usageFilter, setUsageFilter] = useState<string>("all");
	const [sortBy, setSortBy] = useState<SortOption>("name");
	const [sortAsc, setSortAsc] = useState(true);
	const [isUpdating, setIsUpdating] = useState(false);
	const [updatingMessage, setUpdatingMessage] = useState("Updating...");

	// Relationship filter state
	const [relationshipFilter, setRelationshipFilter] = useState<RelationshipFilter | null>(null);
	const [isGraphDialogOpen, setIsGraphDialogOpen] = useState(false);

	// Confirm delete state (for non-workflow entities: forms, agents, apps)
	const [confirmDeleteOpen, setConfirmDeleteOpen] = useState(false);
	const [confirmDeleteEntities, setConfirmDeleteEntities] = useState<
		{ id: string; name: string; entityType: EntityType; slug?: string }[]
	>([]);
	const [isDeleting, setIsDeleting] = useState(false);

	// Workflow delete state
	const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
	const [deletingWorkflowId, setDeletingWorkflowId] = useState<string | null>(null);
	const [pendingDeactivations, setPendingDeactivations] = useState<
		components["schemas"]["PendingDeactivation"][]
	>([]);
	const [availableReplacements, setAvailableReplacements] = useState<
		components["schemas"]["AvailableReplacement"][]
	>([]);
	// Track workflow IDs that returned 409 during bulk delete (for Phase 2)
	const [conflictWorkflowIds, setConflictWorkflowIds] = useState<string[]>([]);

	// Fetch all entity types
	const {
		data: workflows,
		isLoading: loadingWorkflows,
		refetch: refetchWorkflows,
	} = useWorkflows();
	const {
		data: forms,
		isLoading: loadingForms,
		refetch: refetchForms,
	} = useForms();
	const {
		data: agents,
		isLoading: loadingAgents,
		refetch: refetchAgents,
	} = useAgents();
	const {
		data: appsResponse,
		isLoading: loadingApps,
		refetch: refetchApps,
	} = useApplications();
	const { data: organizations } = useOrganizations();
	const { data: roles } = useRoles();

	// Fetch dependency graph when relationship filter is active
	const {
		data: graphData,
		isLoading: loadingGraph,
	} = useDependencyGraph(
		relationshipFilter ? relationshipFilter.entityType as DependencyEntityType : undefined,
		relationshipFilter?.entityId,
		3, // Fixed depth of 3 for relationship filtering
	);

	// Update mutations
	const updateWorkflow = useUpdateWorkflow();
	const updateForm = useUpdateForm();
	const updateAgent = useUpdateAgent();
	const updateApplication = useUpdateApplication();

	const isLoading = loadingWorkflows || loadingForms || loadingAgents || loadingApps;

	// Normalize and combine all entities
	const allEntities = useMemo(
		() => normalizeEntities(workflows ?? [], forms ?? [], agents ?? [], appsResponse?.applications ?? []),
		[workflows, forms, agents, appsResponse],
	);

	// Extract related entity IDs from graph data
	const relatedEntityIds = useMemo(() => {
		if (!relationshipFilter || !graphData?.nodes) return null;

		const ids = new Set<string>();
		for (const node of graphData.nodes) {
			const parts = node.id.split(":");
			if (parts.length === 2) {
				ids.add(parts[1]);
			} else {
				ids.add(node.id);
			}
		}
		return ids;
	}, [relationshipFilter, graphData]);

	// Apply filters
	const filteredEntities = useMemo(() => {
		let result = allEntities;

		if (relationshipFilter && relatedEntityIds) {
			// Relationship mode: only filter by related IDs + search
			result = result.filter((e) => relatedEntityIds.has(e.id));
		} else {
			// Normal mode: apply all standard filters
			if (typeFilter !== "all") {
				result = result.filter((e) => e.entityType === typeFilter);
			}

			if (orgFilter !== "all") {
				if (orgFilter === "global") {
					result = result.filter((e) => !e.organizationId);
				} else {
					result = result.filter((e) => e.organizationId === orgFilter);
				}
			}

			if (accessFilter !== "all") {
				result = result.filter((e) => e.accessLevel === accessFilter);
			}

			if (usageFilter !== "all") {
				if (usageFilter === "unused") {
					result = result.filter((e) => e.usedByCount === 0);
				} else if (usageFilter === "in_use") {
					result = result.filter(
						(e) => e.usedByCount !== null && e.usedByCount > 0,
					);
				}
			}
		}

		if (searchTerm) {
			const term = searchTerm.toLowerCase();
			result = result.filter((e) => e.name.toLowerCase().includes(term));
		}

		result = [...result].sort((a, b) => {
			let cmp = 0;
			switch (sortBy) {
				case "name":
					cmp = a.name.localeCompare(b.name);
					break;
				case "date":
					cmp = a.createdAt.localeCompare(b.createdAt);
					break;
				case "type":
					cmp = a.entityType.localeCompare(b.entityType);
					break;
			}
			return sortAsc ? cmp : -cmp;
		});

		return result;
	}, [
		allEntities,
		relationshipFilter,
		relatedEntityIds,
		typeFilter,
		orgFilter,
		accessFilter,
		usageFilter,
		searchTerm,
		sortBy,
		sortAsc,
	]);

	const activeFilterCount =
		(typeFilter !== "all" ? 1 : 0) +
		(orgFilter !== "all" ? 1 : 0) +
		(accessFilter !== "all" ? 1 : 0) +
		(usageFilter !== "all" ? 1 : 0);

	const handleClearFilters = () => {
		setTypeFilter("all");
		setOrgFilter("all");
		setAccessFilter("all");
		setUsageFilter("all");
	};

	const handleRefresh = () => {
		refetchWorkflows();
		refetchForms();
		refetchAgents();
		refetchApps();
	};

	const handleSelectEntity = (entityId: string, selected: boolean) => {
		setSelectedIds((prev) => {
			const next = new Set(prev);
			if (selected) {
				next.add(entityId);
			} else {
				next.delete(entityId);
			}
			return next;
		});
	};

	const handleSelectAll = (selected: boolean) => {
		if (selected) {
			setSelectedIds(new Set(filteredEntities.map((e) => e.id)));
		} else {
			setSelectedIds(new Set());
		}
	};

	const handleShowRelationships = useCallback((entityId: string, entityType: EntityType, entityName: string) => {
		setRelationshipFilter({
			entityId,
			entityType,
			entityName,
		});
	}, []);

	const handleClearRelationshipFilter = useCallback(() => {
		setRelationshipFilter(null);
	}, []);

	// Delete workflow handlers
	const handleDeleteWorkflow = useCallback(
		async (workflowId: string) => {
			setDeletingWorkflowId(workflowId);

			try {
				const response = await authFetch(`/api/workflows/${workflowId}`, {
					method: "DELETE",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({}),
				});

				if (response.status === 409) {
					const conflict = await response.json();
					setPendingDeactivations(conflict.pending_deactivations ?? []);
					setAvailableReplacements(conflict.available_replacements ?? []);
					setDeleteDialogOpen(true);
				} else if (response.ok) {
					toast.success("Workflow deleted");
					refetchWorkflows();
					setDeletingWorkflowId(null);
				} else {
					const error = await response.json();
					toast.error(error.detail || "Failed to delete workflow");
					setDeletingWorkflowId(null);
				}
			} catch {
				toast.error("Failed to delete workflow");
				setDeletingWorkflowId(null);
			}
		},
		[refetchWorkflows],
	);

	const handleForceDeactivate = useCallback(async () => {
		const idsToProcess = conflictWorkflowIds.length > 0
			? conflictWorkflowIds
			: deletingWorkflowId ? [deletingWorkflowId] : [];

		if (idsToProcess.length === 0) return;

		setDeleteDialogOpen(false);
		setUpdatingMessage(`Deleting ${idsToProcess.length} workflow${idsToProcess.length > 1 ? "s" : ""}...`);
		setIsUpdating(true);

		let successCount = 0;
		const deletedIds: string[] = [];

		try {
			for (const id of idsToProcess) {
				try {
					const response = await authFetch(`/api/workflows/${id}`, {
						method: "DELETE",
						headers: { "Content-Type": "application/json" },
						body: JSON.stringify({ force_deactivation: true }),
					});
					if (response.ok) {
						successCount++;
						deletedIds.push(id);
					} else {
						const error = await response.json();
						toast.error(error.detail || "Failed to delete workflow");
					}
				} catch {
					toast.error("Failed to delete workflow");
				}
			}

			if (successCount > 0) {
				toast.success(`Deleted ${successCount} workflow${successCount > 1 ? "s" : ""}`);
				refetchWorkflows();
				setSelectedIds((prev) => {
					const s = new Set(prev);
					for (const id of deletedIds) s.delete(id);
					return s;
				});
			}
		} finally {
			setDeletingWorkflowId(null);
			setConflictWorkflowIds([]);
			setPendingDeactivations([]);
			setAvailableReplacements([]);
			setIsUpdating(false);
		}
	}, [conflictWorkflowIds, deletingWorkflowId, refetchWorkflows]);

	const handleApplyReplacements = useCallback(
		async (replacements: Record<string, string>) => {
			const idsToProcess = conflictWorkflowIds.length > 0
				? conflictWorkflowIds
				: deletingWorkflowId ? [deletingWorkflowId] : [];

			if (idsToProcess.length === 0) return;

			setDeleteDialogOpen(false);
			setUpdatingMessage(`Deleting ${idsToProcess.length} workflow${idsToProcess.length > 1 ? "s" : ""}...`);
			setIsUpdating(true);

			let successCount = 0;
			const deletedIds: string[] = [];

			try {
				for (const id of idsToProcess) {
					try {
						const response = await authFetch(`/api/workflows/${id}`, {
							method: "DELETE",
							headers: { "Content-Type": "application/json" },
							body: JSON.stringify({ replacements }),
						});
						if (response.ok) {
							successCount++;
							deletedIds.push(id);
						} else {
							const error = await response.json();
							toast.error(error.detail || "Failed to delete workflow");
						}
					} catch {
						toast.error("Failed to delete workflow");
					}
				}

				if (successCount > 0) {
					toast.success(
						`Deleted ${successCount} workflow${successCount > 1 ? "s" : ""} with replacements applied`,
					);
					refetchWorkflows();
					setSelectedIds((prev) => {
						const s = new Set(prev);
						for (const id of deletedIds) s.delete(id);
						return s;
					});
				}
			} finally {
				setDeletingWorkflowId(null);
				setConflictWorkflowIds([]);
				setPendingDeactivations([]);
				setAvailableReplacements([]);
				setIsUpdating(false);
			}
		},
		[conflictWorkflowIds, deletingWorkflowId, refetchWorkflows],
	);

	const handleCancelDelete = useCallback(() => {
		setDeleteDialogOpen(false);
		setDeletingWorkflowId(null);
		setConflictWorkflowIds([]);
		setPendingDeactivations([]);
		setAvailableReplacements([]);
	}, []);

	// Unified delete handler that dispatches by entity type
	const handleDeleteEntity = useCallback(
		(entityId: string, entityName: string, entityType: EntityType) => {
			if (entityType === "workflow") {
				handleDeleteWorkflow(entityId);
				return;
			}
			const entity = allEntities.find((e) => e.id === entityId);
			const slug =
				entityType === "app" && entity
					? (entity.original as ApplicationPublic).slug
					: undefined;
			setConfirmDeleteEntities([{ id: entityId, name: entityName, entityType, slug }]);
			setConfirmDeleteOpen(true);
		},
		[handleDeleteWorkflow, allEntities],
	);

	// Bulk delete handler
	const handleBulkDelete = useCallback(() => {
		const selectedEntities = allEntities.filter((e) => selectedIds.has(e.id));

		const entitiesToDelete = selectedEntities.map((e) => ({
			id: e.id,
			name: e.name,
			entityType: e.entityType,
			slug: e.entityType === "app" ? (e.original as ApplicationPublic).slug : undefined,
		}));

		setConfirmDeleteEntities(entitiesToDelete);
		setConfirmDeleteOpen(true);
	}, [allEntities, selectedIds]);

	// Execute confirmed deletes
	const handleConfirmDelete = useCallback(async () => {
		setIsDeleting(true);
		const totalCount = confirmDeleteEntities.length;
		const nonWorkflows = confirmDeleteEntities.filter((e) => e.entityType !== "workflow");
		const workflowsToDelete = confirmDeleteEntities.filter((e) => e.entityType === "workflow");

		setConfirmDeleteOpen(false);
		setConfirmDeleteEntities([]);
		setIsDeleting(false);
		setUpdatingMessage(`Deleting ${totalCount} ${totalCount === 1 ? "entity" : "entities"}...`);
		setIsUpdating(true);

		let successCount = 0;
		let failCount = 0;
		const deletedIds: string[] = [];

		try {
			if (nonWorkflows.length > 0) {
				const results = await Promise.allSettled(
					nonWorkflows.map(async (entity) => {
						let url: string;
						if (entity.entityType === "form") {
							url = `/api/forms/${entity.id}`;
						} else if (entity.entityType === "agent") {
							url = `/api/agents/${entity.id}`;
						} else if (entity.entityType === "app") {
							url = `/api/applications/${entity.id}`;
						} else {
							return;
						}
						const response = await authFetch(url, { method: "DELETE" });
						if (!response.ok) {
							throw new Error(`Failed to delete ${entity.name}`);
						}
						deletedIds.push(entity.id);
					}),
				);

				for (const result of results) {
					if (result.status === "fulfilled") successCount++;
					else failCount++;
				}
			}

			if (workflowsToDelete.length > 0) {
				const allConflictDeactivations: components["schemas"]["PendingDeactivation"][] = [];
				const allConflictReplacements: components["schemas"]["AvailableReplacement"][] = [];
				const conflictIds: string[] = [];

				for (const wf of workflowsToDelete) {
					try {
						const response = await authFetch(`/api/workflows/${wf.id}`, {
							method: "DELETE",
							headers: { "Content-Type": "application/json" },
							body: JSON.stringify({}),
						});

						if (response.status === 409) {
							const conflict = await response.json();
							conflictIds.push(wf.id);
							allConflictDeactivations.push(
								...(conflict.pending_deactivations ?? []),
							);
							allConflictReplacements.push(
								...(conflict.available_replacements ?? []),
							);
						} else if (response.ok) {
							successCount++;
							deletedIds.push(wf.id);
						} else {
							failCount++;
						}
					} catch {
						failCount++;
					}
				}

				if (conflictIds.length > 0) {
					setConflictWorkflowIds(conflictIds);
					setPendingDeactivations(allConflictDeactivations);
					setAvailableReplacements(allConflictReplacements);
					setDeleteDialogOpen(true);
				}
			}

			if (successCount > 0) {
				toast.success(
					`Deleted ${successCount} of ${totalCount} ${totalCount === 1 ? "entity" : "entities"}`,
				);
				refetchForms();
				refetchAgents();
				refetchApps();
				refetchWorkflows();
				setSelectedIds((prev) => {
					const next = new Set(prev);
					for (const id of deletedIds) {
						next.delete(id);
					}
					return next;
				});
			}
			if (failCount > 0) {
				toast.error(`Failed to delete ${failCount} ${failCount === 1 ? "entity" : "entities"}`);
			}
		} finally {
			setIsUpdating(false);
		}
	}, [confirmDeleteEntities, refetchForms, refetchAgents, refetchApps, refetchWorkflows]);

	const allSelected =
		filteredEntities.length > 0 &&
		filteredEntities.every((e) => selectedIds.has(e.id));
	const someSelected =
		filteredEntities.some((e) => selectedIds.has(e.id)) && !allSelected;

	const cycleSortBy = () => {
		const options: SortOption[] = ["name", "date", "type"];
		const currentIndex = options.indexOf(sortBy);
		setSortBy(options[(currentIndex + 1) % options.length]);
	};

	const handleOrgDrop = useCallback(
		async (entityIds: string[], orgId: string | null) => {
			setUpdatingMessage("Updating...");
			setIsUpdating(true);
			try {
				for (const entityId of entityIds) {
					const entity = allEntities.find((e) => e.id === entityId);
					if (!entity) continue;

					try {
						if (entity.entityType === "workflow") {
							await updateWorkflow.mutateAsync(entityId, {
								organization_id: orgId,
							});
						} else if (entity.entityType === "form") {
							await updateForm.mutateAsync({
								params: { path: { form_id: entityId } },
								body: { organization_id: orgId, clear_roles: false },
							});
						} else if (entity.entityType === "agent") {
							await updateAgent.mutateAsync({
								params: { path: { agent_id: entityId } },
								body: { organization_id: orgId, clear_roles: false },
							});
						} else if (entity.entityType === "app") {
							const app = entity.original as ApplicationPublic;
							await updateApplication.mutateAsync({
								params: { path: { app_id: app.id } },
								body: { scope: orgId ?? "global" },
							});
						}
					} catch (error) {
						toast.error(`Failed to update ${entity.entityType}`, {
							description:
								error instanceof Error ? error.message : "Unknown error",
						});
					}
				}
			} finally {
				setIsUpdating(false);
			}
		},
		[allEntities, updateWorkflow, updateForm, updateAgent, updateApplication],
	);

	const handleRoleDrop = useCallback(
		async (entityIds: string[], roleIdOrAccessLevel: string) => {
			setUpdatingMessage("Updating...");
			setIsUpdating(true);
			const isAccessLevel = roleIdOrAccessLevel === "authenticated";
			const isClearRoles = roleIdOrAccessLevel === "clear-roles";

			try {
				for (const entityId of entityIds) {
					const entity = allEntities.find((e) => e.id === entityId);
					if (!entity) continue;

					try {
						if (entity.entityType === "workflow") {
							if (isClearRoles) {
								await updateWorkflow.mutateAsync(entityId, {
									access_level: "role_based",
									clear_roles: true,
								});
							} else {
								await updateWorkflow.mutateAsync(entityId, {
									access_level: isAccessLevel ? "authenticated" : "role_based",
								});
							}
						} else if (entity.entityType === "form") {
							if (isClearRoles) {
								await updateForm.mutateAsync({
									params: { path: { form_id: entityId } },
									body: {
										access_level: "role_based",
										clear_roles: true,
									},
								});
							} else {
								await updateForm.mutateAsync({
									params: { path: { form_id: entityId } },
									body: {
										access_level: isAccessLevel ? "authenticated" : "role_based",
										clear_roles: false,
									},
								});
							}
						} else if (entity.entityType === "agent") {
							if (isClearRoles) {
								await updateAgent.mutateAsync({
									params: { path: { agent_id: entityId } },
									body: {
										access_level: "role_based",
										clear_roles: true,
									},
								});
							} else {
								await updateAgent.mutateAsync({
									params: { path: { agent_id: entityId } },
									body: {
										access_level: isAccessLevel ? "authenticated" : "role_based",
										clear_roles: false,
									},
								});
							}
						} else if (entity.entityType === "app") {
							const app = entity.original as ApplicationPublic;
							if (isClearRoles) {
								await updateApplication.mutateAsync({
									params: { path: { app_id: app.id } },
									body: {
										access_level: "role_based",
										role_ids: [],
									},
								});
							} else {
								await updateApplication.mutateAsync({
									params: { path: { app_id: app.id } },
									body: {
										access_level: isAccessLevel ? "authenticated" : "role_based",
										role_ids: isAccessLevel ? [] : undefined,
									},
								});
							}
						}
					} catch (error) {
						toast.error(`Failed to update ${entity.entityType}`, {
							description:
								error instanceof Error ? error.message : "Unknown error",
						});
					}
				}
			} finally {
				setIsUpdating(false);
			}
		},
		[allEntities, updateWorkflow, updateForm, updateAgent, updateApplication],
	);

	return (
		<div className="h-full flex flex-col space-y-6">
			{/* Header */}
			<div className="flex items-center justify-between">
				<div>
					<h1 className="text-4xl font-extrabold tracking-tight">
						Entity Management
					</h1>
					<p className="mt-2 text-muted-foreground">
						Manage organization and access settings for workflows, forms,
						agents, and apps
					</p>
				</div>
				<Button
					variant="outline"
					size="icon"
					onClick={handleRefresh}
					title="Refresh"
				>
					<RefreshCw className="h-4 w-4" />
				</Button>
			</div>

			{/* Main Content - Two Column Layout */}
			<div className="flex-1 min-h-0 grid grid-cols-1 lg:grid-cols-5 gap-6">
				{/* Left Column: Entities List */}
				<div className="lg:col-span-2 flex flex-col min-h-0">
					{/* Relationship Filter Banner */}
					{relationshipFilter && (
						<div className="flex items-center gap-2 mb-4 p-3 rounded-lg bg-accent/50 ring-1 ring-foreground/5">
							<Network className="h-4 w-4 text-primary" />
							<span className="text-sm">
								Related to: <strong>{relationshipFilter.entityName}</strong>
							</span>
							<div className="flex items-center gap-1 ml-auto">
								<Button
									variant="outline"
									size="sm"
									onClick={() => setIsGraphDialogOpen(true)}
								>
									<Network className="h-4 w-4 mr-1" />
									View Graph
								</Button>
								<Button
									variant="ghost"
									size="sm"
									onClick={handleClearRelationshipFilter}
								>
									<X className="h-4 w-4 mr-1" />
									Clear
								</Button>
							</div>
						</div>
					)}

					{/* Toolbar: Select All + Search + Filters + Sort */}
					<div className="flex items-center gap-2 mb-4">
						<Checkbox
							checked={allSelected}
							onCheckedChange={handleSelectAll}
							aria-label="Select all"
							title={
								allSelected
									? "Deselect all"
									: someSelected
										? "Select all"
										: "Select all"
							}
							className={cn(
								"h-5 w-5",
								someSelected && "data-[state=checked]:bg-muted",
							)}
							ref={(el) => {
								if (el) {
									(el as HTMLButtonElement).dataset.state = someSelected
										? "indeterminate"
										: allSelected
											? "checked"
											: "unchecked";
								}
							}}
						/>
						<div className="relative flex-1">
							<Input
								placeholder="Search entities..."
								value={searchTerm}
								onChange={(e) => setSearchTerm(e.target.value)}
								className="pr-8"
							/>
							{searchTerm && (
								<Button
									variant="ghost"
									size="icon"
									className="absolute right-0 top-0 h-full w-8 hover:bg-transparent"
									onClick={() => setSearchTerm("")}
								>
									<X className="h-4 w-4" />
								</Button>
							)}
						</div>
						{!relationshipFilter && (
							<>
								<FilterPopover
									typeFilter={typeFilter}
									setTypeFilter={setTypeFilter}
									orgFilter={orgFilter}
									setOrgFilter={setOrgFilter}
									accessFilter={accessFilter}
									setAccessFilter={setAccessFilter}
									usageFilter={usageFilter}
									setUsageFilter={setUsageFilter}
									organizations={organizations ?? []}
									activeFilterCount={activeFilterCount}
									onClearFilters={handleClearFilters}
								/>
								<Button
									variant="outline"
									size="sm"
									className="h-9"
									onClick={cycleSortBy}
									title={`Sort by: ${sortBy}`}
								>
									{sortAsc ? (
										<ArrowUp className="h-4 w-4 mr-2" />
									) : (
										<ArrowDown className="h-4 w-4 mr-2" />
									)}
									{sortBy === "name"
										? "Name"
										: sortBy === "date"
											? "Date"
											: "Type"}
								</Button>
								<Button
									variant="ghost"
									size="icon"
									className="h-9 w-9"
									onClick={() => setSortAsc((prev) => !prev)}
									title={sortAsc ? "Ascending" : "Descending"}
								>
									{sortAsc ? (
										<ArrowUp className="h-4 w-4" />
									) : (
										<ArrowDown className="h-4 w-4" />
									)}
								</Button>
							</>
						)}
					</div>

					{/* Selection info */}
					{(selectedIds.size > 0 || isUpdating) && (
						<div className="flex items-center gap-2 mb-2 text-sm text-muted-foreground">
							{isUpdating && (
								<Loader2 className="h-4 w-4 animate-spin text-primary" />
							)}
							<span>
								{isUpdating
									? updatingMessage
									: `${selectedIds.size} selected`}
							</span>
							{selectedIds.size > 0 && !isUpdating && (
								<>
									<Button
										variant="ghost"
										size="sm"
										className="h-6 px-2 text-xs"
										onClick={() => setSelectedIds(new Set())}
									>
										Clear
									</Button>
									<Button
										variant="ghost"
										size="sm"
										className="h-6 px-2 text-xs text-destructive hover:text-destructive hover:bg-destructive/10"
										onClick={handleBulkDelete}
									>
										<Trash2 className="h-3 w-3 mr-1" />
										Delete
									</Button>
								</>
							)}
						</div>
					)}

					{/* Entity List */}
					<div className="flex-1 min-h-0 overflow-y-auto">
						{isLoading || (relationshipFilter && loadingGraph) ? (
							<div className="space-y-2">
								{[...Array(5)].map((_, i) => (
									<Skeleton key={i} className="h-16 w-full" />
								))}
							</div>
						) : filteredEntities.length > 0 ? (
							<div className="space-y-2 pr-2">
								{filteredEntities.map((entity) => (
									<EntityCard
										key={`${entity.entityType}-${entity.id}`}
										entity={entity}
										selected={selectedIds.has(entity.id)}
										onSelect={(selected) =>
											handleSelectEntity(entity.id, selected)
										}
										onShowRelationships={handleShowRelationships}
										onDelete={handleDeleteEntity}
										organizations={organizations ?? []}
										selectedIds={selectedIds}
										allEntities={allEntities}
									/>
								))}
							</div>
						) : (
							<Card>
								<CardContent className="flex flex-col items-center justify-center py-12 text-center">
									<Filter className="h-12 w-12 text-muted-foreground" />
									<h3 className="mt-4 text-lg font-semibold">
										{relationshipFilter
											? "No related entities found"
											: searchTerm || activeFilterCount > 0
												? "No entities match your filters"
												: "No entities found"}
									</h3>
									<p className="mt-2 text-sm text-muted-foreground">
										{relationshipFilter
											? "This entity has no dependencies"
											: searchTerm || activeFilterCount > 0
												? "Try adjusting your filters"
												: "Create workflows, forms, or agents to manage them here"}
									</p>
								</CardContent>
							</Card>
						)}
					</div>
				</div>

				{/* Right Column: Drop Targets */}
				<div className="lg:col-span-3 flex flex-col gap-4 min-h-0">
					{/* Organizations Section */}
					<div className="flex-1 min-h-0 flex flex-col">
						<div className="flex items-center gap-2 mb-3">
							<Building2 className="h-5 w-5 text-muted-foreground" />
							<h3 className="text-lg font-semibold">Organizations</h3>
						</div>
						<div className="space-y-1.5 overflow-y-auto">
							<OrgDropTarget organization={null} onDrop={handleOrgDrop} />
							{organizations?.map((org) => (
								<OrgDropTarget
									key={org.id}
									organization={org}
									onDrop={handleOrgDrop}
								/>
							))}
						</div>
					</div>

					{/* Access Levels Section */}
					<div className="flex-1 min-h-0 flex flex-col">
						<div className="flex items-center gap-2 mb-3">
							<Shield className="h-5 w-5 text-muted-foreground" />
							<h3 className="text-lg font-semibold">Access Levels</h3>
						</div>
						<div className="space-y-1.5 overflow-y-auto">
							<RoleDropTarget role="authenticated" onDrop={handleRoleDrop} />
							<RoleDropTarget role="clear-roles" onDrop={handleRoleDrop} />
							{roles?.map((role) => (
								<RoleDropTarget
									key={role.id}
									role={role}
									onDrop={handleRoleDrop}
								/>
							))}
						</div>
					</div>
				</div>
			</div>

			{/* Dependency Graph Dialog */}
			<DependencyGraphDialog
				open={isGraphDialogOpen}
				onOpenChange={setIsGraphDialogOpen}
				entityName={relationshipFilter?.entityName ?? ""}
				entityType={relationshipFilter?.entityType ?? null}
				graphData={graphData ?? null}
				isLoading={loadingGraph}
			/>

			{/* Workflow Deactivation Dialog (for delete confirmation) */}
			<WorkflowDeactivationDialog
				pendingDeactivations={pendingDeactivations}
				availableReplacements={availableReplacements}
				open={deleteDialogOpen}
				onResolve={(replacements, workflowsToDeactivate) => {
					const hasReplacements = Object.keys(replacements).length > 0;
					const hasDeactivations = workflowsToDeactivate.length > 0;
					if (hasReplacements) {
						handleApplyReplacements(replacements);
					} else if (hasDeactivations) {
						handleForceDeactivate();
					}
				}}
				onCancel={handleCancelDelete}
			/>

			{/* Confirm Delete Dialog */}
			<DeleteConfirmDialog
				open={confirmDeleteOpen}
				onOpenChange={(open) => {
					if (!open && !isDeleting) {
						setConfirmDeleteOpen(false);
						setConfirmDeleteEntities([]);
					}
				}}
				entities={confirmDeleteEntities}
				isDeleting={isDeleting}
				onConfirm={handleConfirmDelete}
				onCancel={() => {
					setConfirmDeleteOpen(false);
					setConfirmDeleteEntities([]);
				}}
			/>
		</div>
	);
}
