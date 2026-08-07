/**
 * Applications Page
 *
 * Lists all App Builder applications with management capabilities.
 */

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
	RefreshCw,
	LayoutGrid,
	Table as TableIcon,
} from "lucide-react";
import { AppInfoDialog } from "@/components/app-builder/AppInfoDialog";
import {
	ApplicationListSurface,
	type ApplicationListItem,
} from "@/components/applications/ApplicationListSurface";
import { Button } from "@/components/ui/button";
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
import { useApplications, useDeleteApplication } from "@/hooks/useApplications";
import { useAuth } from "@/contexts/AuthContext";
import { useOrganizations } from "@/hooks/useOrganizations";
import { SearchBox } from "@/components/search/SearchBox";
import { useSearch } from "@/hooks/useSearch";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { term, useTerminology } from "@/lib/terminology";
import type { components } from "@/lib/v1";

type Organization = components["schemas"]["OrganizationPublic"];

export function Applications() {
	const navigate = useNavigate();
	const terminology = useTerminology();
	const { isPlatformAdmin } = useAuth();
	const [filterOrgId, setFilterOrgId] = useState<string | null | undefined>(
		undefined,
	);
	const [searchTerm, setSearchTerm] = useState("");
	const [viewMode, setViewMode] = useState<"grid" | "table">("grid");
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
			<div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
				<div>
					<h1 className="text-3xl font-extrabold tracking-tight sm:text-4xl">
						{term(terminology, "app", "formalPlural")}
					</h1>
					<p className="mt-2 text-muted-foreground">
						{canManageApps
							? `Build and manage custom ${term(terminology, "app", "formalPluralLower")}`
							: `Access your custom ${term(terminology, "app", "formalPluralLower")}`}
					</p>
				</div>
				<div className="flex flex-wrap gap-2">
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
					</div>
				</div>

			{/* Search and Filters */}
			<div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:gap-4">
				<SearchBox
					value={searchTerm}
					onChange={setSearchTerm}
					placeholder={`Search ${term(terminology, "app", "formalPluralLower")} by name, description, or slug...`}
					className="flex-1"
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
			</div>

			<div className="flex-1 min-h-0 overflow-auto">
				<ApplicationListSurface
					apps={filteredApps as ApplicationListItem[]}
					viewMode={viewMode}
					isLoading={isLoading}
					isPlatformAdmin={isPlatformAdmin}
					canManageApps={canManageApps}
					getOrgName={getOrgName}
					onLaunch={handleLaunch}
					onPreview={handlePreview}
					onOpenSettings={handleOpenSettings}
					onOpenCode={handleOpenCode}
					onDelete={handleDelete}
					emptySearchActive={Boolean(searchTerm)}
				/>
			</div>

			{/* Delete Confirmation Dialog */}
			<AlertDialog
				open={isDeleteDialogOpen}
				onOpenChange={setIsDeleteDialogOpen}
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
					<AlertDialogFooter>
						<AlertDialogCancel>Cancel</AlertDialogCancel>
						<AlertDialogAction
							onClick={handleConfirmDelete}
							className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
						>
							{deleteApplication.isPending
								? "Deleting..."
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
		</div>
	);
}
