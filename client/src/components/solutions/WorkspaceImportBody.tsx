import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowLeft, Loader2, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { runGitOp } from "@/components/editor/runGitOperation";
import { importWorkspaceBundle, previewWorkspaceBundle, previewWorkspaceBundleFromRepo, type WorkspaceBundlePreview } from "@/services/solutions";
import { InstallFailure, useInstallSession } from "./InstallSession";
import { WorkspaceImportReview } from "./WorkspaceImportReview";
import { ConfigValueFields, asConfigSchemas, nonBlankConfigValues } from "./SolutionConfigFields";
import type { InstallSource, RepoPrefill } from "./CreateEditSolution";

export function WorkspaceImportBody({
	source,
	initialRepo,
	initialFile,
	onBack,
	onClose,
}: {
	source: InstallSource;
	initialRepo: RepoPrefill | null;
	initialFile: File | null;
	onBack: () => void;
	onClose: () => void;
}) {
	const session = useInstallSession();
	const setWide = session.setWide;
	const setTall = session.setTall;
	useEffect(() => {
		setWide(true);
		return () => { setWide(false); setTall(false); };
	}, [setWide, setTall]);
	const queryClient = useQueryClient();
	const inputRef = useRef<HTMLInputElement>(null);
	const [file, setFile] = useState<File | null>(initialFile);
	const [repoUrl, setRepoUrl] = useState(initialRepo?.url ?? "");
	const [repoSubpath, setRepoSubpath] = useState(initialRepo?.subpath ?? "");
	const [repoRef, setRepoRef] = useState(initialRepo?.ref ?? "");
	const [orgId, setOrgId] = useState<string | null>(null);
	const [preview, setPreview] = useState<WorkspaceBundlePreview | null>(null);
	const [decisions, setDecisions] = useState<Record<string, "keep" | "replace">>({});
	const [configValues, setConfigValues] = useState<Record<string, string>>({});
	const [error, setError] = useState<string | null>(null);
	const [loading, setLoading] = useState(false);
	const [dragging, setDragging] = useState(false);
	const previewRequest = useRef(0);
	const conflicts = preview?.items.filter((item) => item.classification === "conflict") ?? [];
	const requiredConfigs = asConfigSchemas(preview?.config_schemas ?? []).filter((cfg) => {
		const item = preview?.items.find((candidate) => candidate.kind === "config" && candidate.name === cfg.key);
		return cfg.requiresInput && item && (
			item.classification === "create" ||
			(item.classification === "conflict" && decisions[item.id] === "replace")
		);
	});
	const complete = conflicts.every((item) => decisions[item.id]) &&
		requiredConfigs.every((cfg) => Boolean(configValues[cfg.key]?.trim()));

	// Changing the target scope invalidates the preview it was computed for.
	function clearPreview() {
		previewRequest.current += 1;
		setPreview(null);
		setDecisions({});
		setConfigValues({});
		setError(null);
		setLoading(false);
	}

	async function loadZip(next: File, targetOrgId = orgId) {
		const request = ++previewRequest.current;
		setFile(next); setPreview(null); setDecisions({}); setConfigValues({}); setError(null); setLoading(true);
		try {
			const result = await previewWorkspaceBundle(next, { organizationId: targetOrgId ?? "" });
			if (request === previewRequest.current) setPreview(result);
		} catch (cause) {
			if (request === previewRequest.current) setError(cause instanceof Error ? cause.message : "Failed to preview workspace import");
		} finally {
			if (request === previewRequest.current) setLoading(false);
		}
	}
	async function loadRepo(targetOrgId = orgId) {
		const request = ++previewRequest.current;
		setPreview(null); setDecisions({}); setConfigValues({}); setError(null); setLoading(true);
		try {
			const result = await previewWorkspaceBundleFromRepo({
				repo_url: repoUrl.trim(),
				git_ref: repoRef.trim() || null,
				repo_subpath: repoSubpath.trim() || null,
				organization_id: targetOrgId,
			});
			if (request === previewRequest.current) setPreview(result);
		}
		catch (cause) { if (request === previewRequest.current) setError(cause instanceof Error ? cause.message : "Failed to preview workspace import"); }
		finally { if (request === previewRequest.current) setLoading(false); }
	}
	function changeScope(nextOrgId: string | null) {
		const hadPreview = preview !== null;
		setOrgId(nextOrgId);
		clearPreview();
		if (source === "zip" && file) void loadZip(file, nextOrgId);
		if (source === "repo" && hadPreview) void loadRepo(nextOrgId);
	}
	// Kick the preview exactly once for a PREFILLED file (page drop). Files
	// picked through the dialog preview via the input onChange — the ref
	// starts "fired" when there is nothing prefilled.
	const initialPreviewFired = useRef(initialFile === null);
	useEffect(() => {
		if (source === "zip" && file && !initialPreviewFired.current) {
			initialPreviewFired.current = true;
			void loadZip(file);
		}
		// Fires at most once (guarded above); depending on loadZip would
		// re-run it every render as its closure changes.
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [source, file]);
	const start = async () => {
		if (!preview || !complete) return;
		try {
			await runGitOp(
				async () => {
					const accepted = await importWorkspaceBundle({
						preview_token: preview.preview_token,
						decisions: conflicts.map((item) => ({ item_id: item.id, action: decisions[item.id] })),
						config_values: nonBlankConfigValues(configValues),
					});
					return { job_id: String(accepted.job_id), status: accepted.status };
				},
				"Workspace import",
				() => {
					toast.success("Workspace import queued", { description: "Progress is available in Notifications." });
					onClose();
				},
				(job) => {
				if (job.status === "succeeded") {
					queryClient.invalidateQueries({ queryKey: ["github", "repo-status"] });
					queryClient.invalidateQueries({ queryKey: ["solutions"] });
				}
				},
			);
		} catch (cause) { setError(cause instanceof Error ? cause.message : "Failed to start workspace import"); }
		finally { session.finish(); }
	};

	return <>
		<DialogHeader className="shrink-0 px-6 pt-6"><DialogTitle>{preview ? "Review workspace import" : "Import into workspace"}</DialogTitle><DialogDescription>{preview ? "Choose which destination definitions to keep or replace. Creates and unchanged items need no decision." : "Choose a target scope and a Solution package to import."}</DialogDescription></DialogHeader>
		<div data-testid="workspace-import-scope" className="grid shrink-0 gap-2 px-6 pt-4">
			<Label>Target scope</Label>
			<OrganizationSelect value={orgId} onChange={(value) => changeScope(value ?? null)} showGlobal aria-label="Target scope" />
			<p className="text-xs text-muted-foreground">Files, integrations, and roles are always global.</p>
		</div>
		{preview || loading ? null : source === "repo" ? (
			<div className="flex min-h-0 flex-1 flex-col gap-3 p-6">
				<div className="grid gap-2">
					<Label htmlFor="workspace-repo-url">Repository URL</Label>
					<Input id="workspace-repo-url" data-testid="workspace-repo-url" placeholder="https://github.com/org/solution.git" value={repoUrl} onChange={(event) => setRepoUrl(event.target.value)} />
				</div>
				<div className="grid grid-cols-2 gap-3">
					<div className="grid gap-2">
						<Label htmlFor="workspace-repo-ref">Ref (optional)</Label>
						<Input id="workspace-repo-ref" data-testid="workspace-repo-ref" placeholder="main" value={repoRef} onChange={(event) => setRepoRef(event.target.value)} />
					</div>
					<div className="grid gap-2">
						<Label htmlFor="workspace-repo-subpath">Subfolder (optional)</Label>
						<Input id="workspace-repo-subpath" data-testid="workspace-repo-subpath" placeholder="packages/demo" value={repoSubpath} onChange={(event) => setRepoSubpath(event.target.value)} />
					</div>
				</div>
				<p className="text-xs text-muted-foreground">One-time snapshot — the repository is not kept connected and no Solution is created.</p>
				{error && <InstallFailure message={error} />}
				<div className="flex gap-2">
					<Button type="button" data-testid="workspace-repo-preview" disabled={!repoUrl.trim() || loading} onClick={() => void loadRepo()}>Preview snapshot</Button>
				</div>
			</div>
		) : (
			<div className="flex min-h-0 flex-1 flex-col gap-3 p-6">
				<input ref={inputRef} type="file" accept=".zip,application/zip" className="hidden" onChange={(event) => { const next = event.target.files?.[0]; if (next) void loadZip(next); event.target.value = ""; }} />
				<button
					type="button"
					data-testid="workspace-dialog-dropzone"
					onClick={() => inputRef.current?.click()}
					onDragOver={(e) => {
						e.preventDefault();
						setDragging(true);
					}}
					onDragLeave={() => setDragging(false)}
					onDrop={(e) => {
						e.preventDefault();
						setDragging(false);
						const next = e.dataTransfer?.files?.[0];
						if (next) void loadZip(next);
					}}
					className={
						"flex min-h-24 w-full flex-col items-center justify-center rounded-lg border-2 border-dashed text-center transition-colors " +
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
				{error && <InstallFailure message={error} />}
			</div>
		)}
		{loading ? <div className="flex min-h-0 flex-1 items-center justify-center gap-2 px-6 py-8"><Loader2 className="size-4 animate-spin" />Reading package…</div> : preview ? <WorkspaceImportReview preview={preview} decisions={decisions} onDecisionsChange={setDecisions} configuration={preview.config_schemas?.length ? (
			<div className="shrink-0 px-5 pt-4" data-testid="workspace-import-config-section">
				<p className="text-sm font-medium">Configuration</p>
				<p className="mb-3 text-xs text-muted-foreground">Entered values are saved during import. Kept conflicts retain their current values.</p>
				<div className="grid max-h-[22dvh] gap-3 overflow-auto pr-1 sm:grid-cols-2">
					<ConfigValueFields
						configs={asConfigSchemas(preview.config_schemas).map((cfg) => ({
							...cfg,
							required: requiredConfigs.some((required) => required.key === cfg.key),
						}))}
						values={configValues}
						onChange={(key, value) => setConfigValues((previous) => ({ ...previous, [key]: value }))}
						disabledKeys={new Set(preview.items.filter((item) => item.kind === "config" && decisions[item.id] === "keep").map((item) => item.name))}
					/>
				</div>
			</div>
		) : null} /> : null}
		{error && preview && <div className="px-6"><InstallFailure message={error} /></div>}
		<DialogFooter data-testid="workspace-import-footer" className={`min-w-0 shrink-0 flex-col items-stretch gap-3 bg-muted/20 px-6 py-4 sm:flex-col ${preview ? "border-t" : ""}`}>{preview && <p className="text-xs text-muted-foreground">Import creates uncommitted Git changes</p>}<div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-center sm:justify-between"><Button type="button" variant="ghost" className="self-start" onClick={onBack}><ArrowLeft className="mr-1 size-4" />Back</Button><div className="flex min-w-0 flex-wrap justify-end gap-2"><Button type="button" variant="outline" onClick={onClose}>Cancel</Button><Button type="button" disabled={!preview || !complete || session.pending} onClick={() => session.run(start)}>Start import job</Button></div></div></DialogFooter>
	</>;
}
