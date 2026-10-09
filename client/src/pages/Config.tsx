import { useRef, useState } from "react";
import {
	Pencil,
	Plus,
	Trash2,
	Key,
	RefreshCw,
	Globe,
	Building2,
	Loader2,
	Download,
	Upload,
} from "lucide-react";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { Alert, AlertTitle, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ConfigDeleteDialog } from "@/components/config/ConfigDeleteDialog";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { SearchBox } from "@/components/search/SearchBox";
import { useWeightedSearch } from "@/hooks/useSearch";
import { toast } from "sonner";
import { ListPageHeader } from "@/components/layout/ListPageHeader";
import { ListToolbar } from "@/components/layout/ListToolbar";
import {
	PageScrollArea,
	PageWorkspace,
} from "@/components/layout/PageWorkspace";
import { RecordActionsMenu } from "@/components/common/RecordActionsMenu";

import { useConfigs, useDeleteConfig } from "@/hooks/useConfig";
import { ImportDialog } from "@/components/ImportDialog";
import { exportEntities } from "@/services/exportImport";
import { ConfigDialog } from "@/components/config/ConfigDialog";
import { useOrgScope } from "@/contexts/OrgScopeContext";
import { useAuth } from "@/contexts/AuthContext";
import { useOrganizations } from "@/hooks/useOrganizations";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import type { components } from "@/lib/v1";

type ConfigType = components["schemas"]["ConfigResponse"];
type Organization = components["schemas"]["OrganizationPublic"];

export function Config() {
	const addButtonRef = useRef<HTMLButtonElement>(null);
	const { scope, isGlobalScope } = useOrgScope();
	const isNarrow = useMediaQuery("(max-width: 1279px)");
	const { isPlatformAdmin } = useAuth();
	const [filterOrgId, setFilterOrgId] = useState<string | null | undefined>(
		undefined,
	);
	const [selectedConfig, setSelectedConfig] = useState<
		ConfigType | undefined
	>();
	const [isDialogOpen, setIsDialogOpen] = useState(false);
	const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
	const [configToDelete, setConfigToDelete] = useState<ConfigType | null>(
		null,
	);
	const [searchTerm, setSearchTerm] = useState("");
	const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
	const [isImportOpen, setIsImportOpen] = useState(false);
	const [isExporting, setIsExporting] = useState(false);

	// Pass filterOrgId to backend for filtering (undefined = all, null = global only)
	// For platform admins, undefined means show all. For non-admins, backend handles filtering.
	const {
		data: configs,
		isFetching,
		isLoading,
		isError,
		refetch,
	} = useConfigs(isPlatformAdmin ? filterOrgId : undefined);
	const deleteConfig = useDeleteConfig({ showErrorToast: false });

	// Fetch organizations for the org name lookup (platform admins only)
	const { data: organizations } = useOrganizations({
		enabled: isPlatformAdmin,
	});

	// Helper to get organization name from ID
	const getOrgName = (orgId: string | null | undefined): string => {
		if (!orgId) return "Global";
		const org = organizations?.find((o: Organization) => o.id === orgId);
		return org?.name || orgId;
	};

	// Apply weighted search — key matches dominate, integration_name/value/type
	// still match but rank lower so a specific key search isn't drowned out by
	// every config tied to a matching integration.
	const filteredConfigs = useWeightedSearch(configs || [], searchTerm, [
		{ field: "key", weight: 10 },
		{ field: "description", weight: 5 },
		{ field: "integration_name", weight: 3 },
		{ field: "value", weight: 1 },
		{ field: "type", weight: 1 },
	]);

	// React Query automatically refetches when scope changes (via orgId in query key)

	const handleEdit = (config: ConfigType) => {
		setSelectedConfig(config);
		setIsDialogOpen(true);
	};

	const handleAdd = () => {
		setSelectedConfig(undefined);
		setIsDialogOpen(true);
	};

	const handleDelete = (config: ConfigType) => {
		setConfigToDelete(config);
		setDeleteDialogOpen(true);
	};

	const handleConfirmDelete = async () => {
		if (!configToDelete?.id) return;
		await deleteConfig.mutateAsync({
			params: { path: { config_id: configToDelete.id } },
		});
		setDeleteDialogOpen(false);
		setConfigToDelete(null);
	};

	const handleDialogClose = () => {
		setIsDialogOpen(false);
		setSelectedConfig(undefined);
	};

	const toggleSelect = (id: string) => {
		setSelectedIds((prev) => {
			const next = new Set(prev);
			if (next.has(id)) next.delete(id);
			else next.add(id);
			return next;
		});
	};

	const visibleIds = filteredConfigs.flatMap((config) =>
		config.id ? [config.id] : [],
	);
	const allVisibleSelected =
		visibleIds.length > 0 && visibleIds.every((id) => selectedIds.has(id));
	const someVisibleSelected = visibleIds.some((id) => selectedIds.has(id));
	const toggleSelectAll = () => {
		setSelectedIds((previous) => {
			const next = new Set(previous);
			for (const id of visibleIds) {
				if (allVisibleSelected) next.delete(id);
				else next.add(id);
			}
			return next;
		});
	};

	const handleExport = async () => {
		const ids = selectedIds.size > 0 ? Array.from(selectedIds) : [];
		setIsExporting(true);
		try {
			await exportEntities("configs", ids);
			toast.success("Export downloaded");
		} catch {
			toast.error("Export failed");
		} finally {
			setIsExporting(false);
		}
	};

	const getTypeBadge = (type: string) => (
		<Badge variant="outline">{type}</Badge>
	);
	const maskValue = (value: unknown, type: string) => {
		if (type === "secret" || type === "secret_ref") return "••••••••";
		if (value === null || value === undefined || value === "") return "—";
		const text =
			typeof value === "object" ? JSON.stringify(value) : String(value);
		return text.length > 160 ? `${text.slice(0, 160)}…` : text;
	};
	const renderActions = (config: ConfigType) => (
		<RecordActionsMenu label={`More actions for ${config.key}`}>
			<DropdownMenuItem
				className="min-h-11 whitespace-nowrap px-3"
				onClick={() => handleEdit(config)}
			>
				<Pencil className="mr-2 h-4 w-4" />
				Edit
			</DropdownMenuItem>
			<DropdownMenuItem
				variant="destructive"
				className="min-h-11 whitespace-nowrap px-3"
				disabled={!config.id}
				onClick={() => handleDelete(config)}
			>
				<Trash2 className="mr-2 h-4 w-4" />
				Delete
			</DropdownMenuItem>
		</RecordActionsMenu>
	);
	const renderSelection = (config: ConfigType) => (
		<Checkbox
			aria-label={`Select ${config.key}`}
			disabled={!config.id}
			checked={!!config.id && selectedIds.has(config.id)}
			onCheckedChange={() => config.id && toggleSelect(config.id)}
		/>
	);
	const renderSelectAll = () => (
		<Checkbox
			aria-label="Select visible configuration"
			disabled={visibleIds.length === 0}
			checked={
				allVisibleSelected
					? true
					: someVisibleSelected
						? "indeterminate"
						: false
			}
			onCheckedChange={toggleSelectAll}
		/>
	);

	return (
		<PageWorkspace className="mx-auto w-full max-w-[1400px]">
			<ListPageHeader
				title="Configuration"
				description={
					isGlobalScope
						? "Platform-wide configuration values"
						: `Configuration for ${scope.orgName || "this organization"}`
				}
				actions={
					<>
						<Button
							variant="outline"
							size="icon"
							onClick={() => refetch()}
							disabled={isFetching}
							aria-label="Refresh configuration"
							className="h-11 w-11 lg:h-9 lg:w-9"
						>
							<RefreshCw
								className={`h-4 w-4 ${
									isFetching
										? "animate-spin motion-reduce:animate-none"
										: ""
								}`}
							/>
						</Button>
						<Button
							className="min-h-11 lg:min-h-0"
							ref={addButtonRef}
							onClick={handleAdd}
							title="Add Config"
						>
							<Plus className="h-4 w-4" />
							Add configuration
						</Button>
					</>
				}
			/>

			<ListToolbar>
				<SearchBox
					value={searchTerm}
					onChange={setSearchTerm}
					aria-label="Search configuration"
					placeholder="Search configuration..."
					className="w-full sm:flex-1"
				/>
				{isPlatformAdmin && (
					<div className="w-full sm:w-64">
						<OrganizationSelect
							value={filterOrgId}
							onChange={setFilterOrgId}
							showAll={true}
							showGlobal={true}
							placeholder="All organizations"
						/>
					</div>
				)}
				{isPlatformAdmin && (
					<div className="grid w-full grid-cols-2 gap-2 sm:ml-auto sm:w-auto sm:flex sm:flex-wrap sm:items-center">
						{selectedIds.size > 0 && (
							<span className="col-span-2 text-sm text-muted-foreground sm:col-span-1 sm:mr-1">
								{selectedIds.size} selected
							</span>
						)}
						<Button
							variant="outline"
							size="sm"
							className="min-h-11 w-full sm:w-auto lg:min-h-0"
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
							className="min-h-11 w-full sm:w-auto lg:min-h-0"
							onClick={() => setIsImportOpen(true)}
						>
							<Upload className="h-4 w-4 mr-1" />
							Import
						</Button>
					</div>
				)}
			</ListToolbar>

			{isError && (
				<Alert variant="destructive">
					<AlertTitle>
						Configuration could not be{" "}
						{configs ? "refreshed" : "loaded"}
					</AlertTitle>
					<AlertDescription>
						Try again to load configuration.
						{configs &&
							" Previously loaded values are still shown."}
					</AlertDescription>
					<Button
						variant="outline"
						className="mt-3 min-h-11 lg:min-h-0"
						disabled={isFetching}
						onClick={() => refetch()}
					>
						Retry configuration
					</Button>
				</Alert>
			)}
			{/* Content */}
			<PageScrollArea
				aria-label="Configuration list"
				className="xl:flex xl:flex-col xl:overflow-hidden"
			>
				{isLoading ? (
					<div
						className="flex items-center justify-center py-12"
						role="status"
						aria-label="Loading configuration"
					>
						<Loader2 className="h-8 w-8 animate-spin motion-reduce:animate-none text-muted-foreground" />
					</div>
				) : isError && !configs ? null : filteredConfigs.length > 0 ? (
					<div className="flex-1 min-h-0">
						{isNarrow ? (
							<div className="rounded-[var(--bf-radius-surface)] border bg-card">
								{isPlatformAdmin && (
									<label className="flex min-h-11 items-center gap-3 border-b px-4 py-2 text-sm">
										{renderSelectAll()}Select visible
									</label>
								)}
								<ul
									aria-label="Configuration"
									className="divide-y"
								>
									{filteredConfigs.map((config) => (
										<li
											key={
												config.id ??
												`${config.org_id}-${config.key}`
											}
											className="min-w-0 space-y-3 p-4"
										>
											<div className="flex items-start gap-2">
												{isPlatformAdmin && (
													<label className="flex h-11 w-11 shrink-0 items-center justify-center">
														{renderSelection(
															config,
														)}
													</label>
												)}
												<h2 className="min-w-0 flex-1 py-1 font-mono text-sm font-medium [overflow-wrap:anywhere]">
													<button
														type="button"
														className="min-h-11 w-full rounded-[var(--bf-radius-control)] text-left hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
														onClick={() =>
															handleEdit(config)
														}
													>
														{config.key}
													</button>
												</h2>
												{renderActions(config)}
											</div>
											<div className="flex flex-wrap items-center justify-between gap-2">
												{getTypeBadge(config.type)}
											</div>
											<dl className="grid gap-3 text-sm sm:grid-cols-2">
												{isPlatformAdmin && (
													<div>
														<dt className="text-xs text-muted-foreground">
															Organization
														</dt>
														<dd className="mt-1 [overflow-wrap:anywhere]">
															{getOrgName(
																config.org_id,
															)}
														</dd>
													</div>
												)}
												<div>
													<dt className="text-xs text-muted-foreground">
														Value
													</dt>
													<dd className="mt-1 line-clamp-2 font-mono text-xs leading-5 [overflow-wrap:anywhere]">
														{maskValue(
															config.value,
															config.type,
														)}
													</dd>
												</div>
												{config.integration_name && (
													<div>
														<dt className="text-xs text-muted-foreground">
															Integration
														</dt>
														<dd className="mt-1 [overflow-wrap:anywhere]">
															{
																config.integration_name
															}
														</dd>
													</div>
												)}
												{config.description && (
													<div>
														<dt className="text-xs text-muted-foreground">
															Description
														</dt>
														<dd className="mt-1 text-muted-foreground [overflow-wrap:anywhere]">
															{config.description}
														</dd>
													</div>
												)}
											</dl>
										</li>
									))}
								</ul>
							</div>
						) : (
							<DataTable className="max-h-full">
								<DataTableHeader>
									<DataTableRow>
										{isPlatformAdmin && (
											<DataTableHead className="w-10">
												{renderSelectAll()}
											</DataTableHead>
										)}
										{isPlatformAdmin && (
											<DataTableHead className="w-0 whitespace-nowrap">
												Organization
											</DataTableHead>
										)}
										<DataTableHead className="w-0 whitespace-nowrap">
											Integration
										</DataTableHead>
										<DataTableHead>Key</DataTableHead>
										<DataTableHead className="w-0 whitespace-nowrap">
											Value
										</DataTableHead>
										<DataTableHead className="w-0 whitespace-nowrap">
											Type
										</DataTableHead>
										<DataTableHead>
											Description
										</DataTableHead>
										<DataTableHead className="w-0 whitespace-nowrap text-right" />
									</DataTableRow>
								</DataTableHeader>
								<DataTableBody>
									{filteredConfigs.map((config) => (
										<DataTableRow
											clickable
											onClick={() => handleEdit(config)}
											key={
												config.id ??
												`${config.org_id}-${config.key}`
											}
										>
											{isPlatformAdmin && (
												<DataTableCell>
													{renderSelection(config)}
												</DataTableCell>
											)}
											{isPlatformAdmin && (
												<DataTableCell className="min-w-40 max-w-56 [overflow-wrap:anywhere]">
													{config.org_id ? (
														<Badge
															variant="outline"
															className="max-w-full whitespace-nowrap text-xs"
														>
															<Building2 className="mr-1 h-3 w-3" />
															{getOrgName(
																config.org_id,
															)}
														</Badge>
													) : (
														<Badge
															variant="outline"
															className="max-w-full whitespace-nowrap text-xs"
														>
															<Globe className="mr-1 h-3 w-3" />
															Global
														</Badge>
													)}
												</DataTableCell>
											)}
											<DataTableCell className="min-w-28 max-w-40 [overflow-wrap:anywhere]">
												{config.integration_name ? (
													<span className="text-xs [overflow-wrap:anywhere]">
														{
															config.integration_name
														}
													</span>
												) : (
													<span className="text-muted-foreground">
														-
													</span>
												)}
											</DataTableCell>
											<DataTableCell className="min-w-40 max-w-64 font-mono text-xs [overflow-wrap:anywhere]">
												<button
													type="button"
													className="min-h-11 rounded-[var(--bf-radius-control)] text-left hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
													onClick={() =>
														handleEdit(config)
													}
												>
													{config.key}
												</button>
											</DataTableCell>
											<DataTableCell className="w-56 min-w-48 max-w-64 font-mono text-xs">
												<span className="line-clamp-2 max-w-56 leading-5 [overflow-wrap:anywhere]">
													{maskValue(
														config.value,
														config.type,
													)}
												</span>
											</DataTableCell>
											<DataTableCell className="min-w-28 max-w-40 [overflow-wrap:anywhere]">
												{getTypeBadge(config.type)}
											</DataTableCell>
											<DataTableCell className="min-w-32 max-w-56 text-sm text-muted-foreground [overflow-wrap:anywhere]">
												{config.description || "-"}
											</DataTableCell>
											<DataTableCell className="w-0 whitespace-nowrap text-right">
												{renderActions(config)}
											</DataTableCell>
										</DataTableRow>
									))}
								</DataTableBody>
							</DataTable>
						)}
					</div>
				) : (
					<Card>
						<CardContent className="flex flex-col items-center justify-center py-12 text-center">
							<Key className="h-12 w-12 text-muted-foreground" />
							<h3 className="mt-4 text-lg font-semibold">
								{searchTerm
									? "No configuration matches your search"
									: "No configuration found"}
							</h3>
							<p className="mt-2 text-sm text-muted-foreground">
								{searchTerm
									? "Try adjusting your search term or clear the filter"
									: "Get started by creating your first config entry"}
							</p>
							<Button
								variant="outline"
								className="mt-4 min-h-11 lg:min-h-0"
								onClick={handleAdd}
								title="Add Config"
							>
								<Plus className="h-4 w-4" />
								Add configuration
							</Button>
						</CardContent>
					</Card>
				)}
			</PageScrollArea>

			<ConfigDialog
				config={selectedConfig}
				open={isDialogOpen}
				onClose={handleDialogClose}
			/>

			<ImportDialog
				open={isImportOpen}
				onOpenChange={setIsImportOpen}
				entityType="configs"
				onImportComplete={() => refetch()}
			/>

			{deleteDialogOpen && configToDelete && (
				<ConfigDeleteDialog
					name={configToDelete.key}
					onOpenChange={setDeleteDialogOpen}
					onConfirm={handleConfirmDelete}
					returnFocusRef={addButtonRef}
				/>
			)}
		</PageWorkspace>
	);
}
