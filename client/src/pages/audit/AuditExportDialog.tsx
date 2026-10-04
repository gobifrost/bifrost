import { useEffect, useId, useRef, useState } from "react";
import { Download, Loader2 } from "lucide-react";

import { OrganizationSelect } from "@/components/forms/OrganizationSelect";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { formatEvents } from "./auditRetentionFormat";
import {
	createAuditExport,
	downloadAuditExport,
	watchAuditJob,
	type AuditExportRequest,
} from "@/services/auditRetention";

const MAX_EXPORT_MS = 366 * 24 * 60 * 60 * 1000;
const TERMINAL = new Set([
	"succeeded",
	"failed",
	"cancelled",
	"requires_action",
]);

type ExportState =
	| { kind: "form" }
	| { kind: "submitting" }
	| { kind: "preparing"; jobId: string }
	| { kind: "ready"; jobId: string; rows: number }
	| { kind: "failed"; message: string };

/** The export request for whole local days, or why the range is not exportable. */
function exportRange(
	startDate: string,
	endDate: string,
): { start: string; end: string } | { error: string } {
	if (!startDate || !endDate)
		return { error: "Choose a start and end date." };
	const start = new Date(`${startDate}T00:00:00`);
	const end = new Date(`${endDate}T23:59:59.999`);
	if (end <= start) {
		return { error: "The end date must be on or after the start date." };
	}
	if (end.getTime() - start.getTime() > MAX_EXPORT_MS) {
		return { error: "An export covers at most 366 days." };
	}
	return { start: start.toISOString(), end: end.toISOString() };
}

export function AuditExportDialog({
	defaultAction,
	defaultStartDate,
	defaultEndDate,
	onClose,
}: {
	defaultAction: string;
	defaultStartDate: string;
	defaultEndDate: string;
	onClose: () => void;
}) {
	const [startDate, setStartDate] = useState(defaultStartDate);
	const [endDate, setEndDate] = useState(defaultEndDate);
	const [action, setAction] = useState(defaultAction);
	const [organizationId, setOrganizationId] = useState<
		string | null | undefined
	>(undefined);
	const [rangeError, setRangeError] = useState<string | null>(null);
	const [state, setState] = useState<ExportState>({ kind: "form" });
	const [downloading, setDownloading] = useState(false);
	const [downloadError, setDownloadError] = useState<string | null>(null);
	const stopWatching = useRef<(() => void) | null>(null);
	const rangeErrorId = useId();

	useEffect(() => () => stopWatching.current?.(), []);

	const filename = `audit-export-${startDate}-${endDate}.jsonl.gz`;

	const startExport = async () => {
		const range = exportRange(startDate, endDate);
		if ("error" in range) {
			setRangeError(range.error);
			return;
		}
		setRangeError(null);
		const body: AuditExportRequest = {
			start_date: range.start,
			end_date: range.end,
		};
		if (action.trim()) body.action = action.trim();
		if (organizationId) body.organization_id = organizationId;
		setState({ kind: "submitting" });
		let jobId: string;
		try {
			jobId = (await createAuditExport(body)).job_id;
		} catch (error) {
			setState({
				kind: "failed",
				message:
					error instanceof Error
						? error.message
						: "Couldn't start the export.",
			});
			return;
		}
		setState({ kind: "preparing", jobId });
		stopWatching.current = watchAuditJob(jobId, (job) => {
			if (!TERMINAL.has(job.status)) return;
			stopWatching.current?.();
			stopWatching.current = null;
			setState(
				job.status === "succeeded"
					? { kind: "ready", jobId, rows: job.result?.rows as number }
					: {
							kind: "failed",
							message:
								job.error?.message ??
								`The export ended ${job.status}.`,
						},
			);
		});
	};

	const download = async (jobId: string) => {
		setDownloading(true);
		setDownloadError(null);
		try {
			await downloadAuditExport(jobId, filename);
		} catch (error) {
			setDownloadError(
				error instanceof Error ? error.message : "Download failed.",
			);
		} finally {
			setDownloading(false);
		}
	};

	const editing = state.kind === "form" || state.kind === "failed";

	return (
		<Dialog open onOpenChange={(open) => !open && onClose()}>
			<DialogContent className="sm:max-w-lg">
				<DialogHeader>
					<DialogTitle>Export audit events</DialogTitle>
					<DialogDescription>
						One file of archived and current events, up to 366 days
						at a time.
					</DialogDescription>
				</DialogHeader>

				<div className="space-y-4">
					<div className="grid gap-4 sm:grid-cols-2">
						<div className="space-y-2">
							<Label htmlFor="audit-export-start">
								Start date
							</Label>
							<Input
								id="audit-export-start"
								type="date"
								required
								value={startDate}
								max={endDate || undefined}
								disabled={!editing}
								aria-invalid={rangeError !== null}
								aria-describedby={
									rangeError ? rangeErrorId : undefined
								}
								onChange={(event) => {
									setStartDate(event.target.value);
									setRangeError(null);
								}}
								className="min-h-11"
							/>
						</div>
						<div className="space-y-2">
							<Label htmlFor="audit-export-end">End date</Label>
							<Input
								id="audit-export-end"
								type="date"
								required
								value={endDate}
								min={startDate || undefined}
								disabled={!editing}
								aria-invalid={rangeError !== null}
								aria-describedby={
									rangeError ? rangeErrorId : undefined
								}
								onChange={(event) => {
									setEndDate(event.target.value);
									setRangeError(null);
								}}
								className="min-h-11"
							/>
						</div>
					</div>
					{rangeError && (
						<p
							id={rangeErrorId}
							role="alert"
							className="text-sm text-destructive"
						>
							{rangeError}
						</p>
					)}
					<div className="space-y-2">
						<Label htmlFor="audit-export-action">
							Action prefix (optional)
						</Label>
						<Input
							id="audit-export-action"
							value={action}
							placeholder="e.g. auth."
							disabled={!editing}
							onChange={(event) => setAction(event.target.value)}
							className="min-h-11"
						/>
					</div>
					<div className="space-y-2">
						<Label id="audit-export-organization">
							Organization (optional)
						</Label>
						<OrganizationSelect
							aria-labelledby="audit-export-organization"
							value={organizationId}
							onChange={setOrganizationId}
							showAll
							showGlobal={false}
							disabled={!editing}
							triggerClassName="min-h-11 w-full"
						/>
					</div>

					{state.kind === "preparing" && (
						<p
							role="status"
							className="flex items-center gap-2 text-sm text-muted-foreground"
						>
							<Loader2
								aria-hidden="true"
								className="size-4 animate-spin motion-reduce:animate-none"
							/>
							Preparing export…
						</p>
					)}
					{state.kind === "ready" && (
						<p
							role="status"
							className="text-sm text-muted-foreground"
						>
							{formatEvents(state.rows)} ready.
						</p>
					)}
					{state.kind === "failed" && (
						<p role="alert" className="text-sm text-destructive">
							{state.message}
						</p>
					)}
					{downloadError && (
						<p role="alert" className="text-sm text-destructive">
							{downloadError}
						</p>
					)}
				</div>

				<DialogFooter>
					<Button
						type="button"
						variant="outline"
						className="min-h-11"
						onClick={onClose}
					>
						Close
					</Button>
					{state.kind === "ready" ? (
						<Button
							type="button"
							className="min-h-11"
							disabled={downloading}
							onClick={() => void download(state.jobId)}
						>
							{downloading ? (
								<Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
							) : (
								<Download className="mr-2 h-4 w-4" />
							)}
							Download
						</Button>
					) : (
						<Button
							type="button"
							className="min-h-11"
							disabled={!editing}
							onClick={() => void startExport()}
						>
							{state.kind === "submitting" ||
							state.kind === "preparing" ? (
								<Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
							) : null}
							Start export
						</Button>
					)}
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
