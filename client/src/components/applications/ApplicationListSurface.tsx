import {
	AppWindow,
	Building2,
	Code2,
	CircleSlash,
	Eye,
	Globe,
	Pencil,
	PlayCircle,
	RefreshCw,
	Check,
	Trash2,
} from "lucide-react";
import type { MouseEvent } from "react";

import { RecordActionsMenu } from "@/components/common/RecordActionsMenu";
import { ResourceCatalogCard } from "@/components/catalog/ResourceCatalogCard";
import { DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { ResourceIcon } from "@/components/ResourceIcon";
import { PageLoader } from "@/components/PageLoader";
import { SolutionManagedBadge } from "@/components/solutions/SolutionManagedBadge";
import {
	ApplicationSdkStatusBadge,
	canUpdateApplicationSdk,
	type ApplicationSdkUpdateState,
} from "@/components/applications/ApplicationSdkStatusBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import { term, useTerminology } from "@/lib/terminology";
import type { components } from "@/lib/v1";
import { prefetchApplicationDetail } from "@/lib/detail-route-loaders";

export type ApplicationListItem = components["schemas"]["ApplicationPublic"] & {
	app_model?: string | null;
	is_solution_managed?: boolean;
	solution_id?: string | null;
};

export interface ApplicationListSurfaceProps {
	apps: ApplicationListItem[];
	viewMode: "grid" | "table";
	/** Query string to preserve the originating page in native navigation links. */
	navigationSearch?: string;
	isLoading?: boolean;
	isPlatformAdmin: boolean;
	canManageApps: boolean;
	getOrgName: (orgId: string | null | undefined) => string;
	onLaunch: (app: ApplicationListItem) => void;
	onPreview?: (app: ApplicationListItem) => void;
	onOpenSettings?: (app: ApplicationListItem) => void;
	onOpenCode?: (app: ApplicationListItem) => void;
	onUpdateSdk?: (app: ApplicationListItem) => void;
	onDelete?: (app: ApplicationListItem) => void;
	getSdkUpdateState?: (app: ApplicationListItem) => ApplicationSdkUpdateState;
	selectionMode?: boolean;
	selectedIds?: Set<string>;
	onToggleSelection?: (app: ApplicationListItem) => void;
	onToggleSelectAllVisible?: () => void;
	onCreateEmpty?: () => void;
	emptySearchActive?: boolean;
}

function isV2App(app: ApplicationListItem): boolean {
	return app.app_model === "standalone_v2";
}

function canLaunchApp(app: ApplicationListItem): boolean {
	return app.is_published;
}

function getApplicationPrimaryAction(
	app: ApplicationListItem,
	actions: Pick<ApplicationListSurfaceProps, "onLaunch" | "onPreview">,
) {
	if (canLaunchApp(app)) return () => actions.onLaunch(app);
	if (!isV2App(app) && actions.onPreview)
		return () => actions.onPreview?.(app);
	return undefined;
}

function getApplicationPrimaryHref(
	app: ApplicationListItem,
	navigationSearch: string,
) {
	if (canLaunchApp(app)) return `/apps/${app.slug}${navigationSearch}`;
	if (!isV2App(app)) return `/apps/${app.slug}/preview${navigationSearch}`;
	return undefined;
}

function isModifiedOpenEvent(
	event: Pick<
		MouseEvent<HTMLElement>,
		"button" | "ctrlKey" | "metaKey" | "shiftKey" | "altKey"
	>,
) {
	return (
		event.button !== 0 ||
		event.ctrlKey ||
		event.metaKey ||
		event.shiftKey ||
		event.altKey
	);
}

function ApplicationActions({
	app,
	onLaunch,
	onPreview,
	onOpenSettings,
	onOpenCode,
	onUpdateSdk,
	onDelete,
	updateState = "idle",
}: { app: ApplicationListItem } & Pick<
	ApplicationListSurfaceProps,
	| "onLaunch"
	| "onPreview"
	| "onOpenSettings"
	| "onOpenCode"
	| "onUpdateSdk"
	| "onDelete"
> & { updateState?: ApplicationSdkUpdateState }) {
	const showPublished = canLaunchApp(app);
	const showPreview =
		!isV2App(app) && app.has_unpublished_changes && onPreview;
	const sdkUpdateAllowed = canUpdateApplicationSdk(app, updateState);
	const sdkStatusNeedsMenu =
		updateState === "failed" ||
		updateState === "queued" ||
		updateState === "updating" ||
		app.sdk_status === "update_available" ||
		app.sdk_status === "update_required" ||
		app.sdk_status === "unknown";
	if (
		!showPublished &&
		!showPreview &&
		!onOpenSettings &&
		(!onOpenCode || isV2App(app)) &&
		!sdkStatusNeedsMenu &&
		!onDelete
	)
		return null;
	return (
		<RecordActionsMenu label={`${app.name} actions`}>
			{showPublished && (
				<DropdownMenuItem
					className="min-h-11"
					onSelect={() => onLaunch(app)}
				>
					<PlayCircle aria-hidden="true" className="size-4" />
					Open Published
				</DropdownMenuItem>
			)}
			{showPreview && (
				<DropdownMenuItem
					className="min-h-11"
					onSelect={() => onPreview?.(app)}
				>
					<Eye aria-hidden="true" className="size-4" />
					Preview
				</DropdownMenuItem>
			)}
			{onOpenSettings && (
				<DropdownMenuItem
					className="min-h-11"
					onSelect={() => onOpenSettings(app)}
				>
					<Pencil aria-hidden="true" className="size-4" />
					Edit
				</DropdownMenuItem>
			)}
			{!isV2App(app) && onOpenCode && (
				<DropdownMenuItem
					className="min-h-11"
					onSelect={() => onOpenCode(app)}
				>
					<Code2 aria-hidden="true" className="size-4" />
					Code editor
				</DropdownMenuItem>
			)}
			{sdkStatusNeedsMenu && onUpdateSdk && (
				<DropdownMenuItem
					className="min-h-11"
					disabled={!sdkUpdateAllowed}
					onSelect={() => {
						if (sdkUpdateAllowed) onUpdateSdk(app);
					}}
				>
					{app.sdk_source_available ? (
						<RefreshCw aria-hidden="true" className="size-4" />
					) : (
						<CircleSlash aria-hidden="true" className="size-4" />
					)}
					{!app.sdk_source_available
						? "Source unavailable — cannot rebuild SDK"
						: updateState === "queued"
							? "SDK update queued…"
							: updateState === "updating"
								? "Updating SDK…"
								: updateState === "failed"
									? "Retry SDK update"
									: app.sdk_status === "unknown"
										? "Rebuild SDK"
										: "Update SDK"}
				</DropdownMenuItem>
			)}
			{onDelete && (
				<DropdownMenuItem
					variant="destructive"
					className="min-h-11"
					onSelect={() => onDelete(app)}
				>
					<Trash2 aria-hidden="true" className="size-4" />
					Delete
				</DropdownMenuItem>
			)}
		</RecordActionsMenu>
	);
}

export function ApplicationListSurface({
	apps,
	viewMode,
	navigationSearch = "",
	isLoading = false,
	isPlatformAdmin,
	canManageApps,
	getOrgName,
	onLaunch,
	onPreview,
	onOpenSettings,
	onOpenCode,
	onUpdateSdk,
	onDelete,
	getSdkUpdateState,
	selectionMode = false,
	selectedIds = new Set(),
	onToggleSelection,
	onToggleSelectAllVisible,
	onCreateEmpty,
	emptySearchActive = false,
}: ApplicationListSurfaceProps) {
	const terminology = useTerminology();
	const isSelectable = (app: ApplicationListItem) =>
		canUpdateApplicationSdk(app, getSdkUpdateState?.(app) ?? "idle");
	const selectableApps = apps.filter(isSelectable);
	const allVisibleSelected =
		selectableApps.length > 0 &&
		selectableApps.every((app) => selectedIds.has(app.id));
	const someVisibleSelected = selectableApps.some((app) =>
		selectedIds.has(app.id),
	);
	const renderSdkBadge = (app: ApplicationListItem, showCurrent = false) => (
		<ApplicationSdkStatusBadge
			status={app.sdk_status}
			showCurrent={showCurrent}
			updateState={getSdkUpdateState?.(app) ?? "idle"}
		/>
	);
	const renderSourceUnavailableBadge = (app: ApplicationListItem) => {
		if (
			app.sdk_source_available ||
			!(
				app.sdk_status === "update_available" ||
				app.sdk_status === "update_required" ||
				app.sdk_status === "unknown"
			)
		) {
			return null;
		}
		return (
			<Badge
				variant="outline"
				aria-label="Source unavailable"
				className="gap-1 border-muted-foreground/30 bg-muted text-xs text-muted-foreground"
			>
				<CircleSlash aria-hidden="true" className="h-3 w-3" />
				Source unavailable
			</Badge>
		);
	};
	const renderName = (app: ApplicationListItem) => {
		const open = getApplicationPrimaryAction(app, { onLaunch, onPreview });
		const href = open
			? getApplicationPrimaryHref(app, navigationSearch)
			: undefined;
		if (!href) {
			return (
				<span className="min-h-11 min-w-0 text-left font-semibold [overflow-wrap:anywhere]">
					{app.name}
				</span>
			);
		}
		return (
			<a
				href={href}
				onClick={(event) => {
					event.stopPropagation();
					if (isModifiedOpenEvent(event)) return;
					event.preventDefault();
					open?.();
				}}
				className="min-h-11 min-w-0 text-left font-semibold [overflow-wrap:anywhere] hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
			>
				{app.name}
			</a>
		);
	};

	if (isLoading) {
		return <PageLoader message="Loading applications…" size="sm" />;
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
							{selectionMode && (
								<DataTableHead className="w-12">
									<Checkbox
										aria-label={`Select all visible ${term(terminology, "app", "formalPlural")} with SDK updates`}
										checked={
											allVisibleSelected
												? true
												: someVisibleSelected
													? "indeterminate"
													: false
										}
										disabled={selectableApps.length === 0}
										onCheckedChange={() =>
											onToggleSelectAllVisible?.()
										}
									/>
								</DataTableHead>
							)}
							{isPlatformAdmin && (
								<DataTableHead className="w-0 whitespace-nowrap">
									Organization
								</DataTableHead>
							)}
							<DataTableHead>Name</DataTableHead>
							<DataTableHead>Description</DataTableHead>
							<DataTableHead className="w-0 whitespace-nowrap">
								Status
							</DataTableHead>
							<DataTableHead className="w-0 whitespace-nowrap text-right" />
						</DataTableRow>
					</DataTableHeader>
					<DataTableBody>
						{apps.map((app) => {
							const selectable = isSelectable(app);
							const opensPreview =
								!isV2App(app) &&
								!canLaunchApp(app) &&
								Boolean(onPreview);
							const updateState =
								getSdkUpdateState?.(app) ?? "idle";
							const sdkStatusNeedsMenu =
								updateState === "failed" ||
								updateState === "updating" ||
								app.sdk_status === "update_available" ||
								app.sdk_status === "update_required" ||
								app.sdk_status === "unknown";
							const open = getApplicationPrimaryAction(app, {
								onLaunch,
								onPreview,
							});
							const href = open
								? getApplicationPrimaryHref(
										app,
										navigationSearch,
									)
								: undefined;
							return (
								<DataTableRow
									key={app.id}
									clickable={!selectionMode && Boolean(open)}
									onClick={selectionMode ? undefined : open}
									href={selectionMode ? undefined : href}
									onPointerEnter={() =>
										prefetchApplicationDetail(
											app,
											opensPreview,
										)
									}
									onFocus={() =>
										prefetchApplicationDetail(
											app,
											opensPreview,
										)
									}
								>
									{selectionMode && (
										<DataTableCell
											onClick={(event) =>
												event.stopPropagation()
											}
										>
											<Checkbox
												aria-label={`Select ${app.name}`}
												checked={selectedIds.has(
													app.id,
												)}
												disabled={!selectable}
												onCheckedChange={() => {
													if (selectable) {
														onToggleSelection?.(
															app,
														);
													}
												}}
											/>
										</DataTableCell>
									)}
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
									<DataTableCell className="min-w-0 font-medium">
										<div className="flex min-w-0 flex-1 items-center gap-2">
											<ResourceIcon
												kind="app"
												id={app.id}
												logo={app.logo_url ?? null}
												size="table"
											/>
											{renderName(app)}
										</div>
									</DataTableCell>
									<DataTableCell className="max-w-xs truncate text-muted-foreground">
										{app.description || (
											<span className="italic">
												No description
											</span>
										)}
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap">
										<div className="flex flex-wrap gap-1">
											{app.is_published && (
												<Badge
													variant="outline"
													className="border-[var(--bf-success)]/20 bg-[var(--bf-success)]/10 text-[var(--bf-success)] text-xs"
												>
													{isV2App(app)
														? "Deployed"
														: "Published"}
												</Badge>
											)}
											{app.has_unpublished_changes && (
												<Badge
													variant="outline"
													className="border-[var(--bf-warning)]/20 bg-[var(--bf-warning)]/10 text-[var(--bf-warning)] text-xs"
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
														{isV2App(app)
															? "Not deployed"
															: "Empty"}
													</Badge>
												)}
											{renderSdkBadge(app, true)}
											{renderSourceUnavailableBadge(app)}
										</div>
									</DataTableCell>
									<DataTableCell
										className="w-0 whitespace-nowrap text-right"
										onClick={(event) =>
											event.stopPropagation()
										}
									>
										<div className="flex justify-end gap-1">
											{app.is_solution_managed && (
												<SolutionManagedBadge
													solutionId={app.solution_id}
												/>
											)}
											{canManageApps &&
												(!app.is_solution_managed ||
													sdkStatusNeedsMenu) && (
													<ApplicationActions
														app={app}
														onLaunch={onLaunch}
														onPreview={onPreview}
														onOpenSettings={
															app.is_solution_managed
																? undefined
																: onOpenSettings
														}
														onOpenCode={
															app.is_solution_managed
																? undefined
																: onOpenCode
														}
														onUpdateSdk={
															onUpdateSdk
														}
														onDelete={
															app.is_solution_managed
																? undefined
																: onDelete
														}
														updateState={
															updateState
														}
													/>
												)}
										</div>
									</DataTableCell>
								</DataTableRow>
							);
						})}
					</DataTableBody>
				</DataTable>
			</div>
		);
	}

	return (
		<div className="grid grid-cols-1 gap-4 sm:grid-cols-[repeat(auto-fill,minmax(min(100%,320px),1fr))]">
			{apps.map((app) => {
				const selectable = isSelectable(app);
				const selected = selectedIds.has(app.id);
				const opensPreview =
					!isV2App(app) && !canLaunchApp(app) && Boolean(onPreview);
				const defaultTarget = getApplicationPrimaryAction(app, {
					onLaunch,
					onPreview,
				});
				const href = defaultTarget
					? getApplicationPrimaryHref(app, navigationSearch)
					: undefined;
				const orgLabel = isPlatformAdmin
					? app.organization_id
						? getOrgName(app.organization_id)
						: "Global"
					: null;
				const card = (
					<div
						key={app.id}
						onPointerEnter={() =>
							prefetchApplicationDetail(app, opensPreview)
						}
						onFocus={() =>
							prefetchApplicationDetail(app, opensPreview)
						}
					>
						<ResourceCatalogCard
							icon={
								<ResourceIcon
									kind="app"
									id={app.id}
									logo={app.logo_url ?? null}
									size="card"
									className="border-sky-500/20 bg-sky-500/10 text-sky-700 dark:text-sky-300 [&_svg]:text-current"
								/>
							}
							title={app.name}
							href={href}
							subtitle={
								<>
									{term(terminology, "app", "formalSingular")}
									<span> · </span>
									{isV2App(app) ? "Code app" : "Legacy app"}
								</>
							}
							description={
								app.description || (
									<span className="italic text-muted-foreground/60">
										No description
									</span>
								)
							}
							action={
								selectionMode ? undefined : (
									<div className="flex items-center gap-1">
										{app.is_solution_managed ? (
											<SolutionManagedBadge
												solutionId={app.solution_id}
											/>
										) : null}
										{canManageApps ? (
											<ApplicationActions
												app={app}
												onLaunch={onLaunch}
												onPreview={onPreview}
												onOpenSettings={
													app.is_solution_managed
														? undefined
														: onOpenSettings
												}
												onOpenCode={
													app.is_solution_managed
														? undefined
														: onOpenCode
												}
												onUpdateSdk={onUpdateSdk}
												onDelete={
													app.is_solution_managed
														? undefined
														: onDelete
												}
												updateState={
													getSdkUpdateState?.(app) ??
													"idle"
												}
											/>
										) : null}
									</div>
								)
							}
							footer={
								orgLabel ? (
									<p className="flex min-w-0 items-center gap-2">
										{app.organization_id ? (
											<Building2 className="size-3.5 shrink-0" />
										) : (
											<Globe className="size-3.5 shrink-0" />
										)}
										<span className="truncate">
											{orgLabel}
										</span>
									</p>
								) : undefined
							}
							onOpen={() => defaultTarget?.()}
							disabled={selectionMode || !defaultTarget}
							titleInteractive={!selectionMode}
						>
							<div className="flex min-w-0 flex-wrap items-center gap-1.5">
								{app.is_published && (
									<Badge
										variant="outline"
										className="border-[var(--bf-success)]/20 bg-[var(--bf-success)]/10 px-1.5 py-0 text-xs text-[var(--bf-success)]"
									>
										{isV2App(app)
											? "Deployed"
											: "Published"}
									</Badge>
								)}
								{app.has_unpublished_changes && (
									<Badge
										variant="outline"
										className="border-[var(--bf-warning)]/20 bg-[var(--bf-warning)]/10 px-1.5 py-0 text-xs text-[var(--bf-warning)]"
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
											{isV2App(app)
												? "Not deployed"
												: "Empty"}
										</Badge>
									)}
								{renderSdkBadge(app)}
								{renderSourceUnavailableBadge(app)}
							</div>
						</ResourceCatalogCard>
					</div>
				);
				if (!selectionMode) return card;
				const toggle = () => {
					if (selectable) onToggleSelection?.(app);
				};
				return (
					<article
						key={app.id}
						role={selectable ? "button" : undefined}
						tabIndex={selectable ? 0 : undefined}
						aria-label={app.name}
						aria-pressed={selectable ? selected : undefined}
						aria-disabled={!selectable}
						onClick={toggle}
						onKeyDown={(event) => {
							if (event.key === "Enter" || event.key === " ") {
								event.preventDefault();
								toggle();
							}
						}}
						className={
							selectable
								? "relative cursor-pointer rounded-[var(--bf-radius-surface)] outline-none ring-offset-background focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
								: "relative rounded-[var(--bf-radius-surface)] opacity-70"
						}
					>
						{selected && (
							<span
								aria-label={`${app.name} selected`}
								className="absolute right-3 top-3 z-10 grid size-6 place-items-center rounded-full bg-primary text-primary-foreground shadow-sm"
							>
								<Check aria-hidden="true" className="size-4" />
							</span>
						)}
						{card}
					</article>
				);
			})}
		</div>
	);
}
