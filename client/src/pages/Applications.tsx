import { useIsDesktop } from "@/hooks/useMediaQuery";
/**
 * Applications Page
 *
 * Lists all App Builder applications with management capabilities.
 */

import { useId, useState, useRef } from "react";
import { useNavigate } from "react-router-dom";
import {
	RefreshCw,
	LayoutGrid,
	Table as TableIcon,
	CheckSquare,
} from "lucide-react";
import { AppInfoDialog } from "@/components/app-builder/AppInfoDialog";
import {
	ApplicationListSurface,
	type ApplicationListItem,
} from "@/components/applications/ApplicationListSurface";
import { Button } from "@/components/ui/button";
import { ListPageHeader } from "@/components/layout/ListPageHeader";
import { ListToolbar } from "@/components/layout/ListToolbar";
import {
	PageScrollArea,
	PageWorkspace,
} from "@/components/layout/PageWorkspace";
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
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import {
	batchUpdateApplicationSdks,
	useApplications,
	useDeleteApplication,
	useUpdateApplicationSdk,
} from "@/hooks/useApplications";
import { useApplicationSdkUpdateJobs } from "@/hooks/useApplicationSdkUpdateJobs";
import { canUpdateApplicationSdk } from "@/components/applications/ApplicationSdkStatusBadge";
import { useOrganizations } from "@/hooks/useOrganizations";
import { useOrganizationName } from "@/hooks/useOrganizationName";
import { SearchBox } from "@/components/search/SearchBox";
import { useSearch } from "@/hooks/useSearch";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { term, useTerminology } from "@/lib/terminology";
import type { components } from "@/lib/v1";
import { toast } from "sonner";
import { orgTarget } from "@/lib/authorization";
import { useAuthorization } from "@/services/authorization";

export function Applications() {
	const updateAllSdkDescriptionId = useId();
	const isDesktop = useIsDesktop();
	const deleteBusy = useRef(false);
	const [deletePending, setDeletePending] = useState(false);
	const [deleteError, setDeleteError] = useState(false);
	const navigate = useNavigate();
	const terminology = useTerminology();
	const authorization = useAuthorization();
	const [filterOrgId, setFilterOrgId] = useState<string | null | undefined>(
		undefined,
	);
	const [searchTerm, setSearchTerm] = useState("");
	const [viewMode, setViewMode] = useState<"grid" | "table">("grid");
	const [selectionMode, setSelectionMode] = useState(false);
	const [selectedSdkUpdateIds, setSelectedSdkUpdateIds] = useState<
		Set<string>
	>(new Set());
	const [batchUpdatePending, setBatchUpdatePending] = useState(false);
	const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false);
	const [infoDialogSlug, setInfoDialogSlug] = useState<string | null>(null);
	const [selectedApp, setSelectedApp] = useState<{
		id: string;
		name: string;
	} | null>(null);

	// Fetch applications
	const {
		data: applicationsData,
		isLoading,
		isError,
		isFetching,
		refetch,
	} = useApplications(
		filterOrgId === undefined ? undefined : (filterOrgId ?? "global"),
	);
	const applications = applicationsData?.applications ?? [];
	const deleteApplication = useDeleteApplication({ errorToast: false });
	const updateApplicationSdk = useUpdateApplicationSdk();
	const sdkUpdateJobs = useApplicationSdkUpdateJobs();

	// Fetch organizations for name lookup (platform admins only)
	const { data: organizations } = useOrganizations({
		enabled: authorization.canAnywhere("organizations.read"),
	});

	const getOrgName = useOrganizationName(organizations);

	const canManageApps = authorization.canAnywhere("apps.readwrite");
	const canManageApp = (app: ApplicationListItem) =>
		authorization.canAt("apps.readwrite", orgTarget(app.organization_id));

	const handleOpenCode = (app: ApplicationListItem) => {
		navigate(`/apps/${app.slug}/edit`);
	};

	const handleOpenSettings = (app: ApplicationListItem) => {
		setInfoDialogSlug(app.slug);
	};

	const handlePreview = (app: ApplicationListItem) => {
		navigate(`/apps/${app.slug}/preview`);
	};

	const handleLaunch = (app: ApplicationListItem) => {
		navigate(`/apps/${app.slug}`);
	};

	const handleDelete = (app: ApplicationListItem) => {
		setSelectedApp({ id: app.id, name: app.name });
		setDeleteError(false);
		setIsDeleteDialogOpen(true);
	};

	const handleUpdateSdk = async (app: ApplicationListItem) => {
		try {
			const operation = await updateApplicationSdk.mutateAsync({
				params: { path: { app_id: app.id } },
			});
			sdkUpdateJobs.trackAccepted([
				{ ...operation, application_id: app.id },
			]);
		} catch {
			// useUpdateApplicationSdk owns the user-facing error toast.
		}
	};

	// Filter and search applications
	const filteredApps = useSearch(applications || [], searchTerm, [
		"name",
		"description",
		"slug",
		(app) => app.id,
	]);

	const actionableApps = (applications as ApplicationListItem[]).filter(
		(app) =>
			canManageApp(app) &&
			canUpdateApplicationSdk(app, sdkUpdateJobs.getUpdateState(app.id)),
	);
	const visibleActionableApps = (
		filteredApps as ApplicationListItem[]
	).filter(
		(app) =>
			canManageApp(app) &&
			canUpdateApplicationSdk(app, sdkUpdateJobs.getUpdateState(app.id)),
	);
	const selectedActionableApps = actionableApps.filter((app) =>
		selectedSdkUpdateIds.has(app.id),
	);
	const actionableCount = actionableApps.length;
	const hiddenActionableCount = Math.max(
		actionableCount - visibleActionableApps.length,
		0,
	);
	const selectedCount = selectedActionableApps.length;
	const hasSearch = searchTerm.trim().length > 0;
	const updateAllSdkDescription =
		hasSearch && hiddenActionableCount > 0
			? `Includes all actionable ${term(terminology, "app", "formalPlural")} in the current organization scope, including ${hiddenActionableCount} hidden by search.`
			: undefined;

	const toggleSdkUpdateSelection = (app: ApplicationListItem) => {
		if (
			!canManageApp(app) ||
			!canUpdateApplicationSdk(app, sdkUpdateJobs.getUpdateState(app.id))
		) {
			return;
		}
		setSelectedSdkUpdateIds((current) => {
			const next = new Set(current);
			if (next.has(app.id)) next.delete(app.id);
			else next.add(app.id);
			return next;
		});
	};

	const toggleSelectAllVisible = () => {
		setSelectedSdkUpdateIds((current) => {
			const next = new Set(current);
			const allVisibleSelected =
				visibleActionableApps.length > 0 &&
				visibleActionableApps.every((app) => next.has(app.id));
			for (const app of visibleActionableApps) {
				if (allVisibleSelected) next.delete(app.id);
				else next.add(app.id);
			}
			return next;
		});
	};

	const handleDoneSelecting = () => {
		setSelectionMode(false);
	};

	const reportBatchResult = (
		result: components["schemas"]["ApplicationSdkUpdateBatchResponse"],
	) => {
		const accepted = result.accepted ?? [];
		const skipped = result.skipped ?? [];
		sdkUpdateJobs.trackAccepted(accepted);
		const appWord = accepted.length === 1 ? "App" : "Apps";
		const skippedSuffix =
			skipped.length > 0 ? ` ${skipped.length} skipped.` : "";
		if (accepted.length === 0 && skipped.length > 0) {
			toast.warning(
				`No SDK updates were queued. ${skipped.length} skipped.`,
			);
			return;
		}
		toast.success(
			`Queued SDK updates for ${accepted.length} ${appWord}.${skippedSuffix}`,
		);
	};

	const handleBatchUpdate = async (
		appsToUpdate: ApplicationListItem[],
		options: { clearSelection: boolean },
	) => {
		if (appsToUpdate.length === 0 || batchUpdatePending) return;
		setBatchUpdatePending(true);
		try {
			const result = await batchUpdateApplicationSdks(
				appsToUpdate.map((app) => app.id),
			);
			reportBatchResult(result);
			if (options.clearSelection) {
				setSelectedSdkUpdateIds(new Set());
				setSelectionMode(false);
			}
		} catch {
			toast.error("Failed to queue SDK updates");
		} finally {
			setBatchUpdatePending(false);
		}
	};

	const handleConfirmDelete = async () => {
		if (!selectedApp || deleteBusy.current) return;
		deleteBusy.current = true;
		setDeletePending(true);
		setDeleteError(false);
		try {
			await deleteApplication.mutateAsync({
				params: { path: { app_id: selectedApp.id } },
			});
			setIsDeleteDialogOpen(false);
			setSelectedApp(null);
		} catch {
			setDeleteError(true);
		} finally {
			deleteBusy.current = false;
			setDeletePending(false);
		}
	};

	return (
		<PageWorkspace className="max-w-7xl mx-auto">
			<ListPageHeader
				title={term(terminology, "app", "formalPlural")}
				description={
					canManageApps
						? `Build and manage custom ${term(terminology, "app", "formalPluralLower")}`
						: `Access your custom ${term(terminology, "app", "formalPluralLower")}`
				}
				actions={
					<>
						{canManageApps && isDesktop && (
							<ToggleGroup
								aria-label="List layout"
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
									size="sm"
									className="size-11 sm:size-9"
								>
									<LayoutGrid className="h-4 w-4" />
								</ToggleGroupItem>
								<ToggleGroupItem
									value="table"
									aria-label="Table view"
									size="sm"
									className="size-11 sm:size-9"
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
							aria-label="Refresh applications"
							className="size-11 sm:size-9"
						>
							<RefreshCw className="h-4 w-4" />
						</Button>
					</>
				}
			/>

			{/* Search and Filters */}
			<ListToolbar>
				<SearchBox
					value={searchTerm}
					onChange={setSearchTerm}
					aria-label={`Search ${term(terminology, "app", "formalPluralLower")}`}
					placeholder={`Search ${term(terminology, "app", "formalPluralLower")}…`}
					className="flex-1"
				/>
				{authorization.canAnywhere("organizations.read") && (
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
				{canManageApps && (
					<div className="flex flex-wrap items-center gap-2 sm:ml-auto">
						{selectionMode ? (
							<>
								<span className="text-sm text-muted-foreground">
									{selectedCount} selected
								</span>
								<Button
									type="button"
									variant="outline"
									size="lg"
									onClick={toggleSelectAllVisible}
									disabled={
										visibleActionableApps.length === 0 ||
										batchUpdatePending
									}
								>
									Select all
								</Button>
								<Button
									type="button"
									variant="default"
									size="lg"
									onClick={() =>
										void handleBatchUpdate(
											selectedActionableApps,
											{ clearSelection: true },
										)
									}
									disabled={
										selectedCount === 0 ||
										batchUpdatePending
									}
								>
									{batchUpdatePending
										? "Queueing…"
										: `Update selected (${selectedCount})`}
								</Button>
								<Button
									type="button"
									variant="outline"
									size="lg"
									onClick={handleDoneSelecting}
									disabled={batchUpdatePending}
								>
									Done
								</Button>
							</>
						) : (
							<>
								{actionableCount > 0 && (
									<Button
										type="button"
										variant="default"
										size="lg"
										onClick={() =>
											void handleBatchUpdate(
												actionableApps,
												{ clearSelection: false },
											)
										}
										disabled={batchUpdatePending}
										aria-describedby={
											updateAllSdkDescription
												? updateAllSdkDescriptionId
												: undefined
										}
										aria-description={
											updateAllSdkDescription
										}
										title={updateAllSdkDescription}
									>
										{batchUpdatePending
											? "Queueing…"
											: `Update all SDKs (${actionableCount})`}
									</Button>
								)}
								{updateAllSdkDescription && (
									<span
										id={updateAllSdkDescriptionId}
										className="sr-only"
									>
										{updateAllSdkDescription}
									</span>
								)}
								<Button
									type="button"
									variant="outline"
									size="lg"
									onClick={() => setSelectionMode(true)}
									disabled={
										actionableCount === 0 ||
										batchUpdatePending
									}
								>
									<CheckSquare
										aria-hidden="true"
										className="size-4"
									/>
									Select
								</Button>
							</>
						)}
					</div>
				)}
			</ListToolbar>

			<PageScrollArea
				aria-label={`${term(terminology, "app", "formalPlural")} list`}
				className={
					isDesktop && viewMode === "table"
						? "space-y-4 lg:flex lg:flex-col lg:overflow-hidden"
						: "space-y-4"
				}
			>
				{isError && (
					<div
						role="alert"
						className="flex flex-col items-start gap-3 rounded-[var(--bf-radius-surface)] border border-destructive/30 p-4 sm:flex-row sm:items-center sm:justify-between"
					>
						<p className="text-sm text-destructive">
							Couldn't load{" "}
							{term(terminology, "app", "formalPluralLower")}.
							{applicationsData !== undefined &&
								" Previously loaded records are shown below."}
						</p>
						<Button
							type="button"
							variant="outline"
							className="min-h-11"
							disabled={isFetching}
							onClick={() => void refetch()}
						>
							{isFetching ? "Retrying…" : "Retry loading"}
						</Button>
					</div>
				)}
				{isLoading && (
					<p role="status" className="sr-only">
						Loading {term(terminology, "app", "formalPluralLower")}…
					</p>
				)}
				{(!isError || applicationsData !== undefined) && (
					<ApplicationListSurface
						apps={filteredApps as ApplicationListItem[]}
						viewMode={isDesktop ? viewMode : "grid"}
						isLoading={isLoading}
						isPlatformAdmin={authorization.canAnywhere(
							"organizations.read",
						)}
						canManageApps={canManageApps}
						canManageApp={canManageApp}
						getOrgName={getOrgName}
						onLaunch={handleLaunch}
						onPreview={handlePreview}
						onOpenSettings={handleOpenSettings}
						onOpenCode={handleOpenCode}
						onUpdateSdk={(app) => {
							void handleUpdateSdk(app);
						}}
						onDelete={handleDelete}
						getSdkUpdateState={(app) =>
							sdkUpdateJobs.getUpdateState(app.id)
						}
						selectionMode={selectionMode}
						selectedIds={selectedSdkUpdateIds}
						onToggleSelection={toggleSdkUpdateSelection}
						onToggleSelectAllVisible={toggleSelectAllVisible}
						emptySearchActive={Boolean(searchTerm)}
					/>
				)}
			</PageScrollArea>

			{/* Delete Confirmation Dialog */}
			<AlertDialog
				open={isDeleteDialogOpen}
				onOpenChange={(open) => {
					if (!deleteBusy.current) setIsDeleteDialogOpen(open);
				}}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>
							Delete {term(terminology, "app", "formalSingular")}?
						</AlertDialogTitle>
						<AlertDialogDescription>
							This will permanently delete the{" "}
							{term(terminology, "app", "formalSingularLower")} "
							{selectedApp?.name}" including all versions and
							data. This action cannot be undone.
						</AlertDialogDescription>
					</AlertDialogHeader>
					{deleteError && (
						<p role="alert" className="text-sm text-destructive">
							Couldn't delete this{" "}
							{term(terminology, "app", "formalSingularLower")}.
							Please retry.
						</p>
					)}
					{deletePending && (
						<p role="status" className="sr-only">
							Deleting…
						</p>
					)}
					<AlertDialogFooter>
						<AlertDialogCancel disabled={deletePending}>
							Cancel
						</AlertDialogCancel>
						<AlertDialogAction
							disabled={deletePending}
							onClick={(event) => {
								event.preventDefault();
								void handleConfirmDelete();
							}}
							className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							{deletePending
								? "Deleting..."
								: deleteError
									? "Retry delete"
									: `Delete ${term(terminology, "app", "formalSingular")}`}
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>

			{/* Application settings dialog (opened from card pencil button) */}
			<AppInfoDialog
				appSlug={infoDialogSlug}
				open={infoDialogSlug !== null}
				onOpenChange={(o) => {
					if (!o) setInfoDialogSlug(null);
				}}
			/>
		</PageWorkspace>
	);
}
