import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import {
	AlertTriangle,
	Plus,
	RefreshCw,
	Upload,
	Download,
	LayoutGrid,
	Table as TableIcon,
	CheckSquare,
} from "lucide-react";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
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
import { SearchBox } from "@/components/search/SearchBox";
import { useSearch } from "@/hooks/useSearch";
import { useIsDesktop } from "@/hooks/useMediaQuery";
import { CreateIntegrationDialog } from "@/components/integrations/CreateIntegrationDialog";
import { ListPageHeader } from "@/components/layout/ListPageHeader";
import { ListToolbar } from "@/components/layout/ListToolbar";
import { ListLoadError } from "@/components/layout/ListLoadError";
import {
	PageScrollArea,
	PageWorkspace,
} from "@/components/layout/PageWorkspace";
import { ImportDialog } from "@/components/ImportDialog";
import { exportEntities } from "@/services/exportImport";
import { GLOBAL_TARGET } from "@/lib/authorization";
import { useAuthorization } from "@/services/authorization";
import {
	useDeleteIntegration,
	useIntegrations,
	type Integration,
} from "@/services/integrations";
import { toast } from "sonner";
import { Link2 } from "lucide-react";
import { IntegrationList } from "./Integrations/IntegrationList";

function IntegrationEmptyState({
	hasSearch,
	canCreate,
	onCreate,
}: {
	hasSearch: boolean;
	canCreate: boolean;
	onCreate: () => void;
}) {
	return (
		<Card>
			<CardContent className="flex flex-col items-center justify-center py-12 text-center">
				<Link2
					aria-hidden="true"
					className="h-12 w-12 text-muted-foreground"
				/>
				<h3 className="mt-4 text-lg font-semibold">
					{hasSearch
						? "No integrations match your search"
						: "No integrations"}
				</h3>
				<p className="mt-2 max-w-md text-sm text-muted-foreground">
					{hasSearch
						? "Try adjusting your search term or clear the filter."
						: canCreate
							? "Get started by creating your first integration. Map organizations to external entities with OAuth and configuration schemas."
							: "No integrations are available in your current scope."}
				</p>
				{canCreate && (
					<Button
						type="button"
						variant="default"
						size="lg"
						onClick={onCreate}
						className="mt-4"
					>
						<Plus aria-hidden="true" className="size-4" />
						Create integration
					</Button>
				)}
			</CardContent>
		</Card>
	);
}

export function Integrations() {
	const navigate = useNavigate();
	const queryClient = useQueryClient();
	const isDesktop = useIsDesktop();
	const authorization = useAuthorization();
	const canManageDefinitions = authorization.canAt(
		"integrations.readwrite",
		GLOBAL_TARGET,
	);
	const canExport = authorization.canAt("integrations.read", GLOBAL_TARGET);
	const [viewMode, setViewMode] = useState<"grid" | "table">("grid");
	const showTable = isDesktop && viewMode === "table";

	const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);
	const [editIntegrationId, setEditIntegrationId] = useState<
		string | undefined
	>();
	const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
	const [integrationToDelete, setIntegrationToDelete] =
		useState<Integration | null>(null);
	const [deleteError, setDeleteError] = useState<string | null>(null);
	const [isDeleting, setIsDeleting] = useState(false);
	const [searchTerm, setSearchTerm] = useState("");
	const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
	const [isCardSelectMode, setIsCardSelectMode] = useState(false);
	const [isImportOpen, setIsImportOpen] = useState(false);
	const [isExporting, setIsExporting] = useState(false);

	const { data, isLoading, isError, isFetching, refetch } = useIntegrations();
	const deleteMutation = useDeleteIntegration();
	const integrations = data?.items ?? [];

	const filteredIntegrations = useSearch(integrations, searchTerm, [
		"name",
		"description",
		"list_entities_data_provider_id",
	]);

	const handleCreate = () => {
		if (!canManageDefinitions) return;
		setEditIntegrationId(undefined);
		setIsCreateDialogOpen(true);
	};

	const handleEdit = (integrationId: string) => {
		if (!canManageDefinitions) return;
		setEditIntegrationId(integrationId);
		setIsCreateDialogOpen(true);
	};

	const handleOpenIntegration = (integrationId: string) => {
		navigate(`/integrations/${integrationId}`);
	};

	const handleDelete = (integration: Integration) => {
		if (!canManageDefinitions) return;
		setIntegrationToDelete(integration);
		setDeleteError(null);
		setDeleteDialogOpen(true);
	};

	const handleConfirmDelete = async () => {
		if (!canManageDefinitions || !integrationToDelete || isDeleting) return;

		setIsDeleting(true);
		setDeleteError(null);
		try {
			await deleteMutation.mutateAsync({
				params: { path: { integration_id: integrationToDelete.id } },
			});
			toast.success("Integration deleted successfully");
			queryClient.invalidateQueries({ queryKey: ["integrations"] });
			setDeleteDialogOpen(false);
			setIntegrationToDelete(null);
		} catch (error) {
			console.error("Failed to delete integration:", error);
			setDeleteError("Failed to delete integration. Try again.");
			toast.error("Failed to delete integration");
		} finally {
			setIsDeleting(false);
		}
	};

	const handleDeleteDialogOpenChange = (open: boolean) => {
		if (!open && isDeleting) return;
		setDeleteDialogOpen(open);
		if (!open) {
			setIntegrationToDelete(null);
			setDeleteError(null);
		}
	};

	const toggleSelect = (id: string) => {
		setSelectedIds((prev) => {
			const next = new Set(prev);
			if (next.has(id)) next.delete(id);
			else next.add(id);
			return next;
		});
	};

	const handleToggleSelectAll = () => {
		setSelectedIds((prev) => {
			const next = new Set(prev);
			const allVisibleSelected =
				filteredIntegrations.length > 0 &&
				filteredIntegrations.every((integration) =>
					next.has(integration.id),
				);

			if (allVisibleSelected) {
				for (const integration of filteredIntegrations) {
					next.delete(integration.id);
				}
			} else {
				for (const integration of filteredIntegrations) {
					next.add(integration.id);
				}
			}

			return next;
		});
	};

	const handleDoneSelectingCards = () => {
		setIsCardSelectMode(false);
	};

	const handleExport = async () => {
		if (!canExport) return;
		const ids = selectedIds.size > 0 ? Array.from(selectedIds) : [];
		setIsExporting(true);
		try {
			await exportEntities("integrations", ids);
			toast.success("Export downloaded");
		} catch {
			toast.error("Export failed");
		} finally {
			setIsExporting(false);
		}
	};

	const selectedCount = selectedIds.size;
	const hasSearch = searchTerm.trim().length > 0;
	const showCardSelectionControls =
		canExport && !showTable && isCardSelectMode;

	return (
		<PageWorkspace className="mx-auto w-full max-w-[1400px]">
			<ListPageHeader
				title="Integrations"
				description="Configure integrations and map organizations to external entities"
				actions={
					<>
						<Button
							type="button"
							variant="outline"
							size="icon-lg"
							onClick={() => refetch()}
							aria-label="Refresh"
							title="Refresh"
						>
							<RefreshCw aria-hidden="true" className="size-4" />
						</Button>
						{canManageDefinitions && (
							<Button
								type="button"
								variant="default"
								size="lg"
								onClick={handleCreate}
							>
								<Plus aria-hidden="true" className="size-4" />
								Create integration
							</Button>
						)}
					</>
				}
			/>

			<ListToolbar>
				<SearchBox
					value={searchTerm}
					onChange={setSearchTerm}
					placeholder="Search integrations by name, OAuth provider, or data provider..."
					className="w-full sm:flex-1"
				/>
				{isDesktop && (
					<ToggleGroup
						type="single"
						aria-label="Integration layout"
						value={viewMode}
						onValueChange={(v) => {
							if (v) setViewMode(v as "grid" | "table");
						}}
					>
						<ToggleGroupItem
							value="grid"
							aria-label="Card view"
							className="gap-2"
						>
							<LayoutGrid className="size-4" />
							Cards
						</ToggleGroupItem>
						<ToggleGroupItem
							value="table"
							aria-label="Table view"
							className="gap-2"
						>
							<TableIcon className="size-4" />
							Table
						</ToggleGroupItem>
					</ToggleGroup>
				)}
				<div className="flex flex-wrap items-center gap-2 sm:ml-auto">
					{showCardSelectionControls && (
						<>
							<span className="text-sm text-muted-foreground">
								{selectedCount} selected
							</span>
							<Button
								type="button"
								variant="outline"
								size="lg"
								onClick={handleToggleSelectAll}
								disabled={filteredIntegrations.length === 0}
							>
								Select all
							</Button>
						</>
					)}
					{!showCardSelectionControls && selectedCount > 0 && (
						<span className="text-sm text-muted-foreground">
							{selectedCount} selected
						</span>
					)}
					{canExport && !showTable && !isCardSelectMode && (
						<Button
							type="button"
							variant="outline"
							size="lg"
							onClick={() => setIsCardSelectMode(true)}
						>
							<CheckSquare
								aria-hidden="true"
								className="mr-1 size-4"
							/>
							Select
						</Button>
					)}
					{canExport && (
						<Button
							type="button"
							variant="outline"
							size="lg"
							onClick={handleExport}
							disabled={isExporting}
						>
							<Download
								aria-hidden="true"
								className="mr-1 size-4"
							/>
							{selectedCount > 0
								? `Export (${selectedCount})`
								: "Export All"}
						</Button>
					)}
					{showCardSelectionControls && (
						<Button
							type="button"
							variant="default"
							size="lg"
							onClick={handleDoneSelectingCards}
						>
							Done
						</Button>
					)}
					{canManageDefinitions && (
						<Button
							type="button"
							variant="outline"
							size="lg"
							onClick={() => setIsImportOpen(true)}
						>
							<Upload
								aria-hidden="true"
								className="mr-1 size-4"
							/>
							Import
						</Button>
					)}
				</div>
			</ListToolbar>

			{isError && (
				<ListLoadError
					resource="integrations"
					hasCachedData={data !== undefined}
					isRetrying={isFetching}
					onRetry={() => void refetch()}
				/>
			)}

			<PageScrollArea
				aria-label="Integrations list"
				className={
					showTable
						? "lg:flex lg:flex-col lg:overflow-hidden"
						: undefined
				}
			>
				{isLoading && !data ? (
					<div className="space-y-2">
						{[...Array(3)].map((_, index) => (
							<div
								key={index}
								className="h-16 w-full animate-pulse rounded-[var(--bf-radius-control)] border border-border/70 bg-muted/30"
							/>
						))}
					</div>
				) : filteredIntegrations.length > 0 ? (
					<IntegrationList
						integrations={filteredIntegrations}
						isDesktop={showTable}
						isCardSelectMode={showCardSelectionControls}
						selectedIds={selectedIds}
						onToggleSelect={toggleSelect}
						onToggleSelectAll={handleToggleSelectAll}
						onOpen={handleOpenIntegration}
						onEdit={handleEdit}
						onDelete={handleDelete}
						canManageDefinitions={canManageDefinitions}
					/>
				) : (
					<IntegrationEmptyState
						hasSearch={hasSearch}
						canCreate={canManageDefinitions}
						onCreate={handleCreate}
					/>
				)}
			</PageScrollArea>

			{canManageDefinitions && (
				<ImportDialog
					open={isImportOpen}
					onOpenChange={setIsImportOpen}
					entityType="integrations"
					onImportComplete={() => refetch()}
				/>
			)}

			{canManageDefinitions && (
				<CreateIntegrationDialog
					open={isCreateDialogOpen}
					onOpenChange={setIsCreateDialogOpen}
					editIntegrationId={editIntegrationId}
				/>
			)}

			<AlertDialog
				open={canManageDefinitions && deleteDialogOpen}
				onOpenChange={handleDeleteDialogOpenChange}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle className="flex items-center gap-2">
							<AlertTriangle
								aria-hidden="true"
								className="size-5 text-destructive"
							/>
							Delete Integration
						</AlertDialogTitle>
						<AlertDialogDescription className="space-y-3">
							<p>
								Are you sure you want to delete the integration{" "}
								<strong className="text-foreground">
									{integrationToDelete?.name}
								</strong>
								?
							</p>
							<p className="text-sm text-destructive">
								This will also delete all organization mappings
								for this integration. This action cannot be
								undone.
							</p>
							{deleteError && (
								<p
									role="alert"
									className="text-sm text-destructive"
								>
									{deleteError}
								</p>
							)}
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel
							disabled={isDeleting}
							onClick={() => {
								setDeleteError(null);
							}}
						>
							Cancel
						</AlertDialogCancel>
						<AlertDialogAction
							onClick={(event) => {
								event.preventDefault();
								void handleConfirmDelete();
							}}
							disabled={isDeleting}
							className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							{isDeleting ? "Deleting…" : "Delete Integration"}
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</PageWorkspace>
	);
}
