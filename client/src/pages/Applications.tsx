/**
 * Applications Page
 *
 * Lists all App Builder applications with management capabilities.
 */

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
	Plus,
	RefreshCw,
	AppWindow,
	Pencil,
	Trash2,
	PlayCircle,
	Globe,
	Building2,
	LayoutGrid,
	Table as TableIcon,
	Eye,
	Code2,
} from "lucide-react";
import { EntityLogo } from "@/components/EntityLogo";
import { AppInfoDialog } from "@/components/app-builder/AppInfoDialog";
import { CreateAppModal } from "@/components/app-builder/CreateAppModal";
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
import { useApplications, useDeleteApplication } from "@/hooks/useApplications";
import { useOrgScope } from "@/contexts/OrgScopeContext";
import { useAuth } from "@/contexts/AuthContext";
import { useOrganizations } from "@/hooks/useOrganizations";
import { SearchBox } from "@/components/search/SearchBox";
import { useSearch } from "@/hooks/useSearch";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import type { components } from "@/lib/v1";

type Organization = components["schemas"]["OrganizationPublic"];

export function Applications() {
	const navigate = useNavigate();
	const { scope, isGlobalScope } = useOrgScope();
	const { isPlatformAdmin } = useAuth();
	const [filterOrgId, setFilterOrgId] = useState<string | null | undefined>(
		undefined,
	);
	const [searchTerm, setSearchTerm] = useState("");
	const [viewMode, setViewMode] = useState<"grid" | "table">("grid");
	const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false);
	const [isEngineSelectOpen, setIsEngineSelectOpen] = useState(false);
	const [infoDialogSlug, setInfoDialogSlug] = useState<string | null>(null);
	const [selectedApp, setSelectedApp] = useState<{
		id: string;
		name: string;
	} | null>(null);

	// Fetch applications
	const {
		data: applicationsData,
		isLoading,
		refetch,
	} = useApplications(
		isPlatformAdmin
			? filterOrgId === undefined
				? undefined
				: (filterOrgId ?? undefined)
			: undefined,
	);
	const applications = applicationsData?.applications ?? [];
	const deleteApplication = useDeleteApplication();

	// Fetch organizations for name lookup (platform admins only)
	const { data: organizations } = useOrganizations({
		enabled: isPlatformAdmin,
	});

	// Helper to get organization name from ID
	const getOrgName = (orgId: string | null | undefined): string => {
		if (!orgId) return "Global";
		const org = organizations?.find((o: Organization) => o.id === orgId);
		return org?.name || orgId;
	};

	// Only platform admins can manage applications
	const canManageApps = isPlatformAdmin;

	const handleCreate = () => {
		setIsEngineSelectOpen(true);
	};

	const handleOpenCode = (appSlug: string) => {
		navigate(`/apps/${appSlug}/edit`);
	};

	const handleOpenSettings = (appSlug: string) => {
		setInfoDialogSlug(appSlug);
	};

	const handlePreview = (appSlug: string) => {
		navigate(`/apps/${appSlug}/preview`);
	};

	const handleLaunch = (appSlug: string) => {
		navigate(`/apps/${appSlug}`);
	};

	const handleDelete = (appId: string, appName: string) => {
		setSelectedApp({ id: appId, name: appName });
		setIsDeleteDialogOpen(true);
	};

	const handleConfirmDelete = async () => {
		if (!selectedApp) return;
		await deleteApplication.mutateAsync({
			params: { path: { app_id: selectedApp.id } },
		});
		setIsDeleteDialogOpen(false);
		setSelectedApp(null);
	};

	// Filter and search applications
	const filteredApps = useSearch(applications || [], searchTerm, [
		"name",
		"description",
		"slug",
		(app) => app.id,
	]);

	return (
		<div className="h-full flex flex-col space-y-6 max-w-7xl mx-auto">
			<div className="flex items-center justify-between">
				<div>
					<div className="flex items-center gap-3">
						<h1 className="text-4xl font-extrabold tracking-tight">
							Applications
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
						{canManageApps
							? "Build and manage custom applications"
							: "Access your custom applications"}
					</p>
				</div>
				<div className="flex gap-2">
					{canManageApps && (
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
					)}
					<Button
						variant="outline"
						size="icon"
						onClick={() => refetch()}
						title="Refresh"
					>
						<RefreshCw className="h-4 w-4" />
					</Button>
					{canManageApps && (
						<Button
							variant="outline"
							size="icon"
							onClick={handleCreate}
							title="Create Application"
						>
							<Plus className="h-4 w-4" />
						</Button>
					)}
				</div>
			</div>

			{/* Search and Filters */}
			<div className="flex items-center gap-4">
				<SearchBox
					value={searchTerm}
					onChange={setSearchTerm}
					placeholder="Search applications by name, description, or slug..."
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
			</div>

			{isLoading ? (
				viewMode === "grid" || !canManageApps ? (
					<div className="grid gap-3 grid-cols-[repeat(auto-fill,minmax(280px,1fr))]">
						{[...Array(6)].map((_, i) => (
							<Skeleton key={i} className="h-48 w-full" />
						))}
					</div>
				) : (
					<div className="space-y-2">
						{[...Array(3)].map((_, i) => (
							<Skeleton key={i} className="h-12 w-full" />
						))}
					</div>
				)
			) : filteredApps && filteredApps.length > 0 ? (
				viewMode === "grid" || !canManageApps ? (
					<div className="grid gap-3 grid-cols-[repeat(auto-fill,minmax(220px,1fr))]">
						{filteredApps.map((app) => {
							const defaultTarget = app.is_published
								? () => handleLaunch(app.slug)
								: () => handlePreview(app.slug);
							return (
								<Card
									key={app.id}
									role="button"
									tabIndex={0}
									onClick={defaultTarget}
									onKeyDown={(e) => {
										if (e.key === "Enter" || e.key === " ") {
											e.preventDefault();
											defaultTarget();
										}
									}}
									className="group relative cursor-pointer hover:border-primary transition-colors flex flex-col overflow-hidden focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
								>
									{/* Body: logo + name + status badges */}
									<div className="flex items-center gap-3 p-4">
										<EntityLogo
											entityType="app"
											entityId={app.id}
											fallback={
												<div className="h-10 w-10 rounded bg-muted flex items-center justify-center shrink-0">
													<AppWindow className="h-5 w-5 text-muted-foreground" />
												</div>
											}
											size={40}
											className="h-10 w-10 rounded object-cover shrink-0 transition-[filter] group-hover:blur-sm"
										/>
										<div className="min-w-0 flex-1">
											<div className="font-medium truncate">
												{app.name}
											</div>
											<div className="mt-1 flex items-center gap-1.5 flex-wrap">
												{app.is_published && (
													<Badge
														variant="default"
														className="text-[10px] px-1.5 py-0"
													>
														Published
													</Badge>
												)}
												{app.has_unpublished_changes && (
													<Badge
														variant="outline"
														className="text-[10px] px-1.5 py-0"
													>
														Draft
													</Badge>
												)}
											</div>
										</div>
									</div>

									{/* Hover overlay: admin-only quick actions */}
									{canManageApps && (
										<>
											{/* Vertical "Open" menu, centered over the blurred logo area */}
											<div className="absolute inset-x-0 top-0 bottom-12 flex flex-col items-start justify-center gap-1 pl-5 bg-background/70 backdrop-blur-sm opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none">
												{app.is_published && (
													<button
														type="button"
														className="pointer-events-auto text-sm font-medium text-foreground hover:text-primary text-left"
														onClick={(e) => {
															e.stopPropagation();
															handleLaunch(app.slug);
														}}
													>
														<PlayCircle className="inline mr-1.5 h-3.5 w-3.5 -mt-0.5" />
														Open Published
													</button>
												)}
												<button
													type="button"
													className="pointer-events-auto text-sm font-medium text-foreground hover:text-primary text-left"
													onClick={(e) => {
														e.stopPropagation();
														handlePreview(app.slug);
													}}
												>
													<Eye className="inline mr-1.5 h-3.5 w-3.5 -mt-0.5" />
													Open Preview
												</button>
											</div>

											{/* Top-right toolbar: Edit (settings dialog) + Code editor */}
											<div className="absolute top-2 right-2 flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
												<Button
													type="button"
													variant="secondary"
													size="icon"
													className="h-7 w-7"
													onClick={(e) => {
														e.stopPropagation();
														handleOpenSettings(
															app.slug,
														);
													}}
													title="Settings"
												>
													<Pencil className="h-3.5 w-3.5" />
												</Button>
												<Button
													type="button"
													variant="secondary"
													size="icon"
													className="h-7 w-7"
													onClick={(e) => {
														e.stopPropagation();
														handleOpenCode(
															app.slug,
														);
													}}
													title="Code editor"
												>
													<Code2 className="h-3.5 w-3.5" />
												</Button>
											</div>
										</>
									)}
								</Card>
							);
						})}
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
									<DataTableHead className="w-0 whitespace-nowrap">Status</DataTableHead>
									<DataTableHead className="w-0 whitespace-nowrap">Version</DataTableHead>
									<DataTableHead className="w-0 whitespace-nowrap text-right" />
								</DataTableRow>
							</DataTableHeader>
							<DataTableBody>
								{filteredApps.map((app) => (
									<DataTableRow key={app.id}>
										{isPlatformAdmin && (
											<DataTableCell className="w-0 whitespace-nowrap">
												{app.organization_id ? (
													<Badge
														variant="outline"
														className="text-xs"
													>
														<Building2 className="mr-1 h-3 w-3" />
														{getOrgName(
															app.organization_id,
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
										<DataTableCell className="font-medium">
											{app.name}
										</DataTableCell>
										<DataTableCell className="max-w-xs truncate text-muted-foreground">
											{app.description || (
												<span className="italic">
													No description
												</span>
											)}
										</DataTableCell>
										<DataTableCell className="w-0 whitespace-nowrap">
											<div className="flex gap-1">
												{app.is_published && (
													<Badge
														variant="default"
														className="text-xs"
													>
														Published
													</Badge>
												)}
												{app.has_unpublished_changes && (
													<Badge
														variant="outline"
														className="text-xs"
													>
														Draft
													</Badge>
												)}
												{!app.is_published &&
													!app.has_unpublished_changes && (
														<Badge
															variant="secondary"
															className="text-xs"
														>
															Empty
														</Badge>
													)}
											</div>
										</DataTableCell>
										<DataTableCell className="w-0 whitespace-nowrap">
											{app.is_published ? "Published" : "-"}
										</DataTableCell>
										<DataTableCell className="w-0 whitespace-nowrap text-right">
											<div className="flex gap-1 justify-end">
												<Button
													size="sm"
													onClick={() =>
														handleLaunch(app.slug)
													}
													disabled={!app.is_published}
													title={
														!app.is_published
															? "No published version"
															: "Open application"
													}
												>
													<PlayCircle className="h-4 w-4" />
												</Button>
												{canManageApps && (
													<>
														{app.has_unpublished_changes && (
															<Button
																variant="ghost"
																size="sm"
																onClick={() =>
																	handlePreview(
																		app.slug,
																	)
																}
																title="Preview draft"
															>
																<Eye className="h-4 w-4" />
															</Button>
														)}
														<Button
															variant="ghost"
															size="sm"
															onClick={() =>
																handleOpenSettings(
																	app.slug,
																)
															}
															title="Settings"
														>
															<Pencil className="h-4 w-4" />
														</Button>
														<Button
															variant="ghost"
															size="sm"
															onClick={() =>
																handleOpenCode(
																	app.slug,
																)
															}
															title="Code editor"
														>
															<Code2 className="h-4 w-4" />
														</Button>
														<Button
															variant="ghost"
															size="sm"
															onClick={() =>
																handleDelete(
																	app.id,
																	app.name,
																)
															}
															title="Delete application"
														>
															<Trash2 className="h-4 w-4" />
														</Button>
													</>
												)}
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
						<AppWindow className="h-12 w-12 text-muted-foreground" />
						<h3 className="mt-4 text-lg font-semibold">
							{searchTerm
								? "No applications match your search"
								: "No applications found"}
						</h3>
						<p className="mt-2 text-sm text-muted-foreground">
							{searchTerm
								? "Try adjusting your search term or clear the filter"
								: canManageApps
									? "Get started by creating your first application"
									: "No applications are currently available"}
						</p>
						{canManageApps && !searchTerm && (
							<Button
								variant="outline"
								size="icon"
								onClick={handleCreate}
								className="mt-4"
								title="Create Application"
							>
								<Plus className="h-4 w-4" />
							</Button>
						)}
					</CardContent>
				</Card>
			)}

			{/* Delete Confirmation Dialog */}
			<AlertDialog
				open={isDeleteDialogOpen}
				onOpenChange={setIsDeleteDialogOpen}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>Delete Application?</AlertDialogTitle>
						<AlertDialogDescription>
							This will permanently delete the application "
							{selectedApp?.name}" including all versions and
							data. This action cannot be undone.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction
							onClick={handleConfirmDelete}
							className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							{deleteApplication.isPending
								? "Deleting..."
								: "Delete Application"}
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>

			{/* Create Application Dialog */}
			<CreateAppModal
				open={isEngineSelectOpen}
				onOpenChange={setIsEngineSelectOpen}
			/>

			{/* Application Settings Dialog (opened from card pencil button) */}
			<AppInfoDialog
				appSlug={infoDialogSlug}
				open={infoDialogSlug !== null}
				onOpenChange={(o) => {
					if (!o) setInfoDialogSlug(null);
				}}
			/>
		</div>
	);
}
