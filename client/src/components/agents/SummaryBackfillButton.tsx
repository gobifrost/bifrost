/**
 * Admin-only affordance to (re-)summarize agent runs in bulk.
 *
 * The button is labeled "Resummarize runs". Clicking opens a dialog that
 * lets the admin pick a scope:
 *   - "Pending or failed" — re-runs anything that hasn't yet been
 *     summarized successfully. (default)
 *   - "Older prompt versions" — sweeps completed runs whose
 *     ``summary_prompt_version`` is older than the current version. Use
 *     this after iterating on the summarizer prompt to roll all old
 *     summaries forward.
 *   - "All completed runs" — every completed run regardless of summary
 *     state. Mostly an escape hatch.
 *
 * The dialog shows a per-scope dry-run estimate (eligible count + cost)
 * before the user confirms. The button hides only when ALL scopes report
 * zero eligible runs — so it stays visible after a prompt bump even when
 * pending/failed is empty.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
	AlertTriangle,
	CheckCircle,
	Loader2,
	RefreshCw,
	X,
	XCircle,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
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
import { Progress } from "@/components/ui/progress";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Label } from "@/components/ui/label";
import {
	useBackfillEligible,
	useBackfillSummaries,
	useCancelBackfillJob,
	useSummaryBackfillJob,
	useSummaryBackfillJobs,
} from "@/services/agentRuns";
import { webSocketService } from "@/services/websocket";

/** Mirrors api/src/services/execution/run_summarizer.SUMMARIZE_PROMPT_VERSION.
 * When you bump that constant on the backend, bump this too — the client
 * uses it to populate the "Older prompt versions" scope's
 * ``prompt_version_below`` filter. (No type sync between the two surfaces;
 * a wrong value here just means the scope sweeps fewer/more runs than
 * intended.) */
const CURRENT_PROMPT_VERSION = "v4";

type ResummarizeScope = "pending" | "older-versions" | "all";

interface ScopeRequestShape {
	statuses: ("pending" | "failed" | "completed")[];
	prompt_version_below: string | null;
}

function scopeToRequest(scope: ResummarizeScope): ScopeRequestShape {
	switch (scope) {
		case "pending":
			return {
				statuses: ["pending", "failed"],
				prompt_version_below: null,
			};
		case "older-versions":
			return {
				statuses: ["pending", "failed", "completed"],
				prompt_version_below: CURRENT_PROMPT_VERSION,
			};
		case "all":
			return {
				statuses: ["pending", "failed", "completed"],
				prompt_version_below: null,
			};
	}
}

const DISMISSED_KEY = "bifrost:dismissed-backfills";

function readDismissed(): Set<string> {
	try {
		const raw = sessionStorage.getItem(DISMISSED_KEY);
		if (!raw) return new Set();
		const parsed = JSON.parse(raw);
		return new Set(Array.isArray(parsed) ? parsed : []);
	} catch {
		return new Set();
	}
}

function writeDismissed(ids: Set<string>) {
	try {
		sessionStorage.setItem(DISMISSED_KEY, JSON.stringify(Array.from(ids)));
	} catch {
		// sessionStorage can throw in private mode; a dismissed card just
		// reappears on next mount — acceptable.
	}
}

export interface SummaryBackfillButtonProps {
	/** Limit backfill to a single agent. Omit for platform-wide. */
	agentId?: string;
	/** Layout hint. */
	size?: "sm" | "default";
}

type Phase = "idle" | "confirm" | "progress";

export function SummaryBackfillButton({
	agentId,
	size = "sm",
}: SummaryBackfillButtonProps) {
	const [phase, setPhase] = useState<Phase>("idle");
	const [scope, setScope] = useState<ResummarizeScope>("pending");
	const [preview, setPreview] = useState<{
		eligible: number;
		estimated_cost_usd: string;
		cost_basis: "history" | "fallback";
	} | null>(null);
	const [estimateError, setEstimateError] = useState<string | null>(null);
	const estimateRequestIdRef = useRef(0);
	const [submitError, setSubmitError] = useState<string | null>(null);
	const [jobId, setJobId] = useState<string | null>(null);
	const [dismissed, setDismissed] = useState<Set<string>>(() =>
		readDismissed(),
	);

	// On mount, re-attach to an already-running job if one exists for this scope.
	const { data: activeJobs } = useSummaryBackfillJobs(true);
	const existingJob = useMemo(
		() =>
			phase === "idle" && activeJobs
				? activeJobs.items.find(
						(j) =>
							(j.agent_id ?? null) === (agentId ?? null) &&
							!dismissed.has(j.id),
					)
				: undefined,
		[phase, activeJobs, agentId, dismissed],
	);
	const effectiveJobId = jobId ?? existingJob?.id ?? null;
	const effectivePhase: Phase =
		phase === "idle" && existingJob ? "progress" : phase;

	function dismissJob(id: string) {
		const next = new Set(dismissed);
		next.add(id);
		setDismissed(next);
		writeDismissed(next);
		setPhase("idle");
		setJobId(null);
	}

	const backfill = useBackfillSummaries();

	// Three eligibility queries — one per scope — drive both the button's
	// visibility and the per-scope count rendered in the dialog.
	const { data: eligiblePending, isLoading: eligibleLoadingPending } =
		useBackfillEligible(agentId);
	const { data: eligibleOldVersions, isLoading: eligibleLoadingVersions } =
		useBackfillEligible(agentId, CURRENT_PROMPT_VERSION);
	const { data: eligibleAll, isLoading: eligibleLoadingAll } =
		useBackfillEligible(agentId, undefined, true);

	function openDialog() {
		// Default scope: "pending" if there's anything to do there, otherwise
		// "older-versions" so the dialog opens with the relevant scope already
		// pre-selected. Falls back to "pending" if both are empty (button
		// would be hidden anyway, so this is just defensive).
		const initialScope: ResummarizeScope =
			eligiblePending && eligiblePending.eligible > 0
				? "pending"
				: eligibleOldVersions && eligibleOldVersions.eligible > 0
					? "older-versions"
					: "pending";
		setScope(initialScope);
		setPreview(null);
		setEstimateError(null);
		setSubmitError(null);
		setPhase("confirm");
		// Fire the dry-run for the initial scope.
		runDryRun(initialScope);
	}

	function runDryRun(s: ResummarizeScope) {
		const requestId = ++estimateRequestIdRef.current;
		const shape = scopeToRequest(s);
		setEstimateError(null);
		setSubmitError(null);
		setPreview(null);
		backfill.mutate(
			{
				body: {
					agent_id: agentId,
					statuses: shape.statuses,
					prompt_version_below: shape.prompt_version_below,
					limit: 5000,
					dry_run: true,
				},
			},
			{
				onSuccess: (data) => {
					if (requestId !== estimateRequestIdRef.current) return;
					setPreview({
						eligible: data.eligible,
						estimated_cost_usd: String(data.estimated_cost_usd),
						cost_basis: data.cost_basis,
					});
				},
				onError: () => {
					if (requestId !== estimateRequestIdRef.current) return;
					setEstimateError("Failed to compute estimate");
					toast.error("Failed to compute estimate");
				},
			},
		);
	}

	function onScopeChange(next: string) {
		const s = next as ResummarizeScope;
		setScope(s);
		runDryRun(s);
	}

	function confirmAndSubmit() {
		setSubmitError(null);
		const shape = scopeToRequest(scope);
		backfill.mutate(
			{
				body: {
					agent_id: agentId,
					statuses: shape.statuses,
					prompt_version_below: shape.prompt_version_below,
					limit: 5000,
					dry_run: false,
				},
			},
			{
				onSuccess: (data) => {
					if (!data.job_id) {
						toast.info("Nothing to resummarize");
						setPhase("idle");
						setSubmitError(null);
						return;
					}
					setJobId(data.job_id);
					setPhase("progress");
					toast.success(`Queued ${data.queued} summaries`);
				},
				onError: () => {
					setSubmitError("Failed to start resummarization");
					toast.error("Failed to start resummarization");
				},
			},
		);
	}

	if (effectivePhase === "progress" && effectiveJobId) {
		return (
			<SummaryBackfillProgress
				jobId={effectiveJobId}
				agentId={agentId}
				onDismiss={() => dismissJob(effectiveJobId)}
			/>
		);
	}

	// Hide the button only when ALL scopes report zero eligible runs — that
	// way a freshly-bumped prompt version still surfaces the affordance even
	// though pending/failed is empty.
	if (
		eligibleLoadingPending ||
		eligibleLoadingVersions ||
		eligibleLoadingAll
	) {
		return null;
	}
	const anyEligible =
		(eligiblePending?.eligible ?? 0) > 0 ||
		(eligibleOldVersions?.eligible ?? 0) > 0 ||
		(eligibleAll?.eligible ?? 0) > 0;
	if (!anyEligible) return null;

	const showAction = preview != null && preview.eligible > 0;

	return (
		<>
			<Button
				type="button"
				variant="outline"
				size={size}
				disabled={backfill.isPending}
				onClick={openDialog}
				className="min-h-11 gap-2"
				data-testid="summary-backfill-button"
			>
				{backfill.isPending && effectivePhase === "idle" ? (
					<Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />
				) : (
					<RefreshCw className="h-4 w-4" />
				)}
				Resummarize runs
			</Button>

			<AlertDialog
				open={effectivePhase === "confirm"}
				onOpenChange={(open) => {
					if (!open) setPhase("idle");
				}}
			>
				<AlertDialogContent className="max-h-[calc(100dvh-1rem)] overflow-y-auto overscroll-contain sm:max-h-[calc(100dvh-4rem)]">
					<AlertDialogHeader>
						<AlertDialogTitle>Resummarize runs</AlertDialogTitle>
						<AlertDialogDescription>
							Pick which runs to re-run the summarizer on. Cost
							estimate updates as you change the scope.
						</AlertDialogDescription>
					</AlertDialogHeader>

					<div className="grid gap-3 py-2">
						<RadioGroup
							value={scope}
							onValueChange={onScopeChange}
							className="grid gap-2"
						>
							<ScopeOption
								id="resum-scope-pending"
								value="pending"
								eligible={eligiblePending?.eligible ?? 0}
								scope={scope}
								title="Pending or failed"
								description="Runs that don't yet have a completed summary."
							/>
							<ScopeOption
								id="resum-scope-old"
								value="older-versions"
								eligible={eligibleOldVersions?.eligible ?? 0}
								scope={scope}
								title="Older prompt versions"
								description={`Completed runs summarized under a prompt older than ${CURRENT_PROMPT_VERSION}, plus any unversioned legacy summaries.`}
							/>
							<ScopeOption
								id="resum-scope-all"
								value="all"
								eligible={eligibleAll?.eligible ?? 0}
								scope={scope}
								title="All completed runs"
								description="Every completed run, regardless of summary state. Use sparingly."
							/>
						</RadioGroup>

						<EstimateLine
							preview={preview}
							error={estimateError}
							pending={backfill.isPending}
							scope={scope}
							agentId={agentId}
							onRetry={() => runDryRun(scope)}
						/>
						<SubmitLine
							error={submitError}
							pending={backfill.isPending}
							onRetry={confirmAndSubmit}
						/>
					</div>

					<AlertDialogFooter>
						<AlertDialogCancel className="min-h-11">
							Cancel
						</AlertDialogCancel>
						{showAction ? (
							<AlertDialogAction
								onClick={(event) => {
									event.preventDefault();
									confirmAndSubmit();
								}}
								disabled={backfill.isPending}
								className="min-h-11"
							>
								{backfill.isPending ? (
									<Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />
								) : null}
								{backfill.isPending ? "Starting…" : "Start"}
							</AlertDialogAction>
						) : null}
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</>
	);
}

interface ScopeOptionProps {
	id: string;
	value: ResummarizeScope;
	eligible: number;
	scope: ResummarizeScope;
	title: string;
	description: string;
}

function ScopeOption({
	id,
	value,
	eligible,
	scope,
	title,
	description,
}: ScopeOptionProps) {
	const selected = scope === value;
	const disabled = eligible === 0;
	return (
		<Label
			htmlFor={id}
			className={`flex min-h-11 cursor-pointer items-start gap-3 rounded-[var(--bf-radius-feature)] border border-border/70 px-3 py-3 text-left transition-colors motion-reduce:transition-none ${
				selected
					? "border-primary/40 bg-primary/5"
					: "bg-card hover:bg-muted/60"
			} ${disabled ? "cursor-not-allowed opacity-60" : ""}`}
		>
			<RadioGroupItem
				id={id}
				value={value}
				disabled={disabled}
				className="mt-0.5 shrink-0"
			/>
			<div className="grid flex-1 gap-1 text-sm">
				<div className="flex items-center justify-between gap-2">
					<span className="font-medium">{title}</span>
					<span className="text-sm text-muted-foreground tabular-nums">
						{eligible} run{eligible === 1 ? "" : "s"}
					</span>
				</div>
				<span className="text-sm leading-6 text-muted-foreground">
					{description}
				</span>
			</div>
		</Label>
	);
}

interface EstimateLineProps {
	preview: {
		eligible: number;
		estimated_cost_usd: string;
		cost_basis: "history" | "fallback";
	} | null;
	error: string | null;
	pending: boolean;
	scope: ResummarizeScope;
	agentId?: string;
	onRetry: () => void;
}

function EstimateLine({
	preview,
	error,
	pending,
	scope,
	agentId,
	onRetry,
}: EstimateLineProps) {
	if (pending && !preview) {
		return (
			<div className="flex min-h-11 items-center gap-2 rounded-[var(--bf-radius-feature)] border border-border/70 bg-muted/50 px-3 py-3 text-sm leading-6 text-muted-foreground">
				<Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />
				Computing estimate…
			</div>
		);
	}
	if (error) {
		return (
			<div className="grid gap-3 rounded-[var(--bf-radius-feature)] border border-border/70 bg-muted/50 px-3 py-3 text-sm leading-6">
				<div className="text-[var(--bf-danger)]">{error}</div>
				<Button
					type="button"
					variant="outline"
					size="sm"
					className="min-h-11 justify-self-start"
					onClick={onRetry}
				>
					Retry estimate
				</Button>
			</div>
		);
	}
	if (!preview) return null;
	const _ = scope; // kept for future per-scope copy
	void _;
	if (preview.eligible === 0) {
		return (
			<div className="rounded-[var(--bf-radius-feature)] border border-border/70 bg-muted/50 px-3 py-3 text-sm leading-6 text-muted-foreground">
				Nothing to do for this scope.
			</div>
		);
	}
	return (
		<div className="rounded-[var(--bf-radius-feature)] border border-border/70 bg-muted/50 px-3 py-3 text-sm leading-6">
			Will resummarize{" "}
			<strong className="font-medium">{preview.eligible}</strong>{" "}
			{agentId ? "runs for this agent" : "runs platform-wide"}. Estimated
			cost{" "}
			<strong className="font-medium">
				${Number(preview.estimated_cost_usd).toFixed(2)}
			</strong>{" "}
			<span className="text-muted-foreground">
				(
				{preview.cost_basis === "history"
					? "from recent summaries"
					: "flat estimate; no history"}
				)
			</span>
			.
		</div>
	);
}

interface SubmitLineProps {
	error: string | null;
	pending: boolean;
	onRetry: () => void;
}

function SubmitLine({ error, pending, onRetry }: SubmitLineProps) {
	const errorRef = useRef<HTMLDivElement>(null);
	useEffect(() => {
		if (error) errorRef.current?.focus();
	}, [error]);
	if (!error) return null;
	return (
		<div
			role="alert"
			ref={errorRef}
			tabIndex={-1}
			className="grid gap-3 rounded-[var(--bf-radius-feature)] border border-border/70 bg-muted/50 px-3 py-3 text-sm leading-6"
		>
			<div className="text-[var(--bf-danger)]">{error}</div>
			<Button
				type="button"
				variant="outline"
				size="sm"
				className="min-h-11 justify-self-start"
				onClick={onRetry}
				disabled={pending}
			>
				{pending ? "Retrying…" : "Retry start"}
			</Button>
		</div>
	);
}

// -----------------------------------------------------------------------------

interface SummaryBackfillProgressProps {
	jobId: string;
	agentId?: string;
	onDismiss: () => void;
}

function SummaryBackfillProgress({
	jobId,
	agentId,
	onDismiss,
}: SummaryBackfillProgressProps) {
	const jobQuery = useSummaryBackfillJob(jobId);
	const cancel = useCancelBackfillJob();
	const [cancelError, setCancelError] = useState<string | null>(null);
	const [live, setLive] = useState<{
		total: number;
		succeeded: number;
		failed: number;
		status: string;
		actual_cost_usd: string;
	} | null>(null);

	useEffect(() => {
		webSocketService.connectInBackground([`summary-backfill:${jobId}`]);
		const unsubscribe = webSocketService.onSummaryBackfillUpdate(
			jobId,
			(update) => {
				setLive({
					total: update.total,
					succeeded: update.succeeded,
					failed: update.failed,
					status: update.status,
					actual_cost_usd: update.actual_cost_usd,
				});
			},
		);
		return unsubscribe;
	}, [jobId]);

	const snapshot = useMemo(
		() =>
			live ?? {
				total: jobQuery.data?.total ?? 0,
				succeeded: jobQuery.data?.succeeded ?? 0,
				failed: jobQuery.data?.failed ?? 0,
				status: jobQuery.data?.status ?? "running",
				actual_cost_usd: String(jobQuery.data?.actual_cost_usd ?? "0"),
			},
		[live, jobQuery.data],
	);

	const pct = useMemo(() => {
		if (!snapshot.total) return 0;
		return Math.min(
			100,
			Math.round(
				((snapshot.succeeded + snapshot.failed) / snapshot.total) * 100,
			),
		);
	}, [snapshot]);

	const isTerminal =
		snapshot.status === "complete" ||
		snapshot.status === "cancelled" ||
		snapshot.status === "failed";

	// Fire the completion toast exactly once per terminal transition. The card
	// intentionally does NOT auto-dismiss — the user clicks X to clear it so
	// failure counts stay visible.
	const toastedRef = useRef(false);
	useEffect(() => {
		if (!isTerminal || toastedRef.current) return;
		toastedRef.current = true;
		const cost = Number(snapshot.actual_cost_usd).toFixed(2);
		if (snapshot.status === "complete") {
			const msg =
				snapshot.failed > 0
					? `Backfilled ${snapshot.succeeded} of ${snapshot.total} — ${snapshot.failed} failed — $${cost}`
					: `Backfilled ${snapshot.succeeded} of ${snapshot.total} — $${cost}`;
			toast.success(msg);
		} else if (snapshot.status === "cancelled") {
			toast.info(
				`Backfill cancelled at ${snapshot.succeeded + snapshot.failed} of ${snapshot.total}`,
			);
		} else if (snapshot.status === "failed") {
			toast.error(
				`Backfill failed at ${snapshot.succeeded + snapshot.failed} of ${snapshot.total}`,
			);
		}
	}, [
		isTerminal,
		snapshot.status,
		snapshot.succeeded,
		snapshot.failed,
		snapshot.total,
		snapshot.actual_cost_usd,
	]);

	function handleCancel() {
		setCancelError(null);
		cancel.mutate(
			{ params: { path: { job_id: jobId } } },
			{
				onError: () => {
					setCancelError("Could not cancel backfill.");
					toast.error("Failed to cancel backfill");
				},
			},
		);
	}

	if (jobQuery.isLoading && !jobQuery.data && !live) {
		return (
			<div
				className="inline-flex w-full max-w-[min(28rem,100%)] flex-col gap-3 rounded-[var(--bf-radius-feature)] border border-border/70 bg-card px-3 py-3 text-sm shadow-sm sm:flex-row sm:items-center"
				data-testid="summary-backfill-progress"
				aria-live="polite"
				data-status="loading"
			>
				<Loader2 className="h-4 w-4 shrink-0 animate-spin motion-reduce:animate-none text-muted-foreground" />
				<div className="flex min-w-0 flex-1 flex-col gap-1.5">
					<div className="font-medium">Loading backfill progress</div>
					<div className="text-sm leading-6 text-muted-foreground">
						Checking the current job state…
					</div>
				</div>
			</div>
		);
	}

	if (jobQuery.isError && !jobQuery.data && !live) {
		return (
			<div
				className="inline-flex w-full max-w-[min(28rem,100%)] flex-col gap-3 rounded-[var(--bf-radius-feature)] border border-border/70 bg-card px-3 py-3 text-sm shadow-sm sm:flex-row sm:items-center"
				data-testid="summary-backfill-progress"
				aria-live="polite"
				data-status="error"
			>
				<AlertTriangle className="h-4 w-4 shrink-0 text-[var(--bf-danger)]" />
				<div className="flex min-w-0 flex-1 flex-col gap-1.5">
					<div className="font-medium">
						Could not load backfill progress
					</div>
					<div className="text-sm leading-6 text-muted-foreground">
						Refresh to check whether the job is still running.
					</div>
				</div>
				<Button
					type="button"
					variant="outline"
					className="min-h-11 shrink-0"
					onClick={() => void jobQuery.refetch()}
					disabled={jobQuery.isFetching}
				>
					{jobQuery.isFetching ? "Retrying…" : "Retry job"}
				</Button>
			</div>
		);
	}

	const statusIcon = !isTerminal ? (
		<Loader2 className="h-4 w-4 shrink-0 animate-spin motion-reduce:animate-none text-muted-foreground" />
	) : snapshot.status === "complete" ? (
		<CheckCircle className="h-4 w-4 shrink-0 text-[var(--bf-success)]" />
	) : snapshot.status === "cancelled" ? (
		<XCircle className="h-4 w-4 shrink-0 text-muted-foreground" />
	) : (
		<AlertTriangle className="h-4 w-4 shrink-0 text-[var(--bf-danger)]" />
	);

	const headerLabel = !isTerminal
		? "Backfilling summaries"
		: snapshot.status === "complete"
			? "Backfill complete"
			: snapshot.status === "cancelled"
				? "Backfill cancelled"
				: "Backfill failed";

	// When there are failures, link to the Runs tab with a `summary=failed`
	// filter so the admin can review the failed runs and retry them.
	const reviewHref = agentId
		? `/agents/${agentId}?tab=runs&summary=failed`
		: "/history?type=agents";

	return (
		<div
			className="inline-flex w-full max-w-[min(28rem,100%)] flex-col gap-3 rounded-[var(--bf-radius-feature)] border border-border/70 bg-card px-3 py-3 text-sm shadow-sm sm:flex-row sm:items-center"
			data-testid="summary-backfill-progress"
			aria-live="polite"
			data-status={snapshot.status}
		>
			{statusIcon}
			<div className="flex min-w-0 flex-1 flex-col gap-1.5">
				<div className="flex items-center justify-between gap-3">
					<span className="font-medium">{headerLabel}</span>
					<span className="text-sm text-muted-foreground">
						{snapshot.succeeded + snapshot.failed} /{" "}
						{snapshot.total}
					</span>
				</div>
				<Progress value={pct} className="h-2" />
				{jobQuery.isError ? (
					<div className="flex flex-wrap items-center gap-2 text-sm leading-6 text-[var(--bf-danger)]">
						<span>Refreshing backfill progress failed.</span>
						<Button
							type="button"
							variant="outline"
							className="min-h-11"
							onClick={() => void jobQuery.refetch()}
							disabled={jobQuery.isFetching}
						>
							{jobQuery.isFetching ? "Retrying…" : "Retry job"}
						</Button>
					</div>
				) : null}
				{cancelError ? (
					<div className="flex flex-wrap items-center gap-2 text-sm leading-6 text-[var(--bf-danger)]">
						<span>{cancelError}</span>
					</div>
				) : null}
				{snapshot.failed > 0 ? (
					<Link
						to={reviewHref}
						className="inline-flex min-h-11 max-w-full flex-wrap items-center gap-1 text-sm leading-6 text-[var(--bf-danger)] hover:underline motion-reduce:transition-none"
					>
						{snapshot.failed} failed —{" "}
						{agentId ? "Review failed runs" : "View agent runs"} →
					</Link>
				) : null}
			</div>
			{isTerminal ? (
				<button
					type="button"
					aria-label="Dismiss"
					title="Dismiss"
					onClick={onDismiss}
					className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors motion-reduce:transition-none hover:bg-accent hover:text-foreground"
					data-testid="summary-backfill-dismiss"
				>
					<X className="h-4 w-4" />
				</button>
			) : (
				<button
					type="button"
					aria-label={
						cancelError ? "Retry cancel" : "Cancel backfill"
					}
					title={cancelError ? "Retry cancel" : "Cancel backfill"}
					onClick={handleCancel}
					disabled={cancel.isPending}
					className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors motion-reduce:transition-none hover:bg-accent hover:text-foreground disabled:opacity-50"
					data-testid="summary-backfill-cancel"
				>
					{cancel.isPending ? (
						<Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />
					) : (
						<X className="h-4 w-4" />
					)}
				</button>
			)}
		</div>
	);
}
