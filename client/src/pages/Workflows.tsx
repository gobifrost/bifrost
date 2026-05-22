import { useState, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import {
	PlayCircle,
	Code,
	Code2,
	RefreshCw,
	Webhook,
	AlertTriangle,
	LayoutGrid,
	Table as TableIcon,
	Bot,
	Database,
	PanelLeft,
	Globe,
	Building2,
	Pencil,
	Unlink,
	Shield,
	Users,
	Loader2,
	History,
} from "lucide-react";
import { useIsDesktop } from "@/hooks/useMediaQuery";
import type { CategoryCount } from "@/components/workflows/WorkflowSidebar";
import { Button } from "@/components/ui/button";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import {
	Tooltip,
	TooltipContent,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import { useWorkflowsFiltered, useWorkflowsMetadata } from "@/hooks/useWorkflows";
import { useWorkflowKeys } from "@/hooks/useWorkflowKeys";
import { useOrgScope } from "@/contexts/OrgScopeContext";
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
import { toast } from "sonner";
import type { components } from "@/lib/v1";

// Extend WorkflowMetadata with fields that may not be in generated types yet
type BaseWorkflow = components["schemas"]["WorkflowMetadata"];
type Workflow = BaseWorkflow & {
	is_orphaned?: boolean;
	access_level?: "authenticated" | "role_based";
};
type Organization = components["schemas"]["OrganizationPublic"];

export function Workflows() {
	const navigate = useNavigate();
	const { scope, isGlobalScope } = useOrgScope();
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
	const [editingWorkflow, setEditingWorkflow] = useState<Workflow | null>(null);
	const [editDialogInitialTab, setEditDialogInitialTab] = useState<string | undefined>(undefined);

	// Orphaned workflow dialog state
	const [orphanedDialogOpen, setOrphanedDialogOpen] = useState(false);
	const [orphanedWorkflow, setOrphanedWorkflow] = useState<Workflow | null>(null);

	// Entity filter state
	const [selectedCategory, setSelectedCategory] = useState<string | null>(null);
	const [selectedFormId, setSelectedFormId] = useState<string | null>(null);
	const [selectedAppId, setSelectedAppId] = useState<string | null>(null);
	const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
	const [endpointFilter, setEndpointFilter] = useState(false);
	const [orphanedFilter, setOrphanedFilter] = useState(false);

	// Open in editor state
	const [openingWorkflowId, setOpeningWorkflowId] = useState<string | null>(null);
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
	const { data, isLoading, refetch } = useWorkflowsFiltered({
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
				categoryMap.set(w.category, (categoryMap.get(w.category) || 0) + 1);
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
		navigate(`/workflows/${workflowName}/execute`);
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
		const workflowMeta = metadata?.workflows?.find(
			(w) => w.name === workflow.name,
		);
		const relativeFilePath = workflowMeta?.relative_file_path;

		if (!relativeFilePath) {
			toast.error("Cannot open in editor: source file not found");
			return;
		}

		setOpeningWorkflowId(workflow.id ?? workflow.name ?? null);
		try {
			const fileResponse = await fileService.readFile(relativeFilePath);
			const fileName = relativeFilePath.split("/").pop() || relativeFilePath;
			const extension = fileName.includes(".") ? fileName.split(".").pop()! : null;

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

	return (
		<div className="h-full flex flex-col space-y-6 max-w-7xl mx-auto">
			<div className="flex items-center justify-between">
				<div>
					<div className="flex items-center gap-3">
						<h1 className="text-4xl font-extrabold tracking-tight">
							Workflows
						</h1>
						{isPlatformAdmin && (
							<Badge
								variant={isGlobalScope ? "default" : "outline"}
								className="text-sm"
							>
								{isGlobalScope ? (
									<>
										<Globe className="mr-1 h-3 w-3" />
										Global
									</>
								) : (
									<>
										<Building2 className="mr-1 h-3 w-3" />
										{scope.orgName}
									</>
								)}
							</Badge>
						)}
					</div>
					<p className="mt-2 text-muted-foreground">
						Execute workflows directly with custom parameters
					</p>
				</div>
				<div className="flex gap-2">
					<ToggleGroup
						type="single"
						value={viewMode}
						onValueChange={(value: string) =>
							value && setViewMode(value as "grid" | "table")
						}
					>
						<ToggleGroupItem
							value="grid"
							aria-label="Grid view"
							size="sm"
						>
							<LayoutGrid className="h-4 w-4" />
						</ToggleGroupItem>
						<ToggleGroupItem
							value="table"
							aria-label="Table view"
							size="sm"
						>
							<TableIcon className="h-4 w-4" />
						</ToggleGroupItem>
					</ToggleGroup>
					<Button
						variant="outline"
						size="icon"
						onClick={() => refetch()}
						aria-label="Refresh"
					>
						<RefreshCw className="h-4 w-4" />
					</Button>
				</div>
			</div>

			{/* Search Box, Org Filter, and Type Filter */}
			<div className="flex flex-col sm:flex-row gap-4 items-start sm:items-center">
				<SearchBox
					value={searchTerm}
					onChange={setSearchTerm}
					placeholder="Search by name, description, or category..."
					className="flex-1"
				/>
				{isPlatformAdmin && (
					<div className="w-64">
						<OrganizationSelect
							value={filterOrgId}
							onChange={setFilterOrgId}
							showAll={true}
							showGlobal={true}
							placeholder="All organizations"
						/>
					</div>
				)}
				<ToggleGroup
					type="single"
					value={typeFilter}
					onValueChange={(value: string) =>
						value && setTypeFilter(value)
					}
				>
					<ToggleGroupItem value="all" size="sm">
						All
					</ToggleGroupItem>
					<ToggleGroupItem value="workflow" size="sm">
						Workflows
					</ToggleGroupItem>
					<ToggleGroupItem value="tool" size="sm">
						Tools
					</ToggleGroupItem>
					<ToggleGroupItem value="data_provider" size="sm">
						Data Providers
					</ToggleGroupItem>
				</ToggleGroup>
			</div>

			{/* Main Content with Sidebar */}
			<div className="flex-1 flex gap-6 min-h-0">
				{/* Sidebar */}
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
						scope={isPlatformAdmin ? filterOrgId ?? undefined : undefined}
						onClose={() => setSidebarOpen(false)}
						className="w-64 shrink-0"
					/>
				) : (
					<Button
						variant="outline"
						size="icon"
						onClick={() => setSidebarOpen(true)}
						className="shrink-0 h-9 w-9"
						title="Show filters"
					>
						<PanelLeft className="h-4 w-4" />
					</Button>
				)}

				{/* Content Area */}
				<div className="flex-1 min-w-0 min-h-0 overflow-auto">
					{isLoading ? (
						viewMode === "grid" ? (
							<div className={"grid gap-4 grid-cols-[repeat(auto-fill,minmax(300px,1fr))]"}>
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
						)
					) : filteredWorkflows.length > 0 ? (
						viewMode === "grid" ? (
							<div className={"grid gap-4 grid-cols-[repeat(auto-fill,minmax(300px,1fr))]"}>
								{filteredWorkflows.map((workflow) => (
									<Card
										key={workflow.id ?? workflow.name}
										className="hover:border-primary transition-colors flex flex-col"
									>
										<CardHeader className="pb-2">
											{/* Top row: Type badge left, Edit button right */}
											<div className="flex items-center justify-between gap-2 mb-3">
												<div className="flex items-center gap-2">
													{workflow.type === "tool" ? (
														<Badge
															variant="secondary"
															className="bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300"
															title={
																workflow.tool_description ||
																"Available as AI tool"
															}
														>
															<Bot className="mr-1 h-3 w-3" />
															Tool
														</Badge>
													) : workflow.type === "data_provider" ? (
														<Badge
															variant="secondary"
															className="bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300"
															title="Provides data for forms and apps"
														>
															<Database className="mr-1 h-3 w-3" />
															Data Provider
														</Badge>
													) : (
														<Badge
															variant="secondary"
															title="Executable workflow"
														>
															<PlayCircle className="mr-1 h-3 w-3" />
															Workflow
														</Badge>
													)}
												</div>
												<div className="flex items-center gap-1">
													<Tooltip>
														<TooltipTrigger asChild>
															<Button
																variant="outline"
																size="icon-sm"
																onClick={() => navigate(`/history?workflow=${workflow.id ?? ""}`)}
																title="View history"
															>
																<History className="h-3.5 w-3.5" />
															</Button>
														</TooltipTrigger>
														<TooltipContent>View history</TooltipContent>
													</Tooltip>
													<Tooltip>
														<TooltipTrigger asChild>
															<Button
																variant="outline"
																size="icon-sm"
																onClick={() => handleOpenInEditor(workflow)}
																disabled={openingWorkflowId === (workflow.id ?? workflow.name)}
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
													{isPlatformAdmin && (
														<Button
															variant="outline"
															size="icon-sm"
															onClick={() =>
																handleEditWorkflow(workflow)
															}
															title="Edit organization scope"
														>
															<Pencil className="h-3.5 w-3.5" />
														</Button>
													)}
												</div>
											</div>

											{/* Title and description */}
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
											{/* Metadata line: Category + Scope + Access Level */}
											<div className="flex items-center gap-2 text-xs text-muted-foreground">
												{workflow.category && (
													<span>{workflow.category}</span>
												)}
												{workflow.category && (isPlatformAdmin || workflow.endpoint_enabled || workflow.is_orphaned || workflow.disable_global_key) && (
													<span>·</span>
												)}
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
																	{workflow.access_level === "authenticated" ? (
																		<>
																			<Users className="h-3 w-3" />
																			Auth
																		</>
																	) : (
																		<>
																			<Shield className="h-3 w-3" />
																			Roles
																		</>
																	)}
																</span>
															</TooltipTrigger>
															<TooltipContent>
																{workflow.access_level === "authenticated"
																	? "Any authenticated user can execute"
																	: "Role-based access required"}
															</TooltipContent>
														</Tooltip>
													</>
												)}
											</div>

											{/* Status badges row (only if any apply) */}
											{(workflow.endpoint_enabled || workflow.is_orphaned || workflow.disable_global_key) && (
												<div className="flex flex-wrap items-center gap-1.5">
													{workflow.is_orphaned && (
														<Badge
															variant="outline"
															className="bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-300 cursor-pointer hover:bg-yellow-200 dark:hover:bg-yellow-800"
															title="This workflow's file no longer exists. Click to resolve."
															onClick={(e) => {
																e.stopPropagation();
																handleOpenOrphanedDialog(workflow);
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
																		  workflowsWithKeys.has(
																				workflow.name ?? "",
																		  )
																		? "default"
																		: "outline"
															}
															className={`cursor-pointer transition-colors ${
																workflow.public_endpoint
																	? "bg-orange-600 hover:bg-orange-700 border-orange-600"
																	: hasGlobalKey ||
																		  workflowsWithKeys.has(
																				workflow.name ?? "",
																		  )
																		? "bg-green-600 hover:bg-green-700"
																		: "text-muted-foreground hover:bg-accent"
															}`}
															onClick={(e) => {
																e.stopPropagation();
																handleEditWorkflow(workflow, "endpoint");
															}}
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
															className="bg-orange-600 text-white hover:bg-orange-700 border-orange-600"
															title="This workflow only accepts workflow-specific API keys (global keys are disabled)"
														>
															Global Opt-Out
														</Badge>
													)}
												</div>
											)}

											{/* Primary action button */}
											<Button
												className="w-full"
												onClick={() =>
													handleExecute(workflow.name ?? "")
												}
											>
												<PlayCircle className="mr-2 h-4 w-4" />
												{workflow.type === "tool"
													? "Test Tool"
													: workflow.type === "data_provider"
														? "Preview Data"
														: "Execute Workflow"}
											</Button>
										</CardContent>
									</Card>
								))}
							</div>
						) : (
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
										{filteredWorkflows.map((workflow) => (
											<DataTableRow key={workflow.id ?? workflow.name}>
												{isPlatformAdmin && (
													<DataTableCell className="w-0 whitespace-nowrap">
														{workflow.organization_id ? (
															<Badge
																variant="outline"
																className="text-xs"
															>
																<Building2 className="mr-1 h-3 w-3" />
																{getOrgName(
																	workflow.organization_id,
																)}
															</Badge>
														) : (
															<Badge
																variant="default"
																className="text-xs"
															>
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
																	handleOpenOrphanedDialog(workflow);
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
														<span className="italic">
															No description
														</span>
													)}
												</DataTableCell>
												<DataTableCell className="w-0 whitespace-nowrap text-right">
													<div className="flex items-center justify-end gap-1">
														<Button
															variant="outline"
															size="icon-sm"
															onClick={() => navigate(`/history?workflow=${workflow.id ?? ""}`)}
															title="View history"
														>
															<History className="h-4 w-4" />
														</Button>
														<Button
															variant="outline"
															size="icon-sm"
															onClick={() => handleOpenInEditor(workflow)}
															disabled={openingWorkflowId === (workflow.id ?? workflow.name)}
															title="Open in editor"
														>
															{openingWorkflowId === (workflow.id ?? workflow.name) ? (
																<Loader2 className="h-4 w-4 animate-spin" />
															) : (
																<Code2 className="h-4 w-4" />
															)}
														</Button>
														{isPlatformAdmin && (
															<Button
																variant="ghost"
																size="icon-sm"
																onClick={() =>
																	handleEditWorkflow(workflow)
																}
																title="Edit organization scope"
															>
																<Pencil className="h-4 w-4" />
															</Button>
														)}
														<Button
															variant="outline"
															size="icon-sm"
															onClick={() =>
																handleExecute(
																	workflow.name ?? "",
																)
															}
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
						)
					) : (
						<Card>
							<CardContent className="flex flex-col items-center justify-center py-12 text-center">
								<Code className="h-12 w-12 text-muted-foreground" />
								<h3 className="mt-4 text-lg font-semibold">
									{searchTerm
										? "No workflows match your search"
										: "No workflows available"}
								</h3>
								<p className="mt-2 text-sm text-muted-foreground">
									{searchTerm
										? "Try adjusting your search term or clear the filter"
										: "No workflows have been registered in the workflow engine"}
								</p>
								{!searchTerm && (
									<Button
										variant="outline"
										onClick={() => openEditor()}
										className="mt-4"
									>
										<Code className="mr-2 h-4 w-4" />
										Open editor
									</Button>
								)}
							</CardContent>
						</Card>
					)}
				</div>
			</div>

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
		</div>
	);
}
