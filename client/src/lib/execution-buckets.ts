import { parseBackendDate } from "@/lib/utils";

export type ChartWindow = "24h" | "7d" | "30d";

/**
 * Terminal states an operator reads as "it broke". Exported so the History
 * page's Failed tab can request exactly the same set as the dashboard query.
 */
export const FAILURE_STATUSES: ReadonlySet<string> = new Set([
	"Failed",
	"Timeout",
	"Stuck",
	"CompletedWithErrors",
]);

/** Still-active states (Cancelling is in flight until the worker confirms). */
const RUNNING_STATUSES = new Set(["Running", "Pending", "Cancelling"]);

export type ExecutionOutcome =
	| "success"
	| "failed"
	| "running"
	| "scheduled"
	| "cancelled";

/**
 * The shared status classifier used by dashboard and History views. Stuck is
 * a terminal failure; Cancelling remains active until the worker confirms.
 */
export function executionOutcome(status: string): ExecutionOutcome | null {
	if (status === "Success") return "success";
	if (FAILURE_STATUSES.has(status)) return "failed";
	if (RUNNING_STATUSES.has(status)) return "running";
	if (status === "Scheduled") return "scheduled";
	if (status === "Cancelled") return "cancelled";
	return null;
}

export interface OutcomeSummary {
	success: number;
	failed: number;
	total: number;
	/** Percentage 0-100, or null when there are no terminal runs. */
	successRate: number | null;
}

export interface AggregatedExecutionBucket {
	start: string;
	success_count: number;
	failed_count: number;
}

export interface ExecutionBucket {
	start: Date;
	label: string;
	success: number;
	failed: number;
}

/**
 * Convert the server's complete, zero-filled aggregate into chart data. The
 * bucket count and range are preserved exactly; only display labels change.
 */
export function formatExecutionBuckets(
	buckets: readonly AggregatedExecutionBucket[],
	window: ChartWindow,
): ExecutionBucket[] {
	return buckets.map((bucket) => {
		const start = parseBackendDate(bucket.start);
		return {
			start,
			label:
				window === "24h"
					? start.toLocaleTimeString("en-US", { hour: "numeric" })
					: start.toLocaleDateString("en-US", {
							month: "short",
							day: "numeric",
						}),
			success: bucket.success_count,
			failed: bucket.failed_count,
		};
	});
}
