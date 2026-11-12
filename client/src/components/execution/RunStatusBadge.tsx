/**
 * The canonical execution status badge, with an explicit visual
 * hierarchy: the common case (success) renders quietly — color lives
 * only on the icon — while failures are the loudest elements on the
 * surface. Used by the History feed, the execution drawer, and the
 * details page so all three agree on what success and failure look like.
 */

import { AlertTriangle, CheckCircle2, Clock, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { parseBackendDate } from "@/lib/utils";

interface RunStatusBadgeProps {
	status: string;
	/** ISO datetime a Scheduled run will fire; shown via title tooltip. */
	scheduledAt?: string | null;
	/** Queue position for Pending executions (live stream metadata). */
	queuePosition?: number;
	/** Why a Pending execution is waiting (queued, memory_pressure). */
	waitReason?: string;
	/** Available memory in MB (memory_pressure waits). */
	availableMemoryMb?: number;
	/** Required memory in MB (memory_pressure waits). */
	requiredMemoryMb?: number;
}

export function RunStatusBadge({
	status,
	scheduledAt,
	queuePosition,
	waitReason,
	availableMemoryMb,
	requiredMemoryMb,
}: RunStatusBadgeProps) {
	const activityIndicator = (
		<span
			data-testid="run-status-activity-indicator"
			aria-hidden="true"
			className="h-2 w-2 shrink-0 rounded-full motion-safe:animate-pulse motion-reduce:animate-none"
			style={{ backgroundImage: "var(--bf-activity-gradient)" }}
		/>
	);

	const activeInfoStyle = {
		borderColor: "var(--bf-info)",
		backgroundColor: "var(--bf-info-soft)",
		color: "var(--bf-info)",
	};
	const activeWarningStyle = {
		borderColor: "var(--bf-warning)",
		backgroundColor: "var(--bf-warning-soft)",
		color: "var(--bf-warning)",
	};
	const activeDangerStyle = {
		borderColor: "var(--bf-danger)",
		backgroundColor: "var(--bf-danger-soft)",
		color: "var(--bf-danger)",
	};

	switch (status) {
		case "Success":
			return (
				<Badge
					variant="outline"
					className="min-w-24 gap-1 font-normal text-[color:var(--bf-success)]"
				>
					<CheckCircle2 className="h-3 w-3 text-[color:var(--bf-success)]" />
					Completed
				</Badge>
			);
		case "Failed":
			return (
				<Badge
					variant="outline"
					className="min-w-24 gap-1 font-normal"
					style={activeDangerStyle}
				>
					<XCircle className="h-3 w-3" />
					Failed
				</Badge>
			);
		case "Timeout":
			return (
				<Badge
					variant="outline"
					className="min-w-24 gap-1 font-normal"
					style={activeDangerStyle}
				>
					<Clock className="h-3 w-3" />
					Timed out
				</Badge>
			);
		case "CompletedWithErrors":
			return (
				<Badge
					variant="outline"
					className="min-w-24 gap-1 font-normal"
					style={activeWarningStyle}
				>
					<AlertTriangle className="h-3 w-3" />
					Completed with errors
				</Badge>
			);
		case "Running":
			return (
				<Badge
					variant="outline"
					className="relative min-w-24 gap-1 overflow-visible font-normal text-foreground"
				>
					<span
						data-testid="run-status-activity-indicator"
						aria-hidden="true"
						className="bf-running-orbit"
					/>
					Running
				</Badge>
			);
		case "Pending": {
			if (waitReason === "queued" && queuePosition) {
				return (
					<Badge
						variant="outline"
						className="min-w-24 gap-1 font-normal"
						style={activeInfoStyle}
					>
						{activityIndicator}
						Queued — position {queuePosition}
					</Badge>
				);
			}
			if (waitReason === "memory_pressure") {
				return (
					<Badge
						variant="outline"
						className="min-w-24 gap-1 font-normal"
						style={activeWarningStyle}
					>
						{activityIndicator}
						Heavy load ({availableMemoryMb ?? "?"}MB /{" "}
						{requiredMemoryMb ?? "?"}MB)
					</Badge>
				);
			}
			return (
				<Badge
					variant="outline"
					className="min-w-24 gap-1 font-normal"
					style={activeInfoStyle}
				>
					{activityIndicator}
					Pending
				</Badge>
			);
		}
		case "Scheduled": {
			// new Date() never throws — an unparseable string yields an
			// Invalid Date, so guard with NaN and fall back to the raw value.
			let title: string | undefined;
			if (scheduledAt) {
				const fireAt = parseBackendDate(scheduledAt);
				title = Number.isNaN(fireAt.getTime())
					? `Scheduled for ${scheduledAt}`
					: `Scheduled for ${fireAt.toLocaleString()}`;
			}
			return (
				<Badge
					variant="outline"
					className="min-w-24 gap-1 font-normal"
					style={activeInfoStyle}
					{...(title ? { title } : {})}
				>
					<Clock className="h-3 w-3" />
					Scheduled
				</Badge>
			);
		}
		case "Cancelling":
			return (
				<Badge
					variant="outline"
					className="min-w-24 gap-1 font-normal"
					style={activeWarningStyle}
				>
					{activityIndicator}
					Cancelling
				</Badge>
			);
		case "Cancelled":
			return (
				<Badge
					variant="outline"
					className="min-w-24 gap-1 font-normal text-muted-foreground"
				>
					<XCircle className="h-3 w-3" />
					Cancelled
				</Badge>
			);
		default:
			return (
				<Badge variant="outline" className="min-w-24 font-normal">
					{status}
				</Badge>
			);
	}
}
