import { useState, useMemo } from "react";
import { useParams, Link, useLocation } from "react-router-dom";
import {
	ArrowLeft,
	ChevronLeft,
	ChevronRight,
	FileJson2,
	Pencil,
	Plus,
	RefreshCw,
	Trash2,
	Copy,
	Check,
	PanelLeft,
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
import { Skeleton } from "@/components/ui/skeleton";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import {
	Tooltip,
	TooltipContent,
	TooltipProvider,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import { useTable, useDocuments, useDeleteDocument } from "@/services/tables";
import { DocumentDialog } from "@/components/tables/DocumentDialog";
import { TableFilterSidebar } from "@/components/tables/TableFilterSidebar";
import { SearchBox } from "@/components/search/SearchBox";
import { useSearch } from "@/hooks/useSearch";
import type { DocumentPublic } from "@/services/tables";
import { parseSolutionFrom } from "@/lib/solution-back-nav";

const PAGE_SIZES = [10, 25, 50, 100];

export function TableDetail() {
	const { tableId } = useParams<{ tableId: string }>();
	const { search } = useLocation();
	const fromSolution = parseSolutionFrom(search);
	const backTo = fromSolution ? `/solutions/${fromSolution}` : "/tables";
	const backLabel = fromSolution ? "Back to Solution" : "Back to Tables";
	const [selectedDocument, setSelectedDocument] = useState<
		DocumentPublic | undefined
	>();
	const [isDialogOpen, setIsDialogOpen] = useState(false);
	const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false);
	const [documentToDelete, setDocumentToDelete] = useState<
		DocumentPublic | undefined
	>();
	const [copiedId, setCopiedId] = useState<string | null>(null);

	// Pagination state
	const [pageSize, setPageSize] = useState(25);
	const [currentPage, setCurrentPage] = useState(0);

	// Filter state
	const [whereClause, setWhereClause] = useState<Record<string, unknown>>({});
	const [searchTerm, setSearchTerm] = useState("");
	const [sidebarOpen, setSidebarOpen] = useState(true);

	const query = useMemo(
		() => ({
			where:
				Object.keys(whereClause).length > 0 ? whereClause : undefined,
			limit: pageSize,
			offset: currentPage * pageSize,
			order_dir: "desc" as const,
		}),
		[whereClause, pageSize, currentPage],
	);

	const { data: table, isLoading: tableLoading } = useTable(tableId || "");
	const {
		data: documentsData,
		isLoading: documentsLoading,
		refetch,
	} = useDocuments(tableId || "", query);
	const deleteDocument = useDeleteDocument();

	const documents = useMemo(
		() => documentsData?.documents ?? [],
		[documentsData?.documents],
	);

	// Apply client-side search filtering
	const filteredDocuments = useSearch(documents, searchTerm, [
		"id",
		(doc) => JSON.stringify(doc.data),
	]);

	const totalDocuments = documentsData?.total ?? 0;
	const totalPages = Math.ceil(totalDocuments / pageSize);
	const hasActiveFilters = Object.keys(whereClause).length > 0;

	const handleAdd = () => {
		setSelectedDocument(undefined);
		setIsDialogOpen(true);
	};

	const handleEdit = (doc: DocumentPublic) => {
		setSelectedDocument(doc);
		setIsDialogOpen(true);
	};

	const handleDelete = (doc: DocumentPublic) => {
		setDocumentToDelete(doc);
		setIsDeleteDialogOpen(true);
	};

	const handleConfirmDelete = async () => {
		if (!documentToDelete || !tableId) return;
		await deleteDocument.mutateAsync({
			params: {
				path: { table_id: tableId, doc_id: documentToDelete.id },
			},
		});
		setIsDeleteDialogOpen(false);
		setDocumentToDelete(undefined);
	};

	const handleDialogClose = () => {
		setIsDialogOpen(false);
		setSelectedDocument(undefined);
	};

	const handleApplyFilters = (where: Record<string, unknown>) => {
		setWhereClause(where);
		setCurrentPage(0);
	};

	const handleClearFilters = () => {
		setWhereClause({});
		setCurrentPage(0);
	};

	const handlePageSizeChange = (value: string) => {
		setPageSize(parseInt(value, 10));
		setCurrentPage(0);
	};

	const copyToClipboard = async (id: string) => {
		await navigator.clipboard.writeText(id);
		setCopiedId(id);
		setTimeout(() => setCopiedId(null), 2000);
	};

	const formatDate = (dateStr: string | null) => {
		if (!dateStr) return "-";
		return new Date(dateStr).toLocaleString(undefined, {
			year: "numeric",
			month: "short",
			day: "numeric",
			hour: "2-digit",
			minute: "2-digit",
		});
	};

	const truncateJson = (obj: Record<string, unknown>, maxLength = 60) => {
		const str = JSON.stringify(obj);
		if (str.length <= maxLength) return str;
		return str.substring(0, maxLength) + "...";
	};

	// Extract common data fields to show as columns (use all documents for consistent columns)
	const dataColumns = useMemo(() => {
		if (documents.length === 0) return [];
		const allKeys = new Set<string>();
		documents.forEach((doc) => {
			Object.keys(doc.data).forEach((key) => allKeys.add(key));
		});
		// Return first 3 unique keys
		return Array.from(allKeys).slice(0, 3);
	}, [documents]);

	// Display documents (filtered by search)
	const displayDocuments = searchTerm ? filteredDocuments : documents;

	if (tableLoading) {
		return (
			<div className="space-y-6">
				<Skeleton className="h-8 w-48" />
				<Skeleton className="h-64 w-full" />
			</div>
		);
	}

	if (!table) {
		return (
			<div className="flex flex-col items-center justify-center py-12">
				<FileJson2 className="h-12 w-12 text-muted-foreground" />
				<h3 className="mt-4 text-lg font-semibold">Table not found</h3>
				<p className="mt-2 text-sm text-muted-foreground">
					The table "{tableId}" does not exist or you don't have
					access.
				</p>
				<Button variant="outline" asChild className="mt-4">
					<Link to={backTo}>
						<ArrowLeft className="h-4 w-4 mr-2" />
						{backLabel}
					</Link>
				</Button>
			</div>
		);
	}

	return (
		<div className="h-full flex flex-col space-y-4">
			{/* Header */}
			<div className="flex items-center justify-between">
				<div>
					<div className="flex items-center gap-3">
						<Button variant="ghost" size="icon" asChild aria-label={backLabel}>
							<Link to={backTo}>
								<ArrowLeft className="h-4 w-4" />
							</Link>
						</Button>
						<h1 className="text-4xl font-extrabold tracking-tight font-mono">
							{table.name}
						</h1>
					</div>
					{table.description && (
						<p className="mt-2 text-muted-foreground ml-12">
							{table.description}
						</p>
					)}
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
						title="Add Document"
						aria-label="Add document"
					>
						<Plus className="h-4 w-4" />
					</Button>
				</div>
			</div>

			{/* Search Box */}
			<div className="flex items-center gap-4">
				<SearchBox
					value={searchTerm}
					onChange={setSearchTerm}
					placeholder="Search this page..."
					className="w-64"
				/>
			</div>

			{/* Main Content with Sidebar */}
			<div className="flex-1 flex gap-6 min-h-0">
				{/* Sidebar */}
				{sidebarOpen ? (
					<TableFilterSidebar
						onApplyFilters={handleApplyFilters}
						onClearFilters={handleClearFilters}
						hasActiveFilters={hasActiveFilters}
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
				<div className="flex-1 min-w-0 flex flex-col">
					{documentsLoading ? (
						<div className="space-y-2">
							{[...Array(5)].map((_, i) => (
								<Skeleton key={i} className="h-12 w-full" />
							))}
						</div>
					) : displayDocuments.length > 0 ? (
						<div className="flex-1 min-h-0 flex flex-col">
							<div className="flex-1 min-h-0">
								<DataTable>
									<DataTableHeader>
										<DataTableRow>
											<DataTableHead className="w-[200px]">
												ID
											</DataTableHead>
											{dataColumns.map((col) => (
												<DataTableHead key={col}>
													{col}
												</DataTableHead>
											))}
											<DataTableHead>
												Data Preview
											</DataTableHead>
											<DataTableHead>
												Created
											</DataTableHead>
											<DataTableHead className="text-right">
												Actions
											</DataTableHead>
										</DataTableRow>
									</DataTableHeader>
									<DataTableBody>
										{displayDocuments.map((doc) => (
											<DataTableRow key={doc.id}>
												<DataTableCell className="font-mono text-xs">
													<TooltipProvider>
														<Tooltip>
															<TooltipTrigger
																asChild
															>
																<button
																	onClick={() =>
																		copyToClipboard(
																			doc.id,
																		)
																	}
																	className="flex items-center gap-1 hover:text-foreground text-muted-foreground"
																>
																	{doc.id.substring(
																		0,
																		8,
																	)}
																	...
																	{copiedId ===
																	doc.id ? (
																		<Check className="h-3 w-3 text-green-500" />
																	) : (
																		<Copy className="h-3 w-3" />
																	)}
																</button>
															</TooltipTrigger>
															<TooltipContent>
																{copiedId ===
																doc.id
																	? "Copied!"
																	: "Click to copy full ID"}
															</TooltipContent>
														</Tooltip>
													</TooltipProvider>
												</DataTableCell>
												{dataColumns.map((col) => (
													<DataTableCell
														key={col}
														className="max-w-[150px] truncate text-sm"
													>
														{doc.data[col] !==
														undefined
															? typeof doc.data[
																	col
																] === "object"
																? JSON.stringify(
																		doc
																			.data[
																			col
																		],
																	)
																: String(
																		doc
																			.data[
																			col
																		],
																	)
															: "-"}
													</DataTableCell>
												))}
												<DataTableCell className="max-w-[200px] font-mono text-xs text-muted-foreground">
													<TooltipProvider>
														<Tooltip>
															<TooltipTrigger
																asChild
															>
																<span className="block truncate cursor-help">
																	{truncateJson(
																		doc.data,
																	)}
																</span>
															</TooltipTrigger>
															<TooltipContent
																side="bottom"
																className="max-w-md"
															>
																<pre className="text-xs whitespace-pre-wrap break-all">
																	{JSON.stringify(
																		doc.data,
																		null,
																		2,
																	)}
																</pre>
															</TooltipContent>
														</Tooltip>
													</TooltipProvider>
												</DataTableCell>
												<DataTableCell className="text-sm text-muted-foreground whitespace-nowrap">
													{formatDate(doc.created_at)}
												</DataTableCell>
												<DataTableCell className="text-right">
													<div className="flex justify-end gap-2">
														<Button
															variant="ghost"
															size="icon"
															onClick={() =>
																handleEdit(doc)
															}
															title="Edit document"
															aria-label="Edit document"
														>
															<Pencil className="h-4 w-4" />
														</Button>
														<Button
															variant="ghost"
															size="icon"
															onClick={() =>
																handleDelete(
																	doc,
																)
															}
															title="Delete document"
															aria-label="Delete document"
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

							{/* Pagination */}
							<div className="flex items-center justify-between py-4 border-t">
								<div className="flex items-center gap-2 text-sm text-muted-foreground">
									<span>
										Showing{" "}
										{currentPage * pageSize + 1} to{" "}
										{Math.min(
											(currentPage + 1) * pageSize,
											totalDocuments,
										)}{" "}
										of {totalDocuments} documents
									</span>
									<Select
										value={pageSize.toString()}
										onValueChange={handlePageSizeChange}
									>
										<SelectTrigger className="w-[80px] h-8">
											<SelectValue />
										</SelectTrigger>
										<SelectContent>
											{PAGE_SIZES.map((size) => (
												<SelectItem
													key={size}
													value={size.toString()}
												>
													{size}
												</SelectItem>
											))}
										</SelectContent>
									</Select>
									<span>per page</span>
								</div>
								<div className="flex items-center gap-2">
									<Button
										variant="outline"
										size="icon"
										onClick={() =>
											setCurrentPage((p) =>
												Math.max(0, p - 1),
											)
										}
										disabled={currentPage === 0}
										aria-label="Previous page"
									>
										<ChevronLeft className="h-4 w-4" />
									</Button>
									<span className="text-sm">
										Page {currentPage + 1} of{" "}
										{Math.max(1, totalPages)}
									</span>
									<Button
										variant="outline"
										size="icon"
										onClick={() =>
											setCurrentPage((p) =>
												Math.min(totalPages - 1, p + 1),
											)
										}
										disabled={currentPage >= totalPages - 1}
										aria-label="Next page"
									>
										<ChevronRight className="h-4 w-4" />
									</Button>
								</div>
							</div>
						</div>
					) : (
						// Empty State
						<Card>
							<CardContent className="flex flex-col items-center justify-center py-12 text-center">
								<FileJson2 className="h-12 w-12 text-muted-foreground" />
								<h3 className="mt-4 text-lg font-semibold">
									{hasActiveFilters || searchTerm
										? "No documents match your filters"
										: "No documents yet"}
								</h3>
								<p className="mt-2 text-sm text-muted-foreground">
									{hasActiveFilters || searchTerm
										? "Try adjusting your filter conditions or search term"
										: "Add your first document to this table"}
								</p>
								{hasActiveFilters || searchTerm ? (
									<Button
										variant="outline"
										onClick={() => {
											handleClearFilters();
											setSearchTerm("");
										}}
										className="mt-4"
									>
										Clear Filters
									</Button>
								) : (
									<Button
										variant="outline"
										onClick={handleAdd}
										className="mt-4"
									>
										<Plus className="mr-2 h-4 w-4" />
										Add a document
									</Button>
								)}
							</CardContent>
						</Card>
					)}
				</div>
			</div>

			{tableId && (
				<DocumentDialog
					document={selectedDocument}
					tableId={tableId}
					open={isDialogOpen}
					onClose={handleDialogClose}
				/>
			)}

			{/* Delete Confirmation Dialog */}
			<AlertDialog
				open={isDeleteDialogOpen}
				onOpenChange={setIsDeleteDialogOpen}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Delete Document</AlertDialogTitle>
						<AlertDialogDescription>
							Are you sure you want to delete this document? This
							action cannot be undone.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction
							onClick={handleConfirmDelete}
							className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							{deleteDocument.isPending
								? "Deleting..."
								: "Delete Document"}
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</div>
	);
}
