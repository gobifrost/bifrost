import { describe, expect, it } from "vitest";

import {
	formatCacheRate,
	formatContextTokens,
	runUsageParts,
} from "./run-usage";

describe("formatContextTokens", () => {
	it("keeps small counts exact and abbreviates larger ones", () => {
		expect(formatContextTokens(950)).toBe("950");
		expect(formatContextTokens(12_000)).toBe("12k");
		expect(formatContextTokens(18_148)).toBe("18.1k");
		expect(formatContextTokens(156_400)).toBe("156k");
		expect(formatContextTokens(1_200_000)).toBe("1.2M");
	});
});

describe("formatCacheRate", () => {
	it("rounds to a whole percent", () => {
		expect(formatCacheRate(0)).toBe("0%");
		expect(formatCacheRate(0.1414)).toBe("14%");
		expect(formatCacheRate(1)).toBe("100%");
	});
});

describe("runUsageParts", () => {
	it("orders cost, context, then cache rate", () => {
		expect(
			runUsageParts({
				cost: "0.0747",
				delegate_cost: "0.0051",
				peak_context_tokens: 18_148,
				cache_hit_rate: 0.14,
			}),
		).toEqual(["$0.0747", "18.1k context", "14% cached"]);
	});

	it("omits measurements that were not recorded instead of showing zero", () => {
		expect(
			runUsageParts({
				cost: "0.002",
				delegate_cost: null,
				peak_context_tokens: null,
				cache_hit_rate: null,
			}),
		).toEqual(["$0.002000"]);
		expect(runUsageParts(null)).toEqual([]);
	});
});
