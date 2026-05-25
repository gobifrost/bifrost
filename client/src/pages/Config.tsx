import { useState } from "react";
import {
	Pencil,
	Plus,
	Trash2,
	Key,
	RefreshCw,
	Globe,
	Building2,
	AlertTriangle,
	Loader2,
	Download,
	Upload,
} from "lucide-react";
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
import { SearchBox } from "@/components/search/SearchBox";
import { useWeightedSearch } from "@/hooks/useSearch";
import { toast } from "sonner";

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
	const { scope, isGlobalScope } = useOrgScope();
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
		refetch,
	} = useConfigs(isPlatformAdmin ? filterOrgId : undefined);
	const deleteConfig = useDeleteConfig();

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

	const handleConfirmDelete = () => {
		if (!configToDelete) return;

		deleteConfig.mutate(
			{ params: { path: { config_id: configToDelete.id! } } },
			{
				onSettled: () => {
					setDeleteDialogOpen(false);
					setConfigToDelete(null);
				},
			},
		);
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

	const toggleSelectAll = () => {
		if (selectedIds.size === filteredConfigs.length) {
			setSelectedIds(new Set());
		} else {
			setSelectedIds(
				new Set(
					filteredConfigs.map(
						(c) => `${c.scope}-${c.key}`,
					),
				),
			);
		}
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

	const getTypeBadge = (type: string) => {
		const variants: Record<
			string,
			"default" | "secondary" | "destructive" | "outline"
		> = {
			string: "default",
			int: "secondary",
			bool: "outline",
			json: "secondary",
			secret_ref: "destructive",
		};
		return <Badge variant={variants[type] || "default"}>{type}</Badge>;
	};

	const maskValue = (value: unknown, type: string) => {
		if (!value) return "-";
		const strValue = String(value);
		if (type === "secret_ref") {
			return "••••••••";
		}
		if (strValue.length > 50) {
			return strValue.substring(0, 50) + "...";
		}
		return strValue;
	};

	return (
		<div className="h-full flex flex-col space-y-6">
			<div className="flex items-center justify-between">
				<div>
					<div className="flex items-center gap-3">
						<h1 className="text-4xl font-extrabold tracking-tight">
							Configuration
						</h1>
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
					</div>
					<p className="mt-2 text-muted-foreground">
						{isGlobalScope
							? "Platform-wide configuration values"
							: `Configuration for ${scope.orgName || "this organization"}`}
					</p>
				</div>
				<div className="flex gap-2">
					<Button
						variant="outline"
						size="icon"
						onClick={() => refetch()}
						disabled={isFetching}
					>
						<RefreshCw
							className={`h-4 w-4 ${
								isFetching ? "animate-spin" : ""
							}`}
						/>
					</Button>
					<Button
						variant="outline"
						size="icon"
						onClick={handleAdd}
						title="Add Config"
					>
						<Plus className="h-4 w-4" />
					</Button>
				</div>
			</div>

			{/* Search and Filters */}
			<div className="flex items-center gap-4">
				<SearchBox
					value={searchTerm}
					onChange={setSearchTerm}
					placeholder="Search config by key, value, type, or description..."
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
			{isFetching ? (
				<div className="flex items-center justify-center py-12">
					<Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
				</div>
			) : filteredConfigs && filteredConfigs.length > 0 ? (
				<div className="flex-1 min-h-0">
					<DataTable className="max-h-full">
						<DataTableHeader>
							<DataTableRow>
								{isPlatformAdmin && (
									<DataTableHead className="w-10">
										<Checkbox
											checked={
												filteredConfigs.length > 0 &&
												selectedIds.size ===
													filteredConfigs.length
											}
											onCheckedChange={toggleSelectAll}
										/>
									</DataTableHead>
								)}
								{isPlatformAdmin && (
									<DataTableHead className="w-0 whitespace-nowrap">Organization</DataTableHead>
								)}
								<DataTableHead className="w-0 whitespace-nowrap">Integration</DataTableHead>
								<DataTableHead>Key</DataTableHead>
								<DataTableHead className="w-0 whitespace-nowrap">Value</DataTableHead>
								<DataTableHead className="w-0 whitespace-nowrap">Type</DataTableHead>
								<DataTableHead>Description</DataTableHead>
								<DataTableHead className="w-0 whitespace-nowrap text-right" />
							</DataTableRow>
						</DataTableHeader>
						<DataTableBody>
							{filteredConfigs.map((config) => (
								<DataTableRow
									key={`${config.scope}-${config.key}`}
								>
									{isPlatformAdmin && (
										<DataTableCell>
											<Checkbox
												checked={selectedIds.has(
													`${config.scope}-${config.key}`,
												)}
												onCheckedChange={() =>
													toggleSelect(
														`${config.scope}-${config.key}`,
													)
												}
											/>
										</DataTableCell>
									)}
									{isPlatformAdmin && (
										<DataTableCell className="w-0 whitespace-nowrap">
											{config.org_id ? (
												<Badge
													variant="outline"
													className="text-xs"
												>
													<Building2 className="mr-1 h-3 w-3" />
													{getOrgName(config.org_id)}
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
									<DataTableCell className="w-0 whitespace-nowrap">
										{config.integration_name ? (
											<Badge variant="outline" className="text-xs">
												{config.integration_name}
											</Badge>
										) : (
											<span className="text-muted-foreground">-</span>
										)}
									</DataTableCell>
									<DataTableCell className="font-mono">
										{config.key}
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap max-w-xs truncate">
										{maskValue(config.value, config.type)}
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap">
										{getTypeBadge(config.type)}
									</DataTableCell>
									<DataTableCell className="max-w-xs truncate text-muted-foreground">
										{config.description || "-"}
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap text-right">
										<div className="flex justify-end gap-2">
											<Button
												variant="ghost"
												size="icon"
												onClick={() =>
													handleEdit(config)
												}
											>
												<Pencil className="h-4 w-4" />
											</Button>
											<Button
												variant="ghost"
												size="icon"
												onClick={() =>
													handleDelete(config)
												}
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
							size="icon"
							onClick={handleAdd}
							className="mt-4"
							title="Add Config"
						>
							<Plus className="h-4 w-4" />
						</Button>
					</CardContent>
				</Card>
			)}

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

			{/* Delete Confirmation Dialog */}
			<AlertDialog
				open={deleteDialogOpen}
				onOpenChange={setDeleteDialogOpen}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle className="flex items-center gap-2">
							<AlertTriangle className="h-5 w-5 text-destructive" />
							Delete Configuration
						</AlertDialogTitle>
						<AlertDialogDescription className="space-y-3">
							<p>
								Are you sure you want to delete the config{" "}
								<strong className="text-foreground">
									{configToDelete?.key}
								</strong>
								?
							</p>
							<div className="bg-muted p-3 rounded-md border border-border">
								<p className="text-sm font-medium text-foreground mb-2">
									Before deleting:
								</p>
								<p className="text-sm">
									We recommend searching for{" "}
									<code className="bg-background px-1.5 py-0.5 rounded text-xs">
										get_config('{configToDelete?.key}')
									</code>{" "}
									in your{" "}
									<code className="bg-background px-1.5 py-0.5 rounded text-xs">
										@workflows/workspace/
									</code>{" "}
									repo to confirm it isn't being used.
								</p>
							</div>
							<p className="text-sm text-destructive">
								Workflows using this config will fail if it's
								deleted. This action cannot be undone.
							</p>
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction
							onClick={handleConfirmDelete}
							className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							I'm Sure - Delete Config
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</div>
	);
}
