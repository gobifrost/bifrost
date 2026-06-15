/**
 * CreateEditSolution — the single, state-driven dialog for installing a
 * Solution (create) and editing an existing install (edit).
 *
 * Create mode offers TWO install sources, both routed through the same
 * preview → confirm → install machinery:
 *   - "From a repository": a repo URL + optional subfolder + ref; previews via
 *     `previewSolutionFromRepo` and installs via `installSolutionFromRepo`.
 *   - "From a zip": a dropzone (or a prefilled file from a page drop) that
 *     previews via `previewInstall` and installs via `installSolution`.
 * When neither source is pre-selected, a small source picker is shown first.
 * There is NO empty-shell "create with no content" path — content always
 * lands via a zip or a repo.
 *
 * Both sources share the read-only confirmation card (`PreviewConfirmation`):
 * the entity summary / upgrade diff / declared config values.
 *
 * Edit mode: name + Organization + global repo access + the git section.
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
	ChevronRight,
	Database,
	FileArchive,
	FileCode,
	GitBranch,
	Loader2,
	Plug,
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
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import {
	useGitHubConfig,
	useCreateGitHubRepository,
} from "@/hooks/useGitHub";
import {
	installSolution,
	installSolutionFromRepo,
	previewInstall,
	previewSolutionFromRepo,
	updateSolution,
	type Solution,
	type SolutionInstallPreview,
	type SolutionRepoPreviewRequest,
	type SolutionUpdate,
	type SolutionUpgradeDiff,
} from "@/services/solutions";
import type { components } from "@/lib/v1";

/** Pre-filled From-repository fields (e.g. from a `?repo=` deep link). */
export interface RepoPrefill {
	url: string;
	subpath?: string | null;
	ref?: string | null;
}

export type CreateEditSolutionMode =
	| {
			kind: "create";
			/**
			 * Which install source to show. When omitted (and no `file`/`repo`
			 * prefill is present), the source picker is shown first.
			 */
			source?: "repo" | "zip";
			/** Prefilled zip (a page drop) — implies the zip source. */
			file?: File;
			/** Prefilled repo fields (a deep link) — implies the repo source. */
			repo?: RepoPrefill;
			organizationId?: string | null;
			intent?: "install" | "update";
	  }
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

/**
 * Parse the colliding config-value keys out of the server's ContentCollision
 * 409 detail. The backend formats it as:
 *   "Import would overwrite existing config values: A, B[; table data: T]. Re-run with replace to overwrite."
 * Returns the parsed keys when the message is a config-value collision, or
 * `null` when it is not (so the caller can fall through to other 409 handling).
 */
function parseCollisionKeys(message: string): string[] | null {
	if (!message.includes("Import would overwrite existing")) return null;
	const match = /config values:\s*([^.;]+)/.exec(message);
	if (!match) return null;
	return match[1]
		.split(",")
		.map((k) => k.trim())
		.filter((k) => k !== "");
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
		{
			icon: Plug,
			label: "integrations",
			count: preview.connection_schemas?.length ?? 0,
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
export function UpgradeDiffView({ diff }: { diff: SolutionUpgradeDiff }) {
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
 * Repository connection for an install (Edit dialog). GitHub must be configured
 * in Settings (a saved token) to back an install with a repo.
 *
 * Connecting: enter a repo URL (+ optional subfolder / ref) and save. Saving a
 * URL on a disconnected install marks it git-connected.
 *
 * Disconnecting: the "Disconnect" action clears the URL and flips the install
 * back to manual (CLI-writable / deploy-allowed). Reconnecting is just editing
 * the URL/subpath/ref of an already-connected install — the normal save.
 */
function GitRepoSection({
	slug,
	connected,
	repoUrl,
	subpath,
	gitRef,
	onRepoUrlChange,
	onSubpathChange,
	onRefChange,
	onDisconnect,
}: {
	slug: string | null;
	connected: boolean;
	repoUrl: string;
	subpath: string;
	gitRef: string;
	onRepoUrlChange: (url: string) => void;
	onSubpathChange: (subpath: string) => void;
	onRefChange: (gitRef: string) => void;
	onDisconnect: () => void;
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
					Repository connection
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
					Repository connection
				</div>
				<Badge variant={connected ? "default" : "secondary"}>
					{connected ? "Connected" : "Not connected"}
				</Badge>
			</div>
			{connected ? (
				<p className="text-xs text-muted-foreground">
					This install pulls its content from a repository. Disconnect to make
					it manual (CLI-writable) again.
				</p>
			) : (
				<p className="text-xs text-muted-foreground">
					Enter a repository URL and save to connect this install to git.
				</p>
			)}
			<Input
				data-testid="git-repo-url"
				value={repoUrl}
				placeholder="https://github.com/org/repo"
				onChange={(e) => onRepoUrlChange(e.target.value)}
			/>
			<div className="grid grid-cols-2 gap-2">
				<div className="space-y-1">
					<Label htmlFor="git-subpath" className="text-xs">
						Subfolder
					</Label>
					<Input
						id="git-subpath"
						data-testid="git-repo-subpath"
						value={subpath}
						placeholder="solutions/my-solution"
						onChange={(e) => onSubpathChange(e.target.value)}
					/>
				</div>
				<div className="space-y-1">
					<Label htmlFor="git-ref" className="text-xs">
						Ref
					</Label>
					<Input
						id="git-ref"
						data-testid="git-repo-ref"
						value={gitRef}
						placeholder="main"
						onChange={(e) => onRefChange(e.target.value)}
					/>
				</div>
			</div>
			<div className="flex items-center gap-2">
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
				{connected && (
					<Button
						type="button"
						variant="ghost"
						size="sm"
						data-testid="git-disconnect"
						className="text-destructive hover:text-destructive"
						onClick={onDisconnect}
					>
						Disconnect
					</Button>
				)}
			</div>
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
					<CreateDispatch mode={mode} onClose={onClose} onSaved={onSaved} />
				) : (
					<EditBody solution={mode.solution} onClose={onClose} onSaved={onSaved} />
				)}
			</DialogContent>
		</Dialog>
	);
}

/**
 * Decides which create surface to show: the source picker, the From-repository
 * form, or the From-zip dropzone. A prefilled `file`/`repo` (or an explicit
 * `source`) skips the picker.
 */
function CreateDispatch({
	mode,
	onClose,
	onSaved,
}: {
	mode: Extract<CreateEditSolutionMode, { kind: "create" }>;
	onClose: () => void;
	onSaved: (solution: Solution) => void;
}) {
	const initialSource: "repo" | "zip" | null =
		mode.source ?? (mode.repo ? "repo" : mode.file ? "zip" : null);
	const [source, setSource] = useState<"repo" | "zip" | null>(initialSource);

	const orgId = mode.organizationId ?? null;
	const lockOrganization = mode.organizationId !== undefined;
	const intent = mode.intent ?? "install";

	if (source === null) {
		return <SourcePicker intent={intent} onPick={setSource} />;
	}
	if (source === "repo") {
		return (
			<RepoBody
				initialRepo={mode.repo ?? null}
				intent={intent}
				onClose={onClose}
				onSaved={onSaved}
			/>
		);
	}
	return (
		<CreateBody
			initialFile={mode.file ?? null}
			initialOrgId={orgId}
			lockOrganization={lockOrganization}
			intent={intent}
			onClose={onClose}
			onSaved={onSaved}
		/>
	);
}

/** The two install sources — a repository (marketplace) or a local zip. */
function SourcePicker({
	intent,
	onPick,
}: {
	intent: "install" | "update";
	onPick: (s: "repo" | "zip") => void;
}) {
	const options: {
		source: "repo" | "zip";
		icon: typeof GitBranch;
		title: string;
		description: string;
		testid: string;
	}[] = [
		{
			source: "repo",
			icon: GitBranch,
			title: "From a repository",
			description: "Install from a GitHub repository — the marketplace path.",
			testid: "source-repo",
		},
		{
			source: "zip",
			icon: FileArchive,
			title: "From a zip",
			description: "Install from an exported Solution .zip on your machine.",
			testid: "source-zip",
		},
	];
	return (
		<>
			<DialogHeader>
				<DialogTitle>
					{intent === "update" ? "Update Solution" : "Install Solution"}
				</DialogTitle>
				<DialogDescription>
					Choose where this Solution comes from.
				</DialogDescription>
			</DialogHeader>
			<div className="space-y-3" data-testid="source-picker">
				{options.map(({ source, icon: Icon, title, description, testid }) => (
					<button
						key={source}
						type="button"
						data-testid={testid}
						onClick={() => onPick(source)}
						className="flex w-full items-center gap-3 rounded-lg border p-4 text-left transition-colors hover:border-primary/60 hover:bg-accent/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
					>
						<Icon className="h-6 w-6 shrink-0 text-muted-foreground" />
						<div className="min-w-0 flex-1">
							<p className="text-sm font-semibold">{title}</p>
							<p className="text-xs text-muted-foreground">{description}</p>
						</div>
						<ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
					</button>
				))}
			</div>
		</>
	);
}

/**
 * The read-only confirmation card shared by the zip and repo install paths:
 * entity summary (fresh) or upgrade diff, then the declared configuration.
 *
 * `configMode` controls the configuration section:
 *   - "edit" (zip path): editable config-value inputs + the full-backup
 *     password prompt, since the zip install carries values and a password.
 *   - "declare" (repo path): a read-only list of declared config keys. The
 *     from-repo install doesn't take values up front — they're set afterward
 *     from the install's Setup/Details — so showing inputs here would be a lie.
 */
function PreviewConfirmation({
	preview,
	configMode,
	configValues,
	onConfigChange,
	backupPassword,
	onBackupPasswordChange,
}: {
	preview: SolutionInstallPreview;
	configMode: "edit" | "declare";
	configValues?: Record<string, string>;
	onConfigChange?: (key: string, value: string) => void;
	backupPassword?: string;
	onBackupPasswordChange?: (value: string) => void;
}) {
	const declaredConfigs = asConfigSchemas(preview.config_schemas);
	const isUpgrade = (preview.existing_install ?? null) !== null;
	return (
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
							<span className="text-muted-foreground"> ({preview.slug})</span>
						) : null}
						.
					</p>
					<div className="mt-3">
						<EntitySummary preview={preview} />
					</div>
				</div>
			)}

			{configMode === "edit" && preview.requires_password && (
				<div className="space-y-2 rounded-lg border p-3">
					<p className="text-sm font-medium">Backup password required</p>
					<p className="text-xs text-muted-foreground">
						This is a full backup. Enter the password used when it was exported.
					</p>
					<div className="space-y-1">
						<Label htmlFor="backup-password">Password</Label>
						<Input
							id="backup-password"
							data-testid="backup-password-input"
							type="password"
							value={backupPassword ?? ""}
							onChange={(e) => onBackupPasswordChange?.(e.target.value)}
							placeholder="Export password"
						/>
					</div>
				</div>
			)}

			{declaredConfigs.length > 0 && (
				<div className="space-y-3" data-testid="config-section">
					<p className="text-sm font-medium">Configuration</p>
					{configMode === "declare" ? (
						<div className="space-y-2">
							{declaredConfigs.map((cfg) => (
								<div key={cfg.key} className="text-sm">
									<span className="font-mono font-medium">{cfg.key}</span>
									{cfg.required && (
										<span className="ml-1 text-destructive" aria-hidden>
											*
										</span>
									)}
									{cfg.description && (
										<p className="text-xs text-muted-foreground">
											{cfg.description}
										</p>
									)}
								</div>
							))}
							<p className="text-xs text-muted-foreground">
								Set these values after installing, from the install's
								configuration.
							</p>
						</div>
					) : (
						declaredConfigs.map((cfg) => {
							const value = configValues?.[cfg.key] ?? "";
							const missing = cfg.required && value.trim() === "";
							return (
								<div key={cfg.key} className="space-y-1">
									<Label
										htmlFor={`cfg-${cfg.key}`}
										className="flex items-center gap-1"
									>
										{cfg.key}
										{cfg.required && (
											<span className="text-destructive" aria-hidden>
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
										type={isSecretType(cfg.type) ? "password" : "text"}
										value={value}
										onChange={(e) => onConfigChange?.(cfg.key, e.target.value)}
									/>
									{missing && (
										<p className="text-xs text-yellow-600 dark:text-yellow-500">
											Required — you can still install and set this later.
										</p>
									)}
								</div>
							);
						})
					)}
				</div>
			)}
		</>
	);
}

/** Build the install-time config-value map, dropping blank entries. */
function nonBlankConfigValues(
	configValues: Record<string, string>,
): Record<string, string> {
	const values: Record<string, string> = {};
	for (const [k, v] of Object.entries(configValues)) {
		if (v.trim() !== "") values[k] = v;
	}
	return values;
}

function CreateBody({
	initialFile,
	initialOrgId,
	lockOrganization,
	intent,
	onClose,
	onSaved,
}: {
	initialFile: File | null;
	initialOrgId: string | null;
	lockOrganization: boolean;
	intent: "install" | "update";
	onClose: () => void;
	onSaved: (solution: Solution) => void;
}) {
	const queryClient = useQueryClient();
	const fileInputRef = useRef<HTMLInputElement>(null);

	const [file, setFile] = useState<File | null>(initialFile);
	const [orgId, setOrgId] = useState<string | null>(initialOrgId);
	const [preview, setPreview] = useState<SolutionInstallPreview | null>(null);
	const [previewError, setPreviewError] = useState<string | null>(null);
	const [previewLoading, setPreviewLoading] = useState(false);
	const [installError, setInstallError] = useState<string | null>(null);
	const [configValues, setConfigValues] = useState<Record<string, string>>({});
	const [backupPassword, setBackupPassword] = useState("");
	const [downgradeConfirm, setDowngradeConfirm] = useState(false);
	// Colliding config-value keys parsed from a ContentCollision 409. When
	// non-null, the replace-secrets confirmation prompt is shown; confirming
	// re-runs the install with replaceSecrets=true.
	const [collisionKeys, setCollisionKeys] = useState<string[] | null>(null);
	const [gitRepoUrl, setGitRepoUrl] = useState("");
	const [gitSubpath, setGitSubpath] = useState("");
	const [gitRef, setGitRef] = useState("");
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
		setBackupPassword("");
		void runPreview(f, orgId);
	}

	const installMutation = useMutation({
		mutationFn: ({
			force,
			replaceSecrets,
		}: {
			force: boolean;
			replaceSecrets?: boolean;
		}) => {
			if (!file) throw new Error("No file selected");
			return installSolution({
				file,
				organizationId: orgId ?? "",
				configValues: nonBlankConfigValues(configValues),
				force,
				replaceSecrets,
				password: backupPassword.trim() || undefined,
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
						repo_subpath: gitSubpath.trim() || null,
						git_ref: gitRef.trim() || null,
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
			const status = (err as { status?: number }).status;
			const message = err instanceof Error ? err.message : "Failed to install";
			if (message.includes("older than installed")) {
				// Server's downgrade guard (409) — ask before forcing.
				setInstallError(null);
				setDowngradeConfirm(true);
				return;
			}
			if (status === 409) {
				const keys = parseCollisionKeys(message);
				if (keys) {
					// ContentCollision (409) — confirm before overwriting existing
					// secret config values, then re-run with replaceSecrets=true.
					setInstallError(null);
					setCollisionKeys(keys);
					return;
				}
			}
			if (status === 422) {
				// Wrong/missing password for a full-backup zip carrying secrets.
				// Clear the entered password so the user can re-type it; the
				// password field stays visible (preview.requires_password is still
				// true) so they can correct it and retry.
				setBackupPassword("");
				setInstallError("Incorrect password — please enter the backup password and try again.");
				return;
			}
			setInstallError(message);
		},
	});

	const existingInstall = preview?.existing_install ?? null;
	const isUpgrade = existingInstall !== null;

	return (
		<>
			<DialogHeader>
				<DialogTitle>
					{isUpgrade && existingInstall
						? `Upgrade ${existingInstall.name} v${existingInstall.version ?? "?"} → v${preview?.version ?? "?"}`
						: intent === "update"
							? "Update Solution"
							: "Install Solution"}
				</DialogTitle>
				<DialogDescription>
					{isUpgrade
						? "This package upgrades an existing install in place. Review the changes below."
						: intent === "update"
							? "Choose a package to update this install in place."
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
								setBackupPassword("");
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
				{!isUpgrade && !lockOrganization && (
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
						<PreviewConfirmation
							preview={preview}
							configMode="edit"
							configValues={configValues}
							onConfigChange={(key, value) =>
								setConfigValues((prev) => ({ ...prev, [key]: value }))
							}
							backupPassword={backupPassword}
							onBackupPasswordChange={setBackupPassword}
						/>

						{!isUpgrade && (
							<GitRepoSection
								slug={preview.slug ?? null}
								connected={gitRepoUrl.trim() !== ""}
								repoUrl={gitRepoUrl}
								subpath={gitSubpath}
								gitRef={gitRef}
								onRepoUrlChange={setGitRepoUrl}
								onSubpathChange={setGitSubpath}
								onRefChange={setGitRef}
								onDisconnect={() => {
									setGitRepoUrl("");
									setGitSubpath("");
									setGitRef("");
								}}
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

			{/* Replace-secrets confirmation (ContentCollision 409). Confirming
			    re-runs the install with replaceSecrets=true. */}
			<AlertDialog
				open={collisionKeys !== null}
				onOpenChange={(o) => {
					if (!o) setCollisionKeys(null);
				}}
			>
				<AlertDialogContent data-testid="replace-secrets-prompt">
					<AlertDialogHeader>
						<AlertDialogTitle>
							Replace existing secret values?
						</AlertDialogTitle>
						<AlertDialogDescription>
							This solution already has values for:{" "}
							<span className="font-mono font-medium">
								{collisionKeys?.join(", ")}
							</span>
							. Replacing them overwrites the existing secret config values
							with the ones from this package.
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel onClick={() => setCollisionKeys(null)}>
							Keep existing
						</AlertDialogCancel>
						<AlertDialogAction
							data-testid="confirm-replace-secrets"
							onClick={() => {
								setCollisionKeys(null);
								installMutation.mutate({
									force: false,
									replaceSecrets: true,
								});
							}}
						>
							Replace secrets
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</>
	);
}

/**
 * From-repository install: a repo URL + optional subfolder + ref, a Resolve
 * action that previews via `previewSolutionFromRepo`, the shared read-only
 * confirmation, and an Install action that installs via
 * `installSolutionFromRepo`. Same preview → confirm → install shape as the zip
 * path; the install is git-connected from birth on the server.
 */
function RepoBody({
	initialRepo,
	intent,
	onClose,
	onSaved,
}: {
	initialRepo: RepoPrefill | null;
	intent: "install" | "update";
	onClose: () => void;
	onSaved: (solution: Solution) => void;
}) {
	const queryClient = useQueryClient();

	const [repoUrl, setRepoUrl] = useState(initialRepo?.url ?? "");
	const [subpath, setSubpath] = useState(initialRepo?.subpath ?? "");
	const [gitRef, setGitRef] = useState(initialRepo?.ref ?? "");
	const [preview, setPreview] = useState<SolutionInstallPreview | null>(null);
	const [previewError, setPreviewError] = useState<string | null>(null);
	const [installError, setInstallError] = useState<string | null>(null);

	// Editing any repo field invalidates a resolved preview — re-resolve before
	// installing into a stale plan.
	function clearPreview() {
		setPreview(null);
		setPreviewError(null);
		setInstallError(null);
	}

	function buildBody(): SolutionRepoPreviewRequest {
		const body: SolutionRepoPreviewRequest = { repo_url: repoUrl.trim() };
		const sub = subpath.trim();
		const ref = gitRef.trim();
		if (sub) body.repo_subpath = sub;
		if (ref) body.git_ref = ref;
		return body;
	}

	const previewMutation = useMutation({
		mutationFn: () => previewSolutionFromRepo(buildBody()),
		onSuccess: (data) => {
			setPreview(data);
			setPreviewError(null);
		},
		onError: (err: unknown) => {
			setPreview(null);
			setPreviewError(
				err instanceof Error ? err.message : "Failed to resolve repository",
			);
		},
	});

	const installMutation = useMutation({
		mutationFn: () => installSolutionFromRepo(buildBody()),
		onSuccess: (created) => {
			queryClient.invalidateQueries({ queryKey: ["solutions"] });
			toast.success(
				preview?.existing_install
					? `Upgraded ${created.name}`
					: `Installed ${created.name}`,
			);
			onSaved(created);
		},
		onError: (err: unknown) => {
			setInstallError(
				err instanceof Error ? err.message : "Failed to install from repository",
			);
		},
	});

	const isUpgrade = (preview?.existing_install ?? null) !== null;
	const canResolve = repoUrl.trim() !== "";

	return (
		<>
			<DialogHeader>
				<DialogTitle>
					{isUpgrade && preview?.existing_install
						? `Upgrade ${preview.existing_install.name} v${preview.existing_install.version ?? "?"} → v${preview?.version ?? "?"}`
						: intent === "update"
							? "Update from repository"
							: "Install from a repository"}
				</DialogTitle>
				<DialogDescription>
					Point at a GitHub repository, resolve what it installs, and install.
				</DialogDescription>
			</DialogHeader>

			<div className="space-y-5">
				<div className="space-y-3">
					<div className="space-y-1.5">
						<Label htmlFor="repo-url">
							Repository URL
							<span className="ml-1 text-destructive" aria-hidden>
								*
							</span>
						</Label>
						<Input
							id="repo-url"
							data-testid="repo-url"
							value={repoUrl}
							placeholder="https://github.com/org/repo"
							onChange={(e) => {
								setRepoUrl(e.target.value);
								clearPreview();
							}}
						/>
					</div>
					<div className="grid grid-cols-2 gap-3">
						<div className="space-y-1.5">
							<Label htmlFor="repo-subpath">Subfolder</Label>
							<Input
								id="repo-subpath"
								data-testid="repo-subpath"
								value={subpath ?? ""}
								placeholder="microsoft-csp"
								onChange={(e) => {
									setSubpath(e.target.value);
									clearPreview();
								}}
							/>
						</div>
						<div className="space-y-1.5">
							<Label htmlFor="repo-ref">Ref / branch / tag</Label>
							<Input
								id="repo-ref"
								data-testid="repo-ref"
								value={gitRef ?? ""}
								placeholder="main"
								onChange={(e) => {
									setGitRef(e.target.value);
									clearPreview();
								}}
							/>
						</div>
					</div>
					{!preview && (
						<Button
							type="button"
							variant="outline"
							data-testid="resolve-repo"
							disabled={!canResolve || previewMutation.isPending}
							onClick={() => previewMutation.mutate()}
						>
							{previewMutation.isPending && (
								<Loader2 className="mr-2 h-4 w-4 animate-spin" />
							)}
							Resolve
						</Button>
					)}
				</div>

				{previewMutation.isPending ? (
					<div className="flex items-center gap-2 py-4 text-muted-foreground">
						<Loader2 className="h-4 w-4 animate-spin" />
						Resolving repository…
					</div>
				) : previewError ? (
					<p className="text-sm text-destructive" data-testid="repo-preview-error">
						{previewError}
					</p>
				) : preview ? (
					<>
						<PreviewConfirmation preview={preview} configMode="declare" />
						{installError && (
							<p className="text-sm text-destructive">{installError}</p>
						)}
					</>
				) : null}
			</div>

			<DialogFooter>
				<Button
					variant="outline"
					onClick={onClose}
					disabled={installMutation.isPending}
				>
					Cancel
				</Button>
				<Button
					data-testid="confirm-install-repo"
					onClick={() => installMutation.mutate()}
					disabled={!preview || installMutation.isPending}
				>
					{installMutation.isPending && (
						<Loader2 className="mr-2 h-4 w-4 animate-spin" />
					)}
					{isUpgrade ? "Upgrade" : "Install"}
				</Button>
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
	const [gitSubpath, setGitSubpath] = useState(solution.repo_subpath ?? "");
	const [gitRef, setGitRef] = useState(solution.git_ref ?? "");
	// Explicit connection intent. Seeded from the install's current state; the
	// Disconnect action flips it off (and clears the URL/subpath/ref), and
	// typing a URL back in flips it on. A connected install with an edited URL
	// is a "reconnect" — the same save, no separate control.
	const [connected, setConnected] = useState(solution.git_connected);

	const saveMut = useMutation({
		mutationFn: () => {
			const update: SolutionUpdate = {};
			if (name !== solution.name) update.name = name;
			if (orgId !== (solution.organization_id ?? null))
				update.organization_id = orgId;
			if (globalRepoAccess !== solution.global_repo_access)
				update.global_repo_access = globalRepoAccess;

			const trimmedUrl = gitRepoUrl.trim();
			// Connect when there's a URL and the user hasn't disconnected;
			// disconnect otherwise. A disconnected install clears its repo coords.
			const nextConnected = connected && trimmedUrl !== "";
			const nextUrl = nextConnected ? trimmedUrl : null;
			const nextSubpath = nextConnected
				? gitSubpath.trim() === ""
					? null
					: gitSubpath.trim()
				: null;
			const nextRef = nextConnected
				? gitRef.trim() === ""
					? null
					: gitRef.trim()
				: null;

			if (nextConnected !== solution.git_connected)
				update.git_connected = nextConnected;
			if (nextUrl !== (solution.git_repo_url ?? null))
				update.git_repo_url = nextUrl;
			if (nextSubpath !== (solution.repo_subpath ?? null))
				update.repo_subpath = nextSubpath;
			if (nextRef !== (solution.git_ref ?? null)) update.git_ref = nextRef;

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
					connected={connected && gitRepoUrl.trim() !== ""}
					repoUrl={gitRepoUrl}
					subpath={gitSubpath}
					gitRef={gitRef}
					onRepoUrlChange={(url) => {
						setGitRepoUrl(url);
						// Typing a URL on a disconnected install re-arms the connection.
						if (url.trim() !== "") setConnected(true);
					}}
					onSubpathChange={setGitSubpath}
					onRefChange={setGitRef}
					onDisconnect={() => {
						setConnected(false);
						setGitRepoUrl("");
						setGitSubpath("");
						setGitRef("");
					}}
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
