import { useState } from "react";
import { RotateCw } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import {
	AlertDialog,
	AlertDialogAction,
	AlertDialogCancel,
	AlertDialogContent,
	AlertDialogDescription,
	AlertDialogFooter,
	AlertDialogHeader,
	AlertDialogTitle,
	AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { ProcessInfo } from "@/services/workers";
import { useRecycleAllProcesses } from "@/services/workers";
import type { ExecutionRowData } from "./ExecutionRow";

function formatUptime(seconds: number): string {
	if (seconds < 60) return `${Math.floor(seconds)}s`;
	if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
	if (seconds < 86400) {
		const hours = Math.floor(seconds / 3600);
		const minutes = Math.floor((seconds % 3600) / 60);
		return minutes > 0 ? `${hours}h ${minutes}m` : `${hours}h`;
	}
	const days = Math.floor(seconds / 86400);
	const hours = Math.floor((seconds % 86400) / 3600);
	return hours > 0 ? `${days}d ${hours}h` : `${days}d`;
}

const stateVariant: Record<string, "secondary" | "default" | "destructive"> = {
	idle: "secondary",
	busy: "default",
	killed: "destructive",
};

function getProcessSurfaceClass(state: ProcessInfo["state"]) {
	if (state === "busy") {
		return "border-[var(--bf-warning)]/20 bg-[var(--bf-warning)]/5";
	}

	if (state === "killed") {
		return "border-destructive/20 bg-destructive/5";
	}

	return "border-border/70 bg-card";
}

function getMemoryPercent(memoryMb: number, maxMem: number) {
	return maxMem > 0 ? Math.min((memoryMb / maxMem) * 100, 100) : 0;
}

interface ForkTableProps {
	workerId: string;
	processes: ProcessInfo[];
	/** Redis process controls are safe only for a ready canonical worker. */
	actionsEnabled?: boolean;
	/** Map of process_id -> execution info for busy processes */
	executions?: Map<string, ExecutionRowData>;
	/** Max memory for this container in bytes (for progress bar scale) */
	containerMemoryMax?: number;
}

export function ForkTable({
	workerId,
	processes,
	actionsEnabled = true,
	executions,
	containerMemoryMax,
}: ForkTableProps) {
	const recycleAll = useRecycleAllProcesses();
	const [recycleDialogOpen, setRecycleDialogOpen] = useState(false);
	const [recycleState, setRecycleState] = useState<"idle" | "pending" | "error">("idle");
	const [recycleError, setRecycleError] = useState<string | null>(null);

	const maxMem =
		containerMemoryMax && containerMemoryMax > 0
			? containerMemoryMax / (1024 * 1024)
			: processes.length > 0
				? Math.max(...processes.map((process) => process.memory_mb), 1)
				: 1;

	const handleRecycleAll = () => {
		if (recycleState === "pending") return;

		setRecycleError(null);
		setRecycleState("pending");

		recycleAll.mutate(
			{ workerId, reason: "manual_recycle" },
			{
				onSuccess: () => {
					toast.success("Recycle request sent");
					setRecycleState("idle");
					setRecycleDialogOpen(false);
				},
				onError: (err) => {
					setRecycleState("error");
					setRecycleError(err.message);
					toast.error(`Recycle failed: ${err.message}`);
				},
			},
		);
	};

	const recyclePending = recycleState === "pending";

	return (
		<div className="@container space-y-3">
			<div className="@3xl:hidden">
				<ul
					aria-label="Worker processes"
					className="space-y-3"
				>
					{processes.map((proc) => {
						const execution = executions?.get(proc.process_id);
						const memPct = getMemoryPercent(proc.memory_mb, maxMem);

						return (
							<li
								key={proc.process_id}
								className={cn(
									"min-w-0 rounded-[var(--bf-radius-surface)] border p-[var(--bf-surface-pad)] shadow-none",
									getProcessSurfaceClass(proc.state),
								)}
							>
								<div className="flex items-start justify-between gap-3">
									<div className="min-w-0">
										<p className="text-xs text-muted-foreground">
											PID
										</p>
										<p className="font-mono text-sm font-medium text-foreground">
											{proc.pid}
										</p>
									</div>
									<Badge
										variant={
											stateVariant[proc.state] ?? "secondary"
										}
										className="h-6 rounded-[var(--bf-radius-control)] px-2 text-[10px]"
									>
										{proc.state}
									</Badge>
								</div>

								<div className="mt-4 space-y-4">
									<div className="space-y-1.5">
										<div className="flex items-center justify-between gap-3 text-xs text-muted-foreground">
											<span>Memory</span>
											<span className="tabular-nums">
												{proc.memory_mb.toFixed(0)} MB
											</span>
										</div>
										<Progress
											value={memPct}
											className="h-2"
											aria-label={`Memory usage for process ${proc.pid}`}
										/>
									</div>

									<dl className="grid grid-cols-2 gap-3 text-sm">
										<div className="min-w-0">
											<dt className="text-xs text-muted-foreground">
												Jobs
											</dt>
											<dd className="mt-1 font-medium text-foreground">
												{proc.executions_completed}
											</dd>
										</div>
										<div className="min-w-0 text-right">
											<dt className="text-xs text-muted-foreground">
												Uptime
											</dt>
											<dd className="mt-1 font-medium text-foreground tabular-nums">
												{formatUptime(proc.uptime_seconds)}
											</dd>
										</div>
									</dl>

									<div className="min-w-0">
										<div className="text-xs text-muted-foreground">
											Execution
										</div>
										{execution ? (
											<div className="mt-2 space-y-1">
												<p className="min-w-0 break-words [overflow-wrap:anywhere] text-sm font-medium text-foreground">
													{execution.workflow_name}
												</p>
												{execution.elapsed_seconds > 0 && (
													<p className="text-xs text-muted-foreground">
														Running for{" "}
														{formatUptime(
															execution.elapsed_seconds,
														)}
													</p>
												)}
											</div>
										) : (
											<p className="mt-2 text-sm text-muted-foreground">
												No active execution
											</p>
										)}
									</div>
								</div>
							</li>
						);
					})}
				</ul>
			</div>

			<div className="hidden overflow-hidden rounded-[var(--bf-radius-surface)] border border-border/70 bg-card shadow-none @3xl:block">
				<Table>
					<TableHeader className="bg-muted/60">
						<TableRow className="text-xs">
							<TableHead className="w-[80px]">PID</TableHead>
							<TableHead className="w-[70px]">State</TableHead>
							<TableHead className="w-[140px]">Memory</TableHead>
							<TableHead className="w-[70px]">Jobs</TableHead>
							<TableHead>Execution</TableHead>
							<TableHead className="w-[80px]">Uptime</TableHead>
						</TableRow>
					</TableHeader>
					<TableBody>
						{processes.map((proc) => {
							const execution = executions?.get(proc.process_id);
							const memPct = getMemoryPercent(proc.memory_mb, maxMem);

							return (
								<TableRow
									key={proc.process_id}
									className={cn(
										proc.state === "busy" &&
											"bg-[var(--bf-warning)]/5",
									)}
								>
									<TableCell className="font-mono text-xs">
										{proc.pid}
									</TableCell>
									<TableCell>
										<Badge
											variant={
												stateVariant[proc.state] ?? "secondary"
											}
											className="text-[10px]"
										>
											{proc.state}
										</Badge>
									</TableCell>
									<TableCell>
										<div className="flex items-center gap-2">
											<Progress
												value={memPct}
												className="h-2 w-16"
												aria-label={`Memory usage for process ${proc.pid}`}
											/>
											<span className="text-xs text-muted-foreground">
												{proc.memory_mb.toFixed(0)} MB
											</span>
										</div>
									</TableCell>
									<TableCell className="text-xs">
										{proc.executions_completed}
									</TableCell>
									<TableCell className="text-xs">
										{execution ? (
											<div className="min-w-0">
												<span className="block min-w-0 break-words [overflow-wrap:anywhere] text-foreground">
													{execution.workflow_name}
												</span>
												{execution.elapsed_seconds > 0 && (
													<span className="block text-muted-foreground">
														{formatUptime(
															execution.elapsed_seconds,
														)}
													</span>
												)}
											</div>
										) : (
											<span className="text-muted-foreground">
												&mdash;
											</span>
										)}
									</TableCell>
									<TableCell className="text-xs text-muted-foreground">
										{formatUptime(proc.uptime_seconds)}
									</TableCell>
								</TableRow>
							);
						})}
					</TableBody>
				</Table>
			</div>

			<div className="flex justify-end">
				<AlertDialog
					open={recycleDialogOpen}
					onOpenChange={(nextOpen) => {
						if (recyclePending) return;
						setRecycleDialogOpen(nextOpen);
						if (!nextOpen) {
							setRecycleError(null);
							setRecycleState("idle");
						}
					}}
				>
					<AlertDialogTrigger asChild>
						<Button
							type="button"
							variant="outline"
							size="sm"
							className="h-11 min-h-11 w-full border-destructive/20 text-destructive hover:bg-destructive/10 hover:text-destructive sm:w-auto"
							disabled={!actionsEnabled}
						>
							<RotateCw className="mr-2 h-4 w-4" />
							Recycle All
						</Button>
					</AlertDialogTrigger>
					<AlertDialogContent>
						<AlertDialogHeader>
							<AlertDialogTitle>Recycle all processes?</AlertDialogTitle>
							<AlertDialogDescription>
								This will gracefully restart all {processes.length} fork(s) in{" "}
								<span className="[overflow-wrap:anywhere]">
									{workerId}
								</span>
								. Running executions will complete before their process is
								recycled.
							</AlertDialogDescription>
						</AlertDialogHeader>
						{recycleError && (
							<div
								role="alert"
								className="rounded-[var(--bf-radius-surface)] border border-destructive/20 bg-destructive/5 p-3 text-sm text-destructive"
							>
								{recycleError}
							</div>
						)}
						<AlertDialogFooter>
							<AlertDialogCancel
								disabled={recyclePending}
								className="min-h-11 lg:min-h-11"
							>
								Cancel
							</AlertDialogCancel>
							<AlertDialogAction
								variant="destructive"
								disabled={recyclePending}
								className="min-h-11 lg:min-h-11"
								onClick={(event) => {
									event.preventDefault();
									handleRecycleAll();
								}}
							>
								{recyclePending
									? "Recycling..."
									: recycleError
										? "Retry recycle"
										: "Recycle All"}
							</AlertDialogAction>
						</AlertDialogFooter>
					</AlertDialogContent>
				</AlertDialog>
			</div>
		</div>
	);
}
