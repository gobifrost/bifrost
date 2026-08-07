import {
	AppWindow,
	Building2,
	Code2,
	Eye,
	Globe,
	Pencil,
	PlayCircle,
	Trash2,
} from "lucide-react";

import { EntityLogo } from "@/components/EntityLogo";
import { SolutionManagedBadge } from "@/components/solutions/SolutionManagedBadge";
import { Badge } from "@/components/ui/badge";
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
import { Skeleton } from "@/components/ui/skeleton";
import { term, useTerminology } from "@/lib/terminology";
import type { components } from "@/lib/v1";

export type ApplicationListItem = components["schemas"]["ApplicationPublic"] & {
	app_model?: string | null;
	is_solution_managed?: boolean;
	solution_id?: string | null;
};

export interface ApplicationListSurfaceProps {
	apps: ApplicationListItem[];
	viewMode: "grid" | "table";
	isLoading?: boolean;
	isPlatformAdmin: boolean;
	canManageApps: boolean;
	getOrgName: (orgId: string | null | undefined) => string;
	onLaunch: (app: ApplicationListItem) => void;
	onPreview?: (app: ApplicationListItem) => void;
	onOpenSettings?: (app: ApplicationListItem) => void;
	onOpenCode?: (app: ApplicationListItem) => void;
	onDelete?: (app: ApplicationListItem) => void;
	onCreateEmpty?: () => void;
	emptySearchActive?: boolean;
}

function isV2App(app: ApplicationListItem): boolean {
	return app.app_model === "standalone_v2";
}

function canLaunchApp(app: ApplicationListItem): boolean {
	return app.is_published || isV2App(app);
}

export function ApplicationListSurface({
	apps,
	viewMode,
	isLoading = false,
	isPlatformAdmin,
	canManageApps,
	getOrgName,
	onLaunch,
	onPreview,
	onOpenSettings,
	onOpenCode,
	onDelete,
	onCreateEmpty,
	emptySearchActive = false,
}: ApplicationListSurfaceProps) {
	const terminology = useTerminology();

	if (isLoading) {
		return viewMode === "grid" || !canManageApps ? (
			<div className="grid grid-cols-1 gap-4 sm:grid-cols-[repeat(auto-fill,minmax(320px,1fr))]">
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
		);
	}

	if (apps.length === 0) {
		return (
			<Card>
				<CardContent className="flex flex-col items-center justify-center py-12 text-center">
					<AppWindow className="h-12 w-12 text-muted-foreground" />
					<h3 className="mt-4 text-lg font-semibold">
						{emptySearchActive
							? `No ${term(terminology, "app", "formalPluralLower")} match your search`
							: `No ${term(terminology, "app", "formalPluralLower")} found`}
					</h3>
					<p className="mt-2 text-sm text-muted-foreground">
							{emptySearchActive
								? "Try adjusting your search term or clear the filter"
								: `No ${term(terminology, "app", "formalPluralLower")} are currently available`}
						</p>
					{canManageApps && !emptySearchActive && onCreateEmpty && (
						<Button
							variant="outline"
							size="icon"
							onClick={onCreateEmpty}
							className="mt-4"
							title={`Create ${term(terminology, "app", "formalSingular")}`}
						>
							<AppWindow className="h-4 w-4" />
						</Button>
					)}
				</CardContent>
			</Card>
		);
	}

	if (viewMode === "table" && canManageApps) {
		return (
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
						{apps.map((app) => (
							<DataTableRow key={app.id}>
								{isPlatformAdmin && (
									<DataTableCell className="w-0 whitespace-nowrap">
										{app.organization_id ? (
											<Badge variant="outline" className="text-xs">
												<Building2 className="mr-1 h-3 w-3" />
												{getOrgName(app.organization_id)}
											</Badge>
										) : (
											<Badge variant="default" className="text-xs">
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
										<span className="italic">No description</span>
									)}
								</DataTableCell>
								<DataTableCell className="w-0 whitespace-nowrap">
									<div className="flex gap-1">
										{app.is_published && (
											<Badge variant="default" className="text-xs">
												Published
											</Badge>
										)}
										{app.has_unpublished_changes && (
											<Badge variant="outline" className="text-xs">
												Draft
											</Badge>
										)}
										{!app.is_published && !app.has_unpublished_changes && (
											<Badge variant="secondary" className="text-xs">
												{isV2App(app) ? "V2" : "Empty"}
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
											onClick={() => onLaunch(app)}
											disabled={!canLaunchApp(app)}
											title={
												!canLaunchApp(app)
													? "No published version"
													: `Open ${term(terminology, "app", "formalSingularLower")}`
											}
										>
											<PlayCircle className="h-4 w-4" />
										</Button>
										{canManageApps &&
											!isV2App(app) &&
											app.has_unpublished_changes &&
											onPreview && (
												<Button
													variant="ghost"
													size="sm"
													onClick={() => onPreview(app)}
													title="Preview draft"
												>
													<Eye className="h-4 w-4" />
												</Button>
											)}
										{app.is_solution_managed && (
											<SolutionManagedBadge solutionId={app.solution_id} />
										)}
										{canManageApps && !app.is_solution_managed && (
											<>
												{onOpenSettings && (
													<Button
														variant="ghost"
														size="sm"
														onClick={() => onOpenSettings(app)}
														title="Settings"
													>
														<Pencil className="h-4 w-4" />
													</Button>
												)}
												{onOpenCode && (
													<Button
														variant="ghost"
														size="sm"
														onClick={() => onOpenCode(app)}
														title="Code editor"
													>
														<Code2 className="h-4 w-4" />
													</Button>
												)}
												{onDelete && (
													<Button
														variant="ghost"
														size="sm"
														onClick={() => onDelete(app)}
														title={`Delete ${term(terminology, "app", "formalSingularLower")}`}
													>
														<Trash2 className="h-4 w-4" />
													</Button>
												)}
											</>
										)}
									</div>
								</DataTableCell>
							</DataTableRow>
						))}
					</DataTableBody>
				</DataTable>
			</div>
		);
	}

	return (
		<div className="grid grid-cols-1 gap-4 sm:grid-cols-[repeat(auto-fill,minmax(320px,1fr))]">
			{apps.map((app) => {
				const defaultTarget = canLaunchApp(app)
					? () => onLaunch(app)
					: onPreview
						? () => onPreview(app)
						: undefined;
				const orgLabel = isPlatformAdmin
					? app.organization_id
						? getOrgName(app.organization_id)
						: "Global"
					: null;
				return (
					<div
						key={app.id}
						role="button"
						tabIndex={0}
						onClick={defaultTarget}
						onKeyDown={(e) => {
							if (defaultTarget && (e.key === "Enter" || e.key === " ")) {
								e.preventDefault();
								defaultTarget();
							}
						}}
						className="group relative flex cursor-pointer flex-col overflow-hidden rounded-2xl bg-card shadow-sm ring-1 ring-foreground/5 transition-all hover:-translate-y-px hover:ring-foreground/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring dark:ring-foreground/10 dark:hover:ring-foreground/15"
					>
						<div className="border-b px-4 py-3">
							<div className="flex items-start justify-between gap-3">
								<div className="flex min-w-0 items-center gap-2">
									<EntityLogo
										entityType="app"
										entityId={app.id}
										logo={app.logo ?? null}
										fallback={
											<AppWindow className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
										}
										size={20}
										className="h-5 w-5 rounded object-cover shrink-0"
									/>
									<span className="truncate text-[14.5px] font-semibold">
										{app.name}
									</span>
								</div>
								{app.is_solution_managed ? (
									<SolutionManagedBadge solutionId={app.solution_id} />
								) : canManageApps ? (
									<div className="flex shrink-0 gap-1">
										{onOpenSettings && (
											<Button
												type="button"
												variant="ghost"
												size="icon"
												className="h-6 w-6"
												onClick={(e) => {
													e.stopPropagation();
													onOpenSettings(app);
												}}
												title="Settings"
												aria-label="Settings"
											>
												<Pencil className="h-3.5 w-3.5" />
											</Button>
										)}
										{onOpenCode && (
											<Button
												type="button"
												variant="ghost"
												size="icon"
												className="h-6 w-6"
												onClick={(e) => {
													e.stopPropagation();
													onOpenCode(app);
												}}
												title="Code editor"
												aria-label="Code editor"
											>
												<Code2 className="h-3.5 w-3.5" />
											</Button>
										)}
									</div>
								) : null}
							</div>
						</div>

						<div className="relative flex-1 px-4 py-3 min-h-[72px]">
							{app.description ? (
								<p className="line-clamp-2 text-[13px] text-muted-foreground">
									{app.description}
								</p>
							) : (
								<p className="text-[13px] italic text-muted-foreground/50">
									No description
								</p>
							)}

							{!isV2App(app) && (
								<div className="pointer-events-none absolute inset-0 flex flex-col items-start justify-center gap-1.5 bg-background/85 px-4 opacity-0 backdrop-blur-sm transition-opacity group-hover:opacity-100">
									{app.is_published && (
										<button
											type="button"
											className="pointer-events-auto text-left text-[13px] font-medium text-foreground hover:text-primary"
											onClick={(e) => {
												e.stopPropagation();
												onLaunch(app);
											}}
										>
											<PlayCircle className="-mt-0.5 mr-1.5 inline h-3.5 w-3.5" />
											Open Published
										</button>
									)}
									{canManageApps && app.has_unpublished_changes && onPreview && (
										<button
											type="button"
											className="pointer-events-auto text-left text-[13px] font-medium text-foreground hover:text-primary"
											onClick={(e) => {
												e.stopPropagation();
												onPreview(app);
											}}
										>
											<Eye className="-mt-0.5 mr-1.5 inline h-3.5 w-3.5" />
											Open Preview
										</button>
									)}
								</div>
							)}
						</div>

						<div className="flex items-center justify-between gap-2 border-t px-4 py-2.5">
							<div className="flex items-center gap-1.5">
								{app.is_published && (
									<Badge variant="default" className="text-[10px] px-1.5 py-0">
										Published
									</Badge>
								)}
								{app.has_unpublished_changes && (
									<Badge variant="outline" className="text-[10px] px-1.5 py-0">
										Draft
									</Badge>
								)}
								{!app.is_published && !app.has_unpublished_changes && (
									<span className="text-[11px] text-muted-foreground">
										{isV2App(app) ? "V2" : "Empty"}
									</span>
								)}
							</div>
							{orgLabel ? (
								<Badge
									variant={app.organization_id ? "outline" : "default"}
									className="text-[10px] px-1.5 py-0"
								>
									{app.organization_id ? (
										<Building2 className="mr-1 h-3 w-3" />
									) : (
										<Globe className="mr-1 h-3 w-3" />
									)}
									{orgLabel}
								</Badge>
							) : null}
						</div>
					</div>
				);
			})}
		</div>
	);
}
