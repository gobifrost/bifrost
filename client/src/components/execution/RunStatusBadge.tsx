/**
 * The canonical execution status badge, with an explicit visual
 * hierarchy: the common case (success) renders quietly — color lives
 * only on the icon — while failures are the loudest elements on the
 * surface. Used by the History feed, the execution drawer, and the
 * details page so all three agree on what success and failure look like.
 */

import {
	AlertTriangle,
	CheckCircle2,
	Clock,
	Loader2,
	XCircle,
} from "lucide-react";
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
	switch (status) {
		case "Success":
			return (
				<Badge
					variant="outline"
					className="gap-1 font-normal text-muted-foreground"
				>
					<CheckCircle2 className="h-3 w-3 text-green-500" />
					Completed
				</Badge>
			);
		case "Failed":
			return (
				<Badge variant="destructive" className="gap-1">
					<XCircle className="h-3 w-3" />
					Failed
				</Badge>
			);
		case "Timeout":
			return (
				<Badge variant="destructive" className="gap-1">
					<Clock className="h-3 w-3" />
					Timed out
				</Badge>
			);
		case "CompletedWithErrors":
			return (
				<Badge
					variant="outline"
					className="gap-1 border-yellow-600/50 font-normal text-yellow-600 dark:text-yellow-500"
				>
					<AlertTriangle className="h-3 w-3" />
					Completed with errors
				</Badge>
			);
		case "Running":
			return (
				<Badge variant="secondary" className="gap-1">
					<Loader2 className="h-3 w-3 animate-spin" />
					Running
				</Badge>
			);
		case "Pending": {
			if (waitReason === "queued" && queuePosition) {
				return (
					<Badge
						variant="outline"
						className="gap-1 font-normal text-muted-foreground"
					>
						<Clock className="h-3 w-3" />
						Queued — position {queuePosition}
					</Badge>
				);
			}
			if (waitReason === "memory_pressure") {
				return (
					<Badge
						variant="outline"
						className="gap-1 border-orange-500/50 font-normal text-orange-600 dark:text-orange-400"
					>
						<Loader2 className="h-3 w-3 animate-spin" />
						Heavy load ({availableMemoryMb ?? "?"}MB /{" "}
						{requiredMemoryMb ?? "?"}MB)
					</Badge>
				);
			}
			return (
				<Badge
					variant="outline"
					className="gap-1 font-normal text-muted-foreground"
				>
					<Clock className="h-3 w-3" />
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
					className="gap-1 border-sky-500/50 font-normal text-sky-700 dark:text-sky-300"
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
					className="gap-1 border-orange-500/50 font-normal text-orange-600 dark:text-orange-400"
				>
					<Loader2 className="h-3 w-3 animate-spin" />
					Cancelling
				</Badge>
			);
		case "Cancelled":
			return (
				<Badge
					variant="outline"
					className="gap-1 font-normal text-muted-foreground"
				>
					<XCircle className="h-3 w-3" />
					Cancelled
				</Badge>
			);
		default:
			return (
				<Badge variant="outline" className="font-normal">
					{status}
				</Badge>
			);
	}
}
