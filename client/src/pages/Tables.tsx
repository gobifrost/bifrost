import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
	Database,
	Pencil,
	Plus,
	Trash2,
	RefreshCw,
	FileJson2,
	Globe,
	Building2,
	Download,
	Upload,
} from "lucide-react";
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
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { SearchBox } from "@/components/search/SearchBox";
import { SolutionManagedBadge } from "@/components/solutions/SolutionManagedBadge";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { useSearch } from "@/hooks/useSearch";
import { useAuth } from "@/contexts/AuthContext";
import { useOrganizations } from "@/hooks/useOrganizations";
import { useTables, useDeleteTable } from "@/services/tables";
import { TableDialog } from "@/components/tables/TableDialog";
import { TablesClaimsTab } from "@/pages/TablesClaimsTab";
import { ImportDialog } from "@/components/ImportDialog";
import { exportEntities } from "@/services/exportImport";
import { toast } from "sonner";
import type { TablePublic } from "@/services/tables";

export function Tables() {
	const navigate = useNavigate();
	const { isPlatformAdmin } = useAuth();
	const [selectedTable, setSelectedTable] = useState<
		TablePublic | undefined
	>();
	const [isDialogOpen, setIsDialogOpen] = useState(false);
	const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false);
	const [tableToDelete, setTableToDelete] = useState<
		TablePublic | undefined
	>();
	const [searchTerm, setSearchTerm] = useState("");
	const [filterOrgId, setFilterOrgId] = useState<string | null | undefined>(
		undefined,
	);
	const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
	const [isImportOpen, setIsImportOpen] = useState(false);
	const [isExporting, setIsExporting] = useState(false);
	const [showOrphaned, setShowOrphaned] = useState(false);

	// Convert filterOrgId to scope for API: undefined = all, null = global only, string = org UUID
	const apiScope =
		filterOrgId === undefined
			? undefined
			: filterOrgId === null
				? "global"
				: filterOrgId;

	const { data, isLoading, refetch } = useTables(apiScope, showOrphaned);
	const deleteTable = useDeleteTable();

	// Fetch organizations for the org name lookup (platform admins only)
	const { data: organizations } = useOrganizations({
		enabled: isPlatformAdmin,
	});

	// Helper to get organization name from ID
	const getOrgName = (orgId: string | null | undefined): string => {
		if (!orgId) return "Global";
		const org = organizations?.find((o) => o.id === orgId);
		return org?.name || orgId;
	};

	const tables = data?.tables ?? [];

	// Apply search filter
	const filteredTables = useSearch(tables, searchTerm, [
		"name",
		"description",
	]);

	const handleEdit = (table: TablePublic) => {
		setSelectedTable(table);
		setIsDialogOpen(true);
	};

	const handleAdd = () => {
		setSelectedTable(undefined);
		setIsDialogOpen(true);
	};

	const handleDelete = (table: TablePublic) => {
		setTableToDelete(table);
		setIsDeleteDialogOpen(true);
	};

	const handleConfirmDelete = async () => {
		if (!tableToDelete) return;
		await deleteTable.mutateAsync({
			params: {
				path: { table_id: tableToDelete.id },
			},
		});
		setIsDeleteDialogOpen(false);
		setTableToDelete(undefined);
	};

	const handleViewDocuments = (table: TablePublic) => {
		navigate(`/tables/${table.id}`);
	};

	const handleDialogClose = () => {
		setIsDialogOpen(false);
		setSelectedTable(undefined);
	};

	const toggleSelect = (id: string) => {
		setSelectedIds((prev) => {
			const next = new Set(prev);
			if (next.has(id)) next.delete(id);
			else next.add(id);
			return next;
		});
	};

	const toggleSelectAll = () => {
		if (selectedIds.size === filteredTables.length) {
			setSelectedIds(new Set());
		} else {
			setSelectedIds(new Set(filteredTables.map((t) => t.id)));
		}
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

	return (
		<div className="h-full flex flex-col space-y-6 max-w-7xl mx-auto">
			{/* Header */}
			<div className="flex items-center justify-between">
				<div>
					<h1 className="text-4xl font-extrabold tracking-tight">
						Data Tables
					</h1>
					<p className="mt-2 text-muted-foreground">
						Manage document tables for your applications
					</p>
				</div>
				<div className="flex gap-2">
					<Button
						variant="outline"
						size="icon"
						onClick={() => refetch()}
						title="Refresh"
						aria-label="Refresh"
					>
						<RefreshCw className="h-4 w-4" />
					</Button>
					<Button
						variant="outline"
						size="icon"
						onClick={handleAdd}
						title="Create Table"
						aria-label="Create table"
					>
						<Plus className="h-4 w-4" />
					</Button>
				</div>
			</div>

			<Tabs defaultValue="tables" className="flex flex-1 min-h-0 flex-col">
				<TabsList className="w-fit">
					<TabsTrigger value="tables">Tables</TabsTrigger>
					<TabsTrigger value="claims">Custom Claims</TabsTrigger>
				</TabsList>

				<TabsContent value="tables" className="flex flex-1 min-h-0 flex-col space-y-6">
					{/* Search and Filters */}
					<div className="flex items-center gap-4">
						<SearchBox
							value={searchTerm}
							onChange={setSearchTerm}
							placeholder="Search tables by name or description..."
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
						<label className="flex items-center gap-2 whitespace-nowrap text-sm text-muted-foreground">
							<Checkbox
								checked={showOrphaned}
								onCheckedChange={(checked) =>
									setShowOrphaned(checked === true)
								}
								aria-label="Show orphaned"
							/>
							Show orphaned
						</label>
						{isPlatformAdmin && (
							<div className="flex items-center gap-2 ml-auto">
								{selectedIds.size > 0 && (
									<span className="text-sm text-muted-foreground">
										{selectedIds.size} selected
									</span>
								)}
								<Button
									variant="outline"
									size="sm"
									onClick={handleExport}
									disabled={isExporting}
								>
									<Download className="h-4 w-4 mr-1" />
									{selectedIds.size > 0
										? `Export (${selectedIds.size})`
										: "Export All"}
								</Button>
								<Button
									variant="outline"
									size="sm"
									onClick={() => setIsImportOpen(true)}
								>
									<Upload className="h-4 w-4 mr-1" />
									Import
								</Button>
							</div>
						)}
					</div>

					{/* Content */}
					{isLoading ? (
						<div className="space-y-2">
							{[...Array(5)].map((_, i) => (
								<Skeleton key={i} className="h-12 w-full" />
							))}
						</div>
					) : filteredTables && filteredTables.length > 0 ? (
						<div className="flex-1 min-h-0">
							<DataTable className="max-h-full">
								<DataTableHeader>
									<DataTableRow>
										{isPlatformAdmin && (
											<DataTableHead className="w-10">
												<Checkbox
													checked={
														filteredTables.length >
															0 &&
														selectedIds.size ===
															filteredTables.length
													}
													onCheckedChange={
														toggleSelectAll
													}
												/>
											</DataTableHead>
										)}
										<DataTableHead className="w-0 whitespace-nowrap">
											Scope
										</DataTableHead>
										<DataTableHead>Name</DataTableHead>
										<DataTableHead>
											Description
										</DataTableHead>
										<DataTableHead className="w-0 whitespace-nowrap">
											Created
										</DataTableHead>
										<DataTableHead className="w-0 whitespace-nowrap text-right" />
									</DataTableRow>
								</DataTableHeader>
								<DataTableBody>
									{filteredTables.map((table) => (
										<DataTableRow
											key={table.id}
											className="cursor-pointer hover:bg-muted/50"
											onClick={() =>
												handleViewDocuments(table)
											}
										>
											{isPlatformAdmin && (
												<DataTableCell>
													<Checkbox
														checked={selectedIds.has(
															table.id,
														)}
														onCheckedChange={() =>
															toggleSelect(
																table.id,
															)
														}
														onClick={(e) =>
															e.stopPropagation()
														}
													/>
												</DataTableCell>
											)}
											<DataTableCell className="w-0 whitespace-nowrap">
												{table.organization_id ? (
													<Badge
														variant="outline"
														className="gap-1"
													>
														<Building2 className="h-3 w-3" />
														{isPlatformAdmin
															? getOrgName(
																	table.organization_id,
																)
															: "Organization"}
													</Badge>
												) : (
													<Badge
														variant="secondary"
														className="gap-1"
													>
														<Globe className="h-3 w-3" />
														Global
													</Badge>
												)}
											</DataTableCell>
											<DataTableCell className="font-medium font-mono">
												<span className="flex items-center gap-2">
													{table.name}
													{table.is_solution_managed && (
														<SolutionManagedBadge
															solutionId={table.solution_id}
														/>
													)}
													{table.orphaned_at && (
														<Badge
															variant="outline"
															className="font-sans text-xs font-normal text-muted-foreground"
														>
															Orphaned
															{table.origin_solution_slug
																? ` · from ${table.origin_solution_slug}`
																: ""}
														</Badge>
													)}
												</span>
											</DataTableCell>
											<DataTableCell className="max-w-xs truncate text-muted-foreground">
												{table.description || "-"}
											</DataTableCell>
											<DataTableCell className="w-0 whitespace-nowrap text-sm text-muted-foreground">
												{formatDate(table.created_at)}
											</DataTableCell>
											<DataTableCell className="w-0 whitespace-nowrap text-right">
												<div
													className="flex justify-end gap-2"
													onClick={(e) =>
														e.stopPropagation()
													}
												>
													<Button
														variant="ghost"
														size="icon"
														onClick={() =>
															handleViewDocuments(
																table,
															)
														}
														title="View documents"
														aria-label="View documents"
													>
														<FileJson2 className="h-4 w-4" />
													</Button>
													<Button
														variant="ghost"
														size="icon"
														onClick={() =>
															handleEdit(table)
														}
														title="Edit table"
														aria-label="Edit table"
													>
														<Pencil className="h-4 w-4" />
													</Button>
													<Button
														variant="ghost"
														size="icon"
														onClick={() =>
															handleDelete(table)
														}
														title="Delete table"
														aria-label="Delete table"
													>
														<Trash2 className="h-4 w-4" />
													</Button>
												</div>
											</DataTableCell>
										</DataTableRow>
									))}
								</DataTableBody>
							</DataTable>
						</div>
					) : (
						// Empty State
						<Card>
							<CardContent className="flex flex-col items-center justify-center py-12 text-center">
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
							</CardContent>
						</Card>
					)}
				</TabsContent>

				<TabsContent value="claims" className="flex-1 min-h-0">
					<TablesClaimsTab />
				</TabsContent>
			</Tabs>

			<TableDialog
				table={selectedTable}
				open={isDialogOpen}
				onClose={handleDialogClose}
			/>

			<ImportDialog
				open={isImportOpen}
				onOpenChange={setIsImportOpen}
				entityType="tables"
				onImportComplete={() => refetch()}
			/>

			{/* Delete Confirmation Dialog */}
			<AlertDialog
				open={isDeleteDialogOpen}
				onOpenChange={setIsDeleteDialogOpen}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Delete Table</AlertDialogTitle>
						<AlertDialogDescription>
							Are you sure you want to delete the table "
							{tableToDelete?.name}"? This will permanently delete
							all documents in this table. This action cannot be
							undone.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction
							onClick={handleConfirmDelete}
							className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							{deleteTable.isPending
								? "Deleting..."
								: "Delete Table"}
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</div>
	);
}
