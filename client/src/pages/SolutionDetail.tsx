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
	putSolutionReadme,
	deleteSolution,
	exportSolution,
	setSolutionConfig,
	syncSolution,
} from "@/services/solutions";
import { SolutionSetupWizard } from "@/components/solutions/SolutionSetupWizard";
import { SolutionReadmeTab } from "@/components/solutions/SolutionReadmeTab";
import { useAuth } from "@/contexts/AuthContext";
import type { components } from "@/lib/v1";

type EntitySummary = components["schemas"]["SolutionEntitySummary"];
type ConfigStatus = components["schemas"]["SolutionConfigStatus"];
type ConfigType = components["schemas"]["ConfigType"];

type TabKey =
	| "readme"
	| "workflows"
	| "apps"
	| "forms"
	| "agents"
	| "tables"
	| "claims"
	| "configs"
	| "setup";

const ENTITY_TABS: {
	key: Exclude<TabKey, "configs" | "setup" | "readme">;
	label: string;
	Icon: typeof Workflow;
}[] = [
	{ key: "workflows", label: "Workflows", Icon: Workflow },
	{ key: "apps", label: "Apps", Icon: AppWindow },
	{ key: "forms", label: "Forms", Icon: FileCode },
	{ key: "agents", label: "Agents", Icon: Bot },
	{ key: "tables", label: "Tables", Icon: Database },
	{ key: "claims", label: "Custom Claims", Icon: KeyRound },
];

/** Per-entity-page link target, carrying the `?from` so the entity page can
 * offer a "back to this Solution" affordance (consumed in Task 19b). */
function entityHref(
	kind: Exclude<TabKey, "configs" | "setup" | "readme">,
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

const ENTITY_TAB_LABEL: Record<Exclude<TabKey, "configs" | "setup" | "readme">, string> = {
	workflows: "workflows",
	apps: "apps",
	forms: "forms",
	agents: "agents",
	tables: "tables",
	claims: "custom claims",
};

const GRID_TABLE_ENTITY_TABS = new Set<Exclude<TabKey, "configs" | "setup" | "readme">>([
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

function entityStatus(entity: EntitySummary, kind: Exclude<TabKey, "configs" | "setup" | "readme">) {
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
	kind: Exclude<TabKey, "configs" | "setup" | "readme">;
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
	kind: Exclude<TabKey, "configs" | "setup" | "readme">;
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
}: {
	kind: Exclude<TabKey, "configs" | "setup" | "readme">;
	items: EntitySummary[];
	solutionId: string;
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

export function SolutionDetail() {
	const { solutionId } = useParams<{ solutionId: string }>();
	const navigate = useNavigate();
	const queryClient = useQueryClient();
	const { data: organizations } = useOrganizations();
	const { isPlatformAdmin } = useAuth();

	// README is the first tab in ORDER, but the default selected tab stays on
	// the entity-centric Workflows view — admins land on what the Solution does,
	// not its prose. The README is one click left.
	const [tab, setTab] = useState<TabKey>("workflows");
	const [editOpen, setEditOpen] = useState(false);
	const [updateOpen, setUpdateOpen] = useState(false);
	const [syncConfirmOpen, setSyncConfirmOpen] = useState(false);
	const [captureOpen, setCaptureOpen] = useState(false);
	const [exportDialogOpen, setExportDialogOpen] = useState(false);
	const [deleteOpen, setDeleteOpen] = useState(false);
	const [deleteConfirm, setDeleteConfirm] = useState("");

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
			includeData,
		}: {
			mode: "shareable" | "full";
			password?: string;
			includeData?: boolean;
		}) => exportSolution(solutionId!, mode, password, includeData),
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

	const deleteMut = useMutation({
		mutationFn: () => deleteSolution(solutionId!),
		onSuccess: (summary) => {
			queryClient.invalidateQueries({ queryKey: ["solutions"] });
			toast.success("Solution uninstalled", {
				description: `Removed ${summary.workflows_deleted} workflows, ${summary.apps_deleted} apps, ${summary.forms_deleted} forms, ${summary.agents_deleted} agents. Kept ${summary.tables_orphaned} tables and ${summary.config_values_orphaned} config values as orphaned data.`,
			});
			navigate("/solutions");
		},
		onError: (err: unknown) => {
			toast.error("Failed to uninstall", {
				description: err instanceof Error ? err.message : "Unknown error",
			});
		},
	});

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

	const orgName = useMemo(() => {
		if (!sol?.organization_id) return "Global";
		return (
			organizations?.find((o) => o.id === sol.organization_id)?.name ??
			sol.organization_id
		);
	}, [sol, organizations]);

	const counts = useMemo(() => {
		return {
			readme: 0,
			workflows: data?.workflows?.length ?? 0,
			apps: data?.apps?.length ?? 0,
			forms: data?.forms?.length ?? 0,
			agents: data?.agents?.length ?? 0,
			tables: data?.tables?.length ?? 0,
			claims: data?.claims?.length ?? 0,
			configs: data?.configs?.length ?? 0,
			setup: setupData?.items?.length ?? 0,
		} satisfies Record<TabKey, number>;
	}, [data, setupData]);

	const itemsFor = (key: Exclude<TabKey, "configs" | "setup" | "readme">): EntitySummary[] =>
		(data?.[key] as EntitySummary[] | undefined) ?? [];

	const requiredUnset = data?.required_configs_unset ?? [];

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
						</div>
						<div className="flex shrink-0 items-center justify-end gap-2">
							{sol.setup_complete === false && (
								<Button
									data-testid="continue-setup"
									variant="outline"
									className="whitespace-nowrap border-yellow-500/60 text-yellow-700 hover:text-yellow-700 dark:text-yellow-400"
									onClick={() => setTab("setup")}
								>
									<AlertTriangle className="mr-1.5 h-4 w-4" />
									Continue Setup
								</Button>
							)}
							{sol.git_connected && sol.update_available_version ? (
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
								onCapture={() => setCaptureOpen(true)}
								onExport={() => setExportDialogOpen(true)}
								onEdit={() => setEditOpen(true)}
								onDelete={() => {
									setDeleteConfirm("");
									setDeleteOpen(true);
								}}
							/>
						</div>
					</div>

					{/* Required-config warning banner */}
					{requiredUnset.length > 0 && (
						<div
							data-testid="required-config-warning"
							className="flex items-center justify-between gap-3 rounded-lg border border-yellow-500/60 bg-yellow-500/10 px-4 py-3"
						>
							<div className="flex items-center gap-2 text-sm">
								<AlertTriangle className="h-4 w-4 text-yellow-600 dark:text-yellow-500" />
								<span>
									{requiredUnset.length} required config
									{requiredUnset.length === 1 ? "" : "s"} need
									{requiredUnset.length === 1 ? "s" : ""} a value
									before this Solution can run.
								</span>
							</div>
							<Button
								size="sm"
								variant="outline"
								onClick={() => setTab("configs")}
							>
								Set values
							</Button>
						</div>
					)}

					{/* Tabs */}
					<Tabs
						value={tab}
						onValueChange={(v) => setTab(v as TabKey)}
						className="flex-1 min-h-0 flex flex-col"
					>
						<TabsList className="self-start">
							<TabsTrigger
								value="readme"
								data-testid="tab-readme"
								className="gap-1.5"
							>
								<FileText className="h-4 w-4" />
								README
							</TabsTrigger>
							{ENTITY_TABS.map(({ key, label, Icon }) => (
								<TabsTrigger
									key={key}
									value={key}
									data-testid={`tab-${key}`}
									className="gap-1.5"
								>
									<Icon className="h-4 w-4" />
									{label}
									<span className="ml-1 text-xs text-muted-foreground">
										{counts[key]}
									</span>
								</TabsTrigger>
							))}
							<TabsTrigger
								value="configs"
								data-testid="tab-configs"
								className="gap-1.5"
							>
								<SlidersHorizontal className="h-4 w-4" />
								Configs
								<span className="ml-1 text-xs text-muted-foreground">
									{counts.configs}
								</span>
							</TabsTrigger>
							<TabsTrigger
								value="setup"
								data-testid="tab-setup"
								className="gap-1.5"
							>
								<CheckCircle2 className="h-4 w-4" />
								Setup
								{sol.setup_complete === false && (
									<AlertTriangle className="ml-0.5 h-3.5 w-3.5 text-yellow-500" />
								)}
							</TabsTrigger>
						</TabsList>

						<TabsContent value="readme" className="flex-1 min-h-0">
							<SolutionReadmeTab
								readme={readmeData?.readme ?? null}
								canEdit={isPlatformAdmin}
								onSave={async (md) => {
									await putSolutionReadme(sol.id, md.trim() ? md : null);
									toast.success("README saved");
									invalidate();
								}}
							/>
						</TabsContent>

						{ENTITY_TABS.map(({ key }) => (
							<TabsContent key={key} value={key} className="flex-1 min-h-0">
								<EntityTabContent
									kind={key}
									items={itemsFor(key)}
									solutionId={sol.id}
								/>
							</TabsContent>
						))}

						<TabsContent value="configs" className="flex-1 min-h-0">
							{data.configs && data.configs.length > 0 ? (
								<div className="space-y-3">
									{data.configs.map((cfg) => (
										<ConfigRow
											key={cfg.id}
											config={cfg}
											orgId={sol.organization_id ?? null}
											onSaved={invalidate}
										/>
									))}
								</div>
							) : (
								<div className="rounded-lg border py-12 text-center text-sm text-muted-foreground">
									This Solution declares no configuration.
								</div>
							)}
						</TabsContent>

						<TabsContent value="setup" className="flex-1 min-h-0">
							{setupError ? (
								<div className="rounded-lg border border-destructive/40 bg-destructive/5 py-12 text-center text-sm text-destructive">
									{setupError instanceof Error
										? setupError.message
										: "Couldn't load setup status"}
								</div>
							) : (
								<SolutionSetupWizard
									items={setupData?.items ?? []}
									setupComplete={setupData?.setup_complete ?? sol.setup_complete}
									onFinish={invalidate}
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
							)}
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
						onExport={(mode, password, includeData) =>
							exportMut.mutate({ mode, password, includeData })
						}
						isPending={exportMut.isPending}
					/>

					{/* Delete / uninstall dialog (type-to-confirm) */}
					<Dialog
						open={deleteOpen}
						onOpenChange={(o) => {
							if (!o) {
								setDeleteOpen(false);
								setDeleteConfirm("");
							}
						}}
					>
						<DialogContent data-testid="delete-dialog">
							<DialogHeader>
								<DialogTitle>Uninstall {sol.name}?</DialogTitle>
								<DialogDescription asChild>
									<div className="space-y-2 text-sm text-muted-foreground">
										<p>
											Workflows, apps, forms, and agents will be
											removed.
										</p>
										<p>
											<span className="font-medium text-foreground">
												Tables (and their data) and config values
												are kept as orphaned data
											</span>{" "}
											— they will be reattached if you reinstall this
											Solution.
										</p>
										<p>The git repository is not touched.</p>
									</div>
								</DialogDescription>
							</DialogHeader>

							<div className="space-y-2">
								<Label htmlFor="delete-confirm">
									Type{" "}
									<span className="font-mono font-semibold text-foreground">
										{sol.name}
									</span>{" "}
									to confirm
								</Label>
								<Input
									id="delete-confirm"
									data-testid="delete-confirm-input"
									value={deleteConfirm}
									onChange={(e) => setDeleteConfirm(e.target.value)}
									autoComplete="off"
								/>
							</div>

							<DialogFooter>
								<Button
									variant="outline"
									onClick={() => {
										setDeleteOpen(false);
										setDeleteConfirm("");
									}}
								>
									Cancel
								</Button>
								<Button
									variant="destructive"
									data-testid="confirm-delete"
									disabled={
										deleteConfirm !== sol.name || deleteMut.isPending
									}
									onClick={() => deleteMut.mutate()}
								>
									{deleteMut.isPending && (
										<Loader2 className="mr-2 h-4 w-4 animate-spin" />
									)}
									Uninstall
								</Button>
							</DialogFooter>
						</DialogContent>
					</Dialog>
				</>
			) : null}
		</div>
	);
}
