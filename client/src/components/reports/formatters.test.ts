import { describe, expect, it } from "vitest";

import {
	formatBytes,
	formatChartDateLabel,
	formatCpuSeconds,
	formatCurrency,
	formatNumber,
} from "./formatters";

describe("report formatters", () => {
	it("formats chart date labels from the primitive values used by Recharts", () => {
		expect(formatChartDateLabel("2026-07-28T12:00:00")).toBe(
			"July 28th, 2026",
		);
		expect(formatChartDateLabel(new Date(2026, 6, 28).getTime())).toBe(
			"July 28th, 2026",
		);
	});

	it("rejects non-primitive chart labels", () => {
		expect(() => formatChartDateLabel(undefined)).toThrow(
			"Chart date label must be a string or number",
		);
		expect(() => formatChartDateLabel({})).toThrow(
			"Chart date label must be a string or number",
		);
	});

	it("formats currency and numeric values", () => {
		expect(formatCurrency("1234.5")).toBe("$1,234.50");
		expect(formatCurrency(undefined)).toBe("$0.00");
		expect(formatCurrency("not-a-number")).toBe("$0.00");
		expect(formatNumber(1234)).toBe("1,234");
		expect(formatNumber(undefined)).toBe("0");
	});

	it("formats byte values", () => {
		expect(formatBytes(0)).toBe("0 B");
		expect(formatBytes(1024)).toBe("1.00 KB");
		expect(formatBytes(1024 * 1024)).toBe("1.00 MB");
	});

	it("formats CPU durations", () => {
		expect(formatCpuSeconds(undefined)).toBe("0s");
		expect(formatCpuSeconds(30)).toBe("30.0s");
		expect(formatCpuSeconds(120)).toBe("2.0m");
		expect(formatCpuSeconds(7200)).toBe("2.00h");
	});
});
