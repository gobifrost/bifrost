/**
 * Tests for the History feed view helpers: day grouping, page rollup,
 * and compact time/duration formatting.
 *
 * Note: backend timestamps come without a "Z" suffix and are parsed as
 * UTC by parseBackendDate, so fixtures use explicit "Z" ISO strings and
 * assertions avoid depending on the host timezone where possible.
 */

import { describe, it, expect } from "vitest";
import {
	groupExecutionsByDay,
	summarizeRuns,
	formatRunTime,
	formatRunDuration,
	runAnchorDate,
} from "./historyView";

function run(overrides: Record<string, unknown> = {}) {
	return {
		execution_id: crypto.randomUUID(),
		status: "Success",
		started_at: "2026-06-11T10:00:00Z",
		completed_at: "2026-06-11T10:00:05Z",
		scheduled_at: null,
		...overrides,
	};
}

describe("runAnchorDate", () => {
	it("prefers started_at, then scheduled_at, then completed_at", () => {
		expect(
			runAnchorDate(
				run({ started_at: "2026-06-11T10:00:00Z" }),
			)?.toISOString(),
		).toBe("2026-06-11T10:00:00.000Z");
		expect(
			runAnchorDate(
				run({
					started_at: null,
					scheduled_at: "2026-06-12T08:00:00Z",
					completed_at: null,
				}),
			)?.toISOString(),
		).toBe("2026-06-12T08:00:00.000Z");
		expect(
			runAnchorDate(
				run({
					started_at: null,
					scheduled_at: null,
					completed_at: "2026-06-10T08:00:00Z",
				}),
			)?.toISOString(),
		).toBe("2026-06-10T08:00:00.000Z");
	});

	it("returns null when no timestamp exists", () => {
		expect(
			runAnchorDate(
				run({
					started_at: null,
					scheduled_at: null,
					completed_at: null,
				}),
			),
		).toBeNull();
	});
});

describe("groupExecutionsByDay", () => {
	it("groups consecutive same-day runs and labels Today/Yesterday", () => {
		// Use a fixed "now" at local noon so day boundaries are stable.
		const now = new Date(2026, 5, 11, 12, 0, 0); // local Jun 11, 2026
		const today = new Date(2026, 5, 11, 9, 0, 0);
		const yesterday = new Date(2026, 5, 10, 22, 0, 0);
		const older = new Date(2026, 5, 1, 8, 0, 0);

		const rows = [
			run({ execution_id: "a", started_at: today.toISOString() }),
			run({ execution_id: "b", started_at: today.toISOString() }),
			run({ execution_id: "c", started_at: yesterday.toISOString() }),
			run({ execution_id: "d", started_at: older.toISOString() }),
		];

		const groups = groupExecutionsByDay(rows, now);
		expect(groups).toHaveLength(3);
		expect(groups[0].label).toBe("Today");
		expect(groups[0].executions.map((r) => r.execution_id)).toEqual([
			"a",
			"b",
		]);
		expect(groups[1].label).toBe("Yesterday");
		expect(groups[1].executions.map((r) => r.execution_id)).toEqual(["c"]);
		// Older days get a short date label.
		expect(groups[2].label).toMatch(/Jun/);
		expect(groups[2].executions.map((r) => r.execution_id)).toEqual(["d"]);
	});

	it("preserves the given (server) order without re-sorting", () => {
		const now = new Date(2026, 5, 11, 12, 0, 0);
		const rows = [
			run({
				execution_id: "first",
				started_at: new Date(2026, 5, 11, 11, 0, 0).toISOString(),
			}),
			run({
				execution_id: "second",
				started_at: new Date(2026, 5, 11, 9, 0, 0).toISOString(),
			}),
		];
		const groups = groupExecutionsByDay(rows, now);
		expect(groups[0].executions.map((r) => r.execution_id)).toEqual([
			"first",
			"second",
		]);
	});

	it("groups non-consecutive same-day rows under one header (interleaved scheduled rows)", () => {
		// Scheduled rows have started_at NULL; Postgres NULLS FIRST puts
		// them before everything in arbitrary order, anchored to future
		// scheduled_at dates. Day headers must still be unique.
		const now = new Date(2026, 5, 11, 12, 0, 0);
		const rows = [
			run({
				execution_id: "sched-13",
				status: "Scheduled",
				started_at: null,
				completed_at: null,
				scheduled_at: new Date(2026, 5, 13, 9, 0, 0).toISOString(),
			}),
			run({
				execution_id: "sched-12-a",
				status: "Scheduled",
				started_at: null,
				completed_at: null,
				scheduled_at: new Date(2026, 5, 12, 8, 0, 0).toISOString(),
			}),
			run({
				execution_id: "sched-13-b",
				status: "Scheduled",
				started_at: null,
				completed_at: null,
				scheduled_at: new Date(2026, 5, 13, 17, 0, 0).toISOString(),
			}),
			run({
				execution_id: "sched-12-b",
				status: "Scheduled",
				started_at: null,
				completed_at: null,
				scheduled_at: new Date(2026, 5, 12, 16, 0, 0).toISOString(),
			}),
			run({
				execution_id: "ran-today",
				started_at: new Date(2026, 5, 11, 9, 0, 0).toISOString(),
			}),
		];

		const groups = groupExecutionsByDay(rows, now);

		// One group per day — no duplicate headers, no duplicate keys.
		const keys = groups.map((g) => g.key);
		expect(new Set(keys).size).toBe(keys.length);
		expect(groups).toHaveLength(3);

		// Newest day first; given order preserved within each group.
		expect(groups[0].executions.map((r) => r.execution_id)).toEqual([
			"sched-13",
			"sched-13-b",
		]);
		expect(groups[1].executions.map((r) => r.execution_id)).toEqual([
			"sched-12-a",
			"sched-12-b",
		]);
		expect(groups[2].label).toBe("Today");
		expect(groups[2].executions.map((r) => r.execution_id)).toEqual([
			"ran-today",
		]);
	});

	it("buckets runs without any timestamp under Undated", () => {
		const groups = groupExecutionsByDay([
			run({
				started_at: null,
				scheduled_at: null,
				completed_at: null,
			}),
		]);
		expect(groups).toHaveLength(1);
		expect(groups[0].label).toBe("Undated");
	});

	it("returns no groups for an empty list", () => {
		expect(groupExecutionsByDay([])).toEqual([]);
	});
});

describe("summarizeRuns", () => {
	it("rolls statuses up into operator-facing buckets via the shared classifier", () => {
		const rollup = summarizeRuns([
			run({ status: "Success" }),
			run({ status: "Success" }),
			run({ status: "Failed" }),
			run({ status: "Timeout" }),
			run({ status: "Stuck" }),
			run({ status: "CompletedWithErrors" }),
			run({ status: "Running" }),
			run({ status: "Pending" }),
			run({ status: "Cancelling" }),
			run({ status: "Scheduled" }),
			run({ status: "Cancelled" }),
		]);
		expect(rollup).toEqual({
			total: 11,
			succeeded: 2,
			// Failed + Timeout + Stuck + CompletedWithErrors — same failure
			// set the dashboard counts; the rollup buckets sum to total
			// minus Cancelled.
			failed: 4,
			running: 3, // Running + Pending + Cancelling
			scheduled: 1,
		});
	});
});

describe("formatRunTime", () => {
	it("renders time-only for same-day timestamps", () => {
		const now = new Date(2026, 5, 11, 12, 0, 0);
		const sameDay = new Date(2026, 5, 11, 8, 12, 0).toISOString();
		const result = formatRunTime(sameDay, now);
		expect(result).toMatch(/8:12|08:12/);
		expect(result).not.toMatch(/Jun/);
	});

	it("includes a short date for other days", () => {
		const now = new Date(2026, 5, 11, 12, 0, 0);
		const otherDay = new Date(2026, 5, 9, 8, 12, 0).toISOString();
		const result = formatRunTime(otherDay, now);
		expect(result).toMatch(/Jun/);
	});
});

describe("formatRunDuration", () => {
	it("formats sub-second, seconds, minutes, and hours compactly", () => {
		expect(
			formatRunDuration(
				"2026-06-11T10:00:00.000Z",
				"2026-06-11T10:00:00.412Z",
			),
		).toBe("412ms");
		expect(
			formatRunDuration("2026-06-11T10:00:00Z", "2026-06-11T10:00:03Z"),
		).toBe("3s");
		expect(
			formatRunDuration("2026-06-11T10:00:00Z", "2026-06-11T10:01:12Z"),
		).toBe("1m 12s");
		expect(
			formatRunDuration("2026-06-11T10:00:00Z", "2026-06-11T12:30:00Z"),
		).toBe("2h 30m");
	});

	it("returns null for missing or inverted timestamps", () => {
		expect(formatRunDuration(null, "2026-06-11T10:00:00Z")).toBeNull();
		expect(formatRunDuration("2026-06-11T10:00:00Z", null)).toBeNull();
		expect(
			formatRunDuration("2026-06-11T10:00:05Z", "2026-06-11T10:00:00Z"),
		).toBeNull();
	});
});
