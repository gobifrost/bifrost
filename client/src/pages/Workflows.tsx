import { useState, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import {
	RefreshCw,
	LayoutGrid,
	Table as TableIcon,
	PanelLeft,
} from "lucide-react";
import { useIsDesktop } from "@/hooks/useMediaQuery";
import type { CategoryCount } from "@/components/workflows/WorkflowSidebar";
import {
	WorkflowListSurface,
	type WorkflowListItem,
} from "@/components/workflows/WorkflowListSurface";
import { Button } from "@/components/ui/button";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import {
	useWorkflowsFiltered,
	useWorkflowsMetadata,
} from "@/hooks/useWorkflows";
import { useWorkflowKeys } from "@/hooks/useWorkflowKeys";
import { useAuth } from "@/contexts/AuthContext";
import { useOrganizations } from "@/hooks/useOrganizations";
import { OrphanedWorkflowDialog } from "@/components/workflows/OrphanedWorkflowDialog";
import { WorkflowEditDialog } from "@/components/workflows/WorkflowEditDialog";
import { WorkflowSidebar } from "@/components/workflows/WorkflowSidebar";
import { SearchBox } from "@/components/search/SearchBox";
import { useSearch } from "@/hooks/useSearch";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { useEditorStore } from "@/stores/editorStore";
import { fileService } from "@/services/fileService";
import { useServicesList } from "@/components/services/useServiceQueries";
import { serviceDetailHref } from "@/pages/ServiceDetail";
import { ListPageHeader } from "@/components/layout/ListPageHeader";
import { ListLoadError } from "@/components/layout/ListLoadError";
import { ListToolbar } from "@/components/layout/ListToolbar";
import {
	PageScrollArea,
	PageWorkspace,
} from "@/components/layout/PageWorkspace";
import { toast } from "sonner";
import type { components } from "@/lib/v1";

// Extend WorkflowMetadata with fields that may not be in generated types yet
type BaseWorkflow = components["schemas"]["WorkflowMetadata"];
type Workflow = BaseWorkflow & {
	is_orphaned?: boolean;
	access_level?: "authenticated" | "everyone" | "role_based";
};
type Organization = components["schemas"]["OrganizationPublic"];

export function Workflows() {
	const navigate = useNavigate();
	const { isPlatformAdmin } = useAuth();
	const { data: apiKeys } = useWorkflowKeys({ includeRevoked: false });
	const isDesktop = useIsDesktop();

	// Filter state
	const [filterOrgId, setFilterOrgId] = useState<string | null | undefined>(
		undefined,
	);
	const [searchTerm, setSearchTerm] = useState("");
	const [viewMode, setViewMode] = useState<"grid" | "table">("grid");
	const [typeFilter, setTypeFilter] = useState<string>("all");
	// Auto-collapse sidebar on smaller screens. Tracking the previous
	// `isDesktop` and adjusting state during render is the React-recommended
	// pattern for "reset state when an external value changes" — avoids the
	// extra effect+render cycle of useEffect+setState. (https://react.dev/learn/you-might-not-need-an-effect#adjusting-some-state-when-a-prop-changes)
	const [sidebarOpen, setSidebarOpen] = useState(isDesktop);
	const [prevIsDesktop, setPrevIsDesktop] = useState(isDesktop);
	if (prevIsDesktop !== isDesktop) {
		setPrevIsDesktop(isDesktop);
		setSidebarOpen(isDesktop);
	}

	// Edit workflow dialog state
	const [editDialogOpen, setEditDialogOpen] = useState(false);
	const [editingWorkflow, setEditingWorkflow] = useState<Workflow | null>(
		null,
	);
	const [editDialogInitialTab, setEditDialogInitialTab] = useState<
		string | undefined
	>(undefined);

	// Orphaned workflow dialog state
	const [orphanedDialogOpen, setOrphanedDialogOpen] = useState(false);
	const [orphanedWorkflow, setOrphanedWorkflow] = useState<Workflow | null>(
		null,
	);

	// Entity filter state
	const [selectedCategory, setSelectedCategory] = useState<string | null>(
		null,
	);
	const [selectedFormId, setSelectedFormId] = useState<string | null>(null);
	const [selectedAppId, setSelectedAppId] = useState<string | null>(null);
	const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
	const [endpointFilter, setEndpointFilter] = useState(false);
	const [orphanedFilter, setOrphanedFilter] = useState(false);

	const activeFilterCount = [
		searchTerm.trim(),
		filterOrgId !== undefined,
		typeFilter !== "all",
		selectedCategory,
		selectedFormId,
		selectedAppId,
		selectedAgentId,
		endpointFilter,
		orphanedFilter,
	].filter(Boolean).length;
	const clearFilters = () => {
		setSearchTerm("");
		setFilterOrgId(undefined);
		setTypeFilter("all");
		setSelectedCategory(null);
		setSelectedFormId(null);
		setSelectedAppId(null);
		setSelectedAgentId(null);
		setEndpointFilter(false);
		setOrphanedFilter(false);
	};

	// Open in editor state
	const [openingWorkflowId, setOpeningWorkflowId] = useState<string | null>(
		null,
	);
	const openFileInTab = useEditorStore((state) => state.openFileInTab);
	const openEditor = useEditorStore((state) => state.openEditor);
	const setSidebarPanel = useEditorStore((state) => state.setSidebarPanel);

	// Fetch workflow metadata (for file paths)
	const { data: metadataData } = useWorkflowsMetadata();
	const metadata = metadataData as { workflows: BaseWorkflow[] } | undefined;

	// Fetch organizations for org name lookup (platform admins only)
	const { data: organizations } = useOrganizations({
		enabled: isPlatformAdmin,
	});

	// Helper to get organization name from ID
	const getOrgName = (orgId: string | null | undefined): string => {
		if (!orgId) return "Global";
		const org = organizations?.find((o: Organization) => o.id === orgId);
		return org?.name || orgId;
	};

	// Fetch workflows with entity filters and org scope
	const { data, isLoading, isError, isFetching, refetch } =
		useWorkflowsFiltered({
			scope: isPlatformAdmin ? filterOrgId : undefined,
			type: typeFilter === "all" ? undefined : typeFilter,
			filterByForm: selectedFormId ?? undefined,
			filterByApp: selectedAppId ?? undefined,
			filterByAgent: selectedAgentId ?? undefined,
		});

	// Cast to Workflow type which includes is_orphaned (may not be in generated types yet)
	const workflows = useMemo(() => (data || []) as Workflow[], [data]);

	// Compute categories from workflows
	const categories = useMemo<CategoryCount[]>(() => {
		const categoryMap = new Map<string, number>();
		workflows.forEach((w) => {
			if (w.category) {
				categoryMap.set(
					w.category,
					(categoryMap.get(w.category) || 0) + 1,
				);
			}
		});
		return Array.from(categoryMap.entries())
			.map(([name, count]) => ({ name, count }))
			.sort((a, b) => a.name.localeCompare(b.name));
	}, [workflows]);

	// Apply category, endpoint, and orphaned filters
	const categoryFilteredWorkflows = useMemo(() => {
		let filtered = workflows;
		if (selectedCategory) {
			filtered = filtered.filter((w) => w.category === selectedCategory);
		}
		if (endpointFilter) {
			filtered = filtered.filter((w) => w.endpoint_enabled);
		}
		if (orphanedFilter) {
			filtered = filtered.filter((w) => w.is_orphaned);
		}
		return filtered;
	}, [workflows, selectedCategory, endpointFilter, orphanedFilter]);

	// Apply search filter (type filtering is now done server-side)
	const filteredWorkflows = useSearch(categoryFilteredWorkflows, searchTerm, [
		"name",
		"display_name",
		"description",
		"category",
		(w) => w.parameters?.map((p) => p.name).join(" ") || "",
	]);

	// Create a map of workflows that have API keys
	const workflowsWithKeys = useMemo(() => {
		if (!apiKeys) return new Set<string>();

		const workflowSet = new Set<string>();
		apiKeys.forEach((key) => {
			if (key.workflow_name && !key.revoked) {
				workflowSet.add(key.workflow_name);
			}
		});
		return workflowSet;
	}, [apiKeys]);

	const hasGlobalKey = useMemo(() => {
		if (!apiKeys) return false;
		return apiKeys.some((key) => !key.workflow_name && !key.revoked);
	}, [apiKeys]);

	const handleExecute = (workflowName: string) => {
		navigate(`/workflows/${encodeURIComponent(workflowName)}/execute`);
	};

	const handleEditWorkflow = (workflow: Workflow, tab?: string) => {
		setEditingWorkflow(workflow);
		setEditDialogInitialTab(tab);
		setEditDialogOpen(true);
	};

	const handleOpenOrphanedDialog = (workflow: Workflow) => {
		setOrphanedWorkflow(workflow);
		setOrphanedDialogOpen(true);
	};

	const handleOpenInEditor = async (workflow: Workflow) => {
		const workflowMeta = metadata?.workflows?.find((w) =>
			workflow.id ? w.id === workflow.id : w.name === workflow.name,
		);
		const relativeFilePath = workflowMeta?.relative_file_path;

		if (!relativeFilePath) {
			toast.error("Cannot open in editor: source file not found");
			return;
		}

		setOpeningWorkflowId(workflow.id ?? workflow.name ?? null);
		try {
			const fileResponse = await fileService.readFile(relativeFilePath);
			const fileName =
				relativeFilePath.split("/").pop() || relativeFilePath;
			const extension = fileName.includes(".")
				? fileName.split(".").pop()!
				: null;

			const fileMetadata = {
				name: fileName,
				path: relativeFilePath,
				type: "file" as const,
				size: 0,
				extension,
				modified: new Date().toISOString(),
				entity_type: null,
				entity_id: null,
			};

			openEditor();
			openFileInTab(
				fileMetadata,
				fileResponse.content,
				fileResponse.encoding as "utf-8" | "base64",
				fileResponse.etag,
			);
			setSidebarPanel("run");
			toast.success("Opened in editor");
		} catch (error) {
			console.error("Failed to open in editor:", error);
			toast.error("Failed to open file in editor");
		} finally {
			setOpeningWorkflowId(null);
		}
	};

	// Service rows deep-link to the Services detail route, resolved
	// against the live services list (admin-only endpoint — disabled
	// for non-admins, who get no service links).
	const { data: servicesData } = useServicesList({
		enabled: isPlatformAdmin,
	});
	const getServiceHref = (workflow: WorkflowListItem) => {
		const match = servicesData?.items.find(
			(s) => s.workflow_id === workflow.id,
		);
		return match ? serviceDetailHref(match) : undefined;
	};

	const workflowList = (
		<PageScrollArea
			aria-label="Workflow list"
			className={
				viewMode === "table"
					? "space-y-4 xl:flex xl:flex-col xl:overflow-hidden"
					: "space-y-4"
			}
		>
			{isError && (
				<ListLoadError
					resource="workflows"
					hasCachedData={data !== undefined}
					isRetrying={isFetching}
					onRetry={() => void refetch()}
				/>
			)}
			{isLoading && (
				<p role="status" className="sr-only">
					Loading workflows…
				</p>
			)}
			{(!isError || data !== undefined) && (
				<WorkflowListSurface
					workflows={filteredWorkflows as WorkflowListItem[]}
					viewMode={isDesktop ? viewMode : "grid"}
					isLoading={isLoading}
					isPlatformAdmin={isPlatformAdmin}
					canManageWorkflows={isPlatformAdmin}
					getOrgName={getOrgName}
					hasGlobalKey={hasGlobalKey}
					workflowsWithKeys={workflowsWithKeys}
					openingWorkflowId={openingWorkflowId}
					onOpenCode={handleOpenInEditor}
					onEditScope={(workflow) => handleEditWorkflow(workflow)}
					onEditEndpoint={(workflow) =>
						handleEditWorkflow(workflow, "endpoint")
					}
					onResolveOrphaned={handleOpenOrphanedDialog}
					onExecute={(workflow) => handleExecute(workflow.name ?? "")}
					getServiceHref={getServiceHref}
					onOpenEmpty={() => openEditor()}
					emptySearchActive={activeFilterCount > 0}
				/>
			)}
		</PageScrollArea>
	);

	return (
		<PageWorkspace className="mx-auto w-full max-w-7xl pb-1 xl:h-full xl:min-h-0">
			<ListPageHeader
				title="Workflows"
				description="Execute workflows directly with custom parameters"
				className="flex-row flex-nowrap sm:flex-wrap"
				actionsClassName="shrink-0 self-start"
				actions={
					<>
						{isDesktop && (
							<ToggleGroup
								aria-label="Workflow layout"
								type="single"
								value={viewMode}
								onValueChange={(value: string) =>
									value &&
									setViewMode(value as "grid" | "table")
								}
							>
								<ToggleGroupItem
									value="grid"
									aria-label="Grid view"
									size="lg"
								>
									<LayoutGrid className="h-4 w-4" />
								</ToggleGroupItem>
								<ToggleGroupItem
									value="table"
									aria-label="Table view"
									size="lg"
								>
									<TableIcon className="h-4 w-4" />
								</ToggleGroupItem>
							</ToggleGroup>
						)}
						<Button
							variant="outline"
							size="icon-lg"
							onClick={() => refetch()}
							aria-label="Refresh"
							disabled={isFetching}
						>
							<RefreshCw
								className={`h-4 w-4 ${isFetching ? "animate-spin motion-reduce:animate-none" : ""}`}
							/>
						</Button>
					</>
				}
			/>

			<ListToolbar className="items-stretch">
				<SearchBox
					value={searchTerm}
					onChange={setSearchTerm}
					placeholder="Search by name, description, or category..."
					className="w-full min-w-0 sm:flex-1"
				/>
				{isPlatformAdmin && (
					<div className="w-full min-w-0 sm:w-64">
						<OrganizationSelect
							aria-label="Organization scope"
							value={filterOrgId}
							onChange={setFilterOrgId}
							showAll={true}
							showGlobal={true}
							placeholder="All organizations"
						/>
					</div>
				)}
				<div className="flex min-w-0 flex-wrap gap-2">
					<ToggleGroup
						aria-label="Workflow type"
						className="grid w-full grid-cols-2 justify-start sm:flex sm:w-auto"
						type="single"
						value={typeFilter}
						onValueChange={(value: string) =>
							value && setTypeFilter(value)
						}
					>
						<ToggleGroupItem value="all" size="lg">
							All
						</ToggleGroupItem>
						<ToggleGroupItem value="workflow" size="lg">
							Workflows
						</ToggleGroupItem>
						<ToggleGroupItem value="tool" size="lg">
							Tools
						</ToggleGroupItem>
						<ToggleGroupItem value="data_provider" size="lg">
							Data Providers
						</ToggleGroupItem>
					</ToggleGroup>
				</div>
				{activeFilterCount > 0 && (
					<div className="flex w-full items-center justify-between gap-3">
						<p
							className="text-sm text-muted-foreground"
							role="status"
						>
							{activeFilterCount}{" "}
							{activeFilterCount === 1 ? "filter" : "filters"}{" "}
							applied
						</p>
						<Button
							type="button"
							variant="ghost"
							className="min-h-11"
							onClick={clearFilters}
						>
							Clear filters
						</Button>
					</div>
				)}
			</ListToolbar>

			{/* Main Content with Sidebar */}
			{isDesktop ? (
				<div className="flex min-h-0 flex-1 gap-6">
					{sidebarOpen ? (
						<WorkflowSidebar
							categories={categories}
							categoriesLoading={isLoading}
							selectedCategory={selectedCategory}
							onCategorySelect={setSelectedCategory}
							selectedFormId={selectedFormId}
							selectedAppId={selectedAppId}
							selectedAgentId={selectedAgentId}
							onFormSelect={setSelectedFormId}
							onAppSelect={setSelectedAppId}
							onAgentSelect={setSelectedAgentId}
							endpointFilter={endpointFilter}
							onEndpointFilterChange={setEndpointFilter}
							orphanedFilter={orphanedFilter}
							onOrphanedFilterChange={setOrphanedFilter}
							scope={
								isPlatformAdmin
									? (filterOrgId ?? undefined)
									: undefined
							}
							onClose={() => setSidebarOpen(false)}
							className="w-64 shrink-0"
						/>
					) : (
						<Button
							variant="outline"
							size="icon-lg"
							onClick={() => setSidebarOpen(true)}
							className="shrink-0"
							title="Show filters"
							aria-expanded={sidebarOpen}
						>
							<PanelLeft className="h-4 w-4" />
						</Button>
					)}
					{workflowList}
				</div>
			) : (
				<div className="flex flex-col gap-4 lg:min-h-0 lg:flex-1 lg:flex-row">
					{sidebarOpen ? (
						<WorkflowSidebar
							categories={categories}
							categoriesLoading={isLoading}
							selectedCategory={selectedCategory}
							onCategorySelect={setSelectedCategory}
							selectedFormId={selectedFormId}
							selectedAppId={selectedAppId}
							selectedAgentId={selectedAgentId}
							onFormSelect={setSelectedFormId}
							onAppSelect={setSelectedAppId}
							onAgentSelect={setSelectedAgentId}
							endpointFilter={endpointFilter}
							onEndpointFilterChange={setEndpointFilter}
							orphanedFilter={orphanedFilter}
							onOrphanedFilterChange={setOrphanedFilter}
							scope={
								isPlatformAdmin
									? (filterOrgId ?? undefined)
									: undefined
							}
							onClose={() => setSidebarOpen(false)}
							className="w-full lg:max-h-full lg:w-64 lg:shrink-0 lg:overflow-auto"
						/>
					) : (
						<Button
							variant="outline"
							onClick={() => setSidebarOpen(true)}
							className="h-11 w-full justify-start gap-2 lg:w-auto lg:shrink-0"
							title="Show filters"
							aria-expanded={sidebarOpen}
						>
							<PanelLeft className="h-4 w-4" />
							<span>Show filters</span>
						</Button>
					)}
					{workflowList}
				</div>
			)}

			{/* Orphaned Workflow Dialog */}
			{orphanedWorkflow && (
				<OrphanedWorkflowDialog
					open={orphanedDialogOpen}
					onClose={() => setOrphanedDialogOpen(false)}
					workflow={orphanedWorkflow}
					onSuccess={() => refetch()}
				/>
			)}

			{/* Workflow Edit Dialog */}
			<WorkflowEditDialog
				workflow={editingWorkflow}
				open={editDialogOpen}
				onOpenChange={setEditDialogOpen}
				onSuccess={() => refetch()}
				initialTab={editDialogInitialTab}
			/>
		</PageWorkspace>
	);
}
