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
	Bot,
	Database,
	SlidersHorizontal,
	CheckCircle2,
	Circle,
	AlertTriangle,
	Pencil,
	Trash2,
	Download,
	Loader2,
} from "lucide-react";
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
import { SearchBox } from "@/components/search/SearchBox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { useOrganizations } from "@/hooks/useOrganizations";
import { CreateEditSolution } from "@/components/solutions/CreateEditSolution";
import {
	getSolutionEntities,
	deleteSolution,
	exportSolution,
	setSolutionConfig,
} from "@/services/solutions";
import type { components } from "@/lib/v1";

type EntitySummary = components["schemas"]["SolutionEntitySummary"];
type ConfigStatus = components["schemas"]["SolutionConfigStatus"];
type ConfigType = components["schemas"]["ConfigType"];

type TabKey = "workflows" | "apps" | "forms" | "agents" | "tables" | "configs";

const ENTITY_TABS: {
	key: Exclude<TabKey, "configs">;
	label: string;
	Icon: typeof Workflow;
}[] = [
	{ key: "workflows", label: "Workflows", Icon: Workflow },
	{ key: "apps", label: "Apps", Icon: AppWindow },
	{ key: "forms", label: "Forms", Icon: FileCode },
	{ key: "agents", label: "Agents", Icon: Bot },
	{ key: "tables", label: "Tables", Icon: Database },
];

/** Per-entity-page link target, carrying the `?from` so the entity page can
 * offer a "back to this Solution" affordance (consumed in Task 19b). */
function entityHref(
	kind: Exclude<TabKey, "configs">,
	entity: EntitySummary,
	solutionId: string,
): string {
	const from = `?from=solution:${solutionId}`;
	switch (kind) {
		case "tables":
			return `/tables/${entity.id}${from}`;
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

const ENTITY_TAB_LABEL: Record<Exclude<TabKey, "configs">, string> = {
	workflows: "workflows",
	apps: "apps",
	forms: "forms",
	agents: "agents",
	tables: "tables",
};

function EntityTabContent({
	kind,
	items,
	solutionId,
}: {
	kind: Exclude<TabKey, "configs">;
	items: EntitySummary[];
	solutionId: string;
}) {
	const navigate = useNavigate();
	const [search, setSearch] = useState("");

	const q = search.trim().toLowerCase();
	const visible = q
		? items.filter((e) => e.name.toLowerCase().includes(q))
		: items;

	if (items.length === 0) {
		return (
			<div className="text-sm text-muted-foreground py-8 text-center rounded-2xl border border-dashed">
				This Solution deploys no {ENTITY_TAB_LABEL[kind]}.
			</div>
		);
	}
	return (
		<div className="flex flex-col gap-3">
			<SearchBox
				value={search}
				onChange={setSearch}
				placeholder={`Search ${ENTITY_TAB_LABEL[kind]}...`}
			/>
			{visible.length === 0 ? (
				<div className="text-sm text-muted-foreground py-8 text-center rounded-2xl border border-dashed">
					No {ENTITY_TAB_LABEL[kind]} match “{search.trim()}”.
				</div>
			) : (
				<DataTable>
					<DataTableHeader>
						<DataTableRow>
							<DataTableHead>Name</DataTableHead>
						</DataTableRow>
					</DataTableHeader>
					<DataTableBody>
						{visible.map((entity) => {
							const href = entityHref(kind, entity, solutionId);
							return (
								<DataTableRow
									key={entity.id}
									clickable
									href={href}
									onClick={() => navigate(href)}
								>
									<DataTableCell className="font-medium">
										{entity.name}
									</DataTableCell>
								</DataTableRow>
							);
						})}
					</DataTableBody>
				</DataTable>
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

	const [tab, setTab] = useState<TabKey>("workflows");
	const [editOpen, setEditOpen] = useState(false);
	const [deleteOpen, setDeleteOpen] = useState(false);
	const [deleteConfirm, setDeleteConfirm] = useState("");

	const { data, isLoading, error } = useQuery({
		queryKey: ["solutions", solutionId, "entities"],
		queryFn: () => getSolutionEntities(solutionId!),
		enabled: !!solutionId,
	});

	const invalidate = () =>
		queryClient.invalidateQueries({
			queryKey: ["solutions", solutionId, "entities"],
		});

	const sol = data?.solution;

	const exportMut = useMutation({
		mutationFn: () => exportSolution(solutionId!),
		onSuccess: ({ blob, filename }) => {
			const url = URL.createObjectURL(blob);
			const a = document.createElement("a");
			a.href = url;
			a.download = filename;
			document.body.appendChild(a);
			a.click();
			a.remove();
			URL.revokeObjectURL(url);
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

	const orgName = useMemo(() => {
		if (!sol?.organization_id) return "Global";
		return (
			organizations?.find((o) => o.id === sol.organization_id)?.name ??
			sol.organization_id
		);
	}, [sol, organizations]);

	const counts = useMemo(() => {
		return {
			workflows: data?.workflows?.length ?? 0,
			apps: data?.apps?.length ?? 0,
			forms: data?.forms?.length ?? 0,
			agents: data?.agents?.length ?? 0,
			tables: data?.tables?.length ?? 0,
			configs: data?.configs?.length ?? 0,
		} satisfies Record<TabKey, number>;
	}, [data]);

	const itemsFor = (key: Exclude<TabKey, "configs">): EntitySummary[] =>
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
							<h1 className="text-3xl font-extrabold tracking-tight">
								{sol.name}
							</h1>
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
							</div>
						</div>
						<div className="flex shrink-0 gap-2">
							<Button
								variant="outline"
								data-testid="export-solution"
								disabled={exportMut.isPending}
								onClick={() => exportMut.mutate()}
							>
								{exportMut.isPending ? (
									<Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
								) : (
									<Download className="mr-1.5 h-4 w-4" />
								)}
								Export
							</Button>
							<Button
								variant="outline"
								data-testid="edit-solution"
								onClick={() => setEditOpen(true)}
							>
								<Pencil className="mr-1.5 h-4 w-4" />
								Edit
							</Button>
							<Button
								variant="outline"
								data-testid="delete-solution"
								className="text-destructive hover:text-destructive"
								onClick={() => {
									setDeleteConfirm("");
									setDeleteOpen(true);
								}}
							>
								<Trash2 className="mr-1.5 h-4 w-4" />
								Delete
							</Button>
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
						</TabsList>

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
