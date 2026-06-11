/**
 * Pure view helpers for the execution History feed: day grouping,
 * page-level rollup, and compact time formatting.
 */

import { parseBackendDate, formatDateShort } from "@/lib/utils";
import { executionOutcome } from "@/lib/execution-buckets";

/** The minimal execution shape the History list needs for grouping/rollup. */
export interface HistoryRunLike {
	execution_id: string;
	status: string;
	started_at?: string | null;
	completed_at?: string | null;
	scheduled_at?: string | null;
}

export interface DayGroup<T extends HistoryRunLike> {
	/** Stable key, e.g. "2026-06-11" (local calendar day) or "unknown". */
	key: string;
	/** Human label: "Today", "Yesterday", or a short date. */
	label: string;
	executions: T[];
}

/** The timestamp that anchors a run on the timeline. */
export function runAnchorDate(run: HistoryRunLike): Date | null {
	const iso = run.started_at ?? run.scheduled_at ?? run.completed_at;
	if (!iso) return null;
	const date = parseBackendDate(iso);
	return Number.isNaN(date.getTime()) ? null : date;
}

function localDayKey(date: Date): string {
	const y = date.getFullYear();
	const m = String(date.getMonth() + 1).padStart(2, "0");
	const d = String(date.getDate()).padStart(2, "0");
	return `${y}-${m}-${d}`;
}

function dayLabel(date: Date, now: Date): string {
	const key = localDayKey(date);
	if (key === localDayKey(now)) return "Today";
	const yesterday = new Date(now);
	yesterday.setDate(now.getDate() - 1);
	if (key === localDayKey(yesterday)) return "Yesterday";
	return formatDateShort(date);
}

/**
 * Group an execution list into calendar-day buckets keyed by each run's
 * anchor date. Grouping is non-consecutive (a Map per day) so interleaved
 * rows — e.g. Scheduled runs, whose NULL started_at sorts arbitrarily on
 * the server but whose anchor is a future scheduled_at — never produce
 * duplicate day headers or duplicate React keys. Groups are ordered
 * newest day first (matching the newest-first feed), with undated runs
 * last; order within a group is preserved as given.
 */
export function groupExecutionsByDay<T extends HistoryRunLike>(
	executions: T[],
	now: Date = new Date(),
): DayGroup<T>[] {
	const byKey = new Map<string, DayGroup<T>>();
	for (const run of executions) {
		const anchor = runAnchorDate(run);
		const key = anchor ? localDayKey(anchor) : "unknown";
		const existing = byKey.get(key);
		if (existing) {
			existing.executions.push(run);
		} else {
			byKey.set(key, {
				key,
				label: anchor ? dayLabel(anchor, now) : "Undated",
				executions: [run],
			});
		}
	}
	return Array.from(byKey.values()).sort((a, b) => {
		if (a.key === "unknown") return 1;
		if (b.key === "unknown") return -1;
		// Keys are "YYYY-MM-DD" — lexicographic compare is chronological.
		return b.key.localeCompare(a.key);
	});
}

export interface RunRollup {
	total: number;
	succeeded: number;
	failed: number;
	running: number;
	scheduled: number;
}

/** Page-level rollup for the header summary line. */
export function summarizeRuns(executions: HistoryRunLike[]): RunRollup {
	const rollup: RunRollup = {
		total: executions.length,
		succeeded: 0,
		failed: 0,
		running: 0,
		scheduled: 0,
	};
	for (const run of executions) {
		switch (executionOutcome(run.status)) {
			case "success":
				rollup.succeeded += 1;
				break;
			case "failed":
				rollup.failed += 1;
				break;
			case "running":
				rollup.running += 1;
				break;
			case "scheduled":
				rollup.scheduled += 1;
				break;
			// "cancelled" and unknown statuses count toward total only.
		}
	}
	return rollup;
}

/**
 * Compact single-line time for feed rows: "08:12 AM" for today,
 * "Jun 10, 08:12 AM" otherwise. The absolute full datetime belongs in a
 * title tooltip alongside this.
 */
export function formatRunTime(iso: string, now: Date = new Date()): string {
	const date = parseBackendDate(iso);
	if (Number.isNaN(date.getTime())) return iso;
	const sameDay = localDayKey(date) === localDayKey(now);
	const time = date.toLocaleTimeString(undefined, {
		hour: "2-digit",
		minute: "2-digit",
	});
	if (sameDay) return time;
	const day = date.toLocaleDateString(undefined, {
		month: "short",
		day: "numeric",
	});
	return `${day}, ${time}`;
}

/** Duration between two backend timestamps, compact ("3s", "1m 12s", "412ms"). */
export function formatRunDuration(
	startedAt: string | null | undefined,
	completedAt: string | null | undefined,
): string | null {
	if (!startedAt || !completedAt) return null;
	const start = parseBackendDate(startedAt).getTime();
	const end = parseBackendDate(completedAt).getTime();
	if (Number.isNaN(start) || Number.isNaN(end) || end < start) return null;
	const ms = end - start;
	if (ms < 1000) return `${Math.round(ms)}ms`;
	const totalSeconds = Math.round(ms / 1000);
	if (totalSeconds < 60) return `${totalSeconds}s`;
	const minutes = Math.floor(totalSeconds / 60);
	const seconds = totalSeconds % 60;
	if (minutes < 60) return seconds ? `${minutes}m ${seconds}s` : `${minutes}m`;
	const hours = Math.floor(minutes / 60);
	const remMinutes = minutes % 60;
	return remMinutes ? `${hours}h ${remMinutes}m` : `${hours}h`;
}
