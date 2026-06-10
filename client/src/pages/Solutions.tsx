/**
 * Solutions Page
 *
 * Operator home for managing Solution installs. Lists installed Solution
 * packages as cards, supports a whole-page drag-and-drop zip install
 * (preview -> scope -> config values -> deploy), and a type-to-confirm,
 * non-destructive uninstall.
 */

import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
	Boxes,
	Upload,
	GitBranch,
	HardDriveUpload,
	Globe,
	Building2,
	Trash2,
	Loader2,
	Workflow,
	AppWindow,
	FileCode,
	Bot,
	Database,
	SlidersHorizontal,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@/components/ui/select";
import { useOrganizations } from "@/hooks/useOrganizations";
import {
	deleteSolution,
	installSolution,
	listSolutions,
	previewInstall,
	type Solution,
	type SolutionInstallPreview,
	type SolutionUpgradeDiff,
} from "@/services/solutions";
import type { components } from "@/lib/v1";

/** A declared config schema item on a preview, narrowed from the loose dict. */
interface PreviewConfigSchema {
	key: string;
	type: string;
	required: boolean;
	description: string | null;
}

function asConfigSchemas(
	raw: SolutionInstallPreview["config_schemas"],
): PreviewConfigSchema[] {
	if (!raw) return [];
	return raw
		.map((item) => {
			const key = typeof item.key === "string" ? item.key : "";
			if (!key) return null;
			return {
				key,
				type: typeof item.type === "string" ? item.type : "string",
				required: item.required === true,
				description:
					typeof item.description === "string"
						? item.description
						: null,
			};
		})
		.filter((x): x is PreviewConfigSchema => x !== null);
}

function isSecretType(type: string): boolean {
	const t = type.toLowerCase();
	return t === "secret" || t === "password";
}

/** Summary chips of what an install/preview creates. */
function EntitySummary({
	preview,
}: {
	preview: SolutionInstallPreview;
}) {
	const items: { icon: typeof Workflow; label: string; count: number }[] = [
		{
			icon: Workflow,
			label: "workflows",
			count: preview.workflows?.length ?? 0,
		},
		{ icon: AppWindow, label: "apps", count: preview.apps?.length ?? 0 },
		{ icon: FileCode, label: "forms", count: preview.forms?.length ?? 0 },
		{ icon: Bot, label: "agents", count: preview.agents?.length ?? 0 },
		{ icon: Database, label: "tables", count: preview.tables?.length ?? 0 },
		{
			icon: SlidersHorizontal,
			label: "configs",
			count: preview.config_schemas?.length ?? 0,
		},
	];
	const present = items.filter((i) => i.count > 0);
	if (present.length === 0) {
		return (
			<p className="text-sm text-muted-foreground">
				This package declares no entities.
			</p>
		);
	}
	return (
		<div className="flex flex-wrap gap-2" data-testid="preview-summary">
			{present.map(({ icon: Icon, label, count }) => (
				<Badge key={label} variant="secondary" className="gap-1.5 py-1">
					<Icon className="h-3.5 w-3.5" />
					<span className="tabular-nums font-semibold">{count}</span>
					<span className="text-muted-foreground">{label}</span>
				</Badge>
			))}
		</div>
	);
}

/** One "Added: …" / "Removed: …" pair for an entity kind; omits empty lists. */
function DiffSection({
	label,
	added,
	removed,
}: {
	label: string;
	added: string[];
	removed: string[];
}) {
	if (added.length === 0 && removed.length === 0) return null;
	return (
		<div className="text-sm">
			<span className="font-medium">{label}</span>
			{added.length > 0 && (
				<p className="text-green-600 dark:text-green-500">
					Added: {added.join(", ")}
				</p>
			)}
			{removed.length > 0 && (
				<p className="text-destructive">Removed: {removed.join(", ")}</p>
			)}
		</div>
	);
}

/** Render a config declaration change as "KEY: secret→string, required→optional". */
function describeConfigChange(
	change: components["schemas"]["SolutionConfigSchemaChange"],
): string {
	const parts: string[] = [];
	if (change.from.type !== change.to.type) {
		parts.push(`${change.from.type}→${change.to.type}`);
	}
	if (change.from.required !== change.to.required) {
		parts.push(
			change.from.required ? "required→optional" : "optional→required",
		);
	}
	return `${change.key}: ${parts.join(", ")}`;
}

/** What an upgrade changes, per entity type plus config declarations. */
function UpgradeDiffView({ diff }: { diff: SolutionUpgradeDiff }) {
	const entitySections: { label: string; key: keyof SolutionUpgradeDiff }[] = [
		{ label: "Workflows", key: "workflows" },
		{ label: "Apps", key: "apps" },
		{ label: "Forms", key: "forms" },
		{ label: "Agents", key: "agents" },
		{ label: "Tables", key: "tables" },
	];
	const configs = diff.config_schemas;
	const hasConfigDiff =
		(configs?.added?.length ?? 0) > 0 ||
		(configs?.removed?.length ?? 0) > 0 ||
		(configs?.changed?.length ?? 0) > 0;
	const hasEntityDiff = entitySections.some(({ key }) => {
		const d = diff[key] as SolutionUpgradeDiff["workflows"] | undefined;
		return (d?.added?.length ?? 0) > 0 || (d?.removed?.length ?? 0) > 0;
	});
	if (!hasEntityDiff && !hasConfigDiff) {
		return (
			<p className="text-sm text-muted-foreground">
				No entity or configuration changes.
			</p>
		);
	}
	return (
		<div className="space-y-3" data-testid="upgrade-diff">
			{entitySections.map(({ label, key }) => {
				const d = diff[key] as
					| SolutionUpgradeDiff["workflows"]
					| undefined;
				return (
					<DiffSection
						key={key}
						label={label}
						added={d?.added ?? []}
						removed={d?.removed ?? []}
					/>
				);
			})}
			{hasConfigDiff && configs && (
				<div className="text-sm">
					<span className="font-medium">Configs</span>
					{(configs.added?.length ?? 0) > 0 && (
						<p className="text-green-600 dark:text-green-500">
							Added: {(configs.added ?? []).join(", ")}
						</p>
					)}
					{(configs.removed?.length ?? 0) > 0 && (
						<p className="text-destructive">
							Removed: {(configs.removed ?? []).join(", ")}
						</p>
					)}
					{(configs.changed ?? []).map((change) => (
						<p key={change.key} className="text-muted-foreground">
							{describeConfigChange(change)}
						</p>
					))}
				</div>
			)}
		</div>
	);
}

export function Solutions() {
	const navigate = useNavigate();
	const queryClient = useQueryClient();
	const fileInputRef = useRef<HTMLInputElement>(null);
	const dragDepth = useRef(0);

	const [isDragging, setIsDragging] = useState(false);

	// Preview / install dialog state.
	const [installFile, setInstallFile] = useState<File | null>(null);
	const [preview, setPreview] = useState<SolutionInstallPreview | null>(null);
	const [previewError, setPreviewError] = useState<string | null>(null);
	const [installError, setInstallError] = useState<string | null>(null);
	const [scopeOrgId, setScopeOrgId] = useState<string>("__global__");
	const [configValues, setConfigValues] = useState<Record<string, string>>(
		{},
	);
	// Set when an install attempt 409'd as a downgrade; gates a confirm step
	// before retrying with force=true.
	const [downgradeConfirm, setDowngradeConfirm] = useState(false);

	// Delete dialog state.
	const [deleteTarget, setDeleteTarget] = useState<Solution | null>(null);
	const [deleteConfirm, setDeleteConfirm] = useState("");

	const { data: organizations } = useOrganizations();

	const {
		data: solutionsData,
		isLoading,
		error: listError,
	} = useQuery({
		queryKey: ["solutions"],
		queryFn: () => listSolutions(),
	});
	const solutions = solutionsData?.solutions ?? [];

	const getOrgName = (orgId: string | null | undefined): string => {
		if (!orgId) return "Global";
		const org = organizations?.find((o) => o.id === orgId);
		return org?.name ?? orgId;
	};

	const previewMutation = useMutation({
		mutationFn: (file: File) => previewInstall(file),
		onSuccess: (data) => {
			setPreview(data);
			setPreviewError(null);
			// Default to Global scope; operator picks an org if desired.
			setScopeOrgId("__global__");
			setConfigValues({});
		},
		onError: (err: unknown) => {
			setPreviewError(
				err instanceof Error ? err.message : "Failed to read package",
			);
		},
	});

	const installMutation = useMutation({
		mutationFn: ({ force }: { force: boolean }) => {
			if (!installFile) throw new Error("No file selected");
			const values: Record<string, string> = {};
			for (const [k, v] of Object.entries(configValues)) {
				if (v.trim() !== "") values[k] = v;
			}
			return installSolution({
				file: installFile,
				organizationId:
					scopeOrgId === "__global__" ? "" : scopeOrgId,
				configValues: values,
				force,
			});
		},
		onSuccess: (created) => {
			queryClient.invalidateQueries({ queryKey: ["solutions"] });
			toast.success(
				preview?.existing_install
					? `Upgraded ${created.name}`
					: `Installed ${created.name}`,
			);
			closeInstallDialog();
			navigate(`/solutions/${created.id}`);
		},
		onError: (err: unknown) => {
			const message =
				err instanceof Error ? err.message : "Failed to install";
			if (message.includes("older than installed")) {
				// Server's downgrade guard (409) — ask before forcing.
				setInstallError(null);
				setDowngradeConfirm(true);
				return;
			}
			setInstallError(message);
		},
	});

	const deleteMutation = useMutation({
		mutationFn: (id: string) => deleteSolution(id),
		onSuccess: (summary) => {
			queryClient.invalidateQueries({ queryKey: ["solutions"] });
			toast.success("Solution uninstalled", {
				description: `Removed ${summary.workflows_deleted} workflows, ${summary.apps_deleted} apps, ${summary.forms_deleted} forms, ${summary.agents_deleted} agents. Kept ${summary.tables_orphaned} tables and ${summary.config_values_orphaned} config values as orphaned data.`,
			});
			setDeleteTarget(null);
			setDeleteConfirm("");
		},
		onError: (err: unknown) => {
			toast.error("Failed to uninstall", {
				description:
					err instanceof Error ? err.message : "Unknown error",
			});
		},
	});

	function openInstallForFile(file: File) {
		setInstallFile(file);
		setPreview(null);
		setPreviewError(null);
		setInstallError(null);
		setDowngradeConfirm(false);
		previewMutation.mutate(file);
	}

	function closeInstallDialog() {
		setInstallFile(null);
		setPreview(null);
		setPreviewError(null);
		setInstallError(null);
		setDowngradeConfirm(false);
		setConfigValues({});
		setScopeOrgId("__global__");
		previewMutation.reset();
		installMutation.reset();
	}

	function handleFilePicked(e: React.ChangeEvent<HTMLInputElement>) {
		const file = e.target.files?.[0];
		if (file) openInstallForFile(file);
		// Reset so picking the same file again re-fires change.
		e.target.value = "";
	}

	// Whole-page drag-and-drop handlers.
	function handleDragEnter(e: React.DragEvent) {
		if (!e.dataTransfer?.types?.includes("Files")) return;
		e.preventDefault();
		dragDepth.current += 1;
		setIsDragging(true);
	}
	function handleDragOver(e: React.DragEvent) {
		if (!e.dataTransfer?.types?.includes("Files")) return;
		e.preventDefault();
	}
	function handleDragLeave(e: React.DragEvent) {
		e.preventDefault();
		dragDepth.current = Math.max(0, dragDepth.current - 1);
		if (dragDepth.current === 0) setIsDragging(false);
	}
	function handleDrop(e: React.DragEvent) {
		e.preventDefault();
		dragDepth.current = 0;
		setIsDragging(false);
		const file = e.dataTransfer?.files?.[0];
		if (file) openInstallForFile(file);
	}

	const declaredConfigs = preview ? asConfigSchemas(preview.config_schemas) : [];
	const existingInstall = preview?.existing_install ?? null;
	const isUpgrade = existingInstall !== null;

	return (
		<div
			data-testid="install-dropzone"
			onDragEnter={handleDragEnter}
			onDragOver={handleDragOver}
			onDragLeave={handleDragLeave}
			onDrop={handleDrop}
			className="relative h-full flex flex-col space-y-6 max-w-7xl mx-auto"
		>
			<input
				ref={fileInputRef}
				type="file"
				accept=".zip,application/zip"
				className="hidden"
				data-testid="install-file-input"
				onChange={handleFilePicked}
			/>

			{/* Drag overlay */}
			{isDragging && (
				<div className="pointer-events-none absolute inset-0 z-50 flex items-center justify-center rounded-xl border-2 border-dashed border-primary bg-background/80 backdrop-blur-sm">
					<div className="flex flex-col items-center gap-3 text-primary">
						<Upload className="h-10 w-10" />
						<p className="text-lg font-semibold">
							Drop a Solution .zip to install
						</p>
					</div>
				</div>
			)}

			{/* Header */}
			<div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
				<div>
					<h1 className="text-3xl font-extrabold tracking-tight sm:text-4xl">
						Solutions
					</h1>
					<p className="mt-2 text-muted-foreground">
						Installed Solution packages
					</p>
				</div>
				<Button onClick={() => fileInputRef.current?.click()}>
					<Upload className="mr-2 h-4 w-4" />
					Install Solution
				</Button>
			</div>

			<div className="flex-1 min-h-0 overflow-auto">
				{isLoading ? (
					<div className="grid grid-cols-1 gap-4 sm:grid-cols-[repeat(auto-fill,minmax(320px,1fr))]">
						{[...Array(3)].map((_, i) => (
							<Skeleton key={i} className="h-36 w-full" />
						))}
					</div>
				) : listError ? (
					<Card>
						<CardContent className="py-10 text-center text-sm text-destructive">
							{listError instanceof Error
								? listError.message
								: "Failed to load Solutions"}
						</CardContent>
					</Card>
				) : solutions.length === 0 ? (
					<button
						type="button"
						onClick={() => fileInputRef.current?.click()}
						className="flex w-full flex-col items-center justify-center rounded-xl border-2 border-dashed py-20 text-center transition-colors hover:border-primary/60 hover:bg-accent/30"
					>
						<Boxes className="h-12 w-12 text-muted-foreground" />
						<h3 className="mt-4 text-lg font-semibold">
							No Solutions installed yet
						</h3>
						<p className="mt-2 max-w-sm text-sm text-muted-foreground">
							Drag a Solution .zip anywhere on this page, or click
							to choose a file to install.
						</p>
					</button>
				) : (
					<div className="grid grid-cols-1 gap-4 sm:grid-cols-[repeat(auto-fill,minmax(320px,1fr))]">
						{solutions.map((sol) => (
							<div
								key={sol.id}
								data-testid="install-card"
								role="button"
								tabIndex={0}
								onClick={() => navigate(`/solutions/${sol.id}`)}
								onKeyDown={(e) => {
									if (e.key === "Enter" || e.key === " ") {
										e.preventDefault();
										navigate(`/solutions/${sol.id}`);
									}
								}}
								className="group relative flex cursor-pointer flex-col overflow-hidden rounded-[10px] border bg-card transition-colors hover:border-border/80 hover:-translate-y-px focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
							>
								<div className="flex items-start justify-between gap-3 border-b px-4 py-3">
									<div className="flex min-w-0 items-center gap-2">
										<Boxes className="h-4 w-4 shrink-0 text-muted-foreground" />
										<div className="min-w-0">
											<div className="truncate text-[14.5px] font-semibold">
												{sol.name}
											</div>
											<div className="truncate text-xs text-muted-foreground">
												{sol.slug}
											</div>
										</div>
									</div>
									<Button
										type="button"
										variant="ghost"
										size="icon"
										className="h-7 w-7 shrink-0 text-muted-foreground hover:text-destructive"
										title="Uninstall"
										aria-label="Uninstall"
										onClick={(e) => {
											e.stopPropagation();
											setDeleteTarget(sol);
											setDeleteConfirm("");
										}}
									>
										<Trash2 className="h-4 w-4" />
									</Button>
								</div>
								<div className="flex items-center gap-2 px-4 py-3">
									<Badge
										variant={
											sol.organization_id
												? "outline"
												: "default"
										}
										className="gap-1"
									>
										{sol.organization_id ? (
											<Building2 className="h-3 w-3" />
										) : (
											<Globe className="h-3 w-3" />
										)}
										{getOrgName(sol.organization_id)}
									</Badge>
									<Badge variant="secondary" className="gap-1">
										{sol.git_connected ? (
											<GitBranch className="h-3 w-3" />
										) : (
											<HardDriveUpload className="h-3 w-3" />
										)}
										{sol.git_connected ? "Git" : "Manual"}
									</Badge>
									{sol.version && (
										<Badge variant="outline">
											v{sol.version}
										</Badge>
									)}
								</div>
							</div>
						))}
					</div>
				)}
			</div>

			{/* Preview / Install dialog */}
			<Dialog
				open={installFile !== null}
				onOpenChange={(open) => {
					if (!open) closeInstallDialog();
				}}
			>
				<DialogContent
					className="max-h-[85vh] overflow-y-auto sm:max-w-lg"
					data-testid="preview-dialog"
				>
					<DialogHeader>
						<DialogTitle>
							{isUpgrade && existingInstall
								? `Upgrade ${existingInstall.name} v${existingInstall.version ?? "?"} → v${preview?.version ?? "?"}`
								: "Install Solution"}
						</DialogTitle>
						<DialogDescription>
							{isUpgrade
								? "This package upgrades an existing install in place. Review the changes below."
								: "Review what this package creates, choose a scope, and set any required configuration values."}
						</DialogDescription>
					</DialogHeader>

					{previewMutation.isPending ? (
						<div className="flex items-center gap-2 py-8 text-muted-foreground">
							<Loader2 className="h-4 w-4 animate-spin" />
							Reading package…
						</div>
					) : previewError ? (
						<p className="py-4 text-sm text-destructive">
							{previewError}
						</p>
					) : preview && downgradeConfirm ? (
						<div
							data-testid="downgrade-confirm"
							className="space-y-2 py-2"
						>
							<p className="text-sm font-medium">
								This is a DOWNGRADE: v
								{existingInstall?.version ?? "?"} → v
								{preview.version ?? "?"}. Replace anyway?
							</p>
							<p className="text-xs text-muted-foreground">
								The installed version is newer than this package.
								Replacing it will overwrite the install's content
								with the older version.
							</p>
						</div>
					) : preview ? (
						<div className="space-y-5">
							{isUpgrade ? (
								<UpgradeDiffView diff={preview.diff ?? {}} />
							) : (
								<div>
									<p className="text-sm">
										This will install{" "}
										<span className="font-semibold">
											{preview.name ?? "this Solution"}
										</span>
										{preview.slug ? (
											<span className="text-muted-foreground">
												{" "}
												({preview.slug})
											</span>
										) : null}
										.
									</p>
									<div className="mt-3">
										<EntitySummary preview={preview} />
									</div>
								</div>
							)}

							{/* Scope picker — only for fresh installs. An upgrade
							    targets the existing install; there is no
							    "create a second install" path here. */}
							{!isUpgrade && (
								<div className="space-y-2">
									<Label htmlFor="solution-scope">Scope</Label>
									<Select
										value={scopeOrgId}
										onValueChange={setScopeOrgId}
									>
										<SelectTrigger
											id="solution-scope"
											data-testid="scope-select"
										>
											<SelectValue />
										</SelectTrigger>
										<SelectContent>
											<SelectItem value="__global__">
												Global
											</SelectItem>
											{(organizations ?? []).map((org) => (
												<SelectItem
													key={org.id}
													value={org.id}
												>
													{org.name}
												</SelectItem>
											))}
										</SelectContent>
									</Select>
								</div>
							)}

							{/* Config values */}
							{declaredConfigs.length > 0 && (
								<div className="space-y-3">
									<p className="text-sm font-medium">
										Configuration
									</p>
									{declaredConfigs.map((cfg) => {
										const value = configValues[cfg.key] ?? "";
										const missing =
											cfg.required && value.trim() === "";
										return (
											<div
												key={cfg.key}
												className="space-y-1"
											>
												<Label
													htmlFor={`cfg-${cfg.key}`}
													className="flex items-center gap-1"
												>
													{cfg.key}
													{cfg.required && (
														<span
															className="text-destructive"
															aria-hidden
														>
															*
														</span>
													)}
												</Label>
												{cfg.description && (
													<p className="text-xs text-muted-foreground">
														{cfg.description}
													</p>
												)}
												<Input
													id={`cfg-${cfg.key}`}
													type={
														isSecretType(cfg.type)
															? "password"
															: "text"
													}
													value={value}
													onChange={(e) =>
														setConfigValues(
															(prev) => ({
																...prev,
																[cfg.key]:
																	e.target
																		.value,
															}),
														)
													}
												/>
												{missing && (
													<p className="text-xs text-yellow-600 dark:text-yellow-500">
														Required — you can still
														install and set this
														later.
													</p>
												)}
											</div>
										);
									})}
								</div>
							)}

							{installError && (
								<p className="text-sm text-destructive">
									{installError}
								</p>
							)}
						</div>
					) : null}

					<DialogFooter>
						{downgradeConfirm ? (
							<>
								<Button
									variant="outline"
									onClick={() => setDowngradeConfirm(false)}
									disabled={installMutation.isPending}
								>
									Cancel
								</Button>
								<Button
									variant="destructive"
									onClick={() =>
										installMutation.mutate({ force: true })
									}
									disabled={installMutation.isPending}
									data-testid="confirm-downgrade"
								>
									{installMutation.isPending && (
										<Loader2 className="mr-2 h-4 w-4 animate-spin" />
									)}
									Replace anyway
								</Button>
							</>
						) : (
							<>
								<Button
									variant="outline"
									onClick={closeInstallDialog}
									disabled={installMutation.isPending}
								>
									Cancel
								</Button>
								<Button
									onClick={() =>
										installMutation.mutate({ force: false })
									}
									disabled={
										!preview ||
										previewMutation.isPending ||
										installMutation.isPending
									}
									data-testid="confirm-install"
								>
									{installMutation.isPending && (
										<Loader2 className="mr-2 h-4 w-4 animate-spin" />
									)}
									{isUpgrade ? "Upgrade" : "Install"}
								</Button>
							</>
						)}
					</DialogFooter>
				</DialogContent>
			</Dialog>

			{/* Delete / uninstall dialog */}
			<Dialog
				open={deleteTarget !== null}
				onOpenChange={(open) => {
					if (!open) {
						setDeleteTarget(null);
						setDeleteConfirm("");
					}
				}}
			>
				<DialogContent data-testid="delete-dialog">
					<DialogHeader>
						<DialogTitle>
							Uninstall {deleteTarget?.name}?
						</DialogTitle>
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
									— they will be reattached if you reinstall
									this Solution, and remain visible via "Show
									orphaned" on the Tables and Configs pages.
								</p>
								<p>The git repository is not touched.</p>
							</div>
						</DialogDescription>
					</DialogHeader>

					<div className="space-y-2">
						<Label htmlFor="delete-confirm">
							Type{" "}
							<span className="font-mono font-semibold text-foreground">
								{deleteTarget?.name}
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
								setDeleteTarget(null);
								setDeleteConfirm("");
							}}
						>
							Cancel
						</Button>
						<Button
							variant="destructive"
							data-testid="confirm-delete"
							disabled={
								deleteConfirm !== deleteTarget?.name ||
								deleteMutation.isPending
							}
							onClick={() => {
								if (deleteTarget)
									deleteMutation.mutate(deleteTarget.id);
							}}
						>
							{deleteMutation.isPending && (
								<Loader2 className="mr-2 h-4 w-4 animate-spin" />
							)}
							Uninstall
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>
		</div>
	);
}
