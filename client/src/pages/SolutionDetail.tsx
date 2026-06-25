/**
 * Solution Detail Page
 *
 * RoleDetail-style tabbed view for a single Solution install: breadcrumb,
 * header with scope/source chips + Edit/Delete actions, a required-config
 * warning banner, and per-entity tabs (Workflows / Apps / Forms / Agents /
 * Tables / Configs). The Configs tab doubles as the config-value entry
 * surface — required inputs an install needs before it can run.
 */

import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
	ChevronLeft,
	Globe,
	Building2,
	GitBranch,
	HardDriveUpload,
	Workflow,
	AppWindow,
	FileCode,
	FileText,
	FolderOpen,
	Bot,
	Database,
	SlidersHorizontal,
	KeyRound,
	CheckCircle2,
	Circle,
	AlertTriangle,
	Loader2,
	Upload,
	LayoutGrid,
	Table as TableIcon,
	PlayCircle,
	Code2,
	Shield,
	Users,
	Unlink,
	ArrowUp,
	RefreshCw,
	PowerOff,
	Trash2,
	RotateCcw,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { SearchBox } from "@/components/search/SearchBox";
import { EntityLogo } from "@/components/EntityLogo";
import {
	ApplicationListSurface,
	type ApplicationListItem,
} from "@/components/applications/ApplicationListSurface";
import {
	FormListSurface,
	type FormListItem,
	type FormValidationState,
} from "@/components/forms/FormListSurface";
import {
	WorkflowListSurface,
	type WorkflowListItem,
} from "@/components/workflows/WorkflowListSurface";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import {
	DataTable,
	DataTableBody,
	DataTableCell,
	DataTableHead,
	DataTableHeader,
	DataTableRow,
} from "@/components/ui/data-table";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
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
import { useOrganizations } from "@/hooks/useOrganizations";
import { CreateEditSolution } from "@/components/solutions/CreateEditSolution";
import { SolutionCaptureDialog } from "@/components/solutions/SolutionCaptureDialog";
import { SolutionActionsMenu } from "@/components/solutions/SolutionActionsMenu";
import { ExportSolutionDialog } from "@/components/solutions/ExportSolutionDialog";
import {
	getSolutionEntities,
	getSolutionSetup,
	getSolutionReadme,
	deleteSolution,
	uninstallSolution,
	getSolutionDeletionSummary,
	exportSolution,
	type SolutionExportOptions,
	setSolutionConfig,
	syncSolution,
	previewSolutionFromRepo,
} from "@/services/solutions";
import { UpgradeDiffView } from "@/components/solutions/CreateEditSolution";
import { SolutionSetupWizard } from "@/components/solutions/SolutionSetupWizard";
import { SolutionReadmeTab } from "@/components/solutions/SolutionReadmeTab";
import type { components } from "@/lib/v1";

type EntitySummary = components["schemas"]["SolutionEntitySummary"];
type ConfigStatus = components["schemas"]["SolutionConfigStatus"];
type ConfigType = components["schemas"]["ConfigType"];
type SolutionDeletionSummary = components["schemas"]["SolutionDeletionSummary"];

/** The three top-level tabs (down from 9). README leads Overview (it's the
 * description, not a section); the 6 entity inventories collapse into Contents
 * (type chips); config VALUES + integration connections live in Configuration;
 * Setup is no longer a tab — it's a STATE surfaced as an Overview banner + a
 * Configuration badge. */
type TabKey = "overview" | "contents" | "configuration";

/** The entity kinds shown inside the Contents tab (the old per-entity tabs). */
type EntityKind =
	| "workflows"
	| "apps"
	| "forms"
	| "agents"
	| "tables"
	| "claims"
	| "files";

/** Contents type-chip selection: a specific kind or the combined summary. */
type ContentsFilter = "all" | EntityKind;

const ENTITY_TABS: {
	key: EntityKind;
	label: string;
	Icon: typeof Workflow;
}[] = [
	{ key: "workflows", label: "Workflows", Icon: Workflow },
	{ key: "apps", label: "Apps", Icon: AppWindow },
	{ key: "forms", label: "Forms", Icon: FileCode },
	{ key: "agents", label: "Agents", Icon: Bot },
	{ key: "tables", label: "Tables", Icon: Database },
	{ key: "claims", label: "Custom Claims", Icon: KeyRound },
	{ key: "files", label: "Files", Icon: FolderOpen },
];

/** Per-entity-page link target, carrying the `?from` so the entity page can
 * offer a "back to this Solution" affordance (consumed in Task 19b). */
function entityHref(
	kind: Exclude<EntityKind, "files">,
	entity: EntitySummary,
	solutionId: string,
): string {
	const from = `?from=solution:${solutionId}`;
	switch (kind) {
		case "tables":
			return `/tables/${entity.id}${from}`;
		case "claims":
			return `/tables${from}`;
		case "agents":
			return `/agents/${entity.id}${from}`;
		case "forms":
			return `/forms/${entity.id}/edit${from}`;
		case "apps":
			return `/apps/${entity.id}/edit${from}`;
		case "workflows":
			// The execute route is keyed by workflow NAME, not id.
			return `/workflows/${encodeURIComponent(entity.name)}/execute${from}`;
	}
}

function isSecretType(type: string): boolean {
	const t = type.toLowerCase();
	return t === "secret" || t === "password";
}

/** Coerce a declared config type string into the API's ConfigType enum. */
function asConfigType(type: string): ConfigType {
	const t = type.toLowerCase();
	if (t === "int" || t === "bool" || t === "json" || t === "secret") return t;
	if (t === "password") return "secret";
	return "string";
}

const ENTITY_TAB_LABEL: Record<EntityKind, string> = {
	workflows: "workflows",
	apps: "apps",
	forms: "forms",
	agents: "agents",
	tables: "tables",
	claims: "custom claims",
	files: "files",
};

const GRID_TABLE_ENTITY_TABS = new Set<EntityKind>([
	"workflows",
	"apps",
	"forms",
	"agents",
]);

function sourceRef(entity: EntitySummary): string {
	if (entity.path && entity.function_name) {
		return `${entity.path}::${entity.function_name}`;
	}
	return entity.path ?? entity.slug ?? "-";
}

function formatDate(value: string | null | undefined): string {
	if (!value) return "-";
	return new Date(value).toLocaleDateString(undefined, {
		year: "numeric",
		month: "short",
		day: "numeric",
	});
}

function workflowTypeBadge(entity: EntitySummary) {
	if (entity.type === "tool") {
		return (
			<Badge
				variant="secondary"
				className="bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300"
			>
				<Bot className="mr-1 h-3 w-3" />
				Tool
			</Badge>
		);
	}
	if (entity.type === "data_provider") {
		return (
			<Badge
				variant="secondary"
				className="bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300"
			>
				<Database className="mr-1 h-3 w-3" />
				Data Provider
			</Badge>
		);
	}
	return (
		<Badge variant="secondary">
			<PlayCircle className="mr-1 h-3 w-3" />
			Workflow
		</Badge>
	);
}

function accessBadge(accessLevel: string | null | undefined) {
	if (!accessLevel) return null;
	return (
		<span className="flex items-center gap-1">
			{accessLevel === "role_based" ? (
				<Shield className="h-3 w-3" />
			) : (
				<Users className="h-3 w-3" />
			)}
			{accessLevel === "role_based"
				? "Roles"
				: accessLevel === "everyone"
					? "Everyone"
					: "Auth"}
		</span>
	);
}

function entityStatus(entity: EntitySummary, kind: EntityKind) {
	if (kind === "forms") {
		return entity.is_active === false ? "Inactive" : "Active";
	}
	if (kind === "agents") {
		return entity.is_active === false ? "Paused" : "Active";
	}
	return null;
}

function SolutionEntityGrid({
	kind,
	items,
	solutionId,
}: {
	kind: Exclude<EntityKind, "files">;
	items: EntitySummary[];
	solutionId: string;
}) {
	const navigate = useNavigate();
	return (
		<div className="grid gap-4 grid-cols-[repeat(auto-fill,minmax(300px,1fr))]">
			{items.map((entity) => {
				const href = entityHref(kind, entity, solutionId);
				const status = entityStatus(entity, kind);
				if (kind === "apps") {
					return (
						<div
							key={entity.id}
							role="button"
							tabIndex={0}
							onClick={() => navigate(href)}
							onKeyDown={(event) => {
								if (event.key === "Enter" || event.key === " ") {
									event.preventDefault();
									navigate(href);
								}
							}}
							className="group relative flex cursor-pointer flex-col overflow-hidden rounded-2xl bg-card shadow-sm ring-1 ring-foreground/5 transition-all hover:-translate-y-px hover:ring-foreground/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring dark:ring-foreground/10 dark:hover:ring-foreground/15"
						>
							<div className="border-b px-4 py-3">
								<div className="flex items-start justify-between gap-3">
									<div className="flex min-w-0 items-center gap-2">
										<EntityLogo
											entityType="app"
											entityId={entity.id}
											fallback={
												<AppWindow className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
											}
											size={20}
											className="h-5 w-5 rounded object-cover shrink-0"
										/>
										<span className="truncate text-[14.5px] font-semibold">
											{entity.name}
										</span>
									</div>
									<Badge variant="outline" className="text-[10px] px-1.5 py-0">
										{entity.app_model ?? "app"}
									</Badge>
								</div>
							</div>
							<div className="relative flex-1 px-4 py-3 min-h-[72px]">
								{entity.description ? (
									<p className="line-clamp-2 text-[13px] text-muted-foreground">
										{entity.description}
									</p>
								) : (
									<p className="text-[13px] italic text-muted-foreground/50">
										No description
									</p>
								)}
								<div className="pointer-events-none absolute inset-0 flex flex-col items-start justify-center gap-1.5 bg-background/85 px-4 opacity-0 backdrop-blur-sm transition-opacity group-hover:opacity-100">
									<span className="text-left text-[13px] font-medium text-foreground">
										<Code2 className="-mt-0.5 mr-1.5 inline h-3.5 w-3.5" />
										Open in Apps
									</span>
								</div>
							</div>
							<div className="flex items-center justify-between gap-2 border-t px-4 py-2.5">
								<div className="flex items-center gap-1.5">
									<span className="text-[11px] text-muted-foreground">
										{entity.slug ?? sourceRef(entity)}
									</span>
								</div>
								<Badge variant="default" className="text-[10px] px-1.5 py-0">
									Managed
								</Badge>
							</div>
						</div>
					);
				}

				if (kind === "agents") {
					return (
						<a
							key={entity.id}
							href={href}
							className="group flex flex-col overflow-hidden rounded-2xl border bg-card shadow-sm ring-1 ring-foreground/5 transition-all hover:-translate-y-px hover:ring-foreground/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
						>
							<div className="border-b px-4 pb-3 pt-3.5">
								<div className="flex items-start justify-between gap-3">
									<div className="flex min-w-0 items-center gap-2">
										<EntityLogo
											entityType="agent"
											entityId={entity.id}
											fallback={
												<Bot className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
											}
											size={20}
											className="h-5 w-5 rounded shrink-0 object-cover"
										/>
										<span className="truncate text-[14.5px] font-semibold">
											{entity.name}
										</span>
										{status && (
											<Badge variant={status === "Paused" ? "secondary" : "default"} className="text-[11px]">
												{status}
											</Badge>
										)}
									</div>
								</div>
								{entity.description && (
									<p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
										{entity.description}
									</p>
								)}
							</div>
							<div className="flex flex-1 items-center justify-between gap-2 p-4 text-xs text-muted-foreground">
								<span>{entity.access_level ?? "authenticated"}</span>
								<span>{entity.type ?? "agent"}</span>
							</div>
						</a>
					);
				}

				return (
					<Card
						key={entity.id}
						className="hover:border-primary transition-colors flex flex-col"
					>
						<CardHeader className="pb-2">
							<div className="mb-3 flex items-center justify-between gap-2">
								<div className="flex items-center gap-2">
									{kind === "workflows" ? (
										workflowTypeBadge(entity)
									) : kind === "forms" ? (
										<Badge variant="secondary">
											<FileCode className="mr-1 h-3 w-3" />
											Form
										</Badge>
									) : kind === "tables" ? (
										<Badge variant="secondary">
											<Database className="mr-1 h-3 w-3" />
											Table
										</Badge>
									) : (
										<Badge variant="secondary">
											<KeyRound className="mr-1 h-3 w-3" />
											Custom Claim
										</Badge>
									)}
								</div>
								<Button
									variant="outline"
									size="icon-sm"
									onClick={() => navigate(href)}
									title={`Open ${entity.name}`}
								>
									<Code2 className="h-3.5 w-3.5" />
								</Button>
							</div>
							<CardTitle
								className={
									kind === "workflows" || kind === "tables" || kind === "claims"
										? "font-mono text-base break-all"
										: "text-base break-all"
								}
							>
								{entity.name}
							</CardTitle>
							{entity.description && (
								<CardDescription className="mt-2 text-sm break-words line-clamp-2">
									{entity.description}
								</CardDescription>
							)}
						</CardHeader>
						<CardContent className="pt-0 mt-auto space-y-3">
							<div className="flex items-center gap-2 text-xs text-muted-foreground">
								{entity.category && <span>{entity.category}</span>}
								{entity.category && <span>·</span>}
								{kind === "workflows" && <span>{sourceRef(entity)}</span>}
								{kind === "forms" && accessBadge(entity.access_level)}
								{kind === "tables" && <span>{formatDate(entity.created_at)}</span>}
								{kind === "claims" && (
									<span className="font-mono">
										{entity.source_table ?? "-"}.{entity.select ?? "*"}
									</span>
								)}
							</div>
							{status && (
								<div className="flex flex-wrap items-center gap-1.5">
									<Badge variant={status === "Active" ? "default" : "secondary"}>
										{status}
									</Badge>
								</div>
							)}
							{kind === "workflows" && entity.type === "data_provider" && (
								<div className="flex flex-wrap items-center gap-1.5">
									<Badge variant="outline">
										<Database className="mr-1 h-3 w-3" />
										Data provider
									</Badge>
								</div>
							)}
							{kind === "tables" && entity.source_table && (
								<div className="flex flex-wrap items-center gap-1.5">
									<Badge variant="outline">
										<Unlink className="mr-1 h-3 w-3" />
										{entity.source_table}
									</Badge>
								</div>
							)}
						</CardContent>
					</Card>
				);
			})}
		</div>
	);
}

function SolutionEntityTable({
	kind,
	items,
	solutionId,
}: {
	kind: Exclude<EntityKind, "files">;
	items: EntitySummary[];
	solutionId: string;
}) {
	const navigate = useNavigate();
	return (
		<DataTable>
			<DataTableHeader>
				<DataTableRow>
					<DataTableHead>Name</DataTableHead>
					<DataTableHead>Description</DataTableHead>
					{kind === "workflows" && (
						<>
							<DataTableHead className="w-0 whitespace-nowrap">Type</DataTableHead>
							<DataTableHead className="w-0 whitespace-nowrap">Category</DataTableHead>
							<DataTableHead className="w-0 whitespace-nowrap">Source</DataTableHead>
						</>
					)}
					{kind === "apps" && (
						<>
							<DataTableHead className="w-0 whitespace-nowrap">Model</DataTableHead>
							<DataTableHead className="w-0 whitespace-nowrap">Source</DataTableHead>
						</>
					)}
					{kind === "forms" && (
						<>
							<DataTableHead className="w-0 whitespace-nowrap">Access</DataTableHead>
							<DataTableHead className="w-0 whitespace-nowrap">Status</DataTableHead>
						</>
					)}
					{kind === "agents" && (
						<>
							<DataTableHead className="w-0 whitespace-nowrap">Access</DataTableHead>
							<DataTableHead className="w-0 whitespace-nowrap">Status</DataTableHead>
						</>
					)}
					{kind === "tables" && (
						<DataTableHead className="w-0 whitespace-nowrap">Created</DataTableHead>
					)}
					{kind === "claims" && (
						<>
							<DataTableHead className="w-0 whitespace-nowrap">Type</DataTableHead>
							<DataTableHead className="w-0 whitespace-nowrap">Source table</DataTableHead>
							<DataTableHead className="w-0 whitespace-nowrap">Select</DataTableHead>
						</>
					)}
				</DataTableRow>
			</DataTableHeader>
			<DataTableBody>
				{items.map((entity) => {
					const status = entityStatus(entity, kind);
					return (
						<DataTableRow
							key={entity.id}
							clickable
							onClick={() => navigate(entityHref(kind, entity, solutionId))}
						>
							<DataTableCell
								className={
									kind === "workflows" || kind === "tables" || kind === "claims"
										? "font-mono font-medium"
										: "font-medium"
								}
							>
								<span className="flex items-center gap-2">
									{kind === "apps" && (
										<EntityLogo
											entityType="app"
											entityId={entity.id}
											fallback={
												<AppWindow className="h-4 w-4 shrink-0 text-muted-foreground" />
											}
											size={18}
											className="h-[18px] w-[18px] rounded object-cover shrink-0"
										/>
									)}
									{kind === "agents" && (
										<EntityLogo
											entityType="agent"
											entityId={entity.id}
											fallback={
												<Bot className="h-4 w-4 shrink-0 text-muted-foreground" />
											}
											size={18}
											className="h-[18px] w-[18px] rounded object-cover shrink-0"
										/>
									)}
									{entity.name}
								</span>
							</DataTableCell>
							<DataTableCell className="max-w-xs truncate text-muted-foreground">
								{entity.description || "-"}
							</DataTableCell>
							{kind === "workflows" && (
								<>
									<DataTableCell className="w-0 whitespace-nowrap">
										{workflowTypeBadge(entity)}
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap text-sm text-muted-foreground">
										{entity.category || "-"}
									</DataTableCell>
									<DataTableCell className="w-0 max-w-[18rem] truncate font-mono text-xs text-muted-foreground">
										{sourceRef(entity)}
									</DataTableCell>
								</>
							)}
							{kind === "apps" && (
								<>
									<DataTableCell className="w-0 whitespace-nowrap">
										<Badge variant="outline">{entity.app_model ?? "-"}</Badge>
									</DataTableCell>
									<DataTableCell className="w-0 max-w-[18rem] truncate font-mono text-xs text-muted-foreground">
										{sourceRef(entity)}
									</DataTableCell>
								</>
							)}
							{kind === "forms" && (
								<>
									<DataTableCell className="w-0 whitespace-nowrap">
										<Badge variant="outline">{entity.access_level ?? "-"}</Badge>
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap">
										<Badge variant={status === "Inactive" ? "secondary" : "default"}>
											{status}
										</Badge>
									</DataTableCell>
								</>
							)}
							{kind === "agents" && (
								<>
									<DataTableCell className="w-0 whitespace-nowrap">
										<Badge variant="outline">{entity.access_level ?? "-"}</Badge>
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap">
										<Badge variant={status === "Paused" ? "secondary" : "default"}>
											{status}
										</Badge>
									</DataTableCell>
								</>
							)}
							{kind === "tables" && (
								<DataTableCell className="w-0 whitespace-nowrap text-sm text-muted-foreground">
									{formatDate(entity.created_at)}
								</DataTableCell>
							)}
							{kind === "claims" && (
								<>
									<DataTableCell className="w-0 whitespace-nowrap text-sm text-muted-foreground">
										{entity.type || "-"}
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap font-mono text-sm">
										{entity.source_table || "-"}
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap font-mono text-sm">
										{entity.select || "-"}
									</DataTableCell>
								</>
							)}
						</DataTableRow>
					);
				})}
			</DataTableBody>
		</DataTable>
	);
}

function EntityTabContent({
	kind,
	items,
	solutionId,
	fileCount,
}: {
	kind: EntityKind;
	items: EntitySummary[];
	solutionId: string;
	/** Actual file count from SolutionEntities.files (files kind only). */
	fileCount?: number;
}) {
	const navigate = useNavigate();
	const [search, setSearch] = useState("");
	const canToggleView = GRID_TABLE_ENTITY_TABS.has(kind);
	const [viewMode, setViewMode] = useState<"grid" | "table">(
		canToggleView ? "grid" : "table",
	);

	const q = search.trim().toLowerCase();
	const visible = q
		? items.filter((e) =>
				[
					e.name,
					e.description,
					e.slug,
					e.path,
					e.function_name,
					e.type,
					e.category,
					e.source_table,
					e.select,
				].some((value) => value?.toLowerCase().includes(q)),
				)
		: items;
	const managedVisible = visible.map((entity) => ({
		...entity,
		is_solution_managed: true,
		solution_id: solutionId,
	}));
	const formValidation = new Map<string, FormValidationState>(
		visible.map((entity) => [
			entity.id,
			{ valid: true, missingParams: [] },
		]),
	);

	// Files are a flat resource with their own dedicated Explorer page — navigate
	// there instead of attempting to inline-render a SolutionFileSummary list.
	if (kind === "files") {
		const count = fileCount ?? 0;
		const href = `/files?install=${solutionId}&from=solution:${solutionId}`;
		return (
			<div className="flex flex-col items-center gap-3 py-8 text-center">
				<FolderOpen className="h-8 w-8 text-muted-foreground" />
				<p className="text-sm text-muted-foreground">
					{count === 0
						? "This Solution has no files."
						: `${count} file${count === 1 ? "" : "s"} owned by this install.`}
				</p>
				<Link
					to={href}
					className="inline-flex items-center gap-1.5 text-sm font-medium hover:underline"
					data-testid="files-view-link"
				>
					<FolderOpen className="h-4 w-4" />
					Browse Files
				</Link>
			</div>
		);
	}

	if (items.length === 0) {
		return (
			<div className="text-sm text-muted-foreground py-8 text-center rounded-2xl border border-dashed">
				This Solution deploys no {ENTITY_TAB_LABEL[kind]}.
			</div>
		);
	}
	return (
		<div className="flex flex-col gap-3">
			<div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
				<SearchBox
					value={search}
					onChange={setSearch}
					placeholder={`Search ${ENTITY_TAB_LABEL[kind]}...`}
					className="flex-1"
				/>
					{canToggleView && (
						<ToggleGroup
							type="single"
							value={viewMode}
							onValueChange={(value: string) =>
								value && setViewMode(value as "grid" | "table")
							}
						>
							<ToggleGroupItem value="grid" aria-label="Grid view" size="sm">
								<LayoutGrid className="h-4 w-4" />
							</ToggleGroupItem>
							<ToggleGroupItem value="table" aria-label="Table view" size="sm">
								<TableIcon className="h-4 w-4" />
							</ToggleGroupItem>
						</ToggleGroup>
					)}
			</div>
				{visible.length === 0 ? (
					<div className="text-sm text-muted-foreground py-8 text-center rounded-2xl border border-dashed">
						No {ENTITY_TAB_LABEL[kind]} match “{search.trim()}”.
					</div>
				) : kind === "workflows" ? (
					<WorkflowListSurface
						workflows={managedVisible as WorkflowListItem[]}
						viewMode={viewMode}
						isPlatformAdmin={false}
						canManageWorkflows={true}
						getOrgName={() => "Solution"}
						onViewHistory={(workflow) =>
							navigate(`/history?workflow=${workflow.id ?? ""}`)
						}
						onExecute={(workflow) =>
							navigate(
								`/workflows/${encodeURIComponent(workflow.name ?? "")}/execute?from=solution:${solutionId}`,
							)
						}
						emptySearchActive={Boolean(search.trim())}
					/>
				) : kind === "apps" ? (
					<ApplicationListSurface
						apps={managedVisible as ApplicationListItem[]}
						viewMode={viewMode}
						isPlatformAdmin={false}
						canManageApps={true}
						getOrgName={() => "Solution"}
						onLaunch={(app) =>
							navigate(`/apps/${app.slug ?? app.id}?from=solution:${solutionId}`)
						}
						onPreview={(app) =>
							navigate(
								`/apps/${app.slug ?? app.id}/preview?from=solution:${solutionId}`,
							)
						}
						emptySearchActive={Boolean(search.trim())}
					/>
				) : kind === "forms" ? (
					<FormListSurface
						forms={managedVisible as FormListItem[]}
						viewMode={viewMode}
						isPlatformAdmin={false}
						canManageForms={true}
						getOrgName={() => "Solution"}
						formValidation={formValidation}
						onLaunch={(form) =>
							navigate(`/execute/${form.id}?from=solution:${solutionId}`)
						}
						emptySearchActive={Boolean(search.trim())}
					/>
				) : viewMode === "grid" ? (
					<SolutionEntityGrid
						kind={kind}
					items={visible}
					solutionId={solutionId}
				/>
			) : (
				<SolutionEntityTable
					kind={kind}
					items={visible}
					solutionId={solutionId}
				/>
			)}
		</div>
	);
}

function ConfigRow({
	config,
	orgId,
	onSaved,
}: {
	config: ConfigStatus;
	orgId: string | null;
	onSaved: () => void;
}) {
	const [value, setValue] = useState("");
	const secret = isSecretType(config.type);

	const saveMut = useMutation({
		mutationFn: () =>
			setSolutionConfig({
				key: config.key,
				value,
				type: asConfigType(config.type),
				organizationId: orgId,
			}),
		onSuccess: () => {
			toast.success(`Saved "${config.key}"`);
			setValue("");
			onSaved();
		},
		onError: (err: unknown) => {
			toast.error(
				err instanceof Error ? err.message : "Failed to save config value",
			);
		},
	});

	const requiredUnset = config.required && !config.value_set;

	return (
		<div
			className={
				"rounded-lg border p-4 " +
				(requiredUnset ? "border-yellow-500/60 bg-yellow-500/5" : "")
			}
		>
			<div className="flex items-center justify-between gap-3">
				<div className="flex min-w-0 items-center gap-2">
					<span className="truncate font-mono text-sm font-medium">
						{config.key}
					</span>
					<Badge variant="outline" className="shrink-0 text-[10px]">
						{config.type}
					</Badge>
					{config.required && (
						<span className="shrink-0 text-xs text-destructive">
							required
						</span>
					)}
				</div>
				<span
					data-testid={`config-status-${config.key}`}
					className={
						"flex shrink-0 items-center gap-1 text-xs font-medium " +
						(config.value_set
							? "text-green-600 dark:text-green-500"
							: "text-muted-foreground")
					}
				>
					{config.value_set ? (
						<CheckCircle2 className="h-3.5 w-3.5" />
					) : (
						<Circle className="h-3.5 w-3.5" />
					)}
					{config.value_set ? "Set" : "Not set"}
				</span>
			</div>
			{config.description && (
				<p className="mt-1 text-xs text-muted-foreground">
					{config.description}
				</p>
			)}
			<div className="mt-3 flex items-center gap-2">
				<Input
					data-testid={`config-value-input-${config.key}`}
					type={secret ? "password" : "text"}
					value={value}
					placeholder={
						config.value_set ? "Enter a new value…" : "Enter a value…"
					}
					onChange={(e) => setValue(e.target.value)}
				/>
				<Button
					data-testid={`save-config-${config.key}`}
					disabled={value.trim() === "" || saveMut.isPending}
					onClick={() => saveMut.mutate()}
				>
					{saveMut.isPending && (
						<Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
					)}
					Save
				</Button>
			</div>
		</div>
	);
}

/** Tailwind classes for a Contents type-chip (selected vs not). */
function chipClass(active: boolean): string {
	return [
		"inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-sm transition-colors",
		active
			? "border-primary bg-primary text-primary-foreground"
			: "border-border bg-background text-muted-foreground hover:bg-muted",
	].join(" ");
}

/** Overview = the install's description (README, rendered GitHub-style) leading
 * a status/contents summary so the tab is never empty. Installed Solution
 * content is read-only in the UI; changes flow through deploy/sync. */
function OverviewTab({
	readme,
	entityCounts,
	configsCount,
	version,
	gitConnected,
	orgName,
	onGoToContents,
}: {
	readme: string | null;
	entityCounts: Record<EntityKind, number>;
	configsCount: number;
	version: string | null;
	gitConnected: boolean;
	orgName: string;
	onGoToContents: () => void;
}) {
	const total = Object.values(entityCounts).reduce((a, b) => a + b, 0);
	return (
		<div className="flex h-full min-h-0 flex-col gap-6 overflow-auto">
			{/* Status summary — always present, so Overview never reads as empty. */}
			<Card>
				<CardHeader>
					<CardTitle className="text-base">At a glance</CardTitle>
					<CardDescription>
						{version ? `Version ${version} · ` : ""}
						{orgName} · {gitConnected ? "Git-connected" : "Manual install"}
					</CardDescription>
				</CardHeader>
				<CardContent>
					<div className="flex flex-wrap gap-4 text-sm">
						{ENTITY_TABS.map(({ key, label, Icon }) => (
							<button
								type="button"
								key={key}
								onClick={onGoToContents}
								className="flex items-center gap-1.5 text-muted-foreground hover:text-foreground"
							>
								<Icon className="h-4 w-4" />
								<span className="font-medium text-foreground">
									{entityCounts[key]}
								</span>
								{label}
							</button>
						))}
						<span className="flex items-center gap-1.5 text-muted-foreground">
							<SlidersHorizontal className="h-4 w-4" />
							<span className="font-medium text-foreground">
								{configsCount}
							</span>
							configs
						</span>
					</div>
					{total === 0 && (
						<p className="mt-3 text-sm text-muted-foreground">
							This Solution deploys no entities yet.
						</p>
					)}
				</CardContent>
			</Card>

			{/* README — the description. Read-only render with an Edit affordance
			    only for disconnected installs. */}
			<div className="min-h-0 flex-1">
				<SolutionReadmeTab
					readme={readme}
					canEdit={false}
				/>
			</div>
		</div>
	);
}

/** The "All" view of Contents — a compact per-type grid that jumps to a chip. */
function ContentsSummary({
	entityCounts,
	onPick,
}: {
	entityCounts: Record<EntityKind, number>;
	onPick: (kind: EntityKind) => void;
}) {
	const total = Object.values(entityCounts).reduce((a, b) => a + b, 0);
	if (total === 0) {
		return (
			<div className="rounded-2xl border border-dashed py-12 text-center text-sm text-muted-foreground">
				This Solution deploys no entities.
			</div>
		);
	}
	return (
		<div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
			{ENTITY_TABS.filter(({ key }) => entityCounts[key] > 0).map(
				({ key, label, Icon }) => (
					<button
						type="button"
						key={key}
						data-testid={`summary-${key}`}
						onClick={() => onPick(key)}
						className="flex items-center gap-3 rounded-xl border p-4 text-left hover:bg-muted"
					>
						<Icon className="h-5 w-5 text-muted-foreground" />
						<div>
							<div className="text-lg font-semibold">{entityCounts[key]}</div>
							<div className="text-xs text-muted-foreground">{label}</div>
						</div>
					</button>
				),
			)}
		</div>
	);
}

/** Configuration = config VALUES (re-key over the install's life) + integration
 * connections (reconnect expired OAuth). This is the permanent home of what was
 * the one-time Setup wizard — revisited, not a wizard. */
function ConfigurationTab({
	configs,
	orgId,
	setupItems,
	setupComplete,
	setupError,
	onInvalidate,
	onSetConfig,
}: {
	configs: ConfigStatus[];
	orgId: string | null;
	setupItems: components["schemas"]["SolutionSetupItem"][];
	setupComplete: boolean;
	setupError: unknown;
	onInvalidate: () => void;
	onSetConfig: (key: string, value: string) => void | Promise<void>;
}) {
	const hasConnections = setupItems.some((i) => i.kind === "connection");
	return (
		<div className="flex flex-col gap-6">
			{/* Integration connections (if the solution declares any). */}
			{setupError ? (
				<div className="rounded-lg border border-destructive/40 bg-destructive/5 py-6 text-center text-sm text-destructive">
					{setupError instanceof Error
						? setupError.message
						: "Couldn't load setup status"}
				</div>
			) : (
				hasConnections && (
					<section data-testid="config-connections">
						<h3 className="mb-2 text-sm font-semibold">Connections</h3>
						<SolutionSetupWizard
							items={setupItems}
							setupComplete={setupComplete}
							onFinish={onInvalidate}
							onSetConfig={onSetConfig}
						/>
					</section>
				)
			)}

			{/* Config values. */}
			<section data-testid="config-values">
				{hasConnections && (
					<h3 className="mb-2 text-sm font-semibold">Config values</h3>
				)}
				{configs.length > 0 ? (
					<div className="space-y-3">
						{configs.map((cfg) => (
							<ConfigRow
								key={cfg.id}
								config={cfg}
								orgId={orgId}
								onSaved={onInvalidate}
							/>
						))}
					</div>
				) : (
					!hasConnections && (
						<div className="rounded-lg border py-12 text-center text-sm text-muted-foreground">
							This Solution declares no configuration.
						</div>
					)
				)}
			</section>
		</div>
	);
}

export function SolutionDetail() {
	const { solutionId } = useParams<{ solutionId: string }>();
	const navigate = useNavigate();
	const queryClient = useQueryClient();
	const { data: organizations } = useOrganizations();

	// Land on Overview — the install's description + at-a-glance status. Contents
	// (entities) and Configuration are one click away.
	const [tab, setTab] = useState<TabKey>("overview");
	const [editOpen, setEditOpen] = useState(false);
	const [updateOpen, setUpdateOpen] = useState(false);
	const [syncConfirmOpen, setSyncConfirmOpen] = useState(false);
	const [captureOpen, setCaptureOpen] = useState(false);
	const [exportDialogOpen, setExportDialogOpen] = useState(false);
	// Hard-delete modal state.
	const [hardDeleteOpen, setHardDeleteOpen] = useState(false);
	const [hardDeleteConfirm, setHardDeleteConfirm] = useState("");
	const [deletionSummary, setDeletionSummary] =
		useState<SolutionDeletionSummary | null>(null);
	const [deletionSummaryLoading, setDeletionSummaryLoading] = useState(false);

	const { data, isLoading, error } = useQuery({
		queryKey: ["solutions", solutionId, "entities"],
		queryFn: () => getSolutionEntities(solutionId!),
		enabled: !!solutionId,
	});

	const {
		data: setupData,
		error: setupError,
		refetch: refetchSetup,
	} = useQuery({
		queryKey: ["solutions", solutionId, "setup"],
		queryFn: () => getSolutionSetup(solutionId!),
		enabled: !!solutionId,
	});

	const { data: readmeData, refetch: refetchReadme } = useQuery({
		queryKey: ["solutions", solutionId, "readme"],
		queryFn: () => getSolutionReadme(solutionId!),
		enabled: !!solutionId,
	});

	const invalidate = () => {
		void queryClient.invalidateQueries({
			queryKey: ["solutions", solutionId, "entities"],
		});
		void refetchSetup();
		void refetchReadme();
	};

	const sol = data?.solution;

	const exportMut = useMutation({
		mutationFn: ({
			mode,
			password,
			options,
		}: {
			mode: "shareable" | "full";
			password?: string;
			options?: SolutionExportOptions;
		}) => exportSolution(solutionId!, mode, password, options),
		onSuccess: ({ blob, filename }) => {
			const url = URL.createObjectURL(blob);
			const a = document.createElement("a");
			a.href = url;
			a.download = filename;
			document.body.appendChild(a);
			a.click();
			a.remove();
			URL.revokeObjectURL(url);
			setExportDialogOpen(false);
		},
		onError: (err: unknown) => {
			toast.error("Failed to export", {
				description: err instanceof Error ? err.message : "Unknown error",
			});
		},
	});

	/** Non-destructive uninstall — flips status to inactive, data frozen. */
	const uninstallMut = useMutation({
		mutationFn: () => uninstallSolution(solutionId!),
		onSuccess: () => {
			void queryClient.invalidateQueries({ queryKey: ["solutions"] });
			invalidate();
			toast.success("Solution uninstalled (inactive)");
		},
		onError: (err: unknown) => {
			toast.error("Failed to uninstall", {
				description: err instanceof Error ? err.message : "Unknown error",
			});
		},
	});

	/** Hard-delete — permanently destroys everything. Requires confirm === slug. */
	const hardDeleteMut = useMutation({
		mutationFn: () => deleteSolution(solutionId!, hardDeleteConfirm),
		onSuccess: (summary) => {
			queryClient.invalidateQueries({ queryKey: ["solutions"] });
			toast.success("Solution permanently deleted", {
				description: `Removed ${summary.workflows_deleted} workflows, ${summary.apps_deleted} apps, ${summary.forms_deleted} forms, ${summary.agents_deleted} agents, ${summary.tables_deleted} tables, ${summary.files_swept} files.`,
			});
			navigate("/solutions");
		},
		onError: (err: unknown) => {
			toast.error("Failed to delete solution", {
				description: err instanceof Error ? err.message : "Unknown error",
			});
		},
	});

	/** Open the hard-delete modal and eagerly fetch the deletion-summary. */
	async function openHardDelete() {
		setHardDeleteConfirm("");
		setDeletionSummary(null);
		setHardDeleteOpen(true);
		setDeletionSummaryLoading(true);
		try {
			const summary = await getSolutionDeletionSummary(solutionId!);
			setDeletionSummary(summary);
		} catch {
			// Summary is informational — allow the modal to proceed without it.
		} finally {
			setDeletionSummaryLoading(false);
		}
	}

	// "Update now" for a git-connected install with an available update: pull the
	// repo at its configured ref and full-replace the installed content. The
	// backend clears `update_available_version` on success, so invalidating the
	// solution query clears the badge.
	const syncMut = useMutation({
		mutationFn: () => syncSolution(solutionId!),
		onSuccess: () => {
			toast.success("Solution updated from repository");
			setSyncConfirmOpen(false);
			void queryClient.invalidateQueries({ queryKey: ["solutions"] });
			invalidate();
		},
		onError: (err: unknown) => {
			toast.error("Failed to update", {
				description: err instanceof Error ? err.message : "Unknown error",
			});
		},
	});

	// Lazily preview the connected repo when the Update-now dialog opens, so the
	// operator sees WHAT the full-replace will change (added/removed/changed
	// entities + config) before confirming — the git update path previously gave
	// no diff (audit CM3/M2). Reuses the from-repo preview endpoint.
	const updateDiffQuery = useQuery({
		queryKey: ["solution-update-diff", solutionId, sol?.git_ref, sol?.repo_subpath],
		enabled: syncConfirmOpen && !!sol?.git_connected && !!sol?.git_repo_url,
		queryFn: () =>
			previewSolutionFromRepo({
				repo_url: sol!.git_repo_url!,
				...(sol!.repo_subpath ? { repo_subpath: sol!.repo_subpath } : {}),
				...(sol!.git_ref ? { git_ref: sol!.git_ref } : {}),
			}),
		staleTime: 0,
	});

	const orgName = useMemo(() => {
		if (!sol?.organization_id) return "Global";
		return (
			organizations?.find((o) => o.id === sol.organization_id)?.name ??
			sol.organization_id
		);
	}, [sol, organizations]);

	const entityCounts = useMemo(() => {
		return {
			workflows: data?.workflows?.length ?? 0,
			apps: data?.apps?.length ?? 0,
			forms: data?.forms?.length ?? 0,
			agents: data?.agents?.length ?? 0,
			tables: data?.tables?.length ?? 0,
			claims: data?.claims?.length ?? 0,
			files: data?.files?.length ?? 0,
		} satisfies Record<EntityKind, number>;
	}, [data]);

	const totalContents = useMemo(
		() => Object.values(entityCounts).reduce((a, b) => a + b, 0),
		[entityCounts],
	);
	const configsCount = data?.configs?.length ?? 0;

	// `files` carries SolutionFileSummary[], not EntitySummary[] — EntityTabContent
	// handles the files kind before it reaches the EntitySummary rendering path.
	const itemsFor = (key: EntityKind): EntitySummary[] => {
		if (key === "files") return [];
		return (data?.[key] as EntitySummary[] | undefined) ?? [];
	};

	const requiredUnset = data?.required_configs_unset ?? [];
	// Contents tab: "All" shows a combined per-type summary; a specific chip
	// renders that kind's full surface (with its specialized actions — workflow
	// execute, form launch, app open — which a merged column list would lose).
	const [contentsFilter, setContentsFilter] =
		useState<ContentsFilter>("all");
	const activeKind: EntityKind | null =
		contentsFilter === "all" ? null : contentsFilter;

	return (
		<div
			data-testid="solution-detail"
			className="h-full flex flex-col space-y-6 max-w-7xl mx-auto"
		>
			{/* Breadcrumb */}
			<div className="text-sm">
				<Link
					to="/solutions"
					className="inline-flex items-center text-muted-foreground hover:text-foreground"
				>
					<ChevronLeft className="mr-1 h-4 w-4" />
					Solutions
				</Link>
				{sol && (
					<>
						<span className="mx-2 text-muted-foreground">/</span>
						<span className="font-medium">{sol.name}</span>
					</>
				)}
			</div>

			{isLoading ? (
				<div className="space-y-4">
					<Skeleton className="h-10 w-64" />
					<Skeleton className="h-9 w-full max-w-xl" />
					<Skeleton className="h-64 w-full" />
				</div>
			) : error ? (
				<Card>
					<CardContent className="py-10 text-center text-sm text-destructive">
						{error instanceof Error
							? error.message
							: "Failed to load Solution"}
					</CardContent>
				</Card>
			) : data && sol ? (
				<>
					{/* Header */}
					<div className="flex items-start justify-between gap-4">
						<div className="min-w-0 flex-1">
							<div className="flex items-center gap-3">
								<h1 className="text-3xl font-extrabold tracking-tight">
									{sol.name}
								</h1>
								{sol.setup_complete === false && (
									<Badge
										data-testid="incomplete-badge"
										variant="outline"
										className="gap-1 border-yellow-500/60 bg-yellow-500/10 text-yellow-700 dark:text-yellow-400"
									>
										<AlertTriangle className="h-3 w-3" />
										Incomplete
									</Badge>
								)}
							</div>
							<p className="mt-1 text-sm text-muted-foreground">
								{sol.slug}
								{sol.upgraded_from_version && (
									<span className="ml-2 text-xs">
										upgraded from v{sol.upgraded_from_version}
									</span>
								)}
							</p>
							<div className="mt-3 flex flex-wrap items-center gap-2">
								{sol.status === "inactive" && (
									<Badge
										variant="secondary"
										className="gap-1 border-muted-foreground/30 text-muted-foreground"
										data-testid="status-inactive-badge"
									>
										<PowerOff className="h-3 w-3" />
										Inactive
									</Badge>
								)}
								{sol.version && (
									<Badge variant="outline">v{sol.version}</Badge>
								)}
								<Badge
									variant={sol.organization_id ? "outline" : "default"}
									className="gap-1"
								>
									{sol.organization_id ? (
										<Building2 className="h-3 w-3" />
									) : (
										<Globe className="h-3 w-3" />
									)}
									{orgName}
								</Badge>
								<Badge variant="secondary" className="gap-1">
									{sol.git_connected ? (
										<GitBranch className="h-3 w-3" />
									) : (
										<HardDriveUpload className="h-3 w-3" />
									)}
									{sol.git_connected ? "Git-connected" : "Manual"}
								</Badge>
								{sol.update_available_version && (
									<Badge
										variant="default"
										className="gap-1"
										data-testid="update-available-badge"
									>
										<ArrowUp className="h-3 w-3" />
										Update available · v{sol.update_available_version}
									</Badge>
								)}
							</div>
							{sol.git_connected && sol.git_repo_url && (
								<p
									className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground"
									data-testid="git-provenance"
								>
									<GitBranch className="h-3 w-3 shrink-0" />
									<span className="font-mono break-all">
										{sol.git_repo_url}
										{sol.repo_subpath ? ` /${sol.repo_subpath}` : ""}
										{sol.git_ref ? ` @ ${sol.git_ref}` : ""}
									</span>
								</p>
							)}
						</div>
						<div className="flex shrink-0 items-center justify-end gap-2">
							{sol.setup_complete === false && sol.status !== "inactive" && (
								<Button
									data-testid="continue-setup"
									variant="outline"
									className="whitespace-nowrap border-yellow-500/60 text-yellow-700 hover:text-yellow-700 dark:text-yellow-400"
									onClick={() => setTab("configuration")}
								>
									<AlertTriangle className="mr-1.5 h-4 w-4" />
									Continue Setup
								</Button>
							)}
							{sol.status === "inactive" ? (
								<Button
									data-testid="reactivate-solution"
									variant="outline"
									className="whitespace-nowrap"
									onClick={() => setUpdateOpen(true)}
								>
									<RotateCcw className="mr-1.5 h-4 w-4" />
									Reactivate
								</Button>
							) : sol.git_connected && sol.update_available_version ? (
								<Button
									data-testid="update-now"
									className="whitespace-nowrap"
									disabled={syncMut.isPending}
									onClick={() => setSyncConfirmOpen(true)}
								>
									{syncMut.isPending ? (
										<Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
									) : (
										<ArrowUp className="mr-1.5 h-4 w-4" />
									)}
									Update now
								</Button>
							) : (
								<Button
									data-testid="update-solution"
									className="whitespace-nowrap"
									onClick={() => setUpdateOpen(true)}
								>
									<Upload className="mr-1.5 h-4 w-4" />
									Update
								</Button>
							)}
							<SolutionActionsMenu
								exporting={exportMut.isPending}
								isInactive={sol.status === "inactive"}
								onCapture={() => setCaptureOpen(true)}
								onExport={() => setExportDialogOpen(true)}
								onEdit={() => setEditOpen(true)}
								onUninstall={() => uninstallMut.mutate()}
								onHardDelete={openHardDelete}
							/>
						</div>
					</div>

					{/* Setup-incomplete banner (Setup is a STATE, not a tab) — deep-links
					    to Configuration where the required values are entered. */}
					{requiredUnset.length > 0 && (
						<div
							data-testid="required-config-warning"
							className="flex items-center justify-between gap-3 rounded-lg border border-yellow-500/60 bg-yellow-500/10 px-4 py-3"
						>
							<div className="flex items-center gap-2 text-sm">
								<AlertTriangle className="h-4 w-4 text-yellow-600 dark:text-yellow-500" />
								<span>
									Setup incomplete — {requiredUnset.length} required config
									{requiredUnset.length === 1 ? "" : "s"} need
									{requiredUnset.length === 1 ? "s" : ""} a value
									before this Solution can run.
								</span>
							</div>
							<Button
								size="sm"
								variant="outline"
								onClick={() => setTab("configuration")}
							>
								Fix
							</Button>
						</div>
					)}

					{/* Tabs (3): Overview · Contents · Configuration */}
					<Tabs
						value={tab}
						onValueChange={(v) => setTab(v as TabKey)}
						className="flex-1 min-h-0 flex flex-col"
					>
						<TabsList className="self-start">
							<TabsTrigger
								value="overview"
								data-testid="tab-overview"
								className="gap-1.5"
							>
								<FileText className="h-4 w-4" />
								Overview
							</TabsTrigger>
							<TabsTrigger
								value="contents"
								data-testid="tab-contents"
								className="gap-1.5"
							>
								<LayoutGrid className="h-4 w-4" />
								Contents
								<span className="ml-1 text-xs text-muted-foreground">
									{totalContents}
								</span>
							</TabsTrigger>
							<TabsTrigger
								value="configuration"
								data-testid="tab-configuration"
								className="gap-1.5"
							>
								<SlidersHorizontal className="h-4 w-4" />
								Configuration
								{sol.setup_complete === false ? (
									<AlertTriangle
										data-testid="config-tab-warning"
										className="ml-0.5 h-3.5 w-3.5 text-yellow-500"
									/>
								) : (
									configsCount > 0 && (
										<span className="ml-1 text-xs text-muted-foreground">
											{configsCount}
										</span>
									)
								)}
							</TabsTrigger>
						</TabsList>

						{/* OVERVIEW — README leads (it's the description, not a section),
						    with a status/contents summary so it's never empty. */}
						<TabsContent value="overview" className="flex-1 min-h-0">
							<OverviewTab
								readme={readmeData?.readme ?? null}
								entityCounts={entityCounts}
								configsCount={configsCount}
								version={sol.version ?? null}
								gitConnected={sol.git_connected}
								orgName={orgName}
								onGoToContents={() => setTab("contents")}
							/>
						</TabsContent>

						{/* CONTENTS — the 6 entity inventories as one tab with type chips. */}
						<TabsContent value="contents" className="flex-1 min-h-0 flex flex-col">
							<div className="mb-3 flex flex-wrap gap-2" data-testid="contents-chips">
								<button
									type="button"
									data-testid="chip-all"
									onClick={() => setContentsFilter("all")}
									className={chipClass(contentsFilter === "all")}
								>
									All
									<span className="ml-1.5 text-xs opacity-70">
										{totalContents}
									</span>
								</button>
								{ENTITY_TABS.map(({ key, label, Icon }) => (
									<button
										type="button"
										key={key}
										data-testid={`chip-${key}`}
										onClick={() => setContentsFilter(key)}
										className={chipClass(contentsFilter === key)}
									>
										<Icon className="h-3.5 w-3.5" />
										{label}
										<span className="ml-1.5 text-xs opacity-70">
											{entityCounts[key]}
										</span>
									</button>
								))}
							</div>
							<div className="flex-1 min-h-0 overflow-auto">
								{activeKind ? (
									<EntityTabContent
										kind={activeKind}
										items={itemsFor(activeKind)}
										solutionId={sol.id}
										fileCount={entityCounts.files}
									/>
								) : (
									<ContentsSummary
										entityCounts={entityCounts}
										onPick={(k) => setContentsFilter(k)}
									/>
								)}
							</div>
						</TabsContent>

						{/* CONFIGURATION — config VALUES + integration connections; the
						    permanent home of what was the one-time Setup wizard. */}
						<TabsContent
							value="configuration"
							className="flex-1 min-h-0 overflow-auto"
						>
							<ConfigurationTab
								configs={data.configs ?? []}
								orgId={sol.organization_id ?? null}
								setupItems={setupData?.items ?? []}
								setupComplete={setupData?.setup_complete ?? sol.setup_complete}
								setupError={setupError}
								onInvalidate={invalidate}
								onSetConfig={async (key, value) => {
									try {
										await setSolutionConfig({
											key,
											value,
											type: asConfigType(
												setupData?.items.find((i) => i.key === key)?.type ??
													"string",
											),
											organizationId: sol.organization_id ?? null,
										});
										toast.success(`Set ${key}`);
										invalidate();
									} catch (err: unknown) {
										toast.error(`Failed to set ${key}`, {
											description:
												err instanceof Error ? err.message : undefined,
										});
									}
								}}
							/>
						</TabsContent>
					</Tabs>

					{editOpen && (
						<CreateEditSolution
							mode={{ kind: "edit", solution: sol }}
							open
							onClose={() => setEditOpen(false)}
							onSaved={() => {
								setEditOpen(false);
								invalidate();
							}}
						/>
					)}

					{updateOpen && (
						<CreateEditSolution
							mode={{
								kind: "create",
								organizationId: sol.organization_id ?? null,
								intent: "update",
							}}
							open
							onClose={() => setUpdateOpen(false)}
							onSaved={() => {
								setUpdateOpen(false);
								invalidate();
							}}
						/>
					)}

					{/* "Update now" confirm (git-connected pull + full-replace) */}
					<AlertDialog
						open={syncConfirmOpen}
						onOpenChange={(o) => !syncMut.isPending && setSyncConfirmOpen(o)}
					>
						<AlertDialogContent data-testid="update-now-dialog">
							<AlertDialogHeader>
								<AlertDialogTitle>
									Update {sol.name}
									{sol.update_available_version
										? ` to v${sol.update_available_version}`
										: ""}
									?
								</AlertDialogTitle>
								<AlertDialogDescription>
									Pull and redeploy this install from its repository. This
									replaces the installed content with the repo's current
									version.
								</AlertDialogDescription>
							</AlertDialogHeader>
							<div className="max-h-[40vh] overflow-y-auto" data-testid="update-diff">
								{updateDiffQuery.isLoading ? (
									<p className="flex items-center gap-2 text-sm text-muted-foreground">
										<Loader2 className="h-4 w-4 animate-spin" />
										Computing what will change…
									</p>
								) : updateDiffQuery.isError ? (
									<p className="text-sm text-muted-foreground">
										Could not preview changes; the update will still apply
										the repo's current version.
									</p>
								) : updateDiffQuery.data?.diff ? (
									<UpgradeDiffView diff={updateDiffQuery.data.diff} />
								) : null}
							</div>
							<AlertDialogFooter>
								<AlertDialogCancel disabled={syncMut.isPending}>
									Cancel
								</AlertDialogCancel>
								<AlertDialogAction
									data-testid="confirm-update-now"
									disabled={syncMut.isPending}
									onClick={(e) => {
										e.preventDefault();
										syncMut.mutate();
									}}
								>
									{syncMut.isPending && (
										<Loader2 className="mr-2 h-4 w-4 animate-spin" />
									)}
									<RefreshCw className="mr-1.5 h-4 w-4" />
									Update now
								</AlertDialogAction>
							</AlertDialogFooter>
						</AlertDialogContent>
					</AlertDialog>

					<SolutionCaptureDialog
						open={captureOpen}
						solutionId={sol.id}
						onClose={() => setCaptureOpen(false)}
						onCaptured={invalidate}
					/>

					{/* Export mode picker dialog */}
					<ExportSolutionDialog
						open={exportDialogOpen}
						onOpenChange={setExportDialogOpen}
						onExport={(mode, password, options) =>
							exportMut.mutate({ mode, password, options })
						}
						isPending={exportMut.isPending}
					/>

					{/* Hard-delete confirmation modal (type-the-slug to confirm) */}
					<Dialog
						open={hardDeleteOpen}
						onOpenChange={(o) => {
							if (!o && !hardDeleteMut.isPending) {
								setHardDeleteOpen(false);
								setHardDeleteConfirm("");
							}
						}}
					>
						<DialogContent data-testid="hard-delete-dialog">
							<DialogHeader>
								<DialogTitle className="flex items-center gap-2">
									<Trash2 className="h-4 w-4 text-destructive" />
									Permanently delete {sol.name}?
								</DialogTitle>
								<DialogDescription asChild>
									<div className="space-y-2 text-sm text-muted-foreground">
										<p>
											This is{" "}
											<span className="font-medium text-foreground">
												irreversible
											</span>
											. All owned entities and files will be
											permanently destroyed.
										</p>
										{deletionSummaryLoading && (
											<p className="flex items-center gap-2">
												<Loader2 className="h-4 w-4 animate-spin" />
												Loading what will be deleted…
											</p>
										)}
										{deletionSummary && !deletionSummaryLoading && (
											<ul
												className="list-disc pl-4 text-foreground"
												data-testid="deletion-summary-list"
											>
												{deletionSummary.files > 0 && (
													<li>
														{deletionSummary.files} file
														{deletionSummary.files !== 1 ? "s" : ""}
													</li>
												)}
												{deletionSummary.tables > 0 && (
													<li>
														{deletionSummary.tables} table
														{deletionSummary.tables !== 1 ? "s" : ""}
													</li>
												)}
												{deletionSummary.workflows > 0 && (
													<li>
														{deletionSummary.workflows} workflow
														{deletionSummary.workflows !== 1 ? "s" : ""}
													</li>
												)}
												{deletionSummary.apps > 0 && (
													<li>
														{deletionSummary.apps} app
														{deletionSummary.apps !== 1 ? "s" : ""}
													</li>
												)}
												{deletionSummary.forms > 0 && (
													<li>
														{deletionSummary.forms} form
														{deletionSummary.forms !== 1 ? "s" : ""}
													</li>
												)}
												{deletionSummary.agents > 0 && (
													<li>
														{deletionSummary.agents} agent
														{deletionSummary.agents !== 1 ? "s" : ""}
													</li>
												)}
												{deletionSummary.claims > 0 && (
													<li>
														{deletionSummary.claims} custom claim
														{deletionSummary.claims !== 1 ? "s" : ""}
													</li>
												)}
												{deletionSummary.events > 0 && (
													<li>
														{deletionSummary.events} event
														{deletionSummary.events !== 1 ? "s" : ""}
													</li>
												)}
												{deletionSummary.config_declarations > 0 && (
													<li>
														{deletionSummary.config_declarations} config
														declaration
														{deletionSummary.config_declarations !== 1
															? "s"
															: ""}
													</li>
												)}
											</ul>
										)}
									</div>
								</DialogDescription>
							</DialogHeader>

							<div className="space-y-2">
								<Label htmlFor="hard-delete-confirm">
									Type{" "}
									<span className="font-mono font-semibold text-foreground">
										{sol.slug}
									</span>{" "}
									to confirm
								</Label>
								<Input
									id="hard-delete-confirm"
									data-testid="hard-delete-confirm-input"
									value={hardDeleteConfirm}
									onChange={(e) => setHardDeleteConfirm(e.target.value)}
									autoComplete="off"
									placeholder={sol.slug}
								/>
							</div>

							<DialogFooter>
								<Button
									variant="outline"
									disabled={hardDeleteMut.isPending}
									onClick={() => {
										setHardDeleteOpen(false);
										setHardDeleteConfirm("");
									}}
								>
									Cancel
								</Button>
								<Button
									variant="destructive"
									data-testid="confirm-hard-delete"
									disabled={
										hardDeleteConfirm !== sol.slug ||
										hardDeleteMut.isPending
									}
									onClick={() => hardDeleteMut.mutate()}
								>
									{hardDeleteMut.isPending && (
										<Loader2 className="mr-2 h-4 w-4 animate-spin" />
									)}
									<Trash2 className="mr-1.5 h-4 w-4" />
									Delete permanently
								</Button>
							</DialogFooter>
						</DialogContent>
					</Dialog>
				</>
			) : null}
		</div>
	);
}
