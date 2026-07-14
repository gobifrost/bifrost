import { User, Building2, Clock, Timer } from "lucide-react";
import { RunStatusBadge } from "./RunStatusBadge";
import { formatDate, formatRelativeTime } from "@/lib/utils";
import type { components } from "@/lib/v1";

type ExecutionStatus =
	| components["schemas"]["ExecutionStatus"]
	| "Cancelling"
	| "Cancelled";

interface ExecutionMetadataBarProps {
	workflowName: string;
	status: ExecutionStatus;
	executedByName?: string | null;
	orgName?: string | null;
	startedAt?: string | null;
	durationMs?: number | null;
	totalDurationMs?: number | null;
	queuePosition?: number;
	waitReason?: string;
	availableMemoryMb?: number;
	requiredMemoryMb?: number;
}

function formatDuration(ms: number): string {
	if (ms < 1000) return `${ms}ms`;
	if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
	const minutes = Math.floor(ms / 60000);
	const seconds = ((ms % 60000) / 1000).toFixed(0);
	return `${minutes}m ${seconds}s`;
}

export function ExecutionMetadataBar({
	workflowName,
	status,
	executedByName,
	orgName,
	startedAt,
	durationMs,
	totalDurationMs,
	queuePosition,
	waitReason,
	availableMemoryMb,
	requiredMemoryMb,
}: ExecutionMetadataBarProps) {
	return (
		<div className="space-y-1.5">
			{/* Workflow name + status */}
			<div className="flex items-center gap-2 min-w-0">
				<h3 className="font-mono text-base font-semibold truncate">
					{workflowName}
				</h3>
				<RunStatusBadge
					status={status}
					queuePosition={queuePosition}
					waitReason={waitReason}
					availableMemoryMb={availableMemoryMb}
					requiredMemoryMb={requiredMemoryMb}
				/>
			</div>
			{/* Inline metadata */}
			<div className="flex items-center gap-3 text-xs text-muted-foreground flex-wrap">
				<span className="flex items-center gap-1">
					<User className="h-3 w-3" />
					{executedByName || "Unknown"}
				</span>
				<span className="flex items-center gap-1">
					<Building2 className="h-3 w-3" />
					{orgName || "Global"}
				</span>
				<span
					className="flex items-center gap-1"
					{...(startedAt ? { title: formatDate(startedAt) } : {})}
				>
					<Clock className="h-3 w-3" />
					{startedAt ? formatRelativeTime(startedAt) : "Not started"}
				</span>
				{totalDurationMs != null && (
					<span
						className="flex items-center gap-1"
						title="Total platform time from worker start through persisted completion"
					>
						<Timer className="h-3 w-3" />
						<span>Total</span>
						<span>{formatDuration(totalDurationMs)}</span>
					</span>
				)}
				<span
					className="flex items-center gap-1"
					title="Time spent executing workflow code"
				>
					<Timer className="h-3 w-3" />
					<span>Workflow</span>
					{durationMs != null
						? <span>{formatDuration(durationMs)}</span>
						: "In progress..."}
				</span>
			</div>
		</div>
	);
}
