import { describe, expect, it } from "vitest";
import {
	executionOutcome,
	formatExecutionBuckets,
	type AggregatedExecutionBucket,
} from "./execution-buckets";

function localIso(
	year: number,
	month: number,
	day: number,
	hour = 0,
): string {
	return new Date(year, month - 1, day, hour).toISOString();
}

function bucket(
	start: string,
	successCount = 0,
	failedCount = 0,
): AggregatedExecutionBucket {
	return {
		start,
		success_count: successCount,
		failed_count: failedCount,
	};
}

describe("formatExecutionBuckets", () => {
	it("preserves all seven server buckets instead of clamping to recent data", () => {
		const serverBuckets = Array.from({ length: 7 }, (_, index) =>
			bucket(localIso(2026, 6, 5 + index), index, 7 - index),
		);

		const formatted = formatExecutionBuckets(serverBuckets, "7d");

		expect(formatted).toHaveLength(7);
		expect(formatted[0]).toMatchObject({ success: 0, failed: 7 });
		expect(formatted[6]).toMatchObject({ success: 6, failed: 1 });
		expect(formatted[0].start.getTime()).toBeLessThan(
			formatted[6].start.getTime(),
		);
	});

	it("preserves all 30 daily buckets", () => {
		const serverBuckets = Array.from({ length: 30 }, (_, index) =>
			bucket(localIso(2026, 5, 13 + index)),
		);

		expect(formatExecutionBuckets(serverBuckets, "30d")).toHaveLength(30);
	});

	it("labels hourly buckets with hours and daily buckets with dates", () => {
		const hourly = formatExecutionBuckets(
			[bucket(localIso(2026, 6, 11, 17))],
			"24h",
		);
		const daily = formatExecutionBuckets(
			[bucket(localIso(2026, 6, 11))],
			"7d",
		);

		expect(hourly[0].label).toBe("5 PM");
		expect(daily[0].label).toBe("Jun 11");
	});
});

describe("executionOutcome", () => {
	it("classifies every known status into one shared outcome", () => {
		expect(executionOutcome("Success")).toBe("success");
		expect(executionOutcome("Failed")).toBe("failed");
		expect(executionOutcome("Timeout")).toBe("failed");
		expect(executionOutcome("Stuck")).toBe("failed");
		expect(executionOutcome("CompletedWithErrors")).toBe("failed");
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
