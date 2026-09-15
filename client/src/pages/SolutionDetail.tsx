import { SolutionUninstallNotice } from "@/components/solutions/SolutionUninstallNotice";
import { SolutionDeleteDialog } from "@/components/solutions/SolutionDeleteDialog";
/**
 * Solution Detail Page
 *
 * RoleDetail-style tabbed view for a single Solution install: breadcrumb,
 * header with scope/source chips + Edit/Delete actions, a required-config
 * warning banner, and per-entity tabs (Workflows / Apps / Forms / Agents /
 * Tables / Configs). The Configs tab doubles as the config-value entry
 * surface — required inputs an install needs before it can run.
 */

import { useId, useMemo, useRef, useState } from "react";
import { Progress } from "@/components/ui/progress";
import {
	PageScrollArea,
	PageWorkspace,
} from "@/components/layout/PageWorkspace";
import { GeneratedEndpointKeyDialog } from "@/components/solutions/GeneratedEndpointKeyDialog";
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
	ArrowUp,
	PowerOff,
	RotateCcw,
	Archive,
	Download,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
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
import { ApplicationSdkStatusBadge } from "@/components/applications/ApplicationSdkStatusBadge";
import {
	FormListSurface,
	type FormListItem,
	type FormValidationState,
} from "@/components/forms/FormListSurface";
import { FormShareDialog } from "@/components/forms/FormShareDialog";
import {
	WorkflowListSurface,
	type WorkflowListItem,
} from "@/components/workflows/WorkflowListSurface";
import { Input } from "@/components/ui/input";

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
	Sheet,
	SheetContent,
	SheetDescription,
	SheetHeader,
	SheetTitle,
} from "@/components/ui/sheet";

import { useOrganizations } from "@/hooks/useOrganizations";
import { CreateEditSolution } from "@/components/solutions/CreateEditSolution";
import { SolutionCaptureDialog } from "@/components/solutions/SolutionCaptureDialog";
import { SolutionActionsMenu } from "@/components/solutions/SolutionActionsMenu";
import { ExportSolutionDialog } from "@/components/solutions/ExportSolutionDialog";
import { FilesExplorer } from "@/components/files/FilesExplorer";
import { EditUserDialog } from "@/components/users/EditUserDialog";
import {
	getSolutionEntities,
	getSolutionSetup,
	getSolutionReadme,
	deleteSolution,
	uninstallSolution,
	exportSolution,
	createSolutionExportJob,
	listSolutionExportJobs,
	downloadSolutionExportJob,
	getSolutionSdkStatus,
	updateSolutionAppSdks,
	type SolutionExportOptions,
	type SolutionExportJob,
	type SolutionSdkStatus,
	setSolutionConfig,
	syncSolution,
	previewSolutionFromRepo,
} from "@/services/solutions";
import { SolutionUpdateDialog } from "@/components/solutions/SolutionUpdateDialog";
import { SolutionSetupWizard } from "@/components/solutions/SolutionSetupWizard";
import { SolutionReadmeTab } from "@/components/solutions/SolutionReadmeTab";
import { workflowKeysService } from "@/services/workflowKeys";
import { useUser } from "@/hooks/useUsers";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { useApplicationSdkUpdateJobs } from "@/hooks/useApplicationSdkUpdateJobs";
import { useUpdateApplicationSdk } from "@/hooks/useApplications";
import type { components } from "@/lib/v1";

type EntitySummary = components["schemas"]["SolutionEntitySummary"];
type ConfigStatus = components["schemas"]["SolutionConfigStatus"];
type ConfigType = components["schemas"]["ConfigType"];

type AccessUserSummary = {
	id: string;
	name?: string | null;
	email: string;
};
type AccessRoleSummary = {
	id: string;
	name: string;
};
type AccessEntitySummary = EntitySummary & {
	role_ids?: string[];
	role_names?: string[];
	access_users?: AccessUserSummary[];
};

/** The three top-level tabs (down from 9). README leads Overview (it's the
 * description, not a section); the 6 entity inventories collapse into Contents
 * (type chips); config VALUES + integration connections live in Configuration;
 * Setup is no longer a tab — it's a STATE surfaced as an Overview banner + a
 * Configuration badge. */
type TabKey = "overview" | "contents" | "access" | "configuration" | "exports";

/** The entity kinds shown inside the Contents tab (the old per-entity tabs). */
type EntityKind =
	"workflows" | "apps" | "forms" | "agents" | "tables" | "claims" | "files";

/** Contents type-chip selection: a specific kind or the combined summary. */
type ContentsFilter = "all" | EntityKind;

type AccessRow = {
	id: string;
	kind: EntityKind;
	name: string;
	description?: string | null;
	accessMode: string;
	roles: AccessRoleSummary[];
	users: AccessUserSummary[];
	path?: string | null;
	functionName?: string | null;
};

const ENTITY_TABS: {
	key: EntityKind;
	label: string;
	Icon: typeof Workflow;
	iconClassName: string;
}[] = [
	{
		key: "workflows",
		label: "Workflows",
		Icon: Workflow,
		iconClassName: "text-blue-600 dark:text-blue-300",
	},
	{
		key: "apps",
		label: "Apps",
		Icon: AppWindow,
		iconClassName: "text-indigo-600 dark:text-indigo-300",
	},
	{
		key: "forms",
		label: "Forms",
		Icon: FileCode,
		iconClassName: "text-emerald-600 dark:text-emerald-300",
	},
	{
		key: "agents",
		label: "Agents",
		Icon: Bot,
		iconClassName: "text-violet-600 dark:text-violet-300",
	},
	{
		key: "tables",
		label: "Tables",
		Icon: Database,
		iconClassName: "text-amber-600 dark:text-amber-300",
	},
	{
		key: "claims",
		label: "Custom Claims",
		Icon: KeyRound,
		iconClassName: "text-rose-600 dark:text-rose-300",
	},
	{
		key: "files",
		label: "Files",
		Icon: FolderOpen,
		iconClassName: "text-cyan-600 dark:text-cyan-300",
	},
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

const ENTITY_KIND_LABEL: Record<EntityKind, string> = {
	workflows: "Workflow",
	apps: "App",
	forms: "Form",
	agents: "Agent",
	tables: "Table",
	claims: "Claim",
	files: "File",
};

const ACCESS_BADGE_CLASS: Record<string, string> = {
	"Role based": "border-border bg-muted/40 text-foreground",
	Authenticated: "border-border bg-muted/40 text-foreground",
	Everyone: "border-border bg-muted/40 text-foreground",
	"Not exposed": "border-border bg-muted/40 text-muted-foreground",
};

function readableAccessMode(entity: EntitySummary): string {
	if (!entity.access_level) return "Not exposed";
	return entity.access_level
		.replace(/_/g, " ")
		.replace(/^\w/, (letter) => letter.toUpperCase());
}

function userLabel(user: AccessUserSummary): string {
	return user.name?.trim() || user.email;
}

function userInitials(user: AccessUserSummary): string {
	const label = userLabel(user);
	const parts = label.split(/\s+/).filter(Boolean);
	if (parts.length >= 2) return `${parts[0][0]}${parts[1][0]}`.toUpperCase();
	return label.slice(0, 2).toUpperCase();
}

function userSummary(row: AccessRow): string {
	if (row.accessMode === "Everyone") return "Everyone";
	if (row.accessMode === "Authenticated") return "All signed-in users";
	if (row.users.length === 0) return "No users assigned";
	return `${row.users.length} user${row.users.length === 1 ? "" : "s"}`;
}

function roleSummaries(item: AccessEntitySummary): AccessRoleSummary[] {
	return (item.role_names ?? []).map((name, index) => ({
		id: item.role_ids?.[index] ?? name,
		name,
	}));
}

function buildEntityAccessRows(
	kind: "workflows" | "apps" | "forms" | "agents",
	items: AccessEntitySummary[],
): AccessRow[] {
	return items.map((item) => ({
		id: `${kind}:${item.id}`,
		kind,
		name: item.name,
		description: item.description,
		accessMode: readableAccessMode(item),
		roles: roleSummaries(item),
		users: item.access_users ?? [],
		path: item.path,
		functionName: item.function_name,
	}));
}

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

function formatDateTime(value: string | null | undefined): string {
	if (!value) return "-";
	return new Date(value).toLocaleString(undefined, {
		year: "numeric",
		month: "short",
		day: "numeric",
		hour: "numeric",
		minute: "2-digit",
	});
}

function formatBytes(value: number | null | undefined): string {
	if (!value) return "-";
	const units = ["B", "KB", "MB", "GB"];
	let size = value;
	let unit = 0;
	while (size >= 1024 && unit < units.length - 1) {
		size /= 1024;
		unit += 1;
	}
	return `${size >= 10 || unit === 0 ? size.toFixed(0) : size.toFixed(1)} ${units[unit]}`;
}

function downloadBlob(blob: Blob, filename: string) {
	const url = URL.createObjectURL(blob);
	const a = document.createElement("a");
	a.href = url;
	a.download = filename;
	document.body.appendChild(a);
	a.click();
	a.remove();
	URL.revokeObjectURL(url);
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
	return (
		<div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
			{items.map((entity) => {
				const href = entityHref(kind, entity, solutionId);
				const status = entityStatus(entity, kind);
				if (kind === "apps") {
					return (
						<div
							key={entity.id}
							className="group relative flex flex-col overflow-hidden rounded-[var(--bf-radius-surface)] border border-border/70 bg-card transition-colors duration-[var(--bf-motion-feedback)] hover:bg-accent/40 motion-reduce:transition-none"
						>
							<div className="border-b px-4 py-3">
								<div className="flex items-start justify-between gap-3">
									<div className="flex min-w-0 items-center gap-2">
										<EntityLogo
											entityType="app"
											entityId={entity.id}
											logo={entity.logo_url ?? null}
											fallback={
												<AppWindow className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
											}
											size={20}
											className="h-5 w-5 rounded object-cover shrink-0"
										/>
										<>
											<Link
												to={href}
												aria-label={entity.name}
												className="absolute inset-0 z-[1] rounded-[var(--bf-radius-surface)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
											/>
											<span className="truncate text-[14.5px] font-semibold">
												{entity.name}
											</span>
										</>
									</div>
									<Badge
										variant="outline"
										className="text-[10px] px-1.5 py-0"
									>
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
								<Badge
									variant="default"
									className="text-[10px] px-1.5 py-0"
								>
									Managed
								</Badge>
							</div>
						</div>
					);
				}

				if (kind === "agents") {
					return (
						<Link
							key={entity.id}
							to={href}
							className="group flex min-w-0 flex-col rounded-[var(--bf-radius-surface)] border border-border/70 bg-card transition-colors duration-[var(--bf-motion-feedback)] hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring motion-reduce:transition-none"
						>
							<div className="border-b px-4 pb-3 pt-3.5">
								<div className="flex items-start justify-between gap-3">
									<div className="flex min-w-0 flex-wrap items-center gap-2">
										<EntityLogo
											entityType="agent"
											entityId={entity.id}
											logo={entity.logo_url ?? null}
											fallback={
												<Bot className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
											}
											size={20}
											className="h-5 w-5 rounded shrink-0 object-cover"
										/>
										<span className="text-sm font-semibold [overflow-wrap:anywhere]">
											{entity.name}
										</span>
										{status && (
											<Badge
												variant="outline"
												className="text-[11px]"
											>
												{status}
											</Badge>
										)}
									</div>
								</div>
								{entity.description && (
									<p className="mt-2 text-sm text-muted-foreground [overflow-wrap:anywhere]">
										{entity.description}
									</p>
								)}
							</div>
							<div className="flex flex-1 flex-wrap items-center justify-between gap-2 p-4 text-xs text-muted-foreground">
								<span>
									{readableAccessMode({
										...entity,
										access_level:
											entity.access_level ??
											"authenticated",
									})}
								</span>
								<span>{entity.type ?? "Agent"}</span>
							</div>
						</Link>
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
							</div>
							<CardTitle
								className={
									kind === "workflows" ||
									kind === "tables" ||
									kind === "claims"
										? "font-mono text-base [overflow-wrap:anywhere]"
										: "text-base [overflow-wrap:anywhere]"
								}
							>
								<>
									<Link
										to={href}
										aria-label={entity.name}
										className="absolute inset-0 z-[1] rounded-[var(--bf-radius-surface)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
									/>
									<span className="">{entity.name}</span>
								</>
							</CardTitle>
							{entity.description && (
								<CardDescription className="mt-2 text-sm [overflow-wrap:anywhere]">
									{entity.description}
								</CardDescription>
							)}
						</CardHeader>
						<CardContent className="pt-0 mt-auto space-y-3">
							<div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground [overflow-wrap:anywhere]">
								{entity.category && (
									<span>{entity.category}</span>
								)}
								{entity.category && <span>·</span>}
								{kind === "workflows" && (
									<span>{sourceRef(entity)}</span>
								)}
								{kind === "forms" &&
									accessBadge(entity.access_level)}
								{kind === "tables" && (
									<span>{formatDate(entity.created_at)}</span>
								)}
								{kind === "claims" && (
									<dl className="w-full space-y-3">
										<div>
											<dt className="mb-1">
												Source table
											</dt>
											<dd className="font-mono text-foreground [overflow-wrap:anywhere]">
												{entity.source_table ?? "-"}
											</dd>
										</div>
										<div>
											<dt className="mb-1">Select</dt>
											<dd className="font-mono text-foreground [overflow-wrap:anywhere]">
												{entity.select ?? "*"}
											</dd>
										</div>
									</dl>
								)}
							</div>
							{status && (
								<div className="flex flex-wrap items-center gap-1.5">
									<Badge
										variant={
											status === "Active"
												? "default"
												: "secondary"
										}
									>
										{status}
									</Badge>
								</div>
							)}
							{kind === "workflows" &&
								entity.type === "data_provider" && (
									<div className="flex flex-wrap items-center gap-1.5">
										<Badge variant="outline">
											<Database className="mr-1 h-3 w-3" />
											Data provider
										</Badge>
									</div>
								)}
							{kind === "tables" && entity.source_table && (
								<dl className="text-xs">
									<dt className="mb-1 text-muted-foreground">
										Source table
									</dt>
									<dd className="font-mono [overflow-wrap:anywhere]">
										{entity.source_table}
									</dd>
								</dl>
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
							<DataTableHead className="w-0 whitespace-nowrap">
								Type
							</DataTableHead>
							<DataTableHead className="w-0 whitespace-nowrap">
								Category
							</DataTableHead>
							<DataTableHead className="w-0 whitespace-nowrap">
								Source
							</DataTableHead>
						</>
					)}
					{kind === "apps" && (
						<>
							<DataTableHead className="w-0 whitespace-nowrap">
								Model
							</DataTableHead>
							<DataTableHead className="w-0 whitespace-nowrap">
								Source
							</DataTableHead>
						</>
					)}
					{kind === "forms" && (
						<>
							<DataTableHead className="w-0 whitespace-nowrap">
								Access
							</DataTableHead>
							<DataTableHead className="w-0 whitespace-nowrap">
								Status
							</DataTableHead>
						</>
					)}
					{kind === "agents" && (
						<>
							<DataTableHead className="w-0 whitespace-nowrap">
								Access
							</DataTableHead>
							<DataTableHead className="w-0 whitespace-nowrap">
								Status
							</DataTableHead>
						</>
					)}
					{kind === "tables" && (
						<DataTableHead className="w-0 whitespace-nowrap">
							Created
						</DataTableHead>
					)}
					{kind === "claims" && (
						<>
							<DataTableHead className="w-0 whitespace-nowrap">
								Type
							</DataTableHead>
							<DataTableHead className="w-0 whitespace-nowrap">
								Source table
							</DataTableHead>
							<DataTableHead className="w-0 whitespace-nowrap">
								Select
							</DataTableHead>
						</>
					)}
				</DataTableRow>
			</DataTableHeader>
			<DataTableBody>
				{items.map((entity) => {
					const status = entityStatus(entity, kind);
					const href = entityHref(kind, entity, solutionId);
					return (
						<DataTableRow
							key={entity.id}
							clickable
							href={href}
							onClick={() => navigate(href)}
						>
							<DataTableCell
								className={
									kind === "workflows" ||
									kind === "tables" ||
									kind === "claims"
										? "font-mono font-medium"
										: "font-medium"
								}
							>
								<span className="flex items-center gap-2">
									{kind === "apps" && (
										<EntityLogo
											entityType="app"
											entityId={entity.id}
											logo={entity.logo_url ?? null}
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
											logo={entity.logo_url ?? null}
											fallback={
												<Bot className="h-4 w-4 shrink-0 text-muted-foreground" />
											}
											size={18}
											className="h-[18px] w-[18px] rounded object-cover shrink-0"
										/>
									)}
									<Link
										to={href}
										onClick={(event) =>
											event.stopPropagation()
										}
										className="inline-flex min-h-11 items-center rounded-[var(--bf-radius-control)] [overflow-wrap:anywhere] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
									>
										{entity.name}
									</Link>
								</span>
							</DataTableCell>
							<DataTableCell className="max-w-xs whitespace-normal [overflow-wrap:anywhere] text-muted-foreground">
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
										<Badge variant="outline">
											{entity.app_model ?? "-"}
										</Badge>
									</DataTableCell>
									<DataTableCell className="w-0 max-w-[18rem] truncate font-mono text-xs text-muted-foreground">
										{sourceRef(entity)}
									</DataTableCell>
								</>
							)}
							{kind === "forms" && (
								<>
									<DataTableCell className="w-0 whitespace-nowrap">
										<Badge variant="outline">
											{entity.access_level ?? "-"}
										</Badge>
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap">
										<Badge
											variant={
												status === "Inactive"
													? "secondary"
													: "default"
											}
										>
											{status}
										</Badge>
									</DataTableCell>
								</>
							)}
							{kind === "agents" && (
								<>
									<DataTableCell className="w-0 whitespace-nowrap">
										<Badge variant="outline">
											{entity.access_level ?? "-"}
										</Badge>
									</DataTableCell>
									<DataTableCell className="w-0 whitespace-nowrap">
										<Badge
											variant={
												status === "Paused"
													? "secondary"
													: "default"
											}
										>
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
	solutionName,
	fileCount,
	sdkStatus,
}: {
	kind: EntityKind;
	items: EntitySummary[];
	solutionId: string;
	solutionName: string;
	/** Actual file count from SolutionEntities.files (files kind only). */
	fileCount?: number;
	sdkStatus?: SolutionSdkStatus;
}) {
	const navigate = useNavigate();
	const isMobile = useMediaQuery("(max-width: 1023px)");
	const updateApplicationSdk = useUpdateApplicationSdk();
	const sdkUpdateJobs = useApplicationSdkUpdateJobs({ solutionId });
	const [search, setSearch] = useState("");
	const [shareForm, setShareForm] = useState<{
		id: string;
		name: string;
	} | null>(null);
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
	const solutionAppSdkStatus = new Map(
		(sdkStatus?.apps ?? []).map((entry) => [entry.application_id, entry]),
	);
	const managedAppVisible = managedVisible.map((entity) => {
		const appSdk = solutionAppSdkStatus.get(entity.id);
		return appSdk
			? {
					...entity,
					sdk_status: appSdk.sdk_status,
					sdk_source_available: appSdk.sdk_source_available,
				}
			: entity;
	});
	const formValidation = new Map<string, FormValidationState>(
		visible.map((entity) => [
			entity.id,
			{ valid: true, missingParams: [] },
		]),
	);

	if (kind === "files") {
		const count = fileCount ?? 0;
		if (count === 0) {
			return (
				<div className="text-sm text-muted-foreground py-8 text-center rounded-[var(--bf-radius-surface)] border border-dashed">
					This Solution has no files.
				</div>
			);
		}
		return (
			<div
				className="h-[min(72dvh,48rem)] min-h-96"
				data-testid="solution-files-tab"
			>
				<FilesExplorer
					install={solutionId}
					installName={solutionName}
					embedded
				/>
			</div>
		);
	}

	if (items.length === 0) {
		return (
			<div className="text-sm text-muted-foreground py-8 text-center rounded-[var(--bf-radius-surface)] border border-dashed">
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
				{canToggleView && !isMobile && (
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
							className="h-11 w-11"
						>
							<LayoutGrid className="h-4 w-4" />
						</ToggleGroupItem>
						<ToggleGroupItem
							value="table"
							aria-label="Table view"
							className="h-11 w-11"
						>
							<TableIcon className="h-4 w-4" />
						</ToggleGroupItem>
					</ToggleGroup>
				)}
			</div>
			{visible.length === 0 ? (
				<div className="text-sm text-muted-foreground py-8 text-center rounded-[var(--bf-radius-surface)] border border-dashed">
					No {ENTITY_TAB_LABEL[kind]} match “{search.trim()}”.
				</div>
			) : kind === "workflows" ? (
				<WorkflowListSurface
					navigationSearch={`?from=solution:${solutionId}`}
					workflows={managedVisible as WorkflowListItem[]}
					viewMode={isMobile ? "grid" : viewMode}
					isPlatformAdmin={false}
					canManageWorkflows={true}
					getOrgName={() => "Solution"}
					onExecute={(workflow) =>
						navigate(
							`/workflows/${encodeURIComponent(workflow.name ?? "")}/execute?from=solution:${solutionId}`,
						)
					}
					emptySearchActive={Boolean(search.trim())}
				/>
			) : kind === "apps" ? (
				<ApplicationListSurface
					navigationSearch={`?from=solution:${solutionId}`}
					apps={managedAppVisible as ApplicationListItem[]}
					viewMode={isMobile ? "grid" : viewMode}
					isPlatformAdmin={false}
					canManageApps={true}
					getOrgName={() => "Solution"}
					onLaunch={(app) =>
						navigate(
							`/apps/${app.slug ?? app.id}?from=solution:${solutionId}`,
						)
					}
					onPreview={(app) =>
						navigate(
							`/apps/${app.slug ?? app.id}/preview?from=solution:${solutionId}`,
						)
					}
					onUpdateSdk={(app) => {
						void updateApplicationSdk
							.mutateAsync({
								params: { path: { app_id: app.id } },
							})
							.then((operation) =>
								sdkUpdateJobs.trackAccepted([
									{
										...operation,
										application_id: app.id,
									},
								]),
							)
							.catch(() => {
								// useUpdateApplicationSdk owns the user-facing error toast.
							});
					}}
					getSdkUpdateState={(app) =>
						sdkUpdateJobs.getUpdateState(app.id)
					}
					emptySearchActive={Boolean(search.trim())}
				/>
			) : kind === "forms" ? (
				<>
					<FormListSurface
						navigationSearch={`?from=solution:${solutionId}`}
						forms={managedVisible as FormListItem[]}
						viewMode={isMobile ? "grid" : viewMode}
						isPlatformAdmin={false}
						canManageForms={true}
						getOrgName={() => "Solution"}
						formValidation={formValidation}
						onLaunch={(form) =>
							navigate(
								`/execute/${form.id}?from=solution:${solutionId}`,
							)
						}
						onShare={(form) =>
							setShareForm({ id: form.id, name: form.name })
						}
						emptySearchActive={Boolean(search.trim())}
					/>
					{shareForm ? (
						<FormShareDialog
							formId={shareForm.id}
							formName={shareForm.name}
							open
							onOpenChange={(open) => !open && setShareForm(null)}
						/>
					) : null}
				</>
			) : isMobile || viewMode === "grid" ? (
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
	const inputId = useId();
	const savingRef = useRef(false);
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
		onSettled: () => {
			savingRef.current = false;
		},
	});

	const requiredUnset = config.required && !config.value_set;

	return (
		<form
			onSubmit={(event) => {
				event.preventDefault();
				if (!value.trim() || savingRef.current) return;
				savingRef.current = true;
				saveMut.mutate();
			}}
			className={
				"min-w-0 rounded-[var(--bf-radius-surface)] border p-4 " +
				(requiredUnset
					? "border-[var(--bf-warning)]/40 bg-[var(--bf-warning)]/5"
					: "")
			}
		>
			<div className="flex flex-wrap items-start justify-between gap-3">
				<div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
					<label
						htmlFor={inputId}
						className="w-full font-mono text-sm font-medium [overflow-wrap:anywhere]"
					>
						{config.key}
					</label>
					<Badge variant="outline" className="shrink-0 text-[10px]">
						{config.type}
					</Badge>
					{config.required && (
						<span className="shrink-0 text-xs text-muted-foreground">
							required
						</span>
					)}
				</div>
				<span
					data-testid={`config-status-${config.key}`}
					className={
						"flex shrink-0 items-center gap-1 text-xs font-medium " +
						(config.value_set
							? "text-[var(--bf-success)]"
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
				<p
					id={`${inputId}-description`}
					className="mt-2 text-sm text-muted-foreground [overflow-wrap:anywhere]"
				>
					{config.description}
				</p>
			)}
			<div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center">
				<Input
					id={inputId}
					aria-describedby={
						config.description
							? `${inputId}-description`
							: undefined
					}
					aria-invalid={saveMut.isError || undefined}
					aria-errormessage={
						saveMut.isError ? `${inputId}-error` : undefined
					}
					disabled={saveMut.isPending}
					className="min-h-11"
					data-testid={`config-value-input-${config.key}`}
					type={secret ? "password" : "text"}
					value={value}
					placeholder={
						config.value_set
							? "Enter a new value…"
							: "Enter a value…"
					}
					onChange={(e) => {
						setValue(e.target.value);
						if (saveMut.isError) saveMut.reset();
					}}
				/>
				<Button
					type="submit"
					className="min-h-11"
					data-testid={`save-config-${config.key}`}
					disabled={value.trim() === "" || saveMut.isPending}
				>
					{saveMut.isPending && (
						<Loader2
							aria-hidden="true"
							className="mr-1.5 h-4 w-4 animate-spin motion-reduce:animate-none"
						/>
					)}
					{saveMut.isPending
						? "Saving…"
						: saveMut.isError
							? "Retry save"
							: "Save"}
				</Button>
			</div>
			{saveMut.isError && (
				<p
					id={`${inputId}-error`}
					role="alert"
					className="mt-3 text-sm text-destructive"
				>
					Couldn't save this value. Your entry is ready to retry.
				</p>
			)}
			{saveMut.isPending && (
				<p role="status" className="sr-only">
					Saving configuration value…
				</p>
			)}
		</form>
	);
}

/** Tailwind classes for a Contents type-chip (selected vs not). */
function chipClass(active: boolean): string {
	return [
		"inline-flex min-h-11 items-center gap-1.5 rounded-[var(--bf-radius-control)] border px-3 py-2 text-sm transition-colors duration-(--bf-motion-feedback) focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transition-none",
		active
			? "border-primary/35 bg-primary/10 text-primary"
			: "border-border bg-background text-muted-foreground hover:bg-muted",
	].join(" ");
}

/** Overview = the install's description (README, rendered GitHub-style) leading
 * a status/contents summary so the tab is never empty. Installed Solution
 * content is read-only in the UI; changes flow through deploy/sync. */
function OverviewTab({
	readme,
	readmeLoading,
	readmeError,
	onRetryReadme,
	entityCounts,
	configsCount,
	version,
	gitConnected,
	orgName,
	onPickEntity,
}: {
	readme: string | null;
	readmeLoading: boolean;
	readmeError: boolean;
	onRetryReadme: () => void;
	entityCounts: Record<EntityKind, number>;
	configsCount: number;
	version: string | null;
	gitConnected: boolean;
	orgName: string;
	onPickEntity: (kind: EntityKind) => void;
}) {
	const total = Object.values(entityCounts).reduce((a, b) => a + b, 0);
	return (
		<div className="flex min-w-0 flex-col gap-5">
			{/* Status summary — always present, so Overview never reads as empty. */}
			<Card>
				<CardHeader>
					<CardTitle className="text-base">At a glance</CardTitle>
					<CardDescription>
						{version ? `Version ${version} · ` : ""}
						{orgName} ·{" "}
						{gitConnected ? "Git-connected" : "Manual install"}
					</CardDescription>
				</CardHeader>
				<CardContent>
					<div className="grid grid-cols-2 gap-2 text-sm sm:flex sm:flex-wrap">
						{ENTITY_TABS.map(
							({ key, label, Icon, iconClassName }) => (
								<button
									type="button"
									key={key}
									onClick={() => onPickEntity(key)}
									className="flex min-h-11 items-center gap-1.5 rounded-[var(--bf-radius-control)] px-2 text-muted-foreground transition-colors duration-[var(--bf-motion-feedback)] hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transition-none"
								>
									<Icon
										className={`h-4 w-4 ${iconClassName}`}
									/>
									<span className="font-medium text-foreground">
										{entityCounts[key]}
									</span>
									{label}
								</button>
							),
						)}
						<span className="flex min-h-11 items-center gap-1.5 px-2 text-muted-foreground">
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
			<div className="min-w-0">
				{readmeLoading ? (
					<p
						role="status"
						className="flex items-center gap-2 text-sm text-muted-foreground"
					>
						<Loader2
							aria-hidden="true"
							className="size-4 animate-spin motion-reduce:animate-none"
						/>
						Loading setup instructions…
					</p>
				) : readmeError ? (
					<div
						role="alert"
						className="space-y-3 rounded-[var(--bf-radius-surface)] border border-destructive/30 bg-destructive/5 p-4 text-sm"
					>
						<p className="text-destructive">
							Couldn't load setup instructions.
						</p>
						<Button
							variant="outline"
							className="min-h-11"
							onClick={onRetryReadme}
						>
							Retry instructions
						</Button>
					</div>
				) : (
					<SolutionReadmeTab readme={readme} canEdit={false} />
				)}
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
			<div className="rounded-[var(--bf-radius-surface)] border border-dashed py-12 text-center text-sm text-muted-foreground">
				This Solution deploys no entities.
			</div>
		);
	}
	return (
		<div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
			{ENTITY_TABS.filter(({ key }) => entityCounts[key] > 0).map(
				({ key, label, Icon, iconClassName }) => (
					<button
						type="button"
						key={key}
						data-testid={`summary-${key}`}
						onClick={() => onPick(key)}
						className="flex min-h-11 min-w-0 items-center gap-3 rounded-[var(--bf-radius-surface)] border p-4 text-left transition-colors duration-[var(--bf-motion-feedback)] hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transition-none"
					>
						<Icon className={`h-5 w-5 shrink-0 ${iconClassName}`} />
						<div>
							<div className="text-lg font-semibold">
								{entityCounts[key]}
							</div>
							<div className="text-xs text-muted-foreground">
								{label}
							</div>
						</div>
					</button>
				),
			)}
		</div>
	);
}

function AccessModeBadge({ mode }: { mode: string }) {
	return (
		<Badge
			variant="outline"
			className={cn(
				"h-auto min-h-5 gap-1 whitespace-normal [overflow-wrap:anywhere]",
				ACCESS_BADGE_CLASS[mode],
			)}
		>
			<Shield className="h-3 w-3" />
			{mode}
		</Badge>
	);
}

function EntityKindBadge({ kind }: { kind: EntityKind }) {
	return (
		<Badge
			variant="outline"
			className={
				"h-auto min-h-5 gap-1 whitespace-normal [overflow-wrap:anywhere]"
			}
		>
			{ENTITY_KIND_LABEL[kind]}
		</Badge>
	);
}

function RoleLinkBadge({ role }: { role: AccessRoleSummary }) {
	return (
		<Badge
			asChild
			variant="outline"
			className="min-h-11 h-auto max-w-full whitespace-normal [overflow-wrap:anywhere] border-border bg-muted/40 px-3 text-foreground hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring"
		>
			<Link to={`/roles/${role.id}`} onClick={(e) => e.stopPropagation()}>
				{role.name}
			</Link>
		</Badge>
	);
}

function UserDetailButton({
	user,
	onOpen,
}: {
	user: AccessUserSummary;
	onOpen: (userId: string) => void;
}) {
	return (
		<button
			type="button"
			onClick={() => onOpen(user.id)}
			className="flex min-h-11 w-full items-center gap-3 px-3 py-2.5 text-left transition-colors duration-[var(--bf-motion-feedback)] motion-reduce:transition-none hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
		>
			<div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-medium">
				{userInitials(user)}
			</div>
			<div className="min-w-0">
				<div className="[overflow-wrap:anywhere] text-sm font-medium">
					{userLabel(user)}
				</div>
				<div className="[overflow-wrap:anywhere] text-xs text-muted-foreground">
					{user.email}
				</div>
			</div>
		</button>
	);
}

function AccessTab({
	rows,
	selected,
	onSelect,
	onClose,
}: {
	rows: AccessRow[];
	selected: AccessRow | null;
	onSelect: (row: AccessRow) => void;
	onClose: () => void;
}) {
	const accessTriggerRef = useRef<HTMLElement | null>(null);
	const isMobile = useMediaQuery("(max-width: 1023px)");
	const [search, setSearch] = useState("");
	const [viewMode, setViewMode] = useState<"grid" | "table">("table");
	const [selectedUserId, setSelectedUserId] = useState<string | null>(null);
	const [userSearch, setUserSearch] = useState("");
	const selectedUserQuery = useUser(
		selected ? (selectedUserId ?? undefined) : undefined,
	);
	const q = search.trim().toLowerCase();
	const visibleRows = useMemo(() => {
		if (!q) return rows;
		return rows.filter((row) =>
			[
				row.name,
				row.description,
				row.kind,
				row.accessMode,
				row.path,
				row.functionName,
			].some((value) => value?.toLowerCase().includes(q)),
		);
	}, [q, rows]);
	const visibleUsers = useMemo(() => {
		if (!selected) return [];
		const query = userSearch.trim().toLowerCase();
		if (!query) return selected.users;
		return selected.users.filter((user) =>
			[user.name, user.email].some((value) =>
				value?.toLowerCase().includes(query),
			),
		);
	}, [selected, userSearch]);
	const openAccessRow = (row: AccessRow, trigger: HTMLElement) => {
		accessTriggerRef.current = trigger;
		setUserSearch("");
		onSelect(row);
	};

	if (rows.length === 0) {
		return (
			<div className="rounded-[var(--bf-radius-surface)] border border-dashed py-10 text-center text-sm text-muted-foreground">
				This Solution has no deployed entities with access metadata.
			</div>
		);
	}

	return (
		<>
			<div className="flex min-h-0 max-h-full flex-col gap-3">
				<div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
					<SearchBox
						value={search}
						onChange={setSearch}
						placeholder="Search access..."
						className="flex-1"
					/>
					{!isMobile && (
						<ToggleGroup
							aria-label="Access layout"
							type="single"
							value={viewMode}
							onValueChange={(value: string) =>
								value && setViewMode(value as "grid" | "table")
							}
						>
							<ToggleGroupItem
								value="grid"
								aria-label="Grid view"
								className="h-11 w-11"
							>
								<LayoutGrid className="h-4 w-4" />
							</ToggleGroupItem>
							<ToggleGroupItem
								value="table"
								aria-label="Table view"
								className="h-11 w-11"
							>
								<TableIcon className="h-4 w-4" />
							</ToggleGroupItem>
						</ToggleGroup>
					)}
				</div>

				{visibleRows.length === 0 ? (
					<div className="rounded-[var(--bf-radius-surface)] border border-dashed py-8 text-center text-sm text-muted-foreground">
						No access rows match “{search.trim()}”.
					</div>
				) : isMobile || viewMode === "grid" ? (
					<div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
						{visibleRows.map((row) => (
							<button
								type="button"
								key={row.id}
								onClick={(event) =>
									openAccessRow(row, event.currentTarget)
								}
								className="flex min-w-0 flex-col gap-3 rounded-[var(--bf-radius-surface)] border border-border/70 bg-card p-4 text-left transition-colors duration-[var(--bf-motion-feedback)] hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring motion-reduce:transition-none"
							>
								<span className="flex flex-wrap items-center justify-between gap-2">
									<EntityKindBadge kind={row.kind} />
									<AccessModeBadge mode={row.accessMode} />
								</span>
								<span className="font-medium [overflow-wrap:anywhere]">
									{row.name}
								</span>
								<span className="text-sm text-muted-foreground [overflow-wrap:anywhere]">
									{row.description || "No description"}
								</span>
								<span className="text-xs text-muted-foreground">
									{userSummary(row)}
								</span>
							</button>
						))}
					</div>
				) : (
					<DataTable className="max-h-full">
						<DataTableHeader>
							<DataTableRow>
								<DataTableHead className="w-28 min-w-28 whitespace-nowrap">
									Type
								</DataTableHead>
								<DataTableHead className="w-28 min-w-28 whitespace-nowrap">
									Entity
								</DataTableHead>
								<DataTableHead>Description</DataTableHead>
								<DataTableHead className="w-40 min-w-40 whitespace-nowrap">
									Access
								</DataTableHead>
							</DataTableRow>
						</DataTableHeader>
						<DataTableBody>
							{visibleRows.map((row) => {
								const isSelected = selected?.id === row.id;
								return (
									<DataTableRow
										key={row.id}
										clickable
										className={[
											"group/row",
											isSelected
												? "bg-muted/70"
												: "hover:bg-muted/50",
										].join(" ")}
										onClick={(event) =>
											openAccessRow(
												row,
												event.currentTarget.querySelector(
													"button",
												) ?? event.currentTarget,
											)
										}
									>
										<DataTableCell className="w-28 min-w-28 whitespace-nowrap">
											<EntityKindBadge kind={row.kind} />
										</DataTableCell>
										<DataTableCell className="min-w-64 max-w-sm whitespace-normal font-medium">
											<button
												type="button"
												onClick={(event) => {
													event.stopPropagation();
													openAccessRow(
														row,
														event.currentTarget,
													);
												}}
												className="min-h-11 text-left [overflow-wrap:anywhere] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
											>
												{row.name}
											</button>
										</DataTableCell>
										<DataTableCell className="max-w-xl whitespace-normal [overflow-wrap:anywhere] text-muted-foreground">
											{row.description || "-"}
										</DataTableCell>
										<DataTableCell className="w-40 min-w-40 whitespace-nowrap">
											<AccessModeBadge
												mode={row.accessMode}
											/>
										</DataTableCell>
									</DataTableRow>
								);
							})}
						</DataTableBody>
					</DataTable>
				)}
			</div>

			<Sheet
				open={!!selected}
				onOpenChange={(open) => {
					if (!open) {
						setSelectedUserId(null);
						onClose();
					}
				}}
			>
				<SheetContent
					side="right"
					className="w-full p-0 sm:max-w-xl"
					onCloseAutoFocus={(event) => {
						if (accessTriggerRef.current?.isConnected) {
							event.preventDefault();
							accessTriggerRef.current.focus({
								preventScroll: true,
							});
						}
					}}
				>
					{selected && (
						<>
							<SheetHeader className="shrink-0 border-b px-5 py-4 pr-16">
								<SheetTitle className="[overflow-wrap:anywhere]">
									{selected.name}
								</SheetTitle>
								<SheetDescription className="sr-only">
									Access details and assigned users.
								</SheetDescription>
							</SheetHeader>

							<div className="flex min-h-0 flex-1 flex-col gap-5 overflow-y-auto px-5 py-5 [&>section]:shrink-0">
								{selected.description && (
									<p className="shrink-0 text-sm text-muted-foreground [overflow-wrap:anywhere]">
										{selected.description}
									</p>
								)}
								{(selected.path || selected.functionName) && (
									<section>
										<h3 className="mb-2 text-sm font-semibold">
											Runtime reference
										</h3>
										<div className="rounded-[var(--bf-radius-surface)] border bg-muted/30 p-3 font-mono text-xs [overflow-wrap:anywhere]">
											{[
												selected.path,
												selected.functionName,
											]
												.filter(Boolean)
												.join("::")}
										</div>
									</section>
								)}

								<section className="grid grid-cols-2 gap-3">
									<div>
										<div className="mb-1.5 text-xs font-medium text-muted-foreground">
											Type
										</div>
										<EntityKindBadge kind={selected.kind} />
									</div>
									<div>
										<div className="mb-1.5 text-xs font-medium text-muted-foreground">
											Access
										</div>
										<AccessModeBadge
											mode={selected.accessMode}
										/>
									</div>
								</section>

								<section>
									<h3 className="mb-2 text-sm font-semibold">
										Roles
									</h3>
									{selected.roles.length > 0 ? (
										<div className="flex flex-wrap gap-2">
											{selected.roles.map((role) => (
												<RoleLinkBadge
													key={role.id}
													role={role}
												/>
											))}
										</div>
									) : (
										<p className="text-sm text-muted-foreground">
											This entity does not require a role
											assignment.
										</p>
									)}
								</section>

								<section className="min-h-0">
									<div className="mb-3 flex items-center justify-between gap-3">
										<h3 className="text-sm font-semibold">
											Users
										</h3>
										<span className="text-xs text-muted-foreground">
											{selected.users.length}
										</span>
									</div>
									{selected.users.length > 0 ? (
										<>
											<SearchBox
												value={userSearch}
												onChange={setUserSearch}
												placeholder="Search users..."
												debounceMs={0}
												className="mb-3"
											/>
											{visibleUsers.length > 0 ? (
												<div className="divide-y rounded-[var(--bf-radius-surface)] border">
													{visibleUsers.map(
														(user) => (
															<div key={user.id}>
																<UserDetailButton
																	user={user}
																	onOpen={
																		setSelectedUserId
																	}
																/>
																{selectedUserId ===
																	user.id &&
																	!selectedUserQuery.data && (
																		<div className="px-3 pb-3 text-sm">
																			{selectedUserQuery.isFetching ? (
																				<p
																					role="status"
																					className="flex items-center gap-2 text-muted-foreground"
																				>
																					<Loader2
																						aria-hidden="true"
																						className="size-4 shrink-0 animate-spin motion-reduce:animate-none"
																					/>
																					Loading
																					user
																					details…
																				</p>
																			) : selectedUserQuery.isError ? (
																				<div
																					role="alert"
																					className="space-y-2"
																				>
																					<p className="text-destructive">
																						Couldn't
																						load
																						user
																						details.
																					</p>
																					<Button
																						variant="outline"
																						className="min-h-11"
																						onClick={() =>
																							void selectedUserQuery.refetch()
																						}
																					>
																						Retry
																						user
																						details
																					</Button>
																				</div>
																			) : null}
																		</div>
																	)}
															</div>
														),
													)}
												</div>
											) : (
												<p className="rounded-[var(--bf-radius-surface)] border border-dashed px-3 py-6 text-sm text-muted-foreground">
													No users match “
													{userSearch.trim()}”.
												</p>
											)}
										</>
									) : (
										<p className="rounded-[var(--bf-radius-surface)] border border-dashed px-3 py-6 text-sm text-muted-foreground">
											{selected.accessMode ===
											"Role based"
												? "No users are currently assigned through this Solution's roles."
												: userSummary(selected)}
										</p>
									)}
								</section>
							</div>
						</>
					)}
				</SheetContent>
			</Sheet>
			<EditUserDialog
				user={selectedUserQuery.data}
				open={Boolean(
					selected && selectedUserId && selectedUserQuery.data,
				)}
				onOpenChange={(open) => {
					if (!open) setSelectedUserId(null);
				}}
			/>
		</>
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
	setupLoading,
	setupFetching,
	onRetrySetup,
	onInvalidate,
	onSetConfig,
	onGenerateWorkflowKey,
	onFinish,
}: {
	configs: ConfigStatus[];
	orgId: string | null;
	setupItems: components["schemas"]["SolutionSetupItem"][];
	setupComplete: boolean;
	setupError: unknown;
	setupLoading: boolean;
	setupFetching: boolean;
	onRetrySetup: () => void;
	onInvalidate: () => void;
	onSetConfig: (key: string, value: string) => void | Promise<void>;
	onGenerateWorkflowKey: (workflowId: string) => void | Promise<void>;
	onFinish: () => void;
}) {
	const hasConnections = setupItems.some((i) => i.kind === "connection");
	const hasSetupRequirements = setupItems.length > 0;
	return (
		<div className="flex flex-col gap-6">
			{/* Setup requirements that are not plain config-value rows. */}
			{setupLoading || (setupFetching && setupError) ? (
				<div
					role="status"
					className="flex items-center gap-2 rounded-[var(--bf-radius-surface)] border p-4 text-sm text-muted-foreground"
				>
					<Loader2
						aria-hidden="true"
						className="size-4 shrink-0 animate-spin motion-reduce:animate-none"
					/>
					Loading setup requirements…
				</div>
			) : setupError ? (
				<div
					role="alert"
					className="space-y-3 rounded-[var(--bf-radius-surface)] border border-destructive/40 bg-destructive/5 p-4 text-sm"
				>
					<p className="text-destructive">
						Couldn't load setup requirements. Retry to check what
						this solution needs.
					</p>
					<Button
						variant="outline"
						className="min-h-11"
						onClick={onRetrySetup}
					>
						Retry setup status
					</Button>
				</div>
			) : (
				hasSetupRequirements && (
					<section data-testid="config-connections">
						<h3 className="mb-2 text-sm font-semibold">Setup</h3>
						<SolutionSetupWizard
							items={setupItems}
							setupComplete={setupComplete}
							onFinish={onFinish}
							onSetConfig={onSetConfig}
							onGenerateWorkflowKey={onGenerateWorkflowKey}
						/>
					</section>
				)
			)}

			{/* Config values. */}
			<section data-testid="config-values">
				{hasConnections && configs.length > 0 && (
					<h3 className="mb-2 text-sm font-semibold">
						Config values
					</h3>
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
					!hasSetupRequirements &&
					!setupLoading &&
					!setupError && (
						<div className="rounded-[var(--bf-radius-surface)] border px-4 py-12 text-center text-sm text-muted-foreground">
							This Solution declares no configuration.
						</div>
					)
				)}
			</section>
		</div>
	);
}

function exportJobStatusBadge(job: SolutionExportJob) {
	return (
		<Badge
			variant="outline"
			className={cn(
				"h-auto min-h-5 whitespace-normal [overflow-wrap:anywhere]",
				job.status === "completed" &&
					"border-[var(--bf-success)]/30 bg-[var(--bf-success)]/10 text-[var(--bf-success)]",
				job.status === "failed" &&
					"border-destructive/30 bg-destructive/10 text-destructive",
			)}
		>
			{job.status.replaceAll("_", " ")}
		</Badge>
	);
}

function ExportsTab({
	jobs,
	isLoading,
	error,
	isFetching,
	onRetry,
	onDownload,
}: {
	jobs: SolutionExportJob[];
	isLoading: boolean;
	error?: string;
	isFetching: boolean;
	onRetry: () => void;
	onDownload: (job: SolutionExportJob) => Promise<void>;
}) {
	if (isLoading) {
		return (
			<Card
				role="status"
				aria-label="Loading backup exports"
				className="py-0"
			>
				<CardContent className="space-y-3 py-6">
					<Skeleton className="h-5 w-48" />
					<Skeleton className="h-16 w-full" />
				</CardContent>
			</Card>
		);
	}

	if (error) {
		return (
			<div
				role="alert"
				className="space-y-3 rounded-[var(--bf-radius-surface)] border border-destructive/30 bg-destructive/5 p-4 text-sm"
			>
				<p className="text-destructive [overflow-wrap:anywhere]">
					Couldn't load backup exports.
				</p>
				<Button
					variant="outline"
					className="min-h-11"
					disabled={isFetching}
					onClick={onRetry}
				>
					{isFetching ? "Loading…" : "Retry exports"}
				</Button>
				{isFetching && (
					<p role="status" className="sr-only">
						Loading backup exports…
					</p>
				)}
			</div>
		);
	}

	if (jobs.length === 0) {
		return (
			<div className="rounded-[var(--bf-radius-surface)] border border-dashed py-10 text-center text-sm text-muted-foreground">
				No backup exports queued yet.
			</div>
		);
	}

	return (
		<div className="space-y-3">
			{jobs.map((job) => (
				<ExportJobRecord
					key={job.id}
					job={job}
					onDownload={onDownload}
				/>
			))}
		</div>
	);
}

function ExportJobRecord({
	job,
	onDownload,
}: {
	job: SolutionExportJob;
	onDownload: (job: SolutionExportJob) => Promise<void>;
}) {
	const pendingRef = useRef(false);
	const download = useMutation({
		mutationFn: () => onDownload(job),
		onSettled: () => {
			pendingRef.current = false;
		},
	});
	const canDownload = job.status === "completed" && !!job.download_url;
	const isDownloading = download.isPending;
	const active = job.status === "pending" || job.status === "running";
	const progress =
		job.progress_percent == null
			? null
			: Math.min(100, Math.max(0, job.progress_percent));
	return (
		<Card data-testid={`export-job-${job.id}`} className="gap-0 py-0">
			<CardContent className="flex flex-col gap-4 p-4 lg:flex-row lg:items-center lg:justify-between">
				<div className="min-w-0 flex-1 space-y-2">
					<div className="flex flex-wrap items-center gap-2">
						{exportJobStatusBadge(job)}
						<span className="text-sm font-medium">
							{progress === null
								? active
									? "Starting…"
									: ""
								: `${progress}%`}
						</span>
						{job.artifact_size_bytes != null && (
							<span className="text-xs text-muted-foreground">
								{formatBytes(job.artifact_size_bytes)}
							</span>
						)}
					</div>
					{active && (
						<Progress
							aria-label="Backup export progress"
							value={progress}
						/>
					)}
					<div className="grid gap-2 text-xs text-muted-foreground lg:grid-cols-3">
						<span>Created {formatDateTime(job.created_at)}</span>
						{job.completed_at && (
							<span>
								Completed {formatDateTime(job.completed_at)}
							</span>
						)}
						{job.expires_at && (
							<span>
								Expires {formatDateTime(job.expires_at)}
							</span>
						)}
					</div>
					{job.status === "expired" && (
						<p className="text-sm text-muted-foreground">
							This archive has expired. Create a new backup export
							to download it.
						</p>
					)}
					{(job.message || job.failure_message) && (
						<p className="text-sm text-muted-foreground [overflow-wrap:anywhere]">
							{job.failure_message ?? job.message}
						</p>
					)}
				</div>
				<Button
					type="button"
					size="default"
					variant="outline"
					disabled={!canDownload || isDownloading}
					onClick={() => {
						if (!pendingRef.current) {
							pendingRef.current = true;
							download.mutate();
						}
					}}
					className="min-h-11 shrink-0"
				>
					{isDownloading ? (
						<Loader2
							aria-hidden="true"
							className="mr-1.5 h-4 w-4 animate-spin motion-reduce:animate-none"
						/>
					) : (
						<Download className="mr-1.5 h-4 w-4" />
					)}
					{isDownloading
						? "Downloading…"
						: download.isError
							? "Retry download"
							: "Download"}
				</Button>
			</CardContent>
			{download.isError && (
				<p role="alert" className="px-4 pb-4 text-sm text-destructive">
					Couldn't download this export. Try again.
				</p>
			)}
			{isDownloading && (
				<p role="status" className="sr-only">
					Downloading backup export…
				</p>
			)}
		</Card>
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
	const [generatedEndpointKey, setGeneratedEndpointKey] = useState<{
		workflowName: string;
		rawKey: string;
	} | null>(null);

	const { data, isLoading, error } = useQuery({
		queryKey: ["solutions", solutionId, "entities"],
		queryFn: () => getSolutionEntities(solutionId!),
		enabled: !!solutionId,
	});

	const {
		data: setupData,
		error: setupError,
		isPending: setupLoading,
		isFetching: setupFetching,
		refetch: refetchSetup,
	} = useQuery({
		queryKey: ["solutions", solutionId, "setup"],
		queryFn: () => getSolutionSetup(solutionId!),
		enabled: !!solutionId,
	});

	const {
		data: readmeData,
		isLoading: readmeLoading,
		isError: readmeError,
		refetch: refetchReadme,
	} = useQuery({
		queryKey: ["solutions", solutionId, "readme"],
		queryFn: () => getSolutionReadme(solutionId!),
		enabled: !!solutionId,
	});

	const { data: solutionSdkStatus } = useQuery({
		queryKey: ["solutions", solutionId, "sdk-status"],
		queryFn: () => getSolutionSdkStatus(solutionId!),
		enabled: !!solutionId,
		staleTime: 60_000,
	});
	const sdkUpdateJobs = useApplicationSdkUpdateJobs({ solutionId });

	const exportJobsQuery = useQuery({
		queryKey: ["solutions", solutionId, "export-jobs"],
		queryFn: () => listSolutionExportJobs(solutionId!),
		enabled: !!solutionId,
		refetchInterval: (query) => {
			const jobs = query.state.data?.jobs ?? [];
			return jobs.some(
				(job) => job.status === "pending" || job.status === "running",
			)
				? 2500
				: false;
		},
	});

	const invalidate = () => {
		void queryClient.invalidateQueries({
			queryKey: ["solutions", solutionId, "entities"],
		});
		void refetchSetup();
		void refetchReadme();
	};

	const pendingEndpointKeys = useRef(new Set<string>());
	const generateWorkflowEndpointKey = async (workflowId: string) => {
		if (pendingEndpointKeys.current.has(workflowId)) return;
		pendingEndpointKeys.current.add(workflowId);
		try {
			const currentSetup = await getSolutionSetup(solutionId!);
			const item = currentSetup.items.find(
				(entry) =>
					entry.kind === "workflow_endpoint_key" &&
					(entry.workflow_id ?? entry.key) === workflowId,
			);
			if (!item)
				throw new Error(
					"Endpoint setup requirement is no longer available",
				);
			if (item.is_set) {
				await workflowKeysService.revokeWorkflowKey(workflowId);
			}
			const result = await workflowKeysService.createWorkflowKey({
				workflow_id: workflowId,
				description: "Generated during Solution setup",
				disable_global_key: false,
			});
			if (result.raw_key) {
				setGeneratedEndpointKey({
					workflowName:
						item?.workflow_name ??
						result.workflow_name ??
						"Workflow endpoint",
					rawKey: result.raw_key,
				});
			}
			invalidate();
			void queryClient.invalidateQueries({ queryKey: ["workflow-keys"] });
		} catch (err: unknown) {
			invalidate();
			void queryClient.invalidateQueries({ queryKey: ["workflow-keys"] });
			throw err;
		} finally {
			pendingEndpointKeys.current.delete(workflowId);
		}
	};

	const sol = data?.solution;
	const effectiveSetupComplete =
		setupData?.setup_complete ?? sol?.setup_complete ?? true;

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
			downloadBlob(blob, filename);
			setExportDialogOpen(false);
		},
	});

	const backupExportMut = useMutation({
		mutationFn: ({
			password,
			options,
		}: {
			password: string;
			options: SolutionExportOptions;
		}) => createSolutionExportJob(solutionId!, { password, options }),
		onSuccess: () => {
			setExportDialogOpen(false);
			setTab("exports");
			toast.success("Backup export queued");
			void queryClient.invalidateQueries({
				queryKey: ["solutions", solutionId, "export-jobs"],
			});
		},
	});

	/** Non-destructive uninstall — flips status to inactive, data frozen. */
	const uninstallBusy = useRef(false);
	const uninstallMut = useMutation({
		onSettled: () => {
			uninstallBusy.current = false;
		},
		mutationFn: () => uninstallSolution(solutionId!),
		onSuccess: () => {
			void queryClient.invalidateQueries({ queryKey: ["solutions"] });
			invalidate();
			toast.success("Solution uninstalled (inactive)");
		},
	});
	function handleUninstall() {
		if (uninstallBusy.current) return;
		uninstallBusy.current = true;
		uninstallMut.mutate();
	}

	/** Hard-delete — permanently destroys everything. Requires confirm === slug. */
	const hardDeleteMut = useMutation({
		mutationFn: (confirmation: string) =>
			deleteSolution(solutionId!, confirmation),
		onSuccess: (summary) => {
			queryClient.invalidateQueries({ queryKey: ["solutions"] });
			toast.success("Solution permanently deleted", {
				description: `Removed ${summary.workflows_deleted} workflows, ${summary.apps_deleted} apps, ${summary.forms_deleted} forms, ${summary.agents_deleted} agents, ${summary.tables_deleted} tables, ${summary.files_swept} files.`,
			});
			navigate("/solutions");
		},
	});

	function openHardDelete() {
		setHardDeleteOpen(true);
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
	});

	const updateSolutionAppSdksMut = useMutation({
		mutationFn: () => updateSolutionAppSdks(solutionId!),
		onSuccess: (result) => {
			sdkUpdateJobs.trackAccepted(result.accepted ?? []);
			const accepted = result.accepted?.length ?? 0;
			const skipped = result.skipped?.length ?? 0;
			toast.success("App SDK updates queued", {
				description:
					skipped > 0
						? `${accepted} queued, ${skipped} already current or unavailable.`
						: `${accepted} app${accepted === 1 ? "" : "s"} queued. Progress will appear in notifications.`,
			});
		},
		onError: (err) => {
			toast.error("Failed to queue app SDK updates", {
				description: err instanceof Error ? err.message : String(err),
			});
		},
	});

	// Lazily preview the connected repo when the Update-now dialog opens, so the
	// operator sees WHAT the full-replace will change (added/removed/changed
	// entities + config) before confirming — the git update path previously gave
	// no diff (audit CM3/M2). Reuses the from-repo preview endpoint.
	const updateDiffQuery = useQuery({
		queryKey: [
			"solution-update-diff",
			solutionId,
			sol?.git_ref,
			sol?.repo_subpath,
		],
		enabled: syncConfirmOpen && !!sol?.git_connected && !!sol?.git_repo_url,
		queryFn: () =>
			previewSolutionFromRepo({
				repo_url: sol!.git_repo_url!,
				...(sol!.repo_subpath
					? { repo_subpath: sol!.repo_subpath }
					: {}),
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
	const accessRows = useMemo<AccessRow[]>(() => {
		if (!data) return [];
		return [
			...buildEntityAccessRows("workflows", data.workflows ?? []),
			...buildEntityAccessRows("apps", data.apps ?? []),
			...buildEntityAccessRows("forms", data.forms ?? []),
			...buildEntityAccessRows("agents", data.agents ?? []),
		];
	}, [data]);
	const [selectedAccessId, setSelectedAccessId] = useState<string | null>(
		null,
	);
	const selectedAccessRow =
		accessRows.find((row) => row.id === selectedAccessId) ?? null;

	// `files` carries SolutionFileSummary[], not EntitySummary[] — EntityTabContent
	// handles the files kind before it reaches the EntitySummary rendering path.
	const itemsFor = (key: EntityKind): EntitySummary[] => {
		if (key === "files") return [];
		return (data?.[key] as EntitySummary[] | undefined) ?? [];
	};

	const requiredUnset = data?.required_configs_unset ?? [];
	const solutionAppIds =
		solutionSdkStatus?.apps?.map((app) => app.application_id) ?? [];
	const solutionSdkUpdating =
		updateSolutionAppSdksMut.isPending ||
		sdkUpdateJobs.isAnyUpdating(solutionAppIds);
	// Contents tab: "All" shows a combined per-type summary; a specific chip
	// renders that kind's full surface (with its specialized actions — workflow
	// execute, form launch, app open — which a merged column list would lose).
	const [contentsFilter, setContentsFilter] = useState<ContentsFilter>("all");
	const activeKind: EntityKind | null =
		contentsFilter === "all" ? null : contentsFilter;
	function openContentKind(kind: EntityKind) {
		setContentsFilter(kind);
		setTab("contents");
	}

	return (
		<PageWorkspace
			data-testid="solution-detail"
			className="mx-auto w-full max-w-7xl gap-5"
		>
			{/* Breadcrumb */}
			<div className="flex min-w-0 shrink-0 flex-wrap items-center text-sm [overflow-wrap:anywhere]">
				<Link
					to="/solutions"
					className="inline-flex min-h-11 items-center rounded-[var(--bf-radius-control)] text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
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
					<div className="flex shrink-0 flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
						<div className="min-w-0 flex-1">
							<div className="flex min-w-0 flex-wrap items-center gap-3">
								<h1 className="min-w-0 text-2xl font-semibold tracking-tight [overflow-wrap:anywhere] sm:text-3xl">
									{sol.name}
								</h1>
								{effectiveSetupComplete === false && (
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
										upgraded from v
										{sol.upgraded_from_version}
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
									<Badge variant="outline">
										v{sol.version}
									</Badge>
								)}
								<Badge variant="outline" className="gap-1">
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
									{sol.git_connected
										? "Git-connected"
										: "Manual"}
								</Badge>
								{sol.update_available_version && (
									<Badge
										variant="default"
										className="gap-1"
										data-testid="update-available-badge"
									>
										<ArrowUp className="h-3 w-3" />
										Update available · v
										{sol.update_available_version}
									</Badge>
								)}
								{solutionSdkStatus && (
									<ApplicationSdkStatusBadge
										status={solutionSdkStatus.sdk_status}
										showCurrent
									/>
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
										{sol.repo_subpath
											? ` /${sol.repo_subpath}`
											: ""}
										{sol.git_ref ? ` @ ${sol.git_ref}` : ""}
									</span>
								</p>
							)}
						</div>
						<div className="flex shrink-0 flex-wrap items-center gap-2 sm:justify-end">
							{effectiveSetupComplete === false &&
								sol.status !== "inactive" && (
									<Button
										data-testid="continue-setup"
										variant="outline"
										className="min-h-11 whitespace-nowrap border-yellow-500/60 text-yellow-700 hover:text-yellow-700 dark:text-yellow-400"
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
									className="min-h-11 whitespace-nowrap"
									onClick={() => setUpdateOpen(true)}
								>
									<RotateCcw className="mr-1.5 h-4 w-4" />
									Reactivate
								</Button>
							) : sol.git_connected &&
							  sol.update_available_version ? (
								<Button
									data-testid="update-now"
									className="min-h-11 whitespace-nowrap"
									disabled={syncMut.isPending}
									onClick={() => setSyncConfirmOpen(true)}
								>
									{syncMut.isPending ? (
										<Loader2
											aria-hidden="true"
											className="mr-1.5 h-4 w-4 animate-spin motion-reduce:animate-none"
										/>
									) : (
										<ArrowUp className="mr-1.5 h-4 w-4" />
									)}
									Update now
								</Button>
							) : (
								<Button
									data-testid="update-solution"
									className="min-h-11 whitespace-nowrap"
									onClick={() => setUpdateOpen(true)}
								>
									<Upload className="mr-1.5 h-4 w-4" />
									Update
								</Button>
							)}
							<SolutionActionsMenu
								exporting={
									exportMut.isPending ||
									backupExportMut.isPending
								}
								isInactive={sol.status === "inactive"}
								onUpdateAppSdks={() => {
									updateSolutionAppSdksMut.mutate();
								}}
								appSdkUpdateDisabled={
									!solutionSdkStatus ||
									solutionSdkStatus.actionable_count === 0
								}
								appSdkUpdating={solutionSdkUpdating}
								onCapture={() => setCaptureOpen(true)}
								onExport={() => setExportDialogOpen(true)}
								onEdit={() => setEditOpen(true)}
								onUninstall={handleUninstall}
								busy={uninstallMut.isPending}
								onHardDelete={openHardDelete}
							/>
						</div>
					</div>

					<SolutionUninstallNotice
						pending={uninstallMut.isPending}
						error={
							uninstallMut.isError
								? uninstallMut.error instanceof Error
									? uninstallMut.error.message
									: "Try again."
								: null
						}
						onRetry={handleUninstall}
					/>

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
									Setup incomplete — {requiredUnset.length}{" "}
									required config
									{requiredUnset.length === 1 ? "" : "s"} need
									{requiredUnset.length === 1 ? "s" : ""} a
									value before this Solution can run.
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
						className="flex min-h-0 flex-1 flex-col"
					>
						<TabsList
							aria-label="Solution sections"
							className="grid w-full shrink-0 grid-cols-2 self-start group-data-horizontal/tabs:h-auto sm:flex sm:w-fit"
						>
							<TabsTrigger
								value="overview"
								data-testid="tab-overview"
								className="min-h-11 gap-1.5 whitespace-normal"
							>
								<FileText className="h-4 w-4" />
								Overview
							</TabsTrigger>
							<TabsTrigger
								value="contents"
								data-testid="tab-contents"
								className="min-h-11 gap-1.5 whitespace-normal"
							>
								<LayoutGrid className="h-4 w-4" />
								Contents
								<span className="ml-1 text-xs text-muted-foreground">
									{totalContents}
								</span>
							</TabsTrigger>
							<TabsTrigger
								value="access"
								data-testid="tab-access"
								className="min-h-11 gap-1.5 whitespace-normal"
							>
								<Shield className="h-4 w-4" />
								Access
								<span className="ml-1 text-xs text-muted-foreground">
									{accessRows.length}
								</span>
							</TabsTrigger>
							<TabsTrigger
								value="configuration"
								data-testid="tab-configuration"
								className="min-h-11 gap-1.5 whitespace-normal"
							>
								<SlidersHorizontal className="h-4 w-4" />
								Configuration
								{effectiveSetupComplete === false ? (
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
							<TabsTrigger
								value="exports"
								data-testid="tab-exports"
								className="min-h-11 gap-1.5 whitespace-normal"
							>
								<Archive className="h-4 w-4" />
								Exports
							</TabsTrigger>
						</TabsList>

						{/* OVERVIEW — README leads (it's the description, not a section),
						    with a status/contents summary so it's never empty. */}
						<TabsContent
							value="overview"
							className="flex min-h-0 flex-1 flex-col"
						>
							<PageScrollArea>
								<OverviewTab
									readme={readmeData?.readme ?? null}
									readmeLoading={readmeLoading}
									readmeError={readmeError}
									onRetryReadme={() => void refetchReadme()}
									entityCounts={entityCounts}
									configsCount={configsCount}
									version={sol.version ?? null}
									gitConnected={sol.git_connected}
									orgName={orgName}
									onPickEntity={openContentKind}
								/>
							</PageScrollArea>
						</TabsContent>

						{/* CONTENTS — the 6 entity inventories as one tab with type chips. */}
						<TabsContent
							value="contents"
							className="flex-1 min-h-0 flex flex-col"
						>
							<label className="mb-3 flex flex-col gap-2 text-sm lg:hidden">
								Content type
								<select
									aria-label="Content type"
									value={contentsFilter}
									onChange={(event) =>
										setContentsFilter(
											event.target.value as
												EntityKind | "all",
										)
									}
									className="min-h-11 w-full rounded-[var(--bf-radius-control)] border bg-background px-3 text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
								>
									<option value="all">
										All ({totalContents})
									</option>
									{ENTITY_TABS.map(({ key, label }) => (
										<option key={key} value={key}>
											{label} ({entityCounts[key]})
										</option>
									))}
								</select>
							</label>
							<div
								className="mb-3 hidden flex-wrap gap-2 lg:flex"
								role="group"
								aria-label="Content types"
								data-testid="contents-chips"
							>
								<button
									type="button"
									data-testid="chip-all"
									aria-pressed={contentsFilter === "all"}
									onClick={() => setContentsFilter("all")}
									className={chipClass(
										contentsFilter === "all",
									)}
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
										aria-pressed={contentsFilter === key}
										onClick={() => setContentsFilter(key)}
										className={chipClass(
											contentsFilter === key,
										)}
									>
										<Icon className="h-3.5 w-3.5" />
										{label}
										<span className="ml-1.5 text-xs opacity-70">
											{entityCounts[key]}
										</span>
									</button>
								))}
							</div>
							<PageScrollArea>
								{activeKind ? (
									<EntityTabContent
										kind={activeKind}
										items={itemsFor(activeKind)}
										solutionId={sol.id}
										solutionName={sol.name}
										fileCount={entityCounts.files}
										sdkStatus={solutionSdkStatus}
									/>
								) : (
									<ContentsSummary
										entityCounts={entityCounts}
										onPick={(k) => setContentsFilter(k)}
									/>
								)}
							</PageScrollArea>
						</TabsContent>

						<TabsContent
							value="access"
							className="flex min-h-0 flex-1 flex-col"
						>
							<PageScrollArea>
								<AccessTab
									rows={accessRows}
									selected={selectedAccessRow}
									onSelect={(row) =>
										setSelectedAccessId(row.id)
									}
									onClose={() => setSelectedAccessId(null)}
								/>
							</PageScrollArea>
						</TabsContent>

						{/* CONFIGURATION — config VALUES + integration connections; the
						    permanent home of what was the one-time Setup wizard. */}
						<TabsContent
							value="configuration"
							className="flex min-h-0 flex-1 flex-col"
						>
							<PageScrollArea>
								<ConfigurationTab
									configs={data.configs ?? []}
									orgId={sol.organization_id ?? null}
									setupItems={setupData?.items ?? []}
									setupComplete={effectiveSetupComplete}
									setupError={setupError}
									setupLoading={setupLoading}
									setupFetching={setupFetching}
									onRetrySetup={() => void refetchSetup()}
									onInvalidate={invalidate}
									onSetConfig={async (key, value) => {
										await setSolutionConfig({
											key,
											value,
											type: asConfigType(
												setupData?.items.find(
													(item) => item.key === key,
												)?.type ?? "string",
											),
											organizationId:
												sol.organization_id ?? null,
										});
										toast.success(`Set ${key}`);
										invalidate();
									}}
									onGenerateWorkflowKey={
										generateWorkflowEndpointKey
									}
									onFinish={() => {
										invalidate();
										if (effectiveSetupComplete)
											setTab("overview");
									}}
								/>
							</PageScrollArea>
						</TabsContent>

						<TabsContent
							value="exports"
							className="flex min-h-0 flex-1 flex-col"
						>
							<PageScrollArea>
								<ExportsTab
									jobs={exportJobsQuery.data?.jobs ?? []}
									isLoading={exportJobsQuery.isLoading}
									isFetching={exportJobsQuery.isFetching}
									onRetry={() =>
										void exportJobsQuery.refetch()
									}
									error={
										exportJobsQuery.error instanceof Error
											? exportJobsQuery.error.message
											: exportJobsQuery.isError
												? "Failed to load backup exports"
												: undefined
									}
									onDownload={async (job) => {
										const { blob, filename } =
											await downloadSolutionExportJob(
												job.id,
											);
										downloadBlob(blob, filename);
									}}
								/>
							</PageScrollArea>
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
								intent:
									sol.status === "inactive"
										? "reactivate"
										: "update",
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
					<SolutionUpdateDialog
						open={syncConfirmOpen}
						name={sol.name}
						version={sol.update_available_version}
						onClose={() => setSyncConfirmOpen(false)}
						onConfirm={() => syncMut.mutateAsync()}
						diff={updateDiffQuery.data?.diff ?? undefined}
						loading={updateDiffQuery.isLoading}
						previewError={updateDiffQuery.isError}
						retrying={updateDiffQuery.isFetching}
						onRetryPreview={() => void updateDiffQuery.refetch()}
					/>

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
						onExport={async (mode, password, options) => {
							if (mode === "full") {
								await backupExportMut.mutateAsync({
									password: password ?? "",
									options: options ?? {
										includeConfigs: true,
										includeSecrets: false,
										includeTables: false,
										includeFiles: true,
									},
								});
								return;
							}
							await exportMut.mutateAsync({
								mode,
								password,
								options,
							});
						}}
						isPending={
							exportMut.isPending || backupExportMut.isPending
						}
					/>

					{generatedEndpointKey && (
						<GeneratedEndpointKeyDialog
							workflowName={generatedEndpointKey.workflowName}
							rawKey={generatedEndpointKey.rawKey}
							onClose={() => setGeneratedEndpointKey(null)}
						/>
					)}

					{/* Hard-delete confirmation modal (type-the-slug to confirm) */}
					<SolutionDeleteDialog
						id={sol.id}
						name={sol.name}
						slug={sol.slug}
						open={hardDeleteOpen}
						onClose={() => setHardDeleteOpen(false)}
						onDelete={(confirmation) =>
							hardDeleteMut.mutateAsync(confirmation)
						}
					/>
				</>
			) : null}
		</PageWorkspace>
	);
}
