import { describe, expect, it } from "vitest";
import {
	bucketExecutions,
	summarizeOutcomes,
	windowStartIso,
	type BucketableExecution,
} from "./execution-buckets";

// Fixed "now" mid-day so hour/day boundaries are unambiguous.
const NOW = new Date("2026-06-11T17:45:30");

function exec(
	status: string,
	startedAt: string | null | undefined,
): BucketableExecution {
	return { status, started_at: startedAt };
}

describe("windowStartIso", () => {
	it("returns the start of the oldest hourly bucket for 24h", () => {
		// Newest bucket starts at 17:00; 23 hours earlier is yesterday 18:00.
		expect(windowStartIso("24h", NOW)).toBe(
			new Date("2026-06-10T18:00:00").toISOString(),
		);
	});

	it("returns local midnight 6 days back for 7d", () => {
		expect(windowStartIso("7d", NOW)).toBe(
			new Date("2026-06-05T00:00:00").toISOString(),
		);
	});

	it("returns local midnight 29 days back for 30d", () => {
		expect(windowStartIso("30d", NOW)).toBe(
			new Date("2026-05-13T00:00:00").toISOString(),
		);
	});
});

describe("bucketExecutions", () => {
	it("zero-fills the full window when there are no executions", () => {
		const buckets = bucketExecutions([], "7d", NOW);
		expect(buckets).toHaveLength(7);
		expect(buckets.every((b) => b.success === 0 && b.failed === 0)).toBe(
			true,
		);
	});

	it("produces 24 hourly buckets for the 24h window, oldest first", () => {
		const buckets = bucketExecutions([], "24h", NOW);
		expect(buckets).toHaveLength(24);
		expect(buckets[0].start.getTime()).toBeLessThan(
			buckets[23].start.getTime(),
		);
		// Newest bucket is the current (partial) hour.
		expect(buckets[23].start).toEqual(new Date("2026-06-11T17:00:00"));
	});

	it("produces 30 daily buckets for the 30d window", () => {
		expect(bucketExecutions([], "30d", NOW)).toHaveLength(30);
	});

	it("counts successes and failures in the right daily bucket", () => {
		const buckets = bucketExecutions(
			[
				exec("Success", "2026-06-11T09:00:00"),
				exec("Success", "2026-06-11T17:30:00"),
				exec("Failed", "2026-06-11T17:35:00"),
				exec("Success", "2026-06-09T08:00:00"),
			],
			"7d",
			NOW,
		);
		const today = buckets[6];
		expect(today.success).toBe(2);
		expect(today.failed).toBe(1);
		const twoDaysAgo = buckets[4];
		expect(twoDaysAgo.success).toBe(1);
		expect(twoDaysAgo.failed).toBe(0);
	});

	it("buckets hourly within the 24h window", () => {
		const buckets = bucketExecutions(
			[
				exec("Success", "2026-06-11T17:05:00"),
				exec("Failed", "2026-06-11T16:59:00"),
			],
			"24h",
			NOW,
		);
		expect(buckets[23].success).toBe(1); // 17:00 bucket
		expect(buckets[23].failed).toBe(0);
		expect(buckets[22].failed).toBe(1); // 16:00 bucket
	});

	it("treats Timeout, Stuck and CompletedWithErrors as failures", () => {
		const buckets = bucketExecutions(
			[
				exec("Timeout", "2026-06-11T10:00:00"),
				exec("Stuck", "2026-06-11T10:00:00"),
				exec("CompletedWithErrors", "2026-06-11T10:00:00"),
			],
			"7d",
			NOW,
		);
		expect(buckets[6].failed).toBe(3);
		expect(buckets[6].success).toBe(0);
	});

	it("excludes non-terminal and cancelled executions from both series", () => {
		const buckets = bucketExecutions(
			[
				exec("Running", "2026-06-11T10:00:00"),
				exec("Pending", "2026-06-11T10:00:00"),
				exec("Scheduled", "2026-06-11T10:00:00"),
				exec("Cancelling", "2026-06-11T10:00:00"),
				exec("Cancelled", "2026-06-11T10:00:00"),
			],
			"7d",
			NOW,
		);
		expect(buckets.every((b) => b.success === 0 && b.failed === 0)).toBe(
			true,
		);
	});

	it("skips executions without a parseable start time", () => {
		const buckets = bucketExecutions(
			[
				exec("Success", null),
				exec("Success", undefined),
				exec("Failed", "not-a-date"),
			],
			"7d",
			NOW,
		);
		expect(buckets.every((b) => b.success === 0 && b.failed === 0)).toBe(
			true,
		);
	});

	it("skips executions before the window start", () => {
		const buckets = bucketExecutions(
			[exec("Success", "2026-06-01T12:00:00")],
			"7d",
			NOW,
		);
		expect(buckets.every((b) => b.success === 0)).toBe(true);
	});

	it("labels hourly buckets with the hour and daily buckets with the date", () => {
		const hourly = bucketExecutions([], "24h", NOW);
		expect(hourly[23].label).toBe("5 PM");
		const daily = bucketExecutions([], "7d", NOW);
		expect(daily[6].label).toBe("Jun 11");
	});
});

describe("summarizeOutcomes", () => {
	it("tallies successes and failures with a percentage rate", () => {
		const summary = summarizeOutcomes([
			exec("Success", "2026-06-11T09:00:00"),
			exec("Success", "2026-06-11T10:00:00"),
			exec("Success", "2026-06-11T11:00:00"),
			exec("Failed", "2026-06-11T12:00:00"),
		]);
		expect(summary).toEqual({
			success: 3,
			failed: 1,
			total: 4,
			successRate: 75,
		});
	});

	it("counts Timeout/Stuck/CompletedWithErrors as failures and ignores non-terminal runs", () => {
		const summary = summarizeOutcomes([
			exec("Timeout", "2026-06-11T09:00:00"),
			exec("Stuck", "2026-06-11T09:00:00"),
			exec("CompletedWithErrors", "2026-06-11T09:00:00"),
			exec("Running", "2026-06-11T09:00:00"),
			exec("Cancelled", "2026-06-11T09:00:00"),
		]);
		expect(summary.failed).toBe(3);
		expect(summary.total).toBe(3);
		expect(summary.successRate).toBe(0);
	});

	it("reports a null rate when there are no terminal runs", () => {
		expect(summarizeOutcomes([]).successRate).toBeNull();
		expect(
			summarizeOutcomes([exec("Pending", "2026-06-11T09:00:00")])
				.successRate,
		).toBeNull();
	});
});
