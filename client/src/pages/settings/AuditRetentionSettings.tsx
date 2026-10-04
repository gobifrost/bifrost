import { useEffect, useRef, useState } from "react";
import { Archive, Eye, Loader2 } from "lucide-react";
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
import {
	formatAuditDay,
	formatAuditTime,
	formatCount,
} from "@/pages/audit/auditRetentionFormat";
import {
	getAuditRetention,
	previewAuditExpiry,
	runAuditArchive,
	updateAuditRetention,
	watchAuditJob,
	type AuditArchivePlan,
	type AuditExpiryPreview,
	type AuditRetentionSettingsUpdate,
	type AuditRetentionStatus,
} from "@/services/auditRetention";
import type { PlatformJob } from "@/services/platformJobs";

const TERMINAL = new Set([
	"succeeded",
	"failed",
	"cancelled",
	"requires_action",
]);

type Draft = { hotDays: string; archiveDays: string; forever: boolean };
type FieldError = { field: "hot" | "archive"; message: string };

type RunState =
	| { kind: "idle" }
	| { kind: "starting" | "previewing" | "running" }
	| { kind: "planned"; plan: AuditArchivePlan }
	| { kind: "failed"; message: string };

function parseDays(value: string): number | null {
	const days = Number(value);
	return value.trim() && Number.isInteger(days) ? days : null;
}

/** The settings a draft would save, or why it cannot be saved. */
function validate(
	draft: Draft,
): { settings: AuditRetentionSettingsUpdate } | { error: FieldError } {
	const hotDays = parseDays(draft.hotDays);
	if (hotDays === null || hotDays < 1 || hotDays > 3650) {
		return {
			error: {
				field: "hot",
				message: "Keep events in the database for 1–3,650 days.",
			},
		};
	}
	if (draft.forever) {
		return { settings: { hot_days: hotDays, archive_days: null } };
	}
	const archiveDays = parseDays(draft.archiveDays);
	if (archiveDays === null || archiveDays < hotDays) {
		return {
			error: {
				field: "archive",
				message: `Keep archives at least as long as events stay in the database (${hotDays} days), or keep them forever.`,
			},
		};
	}
	return { settings: { hot_days: hotDays, archive_days: archiveDays } };
}

function shortensArchive(
	saved: AuditRetentionSettingsUpdate,
	next: AuditRetentionSettingsUpdate,
): boolean {
	if (next.archive_days === null) return false;
	return (
		saved.archive_days === null || next.archive_days < saved.archive_days
	);
}

function planSummary(plan: AuditArchivePlan): string {
	const first = plan.days[0];
	const last = plan.days[plan.days.length - 1];
	const range = first
		? ` from ${formatAuditDay(first.day)}–${formatAuditDay(last.day)}`
		: "";
	return `Would archive ${formatCount(plan.eligible_rows)} events${range}; would delete ${formatCount(plan.expiring_rows)} archived events.`;
}

function lastRunSummary(job: PlatformJob): string {
	const when = job.completed_at ?? job.created_at;
	const result = job.result;
	const archived =
		result && !result.dry_run && typeof result.archived_rows === "number"
			? `: archived ${formatCount(result.archived_rows)} events`
			: "";
	const kind = result?.dry_run ? "Last preview" : "Last run";
	return `${kind} ${job.status} ${formatAuditTime(when)}${archived}.`;
}

function storedSummary(info: AuditRetentionStatus["info"]): string {
	const database = info.oldest_in_database
		? `Database holds events since ${formatAuditTime(info.oldest_in_database)}.`
		: "Database holds no events.";
	const archive = info.archived_through
		? `Archive holds ${formatCount(info.archived_rows)} events through ${formatAuditTime(info.archived_through)}.`
		: "Archive is empty.";
	return `${database} ${archive}`;
}

export function AuditRetentionSettings() {
	const [status, setStatus] = useState<AuditRetentionStatus | null>(null);
	const [draft, setDraft] = useState<Draft>({
		hotDays: "90",
		archiveDays: "365",
		forever: false,
	});
	const [loading, setLoading] = useState(true);
	const [loadError, setLoadError] = useState(false);
	const [loadAttempt, setLoadAttempt] = useState(0);
	const [saving, setSaving] = useState(false);
	const [validationError, setValidationError] = useState<FieldError | null>(
		null,
	);
	const [failedSave, setFailedSave] = useState<Draft | null>(null);
	const [confirm, setConfirm] = useState<{
		settings: AuditRetentionSettingsUpdate;
		preview: AuditExpiryPreview;
	} | null>(null);
	const [run, setRun] = useState<RunState>({ kind: "idle" });
	const savePending = useRef(false);
	const stopWatching = useRef<(() => void) | null>(null);

	const showSaved = (next: AuditRetentionStatus) => {
		setStatus(next);
		setDraft((current) => ({
			hotDays: String(next.settings.hot_days),
			// Keep the last typed archive window while archives are kept forever,
			// so turning "forever" off offers it again.
			archiveDays:
				next.settings.archive_days === null
					? current.archiveDays
					: String(next.settings.archive_days),
			forever: next.settings.archive_days === null,
		}));
	};

	useEffect(() => {
		let active = true;
		getAuditRetention()
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

	const commit = async (
		settings: AuditRetentionSettingsUpdate,
		edit: Draft,
	) => {
		savePending.current = true;
		setSaving(true);
		try {
			showSaved(await updateAuditRetention(settings));
			toast.success("Audit retention saved");
		} catch {
			setFailedSave(edit);
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
		if (
			next.hot_days === saved.hot_days &&
			next.archive_days === saved.archive_days
		)
			return;
		if (!shortensArchive(saved, next)) {
			await commit(next, edit);
			return;
		}
		savePending.current = true;
		setSaving(true);
		try {
			const preview = await previewAuditExpiry(next.archive_days);
			setConfirm({ settings: next, preview });
		} catch {
			setFailedSave(edit);
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
			jobId = (await runAuditArchive({ dry_run: dryRun })).job_id;
		} catch (error) {
			setRun({
				kind: "failed",
				message:
					error instanceof Error
						? error.message
						: "Couldn't start the archive run.",
			});
			return;
		}
		if (!dryRun) {
			toast.success("Audit archive queued", {
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
				setRun({
					kind: "planned",
					plan: job.result as unknown as AuditArchivePlan,
				});
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
			void getAuditRetention().then(setStatus, () => undefined);
		});
	};

	const disabled = loading || saving || loadError || confirm !== null;
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
				<CardTitle>Audit Retention</CardTitle>
				<CardDescription>
					How long audit events stay in the database before they're
					archived, and how long the archive keeps them.
				</CardDescription>
			</CardHeader>
			<CardContent className="space-y-5">
				{loadError && (
					<SettingsLoadError
						name="audit retention settings"
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

				<div className="grid gap-4 sm:grid-cols-2">
					<div className="space-y-2">
						<Label htmlFor="audit-retention-hot-days">
							Keep in database (days)
						</Label>
						<Input
							id="audit-retention-hot-days"
							type="number"
							min={1}
							max={3650}
							value={draft.hotDays}
							disabled={disabled}
							onChange={(event) =>
								editDraft({ hotDays: event.target.value })
							}
							onBlur={() => void saveDraft(draft)}
							aria-invalid={validationError?.field === "hot"}
							aria-describedby="audit-retention-hot-days-help"
							className="min-h-11 w-full sm:w-32"
						/>
						<p
							id="audit-retention-hot-days-help"
							className="text-xs text-muted-foreground"
						>
							Older events move to the archive at the daily run.
						</p>
					</div>
					<div className="space-y-2">
						<Label htmlFor="audit-retention-archive-days">
							Keep in archive (days)
						</Label>
						<Input
							id="audit-retention-archive-days"
							type="number"
							min={1}
							value={draft.archiveDays}
							disabled={disabled || draft.forever}
							onChange={(event) =>
								editDraft({ archiveDays: event.target.value })
							}
							onBlur={() => void saveDraft(draft)}
							aria-invalid={validationError?.field === "archive"}
							aria-describedby="audit-retention-archive-days-help"
							className="min-h-11 w-full sm:w-32"
						/>
						<p
							id="audit-retention-archive-days-help"
							className="text-xs text-muted-foreground"
						>
							Counted from when the event happened. Changes save
							when you leave the field.
						</p>
					</div>
				</div>
				{validationError && (
					<p role="alert" className="text-sm text-destructive">
						{validationError.message}
					</p>
				)}

				<SettingsToggleRow
					id="audit-retention-forever"
					label="Keep archives forever"
					description="Archived events are never deleted."
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
						<p className="text-sm text-destructive">
							Couldn't save audit retention settings. Your edit is
							ready to retry.
						</p>
						<Button
							type="button"
							variant="outline"
							className="min-h-11"
							onClick={() => void saveDraft(failedSave)}
						>
							Retry save
						</Button>
					</div>
				)}

				<div className="flex flex-col gap-3 border-t pt-5 sm:flex-row sm:items-center sm:justify-between">
					<div className="min-w-0 space-y-1 text-sm">
						{status?.last_run && (
							<p className="text-muted-foreground">
								{lastRunSummary(status.last_run)}
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
								<Archive className="mr-2 h-4 w-4" />
							)}
							Run now
						</Button>
					</div>
				</div>
			</CardContent>

			<AlertDialog
				open={confirm !== null}
				onOpenChange={(open) => {
					if (!open) cancelShortening();
				}}
			>
				<AlertDialogContent>
					<AlertDialogHeader>
						<AlertDialogTitle>
							Shorten the archive window?
						</AlertDialogTitle>
						<AlertDialogDescription>
							{confirm && expiryWarning(confirm.preview)}
						</AlertDialogDescription>
					</AlertDialogHeader>
					<AlertDialogFooter>
						<AlertDialogCancel className="min-h-11">
							Cancel
						</AlertDialogCancel>
						<AlertDialogAction
							variant="destructive"
							className="min-h-11"
							onClick={(event) => {
								// Close through state, not Radix, so the close is not a cancel.
								event.preventDefault();
								if (!confirm) return;
								const { settings } = confirm;
								setConfirm(null);
								void commit(settings, draft);
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

function expiryWarning(preview: AuditExpiryPreview): string {
	const range =
		preview.expiring_from && preview.expiring_to
			? ` (${formatAuditDay(preview.expiring_from)}–${formatAuditDay(preview.expiring_to)})`
			: "";
	return `This permanently deletes ${formatCount(preview.expiring_rows)} archived events${range} at the next daily run.`;
}
