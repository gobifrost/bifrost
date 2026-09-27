import type { ReactNode } from "react";
import { Eye } from "lucide-react";
import { RunStatusBadge } from "@/components/execution";
import { Button } from "@/components/ui/button";
import { Link, useNavigate } from "react-router-dom";
import type { components } from "@/lib/v1";
import { formatRunDuration, formatRunTime } from "./historyView";
import {
	createExecutionHistoryOriginState,
	type ExecutionHistoryOrigin,
} from "../navigation";

type ExecutionSummary = components["schemas"]["ExecutionSummary"];
interface ExecutionRecordProps {
	execution: ExecutionSummary;
	status: ExecutionSummary["status"] | "Cancelled";
	organizationName?: string;
	onPreview: () => void;
	actions: ReactNode;
	getOrigin: () => ExecutionHistoryOrigin;
}

export function ExecutionRecord({
	execution,
	status,
	organizationName,
	onPreview,
	actions,
	getOrigin,
}: ExecutionRecordProps) {
	const navigate = useNavigate();
	const anchorIso =
		execution.started_at ??
		execution.scheduled_at ??
		execution.completed_at;
	return (
		<li data-testid="execution-record" className="min-w-0 space-y-3 p-4">
			<div className="flex items-start justify-between gap-3">
				<RunStatusBadge
					status={status}
					scheduledAt={execution.scheduled_at}
				/>
				<Button
					variant="outline"
					size="sm"
					className="min-h-9 shrink-0 gap-1.5 px-2"
					onClick={onPreview}
					aria-label={`Preview execution ${execution.workflow_name}`}
				>
					<Eye className="h-3.5 w-3.5" />
					Preview
				</Button>
			</div>
			<Link
				to={`/history/${execution.execution_id}`}
				onClick={(event) => {
					if (
						event.defaultPrevented ||
						event.button !== 0 ||
						event.metaKey ||
						event.ctrlKey ||
						event.shiftKey ||
						event.altKey
					) {
						return;
					}
					event.preventDefault();
					navigate(`/history/${execution.execution_id}`, {
						state: createExecutionHistoryOriginState(getOrigin()),
					});
				}}
				className="flex min-h-11 items-center rounded-[var(--bf-radius-control)] font-mono text-sm font-medium [overflow-wrap:anywhere] hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
			>
				{execution.workflow_name}
			</Link>
			<dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-2 text-xs">
				{organizationName !== undefined && (
					<>
						<dt className="text-muted-foreground">Organization</dt>
						<dd className="[overflow-wrap:anywhere]">
							{organizationName}
						</dd>
					</>
				)}
				<dt className="text-muted-foreground">Run by</dt>
				<dd className="[overflow-wrap:anywhere]">
					{execution.executed_by_name}
				</dd>
				<dt className="text-muted-foreground">
					{status === "Scheduled" ? "Scheduled" : "Started"}
				</dt>
				<dd>{anchorIso ? formatRunTime(anchorIso) : "Not started"}</dd>
				<dt className="text-muted-foreground">Duration</dt>
				<dd className="tabular-nums">
					{formatRunDuration(
						execution.started_at,
						execution.completed_at,
					) ?? "Not available"}
				</dd>
			</dl>
			{execution.error_message &&
				["Failed", "Timeout", "CompletedWithErrors"].includes(
					status,
				) && (
					<p className="text-sm text-destructive [overflow-wrap:anywhere]">
						{execution.error_message}
					</p>
				)}
			{actions}
		</li>
	);
}
