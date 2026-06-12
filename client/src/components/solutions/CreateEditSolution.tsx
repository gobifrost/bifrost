/**
 * CreateEditSolution — the single, state-driven dialog for both installing a
 * Solution (create) and editing an existing install (edit).
 *
 * Create mode: a dropzone (or a prefilled file when the operator dropped one
 * on the page), the standard Organization selector at the top, the package
 * preview (entity summary / upgrade diff / downgrade confirm), declared
 * config values, and an optional git-connect section.
 *
 * Edit mode: name + Organization + global repo access + the same git section.
 *
 * Git connection is driven by GitHub being configured in Settings (a saved
 * token) — there is no manual "git connected" toggle. An install is
 * git-connected exactly when it has a repository URL; the section offers to
 * create a repository named `solution-<slug>-<suffix>` via the saved token.
 */

import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import {
	AppWindow,
	Bot,
	Database,
	FileArchive,
	FileCode,
	GitBranch,
	Loader2,
	Plus,
	SlidersHorizontal,
	Upload,
	Workflow,
	X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import {
	useGitHubConfig,
	useCreateGitHubRepository,
} from "@/hooks/useGitHub";
import {
	installSolution,
	previewInstall,
	updateSolution,
	type Solution,
	type SolutionInstallPreview,
	type SolutionUpdate,
	type SolutionUpgradeDiff,
} from "@/services/solutions";
import type { components } from "@/lib/v1";

export type CreateEditSolutionMode =
	| { kind: "create"; file?: File }
	| { kind: "edit"; solution: Solution };

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
					typeof item.description === "string" ? item.description : null,
			};
		})
		.filter((x): x is PreviewConfigSchema => x !== null);
}

function isSecretType(type: string): boolean {
	const t = type.toLowerCase();
	return t === "secret" || t === "password";
}

/** Summary chips of what an install/preview creates. */
function EntitySummary({ preview }: { preview: SolutionInstallPreview }) {
	const items: { icon: typeof Workflow; label: string; count: number }[] = [
		{ icon: Workflow, label: "workflows", count: preview.workflows?.length ?? 0 },
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
				const d = diff[key] as SolutionUpgradeDiff["workflows"] | undefined;
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

/** Random 6-char suffix for suggested repository names. */
function repoSuffix(): string {
	return Math.random().toString(36).slice(2, 8);
}

/**
 * Git connection for an install. Connection state is derived: GitHub must be
 * configured in Settings (saved token), and the install must have a repo URL.
 */
function GitRepoSection({
	slug,
	repoUrl,
	onRepoUrlChange,
}: {
	slug: string | null;
	repoUrl: string;
	onRepoUrlChange: (url: string) => void;
}) {
	const { data: ghConfig, isLoading } = useGitHubConfig();
	const createRepo = useCreateGitHubRepository();
	const tokenSaved = ghConfig?.token_saved === true;

	if (isLoading) return null;

	if (!tokenSaved) {
		return (
			<div className="rounded-lg border p-3" data-testid="git-section">
				<div className="flex items-center gap-2 text-sm font-medium">
					<GitBranch className="h-4 w-4 text-muted-foreground" />
					Git repository
				</div>
				<p className="mt-1 text-xs text-muted-foreground">
					GitHub isn't configured.{" "}
					<Link to="/settings/github" className="underline hover:text-foreground">
						Connect GitHub in Settings
					</Link>{" "}
					to back installs with a repository.
				</p>
			</div>
		);
	}

	const suggestedName = `solution-${slug || "install"}-${repoSuffix()}`;

	return (
		<div className="space-y-2 rounded-lg border p-3" data-testid="git-section">
			<div className="flex items-center justify-between gap-2">
				<div className="flex items-center gap-2 text-sm font-medium">
					<GitBranch className="h-4 w-4 text-muted-foreground" />
					Git repository
				</div>
				<Badge variant={repoUrl ? "default" : "secondary"}>
					{repoUrl ? "Connected" : "Not connected"}
				</Badge>
			</div>
			<Input
				data-testid="git-repo-url"
				value={repoUrl}
				placeholder="https://github.com/org/repo"
				onChange={(e) => onRepoUrlChange(e.target.value)}
			/>
			<Button
				type="button"
				variant="outline"
				size="sm"
				data-testid="create-repo"
				disabled={createRepo.isPending}
				onClick={() =>
					createRepo.mutate(
						{
							body: {
								name: suggestedName,
								description: `Bifrost Solution ${slug ?? ""}`.trim(),
								private: true,
							},
						},
						{
							onSuccess: (repo) => {
								onRepoUrlChange(repo.url);
								toast.success(`Created ${repo.full_name}`);
							},
							onError: (err: unknown) => {
								toast.error(
									err instanceof Error
										? err.message
										: "Failed to create repository",
								);
							},
						},
					)
				}
			>
				{createRepo.isPending ? (
					<Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
				) : (
					<Plus className="mr-1.5 h-3.5 w-3.5" />
				)}
				Create {suggestedName}
			</Button>
		</div>
	);
}

export function CreateEditSolution({
	mode,
	open,
	onClose,
	onSaved,
}: {
	mode: CreateEditSolutionMode;
	open: boolean;
	onClose: () => void;
	/** Called after a successful install (with the created install) or edit. */
	onSaved: (solution: Solution) => void;
}) {
	return (
		<Dialog open={open} onOpenChange={(o) => !o && onClose()}>
			<DialogContent
				className="max-h-[85vh] overflow-y-auto sm:max-w-lg"
				data-testid="solution-dialog"
			>
				{mode.kind === "create" ? (
					<CreateBody
						initialFile={mode.file ?? null}
						onClose={onClose}
						onSaved={onSaved}
					/>
				) : (
					<EditBody solution={mode.solution} onClose={onClose} onSaved={onSaved} />
				)}
			</DialogContent>
		</Dialog>
	);
}

function CreateBody({
	initialFile,
	onClose,
	onSaved,
}: {
	initialFile: File | null;
	onClose: () => void;
	onSaved: (solution: Solution) => void;
}) {
	const queryClient = useQueryClient();
	const fileInputRef = useRef<HTMLInputElement>(null);

	const [file, setFile] = useState<File | null>(initialFile);
	const [orgId, setOrgId] = useState<string | null>(null);
	const [preview, setPreview] = useState<SolutionInstallPreview | null>(null);
	const [previewError, setPreviewError] = useState<string | null>(null);
	const [previewLoading, setPreviewLoading] = useState(false);
	const [installError, setInstallError] = useState<string | null>(null);
	const [configValues, setConfigValues] = useState<Record<string, string>>({});
	const [downgradeConfirm, setDowngradeConfirm] = useState(false);
	const [gitRepoUrl, setGitRepoUrl] = useState("");
	const [dragging, setDragging] = useState(false);

	// Monotonic guard so a stale preview response (scope changed while one was
	// in flight) can't clobber a newer one.
	const previewSeq = useRef(0);

	// Kick the preview exactly once for a PREFILLED file (page drop). Files
	// picked through the dialog preview via pickFile — the ref starts "fired"
	// when there is nothing prefilled so this effect never double-previews.
	const initialPreviewFired = useRef(initialFile === null);
	useEffect(() => {
		if (file && !initialPreviewFired.current) {
			initialPreviewFired.current = true;
			void runPreview(file, orgId);
		}
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [file]);

	async function runPreview(f: File, scope: string | null) {
		const seq = ++previewSeq.current;
		setPreviewLoading(true);
		try {
			const data = await previewInstall(f, { organizationId: scope ?? "" });
			if (seq !== previewSeq.current) return;
			setPreview(data);
			setPreviewError(null);
		} catch (err: unknown) {
			if (seq !== previewSeq.current) return;
			// Disarm the previous scope's preview: leaving it set would enable
			// Install into a scope that was never successfully previewed.
			setPreview(null);
			setPreviewError(
				err instanceof Error ? err.message : "Failed to read package",
			);
		} finally {
			if (seq === previewSeq.current) setPreviewLoading(false);
		}
	}

	function pickFile(f: File) {
		setFile(f);
		setPreview(null);
		setPreviewError(null);
		setInstallError(null);
		setDowngradeConfirm(false);
		setConfigValues({});
		void runPreview(f, orgId);
	}

	const installMutation = useMutation({
		mutationFn: ({ force }: { force: boolean }) => {
			if (!file) throw new Error("No file selected");
			const values: Record<string, string> = {};
			for (const [k, v] of Object.entries(configValues)) {
				if (v.trim() !== "") values[k] = v;
			}
			return installSolution({
				file,
				organizationId: orgId ?? "",
				configValues: values,
				force,
			});
		},
		onSuccess: async (created) => {
			// Git connection is part of the create flow: stamp the repo URL on
			// the new install before reporting success.
			let result = created;
			const url = gitRepoUrl.trim();
			if (url) {
				try {
					result = await updateSolution(created.id, {
						git_repo_url: url,
						git_connected: true,
					} as SolutionUpdate);
				} catch (err: unknown) {
					toast.error(
						err instanceof Error
							? `Installed, but failed to connect git: ${err.message}`
							: "Installed, but failed to connect git",
					);
				}
			}
			queryClient.invalidateQueries({ queryKey: ["solutions"] });
			toast.success(
				preview?.existing_install
					? `Upgraded ${created.name}`
					: `Installed ${created.name}`,
			);
			onSaved(result);
		},
		onError: (err: unknown) => {
			const message = err instanceof Error ? err.message : "Failed to install";
			if (message.includes("older than installed")) {
				// Server's downgrade guard (409) — ask before forcing.
				setInstallError(null);
				setDowngradeConfirm(true);
				return;
			}
			setInstallError(message);
		},
	});

	const declaredConfigs = preview ? asConfigSchemas(preview.config_schemas) : [];
	const existingInstall = preview?.existing_install ?? null;
	const isUpgrade = existingInstall !== null;

	return (
		<>
			<DialogHeader>
				<DialogTitle>
					{isUpgrade && existingInstall
						? `Upgrade ${existingInstall.name} v${existingInstall.version ?? "?"} → v${preview?.version ?? "?"}`
						: "Install Solution"}
				</DialogTitle>
				<DialogDescription>
					{isUpgrade
						? "This package upgrades an existing install in place. Review the changes below."
						: "Choose a package and an organization, review what it creates, and set any required configuration values."}
				</DialogDescription>
			</DialogHeader>

			<input
				ref={fileInputRef}
				type="file"
				accept=".zip,application/zip"
				className="hidden"
				data-testid="install-file-input"
				onChange={(e) => {
					const f = e.target.files?.[0];
					if (f) pickFile(f);
					e.target.value = "";
				}}
			/>

			<div className="space-y-5">
				{/* Package dropzone / selected file */}
				{file ? (
					<div className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2">
						<div className="flex min-w-0 items-center gap-2 text-sm">
							<FileArchive className="h-4 w-4 shrink-0 text-muted-foreground" />
							<span className="truncate font-medium">{file.name}</span>
						</div>
						<Button
							type="button"
							variant="ghost"
							size="icon"
							className="h-7 w-7"
							aria-label="Remove file"
							onClick={() => {
								setFile(null);
								setPreview(null);
								setPreviewError(null);
								setDowngradeConfirm(false);
								previewSeq.current++;
								setPreviewLoading(false);
							}}
						>
							<X className="h-4 w-4" />
						</Button>
					</div>
				) : (
					<button
						type="button"
						data-testid="dialog-dropzone"
						onClick={() => fileInputRef.current?.click()}
						onDragOver={(e) => {
							e.preventDefault();
							setDragging(true);
						}}
						onDragLeave={() => setDragging(false)}
						onDrop={(e) => {
							e.preventDefault();
							setDragging(false);
							const f = e.dataTransfer?.files?.[0];
							if (f) pickFile(f);
						}}
						className={
							"flex w-full flex-col items-center justify-center rounded-lg border-2 border-dashed py-8 text-center transition-colors " +
							(dragging
								? "border-primary bg-accent/40"
								: "hover:border-primary/60 hover:bg-accent/30")
						}
					>
						<Upload className="h-8 w-8 text-muted-foreground" />
						<p className="mt-2 text-sm font-medium">
							Drop a Solution .zip here
						</p>
						<p className="text-xs text-muted-foreground">
							or click to choose a file
						</p>
					</button>
				)}

				{/* Organization — standard selector, always at the top. An upgrade
				    targets the existing install's scope; re-picking would create
				    nothing, so it is hidden then. */}
				{!isUpgrade && (
					<div className="space-y-2">
						<Label>Organization</Label>
						<OrganizationSelect
							value={orgId}
							onChange={(value) => {
								const next = value ?? null;
								setOrgId(next);
								// Re-preview at the selected scope so an existing
								// install there is caught and surfaced as an upgrade.
								if (file) void runPreview(file, next);
							}}
							showGlobal
						/>
					</div>
				)}

				{previewLoading ? (
					<div className="flex items-center gap-2 py-4 text-muted-foreground">
						<Loader2 className="h-4 w-4 animate-spin" />
						Reading package…
					</div>
				) : previewError ? (
					<p className="text-sm text-destructive">{previewError}</p>
				) : preview && downgradeConfirm ? (
					<div data-testid="downgrade-confirm" className="space-y-2 py-2">
						<p className="text-sm font-medium">
							This is a DOWNGRADE: v{existingInstall?.version ?? "?"} → v
							{preview.version ?? "?"}. Replace anyway?
						</p>
						<p className="text-xs text-muted-foreground">
							The installed version is newer than this package. Replacing
							it will overwrite the install's content with the older
							version.
						</p>
					</div>
				) : preview ? (
					<>
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

						{declaredConfigs.length > 0 && (
							<div className="space-y-3">
								<p className="text-sm font-medium">Configuration</p>
								{declaredConfigs.map((cfg) => {
									const value = configValues[cfg.key] ?? "";
									const missing = cfg.required && value.trim() === "";
									return (
										<div key={cfg.key} className="space-y-1">
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
													setConfigValues((prev) => ({
														...prev,
														[cfg.key]: e.target.value,
													}))
												}
											/>
											{missing && (
												<p className="text-xs text-yellow-600 dark:text-yellow-500">
													Required — you can still install and
													set this later.
												</p>
											)}
										</div>
									);
								})}
							</div>
						)}

						{!isUpgrade && (
							<GitRepoSection
								slug={preview.slug ?? null}
								repoUrl={gitRepoUrl}
								onRepoUrlChange={setGitRepoUrl}
							/>
						)}

						{installError && (
							<p className="text-sm text-destructive">{installError}</p>
						)}
					</>
				) : null}
			</div>

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
							onClick={() => installMutation.mutate({ force: true })}
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
							onClick={onClose}
							disabled={installMutation.isPending}
						>
							Cancel
						</Button>
						<Button
							onClick={() => installMutation.mutate({ force: false })}
							disabled={
								!preview || previewLoading || installMutation.isPending
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
		</>
	);
}

function EditBody({
	solution,
	onClose,
	onSaved,
}: {
	solution: Solution;
	onClose: () => void;
	onSaved: (solution: Solution) => void;
}) {
	const [name, setName] = useState(solution.name);
	const [orgId, setOrgId] = useState<string | null>(
		solution.organization_id ?? null,
	);
	const [globalRepoAccess, setGlobalRepoAccess] = useState(
		solution.global_repo_access,
	);
	const [gitRepoUrl, setGitRepoUrl] = useState(solution.git_repo_url ?? "");

	const saveMut = useMutation({
		mutationFn: () => {
			const update: SolutionUpdate = {};
			if (name !== solution.name) update.name = name;
			if (orgId !== (solution.organization_id ?? null))
				update.organization_id = orgId;
			if (globalRepoAccess !== solution.global_repo_access)
				update.global_repo_access = globalRepoAccess;
			const nextUrl = gitRepoUrl.trim() === "" ? null : gitRepoUrl.trim();
			if (nextUrl !== (solution.git_repo_url ?? null)) {
				update.git_repo_url = nextUrl;
				// Connection state is derived from the repo URL — no toggle.
				update.git_connected = nextUrl !== null;
			}
			return updateSolution(solution.id, update);
		},
		onSuccess: (updated) => {
			toast.success("Solution updated");
			onSaved(updated);
		},
		onError: (err: unknown) => {
			toast.error(
				err instanceof Error ? err.message : "Failed to update Solution",
			);
		},
	});

	return (
		<>
			<DialogHeader>
				<DialogTitle>Edit Solution</DialogTitle>
				<DialogDescription>
					Update install-local settings. Portable content (workflows, apps,
					forms, etc.) is owned by the bundle and is read-only.
				</DialogDescription>
			</DialogHeader>

			<div className="space-y-4">
				<div className="space-y-2">
					<Label>Organization</Label>
					<OrganizationSelect
						value={orgId}
						onChange={(value) => setOrgId(value ?? null)}
						showGlobal
					/>
				</div>

				<div className="space-y-1.5">
					<Label htmlFor="edit-name">Name</Label>
					<Input
						id="edit-name"
						value={name}
						onChange={(e) => setName(e.target.value)}
					/>
				</div>

				<div className="flex items-center justify-between rounded-lg border p-3">
					<div className="space-y-0.5">
						<Label htmlFor="edit-global-repo">Global repo access</Label>
						<p className="text-xs text-muted-foreground">
							Allow this install to read the global repository.
						</p>
					</div>
					<Switch
						id="edit-global-repo"
						checked={globalRepoAccess}
						onCheckedChange={setGlobalRepoAccess}
					/>
				</div>

				<GitRepoSection
					slug={solution.slug}
					repoUrl={gitRepoUrl}
					onRepoUrlChange={setGitRepoUrl}
				/>
			</div>

			<DialogFooter>
				<Button variant="outline" onClick={onClose}>
					Cancel
				</Button>
				<Button disabled={saveMut.isPending} onClick={() => saveMut.mutate()}>
					{saveMut.isPending && (
						<Loader2 className="mr-2 h-4 w-4 animate-spin" />
					)}
					Save changes
				</Button>
			</DialogFooter>
		</>
	);
}
