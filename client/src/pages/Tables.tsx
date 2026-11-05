import { WorkspaceHeader } from "@/components/layout/WorkspaceHeader";
import { WorkspacePrimaryAction } from "@/components/layout/WorkspacePrimaryAction";
import { TableActionsMenu } from "./tables/TableActionsMenu";
import { useCallback, useRef, useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { Database, Download, Plus, RefreshCw, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { TableDeleteDialog } from "./tables/TableDeleteDialog";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Switch } from "@/components/ui/switch";
import { SearchBox } from "@/components/search/SearchBox";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { ListPageHeader } from "@/components/layout/ListPageHeader";
import { ListToolbar } from "@/components/layout/ListToolbar";
import { PageWorkspace } from "@/components/layout/PageWorkspace";
import { RecordActionsMenu } from "@/components/common/RecordActionsMenu";
import { TableRecordList } from "@/components/tables/TableRecordList";
import { useSearch } from "@/hooks/useSearch";
import { useAuth } from "@/contexts/AuthContext";
import { useAuthorization } from "@/services/authorization";
import { GLOBAL_TARGET, orgTarget } from "@/lib/authorization";
import { useOrganizations } from "@/hooks/useOrganizations";
import { useOrganizationName } from "@/hooks/useOrganizationName";
import { useTables, useDeleteTable } from "@/services/tables";
import { TableDialog } from "@/components/tables/TableDialog";
import { TablesClaimsTab } from "@/pages/TablesClaimsTab";
import { ImportDialog } from "@/components/ImportDialog";
import { exportEntities } from "@/services/exportImport";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import type { TablePublic } from "@/services/tables";

export function Tables() {
	const createButtonRef = useRef<HTMLButtonElement>(null);
	const reduceMotion = useReducedMotion();
	const inlineEditor = useMediaQuery("(min-width: 1280px)");
	const frameRef = useRef<HTMLDivElement>(null);
	const navigate = useNavigate();
	const [activeTab, setActiveTab] = useState("tables");
	const authorization = useAuthorization();
	const { user } = useAuth();
	const canTransferTables = authorization.canAt(
		"platform.readwrite",
		GLOBAL_TARGET,
	);
	const canManageAnyTables = authorization.canAnywhere("tables.readwrite");
	const canReadOrganizations =
		authorization.canAnywhere("organizations.read");
	const [selectedTable, setSelectedTable] = useState<
		TablePublic | undefined
	>();
	const [isDialogOpen, setIsDialogOpen] = useState(false);
	useEffect(() => {
		if (!isDialogOpen || inlineEditor) return;
		const id = requestAnimationFrame(() =>
			frameRef.current?.scrollIntoView?.({
				block: "start",
				behavior: "instant",
			}),
		);
		return () => cancelAnimationFrame(id);
	}, [isDialogOpen, inlineEditor]);
	const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false);
	const [tableToDelete, setTableToDelete] = useState<
		TablePublic | undefined
	>();
	const [searchTerm, setSearchTerm] = useState("");
	const [filterOrgId, setFilterOrgId] = useState<string | null | undefined>(
		undefined,
	);
	const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
	const [selectionMode, setSelectionMode] = useState(false);
	const [isImportOpen, setIsImportOpen] = useState(false);
	const [isExporting, setIsExporting] = useState(false);
	const [editorBusy, setEditorBusy] = useState(false);

	// Convert filterOrgId to scope for API: undefined = all, null = global only, string = org UUID
	const apiScope =
		filterOrgId === undefined
			? undefined
			: filterOrgId === null
				? "global"
				: filterOrgId;

	const { data, isLoading, isFetching, error, refetch } = useTables(apiScope);
	const deleteTable = useDeleteTable();

	// Fetch organizations for the org name lookup (platform admins only)
	const { data: organizations } = useOrganizations({
		enabled: canReadOrganizations,
	});

	const getOrgName = useOrganizationName(organizations);

	const tables = data?.tables ?? [];
	const knownOrganizations = [
		...(user?.organizationId
			? [{ id: user.organizationId, name: getOrgName(user.organizationId) }]
			: []),
		...tables.flatMap((table) =>
			table.organization_id
				? [
						{
							id: table.organization_id,
							name: getOrgName(table.organization_id),
						},
					]
				: [],
		),
	];

	// Apply search filter
	const filteredTables = useSearch(tables, searchTerm, [
		"name",
		"description",
	]);

	const canManageTable = (table: TablePublic) =>
		authorization.canAt(
			"tables.readwrite",
			orgTarget(table.organization_id),
		);
	const handleEdit = (table: TablePublic) => {
		if (editorBusy || !canManageTable(table)) return;
		setSelectedTable(table);
		setIsDialogOpen(true);
	};

	const handleAdd = () => {
		if (editorBusy || !canManageAnyTables) return;
		setSelectedTable(undefined);
		setIsDialogOpen(true);
	};

	const handleDelete = (table: TablePublic) => {
		if (editorBusy || !canManageTable(table)) return;
		setTableToDelete(table);
		setIsDeleteDialogOpen(true);
	};

	const handleConfirmDelete = async () => {
		if (!tableToDelete) return;
		// Defense in depth: solution-managed tables are deploy-owned and
		// read-only on the platform (server returns 409). The Delete button is
		// already disabled for them; this guards the bulk/stale-render path so we
		// never round-trip to a raw 409 (audit U1).
		if (tableToDelete.is_solution_managed) {
			toast.error(
				"Solution-managed entities can only be managed by deployment methods.",
			);
			setIsDeleteDialogOpen(false);
			setTableToDelete(undefined);
			return;
		}
		await deleteTable.mutateAsync({
			params: {
				path: { table_id: tableToDelete.id },
			},
		});
		setIsDeleteDialogOpen(false);
		setTableToDelete(undefined);
	};

	const handleViewDocuments = (table: TablePublic) => {
		if (editorBusy) return;
		navigate(`/tables/${table.id}`);
	};

	const handleDialogClose = () => {
		setEditorBusy(false);
		setIsDialogOpen(false);
		setSelectedTable(undefined);
	};

	const handleEditorBusyChange = useCallback((busy: boolean) => {
		setEditorBusy(busy);
	}, []);

	const toggleSelect = (id: string) => {
		setSelectedIds((prev) => {
			const next = new Set(prev);
			if (next.has(id)) next.delete(id);
			else next.add(id);
			return next;
		});
	};

	const allVisibleSelected =
		filteredTables.length > 0 &&
		filteredTables.every((table) => selectedIds.has(table.id));
	const toggleSelectAll = () => {
		setSelectedIds((previous) => {
			const next = new Set(previous);
			for (const table of filteredTables) {
				if (allVisibleSelected) next.delete(table.id);
				else next.add(table.id);
			}
			return next;
		});
	};

	const handleExport = async () => {
		const ids = selectedIds.size > 0 ? Array.from(selectedIds) : [];
		setIsExporting(true);
		try {
			await exportEntities("tables", ids);
			toast.success("Export downloaded");
		} catch {
			toast.error("Export failed");
		} finally {
			setIsExporting(false);
		}
	};

	const formatDate = (dateStr: string | null) => {
		if (!dateStr) return "-";
		return new Date(dateStr).toLocaleDateString(undefined, {
			year: "numeric",
			month: "short",
			day: "numeric",
		});
	};

	const renderTableActions = (table: TablePublic) =>
		canManageTable(table) ? (
			<TableActionsMenu
				table={table}
				onEdit={() => handleEdit(table)}
				onDelete={() => handleDelete(table)}
			/>
		) : null;

	return (
		<PageWorkspace className="mx-auto w-full max-w-[1600px] gap-4">
			<ListPageHeader
				className="shrink-0"
				title="Data Tables"
				description="Manage document tables for your applications"
			/>

			<Tabs
				value={activeTab}
				onValueChange={(value) => {
					if (!editorBusy) {
						setActiveTab(value);
						handleDialogClose();
					}
				}}
				ref={frameRef}
				className={cn(
					"scroll-mt-4 flex min-h-0 max-h-full flex-col gap-0 overflow-hidden rounded-[var(--bf-radius-feature)] border border-border/70 bg-card",
					isDialogOpen
						? "h-[calc(100dvh-6rem)] lg:h-auto lg:flex-1"
						: "shrink",
				)}
			>
				<WorkspaceHeader>
					<TabsList
						variant="line"
						className="w-full justify-start rounded-none border-0 group-data-horizontal/tabs:data-[variant=line]:border-b-0"
					>
						<TabsTrigger value="tables" className="flex-none px-4">
							Tables
						</TabsTrigger>
						<TabsTrigger value="claims" className="flex-none px-4">
							Custom Claims
						</TabsTrigger>
					</TabsList>
				</WorkspaceHeader>

				<TabsContent
					value="tables"
					className="mt-0 flex min-h-0 flex-1 flex-col"
				>
					<div
						className={cn(
							"flex min-h-0 max-h-full flex-col overflow-hidden",
							isDialogOpen ? "flex-1" : "shrink",
						)}
					>
						<ListToolbar className="shrink-0 gap-0 border-b border-border/70 bg-muted/20 p-0">
							{authorization.canAnywhere("tables.read") && (
								<div className="w-full shrink-0 border-b sm:w-56 sm:self-stretch sm:border-b-0 sm:border-r">
									<OrganizationSelect
										value={filterOrgId}
										onChange={setFilterOrgId}
										showAll={true}
										showGlobal={authorization.canAt(
											"tables.read",
											GLOBAL_TARGET,
										)}
										loadOrganizations={canReadOrganizations}
										knownOrganizations={knownOrganizations}
										filterOrganizations={(org) =>
											authorization.canAt(
												"tables.read",
												orgTarget(org.id),
											)
										}
										placeholder="All Organizations"
										aria-label="Tables scope"
										disabled={editorBusy}
										triggerClassName="h-full min-h-12 rounded-none border-0 bg-transparent px-4 py-2 shadow-none hover:bg-muted/50 focus-visible:ring-inset"
									/>
								</div>
							)}
							<div className="flex min-w-0 flex-1 flex-wrap items-center gap-0 sm:gap-0">
								<SearchBox
									value={searchTerm}
									onChange={setSearchTerm}
									aria-label="Search tables"
									placeholder="Search tables by name or description..."
									className="min-w-0 w-full max-sm:m-3 max-sm:w-[calc(100%-1.5rem)] sm:min-w-40 sm:flex-1 [&>input]:h-10 sm:[&>input]:h-12 sm:[&>input]:rounded-none sm:[&>input]:border-0 sm:[&>input]:bg-transparent sm:[&>input]:shadow-none sm:[&>input]:focus-visible:ring-inset"
								/>
								<div className="flex min-w-0 flex-wrap items-center gap-1 self-stretch pl-3 max-sm:w-full sm:ml-auto sm:gap-2 sm:border-l sm:border-border">
									{canTransferTables && (
										<label className="flex min-h-10 items-center gap-2 text-sm sm:min-h-12">
											<Switch
												aria-label="Select"
												checked={selectionMode}
												onCheckedChange={(value) => {
													setSelectionMode(value);
													if (!value)
														setSelectedIds(
															new Set(),
														);
												}}
												disabled={editorBusy}
											/>
											Select
										</label>
									)}
									<Button
										variant="ghost"
										size="icon"
										aria-label="Refresh"
										aria-busy={isFetching}
										title="Refresh"
										disabled={editorBusy || isFetching}
										onClick={() => refetch()}
									>
										<RefreshCw
											aria-hidden="true"
											className={cn(
												"size-4",
												isFetching &&
													"animate-spin motion-reduce:animate-none",
											)}
										/>
									</Button>
									{canTransferTables && (
										<RecordActionsMenu label="Tables actions">
											<DropdownMenuItem
												onSelect={handleExport}
												disabled={
													editorBusy || isExporting
												}
											>
												<Download className="size-4" />
												{selectedIds.size > 0
													? `Export (${selectedIds.size})`
													: "Export"}
											</DropdownMenuItem>
											<DropdownMenuItem
												onSelect={() =>
													setIsImportOpen(true)
												}
												disabled={editorBusy}
											>
												<Upload className="size-4" />
												Import
											</DropdownMenuItem>
										</RecordActionsMenu>
									)}
									<WorkspacePrimaryAction
										ref={createButtonRef}
										onClick={handleAdd}
										disabled={
											editorBusy || !canManageAnyTables
										}
									>
										<Plus className="size-4" />
										New Table
									</WorkspacePrimaryAction>
								</div>
							</div>
						</ListToolbar>

						{error && (
							<Alert variant="destructive">
								<AlertTitle>
									Tables could not be loaded
								</AlertTitle>
								<AlertDescription className="space-y-3">
									<p>
										{data
											? "Showing the last loaded tables. Refresh to get the latest changes."
											: "Try again to load your tables. Your filters are preserved."}
									</p>
									<Button
										variant="outline"
										className="min-h-11"
										disabled={isFetching}
										onClick={() => refetch()}
									>
										{isFetching
											? "Retrying…"
											: "Retry tables"}
									</Button>
								</AlertDescription>
							</Alert>
						)}
						{/* Content */}
						{isLoading ? (
							<div
								role="status"
								aria-label="Loading tables"
								className="p-6"
							>
								<span className="sr-only">Loading tables…</span>
								<RefreshCw
									aria-hidden="true"
									className="size-5 animate-spin text-primary motion-reduce:animate-none"
								/>
							</div>
						) : error && !data ? null : filteredTables.length > 0 ||
						  isDialogOpen ? (
							<div
								className="relative flex min-h-0 flex-1"
								role="region"
								aria-label="Data tables"
							>
								<div
									className="flex min-h-0 min-w-0 flex-1 flex-col"
									inert={
										isDialogOpen && !inlineEditor
											? true
											: undefined
									}
								>
									{filteredTables.length > 0 ? (
										<TableRecordList
											tables={filteredTables}
											showOrganizationName={
												canReadOrganizations
											}
											selectionMode={selectionMode}
											selectedIds={selectedIds}
											allVisibleSelected={
												allVisibleSelected
											}
											busy={editorBusy}
											onToggleAll={toggleSelectAll}
											onToggle={toggleSelect}
											onOpen={handleViewDocuments}
											scopeName={getOrgName}
											formatDate={formatDate}
											renderActions={renderTableActions}
										/>
									) : (
										<div className="flex min-h-[22rem] flex-1 flex-col items-center justify-center px-6 py-12 text-center">
											<Database className="h-12 w-12 text-muted-foreground" />
											<h3 className="mt-4 text-lg font-semibold">
												No tables found
											</h3>
											<p className="mt-2 text-sm text-muted-foreground">
												Get started by creating your
												first data table
											</p>
										</div>
									)}
								</div>
								<AnimatePresence initial={false}>
									{isDialogOpen && (
										<motion.aside
											key="table-editor"
											aria-label={
												selectedTable
													? "Edit table pane"
													: "Create table pane"
											}

											className={cn(
												"z-20 flex min-h-0 min-w-0 flex-col overflow-hidden bg-card",
												inlineEditor
													? "relative shrink-0 border-l"
													: "absolute inset-0",
											)}
											initial={
												reduceMotion
													? false
													: inlineEditor
														? {
																width: 0,
																opacity: 0,
															}
														: {
																x: "100%",
																opacity: 0,
															}
											}
											animate={
												inlineEditor
													? {
															width: "min(42vw, 560px)",
															opacity: 1,
														}
													: {
															width: "100%",
															x: 0,
															opacity: 1,
														}
											}
											exit={
												inlineEditor
													? { width: 0, opacity: 0 }
													: { x: "100%", opacity: 0 }
											}
											transition={{
												duration: reduceMotion
													? 0
													: 0.2,
												ease: [0.22, 1, 0.36, 1],
											}}
											onKeyDown={(event) => {
												if (
													event.key === "Escape" &&
													!event.defaultPrevented &&
													!editorBusy
												) {
													event.stopPropagation();
													handleDialogClose();
												}
											}}
										>
											<TableDialog
												table={selectedTable}
												open={isDialogOpen}
												onClose={handleDialogClose}
												embedded
												onBusyChange={
													handleEditorBusyChange
												}
											/>
										</motion.aside>
									)}
								</AnimatePresence>
							</div>
						) : (
							// Empty State
							<div className="flex min-h-[22rem] flex-1 flex-col items-center justify-center px-6 py-12 text-center">
								<Database className="h-12 w-12 text-muted-foreground" />
								<h3 className="mt-4 text-lg font-semibold">
									{searchTerm
										? "No tables match your search"
										: "No tables found"}
								</h3>
								<p className="mt-2 text-sm text-muted-foreground">
									{searchTerm
										? "Try adjusting your search term or clear the filter"
										: "Get started by creating your first data table"}
								</p>
								{!searchTerm && (
									<Button
										variant="outline"
										onClick={handleAdd}
										className="mt-4"
									>
										<Plus className="mr-2 h-4 w-4" />
										Create your first table
									</Button>
								)}
							</div>
						)}
					</div>
				</TabsContent>

				<TabsContent value="claims" className="flex-1 min-h-0">
					<TablesClaimsTab />
				</TabsContent>
			</Tabs>

			<ImportDialog
				open={isImportOpen}
				onOpenChange={setIsImportOpen}
				entityType="tables"
				onImportComplete={() => refetch()}
			/>

			{isDeleteDialogOpen && tableToDelete && (
				<TableDeleteDialog
					name={tableToDelete.name}
					onConfirm={handleConfirmDelete}
					onOpenChange={setIsDeleteDialogOpen}
					returnFocusRef={createButtonRef}
				/>
			)}
		</PageWorkspace>
	);
}
