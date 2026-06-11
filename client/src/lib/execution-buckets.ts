/**
 * Pure bucketing logic for the dashboard executions-over-time chart.
 *
 * Takes a fetched window of executions and folds them into zero-filled
 * time buckets (hourly for the 24h window, daily for 7d/30d) with
 * separate success / failed counts per bucket.
 */

import { parseBackendDate } from "@/lib/utils";

export type ChartWindow = "24h" | "7d" | "30d";

export interface BucketableExecution {
	status: string;
	started_at?: string | null;
}

export interface ExecutionBucket {
	/** Bucket start time (inclusive). */
	start: Date;
	/** Short axis label, e.g. "5 PM" (hourly) or "Jun 11" (daily). */
	label: string;
	success: number;
	failed: number;
}

type BucketUnit = "hour" | "day";

const WINDOW_SPECS: Record<ChartWindow, { buckets: number; unit: BucketUnit }> =
	{
		"24h": { buckets: 24, unit: "hour" },
		"7d": { buckets: 7, unit: "day" },
		"30d": { buckets: 30, unit: "day" },
	};

/**
 * Terminal states an operator reads as "it broke". Exported so the History
 * page's Failed tab can request exactly this set from the server — the
 * dashboard's "N failed" link and the Failed tab must agree on what counts.
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
 * The single status → outcome classifier shared by the dashboard chart,
 * the stat cards, and the History rollup. Decisions encoded here:
 * Stuck is a terminal failure (it broke); Cancelling is still running
 * (active until the worker confirms). Returns null for unknown statuses.
 */
export function executionOutcome(status: string): ExecutionOutcome | null {
	if (status === "Success") return "success";
	if (FAILURE_STATUSES.has(status)) return "failed";
	if (RUNNING_STATUSES.has(status)) return "running";
	if (status === "Scheduled") return "scheduled";
	if (status === "Cancelled") return "cancelled";
	return null;
}

function floorToUnit(date: Date, unit: BucketUnit): Date {
	const d = new Date(date);
	d.setMinutes(0, 0, 0);
	if (unit === "day") {
		d.setHours(0);
	}
	return d;
}

/** Step back `count` whole units using calendar arithmetic (DST-safe). */
function stepBack(date: Date, unit: BucketUnit, count: number): Date {
	const d = new Date(date);
	if (unit === "hour") {
		d.setHours(d.getHours() - count);
	} else {
		d.setDate(d.getDate() - count);
	}
	return d;
}

function bucketLabel(start: Date, unit: BucketUnit): string {
	return unit === "hour"
		? start.toLocaleTimeString("en-US", { hour: "numeric" })
		: start.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

/**
 * ISO timestamp for the start of the window — the `startDate` filter to
 * fetch with so the fetched executions exactly cover the buckets.
 */
export function windowStartIso(
	window: ChartWindow,
	now: Date = new Date(),
): string {
	const { buckets, unit } = WINDOW_SPECS[window];
	return stepBack(floorToUnit(now, unit), unit, buckets - 1).toISOString();
}

export interface OutcomeSummary {
	success: number;
	failed: number;
	total: number;
	/** Percentage 0-100, or null when there are no terminal runs. */
	successRate: number | null;
}

/**
 * Tally terminal outcomes across a fetched window — the headline numbers
 * (run count, success rate) that pair with the bucketed chart.
 */
export function summarizeOutcomes(
	executions: readonly BucketableExecution[],
): OutcomeSummary {
	let success = 0;
	let failed = 0;
	for (const execution of executions) {
		const outcome = executionOutcome(execution.status);
		if (outcome === "success") success += 1;
		else if (outcome === "failed") failed += 1;
	}
	const total = success + failed;
	return {
		success,
		failed,
		total,
		successRate: total > 0 ? (success / total) * 100 : null,
	};
}

/**
 * Fold executions into zero-filled buckets covering the window, oldest
 * first. The newest bucket is the current (partial) hour/day. Executions
 * before the window, without a start time, or in a non-terminal state are
 * skipped.
 */
export function bucketExecutions(
	executions: readonly BucketableExecution[],
	window: ChartWindow,
	now: Date = new Date(),
): ExecutionBucket[] {
	const { buckets: bucketCount, unit } = WINDOW_SPECS[window];
	const newest = floorToUnit(now, unit);

	const buckets: ExecutionBucket[] = [];
	for (let i = bucketCount - 1; i >= 0; i--) {
		const start = stepBack(newest, unit, i);
		buckets.push({
			start,
			label: bucketLabel(start, unit),
			success: 0,
			failed: 0,
		});
	}

	for (const execution of executions) {
		const outcome = executionOutcome(execution.status);
		if (
			(outcome !== "success" && outcome !== "failed") ||
			!execution.started_at
		) {
			continue;
		}

		const startedAt = parseBackendDate(execution.started_at).getTime();
		if (Number.isNaN(startedAt)) continue;

		// Walk from the newest bucket down: an execution belongs to the
		// latest bucket whose start is <= its start time.
		for (let i = buckets.length - 1; i >= 0; i--) {
			if (startedAt >= buckets[i].start.getTime()) {
				buckets[i][outcome] += 1;
				break;
			}
		}
		// Before the first bucket start → falls out of the loop, skipped.
	}

	return buckets;
}

/**
 * When the fetch was truncated (API row cap hit), the rows only cover
 * [oldest fetched row, now] — older buckets would render as fake zeros.
 * Drop the buckets that end before the oldest fetched row so the chart's
 * time domain matches the data actually fetched. With no datable rows the
 * buckets are returned unchanged.
 */
export function clampBucketsToData(
	buckets: ExecutionBucket[],
	executions: readonly BucketableExecution[],
): ExecutionBucket[] {
	let oldest: number | null = null;
	for (const execution of executions) {
		if (!execution.started_at) continue;
		const startedAt = parseBackendDate(execution.started_at).getTime();
		if (Number.isNaN(startedAt)) continue;
		if (oldest === null || startedAt < oldest) oldest = startedAt;
	}
	if (oldest === null) return buckets;

	// Keep the bucket containing the oldest row and everything after it.
	// A bucket's end is the next bucket's start; the last bucket is open.
	const firstCovered = buckets.findIndex((_, i) => {
		const next = buckets[i + 1];
		return next === undefined || next.start.getTime() > (oldest as number);
	});
	return firstCovered <= 0 ? buckets : buckets.slice(firstCovered);
}
