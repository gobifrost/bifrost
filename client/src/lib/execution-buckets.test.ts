import { describe, expect, it } from "vitest";
import {
	bucketExecutions,
	clampBucketsToData,
	executionOutcome,
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

/**
 * Local-wall-clock instant as a "Z" ISO string. Buckets are floored in
 * local time while backend timestamps are parsed as UTC (parseBackendDate),
 * so fixtures must be built from local components to stay TZ-robust.
 */
function localIso(
	year: number,
	month: number,
	day: number,
	hour = 0,
	minute = 0,
): string {
	return new Date(year, month - 1, day, hour, minute).toISOString();
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
				exec("Success", localIso(2026, 6, 11, 9)),
				exec("Success", localIso(2026, 6, 11, 17, 30)),
				exec("Failed", localIso(2026, 6, 11, 17, 35)),
				exec("Success", localIso(2026, 6, 9, 8)),
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
				exec("Success", localIso(2026, 6, 11, 17, 5)),
				exec("Failed", localIso(2026, 6, 11, 16, 59)),
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
				exec("Timeout", localIso(2026, 6, 11, 10)),
				exec("Stuck", localIso(2026, 6, 11, 10)),
				exec("CompletedWithErrors", localIso(2026, 6, 11, 10)),
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
				exec("Running", localIso(2026, 6, 11, 10)),
				exec("Pending", localIso(2026, 6, 11, 10)),
				exec("Scheduled", localIso(2026, 6, 11, 10)),
				exec("Cancelling", localIso(2026, 6, 11, 10)),
				exec("Cancelled", localIso(2026, 6, 11, 10)),
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
			[exec("Success", localIso(2026, 6, 1, 12))],
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

describe("executionOutcome", () => {
	it("classifies every known status into one shared outcome", () => {
		expect(executionOutcome("Success")).toBe("success");
		// Stuck is terminal-bad — it counts as failed everywhere.
		expect(executionOutcome("Failed")).toBe("failed");
		expect(executionOutcome("Timeout")).toBe("failed");
		expect(executionOutcome("Stuck")).toBe("failed");
		expect(executionOutcome("CompletedWithErrors")).toBe("failed");
		// Cancelling is still active — it counts as running everywhere.
		expect(executionOutcome("Running")).toBe("running");
		expect(executionOutcome("Pending")).toBe("running");
		expect(executionOutcome("Cancelling")).toBe("running");
		expect(executionOutcome("Scheduled")).toBe("scheduled");
		expect(executionOutcome("Cancelled")).toBe("cancelled");
	});

	it("returns null for unknown statuses", () => {
		expect(executionOutcome("SomethingNew")).toBeNull();
		expect(executionOutcome("")).toBeNull();
	});
});

describe("clampBucketsToData", () => {
	it("drops buckets older than the oldest fetched row", () => {
		const buckets = bucketExecutions([], "7d", NOW);
		const executions = [
			exec("Success", localIso(2026, 6, 9, 8)),
			exec("Failed", localIso(2026, 6, 11, 10)),
		];
		const clamped = clampBucketsToData(buckets, executions);
		// Oldest row is Jun 9 → the Jun 5–8 buckets are not covered.
		expect(clamped).toHaveLength(3);
		expect(clamped[0].start).toEqual(new Date(2026, 5, 9));
	});

	it("keeps the bucket containing the oldest row (not just later ones)", () => {
		const buckets = bucketExecutions([], "24h", NOW);
		const clamped = clampBucketsToData(buckets, [
			exec("Success", localIso(2026, 6, 11, 16, 30)),
		]);
		// 16:30 lives in the 16:00 bucket → 16:00 and 17:00 remain.
		expect(clamped).toHaveLength(2);
		expect(clamped[0].start.getHours()).toBe(16);
	});

	it("returns buckets unchanged when no row has a parseable start time", () => {
		const buckets = bucketExecutions([], "7d", NOW);
		expect(clampBucketsToData(buckets, [])).toBe(buckets);
		expect(
			clampBucketsToData(buckets, [
				exec("Success", null),
				exec("Failed", "not-a-date"),
			]),
		).toBe(buckets);
	});

	it("returns buckets unchanged when the data spans the full window", () => {
		const buckets = bucketExecutions([], "7d", NOW);
		const clamped = clampBucketsToData(buckets, [
			exec("Success", localIso(2026, 6, 5, 1)),
		]);
		expect(clamped).toHaveLength(7);
	});
});
