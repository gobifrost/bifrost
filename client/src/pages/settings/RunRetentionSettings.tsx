import { useEffect, useRef, useState, type MutableRefObject } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Eye, Loader2, Trash2 } from "lucide-react";
import { toast } from "sonner";

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
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SettingsToggleRow } from "@/components/shared/SettingsToggleRow";
import { SettingsLoadError } from "@/components/shared/SettingsLoadError";
import { ApiError, getErrorMessage } from "@/lib/api-error";
import { formatRelativeTime } from "@/lib/utils";
import { formatAuditDay, formatAuditTime } from "@/pages/audit/auditRetentionFormat";
import { watchAuditJob } from "@/services/auditRetention";
import type { PlatformJob } from "@/services/platformJobs";
import {
	RUN_RETENTION_DAYS_QUERY_KEY,
	getRunRetention,
	previewRunRetention,
	startRunRetention,
	updateRunRetention,
	type RunRetentionPlan,
	type RunRetentionPreview,
	type RunRetentionSettingsUpdate,
	type RunRetentionStatus,
} from "@/services/runRetention";

const TERMINAL = new Set([
	"succeeded",
	"failed",
	"cancelled",
	"requires_action",
]);

const MIN_DAYS = 30;
const MAX_DAYS = 3650;

type Draft = { days: string; forever: boolean };
type FailedSave = {
	edit: Draft;
	action: "save" | "preview";
	message: string;
	/** Network and server errors can be retried; a refused request cannot. */
	retryable: boolean;
};

const DAYS_INPUT_ID = "run-retention-days";
const FOREVER_SWITCH_ID = "run-retention-forever";

function failure(
	edit: Draft,
	action: FailedSave["action"],
	error: unknown,
): FailedSave {
	return {
		edit,
		action,
		message: getErrorMessage(error, "Unknown error"),
		retryable: !(
			error instanceof ApiError &&
			error.statusCode !== undefined &&
			error.statusCode < 500
		),
	};
}

/** Focus the control a confirmation came from, once it is enabled again. */
function restoreFocus(pending: MutableRefObject<string | null>) {
	if (!pending.current) return;
	const target = document.getElementById(pending.current);
	if (!target || target.hasAttribute("disabled")) return;
	target.focus();
	pending.current = null;
}

type RunState =
	| { kind: "idle" }
	| { kind: "starting" | "previewing" | "running" }
	| { kind: "planned"; plan: RunRetentionPlan }
	| { kind: "kept-forever" }
	| { kind: "failed"; message: string };

function parseDays(value: string): number | null {
	const days = Number(value);
	return value.trim() && Number.isInteger(days) ? days : null;
}

/** The settings a draft would save, or why it cannot be saved. */
function validate(
	draft: Draft,
): { settings: RunRetentionSettingsUpdate } | { error: string } {
	if (draft.forever) return { settings: { days: null } };
	const days = parseDays(draft.days);
	if (days === null || days < MIN_DAYS || days > MAX_DAYS) {
		return {
			error: `Keep finished runs and events for ${MIN_DAYS}–${MAX_DAYS.toLocaleString()} days.`,
		};
	}
	return { settings: { days } };
}

/** The shorter window an edit would save, or null when it keeps runs longer. */
function shorterWindow(
	saved: RunRetentionSettingsUpdate,
	next: RunRetentionSettingsUpdate,
): number | null {
	if (next.days === null) return null;
	return saved.days === null || next.days < saved.days ? next.days : null;
}

function count(n: number, noun: string): string {
	return `${n.toLocaleString()} ${noun}${n === 1 ? "" : "s"}`;
}

function planParts(plan: RunRetentionPlan): [string, string, string] {
	return [
		count(plan.workflow_runs, "workflow run"),
		count(plan.agent_runs, "agent run"),
		count(plan.events, "event"),
	];
}

function isEmpty(plan: RunRetentionPlan): boolean {
	return plan.workflow_runs + plan.agent_runs + plan.events === 0;
}

function planSummary(plan: RunRetentionPlan): string {
	if (isEmpty(plan)) return "Nothing to delete.";
	const [workflowRuns, agentRuns, events] = planParts(plan);
	return `Would delete ${workflowRuns}, ${agentRuns} and ${events}.`;
}

function lastRunSummary(job: PlatformJob): string {
	const when = formatRelativeTime(job.completed_at ?? job.created_at);
	const result = job.result;
	const kind = result?.dry_run ? "Last preview" : "Last run";
	if (job.status !== "succeeded" || !result) {
		return `${kind} ${when} ${job.status}.`;
	}
	// A preview's counts are in the status line right below.
	if (result.dry_run) return `${kind} ${when}.`;
	if (result.skipped) return `${kind} ${when}: skipped, runs are kept forever.`;
	const [workflowRuns, agentRuns, events] = planParts({
		workflow_runs: result.workflow_runs_deleted as number,
		agent_runs: result.agent_runs_deleted as number,
		events: result.events_deleted as number,
	});
	return `${kind} ${when}: deleted ${workflowRuns}, ${agentRuns}, ${events}.${result.continues ? " Continues tomorrow." : ""}`;
}

function storedSummary(info: RunRetentionStatus["info"]): string {
	const oldest = info.oldest_finished_run
		? `Oldest kept run: ${formatAuditTime(info.oldest_finished_run)}.`
		: "No finished runs are kept.";
	const rolledUp = info.rolled_up_through
		? `Rolled-up history: ${count(info.rolled_up_runs, "run")} through ${formatAuditDay(info.rolled_up_through)}.`
		: "No rolled-up history yet.";
	return `${oldest} ${rolledUp}`;
}

function shortenWarning(
	preview: RunRetentionPreview,
	days: number,
): string {
	if (isEmpty(preview)) {
		return `Nothing is old enough to delete yet. From now on, finished runs and events older than ${days} days are deleted at the daily run.`;
	}
	const [workflowRuns, agentRuns, events] = planParts(preview);
	return `Deletes ${workflowRuns}, ${agentRuns} and ${events} at the next daily run. This can't be undone.`;
}

export function RunRetentionSettings() {
	const queryClient = useQueryClient();
	const [status, setStatus] = useState<RunRetentionStatus | null>(null);
	const [draft, setDraft] = useState<Draft>({ days: "30", forever: false });
	const [loading, setLoading] = useState(true);
	const [loadError, setLoadError] = useState(false);
	const [loadAttempt, setLoadAttempt] = useState(0);
	const [saving, setSaving] = useState(false);
	const [validationError, setValidationError] = useState<string | null>(null);
	const [failedSave, setFailedSave] = useState<FailedSave | null>(null);
	const [confirm, setConfirm] = useState<{
		days: number;
		preview: RunRetentionPreview;
	} | null>(null);
	const [run, setRun] = useState<RunState>({ kind: "idle" });
	const savePending = useRef(false);
	const stopWatching = useRef<(() => void) | null>(null);
	// The control to focus after the shorten dialog closes and saving ends.
	const returnFocusTo = useRef<string | null>(null);
	const dialogOpen = confirm !== null;

	const showSaved = (next: RunRetentionStatus) => {
		setStatus(next);
		setDraft((current) => ({
			// Keep the last typed window while runs are kept forever, so
			// turning "forever" off offers it again.
			days:
				next.settings.days === null
					? current.days
					: String(next.settings.days),
			forever: next.settings.days === null,
		}));
	};

	useEffect(() => {
		let active = true;
		getRunRetention()
			.then((next) => {
				if (active) showSaved(next);
			})
			.catch(() => {
				if (active) setLoadError(true);
			})
			.finally(() => {
				if (active) setLoading(false);
			});
		return () => {
			active = false;
		};
	}, [loadAttempt]);

	useEffect(() => () => stopWatching.current?.(), []);

	const commit = async (settings: RunRetentionSettingsUpdate, edit: Draft) => {
		savePending.current = true;
		setSaving(true);
		try {
			showSaved(await updateRunRetention(settings));
			void queryClient.invalidateQueries({
				queryKey: RUN_RETENTION_DAYS_QUERY_KEY,
			});
			toast.success("Run history settings saved");
		} catch (error) {
			setFailedSave(failure(edit, "save", error));
		} finally {
			savePending.current = false;
			setSaving(false);
		}
	};

	const saveDraft = async (edit: Draft) => {
		if (loading || loadError || !status || savePending.current) return;
		const checked = validate(edit);
		if ("error" in checked) {
			setValidationError(checked.error);
			return;
		}
		setValidationError(null);
		setFailedSave(null);
		const saved = status.settings;
		const next = checked.settings;
		if (next.days === saved.days) return;
		const days = shorterWindow(saved, next);
		if (days === null) {
			await commit(next, edit);
			return;
		}
		savePending.current = true;
		setSaving(true);
		try {
			const preview = await previewRunRetention(days);
			returnFocusTo.current =
				saved.days === null ? FOREVER_SWITCH_ID : DAYS_INPUT_ID;
			setConfirm({ days, preview });
		} catch (error) {
			setFailedSave(failure(edit, "preview", error));
		} finally {
			savePending.current = false;
			setSaving(false);
		}
	};

	const cancelShortening = () => {
		setConfirm(null);
		if (status) showSaved(status);
	};

	const startRun = async (dryRun: boolean) => {
		setRun({ kind: "starting" });
		let jobId: string;
		try {
			jobId = (await startRunRetention(dryRun)).job_id;
		} catch (error) {
			setRun({
				kind: "failed",
				message: getErrorMessage(error, "Couldn't start the run."),
			});
			return;
		}
		if (!dryRun) {
			toast.success("Run history cleanup queued", {
				description: "Progress is available in notifications.",
			});
		}
		setRun({ kind: dryRun ? "previewing" : "running" });
		stopWatching.current?.();
		stopWatching.current = watchAuditJob(jobId, (job) => {
			if (!TERMINAL.has(job.status)) return;
			stopWatching.current?.();
			stopWatching.current = null;
			if (job.status === "succeeded" && dryRun) {
				setRun(
					job.result?.skipped
						? { kind: "kept-forever" }
						: {
								kind: "planned",
								plan: job.result as unknown as RunRetentionPlan,
							},
				);
			} else if (job.status === "succeeded") {
				setRun({ kind: "idle" });
			} else {
				setRun({
					kind: "failed",
					message:
						job.error?.message ??
						`The ${dryRun ? "preview" : "run"} ended ${job.status}.`,
				});
			}
			// Refresh the stored counts and last run. If that read fails the
			// card keeps showing the previous status; the next load corrects it.
			void getRunRetention().then(setStatus, () => undefined);
		});
	};

	const disabled = loading || saving || loadError || dialogOpen;

	useEffect(() => {
		if (!disabled) restoreFocus(returnFocusTo);
	}, [disabled]);
	const busyRun =
		run.kind === "starting" ||
		run.kind === "previewing" ||
		run.kind === "running";
	const editDraft = (change: Partial<Draft>) => {
		setDraft((current) => ({ ...current, ...change }));
		setValidationError(null);
	};

	return (
		<Card>
			<CardHeader>
				<CardTitle>Run history</CardTitle>
				<CardDescription>
					How long finished workflow runs, agent runs and events are kept.
					Older ones are deleted for good at the daily run.
				</CardDescription>
			</CardHeader>
			<CardContent className="space-y-5">
				{loadError && (
					<SettingsLoadError
						name="run history settings"
						onRetry={() => {
							setLoading(true);
							setLoadError(false);
							setLoadAttempt((value) => value + 1);
						}}
					/>
				)}
				{status && (
					<p className="text-sm text-muted-foreground [overflow-wrap:anywhere]">
						{storedSummary(status.info)}
					</p>
				)}

				<div className="space-y-2">
					<Label htmlFor={DAYS_INPUT_ID}>
						Keep finished runs and events (days)
					</Label>
					<Input
						id={DAYS_INPUT_ID}
						type="number"
						min={MIN_DAYS}
						max={MAX_DAYS}
						value={draft.days}
						disabled={disabled || draft.forever}
						onChange={(event) => editDraft({ days: event.target.value })}
						onBlur={() => void saveDraft(draft)}
						aria-invalid={validationError !== null}
						aria-describedby="run-retention-days-help"
						className="min-h-11 w-full sm:w-32"
					/>
					<p
						id="run-retention-days-help"
						className="text-xs text-muted-foreground"
					>
						Counted from when the run finished or the event happened.
						Changes save when you leave the field.
					</p>
				</div>
				{validationError && (
					<p role="alert" className="text-sm text-destructive">
						{validationError}
					</p>
				)}

				<SettingsToggleRow
					id={FOREVER_SWITCH_ID}
					label="Keep forever"
					description="Finished runs and events are never deleted."
					checked={draft.forever}
					disabled={disabled}
					busy={loading ? "loading" : saving ? "saving" : undefined}
					onChange={(forever) => {
						const edit = { ...draft, forever };
						setDraft(edit);
						void saveDraft(edit);
					}}
				/>

				{failedSave && (
					<div role="alert" className="space-y-3">
						<p className="text-sm text-destructive [overflow-wrap:anywhere]">
							{failedSave.action === "save"
								? "Couldn't save run history settings"
								: "Couldn't check what this change deletes"}
							: {failedSave.message}
							{failedSave.retryable && " Your edit is ready to retry."}
						</p>
						{failedSave.retryable && (
							<Button
								type="button"
								variant="outline"
								className="min-h-11"
								onClick={() => void saveDraft(failedSave.edit)}
							>
								Retry save
							</Button>
						)}
					</div>
				)}

				<div className="flex flex-col gap-3 border-t pt-5 sm:flex-row sm:items-center sm:justify-between">
					<div className="min-w-0 space-y-1 text-sm">
						{status && (
							<p className="text-muted-foreground">
								{status.last_run
									? lastRunSummary(status.last_run)
									: "Not run yet."}
							</p>
						)}
						{run.kind === "previewing" && (
							<p role="status" className="text-muted-foreground">
								Preparing preview…
							</p>
						)}
						{run.kind === "planned" && (
							<p role="status">{planSummary(run.plan)}</p>
						)}
						{run.kind === "kept-forever" && (
							<p role="status">
								Runs are kept forever; nothing would be deleted.
							</p>
						)}
						{run.kind === "failed" && (
							<p role="alert" className="text-destructive">
								{run.message}
							</p>
						)}
					</div>
					<div className="flex shrink-0 flex-col gap-2 sm:flex-row">
						<Button
							type="button"
							variant="outline"
							className="min-h-11"
							disabled={disabled || busyRun}
							onClick={() => void startRun(true)}
						>
							{run.kind === "previewing" ? (
								<Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
							) : (
								<Eye className="mr-2 h-4 w-4" />
							)}
							Preview
						</Button>
						<Button
							type="button"
							variant="outline"
							className="min-h-11"
							disabled={disabled || busyRun}
							onClick={() => void startRun(false)}
						>
							{run.kind === "running" ? (
								<Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
							) : (
								<Trash2 className="mr-2 h-4 w-4" />
							)}
							Run now
						</Button>
					</div>
				</div>
			</CardContent>

			<AlertDialog
				open={dialogOpen}
				onOpenChange={(open) => {
					if (!open) cancelShortening();
				}}
			>
				<AlertDialogContent
					onCloseAutoFocus={(event) => {
						event.preventDefault();
						restoreFocus(returnFocusTo);
					}}
				>
					<AlertDialogHeader>
						<AlertDialogTitle>Shorten the run history window?</AlertDialogTitle>
						<AlertDialogDescription>
							{confirm && shortenWarning(confirm.preview, confirm.days)}
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel className="min-h-11">Cancel</AlertDialogCancel>
						<AlertDialogAction
							variant="destructive"
							className="min-h-11"
							onClick={(event) => {
								// Close through state, not Radix, so the close is not a cancel.
								event.preventDefault();
								if (!confirm) return;
								const { days } = confirm;
								setConfirm(null);
								void commit({ days }, draft);
							}}
						>
							Delete and save
						</AlertDialogAction>
					</AlertDialogFooter>
				</AlertDialogContent>
			</AlertDialog>
		</Card>
	);
}
