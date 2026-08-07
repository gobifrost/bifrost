import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { format } from "date-fns";
import { Clock3, Copy, Cpu, Loader2, Server } from "lucide-react";
import { toast } from "sonner";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
	Sheet,
	SheetContent,
	SheetDescription,
	SheetHeader,
	SheetTitle,
} from "@/components/ui/sheet";
import { copyToClipboard } from "@/lib/clipboard";
import {
	getSchedulerTaskHistory,
	type SchedulerTaskStatus,
} from "@/services/schedulerDiagnostics";

interface SchedulerRunDrawerProps {
	task: SchedulerTaskStatus | null;
	onClose: () => void;
}

function formatBytes(value: number | null | undefined) {
	if (value == null) return "Not recorded";
	const units = ["B", "KiB", "MiB", "GiB"];
	let amount = value;
	let unit = 0;
	while (amount >= 1024 && unit < units.length - 1) {
		amount /= 1024;
		unit += 1;
	}
	return `${amount >= 10 || unit === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[unit]}`;
}

function displayStatus(status: string | null | undefined) {
	if (!status) return "Not Run";
	return status
		.split("_")
		.map((word) => word.charAt(0).toUpperCase() + word.slice(1))
		.join(" ");
}

function statusClassName(status: string | null | undefined) {
	if (status === "succeeded") {
		return "border-green-500/30 bg-green-500/15 text-green-700 dark:text-green-400";
	}
	if (["queued", "running", "enqueued", "waiting", "cancel_requested"].includes(status ?? "")) {
		return "border-amber-500/30 bg-amber-500/15 text-amber-700 dark:text-amber-400";
	}
	if (status === "failed" || status === "cancelled") {
		return "border-destructive/30 bg-destructive/10 text-destructive";
	}
	return "";
}

function statusBorderClassName(status: string | null | undefined) {
	if (status === "succeeded") return "border-l-green-500";
	if (["queued", "running", "enqueued", "waiting", "cancel_requested"].includes(status ?? "")) {
		return "border-l-amber-500";
	}
	if (status === "failed" || status === "cancelled") return "border-l-destructive";
	return "border-l-muted-foreground/40";
}

export function SchedulerRunDrawer({ task, onClose }: SchedulerRunDrawerProps) {
	const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
	const query = useQuery({
		queryKey: ["scheduler-task-history", task?.task_id],
		queryFn: ({ signal }) => getSchedulerTaskHistory(task!.task_id, { signal }),
		enabled: task != null,
		refetchInterval: task == null ? false : 10_000,
	});

	const selectedRun =
		query.data?.runs.find((run) => run.id === selectedRunId) ?? query.data?.runs[0];
	const selectedStatus = selectedRun?.platform_job_status ?? selectedRun?.status;
	const containerMemoryChange =
		selectedRun?.platform_job_memory_start_bytes == null ||
		selectedRun.platform_job_memory_peak_bytes == null
			? null
			: Math.max(
					0,
					selectedRun.platform_job_memory_peak_bytes -
						selectedRun.platform_job_memory_start_bytes,
				);
	const orderedLogs = selectedRun
		? [...selectedRun.logs].sort((left, right) => {
			const timestampDifference =
				new Date(left.created_at).getTime() - new Date(right.created_at).getTime();
			return timestampDifference || left.id - right.id;
		})
		: [];

	const handleCopyRunId = async () => {
		if (!selectedRun) return;
		if (await copyToClipboard(selectedRun.id)) {
			toast.success("Run ID copied");
		} else {
			toast.error("Failed to copy run ID");
		}
	};

	return (
		<Sheet open={task != null} onOpenChange={(open) => !open && onClose()}>
			<SheetContent side="right" className="w-full overflow-hidden p-0 sm:max-w-3xl">
				<SheetHeader className="border-b px-5 py-4 pr-14">
					<SheetTitle>{task?.name ?? "Scheduled Job"}</SheetTitle>
					<SheetDescription>Recent runs and the system logs published for each run.</SheetDescription>
				</SheetHeader>

				{query.isLoading ? (
					<div className="flex flex-1 items-center justify-center"><Loader2 className="h-7 w-7 animate-spin text-muted-foreground" /></div>
				) : query.error ? (
					<div className="p-5"><Alert variant="destructive"><AlertDescription>Failed to load recent runs.</AlertDescription></Alert></div>
				) : !query.data?.runs.length ? (
					<div className="flex flex-1 items-center justify-center p-8 text-center text-sm text-muted-foreground">This scheduled job has not run yet.</div>
				) : (
					<div className="grid min-h-0 flex-1 md:grid-cols-[200px_minmax(0,1fr)]">
						<div className="border-b p-3 md:border-b-0 md:border-r">
							<p className="px-2 pb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Recent runs</p>
							<div className="max-h-48 space-y-1 overflow-auto md:max-h-[calc(100vh-130px)]">
								{query.data.runs.map((run) => {
									const runStatus = run.platform_job_status ?? run.status;
									return (
										<Button
											key={run.id}
											variant={run.id === selectedRun?.id ? "secondary" : "ghost"}
											className={`h-auto w-full justify-start rounded-md border-l-4 px-3 py-2 text-left ${statusBorderClassName(runStatus)}`}
											onClick={() => setSelectedRunId(run.id)}
											aria-label={`View ${displayStatus(runStatus)} run ${run.id}`}
										>
											<span className="block text-xs font-medium">{format(new Date(run.started_at), "MMM d, h:mm:ss a")}</span>
										</Button>
									);
								})}
							</div>
						</div>

						{selectedRun && (
							<div className="min-h-0 overflow-auto p-5">
								<div className="flex flex-wrap items-center gap-2">
									<Badge variant="outline" className={statusClassName(selectedStatus)}>{displayStatus(selectedStatus)}</Badge>
									<Badge variant="outline" className={task?.execution_mode === "durable_job" ? "border-blue-500/30 bg-blue-500/15 text-blue-700 dark:text-blue-400" : "border-violet-500/30 bg-violet-500/15 text-violet-700 dark:text-violet-400"}>{task?.execution_mode === "durable_job" ? "Distributed Job" : "Leader Trigger"}</Badge>
								</div>

								<div className="mt-4 rounded-lg border bg-muted/20 px-3 py-2">
									<p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Run ID</p>
									<div className="mt-1 flex items-center gap-2">
										<p className="min-w-0 flex-1 break-all font-mono text-xs">{selectedRun.id}</p>
										<Button type="button" variant="ghost" size="icon-sm" onClick={handleCopyRunId} aria-label="Copy run ID" title="Copy run ID">
											<Copy className="h-3.5 w-3.5" />
										</Button>
									</div>
								</div>

								<div className="mt-4 grid gap-3 sm:grid-cols-2">
									<Card><CardContent className="flex items-center gap-3 py-4"><Clock3 className="h-4 w-4 text-muted-foreground" /><div><p className="text-xs text-muted-foreground">Duration</p><p className="font-medium">{selectedRun.duration_ms == null ? "In progress" : `${selectedRun.duration_ms} ms`}</p></div></CardContent></Card>
									<Card><CardContent className="flex items-center gap-3 py-4"><Cpu className="h-4 w-4 text-muted-foreground" /><div><p className="text-xs text-muted-foreground">Container Memory Change</p><p className="font-medium">{formatBytes(containerMemoryChange)}</p><p className="mt-1 text-xs text-muted-foreground">Shared scheduler cgroup</p></div></CardContent></Card>
									<Card className="sm:col-span-2"><CardContent className="flex items-start gap-3 py-4"><Server className="mt-0.5 h-4 w-4 text-muted-foreground" /><div className="min-w-0"><p className="text-xs text-muted-foreground">Trigger Leader</p><p className="mt-1 break-all font-mono text-xs font-medium">{selectedRun.leader_owner_id}</p></div></CardContent></Card>
								</div>

								<div className="mt-5 space-y-3 text-sm">
									{selectedRun.summary && <div><p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Summary</p><p className="mt-1">{selectedRun.summary}</p></div>}
									{selectedRun.error_message && <Alert variant="destructive"><AlertDescription>{selectedRun.error_message}</AlertDescription></Alert>}
									{selectedRun.platform_job_id && <div><p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Platform job</p><p className="mt-1 break-all font-mono text-xs">{selectedRun.platform_job_id}</p></div>}
								</div>

								<div className="mt-6">
									<h3 className="text-sm font-semibold">Published logs</h3>
									<div className="mt-3 space-y-2">
										{orderedLogs.map((log) => (
											<div key={log.id} className="rounded-md border p-3">
												<div className="flex items-center justify-between gap-3"><Badge variant={log.level === "error" ? "destructive" : log.level === "warning" ? "warning" : "outline"}>{displayStatus(log.level)}</Badge><span className="text-xs text-muted-foreground">{format(new Date(log.created_at), "MMM d, h:mm:ss a")}</span></div>
												<p className="mt-2">{log.message}</p>
												<p className="mt-1 font-mono text-xs text-muted-foreground">{log.code}</p>
											</div>
										))}
										{selectedRun.logs.length === 0 && <p className="rounded-md border border-dashed p-5 text-center text-sm text-muted-foreground">No system logs were published for this run.</p>}
									</div>
								</div>
							</div>
						)}
					</div>
				)}
			</SheetContent>
		</Sheet>
	);
}
